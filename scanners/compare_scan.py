"""C. Page comparison for WCAG 2.0 3.2.4 (Consistent Identification), the (c) group in docs/missed_issues.md.

3.2.4: components with the same function on different pages are identified the same way.
This loads all 4 pages of one version, lists each page's links with their accessible name,
and treats links with the same target (href) on two or more pages as the same component,
e.g. the CityLights navigation. Two rules, both needs_review:

| rule_id                          | hit on a page where the repeated link...                      |
| adj.compare.unidentified         | has no accessible name ("not identified at all", W3C's wording) |
| adj.compare.inconsistent-name    | has a different name from the one most pages use              |

A target used by more than one link on a page is compared only when every page that links to
it has the same number of such links (they are paired in document order); otherwise it is
skipped, because the links can't be paired reliably.

The accessible name is a simplified version of the HTML-AAM rules: aria-labelledby, then
aria-label, then the link's text and its images' alt text, then title.

Writes data/scans/adj_compare_{version}_{page}.json for each page, in the shared hit format
(docs/TRACKS.md, "ADJ's own checks"). dom_index is the element's position in
document.querySelectorAll("*") on a clean page, as in axe_scan.py and rules_scan.py.

Note: the router sends needs_review hits on 3.2.4 to human-only (3.2.4 is human_only in the
corpus), so these hits are flagged for a person, not reported as issues.

Run from the repo root:
  python -m scanners.compare_scan                  # the 4 Before pages
  python -m scanners.compare_scan --version all
"""

import argparse
import collections
import json
import sys
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, sync_playwright

from capture.capture import PAGES, VERSIONS, VIEWPORT, page_url

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "data" / "scans"
TOOL_VERSION = "compare-v1"
WRAPPER = "#meta-header, #meta-footer"  # W3C demo chrome, not part of CityLights
HTML_CAP = 5000  # characters, the hit format's cap
GOTO_TIMEOUT = 60_000  # ms for one W3C page to load

MESSAGES = {
    "unidentified": "This link (to {target}) appears on {pages} of the {total} pages but has no accessible name "
                    "here: no text, no alt text on its image, no aria-label or title. The same component is not "
                    "identified (3.2.4).",
    "inconsistent-name": 'This link (to {target}) is named "{name}" here but "{usual}" on {others}. Components '
                         "with the same function should be identified the same way on every page (3.2.4).",
}

# Runs in the page. Returns every link in the demo's content with what compare() needs.
LINKS_JS = r"""
([wrapper, cap]) => {
  const ALL = Array.from(document.querySelectorAll("*"));
  const SCOPE = document.querySelector("#page") || document.body || document.documentElement;
  const clean = s => (s || "").replace(/\s+/g, " ").trim();
  function selectorFor(el) {
    const steps = [];
    for (let e = el; e && e !== document.documentElement; e = e.parentElement) {
      if (e.id && document.querySelectorAll("#" + CSS.escape(e.id)).length === 1) {
        steps.unshift("#" + CSS.escape(e.id));
        break;
      }
      let step = CSS.escape(e.localName);
      const same = Array.from(e.parentElement.children).filter(c => c.localName === e.localName);
      if (same.length > 1) step += `:nth-of-type(${same.indexOf(e) + 1})`;
      steps.unshift(step);
    }
    if (!steps[0].startsWith("#")) steps.unshift("html");
    return steps.join(" > ");
  }
  function contentName(el) {
    let out = "";
    for (const n of el.childNodes) {
      if (n.nodeType === Node.TEXT_NODE) out += " " + n.textContent;
      if (n.nodeType !== Node.ELEMENT_NODE || n.getAttribute("aria-hidden") === "true") continue;
      if (n.matches("img, area, input[type=image]")) out += " " + (n.getAttribute("alt") || "");
      else out += " " + contentName(n);
    }
    return out;
  }
  function nameOf(el) {
    const ids = (el.getAttribute("aria-labelledby") || "").split(/\s+/).filter(Boolean);
    const byIds = clean(ids.map(id => document.getElementById(id)).filter(Boolean).map(e => e.textContent).join(" "));
    return byIds || clean(el.getAttribute("aria-label")) || clean(contentName(el)) || clean(el.getAttribute("title"));
  }
  return Array.from(SCOPE.querySelectorAll("a[href]")).map(a => {
    const html = a.outerHTML;
    return {target: a.href, name: nameOf(a), dom_index: ALL.indexOf(a), selector: selectorFor(a),
            html: html.length > cap ? html.slice(0, cap) : html, in_wrapper: a.closest(wrapper) !== null};
  });
}
"""


