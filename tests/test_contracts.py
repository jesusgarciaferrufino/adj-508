"""Guards for the team contracts in CONTEXT.md and docs/TRACKS.md.

These run in CI on every pull request so a change that breaks the shared
finding format or a hard rule fails before it reaches main.
"""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FINDING_EXAMPLE = ROOT / "docs" / "finding.example.json"

# The one allowlist lives in scanners/wcag.py; these tests check it rather than keep a copy.
from scanners.wcag import WCAG20_A, WCAG20_A_AA, WCAG20_AA  # noqa: E402

REQUIRED_TOP_LEVEL = {
    "finding_id", "page", "site_version", "rule_id", "wcag",
    "section508", "bucket", "evidence", "ai", "review",
}
REQUIRED_EVIDENCE = {"selector", "html_snippet", "screenshot", "page_url", "captured_at"}
REQUIRED_REVIEW = {"final_status", "reviewer", "decided_at", "comment"}


def test_wcag_list_is_complete():
    # WCAG 2.0 has 25 Level A and 13 Level AA success criteria.
    assert (len(WCAG20_A), len(WCAG20_AA), len(WCAG20_A_AA)) == (25, 13, 38)
    assert all(re.fullmatch(r"[1-4]\.\d\.\d", sc) for sc in WCAG20_A_AA)


@pytest.fixture
def finding():
    if not FINDING_EXAMPLE.exists():
        pytest.skip("docs/finding.example.json not in this branch yet")
    return json.loads(FINDING_EXAMPLE.read_text())


def test_finding_has_required_fields(finding):
    assert finding.keys() >= REQUIRED_TOP_LEVEL
    assert finding["evidence"].keys() >= REQUIRED_EVIDENCE
    assert finding["review"].keys() >= REQUIRED_REVIEW
    assert finding["finding_id"], "every finding needs an ID"


def test_finding_cites_only_wcag20_a_aa(finding):
    bad = set(finding["wcag"]) - WCAG20_A_AA
    assert not bad, f"not a WCAG 2.0 A/AA criterion: {sorted(bad)}"


def test_example_finding_is_not_pre_decided(finding):
    assert finding["review"]["final_status"] is None


# Matches a write to final_status ("final_status = x", "['final_status'] = x",
# or "'final_status': x" in a dict literal). Allows None, "==" comparisons and
# type annotations such as "final_status: str | None = None".
_FINAL_STATUS_WRITE = re.compile(
    r"""final_status['"]?\]?\s*(?<![=!<>])=(?!=)\s*(?!None\b)\S"""
    r"""|['"]final_status['"]\s*:\s*(?!None\b)\S"""
)


def test_ai_code_never_sets_final_status():
    """Hard rule: only a signed-in human reviewer sets the final status."""
    offenders = []
    for folder in ("agents", "scanners", "capture", "prompts"):
        for path in (ROOT / folder).rglob("*"):
            if path.suffix not in {".py", ".md", ".txt", ".yaml", ".yml", ".json"}:
                continue
            for n, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
                if _FINAL_STATUS_WRITE.search(line):
                    offenders.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()}")
    assert not offenders, "AI pipeline code sets final_status:\n" + "\n".join(offenders)
