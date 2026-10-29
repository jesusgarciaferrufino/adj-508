"""Keyboard walkthrough and reading-order view (experimental, Oct 29): what a manual auditor
does with the Tab key and a screen reader, as an ADJ check in the shared hit format.

Per page:
1. Tab walk. Load the page clean, then press Tab up to MAX_TABS times. Every element that
   receives focus is a tab stop: its dom_index, box, and computed style while focused (outline,
   border, box-shadow, background, text-decoration, colour, on the element, its parent and up to
   20 descendants). The walk ends when focus leaves the page and comes back to the first stop
   ("wrapped"), a stop repeats without leaving the page first ("repeat": a focus trap), Tab
   twice reaches nothing, or MAX_TABS presses are used.
2. Reading-order view. The page's text in accessibility-tree order (Chromium's
   Accessibility.getFullAXTree), from #page down, one line per text node. Evidence only.
3. Artifacts for reviewers (not findings): data/scans/keyboard_{v}_{p}.png, the full page with a
   numbered box on every tab stop, and data/scans/keyboard_{v}_{p}_reading_order.txt.

Writes data/scans/adj_keyboard_{version}_{page}.json ({"check": "keyboard", "tool_version":
"keyboard-v1", "walk": {...}, "hits": [...]}); scanners/merge.py reads it like every adj_* file.

Every hit is needs_review (router: grey, so FOR/AGAINST and a human judge it). Stops inside
W3C's wrapper (#meta-header, #meta-footer) are walked but never numbered or reported.

| rule_id                        | criterion | hit when                                                    |
| adj.keyboard.unreachable       | 2.1.1     | a visible link, button, form control or onclick element is   |
|                                |           | never a tab stop (and no tab stop is inside it or around it) |
| adj.keyboard.no-visible-focus  | 2.4.7     | a tab stop that keeps focus looks the same focused/unfocused |
| adj.keyboard.order-jump        | 2.4.3     | a stop is >300px above the one before it, in the same column |
|                                |           | (they overlap horizontally), AND the next stop is >300px     |
|                                |           | below it: focus order bounces against the layout. Moving up  |
|                                |           | to start a new column is normal and is not a hit.            |
| adj.keyboard.focus-lost        | 2.4.3     | focus leaves the element as soon as it arrives (to body)     |

unreachable runs only when the walk finished normally (wrapped round, left the page, or found
nothing to focus); after a trap or MAX_TABS presses the unvisited part of the page is unknown.

dom_index values are read before anything touches the page. The only page changes are focus
listeners (no nodes), finishing CSS transitions before a style is read, and the numbered overlay,
which is added after every hit is computed and removed after the screenshot.

Run from the repo root:
  python -m scanners.keyboard_scan                       # all 4 Before pages
  python -m scanners.keyboard_scan --version all --page survey
"""

import argparse
import json
import sys
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, sync_playwright

from capture.capture import PAGES, VERSIONS, VIEWPORT, page_url
from scanners.rules_scan import HELPERS  # selectorFor, visible, inWrapper, text, quote (Daniel's, read-only)

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "data" / "scans"
TOOL_VERSION = "keyboard-v1"
GOTO_TIMEOUT = 60_000  # ms for one W3C page to load
MAX_TABS = 150
JUMP_PX = 300  # order-jump threshold, vertical page pixels
STATUS = "needs_review"  # every hit: a human and FOR/AGAINST judge it

MESSAGES = {
    "unreachable": '{what} "{name}" can be clicked but is never reached with the Tab key, so a keyboard user '
                   "can't operate it (2.1.1).",
    "no-visible-focus": 'Tab stop {n} ({what} "{name}") looks the same focused and unfocused: no change to its '
                        "outline, border, shadow, background, colour or underline (F78).",
    "order-jump": 'Tab stop {n} ({what} "{name}") is {up}px above stop {prev}, then stop {next} jumps back down '
                  "{down}px: the focus order does not follow the visual order ({ref}).",
    "focus-lost": 'Tab stop {n} ({what} "{name}") loses focus as soon as it gets it: focus goes back to the page '
                  "itself, so the keyboard user can't see or use where they are (F55).",
}
CRITERIA = {"unreachable": "2.1.1", "no-visible-focus": "2.4.7", "order-jump": "2.4.3", "focus-lost": "2.4.3"}

