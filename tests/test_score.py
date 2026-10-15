"""Track S: scripts/score.py and scripts/gt_candidates.py.

The recall numbers go on slides, so each one is checked against a value worked out by hand
from a 6-row fake ground truth and fake run folders.
"""

import csv
import json

import pytest

from scripts import gt_candidates, score

# ground truth: 5 rows on home, 1 on news (news is never run)
GT_ROWS = [
    # page, wcag, finding_id                        bucket in the before/home run
    ("home", "1.1.1", "F-before-home-el0001-1.1.1"),  # machine            -> reported
    ("home", "2.4.4", "F-before-home-el0002-2.4.4"),  # grey, likely_fail  -> reported
    ("home", "1.3.1", "F-before-home-el0003-1.3.1"),  # grey, likely_pass  -> flagged only
    ("home", "3.1.1", "F-before-home-el0004-3.1.1"),  # human-only         -> flagged only
    ("home", "3.3.2", "missed"),                      # scanners never saw it
    ("news", "1.1.1", "F-before-news-el0005-1.1.1"),  # page not run
]


def finding(fid, bucket, recommendation=None):
    f = {"finding_id": fid, "wcag": [fid.rsplit("-", 1)[1]], "bucket": bucket}
    if recommendation:
        f["ai"] = {"recommendation": recommendation}
    return f


BEFORE_HOME = [
    finding("F-before-home-el0001-1.1.1", "machine"),
    finding("F-before-home-el0002-2.4.4", "grey", "likely_fail"),
    finding("F-before-home-el0003-1.3.1", "grey", "likely_pass"),
    finding("F-before-home-el0004-3.1.1", "human-only"),
    finding("F-before-home-el0009-2.4.4", "grey", "unsure"),  # not in the ground truth
]
AFTER_HOME = [
    finding("F-after-home-el0001-1.4.3", "machine"),              # false alarm
    finding("F-after-home-el0002-4.1.2", "grey", "likely_fail"),  # false alarm
    finding("F-after-home-el0003-4.1.2", "grey", "likely_pass"),  # cleared by AGAINST
    finding("F-after-home-el0004-3.1.1", "human-only"),           # human check, not an issue
]


def write_gt(path, rows=GT_ROWS):
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=score.GT_COLUMNS)
        w.writeheader()
        for page, wcag, fid in rows:
            w.writerow({"page": page, "wcag": wcag, "level": "A", "description": f"cause {wcag}",
                        "w3c_ref": f"before/reports/{page}.html {wcag}", "where": "somewhere",
                        "finding_id": fid, "verified_by": "", "notes": ""})
    return path


def write_run(runs_dir, run_id, version, page, findings, stubs=()):
    folder = runs_dir / run_id
    folder.mkdir(parents=True)
    (folder / "summary.json").write_text(json.dumps({"run_id": run_id, "version": version, "page": page,
                                                     "stubs": list(stubs)}))
    (folder / "output_findings.json").write_text(json.dumps(findings))
    return run_id


@pytest.fixture
def setup(tmp_path):
    runs = tmp_path / "runs"
    gt = write_gt(tmp_path / "ground_truth.csv")
    ids = [write_run(runs, "20261016-010000-aaaa", "before", "home", BEFORE_HOME),
           write_run(runs, "20261016-020000-bbbb", "after", "home", AFTER_HOME)]
    return gt, runs, ids


def run_score(setup, stub_grey=False):
    gt, runs, ids = setup
    return score.score(score.load_ground_truth(gt), score.runs_by_page(ids, runs), stub_grey)


def test_recall_figures_match_hand_computed_values(setup):
    s = run_score(setup)
    # 5 home rows (news not run). (a) machine + grey likely_fail = 2. (b) + grey likely_pass + human-only = 4.
    # (c) pairs 1.1.1, 2.4.4, 1.3.1, 3.1.1, 3.3.2: all but 3.3.2 surfaced = 4.
    assert s["recall"]["a"] == {"n": 2, "d": 5, "pct": 40.0}
    assert s["recall"]["b"] == {"n": 4, "d": 5, "pct": 80.0}
    assert s["recall"]["c"] == {"n": 4, "d": 5, "pct": 80.0}


