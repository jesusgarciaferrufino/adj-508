"""Track E evidence package: the site matrix, draft ACR, backlog and evidence pages in one folder.

python -m reports.package --runs <8 run ids> writes data/reports/<package_id>/ from one run per page
of each site version, plus data/reviews/. Statuses and conformance levels come only from human
decisions (CONTEXT.md); every AI field shown is labelled as AI.
"""

import argparse
import csv
import html
import io
import os
import re
import shutil
import sys
from collections import Counter

from agents.runlog import new_run_id
from rag.corpus_tools import CorpusError, load_corpus, write_atomic
from reports import acr, backlog, matrix
from reports.acr import PAGES, PRODUCT, STYLE, _md_text, findings_by_criterion
from reports.matrix import _sc_order, criterion_status
from scanners.findings import REPO, FindingsFileError
from scripts.score import SCORES_DIR, git_commit

SHOTS_DIR = REPO / "data" / "findings" / "shots"  # the only folder screenshots are copied from



class PackageError(ValueError):
    """The package can't be built honestly, e.g. a finding ID that can't be a file name."""


SITE_COLUMNS = ("section508", "sc", "name", "level", *PAGES)


def build_site_matrix(runs, decisions_by_page, corpus, map508):
    """One row per WCAG 2.0 A/AA criterion (38), with each page's status: the whole site at a glance.

    runs               (summary, findings) per page of one site version, from acr.load_site
    decisions_by_page  {page: {finding_id: decision line}}
    A cell is matrix.criterion_status for that page, so it sees finding IDs and human decisions only.
    """
    grouped = findings_by_criterion(runs)
    rows = []
    for sc in sorted(corpus, key=_sc_order):
        row = {"section508": map508[sc]["web"], "sc": sc, "name": corpus[sc]["name"], "level": corpus[sc]["level"]}
        for page in PAGES:
            ids = [f["finding_id"] for f in grouped.get(sc, {}).get(page, [])]
            row[page] = criterion_status(ids, decisions_by_page.get(page, {}))
        rows.append(row)
    return rows


