"""Track E compliance matrix: one row per WCAG 2.0 A/AA criterion for one page of a pipeline run.

    python -m reports.matrix --run <run_id>

Run it as a module from the repo root; `python reports/matrix.py` can't import agents/, rag/ or scanners/.

Reads:
    data/runs/{run_id}/summary.json            which page the run covers, and any stub nodes
    data/runs/{run_id}/output_findings.json    the run's findings (bucket, evidence, ai, ...)
    data/reviews/{version}_{page}.jsonl        human decisions from PULSE (Track F); the last line per finding wins
    rag/wcag20_aa.json, rag/section508_map.json  name, level and Section 508 reference for all 38 criteria

Writes data/reports/{run_id}/matrix_{version}_{page}.csv and .md.

Hard rule (CONTEXT.md): AI never sets a conformance status. `status` comes ONLY from human decisions,
never from bucket, scanner_status or ai.recommendation. AI leaning is shown in its own column, labelled as AI.
"""

import argparse
import csv
import io
import json
import sys
from collections import Counter

from agents import runlog
from agents.evidence import grey_is_issue
from rag import corpus_tools
from rag.corpus_tools import CorpusError, load_corpus, write_atomic
from scanners.findings import REPO, FindingsFileError, validate_findings

RUNS_DIR = runlog.RUNS_DIR  # where pipeline runs live; tests point this at a temp folder
REVIEWS_DIR = REPO / "data" / "reviews"
REPORTS_DIR = REPO / "data" / "reports"

# A finding's human decision: the only values PULSE may record
DECISIONS = {"fail", "pass", "not_applicable"}

# A criterion's status, worded exactly as in the Track E handoff. The Week 5 ACR reads these.
STATUS_FAIL = "Does Not Support (human-confirmed)"
STATUS_SUPPORTS = "Supports (human-confirmed)"
STATUS_NOT_APPLICABLE = "Not Applicable (human-confirmed)"  # agreed with Jesus Oct 16 (VPAT term)
STATUS_PENDING = "Pending human review"
STATUS_NO_FINDINGS = "No automated findings; human check required"

# The AI's leaning on a grey finding (Track C's ai.recommendation). "likely_fail" counts only when the
# citations were verified (the issue rule, agents.evidence.grey_is_issue); a likely_fail with unverified
# citations is "unverified_fail" and goes to a human. "none" = a grey finding the AI never evaluated:
# skipped (recommendation None, case_for "SKIPPED: ..."; older runs wrote "unsure"), a stub, or a failed
# call. Each is counted on its own, never folded into "unsure".
AI_LEANINGS = ("likely_fail", "unverified_fail", "likely_pass", "unsure", "none")

# Track C's router buckets -> the matrix column that counts them
BUCKET_COLUMNS = {"machine": "machine", "grey": "grey", "human-only": "human_checks"}

# The matrix's columns, in order (the handoff's list, plus "test": which checks flagged the criterion).
# Nothing reads the CSV back by these names today; the ACR and package call this module's functions.
COLUMNS = ("section508", "sc", "name", "level", "test", "status", "machine", "grey", "human_checks",
           "finding_ids", "evidence", "ai_leaning", "reviewed_by")

# The "test" column's names for each check, in report order (short forms of reports.acr.TOOL_LABELS).
# ADJ's checks all have scanner adj-rules; the rule_id "adj.{check}.{rule}" tells them apart. A Pa11y
# notice is HTML_CodeSniffer asking a person to check something, not a test result, so it gets its own label.
TEST_LABELS = {"axe-core": "axe-core", "pa11y": "Pa11y", "pa11y-notice": "Pa11y reminder (manual check)",
               "adj.rules": "ADJ rules", "adj.validator": "Nu HTML Checker", "adj.compare": "ADJ page comparison",
               "adj-vision": "AI on screenshots"}
NO_TEST = STATUS_NO_FINDINGS  # the column only knows checks that produced findings, so it says what the status says
TEST_NOTE = ("The `test` column names the scanner checks behind the criterion's findings; it does not list "
             "checks that ran and found nothing. A Pa11y reminder asks for a manual check; it is not a test result.")


class MatrixInputError(ValueError):
    """A run or reviews file is missing or broken. main() prints it and exits 1."""


def _read_json(path):
    """Parse one JSON file, or raise MatrixInputError naming it."""
    if not path.exists():
        raise MatrixInputError(f"{path} not found")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:  # unreadable, not UTF-8, or not JSON
        raise MatrixInputError(f"cannot read {path}: {e}") from None


