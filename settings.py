"""Project settings: the one place model IDs, region and call limits are defined.

Every value can be overridden with an environment variable (see .env.example).
Values only: no boto3 import here, so anything can import settings cheaply.

Bedrock serves Claude models through cross-region inference profiles, so the model
ID needs the "us." prefix (e.g. us.anthropic.claude-sonnet-4-6). The bare
"anthropic.…" ID is rejected for on-demand calls in us-east-1.

To switch models (e.g. to Claude Fable 5.1 once AWS grants access), set
ADJ_MODEL_ID. Never hard-code a model ID anywhere else; tests/test_settings.py
fails if one appears.
"""

import os

MODEL_ID = os.environ.get("ADJ_MODEL_ID", "us.anthropic.claude-sonnet-4-6")
AWS_REGION = os.environ.get("ADJ_AWS_REGION", "us-east-1")
MAX_BEDROCK_CALLS = int(os.environ.get("ADJ_MAX_BEDROCK_CALLS", "20"))
