import boto3

MODEL_ID = "us.anthropic.claude-opus-4-6-v1"

client = boto3.client("bedrock-runtime", region_name="us-east-1")

response = client.converse(
    modelId=MODEL_ID,
    messages=[
        {
            "role": "user",
            "content": [
                {"text": "In one sentence, what is WCAG 2.0 success criterion 1.1.1?"}
            ],
        }
    ],
    inferenceConfig={"maxTokens": 200},
)

print(response["output"]["message"]["content"][0]["text"])
