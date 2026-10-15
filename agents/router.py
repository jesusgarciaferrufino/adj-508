"""Track C: triage router. Puts each finding in one of three buckets.

    machine     a scanner rule is conclusive; reported with evidence, no AI case needed
    grey        a tool can spot it but can't judge it; gets a FOR/AGAINST case file
    human-only  no tool can check it; goes straight to a reviewer checklist

Router v1 rules (manager decision, Oct 9), checked in this order:
  1. scanner_status == "violation" (axe was sure)                     -> machine
  2. page-level notice (scanner_status == "notice", selector "html")  -> human-only
  3. criterion marked human_only in rag/wcag20_aa.json                -> human-only
  4. everything else (needs_review, element-level notices)            -> grey

route_finding(finding) returns ONLY {"bucket", "route_reason"}; the graph merges it in.
No model calls: the rules decide every finding, so routing is free and repeatable.
"""

import json
from functools import cache
from pathlib import Path

MACHINE = "machine"
GREY = "grey"
HUMAN_ONLY = "human-only"
BUCKETS = (MACHINE, GREY, HUMAN_ONLY)

CORPUS = Path(__file__).resolve().parent.parent / "rag" / "wcag20_aa.json"


@cache
def human_only_criteria() -> frozenset[str]:
    """Criteria Track B's corpus marks as needing a human every time (e.g. 2.4.3, 3.2.4)."""
    entries = json.loads(CORPUS.read_text(encoding="utf-8"))
    return frozenset(e["sc"] for e in entries if e.get("human_only"))


def route_finding(finding: dict) -> dict:
    status = finding.get("scanner_status")
    selector = (finding.get("evidence") or {}).get("selector")
    wcag = finding.get("wcag") or []
    sc = wcag[0] if wcag else None

    if status == "violation":
        return {"bucket": MACHINE,
                "route_reason": "rule 1: axe reported a violation (scanner_status=violation)"}
    if status == "notice" and selector == "html":
        return {"bucket": HUMAN_ONLY,
                "route_reason": f"rule 2: page-level notice with no element to inspect (criterion {sc})"}
    if sc in human_only_criteria():
        return {"bucket": HUMAN_ONLY,
                "route_reason": f"rule 3: criterion {sc} is marked human_only in rag/wcag20_aa.json"}
    return {"bucket": GREY,
            "route_reason": f"rule 4: scanner can flag but not judge (scanner_status={status}, criterion {sc})"}
