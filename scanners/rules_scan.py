"""R. ADJ's own code rules: checks on the page's HTML and CSS that axe and Pa11y don't make.

docs/missed_issues.md section (a) lists the W3C failures no scanner reports; these rules
catch them. Each rule runs in the loaded page and returns hits in the shared hit format
(docs/TRACKS.md, "ADJ's own checks"). scanners/merge.py turns the hits into findings.

Writes data/scans/adj_rules_{version}_{page}.json:
  {"check": "rules", "version", "page", "url", "tool_version": "rules-v1",
   "hits": [{"rule_id", "sc", "status", "message", "dom_index", "selector", "html", "in_wrapper"}]}

One hit per element per criterion; a rule that fails several criteria (blur-on-focus) emits
one hit per criterion for the same element. Page-level hits use dom_index 0, selector "html"
and the <html> start tag as html.

status: "violation" when the rule is certain (router sends it to machine), "needs_review"
when a person or FOR/AGAINST should judge (router sends it to grey).

dom_index is the element's position in document.querySelectorAll("*") on a clean page,
the same as axe_scan.py ENRICH_JS, so a hit joins axe's or Pa11y's finding on the same
element and criterion. Nothing is injected into the page, so the indices stay clean.

| rule_id                        | criteria (status)                       |
| adj.rules.blur-on-focus        | 2.1.1, 2.4.7, 3.2.1, 4.1.2 (violation)  |
| adj.rules.no-skip-link         | 2.4.1, page-level (needs_review; violation |
|                                | when a menu of links has no skip link,  |
|                                | headings, landmarks or frames to bypass)|
| adj.rules.no-headings          | 2.4.6, page-level (needs_review)        |
| adj.rules.ascii-lookalike      | 1.1.1 (needs_review)                    |
| adj.rules.link-color-only      | 1.4.1 (needs_review)                    |
| adj.rules.select-navigates     | 3.2.2 (violation)                       |
| adj.rules.control-no-label     | 3.3.2 (violation)                       |
| adj.rules.layout-table-th      | 1.3.1 (needs_review)                    |
| adj.rules.css-bg-info          | 1.1.1 (needs_review)                    |
| adj.rules.layout-table-order   | 1.3.2 (needs_review)                    |
| adj.rules.css-reading-order    | 1.3.2 (needs_review)                    |
| adj.rules.image-of-text        | 1.4.5 (needs_review)                    |

Run from the repo root:
  python -m scanners.rules_scan                          # all 4 Before pages
  python -m scanners.rules_scan --version all --page home
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, sync_playwright

from capture.capture import PAGES, VERSIONS, VIEWPORT, page_url

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "data" / "scans"
TOOL_VERSION = "rules-v1"
WRAPPER = "#meta-header, #meta-footer"  # W3C demo chrome, not part of CityLights
HTML_CAP = 5000  # characters, the hit format's cap
GOTO_TIMEOUT = 60_000  # ms for one W3C page to load

# What a reviewer reads, keyed "{rule} {criterion}". {name} is filled in by the rule.
BLUR = "Element removes its own focus with blur() when it receives focus"
MESSAGES = {
    "blur-on-focus 2.1.1": f"{BLUR}, so a keyboard user can't stay on it to activate it (F55).",
    "blur-on-focus 2.4.7": f"{BLUR}, so no focus indicator is ever shown (F55, F78).",
    "blur-on-focus 3.2.1": f"{BLUR}: receiving focus changes the context (F55).",
    "blur-on-focus 4.1.2": f"{BLUR}, so its focus state can't be determined or kept (F79).",
    "no-skip-link 2.4.1": "No in-page link (skip link) comes before the page's navigation, so keyboard users "
                          "must tab through the repeated header and menu on every page (2.4.1).{also}",
    "no-headings 2.4.6": "The page has no h1-h6 headings, but {count} short block(s) are styled like headings "
                         "(large or bold), e.g. {examples}. Headings are not programmatically determined (2.4.6).",
    "ascii-lookalike 1.1.1": 'Text "{text}" uses ASCII characters as a picture (an arrow); a screen reader reads '
                             "them out one by one and there is no text alternative (F71).",
    "link-color-only 1.4.1": 'Link "{text}" in running text differs from the text around it only by colour: '
                             "no underline or other cue, also on hover and focus (F73).",
    "select-navigates 3.2.2": "Changing the selection in this select list opens another page straight away "
                              "(its onchange changes location), with no warning and no submit button (F37).",
    "control-no-label 3.3.2": "Form control ({what}) has no label: no <label>, aria-label, aria-labelledby "
                              "or title (3.3.2).",
    "layout-table-th 1.3.1": "Header cell <th> in a table that looks like a layout table (it {why}), so layout "
                             "is marked up as data structure (F46).",
    "layout-table-order 1.3.2": "This table holds {count} nested table(s), a sign of a layout table. Read in code "
                                "order (linearized), cell by cell, the content may not make sense (F49).",
    "css-reading-order 1.3.2": "CSS floats place this block ({text}) directly below another block in the same "
                               "column, but in the code it comes after content from another column ({between}), "
                               "so the reading order differs from what is shown (F1).",
    "image-of-text 1.4.5": "Image {file} ({width}x{height} px) is a short, wide graphic of the kind used for "
                           "text such as menu items, headings and phone numbers; if it shows text, that text "
                           "could be real text (1.4.5).",
    "css-bg-info 1.1.1": "Element shows an image only as a CSS background and has no text and no text "
                         "alternative, so the image's information is lost without CSS or to a screen reader (F3).",
}

# Shared helpers, put in front of every rule's code. Each rule runs as its own
# page.evaluate call and returns a list of hits.
HELPERS = r"""
const ALL = Array.from(document.querySelectorAll("*"));
const WRAPPER = __WRAPPER__;
const CAP = __CAP__;
const MESSAGES = __MESSAGES__;
// The demo page sits in #page; test pages without it use the whole body.
const BODY = document.body || document.documentElement;
const SCOPE = document.querySelector("#page") || BODY;

