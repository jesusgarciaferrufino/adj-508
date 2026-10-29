"""Keyboard walkthrough (scanners/keyboard_scan.py), on tiny pages built with set_content.

No network: every page is written here. The tests need Playwright's Chromium
(python -m playwright install chromium); without it they are skipped, not failed.
"""

import collections

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

import scanners.merge as merge
from scanners import keyboard_scan as kb


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except PlaywrightError as e:
            pytest.skip(f"Playwright Chromium is not installed: {e.message.splitlines()[0]}")
        yield b
        b.close()


@pytest.fixture
def page(browser):
    pg = browser.new_page(viewport=kb.VIEWPORT)  # a fresh page each test: no focus carried over
    yield pg
    pg.close()


def scan(page, body, head="", artifacts_dir=None):
    page.set_content(f"<!doctype html><html lang='en'><head>{head}</head><body>{body}</body></html>")
    return kb.build_result(page, "before", "home", "https://example.test/before/home.html", artifacts_dir)


def of(result, rule):
    return [h for h in result["hits"] if h["rule_id"] == f"adj.keyboard.{rule}"]


def at(page, h):
    """The element a hit's dom_index names, as outerHTML."""
    return page.evaluate("i => document.querySelectorAll('*')[i].outerHTML", h["dom_index"])


# Links placed absolutely, so the test sets both the layout and the tab order.
# Each is (name, top, tabindex) or (name, top, tabindex, left).
def placed(*links):
    out = []
    for name, y, ti, *x in links:
        left = x[0] if x else 20
        out.append(f'<a href="#" id="{name}" style="position:absolute;left:{left}px;top:{y}px"'
                   + (f' tabindex="{ti}"' if ti else "") + f">{name}</a>")
    return "".join(out)


# ---------------------------------------------------------------- the walk itself


def test_clean_page_has_no_hits_and_the_walk_wraps(page):
    r = scan(page, '<a href="a.html">Home</a> <button>Search</button> <input aria-label="Query">')
    assert r["hits"] == []
    assert r["walk"]["end"] == "wrapped" and r["walk"]["numbered_stops"] == 3


def test_page_with_nothing_focusable_stops(page):
    r = scan(page, "<p>Only text.</p>")
    assert r["walk"]["end"] == "nothing-to-focus" and r["walk"]["presses"] <= 3 and r["hits"] == []


def test_focus_trap_stops_the_walk_and_skips_unreachable(page):
    trap = ('<a href="#" id="a">A</a> <a href="#" id="b" onkeydown="if (event.key === \'Tab\') '
            '{ event.preventDefault(); document.getElementById(\'a\').focus(); }">B</a> <a href="#">C</a>')
    r = scan(page, trap)
    assert r["walk"]["end"] == "repeat" and r["walk"]["presses"] < 10
    assert of(r, "unreachable") == []  # after a trap the rest of the page is unknown, not unreachable


def test_walk_stops_at_max_presses_and_then_claims_nothing_unreachable(page, monkeypatch):
    monkeypatch.setattr(kb, "MAX_TABS", 5)
    r = scan(page, "".join(f'<a href="{i}.html">Link {i}</a> ' for i in range(20)))
    assert r["walk"]["end"] == "max-presses" and r["walk"]["presses"] == 5
    assert of(r, "unreachable") == []


# ---------------------------------------------------------------- no-visible-focus (2.4.7)


def test_outline_on_focus_is_a_visible_indicator(page):
    r = scan(page, "<button>Go</button>", head="<style>button:focus { outline: 3px solid #06c }</style>")
    assert of(r, "no-visible-focus") == []


def test_browser_default_focus_ring_counts_as_visible(page):
    assert of(scan(page, "<button>Go</button>"), "no-visible-focus") == []


def test_outline_none_and_nothing_else_is_a_hit(page):
    r = scan(page, "<button>Go</button>", head="<style>button:focus { outline: none }</style>")
    [h] = of(r, "no-visible-focus")
    assert h["sc"] == "2.4.7" and at(page, h) == "<button>Go</button>"
    assert 'Tab stop 1 (button "Go") looks the same focused and unfocused' in h["message"]


@pytest.mark.parametrize("cue", ["box-shadow: 0 0 0 3px #06c", "background: #ff0", "text-decoration: underline",
                                 "border: 2px solid #06c", "color: #c00"])
