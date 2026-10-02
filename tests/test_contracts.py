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

# Every WCAG 2.0 Level A and AA success criterion. Nothing else may be cited.
WCAG20_A_AA = {
    "1.1.1",
    "1.2.1", "1.2.2", "1.2.3", "1.2.4", "1.2.5",
    "1.3.1", "1.3.2", "1.3.3",
    "1.4.1", "1.4.2", "1.4.3", "1.4.4", "1.4.5",
    "2.1.1", "2.1.2",
    "2.2.1", "2.2.2",
    "2.3.1",
    "2.4.1", "2.4.2", "2.4.3", "2.4.4", "2.4.5", "2.4.6", "2.4.7",
    "3.1.1", "3.1.2",
    "3.2.1", "3.2.2", "3.2.3", "3.2.4",
    "3.3.1", "3.3.2", "3.3.3", "3.3.4",
    "4.1.1", "4.1.2",
}

REQUIRED_TOP_LEVEL = {
    "finding_id", "page", "site_version", "rule_id", "wcag",
    "section508", "bucket", "evidence", "ai", "review",
}
REQUIRED_EVIDENCE = {"selector", "html_snippet", "screenshot", "page_url", "captured_at"}
REQUIRED_REVIEW = {"final_status", "reviewer", "decided_at", "comment"}


def test_wcag_list_is_complete():
    assert len(WCAG20_A_AA) == 38


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
