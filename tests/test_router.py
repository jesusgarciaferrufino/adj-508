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


def test_week3_threads_route_grey():
    """The 4 findings the Week 3 handoff names must reach FOR/AGAINST."""
    ids = {"before_home": ["F-before-home-el0210-2.4.4", "F-before-home-el0194-2.1.1"],
           "after_home": ["F-after-home-el0062-4.1.2"]}
    for name, wanted in ids.items():
        data = json.loads((REPO / "data" / "findings" / f"{name}.json").read_text())
        findings = {f["finding_id"]: f for f in (data["findings"] if isinstance(data, dict) else data)}
        for fid in wanted:
            assert route_finding(findings[fid])["bucket"] == GREY, fid
