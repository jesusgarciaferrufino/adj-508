"""Track B corpus tools: the WCAG 2.0 A/AA corpus every later step cites from.

    python -m rag.corpus_tools build [--force]   # write rag/wcag20_aa.json; 'text' pulled from the W3C spec
    python -m rag.corpus_tools validate          # offline check of every row; non-zero exit on any error
    python -m rag.corpus_tools verify            # re-check the corpus against the live spec; writes rag/verification.md
    python -m rag.corpus_tools map508            # write rag/section508_map.json

sc / name / level / anchor below were checked against https://www.w3.org/TR/WCAG20/ on 2026-10-02.
'text' is pulled verbatim from the spec at build time (never typed or paraphrased); build refuses
to write a corpus with any criterion's text missing.

Run it as a module from the repo root (python -m rag.corpus_tools), like every ADJ script.
"""

import argparse
import hashlib
import http.client
import json
import os
import re
import sys
import tempfile
import time
import urllib.request
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from scanners.wcag import WCAG20_A, WCAG20_A_AA, WCAG20_AA  # noqa: E402

RAG = REPO / "rag"
CORPUS = RAG / "wcag20_aa.json"
MAP508 = RAG / "section508_map.json"
VERIFICATION = RAG / "verification.md"
SPEC = "https://www.w3.org/TR/WCAG20/"
SPEC_TIMEOUT = 30
SPEC_ATTEMPTS = 3
# What a failed or garbled spec download can raise: network errors, a cut-off response, a non-UTF-8 body.
SPEC_ERRORS = (OSError, http.client.HTTPException, UnicodeDecodeError)

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
    "1.1.1": (
        "Images and other non-text content need a text alternative that does the same job; purely decorative content "
        "should be hidden from assistive tech.",
        ["F3", "F13", "F20", "F30", "F38", "F39", "F65", "F67", "F71", "F72"],
        "Deciding whether an image is decorative or meaningful, and whether existing alt text actually conveys its "
        "purpose",
        False,
    ),
    "1.2.1": (
        "Prerecorded audio-only needs a transcript; prerecorded video-only needs a transcript or an audio track.",
        ["F30", "F67"],
        "Judging whether the alternative is actually equivalent to the media",
        False,
    ),
    "1.2.2": (
        "Prerecorded video with sound needs captions.",
        ["F8", "F74", "F75"],
        "Checking captions are present, accurate and complete",
        False,
    ),
    "1.2.3": (
        "Prerecorded video needs audio description or a full text alternative so visual-only information is not "
        "lost.",
        [],
        "Judging whether visual information is covered by the soundtrack or description",
        False,
    ),
    "1.2.4": (
        "Live video with sound needs real-time captions.",
        [],
        "Confirming live captions exist during a live event",
        False,
    ),
    "1.2.5": (
        "Prerecorded video needs audio description of important visual details (a transcript alone is not enough at "
        "AA).",
        [],
        "Judging whether important visual details are described",
        False,
    ),
    "1.3.1": (
        "Structure shown visually (headings, lists, tables, labels, groupings) must also be in the markup so "
        "assistive tech can convey it.",
        ["F2", "F33", "F34", "F42", "F43", "F46", "F48", "F68", "F87", "F90", "F91"],
        "Deciding whether visually styled text is functioning as a heading, or whether a table is for data or layout",
        False,
    ),
    "1.3.2": (
        "When order matters for meaning, the DOM/reading order must match it.",
        ["F1", "F32", "F33", "F34", "F49"],
        "Judging whether the reading order changes the meaning",
        False,
    ),
    "1.3.3": (
        "Instructions must not rely only on shape, size, position or sound (e.g. 'click the round button on the "
        "right').",
        ["F14", "F26"],
        "Reading instructions in context to see if they depend only on sensory cues",
        False,
    ),
    "1.4.1": (
        "Color cannot be the only way information, actions or distinctions are conveyed.",
        ["F13", "F73", "F81"],
        "Checking whether a non-color cue (underline, icon, text) also conveys the meaning",
        False,
    ),
    "1.4.2": (
        "Audio that autoplays for more than 3 seconds needs a way to pause/stop it or control its volume.",
        ["F23", "F93"],
        "Confirming autoplay duration and whether a usable control exists",
        False,
    ),
    "1.4.3": (
        "Text needs at least 4.5:1 contrast with its background (3:1 for large text); logos and inactive or "
        "decorative text are exempt.",
        ["F24", "F83"],
        "Text over images or gradients, and whether text counts as large, decorative or a logo",
        False,
    ),
    "1.4.4": (
        "Text must be resizable to 200% without assistive tech and without losing content or functionality.",
        ["F69", "F80", "F94"],
        "Zooming to 200% and checking for clipped or overlapping content",
        False,
    ),
    "1.4.5": (
        "Use real text instead of images of text unless the image is customizable or the presentation is essential "
        "(logos are essential).",
        [],
        "Deciding whether an image of text is a logo or essential presentation",
        False,
    ),
    "2.1.1": (
        "Everything that works with a mouse must also work from the keyboard.",
        ["F42", "F54", "F55"],
        "Actually tabbing through and operating the control",
        False,
    ),
    "2.1.2": (
        "Keyboard focus must never get stuck; if leaving needs a special key, users are told.",
        ["F10"],
        "Tabbing into and out of embedded or scripted widgets",
        False,
    ),
    "2.2.1": (
        "Time limits must be adjustable, extendable or removable unless real-time, essential, or over 20 hours.",
        ["F40", "F41", "F58"],
        "Determining whether a time limit exists and whether an exception applies",
        False,
    ),
    "2.2.2": (
        "Moving, blinking, scrolling or auto-updating content that starts automatically needs a pause/stop/hide "
        "control.",
        ["F4", "F7", "F16", "F47", "F50"],
        "Watching the page to see what moves and for how long",
        False,
    ),
    "2.3.1": (
        "Nothing may flash more than three times per second unless below the flash thresholds.",
        [],
        "Measuring flash rate and area",
        False,
    ),
    "2.4.1": (
        "A way to skip repeated blocks (skip link, headings, landmarks) must exist.",
        [],
        "Checking whether a bypass mechanism exists and works across the page set",
        False,
    ),
    "2.4.2": (
        "Each page has a title that describes its topic or purpose.",
        ["F25"],
        "Judging whether a present title actually describes the page",
        False,
    ),
    "2.4.3": (
        "Tab order must preserve meaning and operability.",
        ["F44", "F85"],
        "Tabbing through the page and judging whether the order makes sense",
        True,
    ),
    "2.4.4": (
        "Link purpose must be clear from the link text, or the link text plus its programmatic context (same "
        "sentence, paragraph, list item or table cell).",
        ["F63", "F89"],
        "Whether surrounding text makes a vague link like 'more' or 'click here' clear",
        False,
    ),
    "2.4.5": (
        "There is more than one way to find a page within a site (nav, search, sitemap), except steps in a process.",
        [],
        "Reviewing the whole set of pages, not a single page",
        False,
    ),
    "2.4.6": (
        "Headings and labels that exist must describe their topic or purpose.",
        [],
        "Judging whether heading and label wording is descriptive",
        False,
    ),
    "2.4.7": (
        "Keyboard focus must be visible.",
        ["F55", "F78"],
        "Tabbing through and seeing whether focus is visible on each control",
        False,
    ),
    "3.1.1": (
        "The page's default language is set in code (e.g. lang on html).",
        [],
        "Rarely needed; confirm the lang value matches the content",
        False,
    ),
    "3.1.2": (
        "Passages in a different language are marked with their language.",
        [],
        "Spotting foreign-language passages and checking they are marked",
        False,
    ),
    "3.2.1": (
        "Focusing a control must not trigger a change of context (new window, page change, focus jump).",
        ["F52", "F55"],
        "Tabbing onto controls and watching for context changes",
        False,
    ),
    "3.2.2": (
        "Changing a control's value must not automatically change context unless users are warned first.",
        ["F36", "F37"],
        "Operating selects, radios and checkboxes to see what happens",
        False,
    ),
    "3.2.3": (
        "Repeated navigation appears in the same relative order on every page.",
        ["F66"],
        "Comparing navigation across multiple pages",
        False,
    ),
    "3.2.4": (
        "Components with the same function are identified the same way across pages.",
        ["F31"],
        "Comparing labels and alt text of same-function components across pages",
        True,
    ),
    "3.3.1": (
        "Automatically detected input errors are identified and described in text.",
        [],
        "Submitting the form with errors and reading the feedback",
        False,
    ),
    "3.3.2": (
        "Inputs have labels or instructions.",
        ["F82"],
        "Judging whether visible labels or instructions are adequate",
        False,
    ),
    "3.3.3": (
        "When an error is detected and a fix is known, suggest it (unless that would compromise security or "
        "purpose).",
        [],
        "Submitting invalid input and judging the suggestions",
        False,
    ),
    "3.3.4": (
        "Legal, financial or data-changing submissions are reversible, checked, or confirmable before finalizing.",
        [],
        "Determining whether the form causes a legal or financial commitment and walking the flow",
        False,
    ),
    "4.1.1": (
        "Markup is well-formed: complete tags, correct nesting, no duplicate attributes, unique IDs.",
        ["F70", "F77"],
        "Rarely; deciding whether a parsing error actually affects assistive tech",
        False,
    ),
    "4.1.2": (
        "UI components expose name, role, and state/value to assistive tech; changes are announced.",
        ["F15", "F20", "F42", "F59", "F68", "F79", "F86", "F89"],
        "Custom widgets where the accessible name may come from elsewhere (aria-labelledby, title, context)",
        False,
    ),
}

