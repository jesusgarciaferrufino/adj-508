"""Track E: the draft ACR (reports/acr.py).

Tests never touch the real data/ folder: the `site` fixture builds fake runs and reviews in a temp folder.
"""
import copy
import datetime
import itertools
import json
import os
import re
from urllib.parse import unquote

import pytest

from rag.corpus_tools import load_corpus
from reports import acr, matrix
from reports.matrix import STATUS_FAIL, STATUS_NO_FINDINGS, STATUS_NOT_APPLICABLE, STATUS_PENDING, STATUS_SUPPORTS
from scanners.findings import REPO

F, S, N, P, X = STATUS_FAIL, STATUS_SUPPORTS, STATUS_NOT_APPLICABLE, STATUS_PENDING, STATUS_NO_FINDINGS
LEVELS = {acr.LEVEL_SUPPORTS, acr.LEVEL_PARTIAL, acr.LEVEL_FAIL, acr.LEVEL_NOT_APPLICABLE, acr.LEVEL_PENDING}
EXAMPLE = json.loads((REPO / "docs" / "finding.example.json").read_text())
MODEL = "test-model"  # a placeholder: never a real model ID in code (CONTEXT.md)


@pytest.mark.parametrize("page_statuses,level", [
    # A human-confirmed fail decides the row; pages still Pending can't undo it
    ([F, P, P, P], acr.LEVEL_FAIL),
    ([F, F, F, F], acr.LEVEL_FAIL),
    ([F, N, P, X], acr.LEVEL_FAIL),
    # Only a page a human confirmed as Supports makes it Partially Supports
    ([F, S, P, P], acr.LEVEL_PARTIAL),
    ([F, F, S, S], acr.LEVEL_PARTIAL),
    # No fail: settled only when every page is
    ([S, S, S, S], acr.LEVEL_SUPPORTS),
    ([S, N, N, N], acr.LEVEL_SUPPORTS),
    ([N, N, N, N], acr.LEVEL_NOT_APPLICABLE),
    # Any page still undecided, or with no findings for a human to decide, keeps the row Pending
    ([S, P, S, S], acr.LEVEL_PENDING),
    ([S, X, S, S], acr.LEVEL_PENDING),
    ([N, N, N, X], acr.LEVEL_PENDING),
    ([P, P, P, P], acr.LEVEL_PENDING),
    ([X, X, X, X], acr.LEVEL_PENDING),
    ([], acr.LEVEL_PENDING),
])
def test_acr_level(page_statuses, level):
    assert acr.acr_level(page_statuses) == level


def test_acr_level_only_ever_gives_the_five_levels_and_never_not_evaluated():
    """Every combination of page statuses for 0 to 4 pages: nothing outside LEVELS can come out."""
    statuses = (F, S, N, P, X)
    seen = {acr.acr_level(list(c)) for n in range(5) for c in itertools.product(statuses, repeat=n)}
    assert seen == LEVELS  # every level is reachable, and nothing else is
    assert "Not Evaluated" not in seen  # VPAT 2.5 reserves it for AAA criteria


# ---------------------------------------------------------------- findings_by_criterion

def run_of(page, *sc_and_ids):
    """What matrix.load_run returns for one page: (summary, findings). Only wcag and finding_id matter here."""
    findings = [{"finding_id": fid, "wcag": [sc]} for sc, fid in sc_and_ids]
    return {"version": "before", "page": page}, findings


def test_findings_by_criterion_groups_by_criterion_then_page():
    runs = [
        run_of("home", ("2.4.4", "h1"), ("2.4.4", "h2"), ("1.1.1", "h3")),
        run_of("news", ("2.4.4", "n1")),
        run_of("tickets"),  # no findings at all
    ]
    grouped = acr.findings_by_criterion(runs)
    assert {sc: {page: [f["finding_id"] for f in fs] for page, fs in pages.items()}
            for sc, pages in grouped.items()} == {
        "2.4.4": {"home": ["h1", "h2"], "news": ["n1"]},  # a page with none for it is simply absent
        "1.1.1": {"home": ["h3"]},
    }


def test_findings_by_criterion_with_no_runs_is_empty():
    assert acr.findings_by_criterion([]) == {}


# ---------------------------------------------------------------- criterion_level

PAGES = ("home", "news", "tickets", "survey")


def decided(decision, reviewer="jesus"):
    """The part of a data/reviews line that criterion_status reads."""
    return {"decision": decision, "reviewer": reviewer}


def test_criterion_level_one_confirmed_fail_beats_pending_pages():
    """The real 2.4.4 case: home has a human fail; news and tickets are undecided; survey has no findings."""
    by_page = {"home": [{"finding_id": "h1"}, {"finding_id": "h2"}],
               "news": [{"finding_id": "n1"}],
               "tickets": [{"finding_id": "t1"}]}
    decisions = {"home": {"h1": decided("fail")}}  # h2 undecided: one fail is enough for home
    assert acr.criterion_level(by_page, decisions, PAGES) == acr.LEVEL_FAIL


def test_criterion_level_ignores_bucket_and_ai():
    """AI never sets a level: a machine finding with a verified AI likely_fail and no human decision is Pending."""
    finding = {"finding_id": "h1", "bucket": "machine",
               "ai": {"recommendation": "likely_fail", "citations_verified": True}}
    assert acr.criterion_level({"home": [finding]}, {}, PAGES) == acr.LEVEL_PENDING


# ---------------------------------------------------------------- criterion_evidence

