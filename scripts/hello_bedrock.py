"""Smoke test: can this laptop call Claude on Bedrock with the configured model?

Run from the repo root:
  python -m scripts.hello_bedrock

Uses settings.MODEL_ID and settings.AWS_REGION (override with ADJ_MODEL_ID /
ADJ_AWS_REGION). Prints the model ID it used and a one-sentence answer, or a
one-line hint instead of a traceback when the call fails.
"""

import sys

import boto3
from botocore.exceptions import ClientError, NoCredentialsError

import settings

QUESTION = "In one sentence, what is WCAG 2.0 success criterion 1.1.1?"


def main() -> int:
    print(f"model:  {settings.MODEL_ID}")
    print(f"region: {settings.AWS_REGION}")
    client = boto3.client("bedrock-runtime", region_name=settings.AWS_REGION)
    try:
        response = client.converse(
            modelId=settings.MODEL_ID,
            messages=[{"role": "user", "content": [{"text": QUESTION}]}],
            inferenceConfig={"maxTokens": 200},
        )
    except NoCredentialsError:
        print("FAILED: no AWS credentials found. Run `aws configure` (region us-east-1).")
        return 1
    except ClientError as e:
        err = e.response.get("Error", {})
        print(f"FAILED: {err.get('Code', 'ClientError')}: {err.get('Message', str(e))}")
        print("Hint: check that model access is granted in the Bedrock console, that the region is right, "
              "and that the model ID has the 'us.' inference profile prefix.")
        return 1

    print(response["output"]["message"]["content"][0]["text"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
