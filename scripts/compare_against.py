"""Compare two AGAINST prompts on the four named findings (Track C, Week 4 handoff section 6).

    ADJ_MAX_BEDROCK_CALLS=12 python -m scripts.compare_against           # 12 Bedrock calls
    ADJ_MAX_BEDROCK_CALLS=12 python -m scripts.compare_against --write   # also save the report

The four findings are the ones in docs/evidence_samples.md. For each, FOR runs ONCE
(prompts/evidence_for_v1.md) and both AGAINST prompts get the same evidence, so the only thing
that varies is the AGAINST prompt. Everything else is the pipeline's own code: fill_prompt,
parse_side, citations_ok and recommend from agents/evidence.py, and settings.MODEL_ID.

"No worse" (switch only if it holds on all 4), checked in code for each finding:
- the new AGAINST reply parses and cites the finding's criterion;
- citations still verify if they verified with the old prompt;
- the recommendation doesn't move the wrong way. A Before finding matched in the ground truth
  is a known fail, so the new recommendation can't lean further towards pass. An After finding
  is a known pass (W3C passes every A/AA criterion there), so it can't lean further towards fail.
  A Before finding the ground truth doesn't name has no known answer, so there is no direction
  check for it.

The code can't check the point of _v2: that AGAINST never argues the fail case. The report puts
both AGAINST arguments side by side for a human to read. Switching is a separate one-line change
(PROMPT_AGAINST in agents/evidence.py) after that read.
"""

import argparse
import datetime
import sys

import settings
from agents import evidence
from scanners.findings import REPO, load_findings
from scripts.score import GT_PATH, MISSED, ScoreError, load_ground_truth

SAMPLES = [
    ("before", "home", "F-before-home-el0210-2.4.4"),  # "Click here" (Killer bees): W3C fails it
    ("before", "home", "F-before-home-el0194-2.1.1"),  # "more" arrow with a mouse-only image swap
    ("before", "home", "F-before-home-el0231-1.3.1"),  # <b>Free Penguins</b>
    ("after", "home", "F-after-home-el0062-4.1.2"),    # logo inside an <a> with no href, After site
]
OLD, NEW = "evidence_against_v1", "evidence_against_v2"
CALLS_NEEDED = 3 * len(SAMPLES)  # FOR once, then each AGAINST
LEAN = {"likely_fail": 0, "unsure": 1, "likely_pass": 2}  # 0 = towards fail, 2 = towards pass
REPORT = REPO / "docs" / "against_v2_comparison.md"


def expected_outcome(version: str, finding_id: str, gt_matched: set[str]) -> str | None:
    """"pass" on the After site, "fail" for a Before finding the ground truth matches, else None."""
    if version == "after":
        return "pass"
    return "fail" if finding_id in gt_matched else None


def _side(client, prompt_name: str, finding: dict) -> dict:
    """One Bedrock call with this prompt; the parsed reply, or {"error": ...} if it can't be used."""
    text, usage = evidence._call(client, evidence.fill_prompt(evidence.load_prompt(prompt_name), finding))
    try:
        side = evidence.parse_side(text)
    except evidence.EvidenceError as e:
        return {"error": str(e), "usage": usage}
    side["usage"] = usage
    return side


def compare_one(client, finding: dict, expected: str | None) -> dict:
    """FOR once, then OLD and NEW AGAINST on the same evidence, then the "no worse" checks."""
    for_side = _side(client, evidence.PROMPT_FOR, finding)
    out = {"finding_id": finding["finding_id"], "sc": finding["wcag"][0], "expected": expected,
           "for": for_side, "versions": {}, "problems": []}
    for name in (OLD, NEW):
        against = _side(client, name, finding)
        result = {"against": against, "recommendation": None, "citations_verified": False}
        if "error" not in for_side and "error" not in against:
            result["recommendation"] = evidence.recommend(for_side["strength"], against["strength"])
            result["citations_verified"] = (evidence.citations_ok(for_side, finding)
                                            and evidence.citations_ok(against, finding))
        out["versions"][name] = result

    old, new = out["versions"][OLD], out["versions"][NEW]
    if "error" in for_side:
        out["problems"].append(f"FOR reply unusable, so nothing can be compared: {for_side['error']}")
    elif "error" in new["against"]:
        out["problems"].append(f"new AGAINST reply unusable: {new['against']['error']}")
    else:
        if old["citations_verified"] and not new["citations_verified"]:
            out["problems"].append("citations verified with the old prompt but not with the new one")
        if old["recommendation"] is not None:
            moved = LEAN[new["recommendation"]] - LEAN[old["recommendation"]]
            if expected == "fail" and moved > 0:
                out["problems"].append(f"known fail moved towards pass: {old['recommendation']} -> "
                                       f"{new['recommendation']}")
            if expected == "pass" and moved < 0:
                out["problems"].append(f"known pass moved towards fail: {old['recommendation']} -> "
                                       f"{new['recommendation']}")
    out["no_worse"] = not out["problems"]
    return out


