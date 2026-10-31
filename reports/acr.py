"""Track E draft ACR: one row per WCAG 2.0 A/AA criterion for the whole Before site (VPAT 2.5 Rev 508).

Hard rule (CONTEXT.md): AI never sets a conformance level. A level comes ONLY from human decisions,
through reports.matrix.criterion_status, which sees finding IDs and decisions and nothing else.

Run it as a module from the repo root:  python -m reports.acr --runs <run ids>
"""

import argparse
import datetime
import html
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import get_args
from urllib.parse import quote

from rag.corpus_tools import CorpusError, load_corpus, write_atomic
from reports import matrix
from reports.matrix import (
    STATUS_FAIL,
    STATUS_NOT_APPLICABLE,
    STATUS_SUPPORTS,
    _ai_leaning,
    _md_cell,
    _sc_order,
    criterion_status,
)
from scanners.findings import REPO, FindingsFileError, PageName

# The ACR's conformance levels. VPAT 2.5 reserves "Not Evaluated" for AAA criteria, so a criterion
# nobody has decided yet reads "Pending human review": the draft's flag for what a human must still check.
LEVEL_SUPPORTS = "Supports"
LEVEL_PARTIAL = "Partially Supports"
LEVEL_FAIL = "Does Not Support"
LEVEL_NOT_APPLICABLE = "Not Applicable"
LEVEL_PENDING = "Pending human review"


def acr_level(page_statuses):
    """One criterion's ACR level, from its per-page statuses (matrix.criterion_status, one per Before page).

    A human-confirmed fail on any page is a confirmed failure of the criterion, and pages still
    pending can't undo it. Only a page a human confirmed as Supports turns it into Partially Supports.
    With no fail, the criterion is only settled once every page is: any page still Pending, or with
    no findings for a human to decide, keeps the whole row Pending.
    """
    if STATUS_FAIL in page_statuses:
        return LEVEL_PARTIAL if STATUS_SUPPORTS in page_statuses else LEVEL_FAIL
    if page_statuses and all(s in (STATUS_SUPPORTS, STATUS_NOT_APPLICABLE) for s in page_statuses):
        return LEVEL_SUPPORTS if STATUS_SUPPORTS in page_statuses else LEVEL_NOT_APPLICABLE
    return LEVEL_PENDING


def findings_by_criterion(runs):
    """Group one site version's findings as {criterion: {page: [findings]}}.

    runs: (summary, findings) pairs from matrix.load_run, one per page. Groups by wcag[0], like
    matrix.build_matrix. A page with no findings for a criterion is absent from that criterion's dict.
    """
    grouped = {}
    for summary, findings in runs:
        for f in findings:
            grouped.setdefault(f["wcag"][0], {}).setdefault(summary["page"], []).append(f)
    return grouped


def criterion_level(by_page, decisions_by_page, pages):
    """One criterion's ACR level for the site.

    by_page            this criterion's entry from findings_by_criterion: {page: [findings]}
    decisions_by_page  {page: {finding_id: decision line}}, from matrix.load_decisions for each page
    pages              every Before page; one missing from by_page has no findings for a human to decide

    criterion_status takes finding IDs and decisions, never the findings, so bucket and ai can't
    reach the level.
    """
    statuses = [criterion_status([f["finding_id"] for f in by_page.get(page, [])], decisions_by_page.get(page, {}))
                for page in pages]
    return acr_level(statuses)


class AcrError(ValueError):
    """The ACR can't be built honestly, e.g. a row has a level but no evidence behind it."""