def load_run(run_id):
    """Read one pipeline run. Returns (summary dict, list of finding dicts).

    Findings are checked against docs/finding.example.json's format; fields later
    tracks added (bucket, mapping, ai, ...) are kept.
    """
    run = RUNS_DIR / run_id
    if not run.is_dir():
        raise MatrixInputError(f"run {run_id!r} not found in {RUNS_DIR}")
    summary = _read_json(run / "summary.json")
    if not isinstance(summary, dict) or not {"version", "page"} <= summary.keys():
        raise MatrixInputError(f"{run / 'summary.json'} must have 'version' and 'page'")
    rows = _read_json(run / "output_findings.json")
    findings = validate_findings(rows, str(run / "output_findings.json"))
    return summary, [f.model_dump() for f in findings]


def _is_decision(line):
    """True for a line PULSE would count. Same rule as _is_decision_record in pulse/api.py,
    so the matrix and the extension always agree on a finding's decision."""
    return (isinstance(line, dict)
            and all(isinstance(line.get(k), str) and line[k]
                    for k in ("finding_id", "run_id", "reviewer", "decided_at"))
            and isinstance(line.get("comment", ""), str)
            and line.get("decision") in DECISIONS)


def load_decisions(version, page):
    """Read the human decisions for one page. Returns ({finding_id: decision line}, skipped).

    The file is append-only, so a finding decided twice keeps its last line. Decisions from
    any run count: finding IDs don't change between runs. No file yet means nothing decided.

    A line PULSE wouldn't count (half-written by a crash, not JSON, missing fields) is skipped,
    as PULSE skips it, but never silently: `skipped` lists "<file> line N: reason" for each one,
    and main() prints them and puts them in the report, since a lost "fail" would show as Pending.
    """
    path = REVIEWS_DIR / f"{version}_{page}.jsonl"
    if not path.exists():
        return {}, []
    decisions, skipped = {}, []
    # Split the bytes on "\n" only, like PULSE: splitlines() also breaks on characters such as U+2028
    for n, raw in enumerate(path.read_bytes().split(b"\n"), start=1):
        if not raw.strip():
            continue  # a blank line holds no decision
        try:
            line = json.loads(raw)
            json.dumps(line, ensure_ascii=False).encode("utf-8")  # PULSE also drops text it can't send (half an emoji)
        except (ValueError, RecursionError):  # not JSON, not UTF-8, or absurdly nested
            skipped.append(f"{path} line {n}: not readable JSON")
            continue
        if not _is_decision(line):
            skipped.append(f"{path} line {n}: not a decision PULSE would count "
                           f"(needs finding_id, run_id, reviewer, decided_at and a decision in {sorted(DECISIONS)})")
            continue
        decisions[line["finding_id"]] = line
    return decisions, skipped


def criterion_status(finding_ids, decisions):
    """One criterion's status, from human decisions only.

    Takes finding IDs, not findings, so it can't see bucket, scanner_status or ai: the hard rule
    holds by construction. Order matters: one human "fail" beats any number of undecided findings.
    """
    if not finding_ids:
        return STATUS_NO_FINDINGS
    decided = [decisions[fid]["decision"] for fid in finding_ids if fid in decisions]
    if "fail" in decided:
        return STATUS_FAIL
    if len(decided) < len(finding_ids):
        return STATUS_PENDING
    # Every finding is decided pass or not_applicable. All not_applicable (e.g. "page has no video")
    # is its own status; any pass among them means the criterion applies and is met.
    if all(d == "not_applicable" for d in decided):
        return STATUS_NOT_APPLICABLE
    return STATUS_SUPPORTS


def _evidence(finding, shot_text=lambda path: path):
    """Where a reviewer can see one finding: its selector and, when there is one, its screenshot.

    A finding on the <html> element is about the whole page (e.g. 3.1.1 page language), so it is
    labelled that way instead of showing a bare "html". shot_text turns the screenshot path into
    what to print; it returns None when the screenshot isn't where this matrix is read (the evidence
    package holds only the screenshots it shows), and then only the selector is printed.
    """
    ev = finding["evidence"]
    where = "page-level (whole page)" if ev["selector"] == "html" else ev["selector"]
    shot = shot_text(ev["screenshot"]) if ev["screenshot"] else None  # None: e.g. <title>, nothing on screen
    return f"{where} (screenshot: {shot})" if shot else where


def _leaning(finding):
    """One grey finding's AI leaning; "none" when the AI never evaluated it."""
    ai = finding["ai"]
    if str(ai.get("case_for") or "").startswith("SKIPPED"):  # contract in CONTEXT.md (Track C, #18)
        return "none"
    if ai.get("recommendation") == "likely_fail" and not grey_is_issue(ai):
        return "unverified_fail"
    return ai.get("recommendation") or "none"