def _side_md(side: dict) -> list[str]:
    if "error" in side:
        return [f"> Unusable reply: {side['error']}"]
    quotes = "; ".join(f'"{q}"' for q in side["quoted_evidence"])
    return [f"> {side['argument']}", f"> Quoted: {quotes}", f"> Strength: {side['strength']}/5"]


def format_markdown(rows: list[dict], model_id: str, when: str) -> str:
    switch = all(r["no_worse"] for r in rows)
    out = [f"# AGAINST prompt comparison: `{OLD}` vs `{NEW}`, {when}", "",
           f"Model `{model_id}`, temperature 0. FOR (`{evidence.PROMPT_FOR}`) ran once per finding and both "
           "AGAINST prompts saw the same evidence. Made by `python -m scripts.compare_against --write`.", "",
           "| Finding | Known answer | Recommendation old -> new | Citations verified old -> new | No worse |",
           "| --- | --- | --- | --- | --- |"]
    for r in rows:
        o, n = r["versions"][OLD], r["versions"][NEW]
        out.append(f"| `{r['finding_id']}` | {r['expected'] or 'none (not in ground truth)'} | "
                   f"{o['recommendation']} -> {n['recommendation']} | "
                   f"{o['citations_verified']} -> {n['citations_verified']} | {'yes' if r['no_worse'] else 'NO'} |")
    out += ["", ("**Automatic checks: no worse on all 4.** Before switching, read the AGAINST arguments below: "
                 f"`{NEW}` must state a pass argument and judge it, never argue the fail case. If it does, switch "
                 "`PROMPT_AGAINST` in `agents/evidence.py`.") if switch else
            f"**Automatic checks: worse on at least one finding. Keep `{OLD}`.**"]
    for r in rows:
        out += ["", f"## `{r['finding_id']}` (criterion {r['sc']})", ""]
        out += [f"- {p}" for p in r["problems"]] or ["- No problems found by the checks."]
        out += ["", "**FOR**", ""] + _side_md(r["for"])
        for name in (OLD, NEW):
            out += ["", f"**AGAINST, `{name}`**", ""] + _side_md(r["versions"][name]["against"])
    return "\n".join(out) + "\n"


def _client():
    return evidence._bedrock_client()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=f"Compare {OLD} and {NEW} on the four named findings "
                                             f"({CALLS_NEEDED} Bedrock calls).")
    ap.add_argument("--write", action="store_true", help="also save docs/against_v2_comparison.md")
    args = ap.parse_args(argv)

    if evidence._calls_made + CALLS_NEEDED > settings.MAX_BEDROCK_CALLS:
        print(f"compare_against: needs {CALLS_NEEDED} Bedrock calls but the cap is {settings.MAX_BEDROCK_CALLS}. "
              f"Run: ADJ_MAX_BEDROCK_CALLS={CALLS_NEEDED} python -m scripts.compare_against", file=sys.stderr)
        return 1
    try:
        gt_matched = {r["finding_id"] for r in load_ground_truth(GT_PATH) if r["finding_id"] != MISSED}
    except ScoreError:
        gt_matched = set()  # no ground truth: no direction check on Before findings
    by_id = {}
    for version, page, _ in SAMPLES:
        by_id.update({f.finding_id: f.model_dump() for f in load_findings(version, page)})

    client = _client()
    rows = []
    try:
        for version, _, fid in SAMPLES:
            rows.append(compare_one(client, by_id[fid], expected_outcome(version, fid, gt_matched)))
    except Exception as e:  # noqa: BLE001 - a Bedrock or network error: say what happened, write nothing
        print(f"compare_against: Bedrock call failed after {evidence._calls_made} calls: {type(e).__name__}: {e}",
              file=sys.stderr)
        return 1

    report = format_markdown(rows, settings.MODEL_ID, datetime.date.today().isoformat())
    print(report)
    if args.write:
        REPORT.write_text(report, encoding="utf-8")
        print(f"Saved {REPORT.relative_to(REPO) if REPORT.is_relative_to(REPO) else REPORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
