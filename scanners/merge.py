"""A4. Merge axe and Pa11y results into one findings list per page.

Reads  data/scans/axe_{v}_{p}.json and data/scans/pa11y_{v}_{p}.json
Writes data/findings/{v}_{p}.json        (committed; tracks B, C, D read it)
       data/findings/shots/{v}_{p}_el{dom_index}.jpg   padded element crops
       data/findings/shots/{v}_{p}_page.jpg            whole page, for page-level items

Rules (decided 2026-10-02, see docs/TRACKS.md):
- Drop anything inside W3C's wrapper (#meta-header, #meta-footer).
- Keep only WCAG 2.0 A/AA criteria (scanners/wcag.py).
- One finding per (page, element, criterion). Element = dom_index, because the two
  scanners write different selectors for the same element. Both scanners -> one
  finding with both listed in "sources" and "scanner_details".
- scanner_status: "violation" only when axe reports a violation. axe incomplete,
  every Pa11y error and every Pa11y warning -> "needs_review". Pa11y notice -> "notice".
  All notices are kept. Page-level notices (no element) attach to <html>.
- "source" stays the format's single string (axe-core when axe is involved).
  "sources", "scanner_status" and "scanner_details" are additive fields.
- "bucket" stays null: Track C's router decides it.
- IDs are F-{version}-{page}-el{dom_index:04d}-{criterion}, e.g. F-before-home-el0081-1.1.1.
  They name what the finding is (element + criterion), not where it sits in the list,
  so adding or dropping other findings never changes an existing ID. Unique because
  there is exactly one finding per (element, criterion). Findings are still written
  sorted by (dom_index, criterion, rule_id).
- Screenshots are written to a temporary folder and only swapped in once the whole
  page succeeds, so a crash never leaves findings pointing at deleted images.

Run from the repo root:
  python scanners/merge.py                    # all 4 Before pages
  python scanners/merge.py --version all
"""

import argparse
import collections
import json
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from playwright.sync_api import sync_playwright  # noqa: E402

from capture.capture import PAGES, VERSIONS, VIEWPORT, page_url  # noqa: E402
from scanners.findings import validate_findings  # noqa: E402
from scanners.wcag import SECTION508_WEB, WCAG20_A_AA, axe_tags_to_sc  # noqa: E402

SCANS = REPO / "data" / "scans"
OUT = REPO / "data" / "findings"
SHOTS = OUT / "shots"
PAD = 40           # px of surrounding page around each element crop
STATUS_RANK = {"violation": 3, "needs_review": 2, "notice": 1}
PAGE_LEVEL = 0     # dom_index of <html>


def finding_id(version, name, dom_index, sc):
    """Stable ID from what the finding is: page, element and criterion."""
    return f"F-{version}-{name}-el{dom_index:04d}-{sc}"


def read_scan(path, script):
    if not path.exists():
        sys.exit(f"Missing {path.relative_to(REPO)}. Run: python scanners/{script} "
                 f"--version {path.stem.split('_')[1]} --page {path.stem.split('_')[2]}")
    return json.loads(path.read_text())


def load_hits(version, name):
    """Flatten both scanners into one list of hits; return hits and drop counts."""
    hits, dropped = [], collections.Counter()
    axe = read_scan(SCANS / f"axe_{version}_{name}.json", "axe_scan.py")
    captured_at = axe.get("timestamp")
    for kind, status in (("violations", "violation"), ("incomplete", "needs_review")):
        for rule in axe[kind]:
            for node in rule["nodes"]:
                adj = node["_adj"]
                if adj.get("in_wrapper"):
                    dropped["wrapper"] += len(axe_tags_to_sc(rule["tags"])) or 1
                    continue
                if "error" in adj:
                    dropped["axe_unresolved"] += 1
                    continue
                msg = (node["any"] or node["all"] or node["none"] or [{}])[0].get("message", "")
                for sc in axe_tags_to_sc(rule["tags"]):
                    if sc not in WCAG20_A_AA:
                        dropped["not_wcag20"] += 1
                        continue
                    hits.append({
                        "scanner": "axe-core", "status": status, "axe_type": kind,
                        "rule_id": rule["id"], "message": msg or rule["help"], "sc": sc,
                        "dom_index": adj["dom_index"], "selector": node["target"][0],
                        "html": adj["outer_html"]})

    pa = read_scan(SCANS / f"pa11y_{version}_{name}.json", "pa11y_scan.py")["issues"]
    for i in pa:
        adj = i["_adj"]
        if adj.get("in_wrapper"):
            dropped["wrapper"] += 1
            continue
        sc = adj["wcag_sc"]
        if sc not in WCAG20_A_AA:
            dropped["not_wcag20"] += 1
            continue
        page_level = not i.get("selector")
        if "error" in adj and not page_level:
            dropped["pa11y_unresolved"] += 1
            continue
        hits.append({
            "scanner": "pa11y", "status": "notice" if i["type"] == "notice" else "needs_review",
            "pa11y_type": i["type"], "rule_id": i["code"], "message": i["message"], "sc": sc,
            "dom_index": PAGE_LEVEL if page_level else adj["dom_index"],
            "selector": "html" if page_level else i["selector"],
            "html": "" if page_level else adj["outer_html"], "page_level": page_level})
    return hits, dropped, captured_at


