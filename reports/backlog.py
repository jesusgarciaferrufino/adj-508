"""Track E remediation backlog: a ranked list of accessibility issues on the Before site, with fix guidance.

What goes on it: findings a human decided "fail", plus undecided findings that agents.evidence.is_issue
calls an issue. A human "pass" or "not_applicable" keeps a finding off, whatever a scanner or the AI says.
Each finding's status says who said so; only "Confirmed (human)" is a human decision.

Ranking rule (rank_issues), applied in this order:
  1. status: Confirmed (human), then Suspected (machine), then Suspected (AI, verified citations)
  2. WCAG level: A before AA
  3. more pages affected first
  4. more instances first
  5. ties: criterion number (2.1.1 before 2.4.4), then rule_id, so the order is the same every run

Run it as a module from the repo root:  python -m reports.backlog --runs <run ids>
"""

import argparse
import csv
import io
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import quote

from agents.evidence import is_issue
from rag.corpus_tools import CorpusError, load_corpus
from reports import acr, matrix
from reports.acr import _md_code, _md_text
from reports.matrix import _sc_order
from scanners.findings import REPO, FindingsFileError

GUIDANCE_PATH = REPO / "reports" / "fix_guidance.json"
UNDERSTANDING_BASE = "https://www.w3.org/TR/UNDERSTANDING-WCAG20/"

STATUS_CONFIRMED = "Confirmed (human)"
STATUS_MACHINE = "Suspected (machine)"
STATUS_AI = "Suspected (AI, verified citations)"


def backlog_status(finding, decisions):
    """A finding's backlog status, or None when it doesn't belong on the backlog.

    decisions: {finding_id: decision line} for the finding's page, from matrix.load_decisions.
    A human decision comes first; an undecided finding counts only if is_issue says so (a machine
    finding, or a grey one the AI calls likely_fail with verified citations).
    """
    d = decisions.get(finding["finding_id"])
    if d:
        return STATUS_CONFIRMED if d["decision"] == "fail" else None
    if not is_issue(finding):
        return None
    return STATUS_MACHINE if finding["bucket"] == "machine" else STATUS_AI


# Strongest first: an issue takes the status of its strongest instance, and ranks by it
STATUS_ORDER = (STATUS_CONFIRMED, STATUS_MACHINE, STATUS_AI)


def group_issues(runs, decisions_by_page):
    """The backlog's issues, unranked: one per (criterion, rule_id) across all pages, in first-seen order.

    runs               (summary, findings) per Before page, from matrix.load_run (or acr.load_before_site)
    decisions_by_page  {page: {finding_id: decision line}}

    Issue: sc, rule_id, status (its strongest instance's), pages (each once, in run order), instances
    ({"finding", "page", "status"} for every finding on the backlog). The same rule on two criteria is
    two issues: each fails a different requirement.
    """
    issues = {}
    for summary, findings in runs:
        page = summary["page"]
        for f in findings:
            status = backlog_status(f, decisions_by_page.get(page, {}))
            if status is None:
                continue
            issue = issues.setdefault((f["wcag"][0], f["rule_id"]),
                                      {"sc": f["wcag"][0], "rule_id": f["rule_id"], "pages": [], "instances": []})
            if page not in issue["pages"]:
                issue["pages"].append(page)
            issue["instances"].append({"finding": f, "page": page, "status": status})
    for issue in issues.values():
        issue["status"] = min((i["status"] for i in issue["instances"]), key=STATUS_ORDER.index)
    return list(issues.values())


def rank_issues(issues, corpus):
    """The issues in backlog order (ranking rule at the top of this file), each a copy with "rank" from 1.

    corpus: rag.corpus_tools.load_corpus(), for each criterion's level. The input list isn't changed.
    """
    def key(issue):
        return (STATUS_ORDER.index(issue["status"]), corpus[issue["sc"]]["level"] != "A",
                -len(issue["pages"]), -len(issue["instances"]), _sc_order(issue["sc"]), issue["rule_id"])
    return [{**issue, "rank": n} for n, issue in enumerate(sorted(issues, key=key), start=1)]


