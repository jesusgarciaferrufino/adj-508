"""Track R code rules (scanners/rules_scan.py), each on a tiny page built with set_content.

No network: every page is written here. The tests need Playwright's Chromium
(python -m playwright install chromium); without it they are skipped, not failed.
"""

import collections

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

import scanners.merge as merge
from scanners import rules_scan


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
    pg = browser.new_page(viewport=rules_scan.VIEWPORT)
    yield pg
    pg.close()


def hits_on(page, body, head=""):
    page.set_content(f"<!doctype html><html lang='en'><head>{head}</head><body>{body}</body></html>")
    return rules_scan.run_rules(page)


def of(hits, rule):
    return [h for h in hits if h["rule_id"] == f"adj.rules.{rule}"]


# A page that passes every rule; each test adds the one thing it is about.
NAV = '<nav><a href="#main">Skip to content</a> <a href="a.html">Home</a> <a href="b.html">News</a></nav>'
CLEAN = NAV + '<main id="main"><h1>Title</h1><p>Plain text with no links.</p></main>'


def test_clean_page_has_no_hits(page):
    assert hits_on(page, CLEAN) == []


# ---------------------------------------------------------------- the hit format


def test_hits_point_at_the_element_the_way_axe_scan_does(page):
    """dom_index is the querySelectorAll("*") position, the selector finds the same element,
    and html is its outerHTML: this is how merge joins a hit to axe's finding."""
    hits = hits_on(page, CLEAN + '<p><a href="x.html" onfocus="this.blur()">Go</a></p>'
                   '<div><div><span><select onchange="location.href=this.value"></select></span></div></div>')
    assert hits
    for h in hits:
        same, html = page.evaluate("([i, sel]) => { const el = document.querySelectorAll('*')[i];"
                                   " return [el === document.querySelector(sel), el.outerHTML]; }",
                                   [h["dom_index"], h["selector"]])
        assert same, h["selector"]
        assert h["html"] == html
        assert h["in_wrapper"] is False
        assert set(h) == set(merge.ADJ_HIT_KEYS)


def test_page_level_hit_uses_the_html_start_tag(page):
    [h] = of(hits_on(page, '<p>Text</p><a href="a.html">Home</a>'), "no-skip-link")
    assert (h["dom_index"], h["selector"], h["html"]) == (0, "html", '<html lang="en">')


def test_hits_in_the_w3c_wrapper_are_marked(page):
    hits = hits_on(page, '<div id="meta-header"><a href="x.html" onfocus="blur()">W3C</a></div>'
                   f'<div id="page">{CLEAN}</div>')
    assert hits and all(h["in_wrapper"] for h in hits)


def test_long_html_is_capped(page):
    long = "word " * 2000
    [h] = of(hits_on(page, CLEAN + f'<p><a href="x.html" onfocus="blur()">{long}</a></p>'), "blur-on-focus")[:1]
    assert len(h["html"]) == rules_scan.HTML_CAP


def test_written_file_is_accepted_by_merge(page, tmp_path, monkeypatch):
    """The output goes through Jesus's loader (scanners/merge.py) unchanged."""
    page.set_content("<!doctype html><html><body>" + CLEAN +
                     '<p><a href="x.html" onfocus="blur()">Go</a></p><input type="text"></body></html>')
    result = rules_scan.build_result(page, "before", "home", "https://example.test/before/home.html")
    out = rules_scan.write_result(result, tmp_path)
    assert out.name == "adj_rules_before_home.json"
    monkeypatch.setattr(merge, "SCANS", tmp_path)
    loaded = merge.load_adj_hits("before", "home", collections.Counter())
    assert len(loaded) == len(result["hits"]) == 5
    assert {h["scanner"] for h in loaded} == {"adj-rules"}
    assert result["tool_version"] == rules_scan.TOOL_VERSION


# ---------------------------------------------------------------- 1. blur-on-focus


@pytest.mark.parametrize("handler", ["blur();", "blur()", "this.blur()", "window.status=''; this.blur ( );"])
def test_blur_on_focus_fails_four_criteria(page, handler):
    hits = of(hits_on(page, CLEAN + f'<p><a href="x.html" onfocus="{handler}">Go</a></p>'), "blur-on-focus")
    assert [h["sc"] for h in hits] == ["2.1.1", "2.4.7", "3.2.1", "4.1.2"]
    assert {h["status"] for h in hits} == {"violation"}
    assert len({h["dom_index"] for h in hits}) == 1


