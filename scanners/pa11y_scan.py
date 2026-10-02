"""A3. Run Pa11y (HTML_CodeSniffer) on each W3C BAD page and save the results.

Writes data/scans/pa11y_{version}_{page}.json:
  {"url", "pa11y_version", "command", "issues": [...]}
Each issue is Pa11y's own object (code, type, message, context, selector, ...)
plus an "_adj" block:
  wcag_sc          criterion parsed from the HTMLCS code, e.g. "1.1.1" (None if absent)
  outer_html       full outerHTML of the element, read in Playwright
  outer_html_len   length before the cap
  in_wrapper       True if inside W3C's #meta-header or #meta-footer (merge filters on this)
  dom_index        element position in document order (see axe_scan.py)

Warnings and notices are included on purpose. axe's "incomplete" results on these
pages are almost all background-image contrast, so HTMLCS warnings/notices
("check that alt text is appropriate", "heading markup should be used...", link
purpose) are the main source of grey areas.

Pa11y exits 2 when it finds issues; that is a normal result here. Exit 1 is an error.
Needs: npm install (pa11y in package.json).
Run from the repo root:
  python scanners/pa11y_scan.py                 # all 4 Before pages
  python scanners/pa11y_scan.py --version all --page home
"""

import argparse
import collections
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from playwright.sync_api import sync_playwright  # noqa: E402

from capture.capture import PAGES, VERSIONS, VIEWPORT, page_url  # noqa: E402
from scanners.axe_scan import ENRICH_JS, HTML_CAP, WRAPPER  # noqa: E402

PA11Y = REPO / "node_modules" / ".bin" / "pa11y"
OUT = REPO / "data" / "scans"
SC_RE = re.compile(r"Guideline\d+_\d+\.(\d+)_(\d+)_(\d+)")


def pa11y_version():
    pkg = REPO / "node_modules" / "pa11y" / "package.json"
    return json.loads(pkg.read_text())["version"]


def run_pa11y(url):
    cmd = [str(PA11Y), "--standard", "WCAG2AA", "--runner", "htmlcs",
           "--include-warnings", "--include-notices",
           "--reporter", "json", "--timeout", "60000", url]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO)
    if proc.returncode not in (0, 2):
        raise RuntimeError(
            f"pa11y exited {proc.returncode} for {url}\n"
            f"stderr:\n{proc.stderr.strip()}\nstdout (first 500):\n{proc.stdout[:500]}")
    return json.loads(proc.stdout), cmd


def sc_from_code(code):
    m = SC_RE.search(code or "")
    return ".".join(m.groups()) if m else None


def scan_page(page, version, name):
    url = page_url(version, name)
    issues, cmd = run_pa11y(url)

    page.goto(url, wait_until="networkidle")
    selectors = [i.get("selector") or "" for i in issues]
    extra = page.evaluate(ENRICH_JS, [selectors, WRAPPER, HTML_CAP])
    for issue, sel, e in zip(issues, selectors, extra):
        adj = dict(e) if sel else {"error": "pa11y gave no selector (page-level issue)"}
        adj["wcag_sc"] = sc_from_code(issue.get("code"))
        issue["_adj"] = adj

    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"pa11y_{version}_{name}.json"
    payload = {"url": url, "pa11y_version": pa11y_version(),
               "command": " ".join(Path(c).name if c == str(PA11Y) else c for c in cmd),
               "issues": issues}
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return issues, out


def summarize(issues):
    by_type = collections.Counter(i["type"] for i in issues)
    wrap = sum(1 for i in issues if i["_adj"].get("in_wrapper"))
    no_sel = sum(1 for i in issues if "error" in i["_adj"])
    no_sc = sum(1 for i in issues if not i["_adj"]["wcag_sc"])
    return (f"error {by_type['error']:3}  warning {by_type['warning']:3}  "
            f"notice {by_type['notice']:3}  (wrapper {wrap}, no selector {no_sel}, no SC {no_sc})")


def main():
    ap = argparse.ArgumentParser(description="Run Pa11y on W3C BAD pages.")
    ap.add_argument("--version", default="before", choices=VERSIONS + ["all"])
    ap.add_argument("--page", default="all", choices=PAGES + ["all"])
    a = ap.parse_args()
    if not PA11Y.exists():
        sys.exit(f"pa11y not found at {PA11Y.relative_to(REPO)}. Run: npm install")
    versions = VERSIONS if a.version == "all" else [a.version]
    names = PAGES if a.page == "all" else [a.page]

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        for v in versions:
            for n in names:
                issues, out = scan_page(page, v, n)
                print(f"{v:6} {n:8} {summarize(issues)}  -> {out.relative_to(REPO)}")
        browser.close()
    print("pa11y", pa11y_version())


if __name__ == "__main__":
    main()