def _ai_leaning(findings):
    """What the AI leans towards for a criterion's findings, as counts. Advice only, never a status.

    Starts with "AI:" so nobody mistakes it for a human decision. A value Track C adds later that
    isn't in AI_LEANINGS is still shown under its own name, not hidden in another count.
    Only grey findings count: the AI builds FOR/AGAINST cases for nothing else.
    """
    grey = [f for f in findings if f["bucket"] == "grey"]
    if not grey:
        return ""
    counts = Counter(_leaning(f) for f in grey)
    names = list(AI_LEANINGS) + sorted(set(counts) - set(AI_LEANINGS))
    return "AI: " + ", ".join(f"{name} {counts[name]}" for name in names)


def _check_and_rule(detail):
    """One scanner_details entry as (check, short rule ID): ("pa11y", "H37"), ("adj.rules", "no-skip-link").

    The row already names the criterion, so a Pa11y ID loses its "WCAG2AA.PrincipleN.GuidelineN_N.N_N_N."
    part, and an ADJ ID its "adj.{check}." part. An ID in another form is kept whole, under "ADJ rules"
    for adj-rules. A Pa11y notice is the check "pa11y-notice".
    """
    scanner, rule = detail["scanner"], detail["rule_id"]
    if scanner in ("adj-rules", "adj-vision"):
        parts = rule.split(".", 2)
        if len(parts) == 3 and parts[0] == "adj":
            return ("adj-vision" if scanner == "adj-vision" else f"adj.{parts[1]}"), parts[2]
        return ("adj-vision" if scanner == "adj-vision" else "adj.rules"), rule
    if scanner == "pa11y":
        check = "pa11y-notice" if detail.get("type") == "notice" else "pa11y"
        parts = rule.split(".", 4)
        return check, parts[4] if len(parts) == 5 and parts[0].startswith("WCAG2") else rule
    return scanner, rule


def criterion_tests(findings):
    """Which checks flagged a criterion's findings, e.g. "axe-core: image-alt; Pa11y: H37 / H30.2".

    Checks come in TEST_LABELS order, then any this code doesn't know, by name, so none is hidden.
    Each check's rules come busiest first, ties A-Z, separated by " / " because HTML_CodeSniffer IDs
    like G73,G74 have commas of their own. With no findings: NO_TEST.
    """
    counts = Counter(_check_and_rule(d) for f in findings for d in f["scanner_details"])
    if not counts:
        return NO_TEST
    known = [c for c in TEST_LABELS if any(check == c for check, _ in counts)]
    unknown = sorted({check for check, _ in counts} - set(TEST_LABELS))
    parts = []
    for check in known + unknown:
        rules = sorted((rule for c, rule in counts if c == check), key=lambda r: (-counts[check, r], r))
        label = TEST_LABELS.get(check) or (f"ADJ check {check[4:]}" if check.startswith("adj.") else check)
        parts.append(f"{label}: {' / '.join(rules)}")
    return "; ".join(parts)


def _sc_order(sc):
    """Sort key so "1.4.10" would come after "1.4.9" (plain text order puts it first)."""
    return tuple(int(part) for part in sc.split("."))


def build_matrix(findings, decisions, corpus, map508, shot_text=lambda path: path):
    """One row per WCAG 2.0 A/AA criterion (38), in criterion order. Touches no files.

    findings   from load_run; grouped by wcag[0] (Track A's criterion), not mapping.sc
    decisions  from load_decisions; only these set a row's status
    corpus     from rag.corpus_tools.load_corpus(): {sc: {"name", "level", ...}}
    map508     rag/section508_map.json: {sc: {"web": "E205.4", ...}}
    shot_text  how the evidence column names a screenshot (_evidence); the repo path by default
    """
    by_sc = {}
    for f in findings:
        by_sc.setdefault(f["wcag"][0], []).append(f)
        if f["bucket"] not in BUCKET_COLUMNS:
            print(f"WARNING {f['finding_id']}: unknown bucket {f['bucket']!r}, not counted in any bucket column",
                  file=sys.stderr)

    rows = []
    for sc in sorted(corpus, key=_sc_order):
        group = by_sc.get(sc, [])
        ids = [f["finding_id"] for f in group]
        buckets = Counter(BUCKET_COLUMNS.get(f["bucket"]) for f in group)
        rows.append({
            "section508": map508[sc]["web"],
            "sc": sc,
            "name": corpus[sc]["name"],
            "level": corpus[sc]["level"],
            "test": criterion_tests(group),
            "status": criterion_status(ids, decisions),
            "machine": buckets["machine"],
            "grey": buckets["grey"],
            "human_checks": buckets["human_checks"],
            "finding_ids": "; ".join(ids),
            "evidence": "; ".join(_evidence(f, shot_text) for f in group),  # same order as finding_ids
            "ai_leaning": _ai_leaning(group),
            "reviewed_by": ", ".join(sorted({decisions[i]["reviewer"] for i in ids if i in decisions})),
        })
    return rows


