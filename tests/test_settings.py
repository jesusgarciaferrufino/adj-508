"""settings.py is the single source of truth for the Bedrock model and region."""

import importlib
import re
from pathlib import Path

import pytest

import settings

REPO = Path(__file__).resolve().parent.parent
# Built in pieces so this file doesn't contain the literal the last test searches for.
PREFIX = "us." + "anthropic."
# Any Claude model ID, with or without the inference-profile prefix (e.g. a bare "anthropic.claude-...").
MODEL_ID_RE = re.compile(r"anthropic" + r"\.claude")
DEFAULT_MODEL = PREFIX + "claude-sonnet-4-6"
CODE_DIRS = ["capture", "scanners", "agents", "rag", "scripts", "reports", "pulse"]
ENV_VARS = ["ADJ_MODEL_ID", "ADJ_AWS_REGION", "ADJ_MAX_BEDROCK_CALLS"]
NO_ENV_FILE = "/nonexistent/.env"


@pytest.fixture
def fresh_settings(monkeypatch):
    """Reload settings under a clean environment, and restore it afterwards."""
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ADJ_ENV_FILE", NO_ENV_FILE)  # ignore any real .env on this laptop
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


def test_dotenv_is_read_and_shell_wins(fresh_settings, tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        '# comment\nADJ_MODEL_ID="from-dotenv"\nexport ADJ_AWS_REGION=us-west-2\n\nADJ_MAX_BEDROCK_CALLS=7\n'
    )
    fresh_settings.setenv("ADJ_ENV_FILE", str(env))
    fresh_settings.setenv("ADJ_MAX_BEDROCK_CALLS", "3")  # already set in the shell: must win
    importlib.reload(settings)
    assert settings.MODEL_ID == "from-dotenv"
    assert settings.AWS_REGION == "us-west-2"
    assert settings.MAX_BEDROCK_CALLS == 3


def test_bad_call_cap_names_the_variable(fresh_settings):
    fresh_settings.setenv("ADJ_MAX_BEDROCK_CALLS", "twenty")
    with pytest.raises(ValueError, match="ADJ_MAX_BEDROCK_CALLS must be a whole number"):
        importlib.reload(settings)


def test_call_cap_zero_allowed_negative_refused(fresh_settings):
    fresh_settings.setenv("ADJ_MAX_BEDROCK_CALLS", "0")  # dry run: no calls
    importlib.reload(settings)
    assert settings.MAX_BEDROCK_CALLS == 0
    fresh_settings.setenv("ADJ_MAX_BEDROCK_CALLS", "-5")
    with pytest.raises(ValueError, match="ADJ_MAX_BEDROCK_CALLS must be 0 or more"):
        importlib.reload(settings)


def test_model_id_pattern_catches_bare_ids():
    assert MODEL_ID_RE.search("anthropic" + ".claude-sonnet-4-6")
    assert MODEL_ID_RE.search(PREFIX + "claude-sonnet-4-6")
    assert not MODEL_ID_RE.search("from settings import MODEL_ID")


def test_no_model_id_outside_settings():
    offenders = []
    for d in CODE_DIRS:
        folder = REPO / d
        if not folder.is_dir():
            continue
        for py in folder.rglob("*.py"):
            if MODEL_ID_RE.search(py.read_text(encoding="utf-8", errors="ignore")):
                offenders.append(str(py.relative_to(REPO)))
    assert offenders == [], f"Model ID hard-coded outside settings.py: {offenders}. Use settings.MODEL_ID."
