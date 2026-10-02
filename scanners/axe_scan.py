"""A2. Run axe-core on each W3C BAD page and save the raw results.

Writes data/scans/axe_{version}_{page}.json: axe's own result object, limited to
WCAG 2.0 A and AA rules (tags wcag2a, wcag2aa), keeping both result types we need:
  violations  axe is sure: candidate machine-verified findings
  incomplete  axe can't decide ("needs review"): the grey areas this project is about

For every flagged element we add an "_adj" block with:
  outer_html       the element's full outerHTML read from the live page
                   (axe's own "html" field can be shortened)
  outer_html_len   length before our cap, so truncation is visible
  in_wrapper       True if the element is inside W3C's wrapper (#meta-header at
                   the top or #meta-footer at the bottom), which is not part of the demo.
  dom_index        position in document.querySelectorAll('*'); the merge step's
                   element identity (selectors differ between scanners). The merge step filters on this;
                   the scan keeps everything so nothing is lost here.

The whole document is scanned on purpose: page-level failures such as a missing
<html lang> sit outside the demo's #page div.

Needs: npm install (axe-core in package.json).
Run from the repo root:
  python scanners/axe_scan.py                  # all 4 Before pages
  python scanners/axe_scan.py --version all --page home
"""

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))  # so "python scanners/axe_scan.py" can import capture/

from playwright.sync_api import sync_playwright  # noqa: E402

from capture.capture import PAGES, VERSIONS, VIEWPORT, page_url  # noqa: E402

AXE = REPO / "node_modules" / "axe-core" / "axe.min.js"
OUT = REPO / "data" / "scans"
WRAPPER = "#meta-header, #meta-footer"  # W3C demo chrome, not part of CityLights
HTML_CAP = 5000  # characters; enough for any element on the BAD pages

AXE_OPTIONS = {
    "runOnly": {"type": "tag", "values": ["wcag2a", "wcag2aa"]},
    # Full node lists only for the two types we use; passes/inapplicable stay small.
    "resultTypes": ["violations", "incomplete"],
}

# Runs in the page: for each selector, return full outerHTML and wrapper membership.
ENRICH_JS = """
([selectors, wrapper, cap]) => {
 const all = Array.from(document.querySelectorAll("*"));
 return selectors.map(sel => {
  let el = null;
  try { el = document.querySelector(sel); } catch (e) { return {error: String(e)}; }
  if (!el) return {error: "selector matched nothing"};
  // <html> and <body> contain the whole page; their opening tag is the evidence.
  const whole = el === document.documentElement || el === document.body;
  const html = whole ? el.outerHTML.slice(0, el.outerHTML.indexOf(">") + 1) : el.outerHTML;
  return {
    outer_html: html.length > cap ? html.slice(0, cap) : html,
    outer_html_len: html.length,
    in_wrapper: el.closest(wrapper) !== null,
    dom_index: all.indexOf(el),
  };
 });
}
"""


def node_selector(node):
    """axe gives target as a list; nested lists only appear for iframes/shadow DOM."""
    t = node.get("target") or []
    if len(t) == 1 and isinstance(t[0], str):
        return t[0]
    return None  # not expected on the BAD pages; recorded as an enrich error


def scan_page(page, version, name):
    page.goto(page_url(version, name), wait_until="networkidle")
    axe_tag = page.add_script_tag(path=str(AXE))
    results = page.evaluate("async (opts) => await axe.run(document, opts)", AXE_OPTIONS)
    # Take axe's <script> back out before counting element positions. Left in, it
    # shifts every dom_index after <head> by one versus a clean page (the Pa11y scan
    # and the merge step), so the same element would get two different indices.
    axe_tag.evaluate("e => e.remove()")

    nodes = [n for kind in ("violations", "incomplete") for r in results[kind] for n in r["nodes"]]
    selectors = [node_selector(n) or "" for n in nodes]
    extra = page.evaluate(ENRICH_JS, [selectors, WRAPPER, HTML_CAP])
    for n, sel, e in zip(nodes, selectors, extra, strict=True):
        n["_adj"] = e if sel else {"error": f"unsupported target {n.get('target')!r}"}

    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"axe_{version}_{name}.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return results, out


def summarize(results):
    def count(kind):
        rules = results[kind]
        nodes = [n for r in rules for n in r["nodes"]]
        wrap = sum(1 for n in nodes if n.get("_adj", {}).get("in_wrapper"))
        errs = sum(1 for n in nodes if "error" in n.get("_adj", {}))
        return len(rules), len(nodes), wrap, errs

    v, i = count("violations"), count("incomplete")
    return (f"violations {v[0]:2} rules/{v[1]:3} nodes (wrapper {v[2]})  "
            f"incomplete {i[0]:2} rules/{i[1]:3} nodes (wrapper {i[2]})  "
            f"enrich errors {v[3] + i[3]}")


def main():
    ap = argparse.ArgumentParser(description="Run axe-core on W3C BAD pages.")
    ap.add_argument("--version", default="before", choices=VERSIONS + ["all"])
    ap.add_argument("--page", default="all", choices=PAGES + ["all"])
    a = ap.parse_args()
    if not AXE.exists():
        sys.exit(f"axe-core not found at {AXE.relative_to(REPO)}. Run: npm install")
    versions = VERSIONS if a.version == "all" else [a.version]
    names = PAGES if a.page == "all" else [a.page]

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        for v in versions:
            for n in names:
                results, out = scan_page(page, v, n)
                print(f"{v:6} {n:8} {summarize(results)}  -> {out.relative_to(REPO)}")
        browser.close()
    print("axe-core", results["testEngine"]["version"])


if __name__ == "__main__":
    main()
