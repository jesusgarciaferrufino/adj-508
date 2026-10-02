"""Track B corpus tools.

    python rag/corpus_tools.py build      # write rag/wcag20_aa.json (all 38, text pulled from the W3C spec)
    python rag/corpus_tools.py validate   # check every row; non-zero exit on any error
    python rag/corpus_tools.py map508     # write rag/section508_map.json

sc / name / level / anchor below were checked against https://www.w3.org/TR/WCAG20/ on 2026-10-02.
'text' is pulled verbatim from the spec at build time (never typed or paraphrased).
"""
import json
import re
import sys
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

CORPUS = Path("rag/wcag20_aa.json")
MAP508 = Path("rag/section508_map.json")
SPEC = "https://www.w3.org/TR/WCAG20/"

# sc: (name, level, spec anchor)
CANONICAL = {
    "1.1.1": ("Non-text Content", "A", "text-equiv-all"),
    "1.2.1": ("Audio-only and Video-only (Prerecorded)", "A", "media-equiv-av-only-alt"),
    "1.2.2": ("Captions (Prerecorded)", "A", "media-equiv-captions"),
    "1.2.3": ("Audio Description or Media Alternative (Prerecorded)", "A", "media-equiv-audio-desc"),
    "1.2.4": ("Captions (Live)", "AA", "media-equiv-real-time-captions"),
    "1.2.5": ("Audio Description (Prerecorded)", "AA", "media-equiv-audio-desc-only"),
    "1.3.1": ("Info and Relationships", "A", "content-structure-separation-programmatic"),
    "1.3.2": ("Meaningful Sequence", "A", "content-structure-separation-sequence"),
    "1.3.3": ("Sensory Characteristics", "A", "content-structure-separation-understanding"),
    "1.4.1": ("Use of Color", "A", "visual-audio-contrast-without-color"),
    "1.4.2": ("Audio Control", "A", "visual-audio-contrast-dis-audio"),
    "1.4.3": ("Contrast (Minimum)", "AA", "visual-audio-contrast-contrast"),
    "1.4.4": ("Resize text", "AA", "visual-audio-contrast-scale"),
    "1.4.5": ("Images of Text", "AA", "visual-audio-contrast-text-presentation"),
    "2.1.1": ("Keyboard", "A", "keyboard-operation-keyboard-operable"),
    "2.1.2": ("No Keyboard Trap", "A", "keyboard-operation-trapping"),
    "2.2.1": ("Timing Adjustable", "A", "time-limits-required-behaviors"),
    "2.2.2": ("Pause, Stop, Hide", "A", "time-limits-pause"),
    "2.3.1": ("Three Flashes or Below Threshold", "A", "seizure-does-not-violate"),
    "2.4.1": ("Bypass Blocks", "A", "navigation-mechanisms-skip"),
    "2.4.2": ("Page Titled", "A", "navigation-mechanisms-title"),
    "2.4.3": ("Focus Order", "A", "navigation-mechanisms-focus-order"),
    "2.4.4": ("Link Purpose (In Context)", "A", "navigation-mechanisms-refs"),
    "2.4.5": ("Multiple Ways", "AA", "navigation-mechanisms-mult-loc"),
    "2.4.6": ("Headings and Labels", "AA", "navigation-mechanisms-descriptive"),
    "2.4.7": ("Focus Visible", "AA", "navigation-mechanisms-focus-visible"),
    "3.1.1": ("Language of Page", "A", "meaning-doc-lang-id"),
    "3.1.2": ("Language of Parts", "AA", "meaning-other-lang-id"),
    "3.2.1": ("On Focus", "A", "consistent-behavior-receive-focus"),
    "3.2.2": ("On Input", "A", "consistent-behavior-unpredictable-change"),
    "3.2.3": ("Consistent Navigation", "AA", "consistent-behavior-consistent-locations"),
    "3.2.4": ("Consistent Identification", "AA", "consistent-behavior-consistent-functionality"),
    "3.3.1": ("Error Identification", "A", "minimize-error-identified"),
    "3.3.2": ("Labels or Instructions", "A", "minimize-error-cues"),
    "3.3.3": ("Error Suggestion", "AA", "minimize-error-suggestions"),
    "3.3.4": ("Error Prevention (Legal, Financial, Data)", "AA", "minimize-error-reversible"),
    "4.1.1": ("Parsing", "A", "ensure-compat-parses"),
    "4.1.2": ("Name, Role, Value", "A", "ensure-compat-rsv"),
}