def links_on(page: Page) -> list[dict]:
    """The links on the page that is loaded now (the tests load their own HTML)."""
    return page.evaluate(LINKS_JS, [WRAPPER, HTML_CAP])


def short(target: str) -> str:
    """A readable target for messages: the file name, or the javascript: URL as written."""
    if target.startswith("javascript:"):
        return target
    return target.split("#")[0].rstrip("/").rsplit("/", 1)[-1] or target


def _hit(link: dict, rule: str, **fill) -> dict:
    return {"rule_id": f"adj.compare.{rule}", "sc": "3.2.4", "status": "needs_review",
            "message": MESSAGES[rule].format(**fill), "dom_index": link["dom_index"],
            "selector": link["selector"], "html": link["html"], "in_wrapper": link["in_wrapper"]}


def compare(links_by_page: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """Hits per page, given each page's links. Pure: no browser, so it is easy to test."""
    hits: dict[str, list[dict]] = {p: [] for p in links_by_page}
    groups = {p: collections.defaultdict(list) for p in links_by_page}
    for p, links in links_by_page.items():
        for link in links:
            if not link["in_wrapper"]:
                groups[p][link["target"]].append(link)
    if len(groups) < 2:
        return hits
    targets = sorted({t for g in groups.values() for t in g})
    for target in targets:
        pages = [p for p in groups if target in groups[p]]
        counts = {len(groups[p][target]) for p in pages}
        if len(pages) < 2 or len(counts) != 1:
            continue  # on one page only, or different numbers of links to it: nothing to pair
        for i in range(counts.pop()):
            same = {p: groups[p][target][i] for p in pages}
            names = collections.Counter(link["name"].casefold() for link in same.values() if link["name"])
            usual = names.most_common(1)[0][0] if names else None
            for p, link in same.items():
                if not link["name"]:
                    hits[p].append(_hit(link, "unidentified", target=short(target), pages=len(same), total=len(groups)))
                elif usual is not None and link["name"].casefold() != usual:
                    usual_name = next(x["name"] for x in same.values() if x["name"].casefold() == usual)
                    others = ", ".join(q for q, x in same.items() if x["name"].casefold() == usual)
                    hits[p].append(_hit(link, "inconsistent-name", target=short(target), name=link["name"],
                                        usual=usual_name, others=others))
    for p in hits:
        hits[p].sort(key=lambda h: h["dom_index"])
    return hits


def build_results(version: str, links_by_page: dict[str, list[dict]], urls: dict[str, str]) -> list[dict]:
    hits = compare(links_by_page)
    return [{"check": "compare", "version": version, "page": p, "url": urls[p], "tool_version": TOOL_VERSION,
             "hits": hits[p]} for p in links_by_page]


def write_result(result: dict, out_dir: Path = OUT) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"adj_compare_{result['version']}_{result['page']}.json"
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return out


def scan_version(page: Page, version: str) -> list[dict]:
    """Load all 4 pages of one version and compare them. Raises PlaywrightError if a page fails."""
    links, urls = {}, {}
    for name in PAGES:
        urls[name] = page_url(version, name)
        page.goto(urls[name], wait_until="networkidle", timeout=GOTO_TIMEOUT)
        links[name] = links_on(page)
    return build_results(version, links, urls)


def main() -> int:
    ap = argparse.ArgumentParser(description="Compare the W3C BAD pages of one version for 3.2.4.")
    ap.add_argument("--version", default="before", choices=VERSIONS + ["all"])
    a = ap.parse_args()
    versions = VERSIONS if a.version == "all" else [a.version]

    failed = 0
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport=VIEWPORT)
            for v in versions:
                try:
                    results = scan_version(page, v)
                except PlaywrightError as e:
                    # All pages are compared together, so one failure means no file for any of them.
                    for n in PAGES:
                        (OUT / f"adj_compare_{v}_{n}.json").unlink(missing_ok=True)
                    why = ((e.message or str(e)).splitlines() or ["unknown error"])[0]
                    print(f"{v:6} FAILED, no files written: {why}", file=sys.stderr)
                    failed += 1
                    continue
                for r in results:
                    out = write_result(r)
                    rules = collections.Counter(h["rule_id"].removeprefix("adj.compare.") for h in r["hits"])
                    detail = ", ".join(f"{k} {n}" for k, n in rules.items()) or "none"
                    print(f"{v:6} {r['page']:8} {len(r['hits']):3} hits ({detail})  -> {out.relative_to(REPO)}")
        finally:
            browser.close()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
