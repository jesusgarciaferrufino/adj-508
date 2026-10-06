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
import time
from collections import Counter
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from agents.runlog import RunLog, new_run_id
from scanners.findings import REPO, findings_path, load_findings

# ---------- Other tracks' functions, with stubs until their PRs land ----------
try:
    from agents.mapper import map_finding  # Track B
except ImportError:
    def map_finding(f):
        return {}  # stub: keep the scanner's WCAG tags as they are

try:
    from agents.router import route_finding  # Track C
except ImportError:
    def route_finding(f):
        return {"bucket": f.get("bucket") or "grey", "route_reason": "STUB: router not merged yet"}

try:
    from agents.evidence import build_cases  # Track C
except ImportError:
    def build_cases(f):
        return {"ai": {"case_for": "STUB", "case_against": "STUB",
                       "model_id": None, "prompt_version": "STUB", "usage": {}}}


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
                added = fn(f)
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
        ai = [f["ai"] for f in findings if f.get("bucket") == "grey" and f.get("ai")]
        summary = {
            "run_id": state["run_id"],
            "version": state["version"],
            "page": state["page"],
            "total": len(findings),
            "buckets": dict(buckets),
            "case_files": len(ai),
            "model_ids": sorted({a.get("model_id") for a in ai if a.get("model_id")}),
            "prompt_versions": sorted({a.get("prompt_version") for a in ai if a.get("prompt_version")}),
            "tokens": {
                "input": sum((a.get("usage") or {}).get("input_tokens", 0) for a in ai),
                "output": sum((a.get("usage") or {}).get("output_tokens", 0) for a in ai),
            },
            "timings_sec": log.timings,
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
    print(f"{len(final['findings'])} findings: {b.get('machine', 0)} machine, "
          f"{b.get('grey', 0)} grey, {b.get('human-only', 0)} human-only — "
          f"{b.get('grey', 0)} case files written")
    print(f"Run log: {log.dir}")


if __name__ == "__main__":
    main()