# Installed once per page, before the walk. Keeps the clean element list and logs every focus.
SETUP_JS = r"""
() => { HELPERS_HERE
  const PROPS = ["outline-style", "outline-width", "outline-color", "outline-offset", "box-shadow",
                 "background-color", "background-image", "color", "text-decoration-line",
                 "text-decoration-style", "text-decoration-color"];
  const SIDES = ["top", "right", "bottom", "left"];
  function look(el) {   // one element's visible focus-relevant style, normalised
    const cs = getComputedStyle(el);
    const parts = PROPS.map(p => cs.getPropertyValue(p));
    if (cs.outlineStyle === "none" || cs.outlineWidth === "0px") parts[0] = parts[1] = parts[2] = parts[3] = "none";
    for (const s of SIDES) {
      const st = cs.getPropertyValue(`border-${s}-style`), w = cs.getPropertyValue(`border-${s}-width`);
      parts.push(st === "none" || w === "0px" ? "none" : `${st} ${w} ${cs.getPropertyValue(`border-${s}-color`)}`);
    }
    return parts.join("|");
  }
  function snap(el) {   // the element, its parent and up to 20 descendants: where an indicator could show
    for (const a of el.getAnimations({subtree: true})) { try { a.finish(); } catch (e) {} }
    const els = [el, el.parentElement, ...Array.from(el.querySelectorAll("*")).slice(0, 20)].filter(Boolean);
    return els.map(look).join("#");
  }
  function box(el) {
    const r = el.getBoundingClientRect();
    return {x: r.left + scrollX, y: r.top + scrollY, w: r.width, h: r.height};
  }
  window.__adj = {ALL, log: [], snap, box, inWrapper, selectorFor, visible, text, quote};
  document.addEventListener("focus", e => {   // capture phase: runs before the element's own onfocus
    const el = e.target;
    if (!(el instanceof Element)) return;
    window.__adj.log.push({i: ALL.indexOf(el), box: box(el), wrapper: inWrapper(el)});
  }, true);
  if (document.activeElement && document.activeElement !== document.body) document.activeElement.blur();
  return ALL.length;
}
""".replace("HELPERS_HERE", HELPERS)

# After each Tab: the focus events since the last press, where focus is now, and (if it stayed)
# the focused element's settled style.
READ_JS = r"""
() => {
  const A = window.__adj, events = A.log.splice(0), act = document.activeElement;
  const at = act && act !== document.body && act !== document.documentElement ? A.ALL.indexOf(act) : -1;
  return {events, active: at, look: at >= 0 ? A.snap(act) : null};
}
"""

# Facts about elements by dom_index, for messages and hits.
DESCRIBE_JS = r"""
(indices) => indices.map(i => {
  const A = window.__adj, el = A.ALL[i], CAP = 5000;
  const what = el.localName === "a" ? "link" : el.localName === "area" ? "image-map area"
             : ["input", "select", "textarea", "button"].includes(el.localName) ? el.localName
             : el.localName + " element";
  const img = el.querySelector("img[alt]");
  const raw = el.getAttribute("aria-label") || A.text(el) || (img && img.alt) || el.getAttribute("alt")
              || el.getAttribute("title") || el.getAttribute("value") || el.getAttribute("name") || "";
  const name = A.quote(raw.trim(), 40);
  const html = el.outerHTML;
  return {what, name: name || "(no name)", selector: A.selectorFor(el), in_wrapper: A.inWrapper(el),
          html: html.length > CAP ? html.slice(0, CAP) : html,
          tabindex: parseInt(el.getAttribute("tabindex") || "0", 10)};
})
"""