class BacklogError(ValueError):
    """The backlog can't be built, e.g. fix_guidance.json is missing or broken."""


GUIDANCE_FIELDS = ("sc", "issue", "fix", "technique", "url")
W3C = "https://www.w3.org/"
TECHNIQUE_ID = re.compile(r"(F|G|H|C|SCR|ARIA)\d+")  # W3C's WCAG 2.0 technique and failure IDs


def _guidance_problem(key, entry, corpus):
    """What's wrong with one fix_guidance.json entry, or None when it's fine."""
    if not isinstance(entry, dict) or set(entry) != set(GUIDANCE_FIELDS):
        return f"must be an object with exactly these fields: {', '.join(GUIDANCE_FIELDS)}"
    if not all(isinstance(entry[field], str) and entry[field].strip() for field in GUIDANCE_FIELDS):
        return "every field must be non-empty text"
    sc, technique = entry["sc"], entry["technique"]
    if sc not in corpus:
        return f"{sc!r} is not a WCAG 2.0 A/AA criterion"
    if key in corpus and key != sc:
        return f"a criterion key must name its own criterion, not {sc}"
    if not TECHNIQUE_ID.fullmatch(technique):
        return f"{technique!r} is not a W3C technique or failure ID"
    if technique.startswith("F") and technique not in corpus[sc]["common_failures"]:
        return f"{technique} is not a failure rag/wcag20_aa.json lists for {sc}"  # no invented failures
    if not (entry["url"].startswith(W3C) and entry["url"].endswith(f"/{technique}.html")):
        return f"url must be W3C's page for {technique} ({W3C}.../{technique}.html), not {entry['url']!r}"
    return None


def load_guidance(path=GUIDANCE_PATH, corpus=None):
    """The written fix guidance: {rule_id or sc: {"sc", "issue", "fix", "technique", "url"}}.

    corpus: rag.corpus_tools.load_corpus(), read here when not given. Raises BacklogError naming the
    file when it's missing, unreadable or not a JSON object, and naming the entry when one is broken,
    so a bad edit stops the backlog with a clear message instead of a traceback halfway through.
    """
    if not path.exists():
        raise BacklogError(f"{path} not found")
    try:
        guidance = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:  # unreadable, not UTF-8, or not JSON
        raise BacklogError(f"cannot read {path}: {e}") from None
    if not isinstance(guidance, dict):
        raise BacklogError(f"{path} must hold a JSON object keyed by rule_id or criterion")
    corpus = load_corpus() if corpus is None else corpus
    for key, entry in guidance.items():
        problem = _guidance_problem(key, entry, corpus)
        if problem:
            raise BacklogError(f"{path} entry {key!r}: {problem}")
    return guidance


def understanding_url(sc, corpus):
    """W3C's Understanding page for a criterion, from the anchor of its spec link in the corpus."""
    return UNDERSTANDING_BASE + corpus[sc]["source"].split("#")[1] + ".html"


def guidance_for(issue, guidance, corpus):
    """How to fix an issue: {"issue", "fix", "technique", "url", "written"}.

    Written guidance for the issue's rule_id comes first, then for its criterion. Without either, the
    fix points at the criterion's Understanding page (cut-list item 5) and "written" is False.

    One rule can fire on several criteria (blur-on-focus on four), but its entry is written for one. It is
    used on another criterion only when its W3C failure is one rag/wcag20_aa.json lists for that criterion
    too, so a row never cites a technique for a different requirement than the one it names.
    """
    entry = guidance.get(issue["rule_id"])
    if entry and not (entry["sc"] == issue["sc"] or entry["technique"] in corpus[issue["sc"]]["common_failures"]):
        entry = None
    entry = entry or guidance.get(issue["sc"])
    if entry:
        return {"issue": entry["issue"], "fix": entry["fix"], "technique": entry["technique"],
                "url": entry["url"], "written": True}
    name = corpus[issue["sc"]]["name"]
    return {"issue": name, "technique": None, "written": False,
            "fix": f"See W3C's Understanding page for {issue['sc']} {name}.",
            "url": understanding_url(issue["sc"], corpus)}