def write_csv(rows, path):
    """Write the rows as CSV, header first. The csv module quotes cells holding commas or quotes
    (selectors often do); a row with a missing or extra column raises instead of shifting cells."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    write_atomic(path, buf.getvalue())


def _md_cell(value):
    """One Markdown table cell. "|" would end the cell early (selectors like [lang|=en] have it),
    and a line break would end the row."""
    return str(value).replace("|", "\\|").replace("\n", " ")


def _md_scanner_text(value):
    """A cell that holds text from scanners (test, evidence): backslashes are doubled first, so a \\| in it
    can't turn _md_cell's pipe escape back into a cell border."""
    return _md_cell(str(value).replace("\\", "\\\\"))


def write_markdown(rows, path, title, notes=()):
    """Write the rows as a Markdown table for people to read.

    notes: warnings to show above the table, e.g. that the run used stand-ins, or that reviews
    lines were skipped. A matrix with something wrong under it must say so on its face.
    """
    lines = [f"# {title}", ""]
    for note in notes:
        lines += [f"> **Warning:** {note}", ""]  # blank line between, or Markdown joins them into one paragraph
    lines += ["Status comes only from human reviewers' decisions. The `ai_leaning` column is the AI's advice "
              "on grey-area findings, not a status. `unverified_fail` means the AI leaned fail but its quotes "
              "couldn't be checked against the evidence, so a human decides. " + TEST_NOTE, ""]
    lines.append("| " + " | ".join(COLUMNS) + " |")
    lines.append("|" + " --- |" * len(COLUMNS))
    lines += ["| " + " | ".join((_md_scanner_text if c in ("test", "evidence") else _md_cell)(row[c])
                                for c in COLUMNS) + " |" for row in rows]
    write_atomic(path, "\n".join(lines) + "\n")


def _load_map508(corpus):
    """rag/section508_map.json, checked to have a "web" reference for every criterion."""
    map508 = _read_json(corpus_tools.MAP508)
    missing = [sc for sc in sorted(corpus, key=_sc_order)
               if not isinstance(map508, dict) or not isinstance(map508.get(sc), dict) or "web" not in map508[sc]]
    if missing:
        raise MatrixInputError(f"{corpus_tools.MAP508} has no web reference for {missing[:5]}; "
                               "run python -m rag.corpus_tools map508")
    return map508


def _stub_note(summary):
    """Warning for a run whose router or evidence step was a stand-in, or None for a real run."""
    if "stubs" not in summary:  # runs before stub tracking: can't tell, so don't claim they were real
        return "This run predates stub tracking; bucket counts and AI leaning may be placeholders."
    if summary["stubs"]:
        return (f"Run used stand-ins for: {', '.join(summary['stubs'])}. "
                "Bucket counts and AI leaning are placeholders.")
    return None


def main(argv=None):
    p = argparse.ArgumentParser(description="Build the Track E compliance matrix for one pipeline run")
    p.add_argument("--run", required=True, metavar="RUN_ID", help="a folder name in data/runs/")
    args = p.parse_args(argv)

    try:
        summary, findings = load_run(args.run)
        decisions, skipped = load_decisions(summary["version"], summary["page"])
        corpus = load_corpus()
        map508 = _load_map508(corpus)
    except (MatrixInputError, FindingsFileError, CorpusError) as e:
        print(f"ERROR {e}", file=sys.stderr)
        return 1

    stub_note = _stub_note(summary)
    notes = [stub_note] if stub_note else []
    for s in skipped:
        print(f"WARNING skipped reviews {s}", file=sys.stderr)
        notes.append(f"Skipped reviews {s}. Its decision is not counted; the criterion may show Pending.")

    rows = build_matrix(findings, decisions, corpus, map508)
    version, page = summary["version"], summary["page"]
    out = REPORTS_DIR / args.run
    csv_path, md_path = out / f"matrix_{version}_{page}.csv", out / f"matrix_{version}_{page}.md"
    try:
        write_csv(rows, csv_path)
        write_markdown(rows, md_path, f"Compliance matrix: {version} / {page} (run {args.run})", notes)
    except OSError as e:
        print(f"ERROR could not write the matrix: {e}", file=sys.stderr)
        return 1

    counts = Counter(row["status"] for row in rows)
    print(", ".join(f"{n} {status}" for status, n in counts.most_common()))
    if stub_note:
        print(f"Note: {stub_note}")
    print(f"Wrote {csv_path}\n      {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