def test_missed_row_counts_in_the_denominator(setup, tmp_path):
    s = run_score(setup)
    assert s["per_page"]["home"]["missed"] == 1
    assert s["recall"]["a"]["d"] == 5  # 4 matched rows + 1 missed
    assert [r["page"] for r in s["missed"]["3.3.2"]] == ["home"]

    gt, runs, ids = setup
    no_missed = write_gt(tmp_path / "no_missed.csv", [r for r in GT_ROWS if r[2] != "missed"])
    s2 = score.score(score.load_ground_truth(no_missed), score.runs_by_page(ids, runs))
    assert s2["recall"]["a"] == {"n": 2, "d": 4, "pct": 50.0}


def test_missing_page_shows_not_run_never_zero(setup):
    s = run_score(setup)
    assert s["per_page"]["news"]["status"] == score.NOT_RUN
    assert s["false_alarms"]["news"] == score.NOT_RUN
    assert s["before_pages_scored"] == ["home"]  # news rows are left out of every ratio
    md = score.format_markdown(s)
    news_line = next(line for line in md.splitlines() if line.startswith("| news |") and "GT" not in line)
    assert "not run" in news_line and "0%" not in news_line
    assert "1 of 4 Before pages" in md


def test_grey_likely_pass_is_not_an_issue():
    lp = finding("F-before-home-el0003-1.3.1", "grey", "likely_pass")
    assert not score.is_reported(lp)
    assert score.is_flagged(lp)
    assert not score.is_reported(finding("F-before-home-el0009-2.4.4", "grey", "unsure"))
    assert not score.is_reported(finding("F-before-home-el0004-3.1.1", "human-only"))
    assert score.is_reported(finding("F-before-home-el0002-2.4.4", "grey", "likely_fail"))
    assert score.is_reported(finding("F-before-home-el0001-1.1.1", "machine"))


def test_after_false_alarms_by_criterion(setup):
    s = run_score(setup)
    assert s["false_alarms"]["home"]["by_wcag"] == {"1.4.3": 1, "4.1.2": 1}
    assert s["false_alarms_total"] == 2


def test_stub_grey_counts_every_grey_as_reported_and_says_so(setup):
    s = run_score(setup, stub_grey=True)
    assert s["recall"]["a"] == {"n": 3, "d": 5, "pct": 60.0}
    assert s["false_alarms_total"] == 3
    assert "--stub-grey-counts" in score.format_markdown(s)


def test_bad_ground_truth_is_refused(tmp_path):
    with pytest.raises(score.ScoreError, match="not found"):
        score.load_ground_truth(tmp_path / "nope.csv")
    with pytest.raises(score.ScoreError, match="WCAG 2.0 A/AA"):
        score.load_ground_truth(write_gt(tmp_path / "aaa.csv", [("home", "1.4.6", "missed")]))
    with pytest.raises(score.ScoreError, match="before/home"):
        score.load_ground_truth(write_gt(tmp_path / "wrong.csv", [("home", "1.1.1", "F-before-news-el0001-1.1.1")]))


def test_two_runs_for_one_page_are_refused(setup):
    gt, runs, ids = setup
    extra = write_run(runs, "20261016-030000-cccc", "before", "home", BEFORE_HOME)
    with pytest.raises(score.ScoreError, match="two runs"):
        score.runs_by_page(ids + [extra], runs)


def test_latest_runs_picks_newest_per_page(setup):
    gt, runs, ids = setup
    newer = write_run(runs, "20261016-090000-dddd", "before", "home", BEFORE_HOME)
    assert score.latest_runs(runs) == sorted([newer, "20261016-020000-bbbb"])


def test_write_saves_dated_report(setup, tmp_path, monkeypatch, capsys):
    gt, runs, ids = setup
    monkeypatch.setattr(score, "RUNS_DIR", runs)
    monkeypatch.setattr(score, "SCORES_DIR", tmp_path / "scores")
    monkeypatch.setattr(score, "REPO", tmp_path)
    assert score.main(["--gt", str(gt), "--write"]) == 0
    saved = list((tmp_path / "scores").glob("*.md"))
    assert len(saved) == 1
    text = saved[0].read_text()
    assert "20261016-010000-aaaa" in text and "Commit:" in text


def test_gt_candidates_lists_the_click_here_link():
    rows = gt_candidates.candidates("home", "2.4.4")
    ids = [r["finding_id"] for r in rows]
    assert "F-before-home-el0210-2.4.4" in ids
    assert all(r["wcag"] == "2.4.4" for r in rows)
    assert all(len(r["html"]) <= gt_candidates.HTML_CHARS for r in rows)


def test_gt_candidates_rejects_aaa_criteria(capsys):
    assert gt_candidates.main(["--page", "home", "--wcag", "2.4.9"]) == 2