@pytest.mark.parametrize("handler", ["highlight(this)", "blurb()", "this.blurred = true"])
def test_other_focus_handlers_are_not_blur(page, handler):
    assert of(hits_on(page, CLEAN + f'<p><a href="x.html" onfocus="{handler}">Go</a></p>'), "blur-on-focus") == []


# ---------------------------------------------------------------- 2. no-skip-link


@pytest.mark.parametrize("body", [
    '<a href="a.html">Home</a> <a href="b.html">News</a><main><p>Text</p></main>',               # no in-page link
    '<a href="#nowhere">Skip</a> <a href="a.html">Home</a><main><p>Text</p></main>',             # target missing
    '<a href="a.html">Home</a> <a href="#main">Skip</a><main id="main"><p>Text</p></main>',      # after the nav
    '<nav><a href="a.html">Home</a></nav><a href="#main">Skip</a><main id="main"></main>',       # after a nav landmark
])
def test_missing_or_late_skip_link_is_flagged(page, body):
    [h] = of(hits_on(page, body), "no-skip-link")
    assert (h["sc"], h["status"]) == ("2.4.1", "needs_review")


@pytest.mark.parametrize("body", [
    CLEAN,
    '<a href="#c">Skip</a><a href="a.html">Home</a><a name="c"></a><p>Text</p>',                 # named anchor target
    '<p>A page with no links at all.</p>',
])
def test_working_skip_link_or_no_links_is_fine(page, body):
    assert of(hits_on(page, body), "no-skip-link") == []


def test_skip_link_is_looked_for_inside_page_when_there_is_one(page):
    """On the demo, W3C's own skip link sits in #meta-header, outside #page; it doesn't count."""
    body = ('<div id="meta-header"><a href="#page">Skip to demo</a></div>'
            '<div id="page"><a href="a.html">Home</a><p>Text</p></div>')
    assert len(of(hits_on(page, body), "no-skip-link")) == 1


# ---------------------------------------------------------------- 3. no-headings


def test_heading_styled_text_without_headings_is_flagged(page):
    body = NAV + '<main id="main"><div style="font-size:24px">Welcome to CityLights</div><p>Story text.</p></main>'
    [h] = of(hits_on(page, body), "no-headings")
    assert (h["sc"], h["status"], h["dom_index"]) == ("2.4.6", "needs_review", 0)
    assert '"Welcome to CityLights"' in h["message"]


def test_bold_block_counts_as_heading_styled(page):
    body = NAV + '<main id="main"><p><b>Elsewhere on the Web</b></p><p>Story text.</p></main>'
    assert len(of(hits_on(page, body), "no-headings")) == 1


def test_real_headings_or_plain_text_are_fine(page):
    big_h2 = CLEAN.replace("<h1>Title</h1>", '<h2 style="font-size:24px">Title</h2>')
    assert of(hits_on(page, big_h2), "no-headings") == []
    assert of(hits_on(page, NAV + '<main id="main"><p>Only ordinary text here.</p></main>'), "no-headings") == []


def test_headings_message_never_quotes_dates_or_numbers(page):
    """Before home shows today's date in large type; a message must not change every day."""
    body = NAV + '<main id="main"><p><b>Sunday, 25 October 2026</b></p><p><b>City News</b></p></main>'
    [h] = of(hits_on(page, body), "no-headings")
    assert "2026" not in h["message"] and '"City News"' in h["message"]


# ---------------------------------------------------------------- 4. ascii-lookalike


def test_ascii_arrow_is_flagged_on_its_element(page):
    hits = of(hits_on(page, CLEAN + '<select aria-label="Menu"><option>QUICKMENU ----></option></select>'),
              "ascii-lookalike")
    [h] = hits
    assert (h["sc"], h["status"]) == ("1.1.1", "needs_review")
    assert h["html"].startswith("<option")


@pytest.mark.parametrize("content", ["<p>Pages 1 - 2, A -> B</p>", "<script>// --> </script>",
                                     "<style>/* ==> */</style>"])
def test_ordinary_dashes_and_code_are_not_ascii_art(page, content):
    assert of(hits_on(page, CLEAN + content), "ascii-lookalike") == []


