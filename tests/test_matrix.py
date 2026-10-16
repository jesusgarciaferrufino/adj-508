"""Track E: the compliance matrix (reports/matrix.py).

Tests never touch the real data/ folder: fixtures build a tiny fake run and reviews file in a temp folder.
"""
import copy
import json

import pytest

from rag import corpus_tools
from rag.corpus_tools import load_corpus
from reports import matrix
from scanners.findings import REPO

EXAMPLE = json.loads((REPO / "docs" / "finding.example.json").read_text())
VERSION, PAGE, RUN_ID = "before", "home", "test-run"  # the one fake page and run every test uses
SHOTS = f"data/findings/shots/{VERSION}_{PAGE}"  # screenshot path prefix (docs/TRACKS.md)


def make_finding(dom_index, sc, *, selector, screenshot, bucket, recommendation=None):
    """A valid finding for the fake page: a copy of docs/finding.example.json with the given changes."""
    f = copy.deepcopy(EXAMPLE)
    f["site_version"], f["page"] = VERSION, PAGE
    f["finding_id"] = f"F-{VERSION}-{PAGE}-el{dom_index:04d}-{sc}"
    f["wcag"] = [sc]
    f["evidence"].update(dom_index=dom_index, selector=selector, screenshot=screenshot)
    f["bucket"] = bucket
    if recommendation is not None:
        f["ai"]["recommendation"] = recommendation
    return f


def make_decision(finding_id, decision, *, reviewer="alice", run_id=RUN_ID):
    """One line of data/reviews/{version}_{page}.jsonl, as PULSE (Track F) writes it."""
    return {"finding_id": finding_id, "run_id": run_id, "reviewer": reviewer,
            "decision": decision, "comment": "", "decided_at": "2026-10-14T15:00:00Z"}


@pytest.fixture
def fake_run(tmp_path, monkeypatch):
    """A pipeline run for the fake page with 4 findings, in a temp folder. Returns the findings."""
    runs_dir = tmp_path / "runs"
    monkeypatch.setattr(matrix, "RUNS_DIR", runs_dir)
    findings = [
        # Machine-checked violation the AI thinks fails (used by the "AI never sets status" test)
        make_finding(81, "1.1.1", selector='img[src$="top_weather.gif"]',
                     screenshot=f"{SHOTS}_el0081.jpg",
                     bucket="machine", recommendation="likely_fail"),
        # The Definition of Done finding
        make_finding(210, "2.4.4", selector="#content > p:nth-child(7) > span > a:nth-child(2)",
                     screenshot=f"{SHOTS}_el0210.jpg",
                     bucket="grey", recommendation="unsure"),
        # <title> can't be photographed: screenshot is null
        make_finding(2, "2.4.2", selector="html > head > title", screenshot=None, bucket="grey"),
        # Page-level human check
        make_finding(0, "3.1.1", selector="html", screenshot=f"{SHOTS}_page.jpg", bucket="human-only"),
    ]
    run = runs_dir / RUN_ID
    run.mkdir(parents=True)
    (run / "summary.json").write_text(json.dumps({"run_id": RUN_ID, "version": VERSION, "page": PAGE, "stubs": []}))
    (run / "output_findings.json").write_text(json.dumps(findings))
    return findings


@pytest.fixture
def reviews(fake_run, tmp_path, monkeypatch):
    """Human decisions on fake_run's findings, as a JSONL file in a temp folder. Returns the lines."""
    reviews_dir = tmp_path / "reviews"
    monkeypatch.setattr(matrix, "REVIEWS_DIR", reviews_dir)
    id_for = {f["wcag"][0]: f["finding_id"] for f in fake_run}  # e.g. "2.4.4" -> "F-before-home-el0210-2.4.4"
    lines = [
        # Two reviewers decide the Definition of Done finding; the later line (bob's fail) must win
        make_decision(id_for["2.4.4"], "pass", reviewer="alice"),
        make_decision(id_for["2.4.4"], "fail", reviewer="bob"),
        # Decided on an older run: still counts, because finding IDs don't change between runs
        make_decision(id_for["2.4.2"], "pass", run_id="older-run"),
    ]
    reviews_dir.mkdir()
    (reviews_dir / f"{VERSION}_{PAGE}.jsonl").write_text("".join(json.dumps(line) + "\n" for line in lines))
    return lines


