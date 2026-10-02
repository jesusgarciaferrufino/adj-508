"""Track B requirement mapper: attach the corpus entry for each finding's WCAG 2.0 criterion.

    python -m agents.mapper              # map every page into data/mapped/{version}_{page}.json
    python -m agents.mapper --spotcheck  # score 10 sampled findings against the ground truth

Run it as a module from the repo root; `python agents/mapper.py` can't import rag/ or scanners/.

Track A's merge already gives each finding exactly one WCAG 2.0 A/AA criterion (docs/TRACKS.md),
so mapping is a plain lookup in rag/wcag20_aa.json: no model call and no prompt file. A criterion
that is not in the corpus is rejected, never guessed.

Output, one file per page, keyed by finding_id:
    {"mapper_version", "site_version", "page", "corpus", "corpus_sha256",
     "findings": {finding_id: {"sc", "name", "level", "spec_url"}},
     "rejected": {finding_id: reason}}

A page that can't be read or written is reported and skipped, and its old output file is
removed so no later step reads a stale mapping. Any problem makes the exit status 1.
"""

import argparse
import csv
import json
import random
import re
import sys
from datetime import date
from pathlib import Path

from rag.corpus_tools import CORPUS, CorpusError, corpus_sha256, load_corpus, rel, write_atomic
from scanners.findings import PAGES, REPO, VERSIONS, Finding, FindingsFileError, load_findings

MAPPER_VERSION = "mapper-v2-corpus-lookup"
MAPPED_DIR = REPO / "data" / "mapped"
GROUND_TRUTH = REPO / "data" / "ground-truth" / "ground_truth.csv"
SPOTCHECK_LOG = REPO / "docs" / "mapper_spotcheck.md"
SPOTCHECK_SIZE = 10
SPOTCHECK_SEED = 508

TRUTH_SPLIT = re.compile(r"[;,\s]+")  # a ground-truth cell may list several criteria


class NotInCorpusError(ValueError):
    """A finding cites a criterion that is not in the WCAG 2.0 A/AA corpus."""


def map_finding(finding: Finding, corpus: dict[str, dict]) -> dict:
    """The corpus entry for the finding's one criterion. Raises NotInCorpusError if it has none."""
    sc = finding.wcag[0]
    entry = corpus.get(sc)
    if entry is None:
        raise NotInCorpusError(f"{finding.finding_id}: {sc} is not in {rel(CORPUS)}")
    return {"sc": sc, "name": entry["name"], "level": entry["level"], "spec_url": entry["source"]}


def map_page(findings: list[Finding], corpus: dict[str, dict]) -> tuple[dict, dict]:
    """({finding_id: mapping}, {finding_id: reason}) for one page's findings."""
    mapped, rejected = {}, {}
    for f in findings:
        try:
            mapped[f.finding_id] = map_finding(f, corpus)
        except NotInCorpusError as e:
            rejected[f.finding_id] = str(e)
    return mapped, rejected


def mapped_path(version: str, page: str) -> Path:
    return MAPPED_DIR / f"{version}_{page}.json"


def write_page(version: str, page: str, mapped: dict, rejected: dict, corpus_hash: str) -> Path:
    """Write one page's mapping to data/mapped/{version}_{page}.json and return the path."""
    out = mapped_path(version, page)
    doc = {"mapper_version": MAPPER_VERSION, "site_version": version, "page": page,
           "corpus": rel(CORPUS), "corpus_sha256": corpus_hash,
           "findings": mapped, "rejected": rejected}
    write_atomic(out, json.dumps(doc, indent=2) + "\n")
    return out


def _skip(version: str, page: str, reason: str, problems: list[str]) -> None:
    """Report a skipped page and remove its old output so nobody reads last run's mapping."""
    stale = mapped_path(version, page)
    if stale.exists():
        stale.unlink()
        reason += f" (removed stale {rel(stale)})"
    problems.append(f"skipped {version}/{page}: {reason}")
    print(f"SKIPPED {version}/{page}: {reason}", file=sys.stderr)


def map_all(corpus: dict[str, dict], corpus_hash: str) -> tuple[dict[str, str], list[str]]:
    """Map and write every page. Returns ({finding_id: sc} for everything mapped, [problems])."""
    all_mapped, problems = {}, []
    for version in VERSIONS:
        for page in PAGES:
            try:
                findings = load_findings(version, page)
            except FindingsFileError as e:
                _skip(version, page, str(e), problems)
                continue
            mapped, rejected = map_page(findings, corpus)
            try:
                out = write_page(version, page, mapped, rejected, corpus_hash)
            except OSError as e:
                _skip(version, page, f"could not write output: {e}", problems)
                continue
            for fid, reason in rejected.items():
                problems.append(f"rejected {fid}: {reason}")
                print(f"REJECTED {reason}", file=sys.stderr)
            all_mapped.update({fid: m["sc"] for fid, m in mapped.items()})
            print(f"{version:6} {page:8} {len(mapped):4} mapped, {len(rejected)} rejected -> "
                  f"{rel(out)}")
    return all_mapped, problems


