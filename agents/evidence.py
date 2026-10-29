"""Track C: FOR/AGAINST evidence agents for grey findings.

Two separate Bedrock calls per finding, same evidence, opposite briefs. Neither call
sees the other's output; that independence is the point. Code (not the model) then:
  - checks citations: both sides cite the finding's own criterion; every quote is at
    least MIN_QUOTE_CHARS long and appears in evidence the model was shown (the HTML
    snippet, the surrounding text evidence.context_text, or a scanner message;
    whitespace-insensitive); and at least one quote comes from the page itself (HTML or
    surrounding text), not only from the scanner. The CSS selector does not count: it is
    our own locator, not evidence, and short fragments of it match almost anything;
  - sets the recommendation from the two strengths:
        FOR - AGAINST >= 2  -> likely_fail
        AGAINST - FOR >= 2  -> likely_pass
        otherwise           -> unsure
    A skipped finding (ADJ_EVIDENCE_ONLY or the call cap) gets recommendation None: it was not
    evaluated, which is different from "unsure". Its case_for reads "SKIPPED: <reason>". A
    failed call raises; the graph logs it and keeps the finding without a recommendation.
  - is_issue(finding): the one issue rule (decided Oct 17). A finding is an issue when its
    bucket is "machine", or it is grey, likely_fail AND its citations were verified
    (grey_is_issue). Anything else that surfaced is flagged for a human. scripts/score.py and
    reports/matrix.py import it rather than keeping their own copy.
The AI never decides conformance; a signed-in human reviewer does (CONTEXT.md).

build_cases(finding) returns ONLY {"ai": {...}}; the graph merges it in and only calls
it for findings with bucket == "grey".

Cost controls (settings.py / .env):
  ADJ_MAX_BEDROCK_CALLS  hard cap on calls per process (2 per finding). A call that is
                         retried after throttling still counts once.
  ADJ_EVIDENCE_ONLY      comma-separated finding IDs; others are skipped without a call
  ADJ_CALL_DELAY         seconds to wait before every call (default 0), to stay under a
                         per-minute quota on long runs

Retries live in ONE place, _call (Oct 29; botocore's own retries are off, max_attempts 1).
Transient errors are waited out: 15, 30, 60, 120, 120 seconds (about 6 minutes), one line
on stderr per wait, then the call raises as before (the graph records the finding as an
error). Transient means the AWS error codes in RETRY_CODES (throttling, service unavailable,
internal error, model not ready) or a timeout or connection error (RETRY_CLASSES, matched by
class name, so this module never imports botocore at load time). Anything else raises at once.

Circuit breaker: when a transient error is still there after the last wait, or a throttling
message names a per-day quota (fatal at once, no waits), _gave_up is set and every later
finding in this process is returned skipped (recommendation None, "SKIPPED: Bedrock
unavailable..."), with no call. The run then finishes in minutes instead of hours, and
scripts/score.py warns that those findings were not evaluated.

Tokens: every call that returns is added to a running total at once (tokens_billed()), so a
finding whose FOR call succeeded and AGAINST call failed is still counted. The graph writes
it to summary.json as tokens_billed. A progress line goes to stderr every 10 findings.
"""

import json
import re
import sys
import time
from functools import cache
from pathlib import Path

import settings

REPO = Path(__file__).resolve().parent.parent
PROMPTS = REPO / "prompts"
CORPUS = REPO / "rag" / "wcag20_aa.json"
PROMPT_FOR = "evidence_for_v1"
PROMPT_AGAINST = "evidence_against_v1"
PROMPT_VERSION = f"{PROMPT_FOR}+{PROMPT_AGAINST}"
MAX_TOKENS = 700
MIN_QUOTE_CHARS = 6  # 'alt=""' is the shortest meaningful quote; "a", "href", "Click" are not evidence
PLACEHOLDERS = ("sc", "sc_name", "level", "criterion_text", "needs_human_when",
                "selector", "html_snippet", "context_text", "scanner_messages")

_calls_made = 0  # Bedrock calls in this process; checked against settings.MAX_BEDROCK_CALLS
_findings_done = 0  # findings that went through FOR/AGAINST in this process (progress line)
_calls_failed = 0  # calls that still raised after their retries (progress line)
_billed = {"input_tokens": 0, "output_tokens": 0}  # every call that returned, in this process
_gave_up: str | None = None  # set by the circuit breaker; then no more calls in this process
RETRY_CODES = {"ThrottlingException", "ServiceUnavailableException", "InternalServerException",
               "ModelNotReadyException"}
RETRY_CLASSES = {"ReadTimeoutError", "ConnectTimeoutError", "EndpointConnectionError"}  # botocore, by name
DAILY_QUOTA_RE = re.compile(r"per\s+day", re.IGNORECASE)  # e.g. "Too many tokens per day"
RETRY_WAITS = (15, 30, 60, 120, 120)  # seconds; about 6 minutes in all, then give up
PROGRESS_EVERY = 10  # findings


class EvidenceError(ValueError):
    """The model's reply couldn't be used (not JSON, missing fields, bad strength)."""


