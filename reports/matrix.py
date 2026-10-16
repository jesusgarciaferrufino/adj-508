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
import sys
from collections import Counter

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

# The AI's leaning on a grey finding (Track C's ai.recommendation). "none" = no recommendation on the
# finding (a stub run, or a finding the AI didn't look at); it is counted on its own, never as "unsure".
AI_LEANINGS = ("likely_fail", "likely_pass", "unsure", "none")

# Track C's router buckets -> the matrix column that counts them
BUCKET_COLUMNS = {"machine": "machine", "grey": "grey", "human-only": "human_checks"}


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


def _evidence(finding):
    """Where a reviewer can see one finding: its selector and, when there is one, its screenshot.

    A finding on the <html> element is about the whole page (e.g. 3.1.1 page language), so it is
    labelled that way instead of showing a bare "html".
    """
    ev = finding["evidence"]
    where = "page-level (whole page)" if ev["selector"] == "html" else ev["selector"]
    if ev["screenshot"] is None:  # e.g. <title>: nothing on screen to photograph
        return where
    return f"{where} (screenshot: {ev['screenshot']})"


def _ai_leaning(findings):
    """What the AI leans towards for a criterion's findings, as counts. Advice only, never a status.

    Starts with "AI:" so nobody mistakes it for a human decision. A value Track C adds later that
    isn't in AI_LEANINGS is still shown under its own name, not hidden in another count.
    """
    if not findings:
        return ""
    counts = Counter(f["ai"].get("recommendation") or "none" for f in findings)
    names = list(AI_LEANINGS) + sorted(set(counts) - set(AI_LEANINGS))
    return "AI: " + ", ".join(f"{name} {counts[name]}" for name in names)


def _sc_order(sc):
    """Sort key so "1.4.10" would come after "1.4.9" (plain text order puts it first)."""
    return tuple(int(part) for part in sc.split("."))


def build_matrix(findings, decisions, corpus, map508):
    """One row per WCAG 2.0 A/AA criterion (38), in criterion order. Touches no files.

    findings   from load_run; grouped by wcag[0] (Track A's criterion), not mapping.sc
    decisions  from load_decisions; only these set a row's status
    corpus     from rag.corpus_tools.load_corpus(): {sc: {"name", "level", ...}}
    map508     rag/section508_map.json: {sc: {"web": "E205.4", ...}}
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
            "status": criterion_status(ids, decisions),
            "machine": buckets["machine"],
            "grey": buckets["grey"],
            "human_checks": buckets["human_checks"],
            "finding_ids": "; ".join(ids),
            "evidence": "; ".join(_evidence(f) for f in group),  # same order as finding_ids
            "ai_leaning": _ai_leaning(group),
            "reviewed_by": ", ".join(sorted({decisions[i]["reviewer"] for i in ids if i in decisions})),
        })
    return rows