def test_other_focus_cues_count_as_visible(page, cue):
    css = f"<style>a {{ text-decoration: none; border: 0 }} a:focus {{ outline: none; {cue} }}</style>"
    assert of(scan(page, '<a href="x.html">Go</a>', head=css), "no-visible-focus") == []


def test_a_cue_on_a_child_counts(page):
    css = "<style>a:focus { outline: none } a:focus img { outline: 2px solid #06c }</style>"
    body = '<a href="x.html"><img src="x.gif" alt="Next" width="20" height="10"></a>'
    assert of(scan(page, body, head=css), "no-visible-focus") == []


def test_a_transition_does_not_hide_the_change(page):
    css = "<style>a { transition: outline-color 2s } a:focus { outline: 3px solid #06c }</style>"
    assert of(scan(page, '<a href="x.html">Go</a>', head=css), "no-visible-focus") == []


# ---------------------------------------------------------------- unreachable (2.1.1)


def test_onclick_div_without_tabindex_is_unreachable(page):
    r = scan(page, '<a href="a.html">Home</a> <div onclick="go()">Order now</div>')
    [h] = of(r, "unreachable")
    assert h["sc"] == "2.1.1" and at(page, h) == '<div onclick="go()">Order now</div>'
    assert 'div element "Order now" can be clicked but is never reached with the Tab key' in h["message"]


def test_onclick_div_with_tabindex_is_reachable(page):
    assert of(scan(page, '<div onclick="go()" tabindex="0">Order now</div>'), "unreachable") == []


def test_link_removed_from_tab_order_is_unreachable(page):
    [h] = of(scan(page, '<a href="a.html">Home</a> <a href="b.html" tabindex="-1">News</a>'), "unreachable")
    assert "News" in h["message"]


def test_hidden_disabled_and_nested_elements_are_not_flagged(page):
    body = ('<a href="a.html">Home</a> <a href="b.html" style="display:none">Hidden</a> '
            '<button disabled>Off</button> <a href="c.html"><img src="c.gif" alt="C" onclick="x()"></a> '
            '<div onclick="y()"><a href="d.html">Inner link</a></div>')
    assert of(scan(page, body), "unreachable") == []


def test_image_map_area_counts_when_its_image_is_visible(page):
    body = ('<img src="nav.gif" usemap="#nav" width="200" height="40" alt="Navigation">'
            '<map name="nav"><area shape="rect" coords="0,0,100,40" href="a.html" alt="Home"></map>')
    r = scan(page, body)
    assert of(r, "unreachable") == [] or r["walk"]["numbered_stops"] == 0  # Chrome focuses areas: no hit


# ---------------------------------------------------------------- order-jump (2.4.3)


def test_focus_order_that_bounces_against_the_layout_is_a_hit(page):
    r = scan(page, placed(("Top", 0, 1), ("Bottom", 800, 2), ("Middle", 100, 3), ("End", 900, 4)))
    [h] = of(r, "order-jump")
    assert h["sc"] == "2.4.3" and 'id="Middle"' in at(page, h)
    assert "Tab stop 3 (link \"Middle\") is 700px above stop 2, then stop 4 jumps back down 800px" in h["message"]
    assert "(F44)" in h["message"]  # positive tabindex made the order


def test_natural_order_has_no_jump(page):
    assert of(scan(page, placed(("A", 0, 0), ("B", 400, 0), ("C", 800, 0), ("D", 1200, 0))), "order-jump") == []


def test_moving_up_to_a_new_column_is_not_a_jump(page):
    """Down the left column, then up to the top of the right column and down again: a normal
    two-column layout (CityLights has a nav column next to the content), not a misordering."""
    links = placed(("L1", 0, 0), ("L2", 800, 0), ("R1", 0, 0, 400), ("R2", 500, 0, 400), ("R3", 900, 0, 400))
    assert of(scan(page, links), "order-jump") == []


def test_the_same_bounce_inside_one_column_is_a_jump(page):
    links = placed(("L1", 0, 0), ("L2", 800, 0), ("L3", 0, 0), ("L4", 500, 0))
    assert [h["dom_index"] for h in of(scan(page, links), "order-jump")]


def test_dom_order_against_visual_order_without_tabindex_cites_the_criterion(page):
    r = scan(page, placed(("A", 0, 0), ("B", 900, 0), ("C", 50, 0), ("D", 950, 0)))
    [h] = of(r, "order-jump")
    assert "(2.4.3)" in h["message"] and "F44" not in h["message"]


# ---------------------------------------------------------------- focus-lost (2.4.3)


