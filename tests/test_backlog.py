"""Track E: the remediation backlog (reports/backlog.py)."""
import json
import os
import re

import pytest

from rag.corpus_tools import load_corpus
from reports import backlog, matrix
from scanners.findings import REPO
from tests.test_acr import site  # noqa: F401  the ACR's fake Before site: 4 runs, home's 2.4.4 failed by jesus

CONFIRMED, MACHINE, AI = backlog.STATUS_CONFIRMED, backlog.STATUS_MACHINE, backlog.STATUS_AI


def finding(finding_id, bucket, recommendation=None, verified=True, sc="2.4.4", rule_id="link-name"):
    return {"finding_id": finding_id, "bucket": bucket, "wcag": [sc], "rule_id": rule_id,
            "ai": {"case_for": "FOR ...", "recommendation": recommendation, "citations_verified": verified}}


def decision(value):
    return {"decision": value, "reviewer": "jesus"}


# ---------------------------------------------------------------- backlog_status

@pytest.mark.parametrize("f,decided,status", [
    # A human decision always wins, whatever the bucket or the AI says
    (finding("a", "human-only"), "fail", CONFIRMED),
    (finding("a", "grey", "likely_pass"), "fail", CONFIRMED),
    (finding("a", "machine"), "pass", None),            # a human pass takes a machine violation off the backlog
    (finding("a", "machine"), "not_applicable", None),
    (finding("a", "grey", "likely_fail"), "pass", None),
    # Undecided: only what agents.evidence.is_issue calls an issue
    (finding("a", "machine"), None, MACHINE),
    (finding("a", "grey", "likely_fail"), None, AI),
    (finding("a", "grey", "likely_fail", verified=False), None, None),  # unverified: flagged for a human, not here
    (finding("a", "grey", "likely_pass"), None, None),
    (finding("a", "grey", "unsure"), None, None),
    (finding("a", "grey", None), None, None),           # not evaluated
    (finding("a", "human-only"), None, None),
])
def test_backlog_status(f, decided, status):
    decisions = {"a": decision(decided)} if decided else {}
    assert backlog.backlog_status(f, decisions) == status


def test_status_names_say_who_said_so():
    assert (CONFIRMED, MACHINE, AI) == ("Confirmed (human)", "Suspected (machine)",
                                        "Suspected (AI, verified citations)")


# ---------------------------------------------------------------- group_issues

def before_runs(**findings_by_page):
    """(summary, findings) runs in the order given, as matrix.load_run returns them."""
    return [({"version": "before", "page": page}, fs) for page, fs in findings_by_page.items()]


def test_the_same_rule_on_the_same_criterion_across_pages_is_one_issue():
    blur = {"sc": "2.1.1", "rule_id": "adj.rules.blur-on-focus"}
    runs = before_runs(home=[finding("h1", "machine", **blur), finding("h2", "machine", **blur)],
                news=[finding("n1", "machine", **blur)],
                survey=[finding("s1", "machine", sc="1.1.1", rule_id="image-alt")])
    issues = backlog.group_issues(runs, {})
    assert [(i["sc"], i["rule_id"]) for i in issues] == [("2.1.1", "adj.rules.blur-on-focus"), ("1.1.1", "image-alt")]
    blur_issue = issues[0]
    assert blur_issue["pages"] == ["home", "news"]  # page order, each page once
    assert [i["finding"]["finding_id"] for i in blur_issue["instances"]] == ["h1", "h2", "n1"]
    assert {i["page"] for i in blur_issue["instances"]} == {"home", "news"}


def test_one_rule_on_two_criteria_is_two_issues():
    runs = before_runs(home=[finding("h1", "machine", sc="2.4.4", rule_id="link-name"),
                      finding("h2", "machine", sc="4.1.2", rule_id="link-name")])
    assert [i["sc"] for i in backlog.group_issues(runs, {})] == ["2.4.4", "4.1.2"]


