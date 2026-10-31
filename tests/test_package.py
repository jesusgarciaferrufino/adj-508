"""Track E: the evidence package (reports/package.py)."""
import html
import json
import re
import shutil
from urllib.parse import unquote

import pytest

from rag.corpus_tools import load_corpus
from reports import matrix, package

CORPUS = load_corpus()
MAP508 = {sc: {"web": "E205.4"} for sc in CORPUS}


def finding(finding_id, sc, bucket="grey", recommendation=None):
    return {"finding_id": finding_id, "wcag": [sc], "bucket": bucket,
            "scanner_details": [{"scanner": "axe-core", "rule_id": "test-rule", "type": "violations"}],
            "ai": {"recommendation": recommendation, "citations_verified": True}}


def runs_of(version="before", **findings_by_page):
    """(summary, findings) per page, in the order given, as acr.load_site returns them."""
    return [({"version": version, "page": page}, fs) for page, fs in findings_by_page.items()]


def fail(reviewer="jesus"):
    return {"decision": "fail", "reviewer": reviewer}


# ---------------------------------------------------------------- site matrix

@pytest.fixture
def site_rows():
    runs = runs_of(home=[finding("h1", "2.4.4")],
                   news=[finding("n1", "2.4.4", "machine")],
                   tickets=[finding("t1", "1.1.1", "grey", "likely_fail")],  # the AI leans fail; nobody decided
                   survey=[])
    return package.build_site_matrix(runs, {"home": {"h1": fail()}}, CORPUS, MAP508)


def test_site_matrix_has_a_row_per_criterion_in_order_and_a_column_per_page(site_rows):
    assert len(site_rows) == 38 and [r["sc"] for r in site_rows][:3] == ["1.1.1", "1.2.1", "1.2.2"]
    assert tuple(site_rows[0]) == package.SITE_COLUMNS
    assert package.SITE_COLUMNS[-4:] == ("home", "news", "tickets", "survey")
    r = site_rows[0]
    assert (r["section508"], r["name"], r["level"]) == ("E205.4", "Non-text Content", "A")


def test_site_matrix_cell_is_each_page_status_from_human_decisions_only(site_rows):
    by_sc = {r["sc"]: r for r in site_rows}
    assert [by_sc["2.4.4"][p] for p in ("home", "news", "tickets", "survey")] == [
        matrix.STATUS_FAIL, matrix.STATUS_PENDING, matrix.STATUS_NO_FINDINGS, matrix.STATUS_NO_FINDINGS]
    assert by_sc["1.1.1"]["tickets"] == matrix.STATUS_PENDING  # AI likely_fail sets nothing


def test_site_matrix_test_column_names_the_checks_behind_the_findings_on_every_page():
    def found(finding_id, scanner, rule_id):
        return {**finding(finding_id, "2.4.4"), "scanner_details": [{"scanner": scanner, "rule_id": rule_id}]}
    runs = runs_of(home=[found("h1", "axe-core", "link-name")],
                   news=[found("n1", "pa11y", "WCAG2AA.Principle2.Guideline2_4.2_4_4.H77,H78,H79,H80,H81")],
                   tickets=[], survey=[])
    by_sc = {r["sc"]: r for r in package.build_site_matrix(runs, {}, CORPUS, MAP508)}
    assert package.SITE_COLUMNS.index("test") == package.SITE_COLUMNS.index("level") + 1
    assert by_sc["2.4.4"]["test"] == "axe-core: link-name; Pa11y: H77,H78,H79,H80,H81"  # home's and news's
    assert by_sc["1.2.1"]["test"] == matrix.NO_TEST


def test_site_matrix_csv_has_the_columns_and_a_line_per_criterion(site_rows):
    lines = package.format_site_matrix_csv(site_rows).splitlines()
    assert lines[0] == ",".join(package.SITE_COLUMNS) and len(lines) == 39


