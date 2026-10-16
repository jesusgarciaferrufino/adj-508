"""Track E: the compliance matrix (reports/matrix.py).

Tests never touch the real data/ folder: fixtures build a tiny fake run and reviews file in a temp folder.
"""
import copy
import json

import pytest

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