# Revised 508 non-web exceptions (E205.4 / E207.2), checked against 36 CFR 1194 on 2026-10-25.
NON_WEB_EXCEPT = {"2.4.1", "2.4.5", "3.2.3", "3.2.4"}

# The person's check of rag/section508_map.json. verify rewrites rag/verification.md, so the
# record lives here and is written every time.
SECTION508_VERIFICATION = [
    "## Revised Section 508 mapping (`rag/section508_map.json`)",
    "",
    "Checked on 2026-10-25, row by row, against the Revised 508 Standards, 36 CFR 1194 Appendices A and C "
    "(https://www.access-board.gov/ict/). All 38 rows hold, so all 38 are `verified: true`. "
    "`verify` re-checks the map against the rules below each time it writes this file.",
    "",
    "| Column | Section | What it says | Map |",
    "| --- | --- | --- | --- |",
    "| `web` | E205.4 Accessibility Standard | Electronic content conforms to WCAG 2.0 Level A and AA "
    "(incorporated by reference, 702.10.1). No exception for Web content. | E205.4 for all 38 |",
    "| `non_web_documents` | E205.4, Exception | Non-Web documents need not conform to 2.4.1, 2.4.5, 3.2.3 "
    "and 3.2.4. | E205.4, null for those 4 |",
    "| `software` | E207.2 WCAG Conformance | Software UI and content conform to WCAG 2.0 A and AA; Exception 2: "
    "non-Web software need not conform to the same 4. (Exception 1, assistive technology, and 3, "
    "Complete Processes, are not per criterion.) | E207.2, null for those 4 |",
    "| `authoring_tools` | 504.2 Content Creation or Editing | Authoring tools provide a mode that creates "
    "WCAG 2.0 A and AA content. Its only exception (editing plain-text source) is not per criterion. "
    "| 504.2 for all 38 |",
]