def test_site_matrix_md_says_where_status_comes_from_and_shows_each_page(site_rows):
    md = package.format_site_matrix_md(site_rows, "Site compliance matrix: before", notes=["a | b"])
    assert md.startswith("# Site compliance matrix: before")
    assert "Status comes only from human reviewers' decisions" in md
    assert "> **Warning:** a \\| b" in md
    assert "| 508 | Criterion | Level | Test | home | news | tickets | survey |" in md
    assert "it does not list checks that ran and found nothing" in md and "all four pages" in md
    row = next(line for line in md.splitlines() if line.startswith("| E205.4 | 2.4.4 "))
    assert row.count("|") == 9 and matrix.STATUS_FAIL in row


# ---------------------------------------------------------------- screenshots

@pytest.fixture
def shots_repo(tmp_path, monkeypatch):
    """A fake repo with two screenshots in data/findings/shots/, and an empty package folder."""
    monkeypatch.setattr(package, "REPO", tmp_path / "repo")
    monkeypatch.setattr(package, "SHOTS_DIR", tmp_path / "repo" / "data" / "findings" / "shots")
    package.SHOTS_DIR.mkdir(parents=True)
    for name in ("before_home_el0210.jpg", "before_home_page.jpg"):
        (package.SHOTS_DIR / name).write_bytes(b"jpg " + name.encode())
    return tmp_path / "pkg"


def shot_finding(finding_id, screenshot):
    return {"finding_id": finding_id, "evidence": {"screenshot": screenshot, "selector": "a.one"}}


def test_copy_shots_copies_each_screenshot_once_and_says_where_it_went(shots_repo):
    shared = "data/findings/shots/before_home_page.jpg"  # page-level findings share one screenshot
    findings = [shot_finding("a", "data/findings/shots/before_home_el0210.jpg"), shot_finding("b", shared),
                shot_finding("c", shared), shot_finding("d", None)]  # d has no screenshot (e.g. <title>)
    copied, notes = package.copy_shots(findings, shots_repo)
    assert copied == {"data/findings/shots/before_home_el0210.jpg": "shots/before_home_el0210.jpg",
                      shared: "shots/before_home_page.jpg"}
    assert (shots_repo / "shots" / "before_home_page.jpg").read_bytes() == b"jpg before_home_page.jpg"
    assert sorted(p.name for p in (shots_repo / "shots").iterdir()) == ["before_home_el0210.jpg",
                                                                         "before_home_page.jpg"]
    assert notes == []


@pytest.mark.parametrize("screenshot,why", [
    ("data/findings/shots/gone.jpg", "missing"),
    ("data/findings/shots/../../../secret.txt", "outside data/findings/shots/"),  # run data can't pull other files
    ("/etc/hosts", "outside data/findings/shots/"),
])
def test_copy_shots_skips_a_missing_or_outside_screenshot_with_a_note(shots_repo, screenshot, why):
    copied, notes = package.copy_shots([shot_finding("F-x", screenshot)], shots_repo)
    assert copied == {}
    assert len(notes) == 1 and "F-x" in notes[0] and why in notes[0] and "selector" in notes[0]


def test_shot_href_links_the_copy_from_the_package_root_or_a_subfolder():
    copied = {"data/findings/shots/a.jpg": "shots/a.jpg"}
    assert package.shot_href(copied)("data/findings/shots/a.jpg") == "shots/a.jpg"
    assert package.shot_href(copied, depth=1)("data/findings/shots/a.jpg") == "../shots/a.jpg"


def test_copy_shots_notes_a_missing_screenshot_once_even_when_findings_share_it(shots_repo):
    gone = "data/findings/shots/before_news_page.jpg"
    _, notes = package.copy_shots([shot_finding(f"F-{n}", gone) for n in range(15)], shots_repo)
    assert len(notes) == 1 and "F-0" in notes[0]


# ---------------------------------------------------------------- evidence pages