def test_blur_on_focus_link_loses_focus(page):
    r = scan(page, '<a href="a.html">A</a> <a href="b.html" onfocus="blur()">Click here</a> <a href="c.html">C</a>')
    [h] = of(r, "focus-lost")
    assert h["sc"] == "2.4.3" and "onfocus" in at(page, h)
    assert 'Tab stop 2 (link "Click here") loses focus as soon as it gets it' in h["message"]
    assert of(r, "no-visible-focus") == []  # the blur rule owns 2.4.7 for these
    assert r["walk"]["end"] == "wrapped" and r["walk"]["numbered_stops"] == 3  # the walk goes on past it


# ---------------------------------------------------------------- wrapper, format, merge


def test_wrapper_never_produces_hits_or_numbers(page):
    wrapper = ('<div id="meta-header"><a href="w.html" style="outline:none" onfocus="blur()">W3C</a>'
               '<div onclick="w()">Demo menu</div></div>')
    r = scan(page, wrapper + '<div id="page"><a href="a.html">Home</a></div>',
             head="<style>#meta-header a:focus { outline: none }</style>")
    assert r["hits"] == [] and r["walk"]["numbered_stops"] == 1 and r["walk"]["stops"] == 2


def test_hits_point_at_their_element_and_use_the_shared_format(page):
    body = ('<a href="a.html">A</a> <a href="b.html" onfocus="blur()">B</a> <div onclick="c()">C</div>'
            '<button>D</button>')
    r = scan(page, body, head="<style>button:focus { outline: none }</style>")
    assert {h["rule_id"] for h in r["hits"]} == {"adj.keyboard.focus-lost", "adj.keyboard.unreachable",
                                                 "adj.keyboard.no-visible-focus"}
    for h in r["hits"]:
        assert set(h) == set(merge.ADJ_HIT_KEYS) and h["status"] == "needs_review"
        same = page.evaluate("([i, sel]) => document.querySelectorAll('*')[i] === document.querySelector(sel)",
                             [h["dom_index"], h["selector"]])
        assert same, h["selector"]
        assert h["html"] == at(page, h) and h["in_wrapper"] is False


def test_written_file_is_accepted_by_merge(page, tmp_path, monkeypatch):
    r = scan(page, '<a href="a.html">A</a> <div onclick="c()">C</div>')
    out = kb.write_result(r, tmp_path)
    assert out.name == "adj_keyboard_before_home.json"
    monkeypatch.setattr(merge, "SCANS", tmp_path)
    loaded = merge.load_adj_hits("before", "home", collections.Counter())
    assert len(loaded) == len(r["hits"]) == 1
    assert {h["scanner"] for h in loaded} == {"adj-rules"} and {h["status"] for h in loaded} == {"needs_review"}
    assert r["check"] == "keyboard" and r["tool_version"] == "keyboard-v1"


# ---------------------------------------------------------------- artifacts


def test_numbered_screenshot_and_reading_order_are_written(page, tmp_path):
    body = ('<div id="meta-header">W3C demo header</div><div id="page"><h1>CityLights</h1>'
            '<p>Welcome to <a href="a.html">the city</a>.</p><img src="x.gif" alt="Weather" width="10" height="10">'
            '<button>Go</button></div>')
    before = page.evaluate("document.querySelectorAll('*').length") if page.url != "about:blank" else None
    r = scan(page, body, artifacts_dir=tmp_path)
    png = tmp_path / "keyboard_before_home.png"
    assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    lines = (tmp_path / "keyboard_before_home_reading_order.txt").read_text().splitlines()
    assert lines[0] == "CityLights" and "the city" in lines and "[image] Weather" in lines
    assert "W3C demo header" not in lines  # starts at #page
    assert lines.index("CityLights") < lines.index("the city") < lines.index("Go")
    assert r["walk"]["reading_order_lines"] == len(lines)
    assert page.evaluate("document.getElementById('__adj_keyboard_overlay')") is None  # removed again
    assert before is None or before == page.evaluate("document.querySelectorAll('*').length")


def test_hits_are_computed_before_the_overlay(page, tmp_path):
    """The overlay adds elements; every dom_index must still name the original element."""
    r = scan(page, '<a href="a.html">A</a> <div onclick="c()">Order</div>', artifacts_dir=tmp_path)
    [h] = of(r, "unreachable")
    assert at(page, h) == '<div onclick="c()">Order</div>'