def build_backlog(runs, decisions_by_page, corpus, map508, guidance):
    """The backlog's rows, ranked: each issue from rank_issues plus its criterion, 508 reference and fix.

    runs, decisions_by_page  as for group_issues
    corpus                   rag.corpus_tools.load_corpus()
    map508                   rag/section508_map.json, via matrix._load_map508
    guidance                 load_guidance()
    """
    rows = []
    for issue in rank_issues(group_issues(runs, decisions_by_page), corpus):
        c = corpus[issue["sc"]]
        rows.append({**issue, **guidance_for(issue, guidance, corpus), "name": c["name"], "level": c["level"],
                     "section508": map508[issue["sc"]]["web"]})
    return rows


MD_COLUMNS = ("Rank", "Issue", "Criterion", "Status", "Pages", "Instances", "How to fix", "Evidence")
CSV_COLUMNS = ("rank", "issue", "sc", "criterion_name", "level", "section508", "status", "pages", "instances",
               "how_to_fix", "technique", "technique_url", "evidence")
MD_INSTANCES = 5  # evidence shown per issue in backlog.md; backlog.csv lists every instance

TITLE = "Remediation backlog (draft)"
STATUS_LINE = ("Status says who said so. Only Confirmed (human) is a human decision; Suspected issues come from "
               "a scanner rule or from AI analysis with verified citations, and a human may still clear them.")


def _criterion_text(r):
    return f"{r['sc']} {r['name']} (Level {r['level']}, 508 {r['section508']})"


def _where(instance):
    """An instance's screenshot path, or its selector when it has none (e.g. <title>)."""
    evidence = instance["finding"]["evidence"]
    return evidence["screenshot"] or evidence["selector"]


def _status_text(r):
    """The issue's status, split by instance when they differ ("Confirmed (human): 1 of 122; ...").

    An issue ranks by its strongest instance, but one human fail must not read as a human
    decision on all of them, so a mixed issue says how many instances have each status.
    """
    counts = Counter(i["status"] for i in r["instances"])
    if len(counts) == 1:
        return r["status"]
    return "; ".join(f"{s}: {counts[s]} of {len(r['instances'])}" for s in STATUS_ORDER if s in counts)


def _strongest_first(instances):
    """Instances with human-confirmed ones first, then machine, then AI; page order kept within each."""
    return sorted(instances, key=lambda i: STATUS_ORDER.index(i["status"]))


def _evidence_md(instances, shot_href):
    """The first MD_INSTANCES instances, strongest first, each with its page and screenshot link (or
    selector). When the issue's instances differ in status, each one names its own."""
    mixed = len({i["status"] for i in instances}) > 1
    items = []
    for i in _strongest_first(instances)[:MD_INSTANCES]:
        finding_id, screenshot = i["finding"]["finding_id"], i["finding"]["evidence"]["screenshot"]
        if screenshot:
            where = f"[screenshot of {_md_text(finding_id)}]({quote(shot_href(screenshot), safe='/._-~')})"
        else:
            where = _md_code(i["finding"]["evidence"]["selector"])
        about = f"{finding_id} ({i['page']}, {i['status']}," if mixed else f"{finding_id} ({i['page']},"
        items.append(f"{_md_text(about)} {where})")
    more = len(instances) - MD_INSTANCES
    return "; ".join(items) + (f"; and {more} more in backlog.csv" if more > 0 else "")


def _fix_md(r):
    """The fix text and its W3C link: the cited technique, or the Understanding page for fallback guidance."""
    label = f"W3C {r['technique']}" if r["written"] else f"W3C Understanding {r['sc']}"
    # load_guidance has checked both, but a ] in the label or a ) | or space in the url would still break
    # the link or the table row, so they are escaped here like every other cell
    return f"{_md_text(r['fix'])} [{_md_text(label).replace(']', '\\]')}]({quote(r['url'], safe=':/._-~#')})"


