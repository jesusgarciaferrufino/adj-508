"""Page comparison check (scanners/compare_scan.py) for 3.2.4.

compare() is pure, so most tests give it link lists directly (no browser). The links in
BEFORE and AFTER are what links_on() returned on the real W3C pages in Oct 2026 (names cut
short). The last tests load tiny pages with set_content and are skipped without Playwright's
Chromium. No network.
"""

import collections

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

import scanners.merge as merge
from scanners import compare_scan as cs


def link(target, name, dom_index, in_wrapper=False):
    return {"target": target, "name": name, "dom_index": dom_index, "selector": f"#x{dom_index}",
            "html": f"<a>{name}</a>", "in_wrapper": in_wrapper}


def nav(*names):
    targets = ["javascript:location.href='home.html';", "javascript:location.href='news.html';",
               "javascript:location.href='tickets.html';", "javascript:location.href='survey.html';"]
    return [link(t, n, 135 + 7 * i) for i, (t, n) in enumerate(zip(targets, names, strict=True))]


FOOTER = [link("https://www.w3.org/", "W3C", 300), link("http://www.keio.ac.jp/", "Keio", 301)]
LOGO = [link("https://www.w3.org/WAI/demos/bad/before/home.html", "Red dot with a white letter 'C'", 76)]
BEFORE = {
    "home": LOGO + nav("", "", "", "") + [link("offsite.html", "Click here", 210),
                                          link("offsite.html", "Click here", 213)] + FOOTER,
    "news": LOGO + nav("", "", "", "") + [link("offsite.html", "trombone forgery debacle", 175),
                                          link("offsite.html", "the way that air conditioning works", 180),
                                          link("offsite.html", "picture of Clara F.", 200)] + FOOTER,
    "tickets": LOGO + nav("", "", "", "") + FOOTER,
    "survey": LOGO + nav("", "", "", "") + FOOTER,
}
A = "https://www.w3.org/WAI/demos/bad/after/"
AFTER = {  # each page's own nav item is not a link
    "home": [link(A + "news.html", "News", 99), link(A + "tickets.html", "Tickets", 101),
             link(A + "survey.html", "Survey", 103)] + FOOTER,
    "news": [link(A + "home.html", "Citylights: your access to the city.", 62), link(A + "home.html", "Home", 97),
             link(A + "tickets.html", "Tickets", 101), link(A + "survey.html", "Survey", 103)] + FOOTER,
    "tickets": [link(A + "home.html", "Citylights: your access to the city.", 62), link(A + "home.html", "Home", 97),
                link(A + "news.html", "News", 99), link(A + "survey.html", "Survey", 103)] + FOOTER,
    "survey": [link(A + "home.html", "Citylights: your access to the city.", 62), link(A + "home.html", "Home", 97),
               link(A + "news.html", "News", 99), link(A + "tickets.html", "Tickets", 101)] + FOOTER,
}


def test_before_nav_is_not_identified_on_any_page():
    hits = cs.compare(BEFORE)
    for page in BEFORE:
        assert [h["dom_index"] for h in hits[page]] == [135, 142, 149, 156]
        assert {h["rule_id"] for h in hits[page]} == {"adj.compare.unidentified"}
    h = hits["home"][1]
    assert (h["sc"], h["status"], h["in_wrapper"]) == ("3.2.4", "needs_review", False)
    assert "javascript:location.href='news.html';" in h["message"] and "4 of the 4 pages" in h["message"]


def test_after_has_no_hits():
    assert all(h == [] for h in cs.compare(AFTER).values())


def test_a_page_that_names_the_link_differently_is_flagged():
    pages = {"home": nav("Home", "News", "Tickets", "Survey"), "news": nav("Home", "Latest", "Tickets", "Survey"),
             "tickets": nav("Home", "News", "Tickets", "Survey")}
    hits = cs.compare(pages)
    assert hits["home"] == [] and hits["tickets"] == []
    [h] = hits["news"]
    assert h["rule_id"] == "adj.compare.inconsistent-name"
    assert '"Latest" here but "News" on home, tickets' in h["message"]


def test_case_and_spacing_do_not_count_as_different():
    pages = {"home": [link("a.html", "News", 1)], "news": [link("a.html", "news", 1)]}
    assert all(h == [] for h in cs.compare(pages).values())


def test_links_on_one_page_or_unpairable_counts_are_skipped():
    pages = {"home": [link("only-here.html", "", 1), link("x.html", "", 2), link("x.html", "", 3)],
             "news": [link("x.html", "", 4)]}
    assert all(h == [] for h in cs.compare(pages).values())


def test_wrapper_links_are_ignored():
    pages = {p: [link("w3c.html", "", 1, in_wrapper=True)] for p in ("home", "news")}
    assert all(h == [] for h in cs.compare(pages).values())


def test_results_have_one_file_per_page_and_merge_accepts_them(tmp_path, monkeypatch):
    urls = {p: f"u/{p}" for p in BEFORE}
    results = cs.build_results("before", BEFORE, urls)
    assert [(r["check"], r["page"], len(r["hits"])) for r in results] == [
        ("compare", "home", 4), ("compare", "news", 4), ("compare", "tickets", 4), ("compare", "survey", 4)]
    for r in results:
        cs.write_result(r, tmp_path)
        assert all(set(h) == set(merge.ADJ_HIT_KEYS) for h in r["hits"])
    monkeypatch.setattr(merge, "SCANS", tmp_path)
    dropped = collections.Counter()
    hits = merge.load_adj_hits("before", "news", dropped)
    assert [(h["scanner"], h["sc"], h["dom_index"]) for h in hits] == [("adj-rules", "3.2.4", i)
                                                                     for i in (135, 142, 149, 156)]


# ---------------------------------------------------------------- links_on() in a real browser


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
    pg = browser.new_page(viewport=cs.VIEWPORT)
    yield pg
    pg.close()


def test_names_come_from_text_alt_aria_and_title(page):
    page.set_content("<!doctype html><html><body>"
                     '<a href="a.html">Plain <b>text</b></a>'
                     '<a href="b.html"><img src="b.gif" alt="Image alt"></a>'
                     '<a href="c.html"><img src="c.gif"></a>'
                     '<a href="d.html" aria-label="Label"><img src="d.gif"></a>'
                     '<span id="lb">By id</span><a href="e.html" aria-labelledby="lb">x</a>'
                     '<a href="f.html" title="Title only"></a>'
                     '<a href="g.html"><span aria-hidden="true">hidden</span></a>'
                     "</body></html>")
    names = [x["name"] for x in cs.links_on(page)]
    assert names == ["Plain text", "Image alt", "", "Label", "By id", "Title only", ""]


def test_links_point_at_the_element_the_way_axe_scan_does(page):
    page.set_content('<!doctype html><html><body><div id="meta-header"><a href="w.html">W3C</a></div>'
                     '<div id="page"><p><a href="x.html">X</a></p></div></body></html>')
    links = cs.links_on(page)
    assert [x["in_wrapper"] for x in links] == [False]  # only #page is listed when it exists
    [x] = links
    same, html = page.evaluate("([i, sel]) => { const el = document.querySelectorAll('*')[i];"
                               " return [el === document.querySelector(sel), el.outerHTML]; }",
                               [x["dom_index"], x["selector"]])
    assert same and html == x["html"]
