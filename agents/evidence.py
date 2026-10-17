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
  - is_issue(ai): the issue rule (decided Oct 17). A grey finding is an issue only when it is
    likely_fail AND its citations were verified; anything else is flagged for a human. The
    same rule is used by scripts/score.py and reports/matrix.py.
The AI never decides conformance; a signed-in human reviewer does (CONTEXT.md).

build_cases(finding) returns ONLY {"ai": {...}}; the graph merges it in and only calls
it for findings with bucket == "grey".

Cost controls (settings.py / .env):
  ADJ_MAX_BEDROCK_CALLS  hard cap on calls per process (2 per finding)
  ADJ_EVIDENCE_ONLY      comma-separated finding IDs; others are skipped without a call
"""

import json
import re
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
    cfg = Config(retries={"mode": "adaptive", "max_attempts": 5}, read_timeout=60)
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


def is_issue(ai: dict | None) -> bool:
    """True when a grey finding's AI result counts as an issue: likely_fail with verified citations.

    An unverified likely_fail, a lean to pass, unsure, or not evaluated (None) is flagged for a
    human instead. This is the AI's leaning only, never a conformance status.
    """
    ai = ai or {}
    return ai.get("recommendation") == "likely_fail" and ai.get("citations_verified") is True


def _call(client, prompt: str) -> tuple[str, dict]:
    global _calls_made
    _calls_made += 1
    resp = client.converse(
        modelId=settings.MODEL_ID,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": MAX_TOKENS, "temperature": 0},
    )
    text = resp["output"]["message"]["content"][0]["text"]
    return text, resp.get("usage") or {}


def _skipped(reason: str) -> dict:
    note = f"SKIPPED: {reason}"
    return {"ai": {"case_for": note, "case_against": note, "recommendation": None,  # not evaluated
                   "confidence": None, "citations_verified": False, "model_id": None,
                   "prompt_version": PROMPT_VERSION, "usage": {"input_tokens": 0, "output_tokens": 0}}}


def _describe(side: dict) -> str:
    quotes = "; ".join(f'"{q}"' for q in side["quoted_evidence"])
    return f"{side['argument']}\nQuoted: {quotes}\nStrength: {side['strength']}/5"


def build_cases(finding: dict, client=None) -> dict:
    """FOR and AGAINST cases for one grey finding. Pass a fake client in tests."""
    if settings.EVIDENCE_ONLY and finding.get("finding_id") not in settings.EVIDENCE_ONLY:
        return _skipped("not selected (ADJ_EVIDENCE_ONLY)")
    if _calls_made + 2 > settings.MAX_BEDROCK_CALLS:
        return _skipped("call cap reached")
    client = client or _bedrock_client()

    sides, usage = {}, {"input_tokens": 0, "output_tokens": 0}
    for key, name in (("for", PROMPT_FOR), ("against", PROMPT_AGAINST)):
        text, u = _call(client, fill_prompt(load_prompt(name), finding))
        usage["input_tokens"] += u.get("inputTokens", 0)
        usage["output_tokens"] += u.get("outputTokens", 0)
        sides[key] = parse_side(text)

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
