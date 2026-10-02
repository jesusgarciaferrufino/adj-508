"""WCAG 2.0 Level A and AA success criteria: the only ones ADJ may cite (CONTEXT.md).

Track B's rag/wcag20_aa.json will be the full corpus. This is the plain allowlist
the merge step needs now, so Pa11y's WCAG 2.1/2.2 checks (1.3.4, 1.4.10, 2.5.x,
4.1.3, ...) never enter the findings.
"""

import re

WCAG20_A = {
    "1.1.1", "1.2.1", "1.2.2", "1.2.3", "1.3.1", "1.3.2", "1.3.3", "1.4.1", "1.4.2",
    "2.1.1", "2.1.2", "2.2.1", "2.2.2", "2.3.1", "2.4.1", "2.4.2", "2.4.3", "2.4.4",
    "3.1.1", "3.2.1", "3.2.2", "3.3.1", "3.3.2", "4.1.1", "4.1.2",
}
WCAG20_AA = {
    "1.2.4", "1.2.5", "1.4.3", "1.4.4", "1.4.5", "2.4.5", "2.4.6", "2.4.7",
    "3.1.2", "3.2.3", "3.2.4", "3.3.3", "3.3.4",
}
WCAG20_A_AA = WCAG20_A | WCAG20_AA
assert len(WCAG20_A) == 25 and len(WCAG20_AA) == 13 and len(WCAG20_A_AA) == 38

# Every WCAG 2.0 A/AA criterion is adopted for web content by Revised 508 E205.4.
SECTION508_WEB = "E205.4 (WCAG 2.0 A/AA)"

_AXE_TAG = re.compile(r"wcag(\d)(\d)(\d+)")


def axe_tags_to_sc(tags):
    """axe tags like 'wcag111' -> '1.1.1' (same regex as the week 1 scoring script)."""
    out = []
    for t in tags:
        m = _AXE_TAG.fullmatch(t)
        if m:
            out.append(".".join(m.groups()))
    return out