# sc: (intent, common_failures, needs_human_when, human_only)
# Intent is our own plain-language summary. Failure IDs are a starting list from the
# WCAG 2.0 Techniques; spot-verify the ones for criteria in the ground truth.
CONTENT = {
    "1.1.1": ("Images and other non-text content need a text alternative that does the same job; purely decorative content should be hidden from assistive tech.",
              ["F3", "F13", "F20", "F30", "F38", "F39", "F65", "F67", "F71", "F72"],
              "Deciding whether an image is decorative or meaningful, and whether existing alt text actually conveys its purpose", False),
    "1.2.1": ("Prerecorded audio-only needs a transcript; prerecorded video-only needs a transcript or an audio track.",
              ["F30", "F67"], "Judging whether the alternative is actually equivalent to the media", False),
    "1.2.2": ("Prerecorded video with sound needs captions.",
              ["F8", "F74", "F75"], "Checking captions are present, accurate and complete", False),
    "1.2.3": ("Prerecorded video needs audio description or a full text alternative so visual-only information is not lost.",
              [], "Judging whether visual information is covered by the soundtrack or description", False),
    "1.2.4": ("Live video with sound needs real-time captions.",
              [], "Confirming live captions exist during a live event", False),
    "1.2.5": ("Prerecorded video needs audio description of important visual details (a transcript alone is not enough at AA).",
              [], "Judging whether important visual details are described", False),
    "1.3.1": ("Structure shown visually (headings, lists, tables, labels, groupings) must also be in the markup so assistive tech can convey it.",
              ["F2", "F33", "F34", "F42", "F43", "F46", "F48", "F68", "F87", "F90", "F91"],
              "Deciding whether visually styled text is functioning as a heading, or whether a table is for data or layout", False),
    "1.3.2": ("When order matters for meaning, the DOM/reading order must match it.",
              ["F1", "F32", "F33", "F34", "F49"], "Judging whether the reading order changes the meaning", False),
    "1.3.3": ("Instructions must not rely only on shape, size, position or sound (e.g. 'click the round button on the right').",
              ["F14", "F26"], "Reading instructions in context to see if they depend only on sensory cues", False),
    "1.4.1": ("Color cannot be the only way information, actions or distinctions are conveyed.",
              ["F13", "F73", "F81"], "Checking whether a non-color cue (underline, icon, text) also conveys the meaning", False),
    "1.4.2": ("Audio that autoplays for more than 3 seconds needs a way to pause/stop it or control its volume.",
              ["F23", "F93"], "Confirming autoplay duration and whether a usable control exists", False),
    "1.4.3": ("Text needs at least 4.5:1 contrast with its background (3:1 for large text); logos and inactive or decorative text are exempt.",
              ["F24", "F83"], "Text over images or gradients, and whether text counts as large, decorative or a logo", False),
    "1.4.4": ("Text must be resizable to 200% without assistive tech and without losing content or functionality.",
              ["F69", "F80", "F94"], "Zooming to 200% and checking for clipped or overlapping content", False),
    "1.4.5": ("Use real text instead of images of text unless the image is customizable or the presentation is essential (logos are essential).",
              [], "Deciding whether an image of text is a logo or essential presentation", False),
    "2.1.1": ("Everything that works with a mouse must also work from the keyboard.",
              ["F42", "F54", "F55"], "Actually tabbing through and operating the control", False),
    "2.1.2": ("Keyboard focus must never get stuck; if leaving needs a special key, users are told.",
              ["F10"], "Tabbing into and out of embedded or scripted widgets", False),
    "2.2.1": ("Time limits must be adjustable, extendable or removable unless real-time, essential, or over 20 hours.",
              ["F40", "F41", "F58"], "Determining whether a time limit exists and whether an exception applies", False),
    "2.2.2": ("Moving, blinking, scrolling or auto-updating content that starts automatically needs a pause/stop/hide control.",
              ["F4", "F7", "F16", "F47", "F50"], "Watching the page to see what moves and for how long", False),
    "2.3.1": ("Nothing may flash more than three times per second unless below the flash thresholds.",
              [], "Measuring flash rate and area", False),
    "2.4.1": ("A way to skip repeated blocks (skip link, headings, landmarks) must exist.",
              [], "Checking whether a bypass mechanism exists and works across the page set", False),
    "2.4.2": ("Each page has a title that describes its topic or purpose.",
              ["F25"], "Judging whether a present title actually describes the page", False),
    "2.4.3": ("Tab order must preserve meaning and operability.",
              ["F44", "F85"], "Tabbing through the page and judging whether the order makes sense", True),
    "2.4.4": ("Link purpose must be clear from the link text, or the link text plus its programmatic context (same sentence, paragraph, list item or table cell).",
              ["F63", "F89"], "Whether surrounding text makes a vague link like 'more' or 'click here' clear", False),
    "2.4.5": ("There is more than one way to find a page within a site (nav, search, sitemap), except steps in a process.",
              [], "Reviewing the whole set of pages, not a single page", False),
    "2.4.6": ("Headings and labels that exist must describe their topic or purpose.",
              [], "Judging whether heading and label wording is descriptive", False),
    "2.4.7": ("Keyboard focus must be visible.",
              ["F55", "F78"], "Tabbing through and seeing whether focus is visible on each control", False),
    "3.1.1": ("The page's default language is set in code (e.g. lang on html).",
              [], "Rarely needed; confirm the lang value matches the content", False),
    "3.1.2": ("Passages in a different language are marked with their language.",
              [], "Spotting foreign-language passages and checking they are marked", False),
    "3.2.1": ("Focusing a control must not trigger a change of context (new window, page change, focus jump).",
              ["F52", "F55"], "Tabbing onto controls and watching for context changes", False),
    "3.2.2": ("Changing a control's value must not automatically change context unless users are warned first.",
              ["F36", "F37"], "Operating selects, radios and checkboxes to see what happens", False),
    "3.2.3": ("Repeated navigation appears in the same relative order on every page.",
              ["F66"], "Comparing navigation across multiple pages", False),
    "3.2.4": ("Components with the same function are identified the same way across pages.",
              ["F31"], "Comparing labels and alt text of same-function components across pages", True),
    "3.3.1": ("Automatically detected input errors are identified and described in text.",
              [], "Submitting the form with errors and reading the feedback", False),
    "3.3.2": ("Inputs have labels or instructions.",
              ["F82"], "Judging whether visible labels or instructions are adequate", False),
    "3.3.3": ("When an error is detected and a fix is known, suggest it (unless that would compromise security or purpose).",
              [], "Submitting invalid input and judging the suggestions", False),
    "3.3.4": ("Legal, financial or data-changing submissions are reversible, checked, or confirmable before finalizing.",
              [], "Determining whether the form causes a legal or financial commitment and walking the flow", False),
    "4.1.1": ("Markup is well-formed: complete tags, correct nesting, no duplicate attributes, unique IDs.",
              ["F70", "F77"], "Rarely; deciding whether a parsing error actually affects assistive tech", False),
    "4.1.2": ("UI components expose name, role, and state/value to assistive tech; changes are announced.",
              ["F15", "F20", "F42", "F59", "F68", "F79", "F86", "F89"],
              "Custom widgets where the accessible name may come from elsewhere (aria-labelledby, title, context)", False),
}

