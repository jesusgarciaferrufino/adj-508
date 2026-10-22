"""List the scanner findings that could match one ground-truth row (Track S, for the ground-truth owner).

    python -m scripts.gt_candidates --page home --wcag 1.1.1
    python -m scripts.gt_candidates --page news                # every criterion on that page

For each Before finding on that page (and criterion) it prints the finding_id, scanner_status,
selector, the first 120 characters of the HTML, the screenshot path and the first scanner
message. Pick the one that is the same issue as the W3C report row and put its finding_id in
data/ground-truth/ground_truth.csv. If none fits, the honest answer is `missed`.
"""

import argparse
import sys

from scanners.findings import PAGES, VERSIONS, FindingsFileError, load_findings
from scanners.wcag import WCAG20_A_AA

HTML_CHARS = 120


def _one_line(text: str, limit: int | None = None) -> str:
    flat = " ".join((text or "").split())
    return flat[:limit] if limit else flat


def candidates(page: str, wcag: str | None = None, version: str = "before") -> list[dict]:
    """The findings on one page (optionally one criterion) as plain rows, in document order."""
    rows = []
    for f in load_findings(version, page):
        if wcag and f.wcag[0] != wcag:
            continue
        rows.append({
            "finding_id": f.finding_id,
            # Pa11y's generic "check this manually" reminder (router rule 2). Decision 3: never a match.
            "page_level_notice": f.scanner_status == "notice" and f.evidence.selector == "html",
            "wcag": f.wcag[0],
            "scanner_status": f.scanner_status,
            "selector": f.evidence.selector,
            "html": _one_line(f.evidence.html_snippet, HTML_CHARS),
            "screenshot": f.evidence.screenshot,
            "message": _one_line(f.scanner_details[0].message),
        })
    rows.sort(key=lambda r: (r["wcag"], r["finding_id"]))
    return rows


def format_rows(rows: list[dict]) -> str:
    blocks = []
    for r in rows:
        blocks.append("\n".join([
            f"{r['finding_id']}  [{r['scanner_status']}]"
            + ("  [page-level notice: not a match, use missed]" if r["page_level_notice"] else ""),
            f"  selector:   {r['selector']}",
            f"  html:       {r['html']}",
            f"  screenshot: {r['screenshot'] or '(none: use the selector)'}",
            f"  scanner:    {r['message']}",
        ]))
    return "\n\n".join(blocks)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--page", required=True, choices=PAGES)
    ap.add_argument("--wcag", help="criterion, e.g. 1.1.1 (WCAG 2.0 A/AA only); omit for all")
    ap.add_argument("--version", default="before", choices=VERSIONS)
    args = ap.parse_args(argv)

    if args.wcag and args.wcag not in WCAG20_A_AA:
        print(f"{args.wcag} is not a WCAG 2.0 Level A/AA criterion; ground truth skips AAA rows.",
              file=sys.stderr)
        return 1
    try:
        rows = candidates(args.page, args.wcag, args.version)
    except FindingsFileError as e:
        print(e, file=sys.stderr)
        return 1

    what = f"{args.version}/{args.page}" + (f" {args.wcag}" if args.wcag else "")
    if not rows:
        print(f"No findings for {what}. If W3C fails it here, the ground-truth row is `missed`.")
        return 0
    print(format_rows(rows))
    print(f"\n{len(rows)} finding(s) for {what}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
