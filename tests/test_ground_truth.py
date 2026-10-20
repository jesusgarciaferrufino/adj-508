"""Checks for data/ground-truth/ground_truth.csv (Week 3, Track GT)."""

import csv
from functools import cache

import pytest

from scanners.findings import REPO, load_findings
from scanners.wcag import WCAG20_A_AA

GT_PATH = REPO / "data" / "ground-truth" / "ground_truth.csv"
COLUMNS = [
    "page",
    "wcag",
    "level",
    "description",
    "w3c_ref",
    "where",
    "finding_id",
    "verified_by",
    "notes",
]
PAGES = {"home", "news", "tickets", "survey"}


def _rows():
    # utf-8-sig: Excel may add a BOM, which is fine.
    with GT_PATH.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        return reader.fieldnames, list(reader)


@cache
def _finding_ids(page):
    return {f.finding_id for f in load_findings("before", page)}


def test_file_exists():
    assert GT_PATH.exists(), f"{GT_PATH.relative_to(REPO)} is missing"


def test_columns_exact():
    fieldnames, _ = _rows()
    assert fieldnames == COLUMNS


def test_page_and_wcag_valid():
    _, rows = _rows()
    for i, r in enumerate(rows, start=2):
        assert r["page"] in PAGES, f"line {i}: bad page {r['page']!r}"
        assert r["wcag"] in WCAG20_A_AA, f"line {i}: {r['wcag']!r} is not WCAG 2.0 A/AA"
        assert r["level"] in {"A", "AA"}, f"line {i}: bad level {r['level']!r}"


def test_finding_id_is_missed_or_exists():
    _, rows = _rows()
    for i, r in enumerate(rows, start=2):
        fid = r["finding_id"]
        if fid == "missed":
            continue
        page = r["page"]
        assert fid in _finding_ids(page), f"line {i}: {fid} not in before_{page}"


def test_all_pages_have_rows():
    _, rows = _rows()
    assert {r["page"] for r in rows} == PAGES


@pytest.mark.parametrize("col", ["description", "w3c_ref", "where", "finding_id"])
def test_required_cells_not_blank(col):
    _, rows = _rows()
    blank = [i for i, r in enumerate(rows, start=2) if not r[col].strip()]
    assert not blank, f"blank {col} on lines {blank}"