def finding_at(finding_id, selector, screenshot):
    """The part of a finding the Evidence column reads."""
    return {"finding_id": finding_id, "evidence": {"selector": selector, "screenshot": screenshot}}


def test_criterion_evidence_lists_decided_findings_with_screenshot_and_reviewer():
    by_page = {"home": [finding_at("h1", "a.one", "data/findings/shots/before_home_el0210.jpg"),
                        finding_at("h2", "a.two", "data/findings/shots/before_home_el0213.jpg")],
               "news": [finding_at("n1", "html > head > title", None)]}  # <title>: nothing to photograph
    decisions = {"home": {"h1": decided("fail", "jesus")},  # h2 is undecided: not evidence for anything yet
                 "news": {"n1": decided("pass", "alice")}}
    assert acr.criterion_evidence("2.4.4", acr.LEVEL_PARTIAL, by_page, decisions) == [
        {"finding_id": "h1", "page": "home", "decision": "fail", "reviewer": "jesus",
         "selector": "a.one", "screenshot": "data/findings/shots/before_home_el0210.jpg"},
        {"finding_id": "n1", "page": "news", "decision": "pass", "reviewer": "alice",
         "selector": "html > head > title", "screenshot": None},
    ]


def test_a_row_with_a_level_but_no_evidence_is_an_error():
    """A conformance level nobody can trace to a decided finding is a bug: the generator must fail."""
    with pytest.raises(acr.AcrError, match="2.4.4"):
        acr.criterion_evidence("2.4.4", acr.LEVEL_FAIL, {"home": [finding_at("h1", "a", None)]}, {})


def test_a_pending_row_needs_no_evidence():
    assert acr.criterion_evidence("2.4.4", acr.LEVEL_PENDING, {"home": [finding_at("h1", "a", None)]}, {}) == []


# ---------------------------------------------------------------- criterion_remarks

def machine(finding_id):
    return {"finding_id": finding_id, "bucket": "machine"}


def grey(finding_id, recommendation, verified=True):
    return {"finding_id": finding_id, "bucket": "grey",
            "ai": {"case_for": "FOR ...", "recommendation": recommendation, "citations_verified": verified}}


def test_remarks_name_confirmed_failures_by_page_and_count_what_is_pending():
    by_page = {"home": [machine("h1"), machine("h2")], "news": [machine("n1")], "tickets": [machine("t1")]}
    decisions = {"home": {"h1": decided("fail")}}  # everything else is undecided
    remarks = acr.criterion_remarks(by_page, decisions)
    assert "Confirmed failures: home (1)." in remarks
    assert "Pending human review: home 1, news 1, tickets 1." in remarks
    assert "AI analysis" not in remarks  # no grey findings, so the AI evaluated nothing


def test_remarks_put_the_ai_leaning_under_its_own_label():
    by_page = {"home": [grey("h1", "likely_fail"), grey("h2", "unsure")]}
    remarks = acr.criterion_remarks(by_page, {})
    label = "AI analysis (not a decision):"
    assert f"{label} likely_fail 1, unverified_fail 0, likely_pass 0, unsure 1, none 0" in remarks
    assert remarks.index(label) < remarks.index("likely_fail")  # AI words only ever come after the label


# ---------------------------------------------------------------- reviewer_comments

def reviewed(decision, reviewer, comment, decided_at):
    """A data/reviews line with a comment and a time (criterion_status ignores both)."""
    return {"decision": decision, "reviewer": reviewer, "comment": comment, "decided_at": decided_at}


def test_reviewer_comments_are_fails_first_newest_first_at_most_two():
    by_page = {p: [machine(f"{p}1")] for p in PAGES}
    decisions = {
        "home": {"home1": reviewed("fail", "jesus", "A", "2026-10-22T10:00:00Z")},
        "news": {"news1": reviewed("pass", "alice", "C", "2026-10-24T10:00:00Z")},  # newest, but a pass
        "tickets": {"tickets1": reviewed("fail", "bob", "", "2026-10-25T10:00:00Z")},  # no comment: skipped
        "survey": {"survey1": reviewed("fail", "daniel", "B", "2026-10-23T10:00:00Z")},
    }
    # Fails come before passes, newest first within each; two at most, so alice's pass is cut
    assert acr.reviewer_comments(by_page, decisions) == ['daniel: "B"', 'jesus: "A"']


def test_reviewer_comments_fill_up_with_passes_when_there_are_few_fails():
    by_page = {"home": [machine("h1")], "news": [machine("n1")]}
    decisions = {"home": {"h1": reviewed("fail", "jesus", "A", "2026-10-22T10:00:00Z")},
                 "news": {"n1": reviewed("pass", "alice", "C", "2026-10-24T10:00:00Z")}}
    assert acr.reviewer_comments(by_page, decisions) == ['jesus: "A"', 'alice: "C"']


def test_reviewer_comments_with_no_comments_is_empty():
    assert acr.reviewer_comments({"home": [machine("h1")]}, {"home": {"h1": decided("fail")}}) == []


# ---------------------------------------------------------------- build_acr


def fake_before_site():
    """4 Before runs. 2.4.4 has findings on home (one human-failed) and news; 1.1.1 has one, undecided."""
    def f(fid, sc, bucket="machine"):
        return {"finding_id": fid, "wcag": [sc], "bucket": bucket,
                "evidence": {"selector": f"#{fid}", "screenshot": f"data/findings/shots/{fid}.jpg"}}
    findings = {"home": [f("h1", "2.4.4"), f("h2", "1.1.1")], "news": [f("n1", "2.4.4")], "tickets": [], "survey": []}
    runs = [({"version": "before", "page": p}, fs) for p, fs in findings.items()]
    decisions = {"home": {"h1": reviewed("fail", "jesus", "Click here is not descriptive.", "2026-10-23T20:44:12Z")}}
    return runs, decisions


