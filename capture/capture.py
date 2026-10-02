"""A1. Capture each W3C BAD page once, so later steps reuse it instead of re-fetching.

For each page this writes data/captures/{version}/{page}/:
  page.html       rendered DOM after JavaScript runs
  full.png        full-page screenshot
  a11y_tree.yaml  Playwright aria snapshot (page.aria_snapshot())
  a11y_tree.json  Chromium's full accessibility tree via CDP (Accessibility.getFullAXTree)
  meta.json       URL, version, page, timestamp, tool versions

Why two trees: Playwright 1.63 removed page.accessibility.snapshot(), which the week 2
brief used. aria_snapshot() is the supported replacement but returns YAML text.
The CDP tree is JSON and keeps nodes Chromium marks as ignored (for example an
img with alt=""), which the grey-area agents need.

Run from the repo root:
  python capture/capture.py                      # all 4 Before pages
  python capture/capture.py --version before --page home
  python capture/capture.py --version all        # Before and After
"""

import argparse
import datetime
import json
from importlib.metadata import version as pkg_version
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

BASE = "https://www.w3.org/WAI/demos/bad"
PAGES = ["home", "news", "tickets", "survey"]
VERSIONS = ["before", "after"]
VIEWPORT = {"width": 1280, "height": 900}

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "data" / "captures"


def page_url(version: str, name: str) -> str:
    return f"{BASE}/{version}/{name}.html"


def capture_page(page: Page, version: str, name: str) -> Path:
    """Capture an already-open Playwright page. Reusable by the scanners."""
    d = OUT / version / name
    d.mkdir(parents=True, exist_ok=True)
    url = page_url(version, name)
    page.goto(url, wait_until="networkidle")

    (d / "page.html").write_text(page.content(), encoding="utf-8")
    page.screenshot(path=d / "full.png", full_page=True)
    (d / "a11y_tree.yaml").write_text(page.aria_snapshot(), encoding="utf-8")

    cdp = page.context.new_cdp_session(page)
    try:
        tree = cdp.send("Accessibility.getFullAXTree")
    finally:
        cdp.detach()
    (d / "a11y_tree.json").write_text(json.dumps(tree, indent=2), encoding="utf-8")

    meta = {
        "url": url,
        "version": version,
        "page": name,
        "captured_at": datetime.datetime.now(datetime.timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "viewport": VIEWPORT,
        "playwright": pkg_version("playwright"),
        "browser": page.context.browser.version if page.context.browser else None,
    }
    (d / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return d


def element_shot(page: Page, selector: str, path, timeout_ms: int = 3000):
    """Screenshot one element. Returns the path, or None if it can't be shot.

    Failure is expected on a deliberately broken site: hidden, zero-size or
    detached elements, and selectors that match nothing.
    """
    try:
        el = page.locator(selector).first
        el.scroll_into_view_if_needed(timeout=timeout_ms)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        el.screenshot(path=path, timeout=timeout_ms)
        return Path(path)
    except Exception as e:  # noqa: BLE001 - any Playwright failure means "no picture"
        print(f"  element_shot skipped {selector!r}: {type(e).__name__}: {str(e).splitlines()[0]}")
        return None


def capture(versions, names):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        for v in versions:
            for n in names:
                d = capture_page(page, v, n)
                files = sorted(f.name for f in d.iterdir())
                print(f"{v:6} {n:8} -> {d.relative_to(REPO)}  {files}")
        browser.close()


def main():
    ap = argparse.ArgumentParser(description="Capture W3C BAD pages.")
    ap.add_argument("--version", default="before", choices=VERSIONS + ["all"])
    ap.add_argument("--page", default="all", choices=PAGES + ["all"])
    a = ap.parse_args()
    versions = VERSIONS if a.version == "all" else [a.version]
    names = PAGES if a.page == "all" else [a.page]
    capture(versions, names)


if __name__ == "__main__":
    main()
