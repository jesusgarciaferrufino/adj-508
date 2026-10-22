"""Week 2 pipeline (Track D).

    load findings -> mapper -> router -> FOR/AGAINST (grey only) -> write output

Run from the repo's top folder:
    python -m agents.graph --version before --page home

Contract with the other tracks: each function takes ONE finding (a dict in the
docs/finding.example.json format) and returns ONLY the fields it adds.
    agents/mapper.py    map_finding(finding)   -> {"mapping": {"sc", "name", "level", "spec_url", ...}}  (Track B)
    agents/router.py    route_finding(finding) -> {"bucket": ..., "route_reason": ...}  (Track C)
    agents/evidence.py  build_cases(finding)   -> {"ai": {"case_for": ..., "case_against": ...,
                                                   "model_id": ..., "prompt_version": ...,
                                                   "usage": {"input_tokens": n, "output_tokens": n}}}  (Track C)
Until those files exist, the stubs below stand in so the graph runs today.
"""
import argparse
import importlib
import json
import sys
import time
from collections import Counter
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from agents.runlog import RunLog, new_run_id
from scanners.findings import REPO, findings_path, load_findings

# ---------- Other tracks' functions, with stubs until their PRs land ----------
STUBS = []  # nodes running on a stub in this process; written to summary.json


def load_agent(node, module, name, stub):
    """Import a track's function, or use the stub ONLY if that track's file doesn't exist yet.
    If the file exists but its own imports are broken, fail loudly instead of hiding it."""
    try:
        return getattr(importlib.import_module(module), name)
    except ModuleNotFoundError as e:
        if e.name != module:
            raise  # e.g. a typo'd import inside agents/mapper.py
        STUBS.append(node)
        return stub


def _stub_map_finding(f):
    return {}  # keep the scanner's WCAG tags as they are


def _stub_route_finding(f):
    return {"bucket": f.get("bucket") or "grey", "route_reason": "STUB: router not merged yet"}


def _stub_build_cases(f):
    return {"ai": {"case_for": "STUB", "case_against": "STUB",
                   "model_id": None, "prompt_version": "STUB", "usage": {}}}


map_finding = load_agent("mapper", "agents.mapper", "map_finding", _stub_map_finding)  # Track B
route_finding = load_agent("router", "agents.router", "route_finding", _stub_route_finding)  # Track C
build_cases = load_agent("evidence", "agents.evidence", "build_cases", _stub_build_cases)  # Track C


# ---------- State passed between nodes ----------
class State(TypedDict):
    run_id: str
    version: str
    page: str
    findings: list


def add_fields(finding, added):
    """Add a node's fields to a finding without wiping earlier ones.
    Nested dicts (like "ai") are merged one level deep."""
    out = dict(finding)
    for key, value in added.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = {**out[key], **value}
        else:
            out[key] = value
    return out


def case_file_counts(findings, errors):
    """(AI results that are real case files, how many grey findings were skipped).

    A skipped finding (ADJ_EVIDENCE_ONLY or the call cap) has ai.case_for "SKIPPED: ..." and is
    not a case file. A finding whose evidence call failed is in neither list (it is in errors).
    """
    failed = {e["finding_id"] for e in errors if e["node"] == "evidence"}
    written, skipped = [], 0
    for f in findings:
        if f.get("bucket") != "grey" or not f.get("ai") or f.get("finding_id") in failed:
            continue
        if str(f["ai"].get("case_for") or "").startswith("SKIPPED:"):
            skipped += 1
        else:
            written.append(f["ai"])
    return written, skipped