def full_finding(**ai):
    """A grey finding with everything an evidence page shows; ai fields as given."""
    return {
        "finding_id": "F-before-home-el0210-2.4.4", "site_version": "before", "page": "home",
        "sources": ["pa11y"], "scanner_status": "notice", "rule_id": "WCAG2AA.H77", "wcag": ["2.4.4"],
        "bucket": "grey", "route_reason": "rule 4: scanner can flag but not judge",
        "evidence": {"selector": "#main > a:nth-child(3)", "html_snippet": '<a href="bees.html">Click here</a>',
                     "screenshot": "data/findings/shots/before_home_el0210.jpg",
                     "page_url": "https://www.w3.org/WAI/demos/bad/before/home.html",
                     "context_text": "Killer bees. Click here."},
        "scanner_details": [{"scanner": "pa11y", "rule_id": "WCAG2AA.H77", "type": "notice",
                             "message": "Check that the link text <a> identifies the purpose"}],
        "ai": {"case_for": "Says nothing about bees.", "case_against": "No honest case for a pass.",
               "recommendation": "likely_fail", "citations_verified": True, "model_id": "test-model",
               "prompt_version": "for_v1+against_v1", **ai},
    }


DECIDED = {"decision": "fail", "reviewer": "jesus", "decided_at": "2026-10-23T20:44:12Z",
           "comment": "Click here is not descriptive."}


def page(finding=None, decision=DECIDED, shot="../shots/before_home_el0210.jpg"):
    return package.format_evidence_html(finding or full_finding(), decision, CORPUS, shot)


def test_evidence_page_is_an_accessible_page_named_for_its_finding():
    p = page()
    assert p.startswith("<!doctype html>") and '<html lang="en">' in p
    assert "<title>Evidence: F-before-home-el0210-2.4.4</title>" in p and "<h1>" in p and "<main>" in p
    assert "2.4.4 Link Purpose (In Context) (Level A)" in p


def test_evidence_page_shows_the_human_decision_with_reviewer_date_and_comment():
    p = page()
    assert "Human decision" in p and "Fail" in p and "jesus" in p and "2026-10-23T20:44:12Z" in p
    assert "Click here is not descriptive." in p


def test_an_undecided_finding_says_it_is_pending_human_review():
    p = page(decision=None)
    assert "Pending human review" in p and "jesus" not in p


def test_evidence_page_shows_the_screenshot_with_alt_text_or_the_selector_when_there_is_none():
    assert '<img src="../shots/before_home_el0210.jpg" alt="Screenshot of the element for ' \
           'F-before-home-el0210-2.4.4"' in page()
    p = page(shot=None)
    assert "<img" not in p and "No screenshot" in p and "#main &gt; a:nth-child(3)" in p


def test_evidence_page_escapes_everything_that_came_from_data():
    p = page()
    assert "&lt;a href=&quot;bees.html&quot;&gt;Click here&lt;/a&gt;" in p   # the HTML snippet, as text
    assert "link text &lt;a&gt; identifies" in p                              # a scanner message
    assert '<a href="bees.html">' not in p


def test_evidence_page_lists_scanner_messages_in_a_table_with_headers():
    p = page()
    assert "<caption>Scanner messages</caption>" in p and '<th scope="col">Message</th>' in p
    assert "WCAG2AA.H77" in p and "notice" in p


def test_evidence_page_labels_the_ai_cases_as_analysis_not_a_decision():
    p = page()
    assert "AI analysis (not a decision)" in p
    assert p.index("AI analysis (not a decision)") < p.index("Says nothing about bees.")
    assert "No honest case for a pass." in p and "likely_fail" in p and "test-model" in p
    assert "<dt>Citations verified</dt><dd>yes</dd>" in p