# Revised 508 non-web exceptions (E205.4 / E207.2). Verify against 36 CFR 1194 before the ACR.
NON_WEB_EXCEPT = {"2.4.1", "2.4.5", "3.2.3", "3.2.4"}


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.skip = [], 0
    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"): self.skip += 1
        if tag in ("p", "li", "div", "br", "dd", "dt", "h2", "h3", "h4"): self.parts.append("\n")
    def handle_endtag(self, tag):
        if tag in ("script", "style"): self.skip -= 1
    def handle_data(self, d):
        if not self.skip: self.parts.append(d)


def spec_text(html):
    p = _Text(); p.feed(html)
    return "".join(p.parts)


def extract(text, sc, name):
    pat = (re.escape(sc) + r"\s+" + re.escape(name) + r"\s*:(.*?)How to Meet\s+" + re.escape(sc) + r"\b")
    m = re.search(pat, text, re.S)
    if not m:
        return ""
    body = re.sub(r"\(Level A{1,3}\)", "", m.group(1))
    body = "\n".join(re.sub(r"\s+", " ", ln).strip() for ln in body.splitlines())
    return re.sub(r"\n{2,}", "\n", body).strip()


def build():
    if CORPUS.exists() and "--force" not in sys.argv:
        sys.exit(f"{CORPUS} exists; use --force to rebuild.")
    try:
        html = urllib.request.urlopen(SPEC, timeout=30).read().decode("utf-8")
        text = spec_text(html)
    except Exception as e:
        print(f"WARNING: could not fetch spec ({e}); 'text' left empty. Re-run with network.")
        text = ""
    rows, missing = [], []
    for sc, (name, level, anchor) in CANONICAL.items():
        intent, fails, human_when, human_only = CONTENT[sc]
        t = extract(text, sc, name) if text else ""
        if not t: missing.append(sc)
        rows.append({"sc": sc, "name": name, "level": level, "text": t, "intent": intent,
                     "common_failures": fails, "needs_human_when": human_when,
                     "human_only": human_only, "source": SPEC + "#" + anchor})
    CORPUS.parent.mkdir(parents=True, exist_ok=True)
    CORPUS.write_text(json.dumps(rows, indent=2, ensure_ascii=False))
    print(f"Wrote {len(rows)} criteria to {CORPUS}. Text missing for: {missing or 'none'}")