def format_backlog_md(rows, report_date, notes=(), shot_href=lambda path: path, run_ids=None):
    """The backlog as Markdown: a header saying what the statuses mean, then one table row per issue.

    notes, run_ids and shot_href as for acr.format_acr_md.
    """
    lines = [f"# {TITLE}", "", f"**Product:** {acr.PRODUCT}  ", f"**Report date:** {report_date}  ",
             *([f"**Runs:** {acr._runs_text(run_ids)}  "] if run_ids else []), "",
             STATUS_LINE, "", "Ranked by status, then WCAG level (A before AA), then pages affected, then instances.",
             ""]
    for note in notes:
        lines += [f"> **Warning:** {_md_text(note)}", ""]
    if not rows:
        return "\n".join(lines + ["Nothing is on the backlog: no human-confirmed fails and no suspected issues.", ""])
    lines += ["| " + " | ".join(MD_COLUMNS) + " |", "|" + " --- |" * len(MD_COLUMNS)]
    for r in rows:
        cells = (str(r["rank"]), _md_text(r["issue"]), _md_text(_criterion_text(r)), _md_text(_status_text(r)),
                 _md_text(", ".join(r["pages"])), str(len(r["instances"])), _fix_md(r),
                 _evidence_md(r["instances"], shot_href))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines + [""])


def format_backlog_csv(rows):
    """The backlog as CSV, one line per issue, with every instance and its own status in the evidence column."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for r in rows:
        writer.writerow({
            "rank": r["rank"], "issue": r["issue"], "sc": r["sc"], "criterion_name": r["name"],
            "level": r["level"], "section508": r["section508"], "status": _status_text(r),
            "pages": ", ".join(r["pages"]), "instances": len(r["instances"]), "how_to_fix": r["fix"],
            "technique": r["technique"] or "", "technique_url": r["url"],
            "evidence": "; ".join(f"{i['finding']['finding_id']} ({i['page']}, {i['status']}): {_where(i)}"
                                  for i in _strongest_first(r["instances"])),
        })
    return buf.getvalue()


def main(argv=None):
    p = argparse.ArgumentParser(description="Build the remediation backlog for the Before site")
    p.add_argument("--runs", required=True, nargs="+", metavar="RUN_ID",
                   help="folder names in data/runs/: one Before run per page (After runs are ignored)")
    p.add_argument("--out", metavar="DIR",
                   help="where to write backlog.md and backlog.csv (default: data/reports/backlog)")
    args = p.parse_args(argv)

    try:
        runs, decisions, notes, _ = acr.load_before_site(args.runs)
        corpus = load_corpus()
        rows = build_backlog(runs, decisions, corpus, matrix._load_map508(corpus),
                              load_guidance(GUIDANCE_PATH, corpus))
    except (BacklogError, acr.AcrError, matrix.MatrixInputError, FindingsFileError, CorpusError) as e:
        print(f"ERROR {e}", file=sys.stderr)
        return 1

    out = matrix.REPORTS_DIR / "backlog" if args.out is None else Path(args.out)

    def shot_href(path):  # screenshots stay in data/findings/shots/; link to them from wherever the file is saved
        return os.path.relpath(REPO / path, out)

    run_ids = {summary["page"]: summary["run_id"] for summary, _ in runs}
    md = format_backlog_md(rows, acr.report_date(), notes, shot_href=shot_href, run_ids=run_ids)
    try:
        acr._write_together({out / "backlog.md": md, out / "backlog.csv": format_backlog_csv(rows)})
    except OSError as e:
        print(f"ERROR could not write the backlog: {e}", file=sys.stderr)
        return 1

    for note in notes:
        print(f"WARNING {note}", file=sys.stderr)
    print(f"{len(rows)} issues: " + (", ".join(f"{n} {s}" for s, n in Counter(r["status"] for r in rows).items())
                                     or "none"))
    print(f"Wrote {out / 'backlog.md'}\n      {out / 'backlog.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