# ---------------------------------------------------------------- 5. link-color-only

RUNNING = "This sentence has plenty of ordinary words around the link, {} and then it carries on."


def test_colour_only_inline_link_is_flagged(page):
    css = "<style>a { color: #226C8E; text-decoration: none }</style>"
    [h] = of(hits_on(page, CLEAN + "<p>" + RUNNING.format('<a href="x.html">Click here</a>') + "</p>", css),
             "link-color-only")
    assert (h["sc"], h["status"]) == ("1.4.1", "needs_review")
    assert '"Click here"' in h["message"]


@pytest.mark.parametrize("css", [
    "",                                                                        # browser default underline
    "a { text-decoration: none } a:hover { text-decoration: underline }",      # cue on hover
    "a { text-decoration: none } a:focus { text-decoration: underline }",      # cue on focus
    "a { text-decoration: none; font-weight: bold }",                          # bold
    "a { text-decoration: none; border-bottom: 1px solid }",                   # border
])
def test_link_with_another_cue_is_fine(page, css):
    body = CLEAN + "<p>" + RUNNING.format('<a href="x.html">Click here</a>') + "</p>"
    assert of(hits_on(page, body, f"<style>{css}</style>"), "link-color-only") == []


def test_links_outside_running_text_are_not_checked(page):
    """Menus and lists of links aren't running text; 1.4.1 F73 is about links in a sentence."""
    css = "<style>a { text-decoration: none }</style>"
    body = CLEAN + '<ul><li><a href="a.html">Home</a></li><li><a href="b.html">News</a></li></ul>'
    assert of(hits_on(page, body, css), "link-color-only") == []


def test_focus_check_survives_onfocus_blur(page):
    """Focus is forced through DevTools, so a link that blurs itself is still checked, once."""
    css = "<style>a { text-decoration: none }</style>"
    body = CLEAN + "<p>" + RUNNING.format('<a href="x.html" onfocus="blur()">Read more</a>') + "</p>"
    assert len(of(hits_on(page, body, css), "link-color-only")) == 1


# ---------------------------------------------------------------- 6. select-navigates


@pytest.mark.parametrize("handler", ["location.href = this.value;", "window.location = this.value",
                                     "window.open(this.value)"])
def test_select_that_navigates_on_change_is_a_violation(page, handler):
    body = CLEAN + f'<label>Go <select onchange="{handler}"><option>a</option></select></label>'
    [h] = of(hits_on(page, body), "select-navigates")
    assert (h["sc"], h["status"]) == ("3.2.2", "violation")


def test_select_with_other_onchange_is_fine(page):
    body = CLEAN + '<label>Size <select onchange="updatePrice(this)"><option>a</option></select></label>'
    assert of(hits_on(page, body), "select-navigates") == []


# ---------------------------------------------------------------- 7. control-no-label


@pytest.mark.parametrize("control", ['<input type="text">', '<input>', '<input type="radio" name="r">',
                                     "<select><option>a</option></select>", "<textarea></textarea>",
                                     '<label for="x"> </label><input id="x">'])
def test_unlabelled_control_is_a_violation(page, control):
    [h] = of(hits_on(page, CLEAN + control), "control-no-label")
    assert (h["sc"], h["status"]) == ("3.3.2", "violation")


@pytest.mark.parametrize("control", [
    '<label for="x">Name</label><input id="x">',
    "<label>Name <input></label>",
    '<input aria-label="Name">',
    '<span id="l">Name</span><input aria-labelledby="l">',
    '<input title="Name">',
    '<input type="submit" value="Send">', '<input type="hidden" name="t">',
    '<input type="image" alt="Go" src="go.png">',
    '<input type="text" style="display:none">',
])
def test_labelled_or_self_named_control_is_fine(page, control):
    assert of(hits_on(page, CLEAN + control), "control-no-label") == []


# ---------------------------------------------------------------- 8. layout-table-th