def group(hits):
    groups = collections.defaultdict(list)
    for h in hits:
        groups[(h["dom_index"], h["sc"])].append(h)
    return groups


def build_finding(version, name, hs, url, captured_at):
    hs = sorted(hs, key=lambda h: (h["scanner"] != "axe-core", -STATUS_RANK[h["status"]]))
    primary = hs[0]
    sources = sorted({h["scanner"] for h in hs}, key=lambda s: s != "axe-core")
    status = max((h["status"] for h in hs), key=STATUS_RANK.get)
    details, seen = [], set()
    for h in hs:
        key = (h["scanner"], h["rule_id"], h["message"])
        if key in seen:
            continue
        seen.add(key)
        details.append({"scanner": h["scanner"], "rule_id": h["rule_id"],
                        "type": h.get("axe_type") or h.get("pa11y_type"),
                        "message": h["message"]})
    html = next((h["html"] for h in hs if h["html"]), "")
    return {
        "finding_id": finding_id(version, name, primary["dom_index"], primary["sc"]),
        "page": name,
        "site_version": version,
        "source": primary["scanner"],
        "sources": sources,
        "scanner_status": status,
        "rule_id": primary["rule_id"],
        "wcag": [primary["sc"]],
        "section508": [SECTION508_WEB],
        "bucket": None,
        "evidence": {
            "selector": primary["selector"],
            "html_snippet": html,
            "screenshot": None,  # filled in by take_shots
            "page_url": url,
            "captured_at": captured_at,
        },
        "scanner_details": details,
        "ai": {"case_for": "", "case_against": "", "citations_verified": False,
               "confidence": None, "prompt_version": ""},
        "review": {"final_status": None, "reviewer": None, "decided_at": None, "comment": None},
        "_dom_index": primary["dom_index"],
    }


# Runs in the page: bounding box (page coordinates) and outerHTML for each dom_index.
BOXES_JS = """
(indices) => {
  const all = Array.from(document.querySelectorAll("*"));
  return indices.map(i => {
    const el = all[i];
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return {x: r.left + window.scrollX, y: r.top + window.scrollY, w: r.width, h: r.height,
            html: el.outerHTML};
  });
}
"""