def test_fixtures_build(fake_run, reviews):
    """Both fixtures write their files where matrix.py will look for them."""
    run = matrix.RUNS_DIR / RUN_ID
    assert json.loads((run / "summary.json").read_text())["page"] == PAGE
    assert len(json.loads((run / "output_findings.json").read_text())) == 4
    lines = (matrix.REVIEWS_DIR / f"{VERSION}_{PAGE}.jsonl").read_text().splitlines()
    assert [json.loads(line) for line in lines] == reviews


# ---------------------------------------------------------------- load_run

def test_load_run_reads_summary_and_findings(fake_run):
    summary, findings = matrix.load_run(RUN_ID)
    assert (summary["version"], summary["page"]) == (VERSION, PAGE)
    assert [f["finding_id"] for f in findings] == [f["finding_id"] for f in fake_run]
    assert findings[0]["bucket"] == "machine"  # fields later tracks add are kept
    assert findings[0]["ai"]["recommendation"] == "likely_fail"


def test_load_run_unknown_run(fake_run):
    with pytest.raises(matrix.MatrixInputError, match="no-such-run"):
        matrix.load_run("no-such-run")


def test_load_run_invalid_findings(fake_run):
    bad = copy.deepcopy(fake_run)
    bad[1]["wcag"] = ["9.9.9"]  # not a WCAG 2.0 criterion
    (matrix.RUNS_DIR / RUN_ID / "output_findings.json").write_text(json.dumps(bad))
    with pytest.raises(ValueError, match="F-before-home-el0210-2.4.4"):
        matrix.load_run(RUN_ID)


# ---------------------------------------------------------------- load_decisions

def append_lines(*raw):
    """Add raw lines (bytes) to the end of the fake page's reviews file."""
    with (matrix.REVIEWS_DIR / f"{VERSION}_{PAGE}.jsonl").open("ab") as fh:
        fh.write(b"".join(line + b"\n" for line in raw))


def test_last_decision_wins(fake_run, reviews):
    decisions, skipped = matrix.load_decisions(VERSION, PAGE)
    last = decisions[fake_run[1]["finding_id"]]  # the 2.4.4 finding
    assert (last["decision"], last["reviewer"]) == ("fail", "bob")
    assert skipped == []


def test_decision_from_another_run_counts(fake_run, reviews):
    decisions, _ = matrix.load_decisions(VERSION, PAGE)
    assert decisions[fake_run[2]["finding_id"]]["run_id"] == "older-run"


def test_no_reviews_file_means_nothing_decided(fake_run, tmp_path, monkeypatch):
    monkeypatch.setattr(matrix, "REVIEWS_DIR", tmp_path / "empty")
    assert matrix.load_decisions(VERSION, PAGE) == ({}, [])


def test_blank_lines_are_skipped_quietly(fake_run, reviews):
    append_lines(b"", b"   ")
    decisions, skipped = matrix.load_decisions(VERSION, PAGE)
    assert len(decisions) == 2 and skipped == []


def test_bad_lines_are_skipped_loudly(fake_run, reviews):
    no_reviewer = make_decision(fake_run[0]["finding_id"], "fail")
    del no_reviewer["reviewer"]
    append_lines(
        b'{"finding_id": "F-before-home-el0081-1.1.1", "run_id": "2026',  # line 4: half-written by a crash
        json.dumps(no_reviewer).encode(),                                   # line 5: PULSE wouldn't count it
        json.dumps(make_decision(fake_run[3]["finding_id"], "pass")).encode(),  # line 6: fine
    )
    decisions, skipped = matrix.load_decisions(VERSION, PAGE)
    assert len(skipped) == 2
    assert "line 4: not readable JSON" in skipped[0]
    assert "line 5: not a decision PULSE would count" in skipped[1]
    assert fake_run[0]["finding_id"] not in decisions  # the 1.1.1 "fail" without a reviewer doesn't count
    assert decisions[fake_run[3]["finding_id"]]["decision"] == "pass"  # lines after a bad one still count
    assert decisions[fake_run[1]["finding_id"]]["decision"] == "fail"  # and lines before it