def criterion_evidence(sc, level, by_page, decisions_by_page):
    """The findings behind one criterion's row, one dict per finding, in page order.

    Decided findings are the evidence for the level. On a Pending row, undecided machine findings are
    listed too (decision None): they back ADJ's proposal, for a human to ratify. Other undecided
    findings are left out. A level other than Pending with no decided finding behind it is a bug, so
    it raises AcrError; undecided findings never count for that.
    """
    evidence = []
    for page, findings in by_page.items():
        decisions = decisions_by_page.get(page, {})
        for f in findings:
            d = decisions.get(f["finding_id"])
            if d or (level == LEVEL_PENDING and f.get("bucket") == "machine"):
                evidence.append({"finding_id": f["finding_id"], "page": page,
                                 "decision": d["decision"] if d else None, "reviewer": d["reviewer"] if d else None,
                                 "selector": f["evidence"]["selector"], "screenshot": f["evidence"]["screenshot"]})
    if level != LEVEL_PENDING and not any(e["decision"] for e in evidence):
        raise AcrError(f"{sc}: level {level!r} has no decided finding behind it")
    return evidence


PROPOSAL = "Proposed by ADJ (machine-verified, awaiting human ratification): Does Not Support."


def adj_proposal(level, by_page, decisions_by_page):
    """ADJ's proposed level for a human to ratify, or "" when it has none.

    Only a Pending row with at least one undecided machine finding (a deterministic scanner or rule
    violation) gets one. AI leanings never do, and a row a human has decided has nothing to ratify.
    """
    if level != LEVEL_PENDING:
        return ""
    undecided_machine = any(f.get("bucket") == "machine" and f["finding_id"] not in decisions_by_page.get(page, {})
                            for page, findings in by_page.items() for f in findings)
    return PROPOSAL if undecided_machine else ""


def criterion_remarks(by_page, decisions_by_page):
    """The counts half of a row's Remarks: confirmed failures, what is still pending, and the AI's leaning.

    The AI's counts come last and only under "AI analysis (not a decision):", so they can't read as
    a result. Empty parts are left out; a criterion with no grey findings has no AI sentence.
    """
    failed, pending = [], []
    for page, findings in by_page.items():
        decisions = decisions_by_page.get(page, {})
        n_failed = sum(1 for f in findings if decisions.get(f["finding_id"], {}).get("decision") == "fail")
        n_pending = sum(1 for f in findings if f["finding_id"] not in decisions)
        if n_failed:
            failed.append(f"{page} ({n_failed})")
        if n_pending:
            pending.append(f"{page} {n_pending}")
    parts = []
    if failed:
        parts.append(f"Confirmed failures: {', '.join(failed)}.")
    if pending:
        parts.append(f"Pending human review: {', '.join(pending)}.")
    leaning = _ai_leaning([f for findings in by_page.values() for f in findings])
    if leaning:
        parts.append("AI analysis (not a decision): " + leaning.removeprefix("AI: ") + ".")
    return " ".join(parts)


MAX_COMMENTS = 2
MAX_COMMENT_CHARS = 300  # a Remarks cell stays readable; the full text is kept in data/reviews/


def reviewer_comments(by_page, decisions_by_page):
    """Up to MAX_COMMENTS reviewer comments for one criterion, each as `reviewer: "comment"`.

    Fails come before passes and newer before older within each (decided_at is UTC ISO text, so it
    sorts as text). A decision with no comment is skipped.
    """
    lines = [d for page, findings in by_page.items() for f in findings
             if (d := decisions_by_page.get(page, {}).get(f["finding_id"])) and d.get("comment", "").strip()]
    lines.sort(key=lambda d: d["decided_at"], reverse=True)
    lines.sort(key=lambda d: d["decision"] != "fail")  # stable: keeps newest-first inside each group
    return [f'{d["reviewer"]}: "{_shorten(d["comment"].strip())}"' for d in lines[:MAX_COMMENTS]]


def _shorten(text):
    """text cut to MAX_COMMENT_CHARS, ending in … when it was cut."""
    return text if len(text) <= MAX_COMMENT_CHARS else text[:MAX_COMMENT_CHARS - 1].rstrip() + "…"


