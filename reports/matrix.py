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

import json

from agents import runlog
from scanners.findings import REPO, validate_findings

RUNS_DIR = runlog.RUNS_DIR  # where pipeline runs live; tests point this at a temp folder
REVIEWS_DIR = REPO / "data" / "reviews"
REPORTS_DIR = REPO / "data" / "reports"

# A finding's human decision: the only values PULSE may record
DECISIONS = {"fail", "pass", "not_applicable"}

# A criterion's status, worded exactly as in the Track E handoff. The Week 5 ACR reads these.
STATUS_FAIL = "Does Not Support (human-confirmed)"
STATUS_SUPPORTS = "Supports (human-confirmed)"
STATUS_PENDING = "Pending human review"
STATUS_NO_FINDINGS = "No automated findings; human check required"


class MatrixInputError(ValueError):
    """A run or reviews file is missing or broken. main() prints it and exits 1."""


def _read_json(path):
    """Parse one JSON file, or raise MatrixInputError naming it."""
    if not path.exists():
        raise MatrixInputError(f"{path} not found")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise MatrixInputError(f"{path} is not valid JSON: {e}") from None


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
    # Every finding is pass or not_applicable. All not_applicable (e.g. "page has no video") may need
    # its own "Not Applicable" status for the ACR: open team question #8, ask Jesus before changing.
    return STATUS_SUPPORTS