@pytest.mark.parametrize("table", [
    '<table><tr><th><img src="h.gif" alt="Prices"></th></tr></table>',                           # one row
    '<table><tr><th>Q</th><td>a</td></tr><tr><td><table><tr><td>x</td></tr></table></td></tr></table>',  # nested
    '<table role="presentation"><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>',
    '<table><tr><th>A</th></tr><tr><td>1</td></tr></table>',                                     # one column
])
def test_th_in_a_layout_table_is_flagged(page, table):
    hits = of(hits_on(page, CLEAN + table), "layout-table-th")
    assert hits and {(h["sc"], h["status"]) for h in hits} == {("1.3.1", "needs_review")}
    assert all(h["html"].startswith("<th") for h in hits)


def test_th_in_a_data_table_is_fine(page):
    table = ("<table><caption>Prices</caption><tr><th>Event</th><th>Adult</th></tr>"
             "<tr><td>Concert</td><td>10</td></tr></table>")
    assert of(hits_on(page, CLEAN + table), "layout-table-th") == []


def test_only_the_layout_tables_own_th_are_flagged(page):
    """A data table nested inside a layout table keeps its th unflagged."""
    table = ("<table><tr><td><table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>"
             "</td></tr></table>")
    assert of(hits_on(page, CLEAN + table), "layout-table-th") == []


# ---------------------------------------------------------------- 9. css-bg-info

PIXEL = "data:image/gif;base64,R0lGODlhAQABAIAAAP///wAAACH5BAEAAAAALAAAAAABAAEAAAICRAEAOw=="


def test_textless_css_background_image_is_flagged(page):
    body = CLEAN + f'<div title="image" style="width:100px;height:80px;background:url({PIXEL})"></div>'
    [h] = of(hits_on(page, body), "css-bg-info")
    assert (h["sc"], h["status"]) == ("1.1.1", "needs_review")


@pytest.mark.parametrize("div", [
    f'<div style="width:100px;height:80px;background:url({PIXEL})">Penguins</div>',               # has text
    f'<div aria-label="Penguins" style="width:100px;height:80px;background:url({PIXEL})"></div>', # named
    f'<div style="width:10px;height:10px;background:url({PIXEL})"></div>',                        # tiny
    '<div style="width:100px;height:80px;background:linear-gradient(red, blue)"></div>',          # no image
])
def test_background_with_text_or_name_or_tiny_is_fine(page, div):
    assert of(hits_on(page, CLEAN + div), "css-bg-info") == []


# ---------------------------------------------------------------- audit follow-ups


def test_label_with_only_an_image_alt_counts_as_a_label(page):
    body = CLEAN + '<label for="q"><img alt="Search" src="s.gif"></label><input id="q">'
    assert of(hits_on(page, body), "control-no-label") == []


@pytest.mark.parametrize("handler", ["this.form.location.value = 1", "if (location == 'x') go()",
                                     "check(document.location.href)"])
def test_select_that_only_reads_location_is_fine(page, handler):
    body = CLEAN + f'<label>Go <select onchange="{handler}"><option>a</option></select></label>'
    assert of(hits_on(page, body), "select-navigates") == []


def test_selector_escapes_odd_tag_names(page):
    """Word-pasted HTML has tags like <o:p>; the selector must still be valid CSS."""
    page.set_content(f"<!doctype html><html><body>{CLEAN}<p><o:p><a href='x.html' onfocus='blur()'>Go</a></o:p>"
                     "</p></body></html>")
    [h] = of(rules_scan.run_rules(page), "blur-on-focus")[:1]
    assert page.evaluate("([i, sel]) => document.querySelectorAll('*')[i] === document.querySelector(sel)",
                         [h["dom_index"], h["selector"]])


# ---------------------------------------------------------------- no browser needed (these also run in CI)


def test_every_message_is_used_by_a_rule():
    code = "".join(v for k, v in vars(rules_scan).items() if k.isupper() and isinstance(v, str))
    for key in rules_scan.MESSAGES:
        rule, sc = key.split()
        assert f'"{rule}"' in code and f'"{sc}"' in code, key


def test_write_result_names_the_file_for_merge(tmp_path):
    result = {"check": "rules", "version": "after", "page": "survey", "url": "u", "tool_version": "rules-v1",
              "hits": []}
    assert rules_scan.write_result(result, tmp_path) == tmp_path / "adj_rules_after_survey.json"


def test_summarize_counts_per_rule_and_leaves_out_the_wrapper():
    hit = {"rule_id": "adj.rules.blur-on-focus", "in_wrapper": False}
    result = {"hits": [hit, hit, {**hit, "in_wrapper": True}]}
    assert rules_scan.summarize(result) == "  2 hits (blur-on-focus 2; wrapper 1)"