FAILURE_ID = re.compile(r"F\d+")
LEVEL_MARK = re.compile(r"\(Level (A{1,3})\)")
HEADING = re.compile(r"(\d\.\d\.\d+) (.+?):\s*(.*)", re.S)
BLOCK_TAGS = {"p", "li", "div", "br", "dd", "dt", "ul", "ol", "dl", "h2", "h3", "h4"}


class CorpusError(ValueError):
    """The corpus file is missing or not shaped like the rows build writes."""


def rel(path: Path) -> str:
    """Repo-relative path for messages; the full path when the file is outside the repo."""
    try:
        return str(path.resolve().relative_to(REPO))
    except ValueError:
        return str(path)


def write_atomic(path: Path, text: str) -> None:
    """Write via a temporary file and rename, so a crash never leaves a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:  # closes fd even if a step below fails
            os.fchmod(fh.fileno(), 0o644)  # mkstemp makes the file owner-only; keep normal permissions
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)  # don't leave the temporary file behind (e.g. disk full)
        raise


def _write_or_report(path: Path, text: str) -> bool:
    """write_atomic for the commands: print an ERROR line instead of a traceback (e.g. disk full)."""
    try:
        write_atomic(path, text)
        return True
    except OSError as e:
        print(f"ERROR could not write {rel(path)}: {e}")
        return False


# ---------------------------------------------------------------- reading the spec


class _SpecParser(HTMLParser):
    """Collects the text inside every <div class="sc" id="..."><div class="scinner">, keyed by the sc id."""

    def __init__(self):
        super().__init__()
        self.blocks: dict[str, list[str]] = {}
        self._sc_id = None  # id of the sc div we are inside, waiting for its scinner
        self._anchor = None  # set while inside a scinner
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        classes = (a.get("class") or "").split()
        if tag == "div":
            if self._anchor:
                self._depth += 1
            elif "sc" in classes and a.get("id"):
                self._sc_id = a["id"]
            elif "scinner" in classes and self._sc_id:
                self._anchor, self._depth = self._sc_id, 1
                self.blocks[self._anchor] = []
        if self._anchor and tag in BLOCK_TAGS:
            self.blocks[self._anchor].append("\n")

    def handle_endtag(self, tag):
        if self._anchor and tag == "div":
            self._depth -= 1
            if self._depth == 0:
                self._anchor = self._sc_id = None

    def handle_data(self, data):
        if self._anchor:
            self.blocks[self._anchor].append(re.sub(r"\s+", " ", data))


def _tidy(text: str) -> str:
    """One line per spec paragraph or list item, single spaces, no blank lines."""
    lines = (re.sub(r" {2,}", " ", ln).strip() for ln in text.split("\n"))
    return "\n".join(ln for ln in lines if ln)


def parse_spec(html: str) -> dict[str, dict]:
    """Every success criterion in the spec HTML: {anchor: {"sc", "name", "level", "text"}}."""
    parser = _SpecParser()
    parser.feed(html)
    out = {}
    for anchor, parts in parser.blocks.items():
        m = HEADING.match(_tidy("".join(parts)))
        if not m:
            continue
        sc, name, body = m.groups()
        level = LEVEL_MARK.search(body)
        out[anchor] = {"sc": sc, "name": name.strip(), "level": level.group(1) if level else None,
                       "text": _tidy(LEVEL_MARK.sub("", body))}
    return out


def fetch_spec() -> str:
    """The spec HTML, retried up to SPEC_ATTEMPTS times. Raises one of SPEC_ERRORS if every try fails."""
    def download() -> str:
        with urllib.request.urlopen(SPEC, timeout=SPEC_TIMEOUT) as resp:
            return resp.read().decode("utf-8")

    for attempt in range(1, SPEC_ATTEMPTS):
        try:
            return download()
        except SPEC_ERRORS:
            time.sleep(2 * attempt)
    return download()  # last try: let the error reach the caller


def spec_mismatches(sc: str, found: dict | None) -> list[str]:
    """Differences between CANONICAL[sc] and what the spec says under that anchor."""
    name, level, anchor = CANONICAL[sc]
    if found is None:
        return [f"anchor #{anchor} not found in the spec"]
    problems = []
    if found["sc"] != sc:
        problems.append(f"anchor #{anchor} is {found['sc']} in the spec")
    if found["name"] != name:
        problems.append(f"spec name is {found['name']!r}")
    if found["level"] != level:
        problems.append(f"spec level is {found['level']!r}")
    if not found["text"]:
        problems.append("spec text is empty")
    return problems


# ---------------------------------------------------------------- checks


def canonical_errors() -> list[str]:
    """CANONICAL and CONTENT against each other and against scanners/wcag.py (Track A's allowlist)."""
    errors = []
    if set(CANONICAL) != WCAG20_A_AA:
        errors.append(f"CANONICAL vs scanners/wcag.py: missing {sorted(WCAG20_A_AA - set(CANONICAL))}, "
                      f"extra {sorted(set(CANONICAL) - WCAG20_A_AA)}")
    for sc, (_, level, _) in CANONICAL.items():
        expected = "A" if sc in WCAG20_A else "AA" if sc in WCAG20_AA else None
        if expected and level != expected:
            errors.append(f"{sc}: level {level!r} here, {expected!r} in scanners/wcag.py")
    if set(CONTENT) != set(CANONICAL):
        errors.append(f"CONTENT keys differ from CANONICAL: {sorted(set(CONTENT) ^ set(CANONICAL))}")
    return errors


def corpus_errors(rows) -> list[str]:
    """Every problem with the corpus rows, checked offline against CANONICAL and CONTENT."""
    if not isinstance(rows, list):
        return [f"expected a JSON list of rows, got {type(rows).__name__}"]
    errors, seen = canonical_errors(), set()
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            errors.append(f"row {i}: not an object")
            continue
        sc = r.get("sc")
        tag = f"row {i} ({sc})"
        if not isinstance(sc, str) or sc not in CANONICAL:
            errors.append(f"{tag}: not a WCAG 2.0 A/AA criterion")
            continue
        if sc in seen:
            errors.append(f"{tag}: duplicate")
        seen.add(sc)
        name, level, anchor = CANONICAL[sc]
        intent, fails, human_when, human_only = CONTENT[sc]
        expected = {"name": name, "level": level, "source": f"{SPEC}#{anchor}", "intent": intent,
                    "common_failures": fails, "needs_human_when": human_when, "human_only": human_only}
        errors += [f"{tag}: {k} should be {v!r}" for k, v in expected.items() if r.get(k) != v]
        failures = r.get("common_failures")
        if not isinstance(failures, list) or any(not FAILURE_ID.fullmatch(str(f)) for f in failures):
            errors.append(f"{tag}: common_failures must be a list of failure IDs like 'F65'")
        text = r.get("text")
        if not isinstance(text, str) or not text.strip():
            errors.append(f"{tag}: 'text' is empty")
        elif text.startswith(sc) or LEVEL_MARK.search(text):
            errors.append(f"{tag}: text still includes the SC heading or level")
    missing = set(CANONICAL) - seen
    if missing:
        errors.append(f"missing criteria: {sorted(missing)}")
    return errors


def load_corpus_with_hash(path: Path = CORPUS) -> tuple[dict[str, dict], str]:
    """({sc: row}, first 12 hex digits of the file's SHA-256), both from a single read of the file.

    The hash tells apart output made from different corpus builds. Raises CorpusError if the file
    is missing or malformed.
    """
    try:
        raw = path.read_bytes()
        rows = json.loads(raw.decode("utf-8"))
    except FileNotFoundError:
        raise CorpusError(f"{rel(path)} not found. Build it: python -m rag.corpus_tools build") from None
    except json.JSONDecodeError as e:
        raise CorpusError(f"{rel(path)} is not valid JSON: {e}") from None
    except (OSError, UnicodeDecodeError) as e:
        raise CorpusError(f"cannot read {rel(path)}: {e}") from None
    errors = corpus_errors(rows)
    if errors:
        raise CorpusError(f"{rel(path)} failed validation ({len(errors)} errors), first: {errors[0]}. "
                          "Run: python -m rag.corpus_tools validate")
    return {r["sc"]: r for r in rows}, hashlib.sha256(raw).hexdigest()[:12]


def load_corpus(path: Path = CORPUS) -> dict[str, dict]:
    """The corpus as {sc: row}. Raises CorpusError if the file is missing or malformed."""
    return load_corpus_with_hash(path)[0]


# ---------------------------------------------------------------- commands


def build(force: bool) -> int:
    """Write the corpus from CANONICAL, CONTENT and the live spec text. Writes nothing on any error."""
    if not force:
        # stat(), not exists(): exists() hides a permission error on Python 3.14 but not on 3.13.
        try:
            CORPUS.stat()
        except FileNotFoundError:
            pass
        except OSError as e:  # e.g. rag/ can't be searched
            print(f"ERROR cannot check {rel(CORPUS)}: {e}")
            return 1
        else:
            print(f"{rel(CORPUS)} exists; use --force to rebuild.")
            return 1
    errors = canonical_errors()
    try:
        found = parse_spec(fetch_spec())
    except SPEC_ERRORS as e:
        errors.append(f"could not fetch {SPEC}: {e}")
        found = None
    rows = []
    if found is not None:
        for sc, (name, level, anchor) in CANONICAL.items():
            problems = spec_mismatches(sc, found.get(anchor))
            errors += [f"{sc}: {p}" for p in problems]
            intent, fails, human_when, human_only = CONTENT[sc]
            rows.append({"sc": sc, "name": name, "level": level,
                         "text": found[anchor]["text"] if not problems else "",
                         "intent": intent, "common_failures": fails, "needs_human_when": human_when,
                         "human_only": human_only, "source": f"{SPEC}#{anchor}"})
    if errors:
        for e in errors:
            print("ERROR", e)
        print(f"Nothing written; {rel(CORPUS)} is unchanged.")
        return 1
    if not _write_or_report(CORPUS, json.dumps(rows, indent=2, ensure_ascii=False) + "\n"):
        return 1
    print(f"Wrote {len(rows)} criteria to {rel(CORPUS)}, text pulled from {SPEC}.")
    return 0


def validate() -> int:
    """Check the corpus file offline and print every error. Exit status 1 if there are any."""
    try:
        rows = json.loads(CORPUS.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        print(f"ERROR cannot read {rel(CORPUS)}: {e}")
        return 1
    errors = corpus_errors(rows)
    good_rows = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
    count = len({r["sc"] for r in good_rows if isinstance(r.get("sc"), str) and r["sc"] in CANONICAL})
    print(f"{count}/{len(CANONICAL)} criteria, {len(errors)} errors")
    for e in errors:
        print("ERROR", e)
    return 1 if errors else 0


def verify() -> int:
    """Compare the committed corpus with today's spec, row by row, and write a dated report."""
    try:
        corpus = load_corpus()
        found = parse_spec(fetch_spec())
    except (CorpusError, *SPEC_ERRORS) as e:
        print(f"ERROR {e}")
        print(f"{rel(VERIFICATION)} not written.")
        return 1
    lines, failed = [], 0
    for sc, (name, level, anchor) in CANONICAL.items():
        spec = found.get(anchor)
        problems = spec_mismatches(sc, spec)
        if spec and corpus[sc]["text"] != spec["text"]:
            problems.append("corpus text differs from the spec")
        failed += bool(problems)
        result = "; ".join(problems) if problems else "ok"
        lines.append(f"| {sc} | {name} | {level} | [#{anchor}]({SPEC}#{anchor}) | {result} |")
    passed = len(CANONICAL) - failed
    report = [
        "# WCAG 2.0 corpus verification",
        "",
        f"Generated by `python -m rag.corpus_tools verify` on {date.today().isoformat()}.",
        "",
        f"Each row of `{rel(CORPUS)}` was compared with {SPEC}: the anchor exists, the spec's number, "
        "name and level match `CANONICAL`, and the corpus `text` is identical to the spec text. "
        "`CANONICAL` was also checked against `scanners/wcag.py`.",
        "",
        f"**Result: {passed}/{len(CANONICAL)} criteria match the spec.**",
        "",
        "| SC | Name | Level | Spec anchor | Result |",
        "| --- | --- | --- | --- | --- |",
        *lines,
    ]
    canon = canonical_errors()
    canon_result = "; ".join(canon) if canon else f"identical ({len(CANONICAL)} criteria)."
    report += ["", f"`CANONICAL` vs `scanners/wcag.py`: {canon_result}", "", *SECTION508_VERIFICATION]
    map_problems = check_map508()
    report += ["", "Map check: " + ("; ".join(map_problems) if map_problems else
                                    f"ok ({len(CANONICAL)} rows match the table above)")]
    if not _write_or_report(VERIFICATION, "\n".join(report) + "\n"):
        return 1
    print(f"{passed}/{len(CANONICAL)} criteria match the spec. Wrote {rel(VERIFICATION)}")
    return 1 if failed or canon or map_problems else 0


def check_map508(path: Path = MAP508) -> list[str]:
    """What in the 508 map disagrees with the sections in SECTION508_VERIFICATION; empty if nothing."""
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        return [f"{rel(path)} unreadable ({e})"]
    if not isinstance(current, dict):
        return [f"{rel(path)} is not shaped like map508's output"]
    problems = [f"{sc} missing" for sc in CANONICAL if sc not in current]
    for sc, row in current.items():
        other = None if sc in NON_WEB_EXCEPT else True
        expected = {"web": "E205.4", "non_web_documents": other and "E205.4",
                    "software": other and "E207.2", "authoring_tools": "504.2"}
        if sc not in CANONICAL or not isinstance(row, dict):
            problems.append(f"{sc} is not a WCAG 2.0 A/AA row")
        elif any(row.get(k) != v for k, v in expected.items()):
            problems.append(f"{sc} differs from the sections")
        elif row.get("verified") is not True:
            problems.append(f"{sc} not verified")
    return problems


def map508() -> int:
    """Write the Revised 508 mapping, keeping any 'verified' flags a person has already set."""
    try:
        previous = json.loads(MAP508.read_text(encoding="utf-8"))
    except FileNotFoundError:
        previous = {}  # first run
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        print(f"ERROR cannot read {rel(MAP508)} ({e}); fix or delete it first.")
        return 1
    bad = [sc for sc, v in previous.items() if not isinstance(v, dict)] if isinstance(previous, dict) else None
    if bad is None or bad:
        where = f"entries {sorted(bad)} are" if bad else "the file is"
        print(f"ERROR {rel(MAP508)}: {where} not shaped like map508's output; fix or delete it first.")
        return 1
    out = {sc: {"web": "E205.4",
                "non_web_documents": None if sc in NON_WEB_EXCEPT else "E205.4",
                "software": None if sc in NON_WEB_EXCEPT else "E207.2",
                "authoring_tools": "504.2",
                "verified": previous.get(sc, {}).get("verified") is True} for sc in CANONICAL}
    if not _write_or_report(MAP508, json.dumps(out, indent=2) + "\n"):
        return 1
    done = sum(v["verified"] for v in out.values())
    print(f"Wrote {rel(MAP508)} ({done}/{len(out)} verified). Have a person check it and flip 'verified' to true.")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point; returns the exit status."""
    ap = argparse.ArgumentParser(prog="python -m rag.corpus_tools", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="write the corpus, text pulled from the W3C spec")
    b.add_argument("--force", action="store_true", help="overwrite an existing corpus")
    sub.add_parser("validate", help="offline check of every row")
    sub.add_parser("verify", help="compare with the live spec and write rag/verification.md")
    sub.add_parser("map508", help="write rag/section508_map.json")
    a = ap.parse_args(argv)
    if a.cmd == "build":
        return build(a.force)
    return {"validate": validate, "verify": verify, "map508": map508}[a.cmd]()


if __name__ == "__main__":
    sys.exit(main())
