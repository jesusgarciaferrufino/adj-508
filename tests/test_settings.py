"""settings.py is the single source of truth for the Bedrock model and region."""

import importlib
from pathlib import Path

import pytest

import settings

REPO = Path(__file__).resolve().parent.parent
# Built in pieces so this file doesn't contain the literal the last test searches for.
PREFIX = "us." + "anthropic."
DEFAULT_MODEL = PREFIX + "claude-sonnet-4-6"
CODE_DIRS = ["capture", "scanners", "agents", "rag", "scripts", "reports", "pulse"]
ENV_VARS = ["ADJ_MODEL_ID", "ADJ_AWS_REGION", "ADJ_MAX_BEDROCK_CALLS"]


@pytest.fixture
def fresh_settings(monkeypatch):
    """Reload settings under a clean environment, and restore it afterwards."""
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    yield monkeypatch
    monkeypatch.undo()
    importlib.reload(settings)


def test_defaults_when_env_unset(fresh_settings):
    importlib.reload(settings)
    assert settings.MODEL_ID == DEFAULT_MODEL
    assert settings.AWS_REGION == "us-east-1"
    assert settings.MAX_BEDROCK_CALLS == 20


def test_env_vars_override_defaults(fresh_settings):
    fresh_settings.setenv("ADJ_MODEL_ID", "foo")
    fresh_settings.setenv("ADJ_AWS_REGION", "us-west-2")
    fresh_settings.setenv("ADJ_MAX_BEDROCK_CALLS", "5")
    importlib.reload(settings)
    assert settings.MODEL_ID == "foo"
    assert settings.AWS_REGION == "us-west-2"
    assert settings.MAX_BEDROCK_CALLS == 5


def test_no_model_id_outside_settings():
    offenders = []
    for d in CODE_DIRS:
        folder = REPO / d
        if not folder.is_dir():
            continue
        for py in folder.rglob("*.py"):
            if PREFIX in py.read_text(encoding="utf-8", errors="ignore"):
                offenders.append(str(py.relative_to(REPO)))
    assert offenders == [], f"Model ID hard-coded outside settings.py: {offenders}. Use settings.MODEL_ID."
