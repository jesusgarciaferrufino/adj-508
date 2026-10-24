"""Track A merge step: behavior that doesn't need a browser or the network."""

import pytest

import scanners.merge as merge


def test_missing_scan_raises_with_next_step(tmp_path, monkeypatch):
    """A missing scan file is an exception callers can catch, not a process exit."""
    monkeypatch.setattr(merge, "SCANS", tmp_path)
    with pytest.raises(merge.MissingScanError, match=r"Run: python scanners/axe_scan.py --version before --page home"):
        merge.load_hits("before", "home")


def test_finding_id_is_stable():
    assert merge.finding_id("before", "home", 81, "1.1.1") == "F-before-home-el0081-1.1.1"


# ---------------------------------------------------------------- ADJ checks (adj-rules, adj-vision)

import json  # noqa: E402

from agents.router import route_finding  # noqa: E402
from scanners.findings import validate_findings  # noqa: E402

URL = "https://www.w3.org/WAI/demos/bad/before/home.html"
IMG = '<img src="top_weather.gif">'
LINK = '<a href="#" onfocus="blur()">Home</a>'


def axe_node(dom_index, html, selector, in_wrapper=False):
    return {"target": [selector], "any": [{"message": "Element has no alt"}], "all": [], "none": [],
            "_adj": {"dom_index": dom_index, "outer_html": html, "in_wrapper": in_wrapper}}


def adj_hit(dom_index, sc, status="violation", rule_id="adj.rules.blur-on-focus", html=LINK,
            selector="#home > a", in_wrapper=False):
    return {"rule_id": rule_id, "sc": sc, "status": status, "message": f"{rule_id} fired",
            "dom_index": dom_index, "selector": selector, "html": html, "in_wrapper": in_wrapper}


@pytest.fixture
def scans(tmp_path, monkeypatch):
    """data/scans/ with one axe violation (1.1.1 on dom_index 81), no Pa11y issues, no ADJ files."""
    monkeypatch.setattr(merge, "SCANS", tmp_path)
    axe = {"timestamp": "2026-10-23T12:00:00Z", "incomplete": [], "violations": [
        {"id": "image-alt", "help": "Images must have alt text", "tags": ["wcag2a", "wcag111"],
         "nodes": [axe_node(81, IMG, "img")]}]}
    (tmp_path / "axe_before_home.json").write_text(json.dumps(axe))
    (tmp_path / "pa11y_before_home.json").write_text(json.dumps({"issues": []}))

    def write_adj(check, hits, version="before", page="home"):
        body = {"check": check, "version": version, "page": page, "url": URL, "tool_version": "test", "hits": hits}
        (tmp_path / f"adj_{check}_before_home.json").write_text(json.dumps(body))
    return write_adj


def merged(version="before", page="home"):
    """load_hits -> group -> build_finding, then the shared format check, as merge_page does (minus shots)."""
    hits, dropped, captured_at = merge.load_hits(version, page)
    findings = [merge.build_finding(version, page, hs, URL, captured_at) for hs in merge.group(hits).values()]
    for f in findings:
        f["evidence"]["dom_index"] = f.pop("_dom_index")
        f["evidence"]["screenshot"] = None
    validate_findings(findings)  # the same check merge_page runs before writing
    return {f["finding_id"]: f for f in findings}, dropped


def test_no_adj_files_is_fine(scans):
    by_id, dropped = merged()
    assert list(by_id) == ["F-before-home-el0081-1.1.1"]
    assert by_id["F-before-home-el0081-1.1.1"]["sources"] == ["axe-core"]


def test_adj_rules_hit_becomes_a_valid_finding(scans):
    scans("rules", [adj_hit(135, "2.1.1")])
    by_id, _ = merged()
    f = by_id["F-before-home-el0135-2.1.1"]  # same ID scheme: existing IDs never change
    assert (f["source"], f["sources"], f["scanner_status"]) == ("adj-rules", ["adj-rules"], "violation")
    assert f["rule_id"] == "adj.rules.blur-on-focus" and f["evidence"]["html_snippet"] == LINK
    assert f["scanner_details"] == [{"scanner": "adj-rules", "rule_id": "adj.rules.blur-on-focus",
                                     "type": "rule", "message": "adj.rules.blur-on-focus fired"}]


def test_adj_hit_on_axes_element_and_criterion_joins_axes_finding(scans):
    scans("rules", [adj_hit(81, "1.1.1", status="needs_review", rule_id="adj.rules.alt-lookalike",
                            html=IMG, selector="#weather img")])
    by_id, _ = merged()
    assert list(by_id) == ["F-before-home-el0081-1.1.1"]  # one finding, not two
    f = by_id["F-before-home-el0081-1.1.1"]
    assert f["sources"] == ["axe-core", "adj-rules"] and f["source"] == "axe-core"
    assert f["rule_id"] == "image-alt" and f["evidence"]["selector"] == "img"  # axe stays primary
    assert f["scanner_status"] == "violation"  # strongest status wins
    assert [d["scanner"] for d in f["scanner_details"]] == ["axe-core", "adj-rules"]


