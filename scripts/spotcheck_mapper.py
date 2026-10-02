"""Mapper checkpoint: sample N mapped findings, compare to ground_truth.csv, log the result.

    python scripts/spotcheck_mapper.py --findings data/findings/before_home.mapped.json \
        --truth ground_truth.csv --page home

A finding counts as correct if any ground-truth criterion for its element is among the
mapped criteria. Only findings that match a ground-truth row are sampled.
Set --match-col / --sc-col / --page-col to your CSV's real column names.
"""
import argparse
import csv
import datetime
import json
import random
import re
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--findings", required=True)
ap.add_argument("--truth", default="ground_truth.csv")
ap.add_argument("--page")
ap.add_argument("--n", type=int, default=10)
ap.add_argument("--seed", type=int, default=2)
ap.add_argument("--match-col", default="selector")
ap.add_argument("--sc-col", default="sc")
ap.add_argument("--page-col", default="page")
ap.add_argument("--report", default="docs/mapper_spotcheck.md")
a = ap.parse_args()

truth = {}
with open(a.truth, newline="") as fh:
    for row in csv.DictReader(fh):
        if a.page and row.get(a.page_col) and row[a.page_col] != a.page:
            continue
        key = (row.get(a.match_col) or "").strip()
        if key:
            truth.setdefault(key, set()).update(re.findall(r"\d\.\d\.\d", row.get(a.sc_col, "")))

findings = json.loads(Path(a.findings).read_text())
pool = [f for f in findings if f.get("selector", "").strip() in truth]
random.seed(a.seed)
sample = random.sample(pool, min(a.n, len(pool)))

rows, correct = [], 0
for f in sample:
    expected = truth[f["selector"].strip()]
    got = {m["sc"] for m in f.get("mapped_criteria", [])}
    ok = bool(expected & got)
    correct += ok
    rows.append(f"| {f.get('finding_id', '?')} | `{f['selector'][:40]}` | {', '.join(sorted(expected))} "
                f"| {', '.join(sorted(got)) or '-'} | {f['mapping']['method']} | {'✅' if ok else '❌'} |")

today = datetime.date.today().isoformat()
section = "\n".join([
    f"\n## {today} — {Path(a.findings).name}",
    f"Pool: {len(pool)} findings with ground truth (of {len(findings)}). Seed {a.seed}.",
    f"**Result: {correct}/{len(sample)} correct** (target 8/10)\n",
    "| Finding | Selector | Expected | Mapped | Method | OK |",
    "|---|---|---|---|---|---|", *rows, "",
    "Misses and why: _fill in_\n",
])
Path(a.report).parent.mkdir(parents=True, exist_ok=True)
with open(a.report, "a") as fh:
    fh.write(section)
print(section)