def test_build_acr_has_a_row_for_each_of_the_38_criteria_in_the_vpat_columns():
    runs, decisions = fake_before_site()
    rows = acr.build_acr(runs, decisions, load_corpus())
    assert len(rows) == 38
    assert [r["wcag_level"] for r in rows].count("A") == 25 and [r["wcag_level"] for r in rows].count("AA") == 13
    assert [r["sc"] for r in rows][:3] == ["1.1.1", "1.2.1", "1.2.2"]  # criterion order, not text order
    assert all(set(r) == {"sc", "name", "wcag_level", "conformance", "remarks", "evidence"} for r in rows)
    assert {r["conformance"] for r in rows} <= LEVELS


def test_build_acr_row_for_a_confirmed_failure_and_one_still_pending():
    runs, decisions = fake_before_site()
    by_sc = {r["sc"]: r for r in acr.build_acr(runs, decisions, load_corpus())}
    link = by_sc["2.4.4"]
    assert link["conformance"] == acr.LEVEL_FAIL and link["name"] == "Link Purpose (In Context)"
    assert [e["finding_id"] for e in link["evidence"]] == ["h1"]
    assert link["remarks"].startswith('jesus: "Click here is not descriptive."')  # reviewer comment leads
    assert "Confirmed failures: home (1)." in link["remarks"] and "Pending human review: news 1." in link["remarks"]
    alt = by_sc["1.1.1"]
    assert alt["conformance"] == acr.LEVEL_PENDING
    assert [(e["finding_id"], e["decision"]) for e in alt["evidence"]] == [("h2", None)]  # machine, undecided


# ---------------------------------------------------------------- format_acr_md

def acr_row(sc, wcag_level, conformance=acr.LEVEL_PENDING, remarks="", evidence=()):
    return {"sc": sc, "name": f"Name of {sc}", "wcag_level": wcag_level, "conformance": conformance,
            "remarks": remarks, "evidence": list(evidence)}


def ev(finding_id, screenshot, page="home", decision="fail", reviewer="jesus", selector="a.one"):
    return {"finding_id": finding_id, "page": page, "decision": decision, "reviewer": reviewer,
            "selector": selector, "screenshot": screenshot}


def test_acr_md_header_states_the_product_standard_models_and_that_it_is_a_draft():
    md = acr.format_acr_md([], "2026-10-26", tools=["axe-core", "Pa11y"], model_ids=[MODEL])
    assert "CityLights (W3C Before and After Demonstration, Before version)" in md
    assert "2026-10-26" in md and "E205.4" in md and "WCAG 2.0 Level A and AA" in md
    assert "axe-core" in md and "Pa11y" in md and MODEL in md  # models come from the runs
    assert ("Draft generated by ADJ. Conformance levels are set only by the named human reviewers; "
            "AI analysis appears in Remarks, labelled as AI.") in md


def test_acr_md_has_a_table_per_wcag_level_with_the_vpat_columns():
    rows = [acr_row("1.1.1", "A"), acr_row("2.4.4", "A"), acr_row("1.4.3", "AA")]
    md = acr.format_acr_md(rows, "2026-10-26")
    table1, table2 = md.split("Table 2: Success Criteria, Level AA")
    assert "Table 1: Success Criteria, Level A" in table1
    assert table1.count("| Criteria | Conformance Level | Remarks and Explanations | Evidence |") == 1
    assert "1.1.1 Name of 1.1.1" in table1 and "2.4.4 Name of 2.4.4" in table1 and "1.4.3" not in table1
    assert "1.4.3 Name of 1.4.3" in table2 and "1.1.1" not in table2


def test_acr_md_evidence_names_finding_page_decision_reviewer_and_links_the_screenshot():
    rows = [acr_row("2.4.4", "A", acr.LEVEL_FAIL, evidence=[
        ev("h1", "data/findings/shots/h1.jpg"),
        ev("n1", None, page="news", decision="pass", reviewer="alice", selector="html > head > title")])]
    md = acr.format_acr_md(rows, "2026-10-26", shot_href=lambda path: f"../../../{path}")
    assert "h1 (home, fail by jesus, [screenshot of h1](../../../data/findings/shots/h1.jpg))" in md
    assert "n1 (news, pass by alice, `html > head > title`)" in md  # no screenshot: the selector instead


def test_acr_md_pending_row_has_no_evidence_and_text_cannot_break_the_table():
    rows = [acr_row("1.1.1", "A", remarks='jesus: "uses a | pipe"\nand a new line')]
    md = acr.format_acr_md(rows, "2026-10-26", notes=["This run used stand-ins."])
    assert 'jesus: "uses a \\| pipe" and a new line' in md
    assert "> **Warning:** This run used stand-ins." in md
    row = next(line for line in md.splitlines() if line.startswith("| 1.1.1"))
    assert "| Pending human review |" in row and row.endswith("| — |")


# ---------------------------------------------------------------- load_before_site and main

def example_finding(version, page, dom_index, sc):
    """A valid grey finding: a copy of docs/finding.example.json with the given identity."""
    f = copy.deepcopy(EXAMPLE)
    f["site_version"], f["page"] = version, page
    f["finding_id"] = f"F-{version}-{page}-el{dom_index:04d}-{sc}"
    f["wcag"] = [sc]
    f["bucket"] = "grey"
    f["evidence"].update(dom_index=dom_index, selector="#content a",
                         screenshot=f"data/findings/shots/{version}_{page}_el{dom_index:04d}.jpg")
    return f