def validate():
    rows = json.loads(CORPUS.read_text())
    errors, seen = [], set()
    for i, r in enumerate(rows):
        sc = r.get("sc"); tag = f"row {i} ({sc})"
        if sc not in CANONICAL:
            errors.append(f"{tag}: not a WCAG 2.0 A/AA criterion"); continue
        if sc in seen: errors.append(f"{tag}: duplicate")
        seen.add(sc)
        name, level, anchor = CANONICAL[sc]
        if r.get("name") != name: errors.append(f"{tag}: name should be {name!r}")
        if r.get("level") != level: errors.append(f"{tag}: level should be {level!r}")
        if r.get("source") != SPEC + "#" + anchor: errors.append(f"{tag}: source should be {SPEC}#{anchor}")
        if not isinstance(r.get("human_only"), bool): errors.append(f"{tag}: human_only must be true/false")
        if any(not re.fullmatch(r"F\d+", f) for f in r.get("common_failures", [])):
            errors.append(f"{tag}: bad failure ID")
        for k in ("text", "intent", "needs_human_when"):
            if not str(r.get(k, "")).strip(): errors.append(f"{tag}: '{k}' is empty")
        if r.get("text", "").startswith(sc): errors.append(f"{tag}: text still includes the SC heading")
    missing = set(CANONICAL) - seen
    if missing: errors.append(f"missing criteria: {sorted(missing)}")
    print(f"{len(seen)}/38 criteria, {len(errors)} errors")
    for e in errors: print("ERROR", e)
    sys.exit(1 if errors else 0)


def map508():
    out = {sc: {"web": "E205.4",
                "non_web_documents": None if sc in NON_WEB_EXCEPT else "E205.4",
                "software": None if sc in NON_WEB_EXCEPT else "E207.2",
                "authoring_tools": "504.2", "verified": False} for sc in CANONICAL}
    MAP508.write_text(json.dumps(out, indent=2))
    print(f"Wrote {MAP508}. Have a person check it and flip 'verified' to true.")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    {"build": build, "validate": validate, "map508": map508}.get(cmd, lambda: sys.exit(__doc__))()
