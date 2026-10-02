"""Checks on Track A's committed output in data/findings/, the files Tracks B, C and D read."""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FILES = sorted((ROOT / "data" / "findings").glob("*.json"))
ID_RE = re.compile(r"^F-(before|after)-(home|news|tickets|survey)-el(\d{4})-(\d\.\d\.\d+)$")
STATUSES = {"violation", "needs_review", "notice"}
DETAIL_TYPES = {"axe-core": {"violations", "incomplete"}, "pa11y": {"error", "warning", "notice"}}


def _load(path):
    return json.loads(path.read_text())


@pytest.mark.parametrize("path", FILES, ids=[p.stem for p in FILES])
def test_findings_file(path):
    findings = _load(path)
    version, page = path.stem.split("_")
    ids = [f["finding_id"] for f in findings]
    assert len(ids) == len(set(ids)), "duplicate finding IDs"
    for f in findings:
        m = ID_RE.match(f["finding_id"])
        assert m, f"bad ID format: {f['finding_id']}"
        # The ID must describe the finding it is on, so it can't drift from the data.
        assert m.groups() == (version, page, f"{f['evidence']['dom_index']:04d}", f["wcag"][0]), f["finding_id"]
        assert f["site_version"] == version and f["page"] == page
        assert f["scanner_status"] in STATUSES
        assert f["source"] == f["sources"][0]
        for d in f["scanner_details"]:
            assert d["type"] in DETAIL_TYPES[d["scanner"]], d
        shot = f["evidence"]["screenshot"]
        assert shot is None or (ROOT / shot).is_file(), f"missing screenshot {shot}"
        assert f["bucket"] is None and f["review"]["final_status"] is None


def test_example_is_a_real_finding():
    example = _load(ROOT / "docs" / "finding.example.json")
    by_id = {f["finding_id"]: f for p in FILES for f in _load(p)}
    assert example["finding_id"] in by_id, "docs/finding.example.json must use a real current ID"