# Elements a pointer user can click: links, buttons, controls, onclick. Visible and outside the
# wrapper; image-map areas count when their image is visible.
ACTIONABLE_JS = r"""
() => {
  const A = window.__adj;
  const sel = "a[href], area[href], button, input:not([type=hidden]), select, textarea, [onclick]";
  return A.ALL.map((el, i) => [el, i]).filter(([el]) => {
    if (!el.matches(sel) || el === document.body || el === document.documentElement) return false;
    if (el.matches(":disabled") || A.inWrapper(el)) return false;
    if (el.localName === "area") {
      const map = el.closest("map");
      const img = map && map.name && document.querySelector(`img[usemap="#${CSS.escape(map.name)}"]`);
      return !!img && A.visible(img);
    }
    return A.visible(el);
  }).map(([, i]) => i);
}
"""

# Is element a inside element b, or b inside a?
NESTED_JS = r"""
([i, reached]) => { const A = window.__adj, el = A.ALL[i];
  return reached.some(j => { const o = A.ALL[j]; return el.contains(o) || o.contains(el); }); }
"""

UNFOCUSED_JS = r"""
(indices) => { if (document.activeElement && document.activeElement !== document.body) document.activeElement.blur();
  return indices.map(i => window.__adj.snap(window.__adj.ALL[i])); }
"""

OVERLAY_JS = r"""
(stops) => {
  const layer = document.createElement("div");
  layer.id = "__adj_keyboard_overlay";
  layer.style.cssText = "position:absolute;left:0;top:0;width:0;height:0;z-index:2147483647;pointer-events:none";
  for (const s of stops) {
    const b = document.createElement("div");
    b.style.cssText = `position:absolute;left:${s.x - 2}px;top:${s.y - 2}px;width:${Math.max(s.w, 4)}px;` +
      `height:${Math.max(s.h, 4)}px;border:2px solid #c00;box-sizing:content-box`;
    const n = document.createElement("span");
    n.textContent = s.n;
    n.style.cssText = "position:absolute;left:-2px;top:-18px;background:#c00;color:#fff;font:bold 12px/16px " +
      "sans-serif;padding:0 4px;border-radius:2px";
    b.appendChild(n);
    layer.appendChild(b);
  }
  document.body.appendChild(layer);
}
"""


def tab_walk(page: Page) -> dict:
    """Press Tab until the walk ends. Returns the stops in order and why the walk ended."""
    page.evaluate(SETUP_JS)
    stops, seen, empty, end, presses = [], set(), 0, "max-presses", 0
    while presses < MAX_TABS:
        presses += 1
        page.keyboard.press("Tab")
        r = page.evaluate(READ_JS)
        events = [e for e in r["events"] if e["i"] >= 0]
        if not events:
            empty += 1
            if empty >= 2:
                end = "left-page" if stops else "nothing-to-focus"
                break
            continue
        came_back = empty > 0  # a real wrap leaves the page (an empty press) before the first stop again
        empty = 0
        stop_now = None
        for k, e in enumerate(events):
            if stops and e["i"] == stops[0]["i"]:
                end = "wrapped" if came_back and k == 0 else "repeat"  # straight back to the start: a trap
                stop_now = True
                break
            if e["i"] in seen:
                end = "repeat"
                stop_now = True
                break
            last = k == len(events) - 1
            stops.append({**e, "lost": last and r["active"] != e["i"] and r["active"] < 0,
                          "look": r["look"] if last and r["active"] == e["i"] else None})
            seen.add(e["i"])
        if stop_now:
            break
    return {"stops": stops, "end": end, "presses": presses}


