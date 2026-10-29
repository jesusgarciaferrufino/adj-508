"""V. HTML validator check for WCAG 2.0 4.1.1 (Parsing), the (b) group in docs/missed_issues.md.

Runs the Nu HTML Checker (vnu) on each page's served HTML source and writes one page-level
hit per page that has parse errors, in the shared hit format (docs/TRACKS.md, "ADJ's own
checks"). scanners/merge.py turns the hit into a finding.

Writes data/scans/adj_validator_{version}_{page}.json:
  {"check": "validator", "version", "page", "url", "tool_version": "vnu 26.10.12",
   "hits": [{"rule_id": "adj.validator.parse-error", "sc": "4.1.1", "status": "violation", ...}]}

What counts (WCAG 2.0 4.1.1): complete start and end tags, elements nested per the spec,
no duplicate attributes, unique IDs. Only those Nu errors are kept (PARSE_ERRORS). Obsolete
elements, bad attribute values, missing doctype and CSS errors are HTML5 conformance, not
4.1.1, so they are ignored. That is also why the After pages get no hit.

The Before pages put <NOSCRIPT><B><FONT> in <head>, which ends the head early. Some Nu
versions stop there ("Cannot recover after last error"). When that happens the page is
checked a second time with the <head>'s <noscript> blocks blanked out (line numbers are
kept), and the message says so.

The checker runs locally through npm's vnu-jar (npm install). Its launcher uses the system's
Java 11+ if there is one; otherwise npm install has already downloaded a Java 17 runtime into
node_modules/.cache, so teammates don't need to install Java. --checker-url sends the HTML to
a Nu web service instead, e.g. https://validator.w3.org/nu/ (results can differ slightly
between versions).

Run from the repo root:
  python -m scanners.validator_scan                      # all 4 Before pages
  python -m scanners.validator_scan --version all
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import requests

from capture.capture import PAGES, VERSIONS, page_url

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "data" / "scans"
VNU = REPO / "node_modules" / "vnu-jar" / "vnu-jar.js"  # finds or downloads Java, then runs vnu.jar
RULE_ID = "adj.validator.parse-error"
TIMEOUT = 120  # seconds, for fetching a page or running the checker
LISTED = 12  # errors spelled out in the message; the rest are counted

# Nu error messages that are 4.1.1 parse errors: tags, nesting, attributes, IDs.
PARSE_ERRORS = re.compile(
    r"Stray (start|end) tag"
    r"|End tag “[^”]+” seen, but there were open elements"
    r"|End tag “[^”]+” violates nesting rules"
    r"|Unclosed element"
    r"|No “[^”]+” element in scope"
    r"|Start tag “[^”]+” seen but an element of the same type was already open"
    r"|Start tag “[^”]+” seen in “[^”]+”"
    r"|“[^”]+” start tag in table body"
    r"|Bad start tag in"
    r"|not allowed as child of element"
    r"|No space between attributes"
    r"|Duplicate attribute"
    r"|Duplicate ID"
    r"|Saw “[^”]*” when expecting"
    r"|Attribute value missing"
    r"|Quote “[^”]+” in attribute name"
)
FATAL = "Cannot recover after last error"
HEAD_NOSCRIPT = re.compile(r"<noscript\b.*?</noscript\s*>", re.I | re.S)
HTML_TAG = re.compile(r"<html\b[^>]*>", re.I)


class CheckerError(RuntimeError):
    """The Nu checker couldn't run or returned something that isn't its JSON report."""


def blank_head_noscript(html: str) -> str:
    """The same HTML with every <noscript> block before <body> blanked out, keeping its newlines."""
    body = re.search(r"<body\b", html, re.I)
    end = body.start() if body else len(html)
    head = HEAD_NOSCRIPT.sub(lambda m: "\n" * m.group(0).count("\n"), html[:end])
    return head + html[end:]


def _vnu_command() -> list[str]:
    node = shutil.which("node")
    if not node:
        raise CheckerError("Node.js is not installed; it runs vnu-jar (or pass --checker-url)")
    if not VNU.exists():
        raise CheckerError(f"{VNU.relative_to(REPO)} is missing; run npm install (or pass --checker-url)")
    return [node, str(VNU)]


def run_local(html: str) -> list[dict]:
    """Nu messages from the local vnu-jar."""
    p = subprocess.run(_vnu_command() + ["--format", "json", "--stdout", "--exit-zero-always", "-"],
                       input=html.encode("utf-8"), capture_output=True, timeout=TIMEOUT)
    try:
        return json.loads(p.stdout)["messages"]
    except (ValueError, KeyError) as e:
        why = p.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or [str(e)]
        raise CheckerError(f"vnu-jar gave no JSON report: {why[0]}") from None


def run_service(html: str, url: str) -> list[dict]:
    """Nu messages from a Nu web service."""
    r = requests.post(url, params={"out": "json"}, data=html.encode("utf-8"),
                      headers={"Content-Type": "text/html; charset=utf-8"}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()["messages"]


def local_version() -> str:
    """e.g. "vnu 26.10.12". Raises CheckerError when the local checker can't run."""
    p = subprocess.run(_vnu_command() + ["--version"], capture_output=True, timeout=TIMEOUT)
    words = p.stdout.decode("utf-8", "replace").split()
    if p.returncode != 0 or not words:
        why = p.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or ["no output"]
        raise CheckerError(f"vnu-jar could not start: {why[0]}")
    return "vnu " + words[0]