def test_wrapper_and_non_wcag20_hits_are_dropped(scans):
    scans("rules", [adj_hit(5, "2.1.1", in_wrapper=True), adj_hit(6, "1.4.10"), adj_hit(7, "1.4.6")])
    by_id, dropped = merged()
    assert list(by_id) == ["F-before-home-el0081-1.1.1"]
    assert dropped["wrapper"] == 1 and dropped["not_wcag20"] == 2  # 1.4.10 is WCAG 2.1, 1.4.6 is AAA


def test_vision_check_is_adj_vision(scans):
    scans("vision", [adj_hit(81, "1.4.5", status="needs_review", rule_id="adj.vision.image-of-text", html=IMG)])
    f = merged()[0]["F-before-home-el0081-1.4.5"]
    assert f["sources"] == ["adj-vision"] and f["scanner_details"][0]["type"] == "vision"


def test_sources_follow_the_fixed_order(scans):
    scans("vision", [adj_hit(81, "1.1.1", status="needs_review", rule_id="adj.vision.x", html=IMG)])
    scans("validator", [adj_hit(81, "1.1.1", status="needs_review", rule_id="adj.validator.x", html=IMG)])
    f = merged()[0]["F-before-home-el0081-1.1.1"]
    assert f["sources"] == ["axe-core", "adj-rules", "adj-vision"]  # any non-vision check is adj-rules


def test_page_level_adj_hit_attaches_to_html(scans):
    scans("compare", [adj_hit(0, "3.2.3", status="needs_review", rule_id="adj.compare.nav-order",
                              html="<html>", selector="html")])
    f = merged()[0]["F-before-home-el0000-3.2.3"]
    assert f["evidence"]["selector"] == "html" and f["evidence"]["html_snippet"] == "<html>"


def test_long_html_is_capped(scans):
    scans("rules", [adj_hit(135, "2.1.1", html="<a>" + "x" * 9000 + "</a>")])
    assert len(merged()[0]["F-before-home-el0135-2.1.1"]["evidence"]["html_snippet"]) == merge.HTML_CAP


@pytest.mark.parametrize("hits,body,match", [
    ([{"rule_id": "x", "sc": "2.1.1"}], None, "missing"),
    ([adj_hit(1, "2.1.1", status="notice")], None, "status 'notice'"),
    ([adj_hit(-1, "2.1.1")], None, "dom_index"),
    ([], {"check": "rules", "version": "after", "page": "home", "hits": []}, "expected before/home"),
    ([], {"check": "", "version": "before", "page": "home", "hits": []}, "check"),
])
def test_malformed_adj_file_fails_with_its_name(scans, tmp_path, hits, body, match):
    if body is None:
        scans("rules", hits)
    else:
        (tmp_path / "adj_rules_before_home.json").write_text(json.dumps(body))
    with pytest.raises(merge.AdjScanError, match=match) as e:
        merge.load_hits("before", "home")
    assert "data/scans/adj_rules_before_home.json" in str(e.value)


def test_other_pages_adj_files_are_ignored(scans, tmp_path):
    body = {"check": "rules", "version": "before", "page": "news", "hits": [adj_hit(9, "2.1.1")]}
    (tmp_path / "adj_rules_before_news.json").write_text(json.dumps(body))
    assert list(merged()[0]) == ["F-before-home-el0081-1.1.1"]


def test_router_needs_no_change_for_adj_findings(scans):
    """Decision 2: a deterministic rule (violation) is machine; a rule needing judgment (needs_review) is grey."""
    scans("rules", [adj_hit(135, "2.1.1"),
                    adj_hit(140, "2.4.4", status="needs_review", rule_id="adj.rules.link-lookalike")])
    by_id, _ = merged()
    assert route_finding(by_id["F-before-home-el0135-2.1.1"])["bucket"] == "machine"
    assert route_finding(by_id["F-before-home-el0140-2.4.4"])["bucket"] == "grey"


def test_primary_hit_is_unchanged_for_axe_and_pa11y():
    """The new SOURCE_ORDER sort picks the same primary hit and sources as the old axe-first sort,
    so regenerating data/findings/ with no ADJ files changes no existing finding."""
    import itertools
    kinds = [("axe-core", "violation"), ("axe-core", "needs_review"), ("pa11y", "needs_review"), ("pa11y", "notice")]
    for n in range(1, 5):
        for combo in itertools.permutations(kinds, n):
            hs = [{"scanner": s, "status": st, "rule_id": f"r{i}", "message": "m", "sc": "1.1.1",
                   "dom_index": 3, "selector": "img", "html": IMG, "axe_type": "x", "pa11y_type": "y"}
                  for i, (s, st) in enumerate(combo)]
            old = sorted(hs, key=lambda h: (h["scanner"] != "axe-core", -merge.STATUS_RANK[h["status"]]))
            old_sources = sorted({h["scanner"] for h in hs}, key=lambda s: s != "axe-core")
            new = merge.build_finding("before", "home", hs, URL, None)
            assert (new["rule_id"], new["source"], new["sources"]) == (old[0]["rule_id"], old[0]["scanner"],
                                                                        old_sources)