def test_unknown_decision_is_skipped(fake_run, reviews):
    append_lines(json.dumps(make_decision(fake_run[0]["finding_id"], "maybe")).encode())
    decisions, skipped = matrix.load_decisions(VERSION, PAGE)
    assert fake_run[0]["finding_id"] not in decisions
    assert len(skipped) == 1 and "line 4" in skipped[0]


# ---------------------------------------------------------------- criterion_status

def decided(**by_id):
    """A decisions dict as load_decisions returns it, e.g. decided(a="fail", b="pass")."""
    return {fid: make_decision(fid, decision) for fid, decision in by_id.items()}


@pytest.mark.parametrize("decisions, expected", [
    (decided(a="fail", b="pass"), matrix.STATUS_FAIL),
    (decided(a="pass"), matrix.STATUS_PENDING),                      # b has no decision yet
    (decided(a="pass", b="pass"), matrix.STATUS_SUPPORTS),
    (decided(a="pass", b="not_applicable"), matrix.STATUS_SUPPORTS),
    # All N/A gives Supports under the agreed rules; open team question #8 may change this
    (decided(a="not_applicable", b="not_applicable"), matrix.STATUS_SUPPORTS),
], ids=["a fail", "one undecided", "all pass", "pass and N/A", "all N/A"])
def test_criterion_status(decisions, expected):
    assert matrix.criterion_status(["a", "b"], decisions) == expected


def test_no_findings_needs_a_human_check():
    assert matrix.criterion_status([], decided(a="fail")) == matrix.STATUS_NO_FINDINGS


def test_fail_beats_pending():
    # b is undecided, but a's human "fail" already settles the criterion
    assert matrix.criterion_status(["a", "b"], decided(a="fail")) == matrix.STATUS_FAIL


def test_decisions_on_other_findings_dont_count():
    # A fail on some other criterion's finding must not leak into this one
    assert matrix.criterion_status(["a"], decided(z="fail")) == matrix.STATUS_PENDING


# ---------------------------------------------------------------- build_matrix

CORPUS = load_corpus()
MAP508 = json.loads(corpus_tools.MAP508.read_text(encoding="utf-8"))


def build(findings, decisions):
    """build_matrix on the real 38-criterion corpus, as {sc: row} for easy lookup."""
    return {row["sc"]: row for row in matrix.build_matrix(findings, decisions, CORPUS, MAP508)}


@pytest.fixture
def rows(fake_run, reviews):
    """The fake page's matrix, built the way main() will: load the run and decisions, then build."""
    _, findings = matrix.load_run(RUN_ID)
    decisions, _ = matrix.load_decisions(VERSION, PAGE)
    return build(findings, decisions)


def test_38_rows_in_criterion_order(fake_run):
    out = matrix.build_matrix(fake_run, {}, CORPUS, MAP508)
    scs = [row["sc"] for row in out]
    assert len(scs) == 38 and scs[0] == "1.1.1" and scs[-1] == "4.1.2"
    assert scs == sorted(scs, key=lambda sc: [int(p) for p in sc.split(".")])


def test_row_details_from_corpus_and_508_map(rows):
    row = rows["1.1.1"]
    assert (row["section508"], row["name"], row["level"]) == ("E205.4", "Non-text Content", "A")


def test_criterion_without_findings(rows):
    row = rows["1.2.1"]
    assert row["status"] == matrix.STATUS_NO_FINDINGS
    assert (row["machine"], row["grey"], row["human_checks"]) == (0, 0, 0)
    assert row["finding_ids"] == row["evidence"] == row["ai_leaning"] == row["reviewed_by"] == ""


def test_bucket_counts(rows):
    assert (rows["1.1.1"]["machine"], rows["2.4.4"]["grey"], rows["3.1.1"]["human_checks"]) == (1, 1, 1)
    assert rows["2.4.4"]["machine"] == rows["2.4.4"]["human_checks"] == 0