@cache
def _corpus() -> dict[str, dict]:
    return {e["sc"]: e for e in json.loads(CORPUS.read_text(encoding="utf-8"))}


@cache
def load_prompt(name: str) -> str:
    return (PROMPTS / f"{name}.md").read_text(encoding="utf-8")


@cache
def _bedrock_client():
    import boto3  # imported here so tests and the router never need AWS
    from botocore.config import Config
    # No botocore retries: _call owns every retry, so its waits and stderr lines are the whole story.
    cfg = Config(retries={"mode": "standard", "max_attempts": 1}, read_timeout=60)
    return boto3.client("bedrock-runtime", region_name=settings.AWS_REGION, config=cfg)


def fill_prompt(template: str, finding: dict) -> str:
    """Put the finding's evidence into a prompt. Plain replace, so JSON braces stay literal."""
    sc = finding["wcag"][0]
    entry = _corpus().get(sc, {})
    ev = finding.get("evidence") or {}
    values = {
        "sc": sc,
        "sc_name": entry.get("name", ""),
        "level": entry.get("level", ""),
        "criterion_text": entry.get("text", ""),
        "needs_human_when": entry.get("needs_human_when", ""),
        "selector": ev.get("selector", ""),
        "html_snippet": ev.get("html_snippet", ""),
        "context_text": ev.get("context_text") or "(none)",
        "scanner_messages": " | ".join(d.get("message", "") for d in finding.get("scanner_details") or []),
    }
    for key in PLACEHOLDERS:
        template = template.replace("{" + key + "}", str(values[key]))
    return template


