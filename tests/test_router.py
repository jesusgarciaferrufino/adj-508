"""Track C: the router's four rules, checked one at a time. No network."""

import json
from pathlib import Path

import pytest

from agents.router import BUCKETS, GREY, HUMAN_ONLY, MACHINE, human_only_criteria, route_finding

REPO = Path(__file__).resolve().parent.parent


def make(status, sc="1.1.1", selector="#page img"):
    return {"finding_id": "F-test", "scanner_status": status, "wcag": [sc],
            "evidence": {"selector": selector}}


def test_rule1_violation_is_machine():
    out = route_finding(make("violation"))
    assert out["bucket"] == MACHINE and out["route_reason"].startswith("rule 1")


def test_rule1_beats_human_only_criterion():
    out = route_finding(make("violation", sc="2.4.3"))
    assert out["bucket"] == MACHINE


def test_rule2_page_level_notice_is_human_only():
    out = route_finding(make("notice", sc="1.3.2", selector="html"))
    assert out["bucket"] == HUMAN_ONLY and out["route_reason"].startswith("rule 2")


def test_rule3_human_only_criterion():
    sc = sorted(human_only_criteria())[0]
    out = route_finding(make("needs_review", sc=sc))
    assert out["bucket"] == HUMAN_ONLY and out["route_reason"].startswith("rule 3")


@pytest.mark.parametrize("status", ["needs_review", "notice"])
def test_rule4_everything_else_is_grey(status):
    out = route_finding(make(status, sc="2.4.4", selector="#content a"))
    assert out["bucket"] == GREY and out["route_reason"].startswith("rule 4")


def test_returns_only_its_own_fields():
    assert set(route_finding(make("needs_review"))) == {"bucket", "route_reason"}


def test_missing_fields_route_grey_instead_of_crashing():
    assert route_finding({"finding_id": "F-empty"})["bucket"] == GREY


def test_human_only_list_comes_from_corpus():
    assert human_only_criteria() <= {e["sc"] for e in json.loads(
        (REPO / "rag" / "wcag20_aa.json").read_text())}
    assert human_only_criteria(), "corpus marks no criteria human_only"


@pytest.mark.parametrize("name", ["before_home", "after_home"])
def test_every_real_finding_gets_a_valid_bucket(name):
    data = json.loads((REPO / "data" / "findings" / f"{name}.json").read_text())
    findings = data["findings"] if isinstance(data, dict) else data
    for f in findings:
        out = route_finding(f)
        assert out["bucket"] in BUCKETS and out["route_reason"]


def _real(name):
    data = json.loads((REPO / "data" / "findings" / f"{name}.json").read_text())
    return {f["finding_id"]: f for f in (data["findings"] if isinstance(data, dict) else data)}


def test_week3_threads_route_grey():
    """The Week 3 Before findings must reach FOR/AGAINST."""
    findings = _real("before_home")
    for fid in ["F-before-home-el0210-2.4.4", "F-before-home-el0194-2.1.1"]:
        assert route_finding(findings[fid])["bucket"] == GREY, fid


def test_week3_after_logo_now_goes_to_a_person():
    """Changed Oct 29 (router v2): the After logo is an <a> with no href, HTMLCS's .Placeholder
    check on 4.1.2, one of the four patterns behind the 38 After false alarms (rule 2b)."""
    out = route_finding(_real("after_home")["F-after-home-el0062-4.1.2"])
    assert out["bucket"] == HUMAN_ONLY and out["route_reason"].startswith("rule 2b")
    assert "anchor without href" in out["route_reason"]


def test_router_buckets_match_the_finding_format():
    """The loader (scanners/findings.py) rejects any bucket not in this set, so they must agree."""
    from typing import get_args

    from scanners.findings import Bucket
    assert set(BUCKETS) == set(get_args(Bucket))


def test_rule_1_names_the_real_source():
    """An ADJ rule's violation must not read "axe reported" in PULSE or the ACR."""
    out = route_finding({"scanner_status": "violation", "sources": ["adj-rules"], "wcag": ["2.1.1"],
                         "evidence": {"selector": "#home > a"}})
    assert out["bucket"] == MACHINE and "adj-rules" in out["route_reason"] and "axe" not in out["route_reason"]
    both = route_finding({"scanner_status": "violation", "sources": ["axe-core", "adj-rules"], "wcag": ["1.1.1"],
                          "evidence": {"selector": "img"}})
    assert "axe-core, adj-rules" in both["route_reason"]



