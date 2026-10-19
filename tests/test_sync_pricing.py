"""Integration tests for the OpenRouter ingestion Lambda.

AWS is mocked with moto, so these run offline and create nothing real.
The HTTP call is stubbed with a small fixture rather than hitting the
live API, so the tests are deterministic.
"""

import importlib.util
import json
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

ROOT = Path(__file__).resolve().parent.parent

BUCKET = "test-bucket"
TABLE = "test-table"

FIXTURE = {
    "data": [
        {
            "id": "anthropic/claude-opus-5.5",
            "name": "Anthropic: Claude Opus 5.5",
            "canonical_slug": "anthropic/claude-opus-5.5-20260921",
            "context_length": 1000000,
            "pricing": {"prompt": "0.000004", "completion": "0.00002"},
        },
        {
            "id": "openai/gpt-6-astra",
            "name": "OpenAI: GPT-6 Astra",
            "canonical_slug": "openai/gpt-6-astra-20260903",
            "context_length": 1050000,
            "pricing": {"prompt": "0.00001", "completion": "0.00005"},
        },
        {
            # a model with no pricing block at all
            "id": "broken/no-pricing",
            "name": "Broken: No Pricing",
            "context_length": 8192,
        },
    ]
}


class FakeResponse:
    """Stands in for the object urlopen returns, including context manager."""

    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@pytest.fixture
def aws(monkeypatch):
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-2")
        s3.create_bucket(
            Bucket=BUCKET,
            CreateBucketConfiguration={"LocationConstraint": "us-east-2"},
        )
        dynamodb = boto3.client("dynamodb", region_name="us-east-2")
        dynamodb.create_table(
            TableName=TABLE,
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
        )
        yield


def load_handler(monkeypatch, payload=FIXTURE):
    """Import the Lambda inside the mock, then stub its HTTP call."""
    spec = importlib.util.spec_from_file_location(
        "sync_pricing_app", ROOT / "functions" / "sync_pricing" / "app.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(
        module.urllib.request,
        "urlopen",
        lambda *a, **k: FakeResponse(payload),
    )
    return module


def test_writes_one_price_and_one_meta_per_model(aws, monkeypatch):
    module = load_handler(monkeypatch)
    result = module.lambda_handler({}, None)

    assert result["models_fetched"] == 3
    assert result["records_written"] == 6  # 3 models x 2 item types


def test_meta_record_carries_display_name(aws, monkeypatch):
    module = load_handler(monkeypatch)
    module.lambda_handler({}, None)

    table = boto3.resource("dynamodb", region_name="us-east-2").Table(TABLE)
    item = table.get_item(
        Key={"PK": "MODEL#anthropic/claude-opus-5.5", "SK": "META"}
    )["Item"]

    assert item["name"] == "Anthropic: Claude Opus 5.5"
    assert item["source"] == "openrouter"


def test_price_record_stores_costs_as_strings(aws, monkeypatch):
    """Floats are rejected by boto3 and round badly on money."""
    module = load_handler(monkeypatch)
    module.lambda_handler({}, None)

    table = boto3.resource("dynamodb", region_name="us-east-2").Table(TABLE)
    items = table.query(
        KeyConditionExpression=boto3.dynamodb.conditions.Key("PK").eq(
            "MODEL#anthropic/claude-opus-5.5"
        )
    )["Items"]

    price = next(i for i in items if i["SK"].startswith("PRICE#"))
    assert price["prompt_cost"] == "0.000004"
    assert isinstance(price["prompt_cost"], str)


def test_missing_pricing_block_does_not_crash_the_run(aws, monkeypatch):
    """One malformed record must not cost us the other 470."""
    module = load_handler(monkeypatch)
    result = module.lambda_handler({}, None)

    assert result["models_fetched"] == 3

    table = boto3.resource("dynamodb", region_name="us-east-2").Table(TABLE)
    items = table.query(
        KeyConditionExpression=boto3.dynamodb.conditions.Key("PK").eq(
            "MODEL#broken/no-pricing"
        )
    )["Items"]

    price = next(i for i in items if i["SK"].startswith("PRICE#"))
    assert price["prompt_cost"] == "0"


def test_raw_response_is_archived_to_s3_before_parsing(aws, monkeypatch):
    """The landing zone is what makes a parsing bug recoverable."""
    module = load_handler(monkeypatch)
    module.lambda_handler({}, None)

    s3 = boto3.client("s3", region_name="us-east-2")
    keys = [o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET)["Contents"]]

    assert len(keys) == 1
    assert keys[0].startswith("raw/openrouter/dt=")
    assert keys[0].endswith("models.json")

    body = s3.get_object(Bucket=BUCKET, Key=keys[0])["Body"].read()
    assert json.loads(body) == FIXTURE


def test_sort_key_is_date_partitioned(aws, monkeypatch):
    """PRICE#<date> means a rerun appends history rather than overwriting."""
    module = load_handler(monkeypatch)
    module.lambda_handler({}, None)

    table = boto3.resource("dynamodb", region_name="us-east-2").Table(TABLE)
    items = table.query(
        KeyConditionExpression=boto3.dynamodb.conditions.Key("PK").eq(
            "MODEL#openai/gpt-6-astra"
        )
    )["Items"]

    sort_keys = sorted(i["SK"] for i in items)
    assert sort_keys[0] == "META"
    assert sort_keys[1].startswith("PRICE#20")