@pytest.mark.parametrize("ai,shown", [
    ({"case_for": "", "case_against": "", "recommendation": None}, "Not evaluated by AI"),
    ({"case_for": "SKIPPED: call budget reached", "case_against": "", "recommendation": None},
     "Not evaluated by AI (SKIPPED: call budget reached)"),
])
def test_evidence_page_says_when_the_ai_did_not_evaluate(ai, shown):
    p = page(full_finding(**ai))
    assert shown in p and "Case for" not in p


def test_evidence_filename_is_the_finding_id_and_refuses_anything_else():
    assert package.evidence_filename("F-before-home-el0210-2.4.4") == "F-before-home-el0210-2.4.4.html"
    for bad in ("../index", "F-before-home-el0210-2.4.4/../../x", ""):
        with pytest.raises(package.PackageError):
            package.evidence_filename(bad)


def test_evidence_page_links_the_page_url_only_when_it_is_a_web_address():
    assert '<a href="https://www.w3.org/WAI/demos/bad/before/home.html">' in page()
    f = full_finding()
    f["evidence"]["page_url"] = "javascript:alert(1)"
    assert "javascript:alert(1)" in page(f) and 'href="javascript' not in page(f)


# ---------------------------------------------------------------- index

SCORES_MD = """# ADJ scores, 2026-10-29

## Before recall

| Figure | Value |
| --- | --- |
| (a) Issue level, reported as an issue | 41.5% (54 of 130) |
| (b) Issue level, reported or flagged for a human | 72.3% (94 of 130) |
| (c) Criterion coverage, detected (page and criterion pairs) | 61.0% (47 of 77) |
| (c+) Criterion coverage including page-level 'check manually' reminders (not detections) | 80.5% (62 of 77) |

## After false alarms

Total on 4 of 4 After pages: 3
"""


def test_latest_scores_reads_the_headline_from_the_newest_scores_file(tmp_path):
    (tmp_path / "2026-10-22.md").write_text(SCORES_MD.replace("41.5%", "OLD"))
    (tmp_path / "2026-10-29.md").write_text(SCORES_MD)
    s = package.latest_scores(tmp_path)
    assert s["file"] == "2026-10-29.md"
    assert s["figures"] == [("(a) Issue level, reported as an issue", "41.5% (54 of 130)"),
                            ("(b) Issue level, reported or flagged for a human", "72.3% (94 of 130)"),
                            ("(c) Criterion coverage, detected (page and criterion pairs)", "61.0% (47 of 77)"),
                            ("After false alarms", "Total on 4 of 4 After pages: 3")]


def test_latest_scores_with_no_folder_or_no_file_is_none_and_an_unknown_format_has_no_figures(tmp_path):
    assert package.latest_scores(tmp_path / "nope") is None and package.latest_scores(tmp_path) is None
    (tmp_path / "2026-10-30.md").write_text("# something else\n")
    assert package.latest_scores(tmp_path) == {"file": "2026-10-30.md", "figures": [], "caveats": []}


def contents(**overrides):
    base = {"package_id": "20261029-120000-ab12", "report_date": "2026-10-29", "commit": "e1c2ba9",
            "run_ids": {("before", "home"): "run-bh", ("after", "home"): "run-ah"},
            "acr_counts": {"Pending human review": 37, "Does Not Support": 1}, "backlog_issues": 8,
            "evidence": [("F-before-home-el0210-2.4.4", "ACR, backlog"), ("F-before-news-el0007-1.1.1", "backlog")],
            "notes": [], "scores": {"file": "2026-10-29.md", "figures": [("(a) Issue level", "41.5%")]}}
    return {**base, **overrides}


def test_index_md_says_what_the_package_is_and_traces_to_runs_and_commit():
    md = package.format_index_md(contents())
    assert md.startswith("# ADJ evidence package: CityLights")
    assert "20261029-120000-ab12" in md and "`e1c2ba9`" in md
    assert "Conformance levels and statuses come only from human reviewers' decisions" in md
    assert "| before | home | run-bh |" in md and "| after | home | run-ah |" in md