def parse_errors(messages: list[dict]) -> tuple[list[tuple[int, str]], bool]:
    """(line, message) for each 4.1.1 parse error, and whether the checker gave up early."""
    errors = [(m.get("lastLine") or 0, m["message"]) for m in messages
              if m.get("type") == "error" and PARSE_ERRORS.search(m["message"])]
    fatal = any(m.get("type") == "error" and FATAL in m["message"] for m in messages)
    return errors, fatal


def check_html(html: str, run) -> tuple[list[tuple[int, str]], list[str]]:
    """Parse errors in `html`, sorted by line, and notes for the message. `run` is run_local or a service call."""
    errors, fatal = parse_errors(run(html))
    notes = []
    if fatal:
        blanked = blank_head_noscript(html)
        if blanked != html:
            more, fatal = parse_errors(run(blanked))
            errors = list(dict.fromkeys(errors + more))
            notes.append("<noscript> in <head> ends the head early, so the checker stopped; the rest of the "
                         "page was checked again with that <noscript> removed")
        if fatal:
            notes.append("the checker stopped before the end of the page, so later errors are not listed")
    return sorted(set(errors)), notes


def plain(message: str) -> str:
    return message.replace("“", "").replace("”", "").rstrip(".")


def build_hit(errors: list[tuple[int, str]], notes: list[str], html: str) -> dict:
    listed = "; ".join(f"{plain(m)} (line {line})" for line, m in errors[:LISTED])
    more = f"; and {len(errors) - LISTED} more" if len(errors) > LISTED else ""
    note = f". Note: {'; '.join(notes)}" if notes else ""
    tag = HTML_TAG.search(html)
    return {"rule_id": RULE_ID, "sc": "4.1.1", "status": "violation",
            "message": f"{len(errors)} parse error(s) in the page source (F70): {listed}{more}{note}.",
            "dom_index": 0, "selector": "html", "html": tag.group(0) if tag else "<html>", "in_wrapper": False}


def build_result(html: str, version: str, name: str, url: str, run, tool_version: str) -> dict:
    errors, notes = check_html(html, run)
    hits = [build_hit(errors, notes, html)] if errors else []
    return {"check": "validator", "version": version, "page": name, "url": url,
            "tool_version": tool_version, "hits": hits}


def write_result(result: dict, out_dir: Path = OUT) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"adj_validator_{result['version']}_{result['page']}.json"
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return out


def fetch(url: str) -> str:
    r = requests.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    return r.content.decode(r.encoding or "utf-8", "replace")


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the Nu HTML Checker (WCAG 4.1.1) on W3C BAD pages.")
    ap.add_argument("--version", default="before", choices=VERSIONS + ["all"])
    ap.add_argument("--page", default="all", choices=PAGES + ["all"])
    ap.add_argument("--checker-url", help="use a Nu web service, e.g. https://validator.w3.org/nu/")
    a = ap.parse_args()
    versions = VERSIONS if a.version == "all" else [a.version]
    names = PAGES if a.page == "all" else [a.page]
    if a.checker_url:
        def run(h):
            return run_service(h, a.checker_url)
        tool_version = f"vnu service {a.checker_url}"
    else:
        try:
            run, tool_version = run_local, local_version()
        except (CheckerError, subprocess.TimeoutExpired) as e:
            print(f"Can't run the local checker: {e}", file=sys.stderr)
            return 1

    failed = 0
    for v in versions:
        for n in names:
            url = page_url(v, n)
            try:
                result = build_result(fetch(url), v, n, url, run, tool_version)
            except (requests.RequestException, CheckerError, subprocess.TimeoutExpired) as e:
                (OUT / f"adj_validator_{v}_{n}.json").unlink(missing_ok=True)
                print(f"{v:6} {n:8} FAILED, no file written: {e}", file=sys.stderr)
                failed += 1
                continue
            out = write_result(result)
            count = result["hits"][0]["message"].split(" ", 1)[0] if result["hits"] else "0"
            print(f"{v:6} {n:8} {count:>3} parse errors -> {len(result['hits'])} hit  -> {out.relative_to(REPO)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
