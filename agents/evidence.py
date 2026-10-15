"""Track C: FOR/AGAINST evidence agents for grey findings.

Two separate Bedrock calls per finding, same evidence, opposite briefs. Neither call
sees the other's output; that independence is the point. Code (not the model) then:
  - checks citations: both sides cite the finding's own criterion, and every quote
    appears in evidence the model was shown: the HTML snippet, the surrounding text
    (evidence.context_text), the selector, or a scanner message (whitespace-insensitive);
  - sets the recommendation from the two strengths:
        FOR - AGAINST >= 2  -> likely_fail
        AGAINST - FOR >= 2  -> likely_pass
        otherwise           -> unsure
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


def parse_side(text: str) -> dict:
    """The first {...} object in the reply, checked for the four fields."""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise EvidenceError(f"no JSON object in reply: {text[:200]!r}")
    try:
        side = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise EvidenceError(f"reply is not valid JSON ({e}): {m.group(0)[:200]!r}") from None
    missing = {"argument", "cited_sc", "quoted_evidence", "strength"} - side.keys()
    if missing:
        raise EvidenceError(f"reply missing {sorted(missing)}")
    if not isinstance(side["strength"], int) or not 1 <= side["strength"] <= 5:
        raise EvidenceError(f"strength must be a whole number 1-5, got {side['strength']!r}")
    if isinstance(side["quoted_evidence"], str):
        side["quoted_evidence"] = [side["quoted_evidence"]]
    return side


def _squash(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def citations_ok(side: dict, finding: dict) -> bool:
    """Cites the finding's own criterion, and every quote is really in the evidence."""
    ev = finding.get("evidence") or {}
    sources = [ev.get("html_snippet", ""), ev.get("context_text", ""), ev.get("selector", "")]
    sources += [d.get("message", "") for d in finding.get("scanner_details") or []]
    haystack = [_squash(s) for s in sources if s]
    quotes = [_squash(q) for q in side.get("quoted_evidence") or [] if isinstance(q, str)]
    return (str(side.get("cited_sc", "")).strip() == finding["wcag"][0]
            and bool(quotes)
            and all(any(q in h for h in haystack) for q in quotes))


def recommend(for_strength: int, against_strength: int) -> str:
    gap = for_strength - against_strength
    if gap >= 2:
        return "likely_fail"
    if gap <= -2:
        return "likely_pass"
    return "unsure"


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
    return {"ai": {"case_for": note, "case_against": note, "recommendation": "unsure",
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
