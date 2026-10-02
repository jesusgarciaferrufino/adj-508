"""The finding format as code: one validated way for every track to read Track A's output.

    from scanners.findings import load_findings, load_all

    for f in load_findings("before", "home"):
        f.finding_id, f.wcag[0], f.evidence.selector, f.evidence.html_snippet
        [d.message for d in f.scanner_details]   # what the scanners said

    rows = [f.model_dump() for f in load_findings("before", "home")]  # plain dicts

Loading fails immediately, with the file, finding and field named, if a file does not
match the format in docs/finding.example.json. A broken file is caught when it is read,
not three steps later inside someone else's code.

Later tracks may add their own fields (for example Track B's mapping); extra fields are
kept, not rejected. Write those results to your own folder, never into data/findings/.

Check every committed file:  python -m scanners.findings
"""

import json
import re
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from scanners.wcag import WCAG20_A_AA  # noqa: E402

FINDINGS_DIR = REPO / "data" / "findings"
VERSIONS = ("before", "after")
PAGES = ("home", "news", "tickets", "survey")

Version = Literal["before", "after"]
PageName = Literal["home", "news", "tickets", "survey"]
Scanner = Literal["axe-core", "pa11y"]
ScannerStatus = Literal["violation", "needs_review", "notice"]

ID_RE = re.compile(r"^F-(before|after)-(home|news|tickets|survey)-el(\d{4})-(\d\.\d\.\d+)$")
DETAIL_TYPES = {"axe-core": {"violations", "incomplete"}, "pa11y": {"error", "warning", "notice"}}


class _Model(BaseModel):
    model_config = ConfigDict(extra="allow")


class Evidence(_Model):
    selector: str
    html_snippet: str
    screenshot: str | None  # repo-relative path; None when the element can't be photographed
    page_url: str
    captured_at: str | None
    dom_index: int = Field(ge=0)


class ScannerDetail(_Model):
    scanner: Scanner
    rule_id: str
    type: str  # axe: violations | incomplete; pa11y: error | warning | notice
    message: str

    @model_validator(mode="after")
    def _type_matches_scanner(self):
        if self.type not in DETAIL_TYPES[self.scanner]:
            raise ValueError(f"type {self.type!r} is not a {self.scanner} type {sorted(DETAIL_TYPES[self.scanner])}")
        return self


class AI(_Model):
    case_for: str
    case_against: str
    citations_verified: bool
    confidence: float | None
    prompt_version: str


class Review(_Model):
    final_status: str | None  # set only by a signed-in human reviewer (CONTEXT.md)
    reviewer: str | None
    decided_at: str | None
    comment: str | None


class Finding(_Model):
    finding_id: str
    page: PageName
    site_version: Version
    source: Scanner
    sources: list[Scanner] = Field(min_length=1)
    scanner_status: ScannerStatus
    rule_id: str
    wcag: list[str] = Field(min_length=1, max_length=1)
    section508: list[str]
    bucket: str | None  # set by Track C's router
    evidence: Evidence
    scanner_details: list[ScannerDetail] = Field(min_length=1)
    ai: AI
    review: Review

    @field_validator("wcag")
    @classmethod
    def _wcag20_only(cls, v):
        bad = [c for c in v if c not in WCAG20_A_AA]
        if bad:
            raise ValueError(f"not a WCAG 2.0 A/AA criterion: {bad}")
        return v

    @model_validator(mode="after")
    def _id_describes_finding(self):
        expected = f"F-{self.site_version}-{self.page}-el{self.evidence.dom_index:04d}-{self.wcag[0]}"
        if self.finding_id != expected:
            raise ValueError(f"finding_id {self.finding_id!r} should be {expected!r}")
        if self.source != self.sources[0]:
            raise ValueError("source must be the first entry of sources")
        return self


class FindingsFileError(ValueError):
    """A findings file is missing or does not match the format."""


def findings_path(version: str, page: str) -> Path:
    if version not in VERSIONS or page not in PAGES:
        raise ValueError(f"unknown page {version}/{page}; versions {VERSIONS}, pages {PAGES}")
    return FINDINGS_DIR / f"{version}_{page}.json"


def validate_findings(rows: list, where: str = "findings") -> list[Finding]:
    """Validate a list of finding dicts. Raises FindingsFileError naming the bad finding."""
    if not isinstance(rows, list):
        raise FindingsFileError(f"{where}: expected a JSON list of findings, got {type(rows).__name__}")
    out = []
    for i, row in enumerate(rows):
        try:
            out.append(Finding.model_validate(row))
        except ValidationError as e:
            fid = row.get("finding_id", f"#{i}") if isinstance(row, dict) else f"#{i}"
            raise FindingsFileError(f"{where}: finding {fid} does not match the format:\n{e}") from None
    ids = [f.finding_id for f in out]
    dupes = sorted({x for x in ids if ids.count(x) > 1})
    if dupes:
        raise FindingsFileError(f"{where}: duplicate finding IDs {dupes[:5]}")
    return out


def load_findings(version: str, page: str) -> list[Finding]:
    """Load and validate data/findings/{version}_{page}.json."""
    path = findings_path(version, page)
    rel = path.relative_to(REPO)
    if not path.exists():
        raise FindingsFileError(f"{rel} not found. Pull main, or run Track A: python scanners/merge.py")
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise FindingsFileError(f"{rel} is not valid JSON: {e}") from None
    findings = validate_findings(rows, str(rel))
    for f in findings:
        if (f.site_version, f.page) != (version, page):
            raise FindingsFileError(f"{rel}: {f.finding_id} belongs to {f.site_version}/{f.page}")
    return findings


def load_all() -> dict[tuple[str, str], list[Finding]]:
    """Every page: {("before", "home"): [Finding, ...], ...}."""
    return {(v, p): load_findings(v, p) for v in VERSIONS for p in PAGES}


def main():
    total = 0
    for (v, p), fs in load_all().items():
        total += len(fs)
        print(f"{v:6} {p:8} {len(fs):4} findings ok")
    print(f"{total} findings valid")


if __name__ == "__main__":
    main()