def build_acr(runs, decisions_by_page, corpus):
    """One row per WCAG 2.0 A/AA criterion (38), in criterion order. Touches no files.

    runs               (summary, findings) per Before page, from matrix.load_run
    decisions_by_page  {page: {finding_id: decision line}}, from matrix.load_decisions
    corpus             rag.corpus_tools.load_corpus(): {sc: {"name", "level", ...}}

    Row: sc, name, wcag_level ("A" or "AA", for Table 1 / Table 2), conformance (from human decisions
    only), remarks (reviewer comments, then counts), evidence (criterion_evidence; raises AcrError
    when a level has none).
    """
    grouped = findings_by_criterion(runs)
    pages = [summary["page"] for summary, _ in runs]
    rows = []
    for sc in sorted(corpus, key=_sc_order):
        by_page = grouped.get(sc, {})
        level = criterion_level(by_page, decisions_by_page, pages)
        remarks = " ".join(part for part in [*reviewer_comments(by_page, decisions_by_page),
                                             adj_proposal(level, by_page, decisions_by_page),
                                             criterion_remarks(by_page, decisions_by_page)] if part)
        rows.append({"sc": sc, "name": corpus[sc]["name"], "wcag_level": corpus[sc]["level"],
                     "conformance": level, "remarks": remarks,
                     "evidence": criterion_evidence(sc, level, by_page, decisions_by_page)})
    return rows


PRODUCT = "CityLights (W3C Before and After Demonstration, Before version)"
STANDARD = "Revised Section 508, E205.4, WCAG 2.0 Level A and AA"
# Display names of the checks that can produce findings, in report order. A finding names its checks in
# scanner_details: scanner (axe-core, pa11y, adj-rules, adj-vision) and rule_id ("adj.{check}.{rule}").
# The validator and the page comparison write adj-rules findings, so only the rule_id tells them apart.
TOOL_ORDER = ("axe-core", "pa11y", "adj.rules", "adj.validator", "adj.compare", "adj-vision")
TOOL_LABELS = {"axe-core": "axe-core{v}", "pa11y": "Pa11y{v} (HTML_CodeSniffer)", "adj.rules": "ADJ rule checks",
               "adj.validator": "Nu HTML Checker", "adj.compare": "ADJ page comparison",
               "adj-vision": "AI on screenshots"}
DRAFT_LINE = ("Draft generated by ADJ. Conformance levels are set only by the named human reviewers; "
              "AI analysis appears in Remarks, labelled as AI.")
TABLES = (("A", "Table 1: Success Criteria, Level A"), ("AA", "Table 2: Success Criteria, Level AA"))
COLUMNS = ("Criteria", "Conformance Level", "Remarks and Explanations", "Evidence")


def npm_versions(path=REPO / "package.json"):
    """The scanner versions pinned in package.json, e.g. {"axe-core": "4.13.0", "pa11y": "10.0.0"}.

    These are the pins, not a record of what was installed at scan time. Empty if the file can't be read.
    """
    try:
        deps = json.loads(path.read_text(encoding="utf-8")).get("dependencies", {})
    except (OSError, ValueError, AttributeError):
        return {}
    return {name: v.lstrip("^~") for name, v in deps.items() if isinstance(v, str)}


def tool_names(runs, versions=None):
    """The checks that actually produced findings in these runs, as display names in report order.

    runs      (summary, findings) pairs; each finding's scanner_details says which check fired
    versions  optional {"axe-core": "4.13.0", "pa11y": "10.0.0"} from npm_versions, appended to the names

    A check this code doesn't know (a new adj.{check}) is still named, never left out.
    """
    versions = versions or {}
    used, unknown = set(), set()
    for _, findings in runs:
        for f in findings:
            for d in f["scanner_details"]:
                if d["scanner"] == "adj-rules":
                    check = ".".join(d["rule_id"].split(".")[:2])
                    (used if check in TOOL_ORDER else unknown).add(check)
                else:
                    used.add(d["scanner"])
    names = []
    for key in TOOL_ORDER:
        if key in used:
            v = versions.get(key)  # only axe-core and pa11y have a label that takes a version
            names.append(TOOL_LABELS[key].format(v=f" {v}" if v else ""))
    return names + [f"ADJ check: {c.split('.')[-1]}" for c in sorted(unknown)]


