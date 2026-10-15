"""Checks on Track A's committed output in data/findings/, the files Tracks B, C and D read.

The format rules themselves live in scanners/findings.py; these tests run every
committed file through that loader and check the things a file alone can't show.
"""

import json

import pytest

from scanners.findings import (
    FINDINGS_DIR,
    PAGES,
    REPO,
    VERSIONS,
    FindingsFileError,
    load_findings,
    validate_findings,
)

ALL_PAGES = [(v, p) for v in VERSIONS for p in PAGES]


@pytest.mark.parametrize(("version", "page"), ALL_PAGES, ids=[f"{v}_{p}" for v, p in ALL_PAGES])
def test_committed_file_loads(version, page):
    findings = load_findings(version, page)
    assert findings, "every page has findings"
    for f in findings:
        shot = f.evidence.screenshot
        assert shot is None or (REPO / shot).is_file(), f"{f.finding_id}: missing screenshot {shot}"
        assert f.bucket is None and f.review.final_status is None, "Track A output must be undecided"


def test_only_page_files_in_findings_dir():
    """Other tracks write their own results elsewhere (docs/TRACKS.md), never next to Track A's."""
    expected = {f"{v}_{p}.json" for v, p in ALL_PAGES}
    extra = sorted(p.name for p in FINDINGS_DIR.glob("*.json") if p.name not in expected)
    assert not extra, f"unexpected files in data/findings/: {extra}"


def test_example_is_a_real_finding():
    example = json.loads((REPO / "docs" / "finding.example.json").read_text())
    validate_findings([example], "docs/finding.example.json")
    real = {f.finding_id: f for f in load_findings(example["site_version"], example["page"])}
    assert example["finding_id"] in real


def test_loader_rejects_broken_findings():
    good = json.loads((REPO / "docs" / "finding.example.json").read_text())
    for breakage in (
        lambda f: f.pop("evidence"),
        lambda f: f.update(wcag=["2.5.3"]),  # WCAG 2.1, not allowed
        lambda f: f.update(finding_id="F-before-home-0032"),  # old positional ID
        lambda f: f["scanner_details"][0].update(type="violation"),  # axe says "violations"
        lambda f: f.update(scanner_status="fail"),
    ):
        bad = json.loads(json.dumps(good))
        breakage(bad)
        with pytest.raises(FindingsFileError):
            validate_findings([bad])


def test_loader_keeps_extra_fields():
    f = json.loads((REPO / "docs" / "finding.example.json").read_text())
    f["mapping"] = {"method": "tag"}
    assert validate_findings([f])[0].model_dump()["mapping"] == {"method": "tag"}


def test_context_text_present_and_useful():
    """evidence.context_text (Oct 15): always a string, empty for page-level findings,
    and on the Week 3 thread finding it holds the sentence around the link."""
    findings = {f.finding_id: f for f in load_findings("before", "home")}
    for f in findings.values():
        assert isinstance(f.evidence.context_text, str)
        assert len(f.evidence.context_text) <= 302, f"{f.finding_id}: context over the cap"
        if f.evidence.selector == "html":
            assert f.evidence.context_text == "", f"{f.finding_id}: page-level finding has context"
    click_here = findings["F-before-home-el0210-2.4.4"].evidence.context_text
    assert "Killer bees" in click_here and "Click here" in click_here
