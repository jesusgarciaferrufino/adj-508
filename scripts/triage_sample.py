"""Draw the triage-study sample (docs/triage_study.md): 30 Before findings, stratified, fixed seed.

    python -m scripts.triage_sample            # prints the sample as a Markdown table
    python -m scripts.triage_sample --csv      # the timing sheet rows, as CSV
    python -m scripts.triage_sample --packet   # the Manual condition's scanner output, per finding

--packet reads the frozen sample from docs/triage_timing_sheet.csv, not a new draw, so it still
matches the study after data/findings/ is regenerated.

Eligible: Before findings the router (agents/router.py, policy v1) puts in grey or human-only,
except page-level notices (Pa11y's generic reminder on <html>, which no reviewer can decide
per element) and the findings reviewers have already seen in docs/evidence_samples.md.

Stratified by page (QUOTA) and, within a page, by criterion: criteria are taken in a fixed
shuffled order, one finding each, round after round, until the page's quota is full. The
header, navigation and <title> repeat on every page, so a finding is skipped when an earlier
pick has the same criterion and selector (a reviewer would recognise it the second time). "Random"
here is a SHA-256 of SEED and the name, so the draw is the same on every machine and in any
language. The picks alternate between halves A and B within each page, giving 15 and 15.
"""

import argparse
import csv
import hashlib
import sys
from pathlib import Path

from agents.router import GREY, HUMAN_ONLY, route_finding
from scanners.findings import load_findings

SEED = "adj-triage-2026-10-31"
QUOTA = {"home": 8, "news": 8, "tickets": 7, "survey": 7}
FIRST_HALF = {"home": "A", "news": "B", "tickets": "A", "survey": "B"}  # so the halves come out 15 and 15
SHEET = Path(__file__).resolve().parent.parent / "docs" / "triage_timing_sheet.csv"
SEEN = {"F-before-home-el0210-2.4.4", "F-before-home-el0194-2.1.1", "F-before-home-el0231-1.3.1"}


def rank(*parts: str) -> str:
    return hashlib.sha256(":".join((SEED, *parts)).encode()).hexdigest()


def _row(d: dict, page: str) -> dict:
    return {"finding_id": d["finding_id"], "page": page, "sc": d["wcag"][0], "bucket": route_finding(d)["bucket"],
            "status": d["scanner_status"], "selector": d["evidence"]["selector"],
            "message": " / ".join(s["message"] for s in d["scanner_details"])}


def all_rows(page: str) -> list[dict]:
    return [_row(f.model_dump(), page) for f in load_findings("before", page)]


def eligible(page: str) -> list[dict]:
    out = []
    for r in all_rows(page):
        page_level_notice = r["status"] == "notice" and r["selector"] == "html"
        if r["bucket"] in (GREY, HUMAN_ONLY) and not page_level_notice and r["finding_id"] not in SEEN:
            out.append(r)
    return out


def draw(findings: list[dict], page: str, used: set[tuple[str, str]]) -> list[dict]:
    """The page's picks; `used` holds the (criterion, selector) of earlier picks and is updated."""
    by_sc: dict[str, list[dict]] = {}
    for f in sorted(findings, key=lambda f: rank(f["finding_id"])):
        if (f["sc"], f["selector"]) not in used:
            by_sc.setdefault(f["sc"], []).append(f)
    order = sorted(by_sc, key=lambda sc: rank(page, sc))
    picks, depth = [], 0
    while len(picks) < QUOTA[page] and any(len(by_sc[sc]) > depth for sc in order):
        picks += [by_sc[sc][depth] for sc in order if len(by_sc[sc]) > depth][: QUOTA[page] - len(picks)]
        depth += 1
    used.update((f["sc"], f["selector"]) for f in picks)
    other = "B" if FIRST_HALF[page] == "A" else "A"
    return [{**f, "half": FIRST_HALF[page] if i % 2 == 0 else other} for i, f in enumerate(picks)]


def sample() -> list[dict]:
    used: set[tuple[str, str]] = set()
    return [f for page in QUOTA for f in draw(eligible(page), page, used)]


def main() -> int:
    ap = argparse.ArgumentParser(description="Draw the triage-study sample.")
    ap.add_argument("--csv", action="store_true", help="print the timing sheet rows as CSV")
    ap.add_argument("--packet", action="store_true", help="print what Manual reviewers get for each finding")
    a = ap.parse_args()
    rows = sample()
    if a.packet:
        with SHEET.open(encoding="utf-8", newline="") as fh:
            ids = list(dict.fromkeys(r["finding_id"] for r in csv.DictReader(fh) if r["finding_id"] != "START"))
        found = {f["finding_id"]: f for page in QUOTA for f in all_rows(page)}
        missing = [i for i in ids if i not in found]
        if missing:
            print(f"Not in data/findings/: {', '.join(missing)}", file=sys.stderr)
            return 1
        for i, f in enumerate((found[x] for x in ids), 1):
            print(f"{i}. {f['finding_id']}\n   page:      https://www.w3.org/WAI/demos/bad/"
                  f"before/{f['page']}.html\n   criterion: {f['sc']}\n   selector:  {f['selector']}\n"
                  f"   scanner:   {f['message']}\n")
        return 0
    if a.csv:
        w = csv.writer(sys.stdout)
        w.writerow(["reviewer", "condition", "half", "order", "finding_id", "page", "criterion", "end_time",
                    "decision", "notes"])
        for i, f in enumerate(rows, 1):
            w.writerow(["", "", f["half"], i, f["finding_id"], f["page"], f["sc"], "", "", ""])
        return 0
    print("| # | Half | Finding | Bucket | Scanner status |\n| --- | --- | --- | --- | --- |")
    for i, f in enumerate(rows, 1):
        print(f"| {i} | {f['half']} | `{f['finding_id']}` | {f['bucket']} | {f['status']} |")
    halves = {h: sum(f["half"] == h for f in rows) for h in "AB"}
    print(f"\n{len(rows)} findings; half A {halves['A']}, half B {halves['B']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