def _strength(value) -> int | None:
    """A whole number 1-5, also accepting 4.0 or "4" (models sometimes quote numbers)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return value if isinstance(value, int) and 1 <= value <= 5 else None


def parse_side(text: str) -> dict:
    """The first JSON object in the reply (text before or after it is ignored), checked for the
    four fields."""
    decoder = json.JSONDecoder()
    side = None
    for start in (i for i, ch in enumerate(text) if ch == "{"):
        try:
            candidate, _ = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict):
            side = candidate
            break
    if side is None:
        raise EvidenceError(f"no JSON object in reply: {text[:200]!r}")
    missing = {"argument", "cited_sc", "quoted_evidence", "strength"} - side.keys()
    if missing:
        raise EvidenceError(f"reply missing {sorted(missing)}")
    strength = _strength(side["strength"])
    if strength is None:
        raise EvidenceError(f"strength must be a whole number 1-5, got {side['strength']!r}")
    side["strength"] = strength
    if isinstance(side["quoted_evidence"], str):
        side["quoted_evidence"] = [side["quoted_evidence"]]
    return side


def _squash(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def citations_ok(side: dict, finding: dict) -> bool:
    """Cites the finding's own criterion, every quote is a real, non-trivial piece of the
    evidence, and at least one quote comes from the page (HTML or surrounding text)."""
    ev = finding.get("evidence") or {}
    page = [_squash(s) for s in (ev.get("html_snippet", ""), ev.get("context_text", "")) if s]
    scanner = [_squash(d.get("message", "")) for d in finding.get("scanner_details") or [] if d.get("message")]
    raw = side.get("quoted_evidence") or []
    quotes = [_squash(q) for q in raw if isinstance(q, str)]
    if str(side.get("cited_sc", "")).strip() != finding["wcag"][0]:
        return False
    if not quotes or len(quotes) != len(raw) or any(len(q) < MIN_QUOTE_CHARS for q in quotes):
        return False
    if not all(any(q in h for h in page + scanner) for q in quotes):
        return False
    return any(any(q in h for h in page) for q in quotes)


def recommend(for_strength: int, against_strength: int) -> str:
    gap = for_strength - against_strength
    if gap >= 2:
        return "likely_fail"
    if gap <= -2:
        return "likely_pass"
    return "unsure"


def grey_is_issue(ai: dict | None) -> bool:
    """True when a grey finding's AI result counts as an issue: likely_fail with verified citations.

    Grey findings only: it looks at the ai dict alone, so on a machine finding it returns False.
    To count issues, use is_issue(finding). An unverified likely_fail, a lean to pass, unsure,
    or not evaluated (None) is flagged for a human instead. This is the AI's leaning only,
    never a conformance status.
    """
    ai = ai or {}
    return ai.get("recommendation") == "likely_fail" and ai.get("citations_verified") is True


def is_issue(finding: dict) -> bool:
    """The one issue rule. Used by scripts/score.py and reports/matrix.py; never a conformance status.

    True for a machine finding, or a grey finding whose AI result passes grey_is_issue.
    human-only findings and everything else are not issues.
    """
    bucket = finding.get("bucket")
    return bucket == "machine" or (bucket == "grey" and grey_is_issue(finding.get("ai")))


def _error_code(exc: Exception) -> str | None:
    """The AWS error code of a botocore ClientError (e.g. "ThrottlingException"), else None.

    Read from the exception itself, so this module never has to import botocore.
    """
    response = getattr(exc, "response", None)
    return response.get("Error", {}).get("Code") if isinstance(response, dict) else None


def _transient(exc: Exception) -> str | None:
    """A short name for a transient error worth waiting out, or None if it should raise at once."""
    code = _error_code(exc)
    if code in RETRY_CODES:
        return code
    name = type(exc).__name__
    return name if name in RETRY_CLASSES else None


def _trip(reason: str) -> None:
    """The circuit breaker: no more Bedrock calls in this process."""
    global _gave_up
    _gave_up = reason
    print(f"evidence: stopping Bedrock calls for the rest of this run ({reason}); remaining grey findings "
          "are marked skipped", file=sys.stderr, flush=True)


def tokens_billed() -> dict:
    """Input and output tokens of every call that returned in this process, including calls
    from findings that later failed (FOR succeeded, AGAINST failed; or a reply that didn't parse)."""
    return dict(_billed)


def _call(client, prompt: str) -> tuple[str, dict]:
    """One Converse call. Waits out transient errors (RETRY_WAITS), trips the breaker if they
    don't clear, then raises as before. At most len(RETRY_WAITS) + 1 requests per call."""
    global _calls_made, _calls_failed
    _calls_made += 1  # once per call, however many retries: the cap counts calls we meant to make
    if settings.CALL_DELAY:
        time.sleep(settings.CALL_DELAY)
    for attempt in range(len(RETRY_WAITS) + 1):
        try:
            resp = client.converse(
                modelId=settings.MODEL_ID,
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                inferenceConfig={"maxTokens": MAX_TOKENS, "temperature": 0},
            )
            break
        except Exception as e:
            kind = _transient(e)
            if kind is None:
                _calls_failed += 1
                raise  # not transient (bad request, access denied, ...): no wait, no breaker
            if DAILY_QUOTA_RE.search(str(e)):
                _calls_failed += 1
                _trip(f"{kind}: daily quota reached")
                raise
            if attempt == len(RETRY_WAITS):
                _calls_failed += 1
                _trip(f"{kind} after {len(RETRY_WAITS)} waits")
                raise
            wait = RETRY_WAITS[attempt]
            print(f"evidence: {kind}, waiting {wait}s (attempt {attempt + 1} of {len(RETRY_WAITS)})",
                  file=sys.stderr, flush=True)
            time.sleep(wait)
    usage = resp.get("usage") or {}
    _billed["input_tokens"] += usage.get("inputTokens", 0)
    _billed["output_tokens"] += usage.get("outputTokens", 0)
    text = resp["output"]["message"]["content"][0]["text"]
    return text, usage


def _skipped(reason: str) -> dict:
    note = f"SKIPPED: {reason}"
    return {"ai": {"case_for": note, "case_against": note, "recommendation": None,  # not evaluated
                   "confidence": None, "citations_verified": False, "model_id": None,
                   "prompt_version": PROMPT_VERSION, "usage": {"input_tokens": 0, "output_tokens": 0}}}


def _describe(side: dict) -> str:
    quotes = "; ".join(f'"{q}"' for q in side["quoted_evidence"])
    return f"{side['argument']}\nQuoted: {quotes}\nStrength: {side['strength']}/5"


def _progress() -> None:
    """Every PROGRESS_EVERY findings, one line on stderr, so a long run shows it is alive."""
    global _findings_done
    _findings_done += 1
    if _findings_done % PROGRESS_EVERY == 0:
        print(f"evidence: {_findings_done} findings, {_calls_made} calls attempted ({_calls_failed} failed), "
              f"{_billed['input_tokens']:,} in / {_billed['output_tokens']:,} out tokens billed so far",
              file=sys.stderr, flush=True)


def build_cases(finding: dict, client=None) -> dict:
    """FOR and AGAINST cases for one grey finding. Pass a fake client in tests."""
    if settings.EVIDENCE_ONLY and finding.get("finding_id") not in settings.EVIDENCE_ONLY:
        return _skipped("not selected (ADJ_EVIDENCE_ONLY)")
    if _gave_up:
        return _skipped(f"Bedrock unavailable, stopped calling for the rest of this run: {_gave_up}")
    if _calls_made + 2 > settings.MAX_BEDROCK_CALLS:
        return _skipped("call cap reached")
    client = client or _bedrock_client()

    sides, usage = {}, {"input_tokens": 0, "output_tokens": 0}
    try:
        for key, name in (("for", PROMPT_FOR), ("against", PROMPT_AGAINST)):
            text, u = _call(client, fill_prompt(load_prompt(name), finding))
            usage["input_tokens"] += u.get("inputTokens", 0)
            usage["output_tokens"] += u.get("outputTokens", 0)
            sides[key] = parse_side(text)
    finally:
        _progress()  # counted whether this finding worked or failed

    return {"ai": {
        "case_for": _describe(sides["for"]),
        "case_against": _describe(sides["against"]),
        "recommendation": recommend(sides["for"]["strength"], sides["against"]["strength"]),
        "confidence": None,  # calibrated against ground truth in Week 4
        "citations_verified": citations_ok(sides["for"], finding) and citations_ok(sides["against"], finding),
        "model_id": settings.MODEL_ID,
        "prompt_version": PROMPT_VERSION,
        "usage": usage,
    }}