def test_index_md_links_every_part_of_the_package_relatively():
    md = package.format_index_md(contents())
    for link in ("(acr.html)", "(acr.md)", "(backlog.md)", "(backlog.csv)", "(site_matrix_before.md)",
                 "(site_matrix_before.csv)", "(site_matrix_after.md)", "(matrix_before_home.md)",
                 "(matrix_after_home.csv)", "(evidence/F-before-home-el0210-2.4.4.html)"):
        assert link in md, link
    assert "37 Pending human review" in md and "1 Does Not Support" in md and "8 issues" in md
    assert "F-before-news-el0007-1.1.1 (backlog)" in md


def test_index_md_shows_the_score_headline_and_where_it_came_from_or_says_there_is_none():
    md = package.format_index_md(contents())
    assert "| (a) Issue level | 41.5% |" in md and "docs/scores/2026-10-29.md" in md
    assert "may not be this package's runs" in md
    assert "No benchmark scores yet" in package.format_index_md(contents(scores=None))


def test_index_md_shows_warnings():
    md = package.format_index_md(contents(notes=["Run r1 (news): stand-ins"]))
    assert "> **Warning:** Run r1 (news): stand-ins" in md


def test_index_html_is_an_accessible_page_with_the_same_links_and_escaped_text():
    p = package.format_index_html(contents(notes=["<b>x</b>"]))
    assert p.startswith("<!doctype html>") and '<html lang="en">' in p and "<main>" in p
    assert '<a href="acr.html">' in p and '<a href="evidence/F-before-home-el0210-2.4.4.html">' in p
    assert '<th scope="col">Run</th>' in p and "<caption>" in p
    assert "&lt;b&gt;x&lt;/b&gt;" in p and "<b>x</b>" not in p


# ---------------------------------------------------------------- main: the whole package from 8 fake runs

