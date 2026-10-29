"""Track D: the pipeline survives one finding failing (e.g. a Bedrock call that errors)."""
import json

import pytest

import agents.graph as graph
import agents.runlog as runlog
from scanners.findings import REPO, load_findings

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


def test_stub_used_only_when_the_agent_file_is_missing(tmp_path, monkeypatch):
    # A fake package: one agent file with a typo'd import inside it, and no "missing" file at all
    (tmp_path / "fakeagents").mkdir()
    (tmp_path / "fakeagents" / "__init__.py").write_text("")
    (tmp_path / "fakeagents" / "broken.py").write_text("import a_module_with_a_typo\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(graph, "STUBS", [])

    def stub(f):
        return {}

    # File doesn't exist yet: use the stub and record it
    assert graph.load_agent("router", "fakeagents.missing", "route_finding", stub) is stub
    assert graph.STUBS == ["router"]

    # File exists but its own import is broken: crash loudly, don't hide it behind the stub
    with pytest.raises(ModuleNotFoundError, match="a_module_with_a_typo"):
        graph.load_agent("mapper", "fakeagents.broken", "map_finding", stub)
    assert graph.STUBS == ["router"]


def test_summary_lists_stub_nodes(tmp_path, monkeypatch):
    # Fakes for the router and evidence agent: this test is about summary.json, and once
    # Track C is merged the real build_cases would try to call Bedrock
    monkeypatch.setattr(graph, "route_finding", lambda f: {"bucket": "grey", "route_reason": "test"})
    monkeypatch.setattr(graph, "build_cases", lambda f: {"ai": {"case_for": "for", "case_against": "against"}})
    monkeypatch.setattr(graph, "STUBS", ["router", "evidence"])
    _, log = run_pipeline(tmp_path, monkeypatch)
    summary = json.loads((log.dir / "summary.json").read_text())
    assert summary["stubs"] == ["router", "evidence"]
    assert summary["errors"] == []  # an error here would mean a real agent ran instead of the fakes


def test_run_logs_live_in_the_repo_whatever_the_current_folder():
    assert runlog.RUNS_DIR == REPO / "data" / "runs"
    assert runlog.RUNS_DIR.is_absolute()


def test_skipped_findings_are_not_counted_as_case_files(tmp_path, monkeypatch):
    """With the call cap at 0 every grey finding is skipped: 0 case files, not "90 case files written"."""
    import agents.evidence as evidence
    import settings

    monkeypatch.setattr(graph, "route_finding", lambda f: {"bucket": "grey", "route_reason": "test"})
    monkeypatch.setattr(graph, "build_cases", evidence.build_cases)  # real agent, skipped before any call
    monkeypatch.setattr(evidence, "_calls_made", 0)
    monkeypatch.setattr(settings, "MAX_BEDROCK_CALLS", 0)
    monkeypatch.setattr(settings, "EVIDENCE_ONLY", frozenset())

    final, log = run_pipeline(tmp_path, monkeypatch)
    summary = json.loads((log.dir / "summary.json").read_text())
    n = len(final["findings"])
    assert summary["case_files"] == 0 and summary["skipped"] == n
    assert summary["tokens"] == {"input": 0, "output": 0} and summary["prompt_versions"] == []
    assert all(f["ai"]["recommendation"] is None for f in final["findings"])


def test_case_file_counts_split_written_skipped_and_failed():
    findings = [
        {"finding_id": "a", "bucket": "grey", "ai": {"case_for": "Specific argument."}},
        {"finding_id": "b", "bucket": "grey", "ai": {"case_for": "SKIPPED: call cap reached"}},
        {"finding_id": "c", "bucket": "grey", "ai": {"case_for": ""}},          # call failed
        {"finding_id": "d", "bucket": "machine", "ai": {"case_for": ""}},
    ]
    errors = [{"node": "evidence", "finding_id": "c", "error": "RuntimeError: boom"}]
    written, skipped = graph.case_file_counts(findings, errors)
    assert [a["case_for"] for a in written] == ["Specific argument."] and skipped == 1



def test_summary_bills_tokens_from_findings_that_failed(tmp_path, monkeypatch):
    """tokens_billed counts the FOR call of a finding whose AGAINST call then failed (Oct 27 fix)."""
    from botocore.exceptions import ClientError

    import agents.evidence as evidence
    import settings

    class Client:
        """FOR answers on every finding; AGAINST throttles for good on the second finding only."""
        def __init__(self):
            self.n = 0

        def converse(self, modelId, messages, inferenceConfig):
            self.n += 1
            if self.n >= 4:  # call 1-2: first finding; call 3: second finding's FOR; then AGAINST fails
                raise ClientError({"Error": {"Code": "ThrottlingException", "Message": "slow down"}}, "Converse")
            text = json.dumps({"argument": "x", "cited_sc": "1.1.1", "quoted_evidence": ["nothing here"],
                               "strength": 3})
            return {"output": {"message": {"content": [{"text": text}]}},
                    "usage": {"inputTokens": 1000, "outputTokens": 300}}

    two = sorted(f.finding_id for f in load_findings("before", "home"))[:2]
    monkeypatch.setattr(graph, "route_finding", lambda f: {"bucket": "grey", "route_reason": "test"})
    monkeypatch.setattr(graph, "build_cases", evidence.build_cases)
    monkeypatch.setattr(evidence, "_bedrock_client", lambda: client)
    monkeypatch.setattr(evidence.time, "sleep", lambda s: None)
    monkeypatch.setattr(evidence, "_calls_made", 0)
    monkeypatch.setattr(settings, "MAX_BEDROCK_CALLS", 50)
    monkeypatch.setattr(settings, "EVIDENCE_ONLY", frozenset(two))
    client = Client()

    final, log = run_pipeline(tmp_path, monkeypatch)
    summary = json.loads((log.dir / "summary.json").read_text())
    assert [e["finding_id"] for e in log.errors] == [two[1]]
    assert summary["case_files"] == 1 and summary["tokens"] == {"input": 2000, "output": 600}  # finished only
    assert summary["tokens_billed"] == {"input": 3000, "output": 900}  # plus the failed finding's FOR call


def test_tokens_billed_is_none_without_track_c(tmp_path, monkeypatch):
    monkeypatch.setattr(graph, "route_finding", lambda f: {"bucket": "grey", "route_reason": "test"})
    monkeypatch.setattr(graph, "build_cases", lambda f: {"ai": {"case_for": "for", "case_against": "against"}})
    monkeypatch.setattr(graph, "tokens_billed", lambda: None)  # load_agent's stub when Track C is missing
    final, log = run_pipeline(tmp_path, monkeypatch)
    assert json.loads((log.dir / "summary.json").read_text())["tokens_billed"] is None


def test_tokens_billed_is_per_run_when_one_graph_runs_twice(tmp_path, monkeypatch):
    """The snapshot is taken when each run starts, not when the graph is built."""
    counter = {"input_tokens": 0, "output_tokens": 0}

    def billing_build_cases(f):  # a fake evidence agent that bills 10 in / 1 out per finding
        counter["input_tokens"] += 10
        counter["output_tokens"] += 1
        return {"ai": {"case_for": "for", "case_against": "against"}}

    monkeypatch.setattr(graph, "route_finding", lambda f: {"bucket": "grey", "route_reason": "test"})
    monkeypatch.setattr(graph, "build_cases", billing_build_cases)
    monkeypatch.setattr(graph, "tokens_billed", lambda: dict(counter))
    monkeypatch.setattr(runlog, "RUNS_DIR", tmp_path)
    log = runlog.RunLog("run-a")
    app = graph.build_graph(log)
    counter["input_tokens"] = 999  # tokens spent between building the graph and running it don't count
    summaries = []
    for run_id, page in (("run-a", "home"), ("run-a", "news")):
        app.invoke({"run_id": run_id, "version": "before", "page": page, "findings": []})
        summaries.append(json.loads((log.dir / "summary.json").read_text()))
    log.close()
    n_home, n_news = len(load_findings("before", "home")), len(load_findings("before", "news"))
    assert summaries[0]["tokens_billed"] == {"input": 10 * n_home, "output": n_home}
    assert summaries[1]["tokens_billed"] == {"input": 10 * n_news, "output": n_news}  # only its own


def test_a_missing_track_is_listed_once_in_stubs(monkeypatch):
    monkeypatch.setattr(graph, "STUBS", [])
    graph.load_agent("evidence", "agents.no_such_module", "build_cases", None)
    graph.load_agent("evidence", "agents.no_such_module", "tokens_billed", None)
    assert graph.STUBS == ["evidence"]
