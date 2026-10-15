"""Project settings: the one place model IDs, region and call limits are defined.

Every value can be overridden with an environment variable (see .env.example).
On import, a .env file at the repo root is read if it exists; variables already set
in the shell win over .env, and .env wins over the defaults below. No boto3 import
here, so anything can import settings cheaply.

Bedrock serves Claude models through cross-region inference profiles, so the model
ID needs the "us." prefix (e.g. us.anthropic.claude-sonnet-4-6). The bare
"anthropic.…" ID is rejected for on-demand calls in us-east-1.

To switch models (e.g. to Claude Fable 5.1 once AWS grants access), set
ADJ_MODEL_ID. Never hard-code a model ID anywhere else; tests/test_settings.py
fails if one appears.
"""

import os
from pathlib import Path

# ADJ_ENV_FILE lets tests point at a different file (or none).
ENV_FILE = Path(os.environ.get("ADJ_ENV_FILE", Path(__file__).resolve().parent / ".env"))


def load_dotenv(path: Path = ENV_FILE) -> None:
    """Read KEY=VALUE lines from path into os.environ, without overriding the shell.

    Blank lines and lines starting with # are skipped; surrounding quotes are removed.
    A missing file is fine. No dependency on python-dotenv.
    """
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{name} must be a whole number, got {raw!r} (check your shell or .env)") from None


load_dotenv()

MODEL_ID = os.environ.get("ADJ_MODEL_ID", "us.anthropic.claude-sonnet-4-6")
AWS_REGION = os.environ.get("ADJ_AWS_REGION", "us-east-1")
MAX_BEDROCK_CALLS = _int_env("ADJ_MAX_BEDROCK_CALLS", 20)