# ---------------------------------------------------------------- no-skip-link: certain when nothing bypasses


MENU = ("<table><tr>" + "".join(f'<td><a href="{p}.html">{p}</a></td>' for p in ("home", "news", "survey"))
        + "</tr></table>")


def test_no_skip_link_no_headings_no_landmarks_is_a_violation(page):
    """A menu to bypass, and G1, H69 and ARIA11 all absent: 2.4.1 fails for certain."""
    body = f'<div id="meta-header"><h1>W3C demo</h1></div><div id="page">{MENU}<p>Story text.</p></div>'
    [h] = of(hits_on(page, body), "no-skip-link")
    assert (h["status"], h["dom_index"]) == ("violation", 0)
    assert "no headings and no landmarks" in h["message"]


@pytest.mark.parametrize("extra", ["<h2>News</h2>", "<main><p>Story</p></main>", '<div role="navigation"></div>',
                                   '<iframe title="t" srcdoc="x"></iframe>'])
def test_no_skip_link_stays_needs_review_when_another_bypass_exists(page, extra):
    [h] = of(hits_on(page, f'<div id="page">{MENU}{extra}</div>'), "no-skip-link")
    assert h["status"] == "needs_review"
    assert "no headings and no landmarks" not in h["message"]


def test_no_skip_link_is_not_certain_without_a_menu(page):
    """A page with one link in a sentence (a login or error page) has no repeated block to prove."""
    [h] = of(hits_on(page, '<p>Read our <a href="terms.html">terms</a>.</p>'), "no-skip-link")
    assert h["status"] == "needs_review"


def test_headings_and_landmarks_outside_page_still_count(page):
    """On a real site #page can be just the content wrapper; its header and nav sit outside it."""
    body = f'<header><h1>Site</h1><nav>{MENU}</nav></header><div id="page"><a href="x.html">More</a></div>'
    assert [h["status"] for h in of(hits_on(page, body), "no-skip-link")] == ["needs_review"]


# ---------------------------------------------------------------- 10. layout-table-order (1.3.2 F49)


def test_layout_table_with_nested_tables_is_flagged_once(page):
    inner = "<td><table><tr><td>{}</td></tr></table></td>"
    table = "<table><tr>" + inner.format("Nav") + inner.format("News") + "</tr></table>"
    [h] = of(hits_on(page, CLEAN + table), "layout-table-order")
    assert (h["sc"], h["status"]) == ("1.3.2", "needs_review")
    assert h["html"].startswith("<table") and "2 nested table(s)" in h["message"]


def test_plain_data_table_is_not_a_layout_table_for_order(page):
    table = "<table><tr><th>Event</th><th>Price</th></tr><tr><td>Concert</td><td>10</td></tr></table>"
    assert of(hits_on(page, CLEAN + table), "layout-table-order") == []


# ---------------------------------------------------------------- 11. css-reading-order (1.3.2 F1)

# Before home's teaser: three rows (headlines, images, stories), each row three floated columns.
# The code reads all headlines, then all images, then all stories; the eye reads each column.
COL = 'style="float:left;width:120px;height:{h}px"'
GRID = "".join(
    '<div style="float:left;width:400px">'
    + "".join(f'<div {COL.format(h=h)}>{label} {n}</div>' for n in (1, 2, 3))
    + "</div>"
    for label, h in (("Headline", 30), ("Story", 60)))


def test_floats_that_stack_columns_across_rows_are_flagged(page):
    hits = of(hits_on(page, CLEAN + f'<div style="width:400px">{GRID}</div>'), "css-reading-order")
    assert {(h["sc"], h["status"]) for h in hits} == {("1.3.2", "needs_review")}
    # The three stories, each directly below its headline; not the row containers.
    assert [h["html"].endswith(f"Story {n}</div>") for n, h in zip((1, 2, 3), hits, strict=True)] == [True] * 3
    assert '"Headline 2"' in hits[0]["message"]