@pytest.fixture
def eight_runs(tmp_path, monkeypatch):
    """4 Before + 4 After runs in temp folders. Before home: one grey 2.4.4 finding failed by jesus (its
    screenshot exists). Before news: one machine 1.1.1 finding nobody decided (its screenshot is missing).
    Returns the 8 run IDs."""
    from tests.test_acr import example_finding, write_run
    monkeypatch.setattr(matrix, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(matrix, "REVIEWS_DIR", tmp_path / "reviews")
    monkeypatch.setattr(matrix, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(package, "REPO", tmp_path / "repo")
    monkeypatch.setattr(package, "SHOTS_DIR", tmp_path / "repo" / "data" / "findings" / "shots")
    monkeypatch.setattr(package, "SCORES_DIR", tmp_path / "no-scores")
    package.SHOTS_DIR.mkdir(parents=True)
    (package.SHOTS_DIR / "before_home_el0210.jpg").write_bytes(b"jpg")
    home = example_finding("before", "home", 210, "2.4.4")
    news = example_finding("before", "news", 7, "1.1.1")
    news["bucket"] = "machine"
    ids = []
    for version in ("before", "after"):
        for p in ("home", "news", "tickets", "survey"):
            found = {("before", "home"): [home], ("before", "news"): [news]}.get((version, p), [])
            write_run(matrix.RUNS_DIR, f"run-{version}-{p}", version, p, found)
            ids.append(f"run-{version}-{p}")
    matrix.REVIEWS_DIR.mkdir()
    (matrix.REVIEWS_DIR / "before_home.jsonl").write_text(json.dumps(
        {"finding_id": home["finding_id"], "run_id": "run-before-home", "reviewer": "jesus", "decision": "fail",
         "comment": "Click here is not descriptive.", "decided_at": "2026-10-23T20:44:12Z"}) + "\n")
    return ids


def the_package():
    [folder] = [p for p in matrix.REPORTS_DIR.iterdir()]
    return folder


def test_main_writes_every_file_of_the_package(eight_runs):
    assert package.main(["--runs", *eight_runs]) == 0
    folder = the_package()
    names = {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()}
    expected = {"index.html", "index.md", "acr.md", "acr.html", "backlog.md", "backlog.csv",
                "site_matrix_before.md", "site_matrix_before.csv", "site_matrix_after.md", "site_matrix_after.csv",
                *(f"matrix_{v}_{p}.{ext}" for v in ("before", "after") for p in ("home", "news", "tickets", "survey")
                  for ext in ("md", "csv")),
                "evidence/F-before-home-el0210-2.4.4.html", "evidence/F-before-news-el0007-1.1.1.html",
                "shots/before_home_el0210.jpg"}
    assert names == expected


def test_main_package_says_who_decided_and_flags_the_missing_screenshot(eight_runs):
    package.main(["--runs", *eight_runs])
    folder = the_package()
    acr_row = next(line for line in (folder / "acr.md").read_text().splitlines() if line.startswith("| 2.4.4"))
    assert "Does Not Support" in acr_row and "(shots/before_home_el0210.jpg)" in acr_row
    index = (folder / "index.md").read_text()
    assert "before_news_el0007.jpg" in index and "missing" in index  # the warning is on the front page
    news_page = (folder / "evidence" / "F-before-news-el0007-1.1.1.html").read_text()
    assert "No screenshot" in news_page and "Pending human review" in news_page


LINK = re.compile(r'(?:href|src)="([^"]+)"|\]\(([^)]+)\)')


def test_every_link_in_the_package_is_relative_and_resolves_inside_it(eight_runs):
    package.main(["--runs", *eight_runs])
    folder = the_package().resolve()
    checked = 0
    for page in [*folder.rglob("*.html"), *folder.rglob("*.md")]:
        for m in LINK.finditer(page.read_text()):
            href = html.unescape(unquote(m.group(1) or m.group(2)))
            if href.startswith(("https://", "http://")):
                continue  # W3C technique and Understanding pages, and the live page URL
            target = (page.parent / href).resolve()
            assert target.is_relative_to(folder) and target.exists(), f"{page.name}: {href}"
            checked += 1
    assert checked > 20


def test_main_on_an_incomplete_site_writes_nothing_and_exits_1(eight_runs, capsys):
    assert package.main(["--runs", *eight_runs[:7]]) == 1  # no After survey run
    err = capsys.readouterr().err
    assert err.startswith("ERROR") and "after" in err and "survey" in err
    assert not matrix.REPORTS_DIR.exists() or not any(matrix.REPORTS_DIR.iterdir())


def test_main_that_fails_while_writing_leaves_no_partial_package(eight_runs, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(package, "format_index_html", boom)
    assert package.main(["--runs", *eight_runs]) == 1
    assert "disk full" in capsys.readouterr().err
    assert not any(matrix.REPORTS_DIR.iterdir())


# ---------------------------------------------------------------- audit follow-ups (Oct 27)

def test_a_bug_while_writing_still_leaves_no_partial_package(eight_runs, monkeypatch):
    def bug(*args, **kwargs):
        raise KeyError("not a known error")
    monkeypatch.setattr(package, "format_index_html", bug)
    with pytest.raises(KeyError):  # a bug still shows its traceback...
        package.main(["--runs", *eight_runs])
    assert not any(matrix.REPORTS_DIR.iterdir())  # ...but leaves nothing behind


def test_no_file_in_the_package_points_at_repo_paths(eight_runs):
    package.main(["--runs", *eight_runs])
    for path in the_package().rglob("*"):
        if path.suffix in (".md", ".csv", ".html"):
            for line in path.read_text().splitlines():  # only the missing-screenshot warning names the repo file
                assert "data/findings" not in line or "is missing" in line, f"{path.name}: {line}"


def test_backlog_csv_in_the_package_names_the_copied_screenshot(eight_runs):
    package.main(["--runs", *eight_runs])
    csv_text = (the_package() / "backlog.csv").read_text()
    assert "F-before-home-el0210-2.4.4 (home, Confirmed (human)): shots/before_home_el0210.jpg" in csv_text


def test_each_per_page_matrix_shows_its_own_pages_warnings(eight_runs):
    with (matrix.REVIEWS_DIR / "before_home.jsonl").open("a") as fh:
        fh.write("not json\n")
    package.main(["--runs", *eight_runs])
    folder = the_package()
    assert "Skipped reviews" in (folder / "matrix_before_home.md").read_text()
    assert "Skipped reviews" not in (folder / "matrix_before_news.md").read_text()


def test_a_finding_with_no_screenshot_shows_its_selector_everywhere(eight_runs):
    from tests.test_acr import example_finding, write_run
    tickets = example_finding("before", "tickets", 3, "2.4.2")
    tickets["bucket"], tickets["evidence"]["screenshot"] = "machine", None  # e.g. <title>
    shutil.rmtree(matrix.RUNS_DIR / "run-before-tickets")
    write_run(matrix.RUNS_DIR, "run-before-tickets", "before", "tickets", [tickets])
    assert package.main(["--runs", *eight_runs]) == 0
    p = (the_package() / "evidence" / "F-before-tickets-el0003-2.4.2.html").read_text()
    assert "No screenshot" in p and "#content a" in p
    assert "el0003" not in (the_package() / "index.md").read_text().split("## Runs")[0]  # no warning: none expected


def test_copy_shots_refuses_a_symlink_inside_shots_that_points_outside(shots_repo, tmp_path):
    (tmp_path / "secret.txt").write_text("secret")
    (package.SHOTS_DIR / "link.jpg").symlink_to(tmp_path / "secret.txt")
    copied, notes = package.copy_shots([shot_finding("F-x", "data/findings/shots/link.jpg")], shots_repo)
    assert copied == {} and "outside data/findings/shots/" in notes[0]


def test_latest_scores_ignores_files_that_are_not_dated_and_keeps_the_bound_warnings(tmp_path):
    warning = ("**Warning: 12 grey finding(s) were never evaluated by FOR/AGAINST** (skipped), "
               "so (a) and the After false alarms below are LOWER BOUNDS, not final numbers.")
    (tmp_path / "2026-10-29.md").write_text(SCORES_MD.replace("## Before recall", warning + "\n\n## Before recall"))
    (tmp_path / "README.md").write_text("# How to read these\n")  # sorts after the dates; must not be picked
    s = package.latest_scores(tmp_path)
    assert s["file"] == "2026-10-29.md" and len(s["figures"]) == 4
    assert s["caveats"] == [warning.replace("**", "")]
    md = package.format_index_md(contents(scores=s))
    assert "> **Not final:** Warning: 12 grey finding(s)" in md and "LOWER BOUNDS" in md
    assert "Not final:</strong> Warning: 12 grey" in package.format_index_html(contents(scores=s))


@pytest.mark.parametrize("finding_kw,ai,shown,hidden", [
    ({"bucket": "machine"}, {}, "FOR/AGAINST runs only on grey-area findings, and this one is machine", "Case for"),
    ({}, {"model_id": None}, "<dt>Model</dt><dd>not recorded</dd>", "None"),
    ({}, {"recommendation": None}, "<dt>AI leaning</dt><dd>not evaluated</dd>", "None"),
])
def test_evidence_page_ai_wording_never_prints_none(finding_kw, ai, shown, hidden):
    f = {**full_finding(**ai), **finding_kw}
    p = page(f)
    assert shown in p and hidden not in p.split("AI analysis (not a decision)")[1]


def test_evidence_and_index_links_are_url_encoded():
    assert '<img src="../shots/a%20b%23c.jpg"' in page(shot="../shots/a b#c.jpg")
