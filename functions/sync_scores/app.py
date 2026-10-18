import csv
import io
import os
import urllib.request
import zipfile
from datetime import datetime, timezone

import boto3

BUCKET = os.environ["BUCKET_NAME"]
TABLE = os.environ["TABLE_NAME"]

s3 = boto3.client("s3")
table = boto3.resource("dynamodb").Table(TABLE)

EPOCH_URL = "https://epoch.ai/data/benchmark_data.zip"

WANTED = {
    "epoch_capabilities_index/eci_scores.csv": ("eci", "Model", "eci"),
    "gpqa_diamond.csv": ("gpqa_diamond", "Model version", "mean_score"),
}


def lambda_handler(event, context):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    with urllib.request.urlopen(EPOCH_URL, timeout=60) as response:
        raw = response.read()

    s3.put_object(
        Bucket=BUCKET,
        Key=f"raw/epoch/dt={today}/benchmark_data.zip",
        Body=raw,
    )

    written = 0
    skipped = 0

    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        with table.batch_writer() as batch:
            for filename, (benchmark, name_col, score_col) in WANTED.items():
                text = archive.read(filename).decode("utf-8")
                for row in csv.DictReader(io.StringIO(text)):
                    name = (row.get(name_col) or "").strip()
                    score = (row.get(score_col) or "").strip()
                    if not name or not score:
                        skipped += 1
                        continue
                    batch.put_item(
                        Item={
                            "PK": f"RAWSCORE#epoch#{name}",
                            "SK": f"SCORE#{benchmark}#{today}",
                            "raw_name": name,
                            "benchmark": benchmark,
                            "score": score,
                            "release_date": (row.get("date") or row.get("Release date") or ""),
                            "organization": (row.get("Organization") or ""),
                            "source": "epoch",
                        }
                    )
                    written += 1

    return {"records_written": written, "rows_skipped": skipped}