def format_site_matrix_csv(rows):
    """The site matrix as CSV, header first."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=SITE_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def format_site_matrix_md(rows, title, notes=()):
    """The site matrix as Markdown: criteria down the side, the four pages across."""
    lines = [f"# {title}", ""]
    for note in notes:
        lines += [f"> **Warning:** {_md_text(note)}", ""]
    lines += ["Status comes only from human reviewers' decisions. \"Pending human review\" means findings on that "
              "page wait for a reviewer; \"No automated findings\" still needs a human check.", "",
              "| 508 | Criterion | Level | " + " | ".join(PAGES) + " |", "|" + " --- |" * (3 + len(PAGES))]
    for r in rows:
        cells = (r["section508"], f"{r['sc']} {r['name']}", r["level"], *(r[p] for p in PAGES))
        lines.append("| " + " | ".join(_md_text(c) for c in cells) + " |")
    return "\n".join(lines + [""])


def copy_shots(findings, out):
    """Copy the findings' screenshots into out/shots/, so the package's links work from any folder or zip.

    Returns (copied, notes): copied maps each evidence.screenshot path to its place in the package
    ("shots/<name>"); a screenshot shared by several findings is copied once. A finding with no
    screenshot is skipped quietly (the reports show its selector). A missing file, or a path outside
    data/findings/shots/, is skipped with one note per path: run data must not pull other files into a
    deliverable.
    """
    copied, notes, skipped = {}, [], set()
    shots = SHOTS_DIR.resolve()
    for f in findings:
        path = f["evidence"]["screenshot"]
        if not path or path in copied or path in skipped:  # each file is copied, or noted, once
            continue
        src = (REPO / path).resolve()
        if not src.is_relative_to(shots):
            skipped.add(path)
            notes.append(f"Screenshot {path!r} for {f['finding_id']} is outside data/findings/shots/, so it is "
                         "not in the package; its selector is shown instead.")
            continue
        if not src.is_file():
            skipped.add(path)
            notes.append(f"Screenshot {path} for {f['finding_id']} is missing, so it is not in the package; "
                         "its selector is shown instead.")
            continue
        name = src.relative_to(shots).as_posix()
        (out / "shots" / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, out / "shots" / name)
        copied[path] = f"shots/{name}"
    return copied, notes


def shot_href(copied, depth=0):
    """A shot_href for the ACR and backlog writers: a screenshot's link from a page depth folders below
    the package root (0 for index, acr, backlog; 1 for evidence/ pages)."""
    return lambda path: "../" * depth + copied[path]


FINDING_ID = re.compile(r"F-(before|after)-[a-z]+-el\d{4}-\d\.\d{1,2}\.\d{1,2}")


def evidence_filename(finding_id):
    """evidence/<finding_id>.html. load_run has already checked the ID's format; it is checked again here
    because it becomes a file name, and nothing in run data may write outside evidence/."""
    if not FINDING_ID.fullmatch(finding_id):
        raise PackageError(f"{finding_id!r} is not a finding ID, so it can't name an evidence page")
    return f"{finding_id}.html"


DECISION_WORDS = {"fail": "Fail", "pass": "Pass", "not_applicable": "Not applicable"}
EVIDENCE_STYLE = STYLE + """
pre { white-space: pre-wrap; overflow-wrap: anywhere; background: #f4f4f4; padding: .5rem; }
img { max-width: 100%; height: auto; border: 1px solid #555; }
dt { font-weight: bold; } dd { margin: 0 0 .5rem 0; }
"""


def _ai_html(ai):
    """The FOR/AGAINST section, under a heading that says it is AI analysis and not a decision."""
    esc = html.escape
    out = ["<h2>AI analysis (not a decision)</h2>"]
    case_for = ai.get("case_for") or ""
    if case_for.startswith("SKIPPED:") or not (case_for or ai.get("case_against")):
        why = f" ({case_for})" if case_for else ""
        return out + [f"<p>Not evaluated by AI{esc(why)}. A human decides.</p>"]
    out += ["<p>The AI argues both sides; a human reviewer makes the decision.</p>",
            "<dl>", f"<dt>AI leaning</dt><dd>{esc(str(ai.get('recommendation')))}</dd>",
            f"<dt>Citations verified</dt><dd>Citations verified: {'yes' if ai.get('citations_verified') else 'no'}"
            "</dd>", f"<dt>Model</dt><dd>{esc(str(ai.get('model_id', 'not recorded')))}</dd>",
            f"<dt>Prompts</dt><dd>{esc(str(ai.get('prompt_version') or 'not recorded'))}</dd>", "</dl>",
            "<h3>Case for a failure</h3>", f"<pre>{esc(case_for)}</pre>",
            "<h3>Case against a failure</h3>", f"<pre>{esc(ai.get('case_against') or '')}</pre>"]
    return out


def _url_html(url):
    """A web address as a link, or as plain text when it isn't http(s) (a javascript: URL must not be clickable)."""
    if re.match(r"https?://", url):
        return f'<a href="{html.escape(url)}">{html.escape(url)}</a>'
    return html.escape(url)


def format_evidence_html(finding, decision, corpus, shot):
    """One finding's evidence page: what was found where, the human decision, and the AI's analysis.

    decision  the finding's line from data/reviews/, or None when nobody has decided it
    shot      the screenshot's link from evidence/ (shot_href(copied, depth=1)), or None to show the selector
    Every value that came from data goes through html.escape.
    """
    esc = html.escape
    fid, ev, sc = finding["finding_id"], finding["evidence"], finding["wcag"][0]
    c = corpus[sc]
    out = ["<!doctype html>", '<html lang="en">', "<head>", '<meta charset="utf-8">',
           '<meta name="viewport" content="width=device-width, initial-scale=1">',
           f"<title>Evidence: {esc(fid)}</title>", f"<style>{EVIDENCE_STYLE}</style>", "</head>",
           "<body>", "<main>", f"<h1>Evidence: {esc(fid)}</h1>",
           '<p><a href="../index.html">Back to the package index</a></p>',
           "<dl>", f"<dt>Criterion</dt><dd>{esc(f'{sc} {c['name']} (Level {c['level']})')}</dd>",
           f"<dt>Site and page</dt><dd>{esc(finding['site_version'])} / {esc(finding['page'])}: "
           f"{_url_html(ev['page_url'])}</dd>",
           f"<dt>Rule</dt><dd>{esc(finding['rule_id'])} (found by {esc(', '.join(finding['sources']))}; "
           f"scanner status {esc(finding['scanner_status'])})</dd>",
           f"<dt>Route</dt><dd>{esc(str(finding.get('bucket')))}: {esc(finding.get('route_reason') or '')}</dd>",
           "</dl>", "<h2>Human decision</h2>"]
    if decision:
        out += ["<dl>", f"<dt>Decision</dt><dd>{esc(DECISION_WORDS.get(decision['decision'], decision['decision']))}"
                "</dd>", f"<dt>Reviewer</dt><dd>{esc(decision['reviewer'])}</dd>",
                f"<dt>Decided at</dt><dd>{esc(str(decision.get('decided_at') or 'not recorded'))}</dd>",
                f"<dt>Comment</dt><dd>{esc(decision.get('comment') or 'none')}</dd>", "</dl>"]
    else:
        out.append("<p>Pending human review: no reviewer has decided this finding yet.</p>")
    out.append("<h2>Evidence</h2>")
    if shot:
        out.append(f'<p><a href="{esc(shot)}"><img src="{esc(shot)}" alt="Screenshot of the element for {esc(fid)}">'
                   "</a></p>")
    else:
        out.append("<p>No screenshot for this finding; the selector below locates it.</p>")
    out += ["<dl>", f"<dt>Selector</dt><dd><code>{esc(ev['selector'])}</code></dd>", "</dl>",
            "<h3>HTML</h3>", f"<pre><code>{esc(ev['html_snippet'])}</code></pre>"]
    if ev.get("context_text"):
        out += ["<h3>Text around it</h3>", f"<p>{esc(ev['context_text'])}</p>"]
    out += ["<table>", "<caption>Scanner messages</caption>", "<thead><tr>",
            *(f'<th scope="col">{h}</th>' for h in ("Scanner", "Rule", "Type", "Message")), "</tr></thead>", "<tbody>"]
    out += [f"<tr><td>{esc(d['scanner'])}</td><td>{esc(d['rule_id'])}</td><td>{esc(d['type'])}</td>"
            f"<td>{esc(d['message'])}</td></tr>" for d in finding["scanner_details"]]
    out += ["</tbody>", "</table>", *_ai_html(finding["ai"]), "</main>", "</body>", "</html>", ""]
    return "\n".join(out)


SCORE_ROW = re.compile(r"\| (\((?:a|b|c)\) [^|]+?) \| ([^|]+?) \|")
FALSE_ALARMS = re.compile(r"Total on \d of 4 After pages: .+")


def latest_scores(scores_dir=None):
    """The headline from the newest docs/scores/<date>.md (scripts/score.py --write), or None if there is none.

    Returns {"file", "figures": [(label, value)]}: recall (a), (b), (c) and the After false-alarm total.
    A file in a format this doesn't know gives no figures, and the index just links it.
    """
    scores_dir = SCORES_DIR if scores_dir is None else scores_dir
    files = sorted(scores_dir.glob("*.md")) if scores_dir.is_dir() else []
    if not files:
        return None
    text = files[-1].read_text(encoding="utf-8")  # dated names, so the last is the newest
    figures = [m.groups() for m in SCORE_ROW.finditer(text)]
    alarms = FALSE_ALARMS.search(text)
    if alarms:
        figures.append(("After false alarms", alarms.group()))
    return {"file": files[-1].name, "figures": figures}


INDEX_TITLE = "ADJ evidence package: CityLights"
INDEX_LINE = ("Conformance levels and statuses come only from human reviewers' decisions in PULSE. "
              "AI analysis is shown as advice, labelled as AI, and never sets a level.")
SCORES_CAVEAT = "scored separately by scripts/score.py; its runs may not be this package's runs"


def _index_sections(c):
    """The index's link lists as (heading, [(text, href)]), shared by the Markdown and HTML index."""
    acr_counts = ", ".join(f"{n} {level}" for level, n in c["acr_counts"].items())
    return [
        ("Reports", [
            (f"Draft ACR (VPAT 2.5 Rev 508): {acr_counts}", "acr.html"), ("Draft ACR, Markdown", "acr.md"),
            (f"Remediation backlog: {c['backlog_issues']} issues", "backlog.md"),
            ("Remediation backlog, CSV", "backlog.csv"),
            ("Site compliance matrix, Before", "site_matrix_before.md"),
            ("Site compliance matrix, Before, CSV", "site_matrix_before.csv"),
            ("Site compliance matrix, After (false-alarm evidence)", "site_matrix_after.md"),
            ("Site compliance matrix, After, CSV", "site_matrix_after.csv")]),
        ("Per-page compliance matrices", [
            (f"{v} / {p}{suffix}", f"matrix_{v}_{p}.{ext}")
            for (v, p), _ in _ordered_runs(c) for ext, suffix in (("md", ""), ("csv", ", CSV"))]),
        (f"Evidence pages ({len(c['evidence'])} findings in the ACR or backlog)", [
            (f"{fid} ({used_in})", f"evidence/{evidence_filename(fid)}") for fid, used_in in c["evidence"]]),
    ]


def _ordered_runs(c):
    return sorted(c["run_ids"].items(), key=lambda kv: (kv[0][0] != "before", PAGES.index(kv[0][1])))


def format_index_md(c):
    """The package's front page as Markdown.

    c: {"package_id", "report_date", "commit", "run_ids": {(version, page): run_id}, "acr_counts": {level: n},
        "backlog_issues", "evidence": [(finding_id, "ACR, backlog")], "notes", "scores": latest_scores()}
    """
    lines = [f"# {INDEX_TITLE}", "", f"**Product:** {PRODUCT}  ", f"**Package:** {c['package_id']}  ",
             f"**Generated:** {c['report_date']}  ", f"**Commit:** `{c['commit']}`", "", f"**{INDEX_LINE}**", ""]
    for note in c["notes"]:
        lines += [f"> **Warning:** {_md_text(note)}", ""]
    lines += ["## Runs", "", "| Version | Page | Run |", "| --- | --- | --- |",
              *(f"| {v} | {p} | {_md_text(run_id)} |" for (v, p), run_id in _ordered_runs(c)), ""]
    for heading, links in _index_sections(c):
        lines += [f"## {heading}", "", *(f"- [{_md_text(text)}]({href})" for text, href in links), ""]
    lines += ["## Benchmark", ""]
    s = c["scores"]
    if s is None:
        lines += ["No benchmark scores yet (docs/scores/ is empty).", ""]
    else:
        lines += [f"From docs/scores/{s['file']} ({SCORES_CAVEAT}).", ""]
        if s["figures"]:
            lines += ["| Figure | Value |", "| --- | --- |",
                      *(f"| {_md_text(label)} | {_md_text(value)} |" for label, value in s["figures"]), ""]
    return "\n".join(lines)


def format_index_html(c):
    """The package's front page as HTML: the same content as format_index_md."""
    esc = html.escape
    out = ["<!doctype html>", '<html lang="en">', "<head>", '<meta charset="utf-8">',
           '<meta name="viewport" content="width=device-width, initial-scale=1">',
           f"<title>{esc(INDEX_TITLE)}</title>", f"<style>{STYLE}</style>", "</head>", "<body>", "<main>",
           f"<h1>{esc(INDEX_TITLE)}</h1>", f"<p><strong>Product:</strong> {esc(PRODUCT)}<br>",
           f"<strong>Package:</strong> {esc(c['package_id'])}<br>",
           f"<strong>Generated:</strong> {esc(c['report_date'])}<br>",
           f"<strong>Commit:</strong> <code>{esc(c['commit'])}</code></p>",
           f"<p><strong>{esc(INDEX_LINE)}</strong></p>"]
    out += [f'<p class="warning"><strong>Warning:</strong> {esc(note)}</p>' for note in c["notes"]]
    out += ["<table>", "<caption>Runs</caption>", "<thead><tr>",
            *(f'<th scope="col">{h}</th>' for h in ("Version", "Page", "Run")), "</tr></thead>", "<tbody>",
            *(f"<tr><td>{esc(v)}</td><td>{esc(p)}</td><td>{esc(run_id)}</td></tr>"
              for (v, p), run_id in _ordered_runs(c)), "</tbody>", "</table>"]
    for heading, links in _index_sections(c):
        out += [f"<h2>{esc(heading)}</h2>", "<ul>",
                *(f'<li><a href="{esc(href)}">{esc(text)}</a></li>' for text, href in links), "</ul>"]
    out.append("<h2>Benchmark</h2>")
    s = c["scores"]
    if s is None:
        out.append("<p>No benchmark scores yet (docs/scores/ is empty).</p>")
    else:
        out.append(f"<p>From docs/scores/{esc(s['file'])} ({esc(SCORES_CAVEAT)}).</p>")
        if s["figures"]:
            out += ["<table>", "<caption>Benchmark headline</caption>", "<thead><tr>",
                    '<th scope="col">Figure</th><th scope="col">Value</th>', "</tr></thead>", "<tbody>",
                    *(f"<tr><td>{esc(label)}</td><td>{esc(value)}</td></tr>" for label, value in s["figures"]),
                    "</tbody>", "</table>"]
    out += ["</main>", "</body>", "</html>", ""]
    return "\n".join(out)


def _shown_findings(acr_rows, backlog_rows, runs):
    """The findings the package shows, in criterion then page order: {finding_id: (finding, "ACR, backlog")}."""
    uses = {}
    for r in acr_rows:
        for e in r["evidence"]:
            uses.setdefault(e["finding_id"], []).append("ACR")
    for r in backlog_rows:
        for i in r["instances"]:
            uses.setdefault(i["finding"]["finding_id"], []).append("backlog")
    by_id = {f["finding_id"]: f for _, findings in runs for f in findings}
    return {fid: (by_id[fid], ", ".join(dict.fromkeys(u))) for fid, u in uses.items()}


def write_package(run_ids, folder):
    """Write the whole package into folder; return what the index says, for main to print.

    Reads one run per page of both site versions (acr.load_site) and data/reviews/. Everything shown
    comes from those; the ACR and backlog link screenshots copied into folder/shots/.
    """
    b_runs, b_decisions, b_notes, model_ids = acr.load_site(run_ids, "before")
    a_runs, a_decisions, a_notes, _ = acr.load_site(run_ids, "after")
    corpus = load_corpus()
    map508 = matrix._load_map508(corpus)
    acr_rows = acr.build_acr(b_runs, b_decisions, corpus)
    backlog_rows = backlog.build_backlog(b_runs, b_decisions, corpus, map508,
                                         backlog.load_guidance(backlog.GUIDANCE_PATH, corpus))
    shown = _shown_findings(acr_rows, backlog_rows, b_runs)

    copied, shot_notes = copy_shots([f for f, _ in shown.values()], folder)
    # A screenshot that couldn't be copied would be a broken link: show the selector instead. The findings
    # are this process's own copies, and the backlog rows hold the same dicts; ACR evidence holds its own.
    for f, _ in shown.values():
        if f["evidence"]["screenshot"] not in copied:
            f["evidence"]["screenshot"] = None
    for r in acr_rows:
        for e in r["evidence"]:
            if e["screenshot"] not in copied:
                e["screenshot"] = None

    date, notes = acr.report_date(), [*b_notes, *a_notes, *shot_notes]
    before_ids = {s["page"]: s["run_id"] for s, _ in b_runs}
    tools = acr.tool_names(b_runs, acr.npm_versions())
    at_root = shot_href(copied)
    files = {
        "acr.md": acr.format_acr_md(acr_rows, date, [*b_notes, *shot_notes], model_ids, at_root, tools, before_ids),
        "acr.html": acr.format_acr_html(acr_rows, date, [*b_notes, *shot_notes], model_ids, at_root, tools,
                                        before_ids),
        "backlog.md": backlog.format_backlog_md(backlog_rows, date, [*b_notes, *shot_notes], at_root, before_ids),
        "backlog.csv": backlog.format_backlog_csv(backlog_rows),
    }
    for version, runs, decisions, site_notes in (("before", b_runs, b_decisions, b_notes),
                                                  ("after", a_runs, a_decisions, a_notes)):
        rows = build_site_matrix(runs, decisions, corpus, map508)
        files[f"site_matrix_{version}.csv"] = format_site_matrix_csv(rows)
        files[f"site_matrix_{version}.md"] = format_site_matrix_md(rows, f"Site compliance matrix: {version}",
                                                                   site_notes)
        for summary, findings in runs:
            page_rows = matrix.build_matrix(findings, decisions[summary["page"]], corpus, map508)
            name = f"matrix_{version}_{summary['page']}"
            stub_note = matrix._stub_note(summary)
            matrix.write_csv(page_rows, folder / f"{name}.csv")
            matrix.write_markdown(page_rows, folder / f"{name}.md",
                                  f"Compliance matrix: {version} / {summary['page']} (run {summary['run_id']})",
                                  [stub_note] if stub_note else [])
    in_evidence = shot_href(copied, depth=1)
    for fid, (f, _) in shown.items():
        shot = in_evidence(f["evidence"]["screenshot"]) if f["evidence"]["screenshot"] else None
        files[f"evidence/{evidence_filename(fid)}"] = format_evidence_html(
            f, b_decisions[f["page"]].get(fid), corpus, shot)

    contents = {"package_id": folder.name.removeprefix(".").removesuffix(".partial"), "report_date": date,
                "commit": git_commit(),
                "run_ids": {(s["version"], s["page"]): s["run_id"] for s, _ in [*b_runs, *a_runs]},
                "acr_counts": dict(Counter(r["conformance"] for r in acr_rows).most_common()),
                "backlog_issues": len(backlog_rows), "evidence": [(fid, u) for fid, (_, u) in shown.items()],
                "notes": notes, "scores": latest_scores()}
    files["index.md"] = format_index_md(contents)
    files["index.html"] = format_index_html(contents)
    for name, text in files.items():
        write_atomic(folder / name, text)
    return contents


def main(argv=None):
    p = argparse.ArgumentParser(description="Build the ADJ evidence package: site matrices, draft ACR, backlog "
                                            "and evidence pages")
    p.add_argument("--runs", required=True, nargs="+", metavar="RUN_ID",
                   help="folder names in data/runs/: one run per page of both the Before and After site (8)")
    args = p.parse_args(argv)

    package_id = new_run_id()
    final = matrix.REPORTS_DIR / package_id
    # Built under a hidden name and renamed when complete, so a failure never leaves half a package
    partial = matrix.REPORTS_DIR / f".{package_id}.partial"
    try:
        partial.mkdir(parents=True)
        contents = write_package(args.runs, partial)
        os.replace(partial, final)
    except (PackageError, acr.AcrError, backlog.BacklogError, matrix.MatrixInputError, FindingsFileError,
            CorpusError, OSError) as e:
        shutil.rmtree(partial, ignore_errors=True)
        print(f"ERROR {e}", file=sys.stderr)
        return 1

    for note in contents["notes"]:
        print(f"WARNING {note}", file=sys.stderr)
    print(", ".join(f"{n} {level}" for level, n in contents["acr_counts"].items()) + " (ACR); "
          f"{contents['backlog_issues']} backlog issues; {len(contents['evidence'])} evidence pages")
    print(f"Wrote {final / 'index.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