def build_graph(log):
    def per_finding_node(name, fn, only_bucket=None):
        """Wrap a one-finding function as a graph node that logs what it adds."""
        def node(state):
            t0 = time.time()
            out = []
            for f in state["findings"]:
                if only_bucket and f.get("bucket") != only_bucket:
                    out.append(f)
                    continue
                try:
                    added = fn(f)
                except Exception as e:  # noqa: BLE001 - one bad finding (e.g. a failed Bedrock call) must not stop the run
                    log.error(name, f.get("finding_id"), e)
                    out.append(f)  # keep the finding as it was before this node
                    continue
                log.event(name, f.get("finding_id"), added)
                out.append(add_fields(f, added))
            log.timing(name, time.time() - t0)
            return {"findings": out}
        return node

    def load(state):
        # Track A's loader validates every finding against docs/finding.example.json
        findings = [f.model_dump() for f in load_findings(state["version"], state["page"])]
        path = findings_path(state["version"], state["page"]).relative_to(REPO)
        log.save("input_findings.json", {"source_file": str(path), "findings": findings})
        return {"findings": findings}

    def write_output(state):
        findings = state["findings"]
        buckets = Counter(f.get("bucket") for f in findings)
        ai, skipped = case_file_counts(findings, log.errors)
        summary = {
            "run_id": state["run_id"],
            "version": state["version"],
            "page": state["page"],
            "total": len(findings),
            "buckets": dict(buckets),
            "case_files": len(ai),  # FOR/AGAINST actually ran; skipped findings are counted below
            "skipped": skipped,
            "model_ids": sorted({a.get("model_id") for a in ai if a.get("model_id")}),
            "prompt_versions": sorted({a.get("prompt_version") for a in ai if a.get("prompt_version")}),
            "tokens": {
                "input": sum((a.get("usage") or {}).get("input_tokens", 0) for a in ai),
                "output": sum((a.get("usage") or {}).get("output_tokens", 0) for a in ai),
            },
            "timings_sec": log.timings,
            "errors": log.errors,
            "stubs": STUBS,  # nodes that were stand-ins, not the real track code
        }
        log.save("output_findings.json", findings)
        log.save("summary.json", summary)
        return {}

    g = StateGraph(State)
    g.add_node("load", load)
    g.add_node("mapper", per_finding_node("mapper", map_finding))
    g.add_node("router", per_finding_node("router", route_finding))
    g.add_node("evidence", per_finding_node("evidence", build_cases, only_bucket="grey"))
    g.add_node("write_output", write_output)

    g.add_edge(START, "load")
    g.add_edge("load", "mapper")
    g.add_edge("mapper", "router")
    g.add_edge("router", "evidence")      # evidence only touches grey findings
    g.add_edge("evidence", "write_output")
    g.add_edge("write_output", END)
    return g.compile()


def main():
    p = argparse.ArgumentParser(description="Run the ADJ week-2 pipeline")
    p.add_argument("--version", default="before", choices=["before", "after"])
    p.add_argument("--page", default="home", choices=["home", "news", "tickets", "survey"])
    args = p.parse_args()

    run_id = new_run_id()
    log = RunLog(run_id)
    try:
        final = build_graph(log).invoke(
            {"run_id": run_id, "version": args.version, "page": args.page, "findings": []}
        )
    finally:
        log.close()

    b = Counter(f.get("bucket") for f in final["findings"])
    failed_cases = sum(1 for e in log.errors if e["node"] == "evidence")
    summary = json.loads((log.dir / "summary.json").read_text())  # counts write_output already made
    print(f"{len(final['findings'])} findings: {b.get('machine', 0)} machine, "
          f"{b.get('grey', 0)} grey, {b.get('human-only', 0)} human-only — "
          f"{summary['case_files']} case files written, {summary['skipped']} skipped"
          + (f", {failed_cases} failed" if failed_cases else ""))
    if log.errors:
        print(f"{len(log.errors)} errors (those findings were kept unchanged; details in events.jsonl):")
        for e in log.errors[:5]:
            print(f"  {e['node']} {e['finding_id']}: {e['error']}")
    if STUBS:
        print(f"Stubs (not real yet): {', '.join(STUBS)}")
    print(f"Run log: {log.dir.relative_to(REPO)}")
    if log.errors:
        sys.exit(1)  # non-zero exit so scripts and CI notice a partial run


if __name__ == "__main__":
    main()