function inWrapper(el) { return el.closest(WRAPPER) !== null; }

function visible(el) {
  if (el.getClientRects().length === 0) return false;
  return getComputedStyle(el).visibility !== "hidden";
}

// A selector that matches exactly this element: the nearest unique id, then child steps.
function selectorFor(el) {
  if (el === document.documentElement) return "html";
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

function message(rule, sc, fill) {
  const template = MESSAGES[`${rule} ${sc}`];
  if (template === undefined) throw new Error(`no message for ${rule} ${sc}`);
  return template.replace(/\{(\w+)\}/g, (_, k) => {
    if (!(k in fill)) throw new Error(`message ${rule} ${sc} needs {${k}}`);
    return fill[k];
  });
}

function hit(el, rule, sc, status, fill = {}) {
  const html = el.outerHTML;
  return {rule_id: "adj.rules." + rule, sc, status, message: message(rule, sc, fill),
          dom_index: ALL.indexOf(el), selector: selectorFor(el),
          html: html.length > CAP ? html.slice(0, CAP) : html, in_wrapper: inWrapper(el)};
}

function pageHit(rule, sc, status, fill = {}) {
  const html = document.documentElement.outerHTML;
  return {rule_id: "adj.rules." + rule, sc, status, message: message(rule, sc, fill),
          dom_index: 0, selector: "html", html: html.slice(0, html.indexOf(">") + 1), in_wrapper: false};
}

function text(el) { return (el.textContent || "").replace(/\s+/g, " ").trim(); }

function quote(s, n = 60) { return s.length > n ? s.slice(0, n - 3) + "..." : s; }

// The block an inline element's text belongs to.
function blockOf(el) {
  for (let e = el.parentElement; e; e = e.parentElement) {
    const d = getComputedStyle(e).display;
    if (!d.startsWith("inline") && d !== "contents") return e;
  }
  return BODY;
}
""".replace("__WRAPPER__", json.dumps(WRAPPER)).replace("__CAP__", str(HTML_CAP)).replace(
    "__MESSAGES__", json.dumps(MESSAGES))

# 1. onfocus="blur()": the element throws focus away as soon as it gets it.
BLUR_ON_FOCUS = r"""
const hits = [];
for (const el of ALL) {
  if (!/\bblur\s*\(\s*\)/.test(el.getAttribute("onfocus") || "")) continue;
  for (const sc of ["2.1.1", "2.4.7", "3.2.1", "4.1.2"]) hits.push(hit(el, "blur-on-focus", sc, "violation"));
}
return hits;
"""

# 2. No in-page link (a working skip link) before the first navigation block.
NO_SKIP_LINK = r"""
function targetOf(a) {
  let id = a.getAttribute("href").slice(1);
  try { id = decodeURIComponent(id); } catch (e) { /* keep it as written */ }
  if (!id) return null;
  return document.getElementById(id) || document.querySelector(`a[name="${CSS.escape(id)}"]`);
}
const links = Array.from(SCOPE.querySelectorAll("a[href]"));
const isSkip = a => a.getAttribute("href").startsWith("#") && targetOf(a) && targetOf(a) !== a;
// The navigation starts at the page's first ordinary link; a skip link must come before it.
// -1: no ordinary links, so no repeated navigation to bypass. Above 0: a skip link comes first.
if (links.findIndex(a => !isSkip(a)) !== 0) return [];
// Certain only when there is a block of links to bypass and none of the usual bypass techniques:
// no headings (H69), no landmarks (ARIA11), no frames, anywhere on the page outside W3C's wrapper.
// A navigation block: an element holding 3 or more ordinary links and little other text.
const ordinary = links.filter(a => !isSkip(a));
const otherText = el => {
  let t = "";
  const walk = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  for (let n = walk.nextNode(); n; n = walk.nextNode()) if (!n.parentElement.closest("a")) t += n.nodeValue;
  return t.replace(/\s/g, "").length;
};
const candidates = new Set();
for (const a of ordinary)
  for (let e = a.parentElement; e && e !== BODY.parentElement; e = e.parentElement) candidates.add(e);
const navBlock = Array.from(candidates)
  .some(el => ordinary.filter(a => el.contains(a)).length >= 3 && otherText(el) <= 100);
const LANDMARKS = "main, nav, header, footer, aside, [role=main], [role=navigation], [role=banner], "
  + "[role=contentinfo], [role=complementary], [role=search], [role=region]";
const outside = sel => Array.from(BODY.querySelectorAll(sel)).some(el => !inWrapper(el));
const certain = navBlock && !outside("h1, h2, h3, h4, h5, h6, [role=heading]") && !outside(LANDMARKS)
  && !outside("frame, iframe");
const also = certain ? " The page also has no headings and no landmarks, so none of the usual bypass techniques"
  + " (G1, H69, ARIA11) is present." : "";
return [pageHit("no-skip-link", "2.4.1", certain ? "violation" : "needs_review", {also})];
"""

# 3. Text styled like headings, but no h1-h6 at all.
NO_HEADINGS = r"""
if (SCOPE.querySelector("h1, h2, h3, h4, h5, h6, [role=heading]")) return [];
const base = parseFloat(getComputedStyle(SCOPE).fontSize) || 16;
const looksLikeHeading = el => {
  const s = getComputedStyle(el);
  return parseFloat(s.fontSize) >= base * 1.2 || parseInt(s.fontWeight, 10) >= 600;
};
const examples = [];
for (const block of SCOPE.querySelectorAll("*")) {
  if (inWrapper(block) || !visible(block)) continue;
  const d = getComputedStyle(block).display;
  if (d.startsWith("inline") || d === "contents" || d === "none") continue;
  if (block.closest("a, button, label, select, option, textarea, script, style")) continue;
  if (block.querySelector("a, input, select, textarea, button, img")) continue;
  // Innermost blocks only, so one heading isn't counted again as part of its container.
  if (Array.from(block.querySelectorAll("*")).some(c => !getComputedStyle(c).display.startsWith("inline"))) continue;
  const t = text(block);
  // No digits: Before home shows today's date, and a message must not change from day to day.
  if (t.length < 3 || t.length > 80 || t.split(" ").length > 12 || /\d/.test(t)) continue;
  // Every piece of text in the block must be in large or bold type.
  const parts = [];
  const walk = document.createTreeWalker(block, NodeFilter.SHOW_TEXT);
  for (let n = walk.nextNode(); n; n = walk.nextNode()) if (n.nodeValue.trim()) parts.push(n.parentElement);
  if (parts.length && parts.every(looksLikeHeading) && !examples.includes(t)) examples.push(t);
}
if (!examples.length) return [];
const shown = examples.slice(0, 3).map(t => `"${quote(t, 40)}"`).join(", ");
return [pageHit("no-headings", "2.4.6", "needs_review", {count: examples.length, examples: shown})];
"""

# 4. ASCII art standing in for an arrow or picture, e.g. "QUICKMENU ---->".
ASCII_LOOKALIKE = r"""
const ART = /-{2,}>|<-{2,}|={2,}>|<={2,}/;
const hits = [], seen = new Set();
const walk = document.createTreeWalker(BODY, NodeFilter.SHOW_TEXT);
for (let n = walk.nextNode(); n; n = walk.nextNode()) {
  const el = n.parentElement;
  if (!ART.test(n.nodeValue) || seen.has(el) || el.closest("script, style, noscript, textarea")) continue;
  seen.add(el);
  hits.push(hit(el, "ascii-lookalike", "1.1.1", "needs_review", {text: quote(text(el))}));
}
return hits;
"""

# 5a. Links inside running text with no non-colour cue in the normal state. Returns
# candidates; the Python side then forces :hover and :focus and asks LINK_CUE again.
LINK_CANDIDATES = r"""
const out = [];
for (const a of BODY.querySelectorAll("a[href]")) {
  if (inWrapper(a) || !visible(a) || !/[a-z]/i.test(text(a))) continue;
  if (a.querySelector("img, svg, input")) continue;  // an icon is a non-colour cue
  let outside = "";
  const walk = document.createTreeWalker(blockOf(a), NodeFilter.SHOW_TEXT);
  for (let n = walk.nextNode(); n; n = walk.nextNode()) if (!n.parentElement.closest("a")) outside += n.nodeValue;
  if (outside.replace(/\s/g, "").length < 20) continue;  // not running text
  out.push(ALL.indexOf(a));
}
return out;
"""

# 5b. True if the link at this dom_index looks different from its surrounding text by
# something other than colour, in whatever state is forced on it right now.
LINK_CUE = r"""
const a = ALL[__INDEX__];
const s = getComputedStyle(a), p = getComputedStyle(a.parentElement);
const decorated = [a, ...a.querySelectorAll("*")].some(e => getComputedStyle(e).textDecorationLine !== "none");
const border = ["Top", "Right", "Bottom", "Left"].some(side =>
  parseFloat(s[`border${side}Width`]) > 0 && s[`border${side}Style`] !== "none");
const pseudo = ["::before", "::after"].some(w => !["none", "normal", '""'].includes(getComputedStyle(a, w).content));
return decorated || border || pseudo || s.boxShadow !== "none" || s.backgroundImage !== "none"
  || Math.abs(parseInt(s.fontWeight, 10) - parseInt(p.fontWeight, 10)) >= 300
  || s.fontStyle !== p.fontStyle || s.fontFamily !== p.fontFamily || s.textTransform !== p.textTransform
  || Math.abs(parseFloat(s.fontSize) - parseFloat(p.fontSize)) > parseFloat(p.fontSize) * 0.15;
"""

LINK_HIT = r"""
const a = ALL[__INDEX__];
return [hit(a, "link-color-only", "1.4.1", "needs_review", {text: quote(text(a))})];
"""

# 6. A select list that goes to another page as soon as the selection changes.
SELECT_NAVIGATES = r"""
// Assigning location (or location.href), location.assign/replace(), or window.open().
const NAVIGATES = new RegExp([
  String.raw`(^|[^.\w])((window|document|top|self|parent)\.)?location(\.href)?\s*=(?!=)`,
  String.raw`\blocation\.(assign|replace)\s*\(`,
  String.raw`\bwindow\.open\s*\(`,
].join("|"));
const hits = [];
for (const sel of document.querySelectorAll("select[onchange]"))
  if (NAVIGATES.test(sel.getAttribute("onchange")))
    hits.push(hit(sel, "select-navigates", "3.2.2", "violation"));
return hits;
"""

# 7. A form control with no programmatic label at all.
CONTROL_NO_LABEL = r"""
const NAMED_BY_VALUE = ["hidden", "submit", "reset", "button", "image"];
const hasText = s => !!(s && s.trim());
const labelledBy = el => (el.getAttribute("aria-labelledby") || "").split(/\s+/)
  .some(id => id && hasText(document.getElementById(id)?.textContent));
// A label's text, counting the alt of images inside it (<label><img alt="Search"></label>).
const labelText = l => l.textContent + Array.from(l.querySelectorAll("img[alt]")).map(i => i.alt).join(" ");
const labelled = el => Array.from(el.labels || []).some(l => hasText(labelText(l)))
  || hasText(el.getAttribute("aria-label")) || labelledBy(el) || hasText(el.getAttribute("title"));
const hits = [];
for (const el of BODY.querySelectorAll("input, select, textarea")) {
  const type = (el.getAttribute("type") || "text").toLowerCase();
  if (el.localName === "input" && NAMED_BY_VALUE.includes(type)) continue;
  if (!visible(el) || labelled(el)) continue;
  const what = el.localName === "input" ? `${type} field` : el.localName;
  hits.push(hit(el, "control-no-label", "3.3.2", "violation", {what}));
}
return hits;
"""

# 8. th header cells inside a table that is used for layout.
LAYOUT_TABLE_TH = r"""
const hits = [];
for (const table of BODY.querySelectorAll("table")) {
  const ths = Array.from(table.querySelectorAll("th")).filter(th => th.closest("table") === table);
  if (!ths.length) continue;
  const rows = Array.from(table.rows);
  const why = ["presentation", "none"].includes(table.getAttribute("role")) ? "has role=presentation"
    : table.querySelector("table") ? "contains other tables"
    : rows.length < 2 ? "has only one row"
    : Math.max(...rows.map(r => r.cells.length)) < 2 ? "has only one column" : null;
  if (why) for (const th of ths) hits.push(hit(th, "layout-table-th", "1.3.1", "needs_review", {why}));
}
return hits;
"""

# 9. An image shown as a CSS background on an element with no text and no text alternative.
CSS_BG_INFO = r"""
const hits = [];
for (const el of BODY.querySelectorAll("*")) {
  if (inWrapper(el) || !visible(el) || !getComputedStyle(el).backgroundImage.includes("url(")) continue;
  const box = el.getBoundingClientRect();
  if (box.width < 24 || box.height < 24) continue;  // tiny: bullets, corners, spacers
  if (text(el) || el.querySelector("img, svg, input, select, textarea, button, object, canvas")) continue;
  if (el.getAttribute("aria-label")?.trim() || el.getAttribute("aria-labelledby")) continue;
  hits.push(hit(el, "css-bg-info", "1.1.1", "needs_review"));
}
return hits;
"""


# 10. A layout table built from nested tables: its code order is the reading order.
LAYOUT_TABLE_ORDER = r"""
const hits = [];
for (const table of BODY.querySelectorAll("table")) {
  if (table.parentElement.closest("table") || inWrapper(table)) continue;  // outermost tables only
  const nested = table.querySelectorAll("table").length;
  if (nested) hits.push(hit(table, "layout-table-order", "1.3.2", "needs_review", {count: nested}));
}
return hits;
"""

# 11. Floats that stack blocks into columns while the code runs across the columns.
# Block B sits directly below block A, at the same left edge, but between A and B in the code
# there is content from another column, so a screen reader leaves the column and comes back.
CSS_READING_ORDER = r"""
const GAP = 4;  // px: blocks touching edge to edge, as floats with fixed heights and no margins do
const boxes = [];
for (const el of SCOPE.querySelectorAll("*")) {
  if (getComputedStyle(el).float === "none" || inWrapper(el) || !visible(el)) continue;
  const b = el.getBoundingClientRect();
  if (b.width >= 50 && b.height >= 10) boxes.push({el, b});
}
const related = (x, y) => x.contains(y) || y.contains(x);
// Blocks only: a float holding two or more floats is a row container, not a block in a column.
// (A story with one floated photo in it is still a block.)
const blocks = boxes.filter(X => boxes.filter(Y => Y !== X && X.el.contains(Y.el)).length < 2);
const describe = el => text(el) ? `"${quote(text(el), 40)}"`  // an image block has no text: name its tag
  : `<${el.localName}${el.getAttribute("class") ? ` class="${quote(el.getAttribute("class"), 30)}"` : ""}>`;
// Cards of one size and class in a grid are meant to be read row by row; only mixed blocks
// (a headline above an image above a story) make a column that the code doesn't follow.
const sameKind = (x, y) => x.el.getAttribute("class") === y.el.getAttribute("class")
  && x.el.localName === y.el.localName
  && Math.abs(x.b.width - y.b.width) <= GAP && Math.abs(x.b.height - y.b.height) <= GAP;
const hits = [];
for (const [i, B] of blocks.entries()) {
  const A = blocks.slice(0, i).reverse().find(A => !related(A.el, B.el)
    && Math.abs(A.b.left - B.b.left) <= GAP && Math.abs(A.b.bottom - B.b.top) <= GAP);
  if (!A || sameKind(A, B)) continue;
  const between = blocks.slice(blocks.indexOf(A) + 1, i).find(C => !related(C.el, A.el) && !related(C.el, B.el)
    && Math.abs(C.b.left - A.b.left) > GAP);
  if (between) hits.push(hit(B.el, "css-reading-order", "1.3.2", "needs_review",
    {text: describe(B.el), between: describe(between.el)}));
}
return hits;
"""

# 12. Short, wide images: the shape of menu items, headings and phone numbers drawn as pictures.
# Photographs (.jpg) and logos (exempt from 1.4.5) are left out; tiny spacers and rules too.
IMAGE_OF_TEXT = r"""
const hits = [];
for (const img of BODY.querySelectorAll("img, input[type=image]")) {
  if (inWrapper(img) || !visible(img)) continue;
  const raw = (img.getAttribute("src") || "").trim();
  const src = raw.split(/[?#]/)[0];
  if (/^data:image\/jpe?g/i.test(raw) || /\.jpe?g$/i.test(src)) continue;  // photographs
  // Logos are exempt from 1.4.5 ("toplogo", "logos", "logotype"; not "logout" or "logon").
  if (/logo(?![un])/i.test(src.split("/").pop() + " " + (img.getAttribute("alt") || ""))) continue;
  const file = raw.startsWith("data:") ? "(inline image)" : src.split("/").pop() || "(no src)";
  const {width, height} = img.getBoundingClientRect();
  if (height < 15 || height > 40 || width < 60 || width < 3 * height) continue;
  hits.push(hit(img, "image-of-text", "1.4.5", "needs_review",
    {file, width: Math.round(width), height: Math.round(height)}));
}
return hits;
"""


def _run(page: Page, body: str, index: int | None = None) -> Any:
    code = body.replace("__INDEX__", str(index)) if index is not None else body
    return page.evaluate(f"() => {{ {HELPERS}\n{code} }}")


def _dom_node_ids(cdp) -> list[int]:
    """CDP node ids in document.querySelectorAll("*") order, so ids[i] is dom_index i."""
    root = cdp.send("DOM.getDocument", {"depth": 0})["root"]["nodeId"]
    return cdp.send("DOM.querySelectorAll", {"nodeId": root, "selector": "*"})["nodeIds"]


def link_color_only(page: Page) -> list[dict]:
    """Rule 5: links with no non-colour cue in the normal, hover and focus states.

    Hover and focus are forced with Chrome's DevTools protocol instead of moving the mouse
    or focusing, because on the Before pages focusing a link runs blur() straight away.
    """
    candidates = _run(page, LINK_CANDIDATES)
    if not candidates:
        return []
    cdp = page.context.new_cdp_session(page)
    try:
        cdp.send("DOM.enable")
        cdp.send("CSS.enable")
        node_ids = _dom_node_ids(cdp)
        if len(node_ids) != page.evaluate("document.querySelectorAll('*').length"):
            # The page changed between the two listings, so an index could name the wrong element.
            print("  link-color-only skipped: the page's elements changed while it was being checked",
                  file=sys.stderr)
            return []
        hits = []
        for i in candidates:
            no_cue = True
            for state in ([], ["hover"], ["focus", "focus-visible"]):
                cdp.send("CSS.forcePseudoState", {"nodeId": node_ids[i], "forcedPseudoClasses": state})
                if _run(page, LINK_CUE, i):
                    no_cue = False
                    break
            cdp.send("CSS.forcePseudoState", {"nodeId": node_ids[i], "forcedPseudoClasses": []})
            if no_cue:
                hits += _run(page, LINK_HIT, i)
        return hits
    finally:
        cdp.detach()


def run_rules(page: Page) -> list[dict]:
    """Every rule's hits on the page that is loaded now, in rule order."""
    hits = []
    for body in (BLUR_ON_FOCUS, NO_SKIP_LINK, NO_HEADINGS, ASCII_LOOKALIKE):
        hits += _run(page, body)
    hits += link_color_only(page)
    for body in (SELECT_NAVIGATES, CONTROL_NO_LABEL, LAYOUT_TABLE_TH, CSS_BG_INFO,
                 LAYOUT_TABLE_ORDER, CSS_READING_ORDER, IMAGE_OF_TEXT):
        hits += _run(page, body)
    return hits


def build_result(page: Page, version: str, name: str, url: str) -> dict:
    """The file's contents for the page that is loaded now (the tests load their own HTML)."""
    return {"check": "rules", "version": version, "page": name, "url": url,
            "tool_version": TOOL_VERSION, "hits": run_rules(page)}


def write_result(result: dict, out_dir: Path = OUT) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"adj_rules_{result['version']}_{result['page']}.json"
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return out


def scan_page(page: Page, version: str, name: str) -> tuple[dict, Path]:
    url = page_url(version, name)
    page.goto(url, wait_until="networkidle", timeout=GOTO_TIMEOUT)
    result = build_result(page, version, name, url)
    return result, write_result(result)


def summarize(result: dict) -> str:
    counts: dict[str, int] = {}
    for h in result["hits"]:
        if not h["in_wrapper"]:
            short = h["rule_id"].removeprefix("adj.rules.")
            counts[short] = counts.get(short, 0) + 1
    wrapper = sum(h["in_wrapper"] for h in result["hits"])
    per_rule = ", ".join(f"{r} {n}" for r, n in counts.items()) or "none"
    return f"{len(result['hits']) - wrapper:3} hits ({per_rule}; wrapper {wrapper})"


def main() -> int:
    ap = argparse.ArgumentParser(description="Run ADJ's code rules on W3C BAD pages.")
    ap.add_argument("--version", default="before", choices=VERSIONS + ["all"])
    ap.add_argument("--page", default="all", choices=PAGES + ["all"])
    a = ap.parse_args()
    versions = VERSIONS if a.version == "all" else [a.version]
    names = PAGES if a.page == "all" else [a.page]

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport=VIEWPORT)
            failed = 0
            for v in versions:
                for n in names:
                    try:
                        result, out = scan_page(page, v, n)
                    except PlaywrightError as e:
                        # Remove the old file, so merge can't mistake it for this run's result.
                        (OUT / f"adj_rules_{v}_{n}.json").unlink(missing_ok=True)
                        why = ((e.message or str(e)).splitlines() or ["unknown error"])[0]
                        print(f"{v:6} {n:8} FAILED, no file written: {why}", file=sys.stderr)
                        failed += 1
                        continue
                    print(f"{v:6} {n:8} {summarize(result)}  -> {out.relative_to(REPO)}")
        finally:
            browser.close()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