def take_shots(page, version, name, findings, url):
    """Padded crop per element; whole-page JPEG for page-level findings.

    New images go to a temporary folder first. Only after every image for this page
    is written are the old ones deleted and the new ones moved into place.
    """
    tmp = SHOTS / f".tmp_{version}_{name}"
    shutil.rmtree(tmp, ignore_errors=True)  # leftovers from a crashed run
    tmp.mkdir(parents=True)
    page.goto(url, wait_until="networkidle")
    doc_w = page.evaluate("document.documentElement.scrollWidth")
    doc_h = page.evaluate("document.documentElement.scrollHeight")
    indices = sorted({f["_dom_index"] for f in findings if f["_dom_index"] != PAGE_LEVEL})
    boxes = dict(zip(indices, page.evaluate(BOXES_JS, indices), strict=True))
    stats = collections.Counter()

    page_shot = tmp / f"{version}_{name}_page.jpg"
    page.screenshot(path=page_shot, full_page=True, type="jpeg", quality=60)
    paths = {PAGE_LEVEL: page_shot}
    for i, b in boxes.items():
        if not b or b["w"] < 1 or b["h"] < 1:
            stats["no_box"] += 1  # hidden, zero-size, or inside <head>
            continue
        x0, y0 = max(0, b["x"] - PAD), max(0, b["y"] - PAD)
        x1, y1 = min(doc_w, b["x"] + b["w"] + PAD), min(doc_h, b["y"] + b["h"] + PAD)
        if x1 - x0 < 1 or y1 - y0 < 1:
            stats["offscreen"] += 1  # e.g. text moved off-page for screen readers
            continue
        path = tmp / f"{version}_{name}_el{i:04d}.jpg"
        page.screenshot(path=path, full_page=True, type="jpeg", quality=70,
                        clip={"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0})
        paths[i] = path
        stats["shot"] += 1

    # All images written: swap them in. Other pages' images are untouched.
    for old in SHOTS.glob(f"{version}_{name}_*"):
        old.unlink()
    for new in tmp.iterdir():
        new.replace(SHOTS / new.name)
    tmp.rmdir()
    paths = {k: SHOTS / v.name for k, v in paths.items()}

    # Page-level Pa11y notices have no element; their evidence is the real <html> start tag.
    root_tag = page.evaluate(
        "(() => { const h = document.documentElement.outerHTML; return h.slice(0, h.indexOf('>') + 1); })()")
    for f in findings:
        if f["_dom_index"] == PAGE_LEVEL and not f["evidence"]["html_snippet"]:
            f["evidence"]["html_snippet"] = root_tag
        p = paths.get(f["_dom_index"])
        f["evidence"]["screenshot"] = str(p.relative_to(REPO)) if p else None
        b = boxes.get(f["_dom_index"])
        if b and f["evidence"]["html_snippet"] and not b["html"].startswith(f["evidence"]["html_snippet"][:200]):
            stats["dom_mismatch"] += 1  # page changed between scan and merge
    return stats


def merge_page(page, version, name):
    url = page_url(version, name)
    hits, dropped, captured_at = load_hits(version, name)
    groups = group(hits)
    findings = [build_finding(version, name, hs, url, captured_at) for hs in groups.values()]
    findings.sort(key=lambda f: (f["_dom_index"], [int(x) for x in f["wcag"][0].split(".")], f["rule_id"]))
    shot_stats = take_shots(page, version, name, findings, url)
    for f in findings:
        f["evidence"]["dom_index"] = f.pop("_dom_index")

    # Never write a file the other tracks can't load: check it against the format first.
    validate_findings(findings, f"{version}_{name} (before writing)")
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"{version}_{name}.json"
    out.write_text(json.dumps(findings, indent=2), encoding="utf-8")
    return findings, hits, dropped, shot_stats, out


def summarize(findings, hits, dropped, shots):
    st = collections.Counter(f["scanner_status"] for f in findings)
    both = sum(1 for f in findings if len(f["sources"]) == 2)
    axe_inc_hits = sum(1 for h in hits if h.get("axe_type") == "incomplete")
    axe_inc_kept = sum(1 for f in findings
                       if any(d["scanner"] == "axe-core" and d["type"] == "incomplete"
                              for d in f["scanner_details"]))
    return (f"{len(hits)} hits -> {len(findings)} findings "
            f"(violation {st['violation']}, needs_review {st['needs_review']}, notice {st['notice']}; "
            f"both scanners {both}) | dropped {dict(dropped)} | "
            f"axe incomplete in {axe_inc_hits} hits, kept in {axe_inc_kept} findings | shots {dict(shots)}")


def main():
    ap = argparse.ArgumentParser(description="Merge axe + Pa11y into findings.")
    ap.add_argument("--version", default="before", choices=VERSIONS + ["all"])
    ap.add_argument("--page", default="all", choices=PAGES + ["all"])
    a = ap.parse_args()
    versions = VERSIONS if a.version == "all" else [a.version]
    names = PAGES if a.page == "all" else [a.page]
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        for v in versions:
            for n in names:
                findings, hits, dropped, shots, out = merge_page(page, v, n)
                print(f"{v:6} {n:8} {summarize(findings, hits, dropped, shots)}  -> {out.relative_to(REPO)}")
        browser.close()


if __name__ == "__main__":
    main()