def test_an_issue_takes_its_strongest_instance_status_and_leaves_out_what_is_not_an_issue():
    runs = before_runs(home=[finding("h1", "grey", "likely_fail"),      # Suspected (AI)
                      finding("h2", "machine"),                  # Suspected (machine)
                      finding("h3", "grey", "likely_pass")],     # not an issue: not an instance
                news=[finding("n1", "grey", "likely_pass")])     # a human failed it: Confirmed
    [issue] = backlog.group_issues(runs, {"news": {"n1": decision("fail")}})
    assert issue["status"] == CONFIRMED
    assert [(i["finding"]["finding_id"], i["status"]) for i in issue["instances"]] == [
        ("h1", AI), ("h2", MACHINE), ("n1", CONFIRMED)]


def test_a_criterion_with_nothing_on_the_backlog_has_no_issue():
    runs = before_runs(home=[finding("h1", "machine")])
    assert backlog.group_issues(runs, {"home": {"h1": decision("pass")}}) == []


# ---------------------------------------------------------------- rank_issues

# Just the field rank_issues reads. 1.4.3 and 2.4.6 are AA; the rest are A.
CORPUS = {sc: {"level": "AA" if sc in ("1.4.3", "2.4.6") else "A"}
          for sc in ("1.1.1", "1.4.3", "2.1.1", "2.4.4", "2.4.6")}


def issue(name, status=MACHINE, sc="1.1.1", pages=1, instances=1, rule_id=None):
    """An issue as group_issues returns it, differing from the others only where a test says so."""
    return {"sc": sc, "rule_id": rule_id or name, "status": status, "name": name,
            "pages": ["home", "news", "tickets", "survey"][:pages],
            "instances": [{"finding": {}, "page": "home", "status": status}] * instances}


def order(issues):
    return [i["name"] for i in backlog.rank_issues(issues, CORPUS)]


def test_rank_1_confirmed_before_suspected_machine_before_suspected_ai():
    assert order([issue("ai", AI), issue("machine", MACHINE), issue("human", CONFIRMED)]) == ["human", "machine", "ai"]


def test_rank_2_level_a_before_aa():
    assert order([issue("aa", sc="1.4.3"), issue("a", sc="2.4.4")]) == ["a", "aa"]


def test_rank_3_more_pages_first():
    assert order([issue("one", pages=1), issue("four", pages=4), issue("two", pages=2)]) == ["four", "two", "one"]


def test_rank_4_more_instances_first():
    assert order([issue("few", instances=2), issue("many", instances=9)]) == ["many", "few"]


def test_rank_5_ties_break_by_criterion_number_then_rule_id():
    """The same order every run: 2.1.1 before 2.4.4 (numbers, not text), then rule_id."""
    assert order([issue("z", sc="2.4.4", rule_id="b"), issue("y", sc="2.4.4", rule_id="a"),
                  issue("x", sc="2.1.1")]) == ["x", "y", "z"]


def test_rules_apply_in_order_status_outranks_level_outranks_pages_outranks_instances():
    assert order([issue("aa-human", CONFIRMED, sc="1.4.3"),       # status beats level
                  issue("a-machine-4-pages", MACHINE, pages=4),   # level beats pages
                  issue("aa-machine-4-pages", MACHINE, sc="2.4.6", pages=4, instances=1),
                  issue("aa-machine-1-page", MACHINE, sc="1.4.3", pages=1, instances=50)]) == [  # pages beat instances
        "aa-human", "a-machine-4-pages", "aa-machine-4-pages", "aa-machine-1-page"]


def test_ranks_are_numbered_from_1_and_the_input_is_not_changed():
    issues = [issue("b", AI), issue("a", CONFIRMED)]
    ranked = backlog.rank_issues(issues, CORPUS)
    assert [(i["rank"], i["name"]) for i in ranked] == [(1, "a"), (2, "b")]
    assert "rank" not in issues[0]


# ---------------------------------------------------------------- fix guidance

REAL_CORPUS = load_corpus()
WRITTEN = {
    "adj.rules.blur-on-focus": {"sc": "2.1.1", "issue": "Links lose keyboard focus as soon as they get it",
                                "fix": "Remove onfocus=\"blur()\".", "technique": "F55",
                                "url": "https://www.w3.org/TR/WCAG20-TECHS/F55.html"},
    "2.1.1": {"sc": "2.1.1", "issue": "Something can't be used with a keyboard", "fix": "Make it keyboard operable.",
              "technique": "G202", "url": "https://www.w3.org/TR/WCAG20-TECHS/G202.html"},
}