def test_evidence_forms(rows):
    assert rows["1.1.1"]["evidence"] == f'img[src$="top_weather.gif"] (screenshot: {SHOTS}_el0081.jpg)'
    assert rows["2.4.2"]["evidence"] == "html > head > title"  # no screenshot: selector alone
    assert rows["3.1.1"]["evidence"] == f"page-level (whole page) (screenshot: {SHOTS}_page.jpg)"


def test_evidence_lines_up_with_finding_ids(fake_run):
    two = [make_finding(210, "2.4.4", selector="a.one", screenshot=None, bucket="grey"),
           make_finding(211, "2.4.4", selector="a.two", screenshot=None, bucket="grey")]
    row = build(two, {})["2.4.4"]
    assert row["finding_ids"] == "F-before-home-el0210-2.4.4; F-before-home-el0211-2.4.4"
    assert row["evidence"] == "a.one; a.two"


def test_ai_leaning_counts_none_on_its_own(rows):
    assert rows["1.1.1"]["ai_leaning"] == "AI: likely_fail 1, likely_pass 0, unsure 0, none 0"
    assert rows["2.4.2"]["ai_leaning"] == "AI: likely_fail 0, likely_pass 0, unsure 0, none 1"


def test_reviewed_by_names_the_winning_reviewers(fake_run):
    a, b = (make_finding(i, "2.4.4", selector="a", screenshot=None, bucket="grey") for i in (210, 211))
    decisions = {a["finding_id"]: make_decision(a["finding_id"], "pass", reviewer="bob"),
                 b["finding_id"]: make_decision(b["finding_id"], "pass", reviewer="alice")}
    assert build([a, b], decisions)["2.4.4"]["reviewed_by"] == "alice, bob"


def test_ai_never_sets_status(rows, fake_run):
    """Hard rule, end to end: everything automated says "fail", no human has decided -> Pending."""
    f = fake_run[0]
    assert (f["bucket"], f["scanner_status"], f["ai"]["recommendation"]) == ("machine", "violation", "likely_fail")
    assert rows["1.1.1"]["status"] == matrix.STATUS_PENDING


def test_definition_of_done_row(rows):
    """DoD #2: the 2.4.4 row shows the finding, its screenshot and who decided it."""
    row = rows["2.4.4"]
    assert row["finding_ids"] == "F-before-home-el0210-2.4.4"
    assert f"{SHOTS}_el0210.jpg" in row["evidence"]
    assert row["reviewed_by"] == "bob"  # bob's later fail beat alice's pass
    assert row["status"] == matrix.STATUS_FAIL


def test_decision_on_finding_not_in_run_is_ignored(fake_run, reviews):
    _, findings = matrix.load_run(RUN_ID)
    decisions, _ = matrix.load_decisions(VERSION, PAGE)
    stray = "F-before-home-el0999-1.1.1"  # not in this run
    with_stray = {**decisions, stray: make_decision(stray, "fail", reviewer="mallory")}
    assert build(findings, with_stray) == build(findings, decisions)


def test_grouped_by_wcag_not_mapping(fake_run):
    f = copy.deepcopy(fake_run[1])  # the 2.4.4 finding
    f["mapping"] = {"sc": "1.3.1", "name": "Info and Relationships"}  # a mapper disagreeing must not move it
    out = build([f], {})
    assert out["2.4.4"]["finding_ids"] == f["finding_id"] and out["1.3.1"]["finding_ids"] == ""


def test_unknown_bucket_warns_but_keeps_the_finding(fake_run, capsys):
    f = copy.deepcopy(fake_run[1])
    f["bucket"] = None  # e.g. a run without the real router
    out = build([f], {})
    assert len(out) == 38
    row = out["2.4.4"]
    assert row["finding_ids"] == f["finding_id"] and row["status"] == matrix.STATUS_PENDING
    assert (row["machine"], row["grey"], row["human_checks"]) == (0, 0, 0)
    assert f"WARNING {f['finding_id']}: unknown bucket None" in capsys.readouterr().err