def report_date():
    """Today's date in UTC, the time zone of every decided_at in data/reviews/."""
    return datetime.datetime.now(datetime.UTC).date().isoformat()


def _runs_text(run_ids):
    """'home <run id>, news <run id>, ...': which run each page's rows came from."""
    return ", ".join(f"{page} {run_id}" for page, run_id in run_ids.items())


def _methods(tools, model_ids):
    """The Evaluation methods list: the tools that ran, the models the runs used, then the human review."""
    return [*tools, *(f"{m} on Amazon Bedrock (FOR/AGAINST evidence)" for m in model_ids), "human review in PULSE"]


# Every line break Markdown or str.splitlines() honours; inside a table cell any of them ends the row
LINE_BREAKS = re.compile(r"[\r\n\v\f\x1c-\x1e\x85\u2028\u2029]+")


def _md_text(value):
    """Free text (names, remarks, notes) made safe for a Markdown table cell.

    Backslashes are doubled first, so text like a\\|b can't turn the pipe escape that _md_cell adds
    back into a border. < and & become entities and [ is escaped, so reviewer text can't inject
    HTML or a link into the ACR. * and _ are left alone: at worst they italicise a word.
    """
    text = LINE_BREAKS.sub(" ", str(value)).replace("\\", "\\\\")
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace("[", "\\[")
    return _md_cell(text)


def _md_code(text):
    """A selector as a Markdown code span that survives backticks and pipes inside it.

    Code spans show backslashes as written, so CSS escapes (#a\\:b) stay readable; only a backslash
    right before a pipe is doubled, so the pipe's escape still holds. The fence is one backtick
    longer than any run inside, padded with spaces when there is one.
    """
    text = LINE_BREAKS.sub(" ", text)
    text = re.sub(r"\\+(?=\|)", lambda m: m.group() * 2, text).replace("|", "\\|")
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence, pad = "`" * (longest + 1), " " if longest else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def _decided_text(e):
    """'fail by jesus' for a decided finding; an undecided machine finding is waiting for a human."""
    return f"{e['decision']} by {e['reviewer']}" if e["decision"] else "machine-verified, awaiting review"


def _evidence_cell(evidence, shot_href):
    """One row's Evidence cell: each finding with its page, decision and reviewer (or that it awaits
    review) and screenshot link.

    A finding with no screenshot (e.g. <title>) shows its selector instead. Each piece is escaped on
    its own, so the cell's own link and code span keep working.
    """
    items = []
    for e in evidence:
        if e["screenshot"]:
            # the link names its finding, so a list of the page's links reads apart (WCAG 2.4.4)
            where = f"[screenshot of {_md_text(e['finding_id'])}]({quote(shot_href(e['screenshot']), safe='/._-~')})"
        else:
            where = _md_code(e["selector"])
        about = _md_text(f"{e['finding_id']} ({e['page']}, {_decided_text(e)}, ")
        items.append(f"{about}{where})")
    return "; ".join(items) or "—"