def test_guidance_for_a_rule_beats_guidance_for_its_criterion():
    g = backlog.guidance_for(issue("x", sc="2.1.1", rule_id="adj.rules.blur-on-focus"), WRITTEN, REAL_CORPUS)
    assert g["technique"] == "F55" and g["issue"].startswith("Links lose") and g["written"] is True


def test_guidance_falls_back_to_the_criterion_entry_then_to_the_understanding_page():
    by_criterion = backlog.guidance_for(issue("x", sc="2.1.1", rule_id="adj.rules.other"), WRITTEN, REAL_CORPUS)
    assert by_criterion["technique"] == "G202"
    fallback = backlog.guidance_for(issue("x", sc="1.1.1", rule_id="image-alt"), WRITTEN, REAL_CORPUS)
    assert fallback == {"issue": "Non-text Content", "technique": None, "written": False,
                        "fix": "See W3C's Understanding page for 1.1.1 Non-text Content.",
                        "url": "https://www.w3.org/TR/UNDERSTANDING-WCAG20/text-equiv-all.html"}


@pytest.mark.parametrize("sc,technique", [
    ("2.1.1", "F55"),   # the criterion the entry was written for
    ("2.4.7", "F55"),   # another criterion F55 fails too: the entry still fits
    ("3.2.1", "F55"),
    ("4.1.2", None),    # F55 doesn't fail 4.1.2: no written guidance there, so the Understanding page
])
def test_a_rule_entry_is_used_only_on_criteria_its_technique_fits(sc, technique):
    g = backlog.guidance_for(issue("x", sc=sc, rule_id="adj.rules.blur-on-focus"), WRITTEN, REAL_CORPUS)
    assert g["technique"] == technique and g["written"] is (technique is not None)


def test_a_rule_entry_that_does_not_fit_falls_through_to_the_criterion_entry():
    """axe link-name fires on 2.4.4 and 4.1.2; its F89 entry fits both (F89 fails each). A G technique
    written for 2.4.4 doesn't fit 4.1.2, so 4.1.2's own entry is used."""
    written = {"link-name": {"sc": "2.4.4", "issue": "Links have no name", "fix": "Name them.",
                             "technique": "F89", "url": "https://www.w3.org/TR/WCAG20-TECHS/F89.html"},
               "other-rule": {"sc": "2.4.4", "issue": "Vague links", "fix": "Say where.",
                              "technique": "G91", "url": "https://www.w3.org/TR/WCAG20-TECHS/G91.html"},
               "4.1.2": {"sc": "4.1.2", "issue": "No name or role", "fix": "Give it a name.",
                         "technique": "F68", "url": "https://www.w3.org/TR/WCAG20-TECHS/F68.html"}}
    def technique(rule_id):
        return backlog.guidance_for(issue("x", sc="4.1.2", rule_id=rule_id), written, REAL_CORPUS)["technique"]
    assert technique("link-name") == "F89" and technique("other-rule") == "F68"


def test_every_criterion_has_an_understanding_url_built_from_its_spec_anchor():
    for sc, c in REAL_CORPUS.items():  # all 38 checked live against w3.org on 2026-10-27
        assert backlog.understanding_url(sc, REAL_CORPUS) == (
            f"https://www.w3.org/TR/UNDERSTANDING-WCAG20/{c['source'].split('#')[1]}.html")


def test_the_real_fix_guidance_file_only_cites_real_criteria_and_their_own_failures():
    """No invented criteria (CONTEXT.md): every entry names a WCAG 2.0 A/AA criterion, and a W3C failure
    (F##) it cites must be one rag/wcag20_aa.json lists for that criterion."""
    guidance = backlog.load_guidance()
    assert isinstance(guidance, dict)
    for key, entry in guidance.items():
        assert set(entry) == {"sc", "issue", "fix", "technique", "url"}, key
        assert entry["sc"] in REAL_CORPUS, key
        assert key == entry["sc"] or key not in REAL_CORPUS, key  # a criterion key names its own criterion
        assert all(isinstance(entry[f], str) and entry[f].strip() for f in ("issue", "fix", "technique", "url")), key
        assert re.fullmatch(r"(F|G|H|C|SCR|ARIA)\d+", entry["technique"]), key
        if entry["technique"].startswith("F"):
            assert entry["technique"] in REAL_CORPUS[entry["sc"]]["common_failures"], key
        assert entry["url"].startswith("https://www.w3.org/"), key