def test_one_float_per_row_or_whole_columns_are_fine(page):
    """After's layout: each column is one block, so the code reads a column at a time."""
    whole_columns = "".join(f'<div style="float:left;width:120px"><p>Headline {n}</p><p>Story {n}</p></div>'
                            for n in (1, 2, 3))
    stacked = ('<div style="float:left;width:300px;height:40px">One</div>'
               '<div style="float:left;clear:left;width:300px;height:40px">Two</div>')
    assert of(hits_on(page, CLEAN + whole_columns), "css-reading-order") == []
    assert of(hits_on(page, CLEAN + stacked), "css-reading-order") == []


def test_a_story_with_one_floated_photo_is_still_a_block(page):
    """News "Your Shout" holds a small floated photo; it must still count as a block in its column."""
    photo = f'<img style="float:left;width:60px;height:60px" src="{PIXEL}" alt="Clara">'
    body = ('<div style="width:500px">'
            '<div style="float:left;width:240px;height:50px">Left top</div>'
            '<div style="float:right;width:240px;height:50px">Right top</div>'
            '<div style="float:left;width:240px;height:80px">Left bottom</div>'
            f'<div style="float:right;width:240px;height:80px">{photo}Your Shout</div></div>')
    hits = of(hits_on(page, CLEAN + body), "css-reading-order")
    assert any("Your Shout" in h["message"] for h in hits)


# ---------------------------------------------------------------- 12. image-of-text (1.4.5)


@pytest.mark.parametrize(("src", "w", "h"), [("nav_home.gif", 88, 27), ("headline_ticket_offers.gif", 216, 21),
                                             ("telefon.png", 110, 20)])
def test_short_wide_graphic_is_flagged_as_image_of_text(page, src, w, h):
    body = CLEAN + f'<img src="./img/{src}" width="{w}" height="{h}" style="display:inline-block">'
    [hit] = of(hits_on(page, body), "image-of-text")
    assert (hit["sc"], hit["status"]) == ("1.4.5", "needs_review")
    assert src in hit["message"] and f"{w}x{h} px" in hit["message"]


@pytest.mark.parametrize(("src", "w", "h", "alt"), [
    ("toplogo.png", 443, 30, "Citylights"),        # logo: exempt from 1.4.5
    ("weather.png", 145, 86, "Sunny Spells"),      # too tall
    ("photo.jpg", 300, 40, "Penguins"),            # a photograph
    ("morearrow.gif", 48, 10, ""),                 # tiny arrow
    ("marker.gif", 78, 1, ""),                     # a rule line
    ("square.gif", 40, 40, "x"),                   # not wide
])
def test_logos_photos_and_spacers_are_not_images_of_text(page, src, w, h, alt):
    body = CLEAN + f'<img src="./img/{src}" width="{w}" height="{h}" alt="{alt}" style="display:inline-block">'
    assert of(hits_on(page, body), "image-of-text") == []


def test_a_uniform_card_grid_is_meant_to_be_read_row_by_row(page):
    card = '<div class="card" style="float:left;width:120px;height:80px">Product {}</div>'
    grid = '<div style="width:360px">' + "".join(card.format(n) for n in range(1, 7)) + "</div>"
    assert of(hits_on(page, CLEAN + grid), "css-reading-order") == []


def test_reading_order_names_image_blocks_by_tag(page):
    row = '<div style="float:left;width:300px">{}</div>'
    blocks = (row.format('<div style="float:left;width:140px;height:30px">Headline A</div>'
                         '<div style="float:left;width:140px;height:30px">Headline B</div>')
              + row.format('<svg class="pic" style="float:left;width:140px;height:60px"></svg>'
                           '<div style="float:left;width:140px;height:60px">Story B</div>'))
    hits = of(hits_on(page, CLEAN + f'<div style="width:300px">{blocks}</div>'), "css-reading-order")
    assert hits and '<svg class="pic">' in hits[0]["message"] and "object" not in hits[0]["message"]


@pytest.mark.parametrize(("src", "name"), [(f"{PIXEL}", "(inline image)"), ("", "(no src)"),
                                           ("./img/logout.png", "logout.png")])
def test_image_of_text_names_odd_sources_and_does_not_skip_logout(page, src, name):
    body = CLEAN + f'<img src="{src}" width="100" height="25" alt="x" style="display:inline-block">'
    [h] = of(hits_on(page, body), "image-of-text")
    assert f"Image {name} (100x25 px)" in h["message"]