def format_acr_md(rows, report_date, notes=(), model_ids=(), shot_href=lambda path: path, tools=(), run_ids=None):
    """The draft ACR as Markdown text (VPAT 2.5 Rev 508, WCAG section): a header, then Table 1 and Table 2.

    rows        from build_acr
    notes       warnings shown above the tables, e.g. that a run used stand-ins
    model_ids   the models the runs used (summary.json model_ids); listed under Evaluation methods
    tools       the checks that produced findings (tool_names); listed under Evaluation methods
    run_ids     {page: run_id} the rows came from, so the ACR traces back to data/runs/
    shot_href   turns an evidence.screenshot path into a link that works from where the file is saved
    """
    methods = _methods(tools, model_ids)
    lines = ["# Accessibility Conformance Report (draft)", "",
             f"**Product:** {PRODUCT}  ", f"**Report date:** {report_date}  ",
             f"**Standard:** {STANDARD}  ",
             *([f"**Runs:** {_runs_text(run_ids)}  "] if run_ids else []),
             f"**Evaluation methods:** {', '.join(methods)}", "",
             f"**{DRAFT_LINE}**", ""]
    for note in notes:
        lines += [f"> **Warning:** {_md_text(note)}", ""]  # notes carry finding IDs and reviewer names
    for wcag_level, title in TABLES:
        lines += [f"## {title}", "", "| " + " | ".join(COLUMNS) + " |", "|" + " --- |" * len(COLUMNS)]
        for r in (r for r in rows if r["wcag_level"] == wcag_level):
            cells = (_md_text(f"{r['sc']} {r['name']}"), _md_text(r["conformance"]), _md_text(r["remarks"]),
                     _evidence_cell(r["evidence"], shot_href))
            lines.append("| " + " | ".join(cells) + " |")
        lines.append("")
    return "\n".join(lines)


STYLE = """
body { font: 16px/1.5 system-ui, sans-serif; max-width: 72rem; margin: 0 auto; padding: 1rem;
       color: #111; background: #fff; }
table { border-collapse: collapse; width: 100%; margin-bottom: 2rem; }
caption { text-align: left; font-weight: bold; font-size: 1.2rem; margin: 1rem 0 .5rem; }
th, td { border: 1px solid #555; padding: .4rem .6rem; text-align: left; vertical-align: top; }
thead th { background: #eee; }
.warning { border-left: 4px solid #333; padding-left: .75rem; }
td ul { margin: 0; padding-left: 1.1rem; }
@media print { body { max-width: none; font-size: 11pt; } tr { break-inside: avoid; } }
"""


def _evidence_html(evidence, shot_href):
    """One row's Evidence cell as HTML; same content as _evidence_cell."""
    if not evidence:
        return "—"
    items = []
    for e in evidence:
        if e["screenshot"]:
            where = (f'<a href="{html.escape(shot_href(e["screenshot"]))}">'
                     f'screenshot of {html.escape(e["finding_id"])}</a>')
        else:
            where = f"<code>{html.escape(e['selector'])}</code>"
        head = html.escape(f"{e['finding_id']} ({e['page']}, {_decided_text(e)}, ")
        items.append(f"<li>{head}{where})</li>")
    return "<ul>" + "".join(items) + "</ul>"


def format_acr_html(rows, report_date, notes=(), model_ids=(), shot_href=lambda path: path, tools=(), run_ids=None):
    """The draft ACR as a printable HTML page: the same content as format_acr_md.

    Every value that came from data goes through html.escape. Each table has a caption and
    <th scope> headers, and a criterion's cell is a row header, so a screen reader names the row.
    """
    esc = html.escape
    out = ["<!doctype html>", '<html lang="en">', "<head>", '<meta charset="utf-8">',
           '<meta name="viewport" content="width=device-width, initial-scale=1">',
           "<title>Accessibility Conformance Report (draft)</title>", f"<style>{STYLE}</style>", "</head>",
           "<body>", "<main>", "<h1>Accessibility Conformance Report (draft)</h1>",
           f"<p><strong>Product:</strong> {esc(PRODUCT)}<br>",
           f"<strong>Report date:</strong> {esc(report_date)}<br>",
           f"<strong>Standard:</strong> {esc(STANDARD)}<br>",
           *([f"<strong>Runs:</strong> {esc(_runs_text(run_ids))}<br>"] if run_ids else []),
           f"<strong>Evaluation methods:</strong> {esc(', '.join(_methods(tools, model_ids)))}</p>",
           f"<p><strong>{esc(DRAFT_LINE)}</strong></p>"]
    out += [f'<p class="warning"><strong>Warning:</strong> {esc(note)}</p>' for note in notes]
    for wcag_level, title in TABLES:
        out += ["<table>", f"<caption>{esc(title)}</caption>", "<thead><tr>",
                *(f'<th scope="col">{esc(c)}</th>' for c in COLUMNS), "</tr></thead>", "<tbody>"]
        for r in (r for r in rows if r["wcag_level"] == wcag_level):
            out.append(f'<tr><th scope="row">{esc(r["sc"] + " " + r["name"])}</th><td>{esc(r["conformance"])}</td>'
                       f'<td>{esc(r["remarks"])}</td><td>{_evidence_html(r["evidence"], shot_href)}</td></tr>')
        out += ["</tbody>", "</table>"]
    out += ["</main>", "</body>", "</html>", ""]
    return "\n".join(out)