def test_load_guidance_of_a_missing_or_broken_file_is_a_clear_error(tmp_path):
    with pytest.raises(backlog.BacklogError, match="nope.json"):
        backlog.load_guidance(tmp_path / "nope.json")
    (tmp_path / "bad.json").write_text("{not json")
    with pytest.raises(backlog.BacklogError, match="bad.json"):
        backlog.load_guidance(tmp_path / "bad.json")
    (tmp_path / "list.json").write_text("[]")
    with pytest.raises(backlog.BacklogError, match="JSON object"):
        backlog.load_guidance(tmp_path / "list.json")


GOOD_ENTRY = WRITTEN["adj.rules.blur-on-focus"]


@pytest.mark.parametrize("key,entry,problem", [
    ("r", "Remove blur()", "exactly these fields"),                                   # not an object
    ("r", {k: v for k, v in GOOD_ENTRY.items() if k != "technique"}, "exactly these fields"),
    ("r", {**GOOD_ENTRY, "extra": "x"}, "exactly these fields"),
    ("r", {**GOOD_ENTRY, "technique": None}, "non-empty text"),
    ("r", {**GOOD_ENTRY, "fix": "  "}, "non-empty text"),
    ("r", {**GOOD_ENTRY, "sc": "2.2.3"}, "not a WCAG 2.0 A/AA criterion"),            # AAA: never cited
    ("2.4.4", GOOD_ENTRY, "must name its own criterion"),                             # 2.4.4 key, 2.1.1 entry
    ("r", {**GOOD_ENTRY, "technique": "F55 "}, "not a W3C technique"),
    ("r", {**GOOD_ENTRY, "technique": "F63"}, "not a failure rag/wcag20_aa.json lists for 2.1.1"),
    ("r", {**GOOD_ENTRY, "url": "https://example.com/F55.html"}, "url must be W3C's page for F55"),
    ("r", {**GOOD_ENTRY, "url": "https://www.w3.org/TR/WCAG20-TECHS/F54.html"}, "url must be W3C's page for F55"),
])
def test_load_guidance_names_the_broken_entry_and_what_is_wrong(tmp_path, key, entry, problem):
    path = tmp_path / "fix_guidance.json"
    path.write_text(json.dumps({"image-alt": WRITTEN["2.1.1"] | {"sc": "2.1.1"}, key: entry}))
    with pytest.raises(backlog.BacklogError, match=re.escape(f"entry {key!r}: ") + ".*" + re.escape(problem)):
        backlog.load_guidance(path, REAL_CORPUS)


def test_load_guidance_accepts_good_entries(tmp_path):
    path = tmp_path / "fix_guidance.json"
    path.write_text(json.dumps(WRITTEN))
    assert backlog.load_guidance(path, REAL_CORPUS) == WRITTEN


# ---------------------------------------------------------------- build_backlog and the writers

MAP508 = {sc: {"web": "E205.4"} for sc in REAL_CORPUS}


def shot(finding_id, page="home", status=MACHINE, screenshot="data/findings/shots/x.jpg", selector="a.one"):
    """An issue instance as group_issues makes it, with just the evidence the writers read."""
    return {"finding": {"finding_id": finding_id, "evidence": {"screenshot": screenshot, "selector": selector}},
            "page": page, "status": status}


