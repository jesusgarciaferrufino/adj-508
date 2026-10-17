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
    ("home", "2.4.4", "F-before-home-el0002-2.4.4"),  # grey, likely_fail, verified -> reported
    ("home", "1.3.1", "F-before-home-el0003-1.3.1"),  # grey, likely_pass  -> flagged only
    ("home", "3.1.1", "F-before-home-el0004-3.1.1"),  # human-only         -> flagged only
    ("home", "3.3.2", "missed"),                      # scanners never saw it
    ("news", "1.1.1", "F-before-news-el0005-1.1.1"),  # page not run
]


EXAMPLE = json.loads((score.REPO / "docs" / "finding.example.json").read_text())


def finding(fid, bucket, recommendation=None, case_for="Specific argument.", verified=True):
    """A full finding in the shared format (the loader checks runs), built from the example."""
    _, version, page, el, sc = fid.split("-")
    f = json.loads(json.dumps(EXAMPLE))
    f.update(finding_id=fid, site_version=version, page=page, wcag=[sc], bucket=bucket)
    f["evidence"]["dom_index"] = int(el[2:])
    if recommendation:
        f["ai"].update(recommendation=recommendation, case_for=case_for, citations_verified=verified)
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


def write_run(runs_dir, run_id, version, page, findings, stubs=()):  # stubs=None: no "stubs" key
    folder = runs_dir / run_id
    folder.mkdir(parents=True)
    summary = {"run_id": run_id, "version": version, "page": page}
    if stubs is not None:
        summary["stubs"] = list(stubs)
    (folder / "summary.json").write_text(json.dumps(summary))
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
    assert gt_candidates.main(["--page", "home", "--wcag", "2.4.9"]) == 1


def test_unevaluated_grey_is_counted_and_warned(setup, tmp_path):
    """Skipped FOR/AGAINST must not read as a clean After site (it said 0 false alarms before)."""
    gt, runs, ids = setup
    skipped = [finding("F-after-home-el0007-4.1.2", "grey", "unsure", case_for="SKIPPED: call cap reached"),
               finding("F-after-home-el0008-1.3.1", "grey")]  # evidence call failed: no ai at all
    after = write_run(runs, "20261016-040000-eeee", "after", "news", skipped)
    s = score.score(score.load_ground_truth(gt), score.runs_by_page(ids + [after], runs))
    assert s["unevaluated"] == {"before/home": 0, "after/home": 0, "after/news": 2}
    assert s["false_alarms"]["news"]["by_wcag"] == {}  # neither counts as reported...
    md = score.format_markdown(s)
    assert "2 grey finding(s) were never evaluated" in md and "LOWER BOUNDS" in md  # ...and the report says why


def test_real_recommendations_are_not_unevaluated():
    for rec in ("likely_fail", "likely_pass", "unsure"):
        assert not score.is_unevaluated(finding("F-after-home-el0001-4.1.2", "grey", rec))
    assert not score.is_unevaluated(finding("F-after-home-el0002-1.1.1", "machine"))


def test_no_warning_when_everything_was_evaluated(setup):
    assert "never evaluated" not in score.format_markdown(run_score(setup))


def test_match_under_a_different_criterion_is_listed(tmp_path):
    runs = tmp_path / "runs"
    rid = write_run(runs, "20261016-050000-ffff", "before", "home",
                    [finding("F-before-home-el0085-4.1.2", "machine")])
    gt = write_gt(tmp_path / "gt.csv", [("home", "3.3.2", "F-before-home-el0085-4.1.2")])
    s = score.score(score.load_ground_truth(gt), score.runs_by_page([rid], runs))
    assert s["recall"]["a"]["n"] == 1  # the element was caught
    assert [(r["wcag"], r["finding_wcag"]) for r in s["other_criterion"]] == [("3.3.2", "4.1.2")]
    md = score.format_markdown(s)
    assert "Found under a different criterion" in md and "W3C 3.3.2" in md


