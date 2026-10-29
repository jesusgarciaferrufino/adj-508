import sys
from pathlib import Path

# Lets tests import capture/, scanners/ and agents/ no matter where pytest is started.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_real_bedrock(monkeypatch):
    """Tests must never call Bedrock (cost, and CI has no AWS keys). Any code path that
    reaches the real client fails at once; pass a fake client instead."""
    try:
        from agents import evidence
    except ModuleNotFoundError:  # Track C not merged yet
        return

    def refuse():
        raise RuntimeError("tests must not call Bedrock; pass a fake client to build_cases()")

    monkeypatch.setattr(evidence, "_bedrock_client", refuse)


@pytest.fixture(autouse=True)
def _fresh_evidence_state(monkeypatch):
    """Track C keeps per-process counters and a circuit breaker (agents/evidence.py). Reset them for
    every test, so a test that trips the breaker or spends calls can't change what the next one sees."""
    try:
        from agents import evidence
    except ModuleNotFoundError:
        return
    monkeypatch.setattr(evidence, "_calls_made", 0)
    monkeypatch.setattr(evidence, "_calls_failed", 0)
    monkeypatch.setattr(evidence, "_findings_done", 0)
    monkeypatch.setattr(evidence, "_gave_up", None)
    monkeypatch.setattr(evidence, "_billed", {"input_tokens": 0, "output_tokens": 0})