PAGES = get_args(PageName)  # the four pages, in report order


def _orphan_notes(decisions, findings):
    """Warnings for decisions on findings the run doesn't have (e.g. a rescan dropped them).

    data/reviews/ is append-only and matched by finding ID, so these can't be deleted, and a row is
    built only from the run's findings. A human fail left out could make its row read too well, so
    that warning says so and names the criterion (the last part of the finding ID).
    """
    have = {f["finding_id"] for f in findings}
    notes = []
    for finding_id, d in decisions.items():
        if finding_id in have:
            continue
        if d["decision"] == "fail":
            sc = finding_id.rsplit("-", 1)[-1]
            notes.append(f"A human-confirmed fail on {finding_id} ({d['reviewer']}) is NOT counted: the finding "
                         f"is not in the run. Criterion {sc} may be understated.")
        else:
            notes.append(f"The {d['decision']} decision on {finding_id} ({d['reviewer']}) is not counted: "
                         "the finding is not in the run.")
    return notes


def _check_run(run_id, summary, findings):
    """Raise AcrError if a run can't be reported on as it is.

    matrix.load_run checks only that summary.json has version and page, so the fields the ACR prints
    (model_ids, stubs) and the run's page are checked here, and every finding must be for the page
    its summary names. A malformed run stops the ACR with its name rather than being quietly fixed.
    """
    if summary["page"] not in PAGES:
        raise AcrError(f"run {run_id}: summary.json page {summary['page']!r} is not one of {list(PAGES)}")
    for field in ("model_ids", "stubs"):
        value = summary.get(field, [])
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise AcrError(f"run {run_id}: summary.json {field} must be a list of strings, got {value!r}")
    for f in findings:
        if (f["site_version"], f["page"]) != (summary["version"], summary["page"]):
            raise AcrError(f"run {run_id}: summary.json says {summary['version']}/{summary['page']}, but finding "
                           f"{f['finding_id']} is {f['site_version']}/{f['page']}")


def load_before_site(run_ids):
    """Read the Before site, which the ACR reports on: load_site(run_ids, "before")."""
    return load_site(run_ids, "before")


def load_site(run_ids, version):
    """Read one site version: one run per page, plus each page's human decisions.

    Returns (runs, decisions_by_page, notes, model_ids): runs as (summary, findings) in PAGES order;
    notes are warnings for the top of a report (stand-in runs, skipped reviews lines); model_ids are
    the models the runs used. Runs of the other version in run_ids are ignored. Raises AcrError unless
    every page has exactly one run of this version, and for a malformed run (_check_run).
    """
    runs, decisions_by_page, notes_by_page, model_ids = load_site_by_page(run_ids, version)
    return runs, decisions_by_page, [n for page in PAGES for n in notes_by_page[page]], model_ids


