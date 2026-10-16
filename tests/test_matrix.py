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