def test_build_backlog_ranks_issues_and_adds_criterion_508_and_fix():
    runs = before_runs(home=[finding("h1", "machine", sc="1.1.1", rule_id="image-alt")],
                       news=[finding("n1", "machine", sc="2.1.1", rule_id="adj.rules.blur-on-focus"),
                             finding("n2", "grey", "likely_pass")])
    rows = backlog.build_backlog(runs, {"news": {"n1": decision("fail")}}, REAL_CORPUS, MAP508, WRITTEN)
    assert [(r["rank"], r["sc"], r["status"]) for r in rows] == [(1, "2.1.1", CONFIRMED), (2, "1.1.1", MACHINE)]
    blur = rows[0]
    assert (blur["name"], blur["level"], blur["section508"]) == ("Keyboard", "A", "E205.4")
    assert blur["issue"].startswith("Links lose") and blur["technique"] == "F55" and blur["written"]
    assert blur["pages"] == ["news"] and [i["finding"]["finding_id"] for i in blur["instances"]] == ["n1"]
    assert rows[1]["written"] is False and rows[1]["issue"] == "Non-text Content"  # no entry: the fallback


def test_build_backlog_with_nothing_on_it_is_empty():
    assert backlog.build_backlog(before_runs(home=[]), {}, REAL_CORPUS, MAP508, WRITTEN) == []


UNDERSTANDING_2_1_1 = backlog.understanding_url("2.1.1", REAL_CORPUS)


def row(rank=1, instances=1, written=True, issue="Links lose focus", status=CONFIRMED):
    return {"rank": rank, "issue": issue, "sc": "2.1.1", "name": "Keyboard", "level": "A", "section508": "E205.4",
            "status": status, "pages": ["home", "news"], "fix": "Remove onfocus=\"blur()\".",
            "technique": "F55" if written else None, "written": written,
            "url": "https://www.w3.org/TR/WCAG20-TECHS/F55.html" if written else UNDERSTANDING_2_1_1,
            "instances": [shot(f"F-{n}", status=status) for n in range(instances)]}


def test_backlog_md_says_it_is_a_draft_and_who_set_each_status():
    md = backlog.format_backlog_md([row()], "2026-10-27", notes=["Run r1 (news): stand-ins"])
    assert md.startswith("# Remediation backlog (draft)") and "2026-10-27" in md
    assert "Only Confirmed (human) is a human decision" in md and "> **Warning:** Run r1 (news)" in md


def test_backlog_md_has_the_handoff_columns_and_a_row_per_issue():
    md = backlog.format_backlog_md([row(1), row(2, status=MACHINE)], "2026-10-27")
    assert "| Rank | Issue | Criterion | Status | Pages | Instances | How to fix | Evidence |" in md
    first = next(line for line in md.splitlines() if line.startswith("| 1 |"))
    assert "2.1.1 Keyboard (Level A, 508 E205.4)" in first and "home, news" in first
    assert "[W3C F55](https://www.w3.org/TR/WCAG20-TECHS/F55.html)" in first
    assert "[screenshot of F-0](data/findings/shots/x.jpg)" in first


def test_backlog_md_fallback_guidance_links_the_understanding_page():
    first = next(line for line in backlog.format_backlog_md([row(written=False)], "d").splitlines()
                 if line.startswith("| 1 |"))
    assert f"[W3C Understanding 2.1.1]({UNDERSTANDING_2_1_1})" in first and "F55" not in first


def test_backlog_md_shows_five_instances_then_points_at_the_csv():
    first = next(line for line in backlog.format_backlog_md([row(instances=7)], "d").splitlines()
                 if line.startswith("| 1 |"))
    assert "F-4" in first and "F-5" not in first and "and 2 more in backlog.csv" in first


def test_backlog_md_shows_the_selector_when_there_is_no_screenshot_and_text_cannot_break_the_table():
    r = row(issue="a | b\nc")
    r["instances"] = [shot("F-0", screenshot=None, selector="[lang|=en]")]
    first = next(line for line in backlog.format_backlog_md([r], "d").splitlines() if line.startswith("| 1 |"))
    assert "a \\| b c" in first and "`[lang\\|=en]`" in first


def test_backlog_md_fix_link_cannot_break_the_link_or_the_table():
    r = row()
    r["technique"], r["url"] = "F55]", "https://www.w3.org/x) |y.html"
    first = next(line for line in backlog.format_backlog_md([r], "d").splitlines() if line.startswith("| 1 |"))
    assert "[W3C F55\\]](https://www.w3.org/x%29%20%7Cy.html)" in first


