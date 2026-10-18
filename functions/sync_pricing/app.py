import json
import os
import urllib.request
from datetime import datetime, timezone

import boto3

BUCKET = os.environ["BUCKET_NAME"]
TABLE = os.environ["TABLE_NAME"]

s3 = boto3.client("s3")
table = boto3.resource("dynamodb").Table(TABLE)

OPENROUTER_URL = "https://openrouter.ai/api/v1/models"

def lambda_handler(event, context):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    with urllib.request.urlopen(OPENROUTER_URL, timeout=30) as response:
        raw = response.read()

    s3.put_object(
        Bucket=BUCKET,
        Key=f"raw/openrouter/dt={today}/models.json",
        Body=raw,
    )

    models = json.loads(raw)["data"]

    written = 0
    with table.batch_writer() as batch:
        for model in models:
            pricing = model.get("pricing", {})
            batch.put_item(
                Item={
                    "PK": f"MODEL#{model['id']}",
                    "SK": f"PRICE#{today}",
                    "prompt_cost": str(pricing.get("prompt", "0")),
                    "completion_cost": str(pricing.get("completion", "0")),
                    "context_length": str(model.get("context_length", "0")),
                    "source": "openrouter",
                }
            )
            written += 1

    return {"models_fetched": len(models), "records_written": written}