def _hit(rule: str, d: dict, fill: dict, dom_index: int) -> dict:
    return {"rule_id": f"adj.keyboard.{rule}", "sc": CRITERIA[rule], "status": STATUS,
            "message": MESSAGES[rule].format(what=d["what"], name=d["name"], **fill), "dom_index": dom_index,
            "selector": d["selector"], "html": d["html"], "in_wrapper": d["in_wrapper"]}


def _same_column(a: dict, b: dict) -> bool:
    """Do the two boxes overlap horizontally? A jump up into another column is a new column, not
    a misordered one (a nav column next to the content would otherwise fire on every page)."""
    return a["x"] < b["x"] + b["w"] and b["x"] < a["x"] + a["w"]


def check_walk(page: Page, walk: dict) -> tuple[list[dict], list[dict]]:
    """The hits, and the numbered stops (outside the wrapper) for the overlay."""
    stops = [s for s in walk["stops"] if not s["wrapper"]]
    info = dict(zip([s["i"] for s in stops], page.evaluate(DESCRIBE_JS, [s["i"] for s in stops]), strict=True))
    numbered = [{**s, "n": n} for n, s in enumerate(stops, start=1)]
    hits = []

    for s in numbered:
        if s["lost"]:
            hits.append(_hit("focus-lost", info[s["i"]], {"n": s["n"]}, s["i"]))

    kept = [s for s in numbered if not s["lost"] and s["look"] is not None]
    unfocused = page.evaluate(UNFOCUSED_JS, [s["i"] for s in kept]) if kept else []
    for s, plain in zip(kept, unfocused, strict=True):
        if s["look"] == plain:
            hits.append(_hit("no-visible-focus", info[s["i"]], {"n": s["n"]}, s["i"]))

    seq = [s for s in numbered if s["box"]["w"] > 0 and s["box"]["h"] > 0]
    for prev, cur, nxt in zip(seq, seq[1:], seq[2:], strict=False):  # consecutive triples
        up, down = prev["box"]["y"] - cur["box"]["y"], nxt["box"]["y"] - cur["box"]["y"]
        if up > JUMP_PX and down > JUMP_PX and _same_column(prev["box"], cur["box"]):
            positive = any(info[s["i"]]["tabindex"] > 0 for s in (prev, cur, nxt))
            hits.append(_hit("order-jump", info[cur["i"]], {
                "n": cur["n"], "prev": prev["n"], "next": nxt["n"], "up": round(up), "down": round(down),
                "ref": "F44" if positive else "2.4.3"}, cur["i"]))

    if walk["end"] in ("wrapped", "left-page", "nothing-to-focus"):
        reached = [s["i"] for s in walk["stops"]]
        missing = [i for i in page.evaluate(ACTIONABLE_JS)
                   if i not in set(reached) and not page.evaluate(NESTED_JS, [i, reached])]
        for i, d in zip(missing, page.evaluate(DESCRIBE_JS, missing) if missing else [], strict=True):
            hits.append(_hit("unreachable", d, {}, i))
    return hits, numbered


def reading_order(page: Page) -> list[str]:
    """Text in accessibility-tree order, from #page down (the whole page when there is no #page)."""
    cdp = page.context.new_cdp_session(page)
    try:
        cdp.send("DOM.enable")
        cdp.send("Accessibility.enable")
        nodes = cdp.send("Accessibility.getFullAXTree")["nodes"]
        by_id = {n["nodeId"]: n for n in nodes}
        start = nodes[0] if nodes else None
        root = cdp.send("DOM.getDocument", {"depth": 0})["root"]["nodeId"]
        found = cdp.send("DOM.querySelector", {"nodeId": root, "selector": "#page"})["nodeId"]
        if found:
            backend = cdp.send("DOM.describeNode", {"nodeId": found})["node"]["backendNodeId"]
            start = next((n for n in nodes if n.get("backendDOMNodeId") == backend), start)
    finally:
        cdp.detach()
    lines, stack = [], [start] if start else []
    while stack:
        n = stack.pop()
        if not n.get("ignored"):
            role = (n.get("role") or {}).get("value")
            name = " ".join(str((n.get("name") or {}).get("value") or "").split())
            if role == "StaticText" and name:
                lines.append(name)
            elif role in ("image", "img") and name:
                lines.append(f"[image] {name}")
        stack.extend(by_id[c] for c in reversed(n.get("childIds") or []) if c in by_id)
    return lines


