"""Track D: the pipeline survives one finding failing (e.g. a Bedrock call that errors)."""
import json

import agents.graph as graph
import agents.runlog as runlog
from scanners.findings import load_findings

BAD_ID = "F-before-home-el0081-1.1.1"


def run_pipeline(tmp_path, monkeypatch):
    monkeypatch.setattr(runlog, "RUNS_DIR", tmp_path)  # keep test runs out of data/runs/
    log = runlog.RunLog("test-run")
    try:
        final = graph.build_graph(log).invoke(
            {"run_id": "test-run", "version": "before", "page": "home", "findings": []}
        )
    finally:
        log.close()
    return final, log


def test_one_failing_finding_does_not_stop_the_run(tmp_path, monkeypatch):
    def route_all_grey(f):
        return {"bucket": "grey", "route_reason": "test"}

    def flaky_build_cases(f):
        if f["finding_id"] == BAD_ID:
            raise RuntimeError("simulated Bedrock failure")
        return {"ai": {"case_for": "for", "case_against": "against"}}

    # Fixed router so the test doesn't depend on which bucket Track C's real router picks
    monkeypatch.setattr(graph, "route_finding", route_all_grey)
    monkeypatch.setattr(graph, "build_cases", flaky_build_cases)

    final, log = run_pipeline(tmp_path, monkeypatch)
    by_id = {f["finding_id"]: f for f in final["findings"]}

    # Every finding is still there, and only the bad one is missing its cases
    assert len(by_id) == len(load_findings("before", "home"))
    assert by_id[BAD_ID]["ai"]["case_for"] == ""
    assert all(f["ai"]["case_for"] == "for" for fid, f in by_id.items() if fid != BAD_ID)

    # The error is in the run log and the summary
    assert log.errors == [{"node": "evidence", "finding_id": BAD_ID,
                           "error": "RuntimeError: simulated Bedrock failure"}]
    events = [json.loads(line) for line in (log.dir / "events.jsonl").read_text().splitlines()]
    assert [e["finding_id"] for e in events if "error" in e] == [BAD_ID]
    summary = json.loads((log.dir / "summary.json").read_text())
    assert summary["errors"] == log.errors
    assert summary["case_files"] == len(by_id) - 1
