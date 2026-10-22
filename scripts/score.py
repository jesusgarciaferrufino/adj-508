"""Score pipeline runs against the ground truth: the numbers that go on slides (Track S).

    python -m scripts.score                          # newest finished run for each of the 8 pages
    python -m scripts.score --runs 20261016-010203-ab12 20261016-010305-cd34
    python -m scripts.score --stub-grey-counts       # runs where FOR/AGAINST was skipped or stubbed
    python -m scripts.score --write                  # also save docs/scores/YYYY-MM-DD.md

Inputs: data/ground-truth/ground_truth.csv (one row per W3C failure cause on a Before page;
`finding_id` is the matching finding or `missed`) and run folders data/runs/{run_id}/ with
summary.json and output_findings.json, written by `python -m agents.graph`.

Definitions (router policy v1, CONTEXT.md, with the issue rule decided Oct 17). A judge may
ask, so they are spelled out here:

- **Reported as an issue:** `bucket == "machine"`, or `bucket == "grey"` and
  `ai.recommendation == "likely_fail"` and `ai.citations_verified` is true. Every ACR statement
  must link to evidence, and an unverified citation is exactly the case where it doesn't, so a
  likely_fail with unverified citations is flagged for a human, never an issue. The rule is
  imported from agents/evidence.py is_issue(finding), the one implementation the matrix also
  uses, so the two can't drift apart. With --stub-grey-counts, every grey finding
  counts as reported (for runs where the evidence agent was skipped or a stub); the output says so.
- **Flagged for a human:** not reported, and `bucket == "human-only"` or `bucket == "grey"`.
  A grey finding that leans pass, is unsure, was not evaluated, or is a likely_fail with
  unverified citations is sent to a human, not called an issue.
- **Before recall (a), issue level, reported only:** ground-truth rows whose finding_id is
  reported as an issue in that page's run, divided by all ground-truth rows on scored pages.
  `missed` rows count in the denominator and never in the numerator.
- **Before recall (b), issue level, reported or flagged:** the same, but a row also counts when
  its finding is flagged for a human. This is "the tool surfaced it to someone".
- **Before recall (c), criterion level:** distinct (page, criterion) pairs in the ground truth
  where the run has at least one finding on that page citing that criterion that is reported or
  flagged. The most generous figure: it does not check that it is the same element.
- **After false alarms:** findings on the After pages reported as issues. W3C's After reports
  pass every A/AA criterion, so every one of them is a false alarm.
- **Grey not evaluated:** grey findings where FOR/AGAINST did not produce a recommendation
  (`ai.recommendation` is None: skipped by ADJ_EVIDENCE_ONLY or the call cap, or the call
  failed; older runs wrote "unsure" with a `SKIPPED:` case_for, which also counts). They count as flagged,
  not reported, so while any exist (a) and the After false alarms are LOWER BOUNDS. The report
  shows the count per run and warns. Compare with --stub-grey-counts for the upper bound.
- **Unverified likely_fail:** a grey likely_fail whose citations were NOT verified in code
  (ai.citations_verified false). Flagged for a human, not reported; the report says how many.
- **Runs without stub tracking:** a summary.json with no "stubs" key predates stub tracking
  (runs before commit fffa6f2, e.g. Oct 4 to 6). The report marks such runs "predates stub
  tracking" (the matrix's wording) and warns above the tables, rather than calling them real.
  When picking runs automatically, a page's newest run WITH stub tracking wins; an old run is
  used only when the page has nothing newer.
- **Found under a different criterion:** a ground-truth row whose matched finding cites a
  different criterion (e.g. W3C's 3.3.2 "no label" matched to our 4.1.2 finding on the same
  field, because the scanners never assign 3.3.2). It counts as found in (a) and (b), since the
  element was caught, but is listed separately so the headline number can be explained.

A page with no run is shown as "not run", never as zero, and is left out of every ratio.
Nothing here sets or reads a final conformance status; that comes only from data/reviews/.
"""

import argparse
import csv
import datetime
import json
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from agents.evidence import grey_is_issue, is_issue
from scanners.findings import PAGES, REPO, FindingsFileError, validate_findings
from scanners.wcag import WCAG20_A_AA