def build_result(page: Page, version: str, name: str, url: str, artifacts_dir: Path | None = None) -> dict:
    """The file's contents for the page that is loaded now. With artifacts_dir, also writes the
    numbered screenshot and the reading-order text there."""
    walk = tab_walk(page)
    hits, numbered = check_walk(page, walk)
    lines = reading_order(page)  # before the overlay, so the overlay's numbers aren't read out
    if artifacts_dir is not None:
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        (artifacts_dir / f"keyboard_{version}_{name}_reading_order.txt").write_text(
            "\n".join(lines) + "\n", encoding="utf-8")
        page.evaluate(OVERLAY_JS, [{**s["box"], "n": s["n"]} for s in numbered])
        try:
            page.screenshot(path=artifacts_dir / f"keyboard_{version}_{name}.png", full_page=True)
        finally:
            page.evaluate("() => document.getElementById('__adj_keyboard_overlay')?.remove()")
    return {"check": "keyboard", "version": version, "page": name, "url": url, "tool_version": TOOL_VERSION,
            "walk": {"presses": walk["presses"], "stops": len(walk["stops"]), "numbered_stops": len(numbered),
                     "end": walk["end"], "reading_order_lines": len(lines)},
            "hits": hits}


def write_result(result: dict, out_dir: Path = OUT) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"adj_keyboard_{result['version']}_{result['page']}.json"
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return out


def scan_page(page: Page, version: str, name: str) -> tuple[dict, Path]:
    url = page_url(version, name)
    page.goto(url, wait_until="networkidle", timeout=GOTO_TIMEOUT)
    result = build_result(page, version, name, url, artifacts_dir=OUT)
    return result, write_result(result)


def summarize(result: dict) -> str:
    counts: dict[str, int] = {}
    for h in result["hits"]:
        short = h["rule_id"].removeprefix("adj.keyboard.")
        counts[short] = counts.get(short, 0) + 1
    w = result["walk"]
    per_rule = ", ".join(f"{r} {n}" for r, n in counts.items()) or "none"
    return f"{len(result['hits']):3} hits ({per_rule}) | {w['numbered_stops']} tab stops, walk {w['end']}"


def main() -> int:
    ap = argparse.ArgumentParser(description="Keyboard walkthrough and reading-order view on W3C BAD pages.")
    ap.add_argument("--version", default="before", choices=VERSIONS + ["all"])
    ap.add_argument("--page", default="all", choices=PAGES + ["all"])
    a = ap.parse_args()
    versions = VERSIONS if a.version == "all" else [a.version]
    names = PAGES if a.page == "all" else [a.page]

    with sync_playwright() as p:
        browser = p.chromium.launch()
        failed = 0
        try:
            for v in versions:
                for n in names:
                    page = browser.new_page(viewport=VIEWPORT)  # a fresh page each time: no focus carried over
                    try:
                        result, out = scan_page(page, v, n)
                    except PlaywrightError as e:
                        (OUT / f"adj_keyboard_{v}_{n}.json").unlink(missing_ok=True)  # no stale file for merge
                        why = ((e.message or str(e)).splitlines() or ["unknown error"])[0]
                        print(f"{v:6} {n:8} FAILED, no file written: {why}", file=sys.stderr)
                        failed += 1
                        continue
                    finally:
                        page.close()
                    print(f"{v:6} {n:8} {summarize(result)}  -> {out.relative_to(REPO)}")
        finally:
            browser.close()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