def test_run_without_stub_tracking_says_so_above_the_tables(setup):
    gt, runs, ids = setup
    old = write_run(runs, "20261005-010000-0000", "after", "news", [], stubs=None)
    s = score.score(score.load_ground_truth(gt), score.runs_by_page(ids + [old], runs))
    md = score.format_markdown(s)
    news_row = next(line for line in md.splitlines() if line.startswith("| after/news |"))
    assert "predates stub tracking" in news_row and "none" not in news_row  # the matrix's wording
    warning = md.index("after/news predate stub tracking")
    assert warning < md.index("## Before recall") < md.index("## After false alarms")


def test_latest_runs_prefers_a_run_with_stub_tracking(tmp_path):
    runs = tmp_path / "runs"
    tracked = write_run(runs, "20261013-010000-aaaa", "before", "home", BEFORE_HOME)
    write_run(runs, "20261016-010000-bbbb", "before", "home", BEFORE_HOME, stubs=None)  # newer, but old format
    only_old = write_run(runs, "20261005-010000-cccc", "before", "news", [], stubs=None)
    assert score.latest_runs(runs) == sorted([tracked, only_old])
    s = score.score(score.load_ground_truth(write_gt(tmp_path / "gt.csv")),
                    score.runs_by_page(score.latest_runs(runs), runs))
    assert "before/news predate stub tracking" in score.format_markdown(s)  # used, but warned


def test_unverified_likely_fail_is_flagged_not_an_issue(tmp_path):
    """Issue rule (Oct 17): likely_fail counts only with verified citations."""
    runs = tmp_path / "runs2"
    before = write_run(runs, "20261016-060000-abcd", "before", "home",
                       [finding("F-before-home-el0002-2.4.4", "grey", "likely_fail", verified=False)])
    after = write_run(runs, "20261016-060100-abce", "after", "home",
                      [finding("F-after-home-el0002-4.1.2", "grey", "likely_fail", verified=False),
                       finding("F-after-home-el0003-4.1.2", "grey", "likely_fail", verified=True)])
    gt = write_gt(tmp_path / "gt2.csv", [("home", "2.4.4", "F-before-home-el0002-2.4.4")])
    s = score.score(score.load_ground_truth(gt), score.runs_by_page([before, after], runs))
    assert s["recall"]["a"]["n"] == 0  # not reported...
    assert s["recall"]["b"]["n"] == 1  # ...but flagged for a human, so not lost
    assert s["false_alarms"]["home"]["by_wcag"] == {"4.1.2": 1}  # only the verified one
    assert s["unverified_fails"] == {"before/home": 1, "after/home": 1}
    assert "flagged for a human, not reported" in score.format_markdown(s)


def test_verified_flag_must_be_true_not_just_present():
    f = finding("F-before-home-el0002-2.4.4", "grey", "likely_fail")
    f["ai"]["citations_verified"] = None
    assert not score.is_reported(f) and score.is_flagged(f)


def test_none_recommendation_is_not_evaluated(setup):
    """agents/evidence.py writes recommendation None for skipped findings (after #16's follow-up)."""
    f = finding("F-after-home-el0007-4.1.2", "grey", "likely_fail", case_for="SKIPPED: call cap reached")
    f["ai"]["recommendation"] = None
    assert score.is_unevaluated(f) and not score.is_reported(f) and score.is_flagged(f)


def test_runs_are_checked_with_the_shared_loader(setup):
    gt, runs, ids = setup
    bad = finding("F-after-home-el0005-1.1.1", "machine")
    bad["bucket"] = "Machine"  # typo the loader rejects
    rid = write_run(runs, "20261016-070000-bad1", "after", "survey", [bad])
    with pytest.raises(score.ScoreError, match="does not match the format"):
        score.runs_by_page([rid], runs)


def test_cli_errors_exit_1(tmp_path):
    assert score.main(["--gt", str(tmp_path / "missing.csv")]) == 1