def load_site_by_page(run_ids, version):
    """load_site, with the warnings kept per page ({page: [notes]}) for reports about one page."""
    found = {}
    for run_id in run_ids:
        summary, findings = matrix.load_run(run_id)
        if summary["version"] == version:
            _check_run(run_id, summary, findings)
            found.setdefault(summary["page"], []).append((run_id, summary, findings))
    missing = [p for p in PAGES if p not in found]
    several = [p for p in PAGES if len(found.get(p, [])) > 1]
    if missing or several:
        raise AcrError(f"need exactly one {version} run per page; missing: {missing or 'none'}, "
                       f"more than one: {several or 'none'}")

    runs, decisions_by_page, notes_by_page, model_ids = [], {}, {}, set()
    for page in PAGES:
        notes = notes_by_page[page] = []
        run_id, summary, findings = found[page][0]
        summary["run_id"] = run_id  # the folder name is the run's ID; the writers print it in the header
        runs.append((summary, findings))
        decisions_by_page[page], skipped = matrix.load_decisions(version, page)
        notes += _orphan_notes(decisions_by_page[page], findings)
        stub_note = matrix._stub_note(summary)
        if stub_note:
            notes.append(f"Run {run_id} ({page}): {stub_note}")
        # load_decisions names the file by its absolute path; the report shows it repo-style instead,
        # so a deliverable never carries someone's local folders
        reviews_file = matrix.REVIEWS_DIR / f"{version}_{page}.jsonl"
        notes += [f"Skipped reviews {s.replace(str(reviews_file), f'data/reviews/{reviews_file.name}')}. "
                  "Its decision is not counted; the criterion may show Pending." for s in skipped]
        model_ids.update(summary.get("model_ids", []))
    return runs, decisions_by_page, notes_by_page, sorted(model_ids)


def _write_together(files):
    """Write {path: text} so that either every file is new or none changed.

    acr.md and acr.html are one report: each is staged under a temporary name first, and only when
    all are written are they moved into place. A failure (disk full) leaves the old pair as it was.
    """
    staged = {path: path.with_name(f".{path.name}.staged") for path in files}
    try:
        for path, text in files.items():
            write_atomic(staged[path], text)
    except OSError:
        for tmp in staged.values():
            tmp.unlink(missing_ok=True)
        raise
    for path, tmp in staged.items():
        os.replace(tmp, path)


def main(argv=None):
    p = argparse.ArgumentParser(description="Build the draft ACR for the Before site")
    p.add_argument("--runs", required=True, nargs="+", metavar="RUN_ID",
                   help="folder names in data/runs/: one Before run per page (After runs are ignored)")
    p.add_argument("--out", metavar="DIR", help="where to write acr.md and acr.html (default: data/reports/acr)")
    args = p.parse_args(argv)

    try:
        runs, decisions, notes, model_ids = load_before_site(args.runs)
        rows = build_acr(runs, decisions, load_corpus())
    except (AcrError, matrix.MatrixInputError, FindingsFileError, CorpusError) as e:
        print(f"ERROR {e}", file=sys.stderr)
        return 1

    out = matrix.REPORTS_DIR / "acr" if args.out is None else Path(args.out)
    shown = (rows, report_date(), notes, model_ids)
    run_ids = {summary["page"]: summary["run_id"] for summary, _ in runs}

    def shot_href(path):  # screenshots stay in data/findings/shots/; link to them from wherever the ACR is saved
        return os.path.relpath(REPO / path, out)

    tools = tool_names(runs, npm_versions())
    md = format_acr_md(*shown, shot_href=shot_href, tools=tools, run_ids=run_ids)
    page = format_acr_html(*shown, shot_href=shot_href, tools=tools, run_ids=run_ids)
    try:
        _write_together({out / "acr.md": md, out / "acr.html": page})
    except OSError as e:
        print(f"ERROR could not write the ACR: {e}", file=sys.stderr)
        return 1

    for note in notes:
        print(f"WARNING {note}", file=sys.stderr)
    print(", ".join(f"{n} {level}" for level, n in Counter(r["conformance"] for r in rows).most_common()))
    print(f"Wrote {out / 'acr.md'}\n      {out / 'acr.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