# ---------------------------------------------------------------- router v2: rules 2b and 2c (Oct 29)

H43 = ("WCAG2AA.Principle1.Guideline1_3.1_3_1.H43.IncorrectAttr",
       'Incorrect headers attribute on this td element. Expected headers attribute of "c2 c6", but found "c1 c2 c6"')
PLACEHOLDER = ("WCAG2AA.Principle4.Guideline4_1.4_1_2.H91.A.Placeholder",
               "Anchor element found with link content, but no href, ID or name attribute has been supplied.")
SELECT_VALUE = ("WCAG2AA.Principle4.Guideline4_1.4_1_2.H91.Select.Value",
                "This select element does not have a value available to an accessibility API.")


def with_details(status, sc, *details, scanner="pa11y"):
    f = make(status, sc)
    f["scanner_details"] = [{"scanner": scanner, "rule_id": r, "type": "error", "message": m} for r, m in details]
    return f


def test_rule2b_h43_only_is_human_only():
    out = route_finding(with_details("needs_review", "1.3.1", H43))
    assert out["bucket"] == HUMAN_ONLY and out["route_reason"].startswith("rule 2b")
    assert "H43.IncorrectAttr" in out["route_reason"] and "a person must judge" in out["route_reason"]


def test_rule2b_needs_every_detail_to_be_unreliable():
    other = ("WCAG2AA.Principle1.Guideline1_3.1_3_1.H63.3", "Scope attribute on a th element is invalid.")
    assert route_finding(with_details("needs_review", "1.3.1", H43, other))["bucket"] == GREY
    axe = ("td-headers-attr", "Some cells reference headers that are not th elements")
    assert route_finding(with_details("needs_review", "1.3.1", H43, axe))["bucket"] == GREY


def test_rule1_still_beats_rule2b():
    assert route_finding(with_details("violation", "1.3.1", H43))["bucket"] == MACHINE


def test_rule2b_check_must_match_its_criterion():
    """H43 filed under another criterion isn't the known-unreliable check."""
    assert route_finding(with_details("needs_review", "4.1.2", H43))["bucket"] == GREY


def test_rule2b_placeholder_anchor_is_human_only():
    out = route_finding(with_details("needs_review", "4.1.2", PLACEHOLDER))
    assert out["bucket"] == HUMAN_ONLY and "anchor without href" in out["route_reason"]


def test_rule2b_select_value_is_human_only_but_other_value_checks_stay_grey():
    assert route_finding(with_details("needs_review", "4.1.2", SELECT_VALUE))["bucket"] == HUMAN_ONLY
    input_value = ("WCAG2AA.Principle4.Guideline4_1.4_1_2.H91.InputText.Value",
                   "This textinput element does not have a value available to an accessibility API.")
    assert route_finding(with_details("needs_review", "4.1.2", input_value))["bucket"] == GREY


@pytest.mark.parametrize("status", ["needs_review", "notice"])
def test_rule2c_error_identification_needs_a_person(status):
    out = route_finding(make(status, "3.3.1"))
    assert out["bucket"] == HUMAN_ONLY and out["route_reason"].startswith("rule 2c")
    assert "submitting a form and triggering an error" in out["route_reason"]


def test_rule1_still_beats_rule2c():
    assert route_finding(make("violation", "3.3.1"))["bucket"] == MACHINE


def test_finding_with_no_details_is_not_rule2b():
    assert route_finding(make("needs_review", "1.3.1"))["bucket"] == GREY


def test_no_ground_truth_match_is_moved_by_rules_2b_or_2c():
    """Router v2 must not cost recall: no Before finding the ground truth matches leaves grey or machine."""
    import csv

    from scanners.findings import load_all
    gt = {r["finding_id"] for r in csv.DictReader((REPO / "data" / "ground-truth" / "ground_truth.csv").open())
          if r["finding_id"] != "missed"}
    moved = [f.finding_id for (v, p), fs in load_all().items() for f in fs
             if f.finding_id in gt and route_finding(f.model_dump())["route_reason"].startswith(("rule 2b", "rule 2c"))]
    assert moved == []