def write_run(runs_dir, run_id, version, page, findings, **summary):
    run = runs_dir / run_id
    run.mkdir(parents=True)
    body = {"run_id": run_id, "version": version, "page": page, "stubs": [], "model_ids": [MODEL], **summary}
    (run / "summary.json").write_text(json.dumps(body))
    (run / "output_findings.json").write_text(json.dumps(findings))


@pytest.fixture
def site(tmp_path, monkeypatch):
    """4 Before runs (home has one 2.4.4 finding, failed by jesus), 1 After run, all in temp folders.
    Returns the 5 run IDs, After last."""
    monkeypatch.setattr(matrix, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(matrix, "REVIEWS_DIR", tmp_path / "reviews")
    monkeypatch.setattr(matrix, "REPORTS_DIR", tmp_path / "reports")
    home = example_finding("before", "home", 210, "2.4.4")
    for page in PAGES:
        write_run(matrix.RUNS_DIR, f"run-{page}", "before", page, [home] if page == "home" else [])
    write_run(matrix.RUNS_DIR, "run-after", "after", "home", [example_finding("after", "home", 5, "1.1.1")])
    matrix.REVIEWS_DIR.mkdir()
    (matrix.REVIEWS_DIR / "before_home.jsonl").write_text(json.dumps(
        {"finding_id": home["finding_id"], "run_id": "run-home", "reviewer": "jesus", "decision": "fail",
         "comment": "Click here is not descriptive.", "decided_at": "2026-10-23T20:44:12Z"}) + "\n")
    return [f"run-{p}" for p in PAGES] + ["run-after"]


def test_load_before_site_reads_the_four_before_runs_and_their_decisions(site):
    runs, decisions, notes, model_ids = acr.load_before_site(site)  # the After run is in the list; ignored
    assert [summary["page"] for summary, _ in runs] == list(PAGES)
    assert list(decisions["home"]) == ["F-before-home-el0210-2.4.4"] and decisions["news"] == {}
    assert notes == [] and model_ids == [MODEL]


@pytest.mark.parametrize("drop,match", [(["run-survey"], "survey"), ([], "home")])
def test_load_before_site_needs_exactly_one_run_per_before_page(site, drop, match):
    ids = [i for i in site if i not in drop]
    if not drop:  # a second home run
        write_run(matrix.RUNS_DIR, "run-home-2", "before", "home", [])
        ids.append("run-home-2")
    with pytest.raises(acr.AcrError, match=match):
        acr.load_before_site(ids)


def test_load_before_site_turns_stand_ins_and_skipped_reviews_into_notes(site):
    (matrix.RUNS_DIR / "run-news" / "summary.json").write_text(json.dumps(
        {"run_id": "run-news", "version": "before", "page": "news", "stubs": ["router"], "model_ids": []}))
    with (matrix.REVIEWS_DIR / "before_home.jsonl").open("a") as fh:
        fh.write("not json\n")
    _, _, notes, _ = acr.load_before_site(site)
    assert any("router" in n and "news" in n for n in notes) and any("line 2" in n for n in notes)


def test_main_writes_acr_md_from_the_runs_and_reviews(site):
    assert acr.main(["--runs", *site]) == 0
    md = (matrix.REPORTS_DIR / "acr" / "acr.md").read_text()
    row = next(line for line in md.splitlines() if line.startswith("| 2.4.4"))
    assert "Does Not Support" in row and "F-before-home-el0210-2.4.4" in row and "by jesus" in row
    assert MODEL in md


def test_main_reports_an_incomplete_site_and_exits_1(site, capsys):
    assert acr.main(["--runs", "run-home"]) == 1
    assert "ERROR" in capsys.readouterr().err and not (matrix.REPORTS_DIR / "acr").exists()


# ---------------------------------------------------------------- format_acr_html

def test_acr_html_is_an_accessible_page_with_a_captioned_table_per_wcag_level():
    rows = [acr_row("1.1.1", "A"), acr_row("1.4.3", "AA")]
    html = acr.format_acr_html(rows, "2026-10-26", model_ids=[MODEL])
    assert html.startswith("<!doctype html>") and '<html lang="en">' in html and '<meta charset="utf-8">' in html
    assert "<title>" in html and html.count("<h1>") == 1
    assert "<caption>Table 1: Success Criteria, Level A</caption>" in html
    assert "<caption>Table 2: Success Criteria, Level AA</caption>" in html
    for column in acr.COLUMNS:
        assert html.count(f'<th scope="col">{column}</th>') == 2  # once per table
    assert '<th scope="row">1.1.1 Name of 1.1.1</th>' in html  # the criterion names its row
    assert acr.DRAFT_LINE in html and MODEL in html and "2026-10-26" in html


def test_acr_html_escapes_everything_that_came_from_data():
    rows = [acr_row("1.1.1", "A", remarks='<script>alert(1)</script> & "quoted"',
                    evidence=[ev("h1", 'shots/a"b.jpg', selector="a[href='x']<b>")])]
    html = acr.format_acr_html(rows, "2026-10-26", notes=["<b>stand-ins</b>"])
    assert "<script" not in html and "<b>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt; &amp; &quot;quoted&quot;" in html
    assert 'href="shots/a&quot;b.jpg"' in html


def test_acr_html_evidence_links_the_screenshot_or_shows_the_selector():
    rows = [acr_row("2.4.4", "A", acr.LEVEL_FAIL, evidence=[
        ev("h1", "data/findings/shots/h1.jpg"),
        ev("n1", None, page="news", decision="pass", reviewer="alice", selector="html > head > title")])]
    html = acr.format_acr_html(rows, "2026-10-26", shot_href=lambda path: f"../../../{path}")
    assert 'h1 (home, fail by jesus, <a href="../../../data/findings/shots/h1.jpg">screenshot of h1</a>)' in html
    assert "n1 (news, pass by alice, <code>html &gt; head &gt; title</code>)" in html


def test_acr_html_warnings_are_visible_text_and_a_pending_row_says_so_in_words():
    rows = [acr_row("1.1.1", "A")]
    html = acr.format_acr_html(rows, "2026-10-26", notes=["This run used stand-ins."])
    assert "Warning:" in html and "This run used stand-ins." in html
    assert "<td>Pending human review</td>" in html  # the level is text, not only a colour


def test_main_also_writes_acr_html_with_the_same_rows(site):
    assert acr.main(["--runs", *site]) == 0
    page = (matrix.REPORTS_DIR / "acr" / "acr.html").read_text()
    assert '<th scope="row">2.4.4 Link Purpose (In Context)</th><td>Does Not Support</td>' in page
    assert "F-before-home-el0210-2.4.4" in page and "jesus" in page


def add_review(finding_id, decision, reviewer="jesus"):
    """Append one more human decision to the fake site's before/home reviews file."""
    with (matrix.REVIEWS_DIR / "before_home.jsonl").open("a") as fh:
        fh.write(json.dumps({"finding_id": finding_id, "run_id": "run-home", "reviewer": reviewer,
                             "decision": decision, "comment": "", "decided_at": "2026-10-24T10:00:00Z"}) + "\n")


def test_a_decision_on_a_finding_the_run_no_longer_has_is_a_visible_note(site):
    add_review("F-before-home-el0999-2.4.4", "pass", "alice")  # a rescan dropped el0999
    _, _, notes, _ = acr.load_before_site(site)
    assert any("F-before-home-el0999-2.4.4" in n and "alice" in n and "not in the run" in n for n in notes)


def test_a_human_fail_on_a_missing_finding_is_a_loud_warning_naming_the_criterion(site):
    """The fail can't be counted, so the report must say so: its row may read too well."""
    add_review("F-before-home-el0999-2.4.4", "fail")
    _, _, notes, _ = acr.load_before_site(site)  # still builds: a stale line must not block the ACR
    note = next(n for n in notes if "F-before-home-el0999-2.4.4" in n)
    assert "human-confirmed fail" in note and "NOT counted" in note and "jesus" in note
    assert "Criterion 2.4.4 may be understated" in note


# ---------------------------------------------------------------- tool_names, npm_versions

def detail(scanner, rule_id):
    return {"scanner": scanner, "rule_id": rule_id, "type": "x", "message": "m"}


def run_with(*details):
    """One run whose single finding has these scanner_details."""
    return {"version": "before", "page": "home"}, [{"finding_id": "f", "scanner_details": list(details)}]


def test_tool_names_lists_only_the_tools_that_produced_findings():
    runs = [run_with(detail("axe-core", "image-alt"), detail("pa11y", "WCAG2AA.x"))]
    assert acr.tool_names(runs) == ["axe-core", "Pa11y (HTML_CodeSniffer)"]  # no ADJ checks, no validator


def test_tool_names_tell_adj_rules_validator_comparison_and_vision_apart_by_rule_id():
    runs = [run_with(detail("adj-vision", "adj.vision.image-of-text"), detail("adj-rules", "adj.compare.nav-order")),
            run_with(detail("adj-rules", "adj.validator.duplicate-id"), detail("adj-rules", "adj.rules.blur-on-focus"),
                     detail("axe-core", "image-alt"))]
    assert acr.tool_names(runs) == ["axe-core", "ADJ rule checks", "Nu HTML Checker", "ADJ page comparison",
                                    "AI on screenshots"]  # fixed order, whatever order they appear in


def test_tool_names_add_versions_and_name_an_unknown_check_instead_of_hiding_it():
    runs = [run_with(detail("axe-core", "image-alt"), detail("pa11y", "x"), detail("adj-rules", "adj.future.thing"))]
    assert acr.tool_names(runs, {"axe-core": "4.13.0", "pa11y": "10.0.0"}) == [
        "axe-core 4.13.0", "Pa11y 10.0.0 (HTML_CodeSniffer)", "ADJ check: future"]


def test_npm_versions_reads_the_pins_in_package_json_and_survives_a_missing_file(tmp_path):
    pins = json.loads((REPO / "package.json").read_text())["dependencies"]
    assert acr.npm_versions()["axe-core"] == pins["axe-core"].lstrip("^~")
    assert acr.npm_versions(tmp_path / "nope.json") == {}


def test_the_header_lists_the_tools_it_is_given_and_nothing_else():
    for out in (acr.format_acr_md([], "2026-10-26", tools=["axe-core"]),
                acr.format_acr_html([], "2026-10-26", tools=["axe-core"])):
        assert "axe-core" in out and "human review in PULSE" in out
        assert "Nu HTML Checker" not in out and "ADJ rule checks" not in out


def test_main_lists_the_tools_the_fake_site_actually_used(site):
    assert acr.main(["--runs", *site]) == 0
    md = (matrix.REPORTS_DIR / "acr" / "acr.md").read_text()
    methods = next(line for line in md.splitlines() if line.startswith("**Evaluation methods:**"))
    assert "axe-core" in methods and "Pa11y" in methods and "Nu HTML Checker" not in methods


def test_skipped_review_notes_name_the_file_without_a_local_absolute_path(site):
    with (matrix.REVIEWS_DIR / "before_home.jsonl").open("a") as fh:
        fh.write("not json\n")
    _, _, notes, _ = acr.load_before_site(site)
    skipped = [n for n in notes if n.startswith("Skipped reviews")]
    assert skipped and all(str(matrix.REVIEWS_DIR) not in n and str(REPO) not in n for n in skipped)
    assert any("data/reviews/before_home.jsonl line 2" in n for n in skipped)


# ---------------------------------------------------------------- inputs recorded in the header

def test_both_headers_name_the_run_used_for_each_page():
    run_ids = {"home": "20261028-090000-aaaa", "news": "20261028-091000-bbbb"}
    md = acr.format_acr_md([], "2026-10-26", run_ids=run_ids)
    page = acr.format_acr_html([], "2026-10-26", run_ids=run_ids)
    assert "**Runs:** home 20261028-090000-aaaa, news 20261028-091000-bbbb" in md
    assert "<strong>Runs:</strong> home 20261028-090000-aaaa, news 20261028-091000-bbbb" in page


def test_main_records_the_four_before_runs_and_not_the_after_run(site):
    assert acr.main(["--runs", *site]) == 0
    md = (matrix.REPORTS_DIR / "acr" / "acr.md").read_text()
    runs_line = next(line for line in md.splitlines() if line.startswith("**Runs:**"))
    assert runs_line.startswith("**Runs:** home run-home, news run-news, tickets run-tickets, survey run-survey")
    assert "run-after" not in md


def test_the_report_date_is_utc_like_the_decisions():
    assert acr.report_date() == datetime.datetime.now(datetime.UTC).date().isoformat()


# ---------------------------------------------------------------- Markdown escaping

def cell_count(row):
    """How many cells a GitHub Markdown table row has: a backslash escapes the next character,
    every other | is a border."""
    borders, i = 0, 0
    while i < len(row):
        if row[i] == "\\":
            i += 2
            continue
        borders += row[i] == "|"
        i += 1
    return borders - 1


def md_row(sc, md):
    rows = [line for line in md.splitlines() if line.startswith(f"| {sc}")]
    assert len(rows) == 1, f"the {sc} row was split over {len(rows)} lines"
    return rows[0]


@pytest.mark.parametrize("remarks", [
    'jesus: "a\\|b"',                    # a backslash before a pipe must not leave a bare border
    "ends in a backslash\\",             # ...nor eat the cell's closing border
    "carriage\rreturn",                  # line breaks Markdown or splitlines() would honour
    "line\u2028separator and\u2029paragraph and\x85next",
])
def test_remarks_text_never_adds_a_cell_or_breaks_the_row(remarks):
    md = acr.format_acr_md([acr_row("1.1.1", "A", remarks=remarks)], "2026-10-26")
    assert cell_count(md_row("1.1.1", md)) == 4


def test_reviewer_text_cannot_inject_html_or_a_link_into_the_markdown():
    md = acr.format_acr_md([acr_row("1.1.1", "A", remarks='<script>x</script> & [click](http://evil.example)')],
                           "2026-10-26")
    row = md_row("1.1.1", md)
    assert "<script" not in md and "&lt;script>" in row and "&amp;" in row
    assert "\\[click](" in row  # an escaped [ is text, not a link


@pytest.mark.parametrize("selector,shown", [
    ("a[title=`x`]", "`` a[title=`x`] ``"),   # a backtick in the selector needs a longer fence
    ("[lang|=en]", "`[lang\\|=en]`"),          # a pipe in a selector is escaped, not a border
    ("#a\\:b", "`#a\\:b`"),                    # CSS escapes stay as written inside the code span
])
def test_selector_code_spans_survive_backticks_and_pipes(selector, shown):
    rows = [acr_row("2.4.4", "A", acr.LEVEL_FAIL, evidence=[ev("n1", None, selector=selector)])]
    row = md_row("2.4.4", acr.format_acr_md(rows, "2026-10-26"))
    assert shown in row and cell_count(row) == 4


# ---------------------------------------------------------------- robustness

def test_a_failed_write_leaves_the_previous_acr_pair_untouched(site, monkeypatch, capsys):
    """acr.md and acr.html are one report: never a new .md next to an old .html."""
    out = matrix.REPORTS_DIR / "acr"
    out.mkdir(parents=True)
    (out / "acr.md").write_text("old md")
    (out / "acr.html").write_text("old html")
    real = acr.write_atomic

    def disk_full_on_html(path, text):
        if "acr.html" in path.name:
            raise OSError("disk full")
        real(path, text)

    monkeypatch.setattr(acr, "write_atomic", disk_full_on_html)
    assert acr.main(["--runs", *site]) == 1
    assert "disk full" in capsys.readouterr().err
    assert (out / "acr.md").read_text() == "old md" and (out / "acr.html").read_text() == "old html"
    assert sorted(p.name for p in out.iterdir()) == ["acr.html", "acr.md"]  # no temp files left behind


@pytest.mark.parametrize("field,value", [
    ("model_ids", MODEL),  # a string would be listed one character at a time
    ("model_ids", [None]),
    ("stubs", "router"),
    ("stubs", [1]),
])
def test_a_summary_with_malformed_model_ids_or_stubs_is_a_clear_error(site, field, value):
    path = matrix.RUNS_DIR / "run-news" / "summary.json"
    summary = json.loads(path.read_text())
    summary[field] = value
    path.write_text(json.dumps(summary))
    with pytest.raises(acr.AcrError, match=f"run-news.*{field}"):
        acr.load_before_site(site)


def test_a_run_whose_findings_are_for_another_page_is_an_error(site):
    (matrix.RUNS_DIR / "run-news" / "output_findings.json").write_text(
        json.dumps([example_finding("before", "home", 7, "1.1.1")]))  # summary says news
    with pytest.raises(acr.AcrError, match="run-news.*F-before-home-el0007-1.1.1"):
        acr.load_before_site(site)


def test_a_run_for_a_page_the_acr_does_not_know_is_an_error_not_ignored(site):
    write_run(matrix.RUNS_DIR, "run-odd", "before", "contact", [])
    with pytest.raises(acr.AcrError, match="run-odd.*contact"):
        acr.load_before_site([*site, "run-odd"])


def test_long_reviewer_comments_are_cut_to_a_readable_length():
    by_page = {"home": [machine("h1")]}
    decisions = {"home": {"h1": reviewed("fail", "jesus", "x" * 1000, "2026-10-22T10:00:00Z")}}
    [comment] = acr.reviewer_comments(by_page, decisions)
    assert comment.startswith('jesus: "xxx') and comment.endswith('…"')
    assert len(comment) <= len('jesus: ""') + acr.MAX_COMMENT_CHARS


def test_acr_html_content_is_in_a_main_landmark():
    html = acr.format_acr_html([acr_row("1.1.1", "A")], "2026-10-26", notes=["A note."])
    assert html.count("<main>") == 1 and html.count("</main>") == 1
    start, end = html.index("<main>"), html.index("</main>")
    assert start < html.index("<h1>") and html.rindex("</table>") < end  # heading, notes and both tables inside


def test_each_screenshot_link_names_its_finding_so_a_list_of_links_reads_apart():
    rows = [acr_row("2.4.4", "A", acr.LEVEL_FAIL, evidence=[ev("h1", "shots/h1.jpg"), ev("h2", "shots/h2.jpg")])]
    html = acr.format_acr_html(rows, "2026-10-26")
    assert ">screenshot of h1</a>" in html and ">screenshot of h2</a>" in html and ">screenshot</a>" not in html


# ---------------------------------------------------------------- end to end through build_acr and main

def finding(finding_id, sc, bucket="machine", ai=None):
    """A finding with just the fields build_acr reads."""
    f = {"finding_id": finding_id, "wcag": [sc], "bucket": bucket,
         "evidence": {"selector": f"#{finding_id}", "screenshot": f"data/findings/shots/{finding_id}.jpg"}}
    if ai is not None:
        f["ai"] = ai
    return f


def mixed_site():
    """1.1.1 on every page, all passed; 2.4.4 failed on home, passed on news; 1.4.3 a grey finding on news
    the AI is sure fails, undecided."""
    sure = {"case_for": "FOR ...", "recommendation": "likely_fail", "citations_verified": True}
    findings = {p: [finding(f"{p}-alt", "1.1.1")] for p in PAGES}
    findings["home"].append(finding("home-link", "2.4.4"))
    findings["news"] += [finding("news-link", "2.4.4"), finding("news-contrast", "1.4.3", "grey", sure)]
    runs = [({"version": "before", "page": p}, fs) for p, fs in findings.items()]
    decisions = {p: {f"{p}-alt": reviewed("pass", "alice", "", "2026-10-27T10:00:00Z")} for p in PAGES}
    decisions["home"]["home-link"] = reviewed("fail", "jesus", "", "2026-10-27T10:05:00Z")
    decisions["news"]["news-link"] = reviewed("pass", "alice", "", "2026-10-27T10:10:00Z")
    return runs, decisions


def test_build_acr_gives_supports_and_partially_supports_from_human_passes():
    runs, decisions = mixed_site()
    by_sc = {r["sc"]: r for r in acr.build_acr(runs, decisions, load_corpus())}
    alt, link = by_sc["1.1.1"], by_sc["2.4.4"]
    assert alt["conformance"] == acr.LEVEL_SUPPORTS
    assert [e["finding_id"] for e in alt["evidence"]] == [f"{p}-alt" for p in PAGES]  # every page's pass
    assert link["conformance"] == acr.LEVEL_PARTIAL
    assert [(e["finding_id"], e["decision"]) for e in link["evidence"]] == [("home-link", "fail"),
                                                                             ("news-link", "pass")]


def test_build_acr_ai_never_sets_a_level_even_when_it_is_sure():
    runs, decisions = mixed_site()
    contrast = next(r for r in acr.build_acr(runs, decisions, load_corpus()) if r["sc"] == "1.4.3")
    assert contrast["conformance"] == acr.LEVEL_PENDING and contrast["evidence"] == []
    assert "AI analysis (not a decision): likely_fail 1" in contrast["remarks"]  # shown, but only as advice


def test_remarks_never_state_a_level_or_an_unlabelled_ai_result():
    runs, decisions = mixed_site()
    for row in acr.build_acr(runs, decisions, load_corpus()):
        assert not row["remarks"].startswith("AI") and "AI: " not in row["remarks"]
        for level in (acr.LEVEL_SUPPORTS, acr.LEVEL_PARTIAL, acr.LEVEL_FAIL, acr.LEVEL_NOT_APPLICABLE):
            assert level not in row["remarks"], (row["sc"], row["remarks"])


# ---------------------------------------------------------------- ADJ proposes, a human ratifies

PROPOSAL = "Proposed by ADJ (machine-verified, awaiting human ratification): Does Not Support."


def proposal_site():
    """1.1.1: machine findings on home (two) and news (one), all undecided. 1.4.3: only a grey finding the
    AI is sure fails, undecided. 2.4.4: a machine finding a human already failed."""
    sure = {"case_for": "FOR ...", "recommendation": "likely_fail", "citations_verified": True}
    findings = {p: [] for p in PAGES}
    findings["home"] += [finding("home-alt1", "1.1.1"), finding("home-alt2", "1.1.1"), finding("home-link", "2.4.4")]
    findings["news"] += [finding("news-alt", "1.1.1"), finding("news-contrast", "1.4.3", "grey", sure)]
    runs = [({"version": "before", "page": p}, fs) for p, fs in findings.items()]
    decisions = {"home": {"home-link": reviewed("fail", "jesus", "", "2026-10-30T10:00:00Z")}}
    return runs, decisions


def test_a_pending_row_with_machine_findings_shows_adjs_proposal_and_lists_them_as_evidence():
    runs, decisions = proposal_site()
    alt = next(r for r in acr.build_acr(runs, decisions, load_corpus()) if r["sc"] == "1.1.1")
    assert alt["conformance"] == acr.LEVEL_PENDING  # the level column stays human-only
    assert PROPOSAL in alt["remarks"]
    assert "Does Not Support" not in alt["remarks"].replace(PROPOSAL, "")  # only ever inside the proposal
    assert [(e["finding_id"], e["page"], e["decision"], e["screenshot"]) for e in alt["evidence"]] == [
        ("home-alt1", "home", None, "data/findings/shots/home-alt1.jpg"),
        ("home-alt2", "home", None, "data/findings/shots/home-alt2.jpg"),
        ("news-alt", "news", None, "data/findings/shots/news-alt.jpg"),
    ]


def test_an_undecided_machine_finding_reads_as_awaiting_review_in_both_formats():
    rows = [acr_row("1.1.1", "A", remarks=PROPOSAL, evidence=[
        ev("h1", "data/findings/shots/h1.jpg", decision=None, reviewer=None),
        ev("h2", "data/findings/shots/h2.jpg", decision="pass", reviewer="alice")])]
    md = acr.format_acr_md(rows, "2026-10-30", shot_href=lambda path: f"../../../{path}")
    html = acr.format_acr_html(rows, "2026-10-30", shot_href=lambda path: f"../../../{path}")
    assert "h1 (home, machine-verified, awaiting review, [screenshot of h1](../../../data/findings/shots/h1.jpg))" in md
    assert ('h1 (home, machine-verified, awaiting review, '
            '<a href="../../../data/findings/shots/h1.jpg">screenshot of h1</a>)') in html
    assert "h2 (home, pass by alice, " in md and "h2 (home, pass by alice, " in html  # decided ones unchanged
    assert "None" not in md and "None" not in html


def test_an_ai_only_likely_fail_gets_no_proposal_and_no_evidence():
    runs, decisions = proposal_site()
    contrast = next(r for r in acr.build_acr(runs, decisions, load_corpus()) if r["sc"] == "1.4.3")
    assert contrast["conformance"] == acr.LEVEL_PENDING
    assert "Proposed by ADJ" not in contrast["remarks"] and contrast["evidence"] == []
    assert "AI analysis (not a decision): likely_fail 1" in contrast["remarks"]


def test_a_row_a_human_already_decided_gets_no_proposal():
    """Nothing is awaiting ratification once a human has set the level."""
    runs, decisions = proposal_site()
    link = next(r for r in acr.build_acr(runs, decisions, load_corpus()) if r["sc"] == "2.4.4")
    assert link["conformance"] == acr.LEVEL_FAIL and "Proposed by ADJ" not in link["remarks"]


def test_undecided_machine_findings_never_count_as_evidence_for_a_level():
    """The no-evidence guard counts human decisions only: a machine finding alone can't back Supports."""
    with pytest.raises(acr.AcrError, match="1.1.1"):
        acr.criterion_evidence("1.1.1", acr.LEVEL_SUPPORTS, {"home": [finding("home-alt1", "1.1.1")]}, {})


def test_main_exits_1_and_writes_nothing_when_a_level_has_no_evidence(site, monkeypatch, capsys):
    monkeypatch.setattr(acr, "criterion_level", lambda *args: acr.LEVEL_SUPPORTS)  # a level no decision backs
    assert acr.main(["--runs", *site]) == 1
    assert "no decided finding behind it" in capsys.readouterr().err
    assert not (matrix.REPORTS_DIR / "acr").exists()


def test_main_screenshot_links_reach_the_real_screenshot_from_where_the_acr_is_saved(site):
    assert acr.main(["--runs", *site]) == 0
    out = matrix.REPORTS_DIR / "acr"
    target = os.path.normpath(REPO / "data/findings/shots/before_home_el0210.jpg")
    [md_href] = re.findall(r"\[screenshot of F-before-home-el0210-2\.4\.4\]\(([^)]+)\)", (out / "acr.md").read_text())
    [html_href] = re.findall(r'<a href="([^"]+)">screenshot of F-before-home-el0210-2\.4\.4</a>',
                             (out / "acr.html").read_text())
    assert os.path.normpath(out / unquote(md_href)) == target
    assert os.path.normpath(out / html_href) == target


def test_a_generated_acr_never_says_not_evaluated(site):
    assert acr.main(["--runs", *site]) == 0
    for name in ("acr.md", "acr.html"):
        assert "Not Evaluated" not in (matrix.REPORTS_DIR / "acr" / name).read_text()