# ---------------------------------------------------------------- spot-check


def read_ground_truth(path: Path = GROUND_TRUTH) -> dict[str, set[str]]:
    """{finding_id: {criteria}} from the ground-truth sheet's finding_id and wcag columns."""
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        missing = {"finding_id", "wcag"} - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path.name} is missing column(s) {sorted(missing)}")
        truth = {}
        for row in reader:
            fid = (row["finding_id"] or "").strip()
            criteria = {c for c in TRUTH_SPLIT.split(row["wcag"] or "") if c}
            if fid and criteria:
                truth[fid] = criteria
    return truth


def score_spotcheck(mapped: dict[str, str], truth: dict[str, set[str]],
                    size: int = SPOTCHECK_SIZE, seed: int = SPOTCHECK_SEED) -> list[tuple[str, str, set[str], bool]]:
    """Sample up to `size` findings present in both, with a fixed seed. [(finding_id, mapped, truth, correct)]."""
    common = sorted(set(mapped) & set(truth))
    sample = random.Random(seed).sample(common, min(size, len(common)))
    return [(fid, mapped[fid], truth[fid], mapped[fid] in truth[fid]) for fid in sorted(sample)]


def append_spotcheck(results: list[tuple[str, str, set[str], bool]]) -> None:
    """Append a dated section with the score and one table row per sampled finding."""
    correct = sum(ok for *_, ok in results)
    lines = [] if SPOTCHECK_LOG.exists() else [
        "# Mapper spot-check log",
        "",
        "Appended by `python -m agents.mapper --spotcheck`: a fixed-seed sample of mapped findings "
        f"compared with the `wcag` column of `{rel(GROUND_TRUTH)}`.",
    ]
    lines += [
        "",
        f"## {date.today().isoformat()}: {MAPPER_VERSION}, seed {SPOTCHECK_SEED}",
        "",
        f"**{correct}/{len(results)} correct.**",
        "",
        "| finding_id | Mapped | Ground truth | Match |",
        "| --- | --- | --- | --- |",
        *(f"| {fid} | {sc} | {', '.join(sorted(t))} | {'yes' if ok else 'NO'} |" for fid, sc, t, ok in results),
    ]
    SPOTCHECK_LOG.parent.mkdir(parents=True, exist_ok=True)
    with SPOTCHECK_LOG.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def spotcheck(mapped: dict[str, str]) -> int:
    """Score a fixed-seed sample of `mapped` against the ground truth and log it. Returns the exit status."""
    if not GROUND_TRUTH.exists():
        print(f"ERROR {rel(GROUND_TRUTH)} not found; nothing appended.", file=sys.stderr)
        return 1
    try:
        truth = read_ground_truth(GROUND_TRUTH)
    except (ValueError, csv.Error) as e:
        print(f"ERROR {e}; nothing appended.", file=sys.stderr)
        return 1
    results = score_spotcheck(mapped, truth)
    if not results:
        print("ERROR no finding_id appears in both the mapper output and the ground truth; nothing appended.",
              file=sys.stderr)
        return 1
    append_spotcheck(results)
    correct = sum(ok for *_, ok in results)
    print(f"Spot-check: {correct}/{len(results)} correct. Appended to {rel(SPOTCHECK_LOG)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point; returns the exit status."""
    ap = argparse.ArgumentParser(prog="python -m agents.mapper", description=__doc__.splitlines()[0])
    ap.add_argument("--spotcheck", action="store_true",
                    help=f"also score {SPOTCHECK_SIZE} sampled findings against the ground truth")
    a = ap.parse_args(argv)
    try:
        corpus, corpus_hash = load_corpus(), corpus_sha256()
    except (CorpusError, OSError) as e:
        print(f"ERROR {e}", file=sys.stderr)
        return 1
    mapped, problems = map_all(corpus, corpus_hash)
    print(f"{len(mapped)} findings mapped, {len(problems)} problem(s).")
    status = 1 if problems else 0
    if a.spotcheck:
        status = spotcheck(mapped) or status
    return status


if __name__ == "__main__":
    sys.exit(main())
