"""Validator check (scanners/validator_scan.py): which Nu messages count for 4.1.1, and the hit it writes.

No network. Most tests feed the check Nu messages copied from real runs on the W3C pages, so they
need neither Java nor vnu.jar. The last test runs the real checker (npm's vnu-jar) and is skipped without it.
"""

import collections
import shutil

import pytest

import scanners.merge as merge
from scanners import validator_scan as vs


def msg(line, text, sub=None):
    m = {"type": "error", "lastLine": line, "message": text}
    if sub:
        m["subType"] = sub
    return m


# Real Nu messages from before/home.html (Oct 2026): first run on the page as served...
HOME_SERVED = [
    msg(1, "Start tag seen without seeing a doctype first. Expected “<!DOCTYPE html>”."),
    msg(39, "CSS: “background”: “#eeeee” is not a “background-color” value."),
    msg(143, "Bad start tag in “b” in “noscript” in “head”."),
    msg(143, "The “font” element is obsolete. Use CSS instead."),
    msg(143, "Stray end tag “noscript”."),
    msg(145, "Stray end tag “head”."),
    msg(146, "Start tag “body” seen but an element of the same type was already open."),
    msg(146, "Cannot recover after last error. Any further errors will be ignored.", "fatal"),
]
# ...then with the <noscript> in <head> blanked out.
HOME_BLANKED = [
    msg(160, "Bad value “100%” for attribute “width” on element “table”: Expected a digit but saw “%” instead."),
    msg(171, "An “img” element must have an “alt” attribute, except under certain conditions."),
    msg(180, "The “center” element is obsolete. Use CSS instead."),
    msg(215, "Unclosed element “font”."),
    msg(227, "End tag “td” seen, but there were open elements."),
    msg(305, "Table cell spans past the end of its row group established by a “tbody” element; clipped."),
    msg(362, "Stray end tag “tr”."),
    {"type": "info", "subType": "warning", "lastLine": 131,
     "message": "The “type” attribute is unnecessary for JavaScript resources."},
]
# after/home.html: only HTML5 conformance errors, no parse errors.
AFTER_HOME = [
    msg(1, "Obsolete doctype. Expected “<!DOCTYPE html>”."),
    msg(118, "The “acronym” element is obsolete. Use the “abbr” element instead."),
]

PAGE = "<HTML>\n<HEAD>\n<NOSCRIPT><B><FONT COLOR=RED>No JS</FONT></B></NOSCRIPT>\n</HEAD>\n<BODY><p>x</p></BODY></HTML>"


def test_only_parse_errors_count():
    errors, fatal = vs.parse_errors(HOME_SERVED + HOME_BLANKED)
    assert [line for line, _ in errors] == [143, 143, 145, 146, 215, 227, 362]
    assert fatal


def test_no_parse_errors_on_after():
    assert vs.parse_errors(AFTER_HOME) == ([], False)


def test_blank_head_noscript_keeps_lines_and_body():
    html = "<HTML><HEAD>\n<NOSCRIPT>\n<B>x</B>\n</NOSCRIPT>\n</HEAD><BODY><noscript>keep</noscript></BODY></HTML>"
    out = vs.blank_head_noscript(html)
    assert "<B>x</B>" not in out
    assert out.count("\n") == html.count("\n")
    assert "<noscript>keep</noscript>" in out  # a <noscript> in <body> is fine and stays


def test_rechecks_without_head_noscript_when_the_checker_gives_up():
    seen = []

    def run(html):
        seen.append(html)
        return HOME_SERVED if len(seen) == 1 else HOME_BLANKED

    errors, notes = vs.check_html(PAGE, run)
    assert len(seen) == 2 and "NOSCRIPT" not in seen[1].upper()
    assert [line for line, _ in errors] == [143, 143, 145, 146, 215, 227, 362]
    assert len(notes) == 1 and "<noscript> in <head>" in notes[0]


def test_one_run_when_the_checker_finishes():
    calls = []
    errors, notes = vs.check_html(PAGE, lambda h: calls.append(h) or HOME_BLANKED)
    assert len(calls) == 1 and notes == [] and len(errors) == 3


def test_says_so_when_the_checker_still_stops():
    news = [msg(202, "“td” start tag in table body."), msg(210, "Start tag “p” seen in “table”."),
            msg(210, "Cannot recover after last error. Any further errors will be ignored.", "fatal")]
    errors, notes = vs.check_html(PAGE, lambda h: HOME_SERVED if "NOSCRIPT" in h else news)
    assert (202, "“td” start tag in table body.") in errors
    assert any("later errors are not listed" in n for n in notes)


def test_result_is_one_page_level_violation():
    r = vs.build_result(PAGE, "before", "home", "u", lambda h: HOME_BLANKED, "vnu test")
    assert (r["check"], r["version"], r["page"], r["tool_version"]) == ("validator", "before", "home", "vnu test")
    [h] = r["hits"]
    assert (h["rule_id"], h["sc"], h["status"]) == ("adj.validator.parse-error", "4.1.1", "violation")
    assert (h["dom_index"], h["selector"], h["html"], h["in_wrapper"]) == (0, "html", "<HTML>", False)
    assert h["message"].startswith("3 parse error(s) in the page source (F70): Unclosed element font (line 215);")
    assert set(h) == set(merge.ADJ_HIT_KEYS)


def test_no_hit_without_parse_errors():
    assert vs.build_result(PAGE, "after", "home", "u", lambda h: AFTER_HOME, "t")["hits"] == []


def test_long_lists_are_cut():
    many = [msg(i, "Stray end tag “tr”.") for i in range(1, 21)]
    [h] = vs.build_result("<html>", "before", "home", "u", lambda h: many, "t")["hits"]
    assert h["message"].count("(line ") == vs.LISTED and "and 8 more" in h["message"]


def test_merge_accepts_the_file(tmp_path, monkeypatch):
    r = vs.build_result(PAGE, "before", "home", "u", lambda h: HOME_BLANKED, "t")
    vs.write_result(r, tmp_path)
    monkeypatch.setattr(merge, "SCANS", tmp_path)
    dropped = collections.Counter()
    [h] = merge.load_adj_hits("before", "home", dropped)
    assert (h["scanner"], h["sc"], h["page_level"]) == ("adj-rules", "4.1.1", True)
    assert not dropped


@pytest.mark.skipif(not (shutil.which("node") and vs.VNU.exists()), reason="needs Node.js and npm install (vnu-jar)")
def test_real_vnu_finds_a_stray_end_tag():
    html = "<!doctype html><html lang='en'><head><title>t</title></head><body><table><tr><td>a</td></tr></tr></table>"
    errors, _ = vs.check_html(html, vs.run_local)
    assert any("Stray end tag" in m for _, m in errors)