GT_PATH = REPO / "data" / "ground-truth" / "ground_truth.csv"
RUNS_DIR = REPO / "data" / "runs"
SCORES_DIR = REPO / "docs" / "scores"
GT_COLUMNS = ["page", "wcag", "level", "description", "w3c_ref", "where", "finding_id", "verified_by", "notes"]
MISSED = "missed"
NOT_RUN = "not run"


class ScoreError(ValueError):
    """Input that can't be scored: a missing or malformed ground truth or run."""


# ---------------------------------------------------------------- inputs


def load_ground_truth(path: Path = GT_PATH) -> list[dict]:
    """Rows of the ground-truth CSV, checked for the columns and values scoring relies on."""
    if not path.exists():
        raise ScoreError(f"{path} not found. The ground truth has not landed yet "
                         "(see claude/week3-handoff-ground-truth.md).")
    with path.open(encoding="utf-8-sig", newline="") as fh:  # Excel may add a BOM
        reader = csv.DictReader(fh)
        missing = [c for c in GT_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise ScoreError(f"{path.name} is missing columns {missing}; expected {GT_COLUMNS}")
        rows = []
        for line, raw in enumerate(reader, start=2):
            row = {k: (raw.get(k) or "").strip() for k in GT_COLUMNS}
            if not any(row.values()):
                continue  # blank line
            if row["page"] not in PAGES:
                raise ScoreError(f"{path.name} line {line}: page {row['page']!r} is not one of {PAGES}")
            if row["wcag"] not in WCAG20_A_AA:
                raise ScoreError(f"{path.name} line {line}: {row['wcag']!r} is not WCAG 2.0 A/AA")
            if not row["finding_id"]:
                raise ScoreError(f"{path.name} line {line}: finding_id is empty (use `{MISSED}`)")
            if row["finding_id"] != MISSED and not row["finding_id"].startswith(f"F-before-{row['page']}-"):
                raise ScoreError(f"{path.name} line {line}: {row['finding_id']} is not a before/{row['page']} finding")
            row["line"] = line
            rows.append(row)
    if not rows:
        raise ScoreError(f"{path.name} has no rows")
    return rows


@dataclass
class Run:
    run_id: str
    version: str
    page: str
    findings: list[dict]
    stubs: list[str] | None = field(default_factory=list)  # None: run predates stub tracking


def load_run(run_id: str, runs_dir: Path | None = None) -> Run:
    folder = (runs_dir or RUNS_DIR) / run_id
    try:
        summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
        findings = json.loads((folder / "output_findings.json").read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise ScoreError(f"run {run_id}: {Path(e.filename).name} not found in {folder} "
                         "(unfinished run, or a run from another machine?)") from None
    except (OSError, json.JSONDecodeError) as e:
        raise ScoreError(f"run {run_id}: unreadable run files: {e}") from None
    try:  # the shared loader: same format checks as every other track (bucket values, IDs, ...)
        checked = [f.model_dump() for f in validate_findings(findings, f"run {run_id} output_findings.json")]
    except FindingsFileError as e:
        raise ScoreError(str(e)) from None
    stubs = list(summary["stubs"] or []) if "stubs" in summary else None
    return Run(run_id, summary.get("version"), summary.get("page"), checked, stubs)


def latest_runs(runs_dir: Path | None = None) -> list[str]:
    """The newest finished run for each page (run ids sort by time).

    A run with stub tracking (a "stubs" key in summary.json) beats any run without it, however
    new, so an old Oct 4 to 6 run is used only when a page has nothing else.
    """
    runs_dir = runs_dir or RUNS_DIR
    if not runs_dir.is_dir():
        return []
    tracked, untracked = {}, {}
    for folder in sorted((p for p in runs_dir.iterdir() if p.is_dir()), key=lambda p: p.name, reverse=True):
        try:
            run = load_run(folder.name, runs_dir)
        except ScoreError:
            continue
        (untracked if run.stubs is None else tracked).setdefault((run.version, run.page), run.run_id)
    return sorted({**untracked, **tracked}.values())


def runs_by_page(run_ids: list[str], runs_dir: Path | None = None) -> dict[tuple[str, str], Run]:
    out = {}
    for run_id in run_ids:
        run = load_run(run_id, runs_dir)
        key = (run.version, run.page)
        if key in out:
            raise ScoreError(f"two runs for {key[0]}/{key[1]}: {out[key].run_id} and {run_id}; pass one")
        out[key] = run
    return out


# ---------------------------------------------------------------- definitions


def is_reported(f: dict, stub_grey: bool = False) -> bool:
    """Reported as an issue: the shared rule, agents.evidence.is_issue (see the docstring).

    --stub-grey-counts overrides it for grey findings only, for runs where evidence was skipped.
    """
    if stub_grey and f.get("bucket") == "grey":
        return True
    return is_issue(f)


def is_flagged(f: dict, stub_grey: bool = False) -> bool:
    return not is_reported(f, stub_grey) and f.get("bucket") in ("human-only", "grey")


def _surfaced(f: dict, stub_grey: bool = False) -> bool:
    """Reported as an issue or flagged for a human: the tool put it in front of someone."""
    return is_reported(f, stub_grey) or is_flagged(f, stub_grey)


def is_unverified_fail(f: dict) -> bool:
    """A grey likely_fail that the issue rule rejects because its citations were not verified."""
    ai = f.get("ai") or {}
    return f.get("bucket") == "grey" and ai.get("recommendation") == "likely_fail" and not grey_is_issue(ai)


RECOMMENDATIONS = ("likely_fail", "likely_pass", "unsure")


def is_unevaluated(f: dict) -> bool:
    """A grey finding with no real FOR/AGAINST result: skipped, failed, or never run."""
    if f.get("bucket") != "grey":
        return False
    ai = f.get("ai") or {}
    skipped = str(ai.get("case_for") or "").startswith("SKIPPED")
    return skipped or ai.get("recommendation") not in RECOMMENDATIONS


def _wcag(f: dict) -> str | None:
    wcag = f.get("wcag") or []
    return wcag[0] if wcag else None


# ---------------------------------------------------------------- scoring


def _ratio(n: int, d: int) -> dict:
    return {"n": n, "d": d, "pct": round(100 * n / d, 1) if d else None}


def score(gt: list[dict], runs: dict[tuple[str, str], Run], stub_grey: bool = False) -> dict:
    """All the numbers, as plain data. `format_markdown` turns it into the report."""
    before_scored = [p for p in PAGES if ("before", p) in runs]
    per_page = {}
    hits_a = hits_b = rows_scored = 0
    pairs_hit, pairs_all = set(), set()
    not_in_run, other_criterion = [], []

    for page in PAGES:
        rows = [r for r in gt if r["page"] == page]
        run = runs.get(("before", page))
        if run is None:
            per_page[page] = {"status": NOT_RUN, "rows": len(rows)}
            continue
        by_id = {f.get("finding_id"): f for f in run.findings}
        a = b = 0
        for r in rows:
            f = by_id.get(r["finding_id"])
            if r["finding_id"] != MISSED and f is None:
                not_in_run.append(r)
            if f is not None and _wcag(f) != r["wcag"]:
                other_criterion.append({**r, "finding_wcag": _wcag(f)})
            if f is not None and is_reported(f, stub_grey):
                a += 1
            if f is not None and _surfaced(f, stub_grey):
                b += 1
        surfaced = {_wcag(f) for f in run.findings if _surfaced(f, stub_grey)}
        page_pairs = {r["wcag"] for r in rows}
        pairs_all |= {(page, c) for c in page_pairs}
        pairs_hit |= {(page, c) for c in page_pairs & surfaced}
        per_page[page] = {"status": "scored", "run_id": run.run_id, "rows": len(rows),
                          "missed": sum(r["finding_id"] == MISSED for r in rows),
                          "a": _ratio(a, len(rows)), "b": _ratio(b, len(rows)),
                          "c": _ratio(len(page_pairs & surfaced), len(page_pairs))}
        hits_a, hits_b, rows_scored = hits_a + a, hits_b + b, rows_scored + len(rows)

    false_alarms = {}
    for page in PAGES:
        run = runs.get(("after", page))
        if run is None:
            false_alarms[page] = NOT_RUN
            continue
        false_alarms[page] = {"run_id": run.run_id,
                              "by_wcag": dict(sorted(Counter(_wcag(f) for f in run.findings
                                                             if is_reported(f, stub_grey)).items()))}

    missed = defaultdict(list)
    for r in gt:
        if r["finding_id"] == MISSED:
            missed[r["wcag"]].append(r)

    return {
        "stub_grey": stub_grey,
        "before_pages_scored": before_scored,
        "recall": {"a": _ratio(hits_a, rows_scored), "b": _ratio(hits_b, rows_scored),
                   "c": _ratio(len(pairs_hit), len(pairs_all))},
        "per_page": per_page,
        "false_alarms": false_alarms,
        "false_alarms_total": sum(sum(v["by_wcag"].values()) for v in false_alarms.values() if v != NOT_RUN),
        "missed": dict(sorted(missed.items(), key=lambda kv: [int(x) for x in kv[0].split(".")])),
        "gt_rows": len(gt),
        "not_in_run": not_in_run,
        "runs": sorted((f"{v}/{p}", r.run_id, r.stubs) for (v, p), r in runs.items()),
        "unverified_fails": {f"{v}/{p}": sum(is_unverified_fail(f) for f in r.findings) for (v, p), r in runs.items()},
        "unevaluated": {f"{v}/{p}": sum(is_unevaluated(f) for f in r.findings) for (v, p), r in runs.items()},
        "other_criterion": other_criterion,
    }


# ---------------------------------------------------------------- output


def _pct(r: dict) -> str:
    return "n/a" if r["pct"] is None else f"{r['pct']}% ({r['n']}/{r['d']})"


def format_markdown(s: dict, commit: str = "", when: str = "") -> str:
    out = ["# ADJ scores" + (f", {when}" if when else ""), ""]
    if commit:
        out.append(f"Commit: `{commit}`")
    mode = ("**--stub-grey-counts: every grey finding counted as reported** (evidence skipped or stubbed)"
            if s["stub_grey"] else "Grey counts as reported only when `ai.recommendation == likely_fail` "
            "and `ai.citations_verified` is true")
    out += [f"Mode: {mode}", "", "## Runs", "", "| Page | Run | Stub nodes | Grey not evaluated |",
            "| --- | --- | --- | --- |"]
    out += [f"| {k} | `{rid}` | {'predates stub tracking' if st is None else ', '.join(st) or 'none'} | "
            f"{s['unevaluated'].get(k, 0)} |" for k, rid, st in s["runs"]] or ["| (none) | | | |"]
    untracked = [k for k, _, st in s["runs"] if st is None]
    if untracked:
        out += ["", f"**Warning: {', '.join(untracked)} predate stub tracking**, so it is unknown whether their "
                    "router and evidence were real. Rerun those pages before quoting these numbers."]
    unverified = sum(s["unverified_fails"].values())
    if unverified:
        out += ["", f"Note: {unverified} grey likely_fail finding(s) have citations_verified false. They are "
                    "flagged for a human, not reported as issues (issue rule, Oct 17)."]
    not_evaluated = sum(s["unevaluated"].values())
    if not_evaluated and not s["stub_grey"]:
        out += ["", f"**Warning: {not_evaluated} grey finding(s) were never evaluated by FOR/AGAINST** (skipped by "
                    "ADJ_EVIDENCE_ONLY or the call cap, or the call failed). They count as flagged, not reported, "
                    "so (a) and the After false alarms below are LOWER BOUNDS, not final numbers. Rerun with "
                    "evidence on every grey finding, or compare with --stub-grey-counts for the upper bound."]
    stubbed = sorted({n for _, _, st in s["runs"] for n in (st or [])})
    if stubbed and not s["stub_grey"]:
        out += ["", f"Warning: {', '.join(stubbed)} ran as a stub in some runs. Grey findings there have no "
                    "real recommendation, so (a) undercounts. Compare with --stub-grey-counts."]

    pages = s["before_pages_scored"]
    scope = f"{len(pages)} of 4 Before pages ({', '.join(pages) or 'none'})"
    r = s["recall"]
    out += ["", "## Before recall", "", f"Scope: {scope}. Target: 90% of W3C's issues.", "",
            "| Figure | Value |", "| --- | --- |",
            f"| (a) Issue level, reported as an issue | {_pct(r['a'])} |",
            f"| (b) Issue level, reported or flagged for a human | {_pct(r['b'])} |",
            f"| (c) Criterion level, reported or flagged (page and criterion pairs) | {_pct(r['c'])} |",
            f"| Rows in (a)/(b) found under a different criterion | {len(s['other_criterion'])} |",
            "", "| Page | Run | GT rows | missed | (a) | (b) | (c) |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for page, p in s["per_page"].items():
        if p["status"] == NOT_RUN:
            out.append(f"| {page} | {NOT_RUN} | {p['rows']} | | {NOT_RUN} | {NOT_RUN} | {NOT_RUN} |")
        else:
            out.append(f"| {page} | `{p['run_id']}` | {p['rows']} | {p['missed']} | "
                       f"{_pct(p['a'])} | {_pct(p['b'])} | {_pct(p['c'])} |")

    out += ["", "## After false alarms", "",
            "Findings on the After site reported as issues. W3C passes every A/AA criterion there, "
            "so all of them are false.", "",
            "| Page | Run | False alarms | By criterion |", "| --- | --- | --- | --- |"]
    for page, fa in s["false_alarms"].items():
        if fa == NOT_RUN:
            out.append(f"| {page} | {NOT_RUN} | {NOT_RUN} | |")
        else:
            crit = ", ".join(f"{c}: {n}" for c, n in fa["by_wcag"].items()) or "none"
            out.append(f"| {page} | `{fa['run_id']}` | {sum(fa['by_wcag'].values())} | {crit} |")
    after_run = sum(fa != NOT_RUN for fa in s["false_alarms"].values())
    out.append(f"\nTotal on {after_run} of 4 After pages: {s['false_alarms_total']}")

    n_missed = sum(len(v) for v in s["missed"].values())
    out += ["", f"## Missed by the scanners ({n_missed} of {s['gt_rows']} ground-truth rows)", ""]
    if not s["missed"]:
        out.append("None.")
    for wcag, rows in s["missed"].items():
        out.append(f"- **{wcag}** ({len(rows)}): " + "; ".join(f"{r['page']}: {r['description']}" for r in rows))

    if s["other_criterion"]:
        out += ["", "## Found under a different criterion", "",
                "The element was caught, so these count in (a) and (b), but our finding cites a different "
                "criterion than W3C. Explain these if asked; the mapper's secondary criteria (Week 4) should "
                "close them."]
        out += [f"- line {r['line']}: {r['page']} W3C {r['wcag']}, our `{r['finding_id']}` cites "
                f"{r['finding_wcag']}" for r in s["other_criterion"]]

    if s["not_in_run"]:
        out += ["", "## Ground-truth finding_ids not in the run", "",
                "These rows name a finding the run does not contain (stale ID, or findings regenerated). "
                "They count as not found."]
        out += [f"- line {r['line']}: {r['page']} {r['wcag']} `{r['finding_id']}`" for r in s["not_in_run"]]
    return "\n".join(out) + "\n"


def git_commit() -> str:
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True,
                             text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=REPO,
                               capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return sha + (" (uncommitted changes)" if dirty else "")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Score pipeline runs against the ground truth. "
                                             "Definitions: see the module docstring.")
    ap.add_argument("--runs", nargs="+", metavar="RUN_ID",
                    help="run ids under data/runs/ (default: newest finished run for each page)")
    ap.add_argument("--gt", type=Path, default=GT_PATH, help="ground-truth CSV (default: %(default)s)")
    ap.add_argument("--stub-grey-counts", action="store_true",
                    help="count every grey finding as reported (for runs with evidence skipped or stubbed)")
    ap.add_argument("--write", action="store_true", help="also save docs/scores/YYYY-MM-DD.md")
    args = ap.parse_args(argv)

    try:
        gt = load_ground_truth(args.gt)
        run_ids = args.runs or latest_runs()
        if not run_ids:
            raise ScoreError("no finished runs in data/runs/. Run: python -m agents.graph --version before --page home")
        result = score(gt, runs_by_page(run_ids), args.stub_grey_counts)
    except ScoreError as e:
        print(f"score: {e}", file=sys.stderr)
        return 1

    today = datetime.date.today().isoformat()
    report = format_markdown(result, git_commit(), today)
    print(report)
    if args.write:
        SCORES_DIR.mkdir(parents=True, exist_ok=True)
        path = SCORES_DIR / f"{today}.md"
        path.write_text(report, encoding="utf-8")
        print(f"Saved {path.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