def test_backlog_md_with_nothing_on_it_says_so():
    assert "Nothing is on the backlog" in backlog.format_backlog_md([], "d")


def test_backlog_csv_has_one_row_per_issue_and_every_instance():
    lines = backlog.format_backlog_csv([row(instances=7)]).splitlines()
    assert lines[0] == ",".join(backlog.CSV_COLUMNS)
    assert len(lines) == 2 and all(f"F-{n} (home, {CONFIRMED}): data/findings/shots/x.jpg" in lines[1]
                                   for n in range(7))


def mixed_row():
    """1 human-confirmed instance listed after 6 machine ones, as group_issues would make it."""
    r = row(instances=6, status=MACHINE)
    r["status"] = CONFIRMED
    r["instances"].append(shot("F-human", page="news", status=CONFIRMED))
    return r


def test_one_human_fail_does_not_read_as_a_human_decision_on_every_instance():
    md_row = next(line for line in backlog.format_backlog_md([mixed_row()], "d").splitlines()
                  if line.startswith("| 1 |"))
    assert f"{CONFIRMED}: 1 of 7; {MACHINE}: 6 of 7" in md_row
    # the confirmed one is shown first, inside the first five, with its own status
    assert md_row.index(f"F-human (news, {CONFIRMED},") < md_row.index(f"F-0 (home, {MACHINE},")
    assert "and 2 more in backlog.csv" in md_row


def test_csv_status_and_evidence_say_who_set_each_instance():
    _, line = backlog.format_backlog_csv([mixed_row()]).splitlines()
    assert f"{CONFIRMED}: 1 of 7; {MACHINE}: 6 of 7" in line
    assert line.index(f"F-human (news, {CONFIRMED})") < line.index(f"F-0 (home, {MACHINE})")


def test_an_issue_whose_instances_all_agree_shows_just_the_status():
    md_row = next(line for line in backlog.format_backlog_md([row(instances=2)], "d").splitlines()
                  if line.startswith("| 1 |"))
    assert f"| {CONFIRMED} |" in md_row and "of 2" not in md_row and "F-0 (home, [screenshot" in md_row


def test_main_writes_backlog_md_and_csv_from_the_runs_and_reviews(site):  # noqa: F811
    assert backlog.main(["--runs", *site]) == 0
    out = matrix.REPORTS_DIR / "backlog"
    md, csv_text = (out / "backlog.md").read_text(), (out / "backlog.csv").read_text()
    assert "F-before-home-el0210-2.4.4" in md and CONFIRMED in md
    assert "F-before-home-el0210-2.4.4" in csv_text


def test_main_reports_an_incomplete_site_and_exits_1(site, capsys):  # noqa: F811
    assert backlog.main(["--runs", "run-home"]) == 1
    assert "ERROR" in capsys.readouterr().err and not (matrix.REPORTS_DIR / "backlog").exists()


def test_main_with_broken_guidance_reports_the_entry_and_exits_1(site, capsys, tmp_path, monkeypatch):  # noqa: F811
    bad = tmp_path / "fix_guidance.json"
    bad.write_text(json.dumps({"image-alt": {"sc": "1.1.1", "issue": "No alt"}}))
    monkeypatch.setattr(backlog, "GUIDANCE_PATH", bad)
    assert backlog.main(["--runs", *site]) == 1
    err = capsys.readouterr().err
    assert err.startswith("ERROR") and "'image-alt'" in err and "Traceback" not in err
    assert not (matrix.REPORTS_DIR / "backlog").exists()


def test_main_out_writes_elsewhere_with_screenshot_links_relative_to_it(site, tmp_path):  # noqa: F811
    out = tmp_path / "somewhere" / "else"
    assert backlog.main(["--runs", *site, "--out", str(out)]) == 0
    assert (out / "backlog.csv").exists() and not (matrix.REPORTS_DIR / "backlog").exists()
    shot = os.path.relpath(REPO / "data/findings/shots/before_home_el0210.jpg", out)
    assert f"[screenshot of F-before-home-el0210-2.4.4]({shot})" in (out / "backlog.md").read_text()
