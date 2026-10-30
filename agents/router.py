"""Track C: triage router. Puts each finding in one of three buckets.

    machine     a scanner rule is conclusive; reported with evidence, no AI case needed
    grey        a tool can spot it but can't judge it; gets a FOR/AGAINST case file
    human-only  no tool can check it; goes straight to a reviewer checklist

Router v2 rules (v1 Oct 9; 2b and 2c added Oct 29), checked in this order:
  1.  scanner_status == "violation" (axe or an ADJ code rule was sure) -> machine
  2.  page-level notice (scanner_status == "notice", selector "html")  -> human-only
  2b. every scanner detail is a check known to be unreliable for its
      criterion (UNRELIABLE_CHECKS)                                     -> human-only
  2c. the criterion can only be judged by interacting with the page
      (NEEDS_INTERACTION: 3.3.1)                                        -> human-only
  3.  criterion marked human_only in rag/wcag20_aa.json                -> human-only
  4.  everything else (needs_review, element-level notices)            -> grey

Why 2b and 2c (Oct 29): the full FOR/AGAINST run had 38 false alarms on W3C's After site, which
passes every A/AA criterion. All 38 came from four scanner warnings that a machine can't
trust: HTMLCS's guess at a complex table's headers (H43.IncorrectAttr), an anchor without href
(the current-page nav item), a select with a default first option, and 3.3.1, which a static
scan can't judge at all. FOR/AGAINST leaned likely_fail on them. Re-scored from that run with
these routed to a person: After false alarms 38 -> 0, recall (a) and (b) unchanged, no Before
ground-truth row affected. The rules say when a scanner warning can't be trusted; they say
nothing about this site's content. A finding still reaches a person either way; 2b and 2c only
skip the AI case file. Rule 1 runs first, so a violation stays machine.

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

# Rule 2b: scanner checks whose warning a machine can't trust for this criterion.
# (short name, criterion, end of rule_id, words the message must contain or None)
UNRELIABLE_CHECKS = (
    # HTMLCS compares the cell's headers attribute with its own guess at the table's header
    # structure, and the guess is wrong on valid complex tables. A person must read the table.
    ("H43.IncorrectAttr", "1.3.1", "H43.IncorrectAttr", None),
    # An <a> with no href is a placeholder, not a link (W3C uses one for the current-page nav
    # item). Whether that is acceptable is a human call.
    ("anchor without href", "4.1.2", ".Placeholder", None),
    # Fires on working selects whose first option is the default value.
    ("select value", "4.1.2", ".Value", "select"),
)

# Rule 2c: criteria that can only be judged by interacting with the page, never by a static scan.
NEEDS_INTERACTION = {
    "3.3.1": "can only be judged by submitting a form and triggering an error",
}


def unreliable_check(detail: dict, sc: str | None) -> str | None:
    """The short name of the known-unreliable check this scanner detail is, or None."""
    rule_id, message = detail.get("rule_id") or "", (detail.get("message") or "").lower()
    for name, criterion, suffix, word in UNRELIABLE_CHECKS:
        if sc == criterion and rule_id.endswith(suffix) and (word is None or word in message):
            return name
    return None


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
        sources = ", ".join(finding.get("sources") or [finding.get("source") or "a scanner"])
        return {"bucket": MACHINE,
                "route_reason": f"rule 1: deterministic violation reported by {sources} (scanner_status=violation)"}
    if status == "notice" and selector == "html":
        return {"bucket": HUMAN_ONLY,
                "route_reason": f"rule 2: page-level notice with no element to inspect (criterion {sc})"}
    details = finding.get("scanner_details") or []
    names = [unreliable_check(d, sc) for d in details]
    if details and all(names):  # EVERY detail: any other scanner check keeps it grey
        return {"bucket": HUMAN_ONLY,
                "route_reason": f"rule 2b: scanner check known to be unreliable for this criterion "
                                f"({', '.join(sorted(set(names)))}): a person must judge"}
    if sc in NEEDS_INTERACTION:
        return {"bucket": HUMAN_ONLY,
                "route_reason": f"rule 2c: criterion {sc} {NEEDS_INTERACTION[sc]}: a person must judge"}
    if sc in human_only_criteria():
        return {"bucket": HUMAN_ONLY,
                "route_reason": f"rule 3: criterion {sc} is marked human_only in rag/wcag20_aa.json"}
    return {"bucket": GREY,
            "route_reason": f"rule 4: scanner can flag but not judge (scanner_status={status}, criterion {sc})"}
