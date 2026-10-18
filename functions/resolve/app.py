import os
import re
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Attr

TABLE = os.environ["TABLE_NAME"]
table = boto3.resource("dynamodb").Table(TABLE)

EFFORT_SUFFIXES = (
    "_max", "_xhigh", "_high", "_medium", "_low", "_minimal", "_none",
    "_instant", "_pro", "_batch",
)
DATE_PATTERN = re.compile(r"[-_](20\d{2}[-_]?\d{2}[-_]?\d{2})$")
CONTEXT_PATTERN = re.compile(r"_\d+k$", re.IGNORECASE)
PAREN_PATTERN = re.compile(r"\s*\([^)]*\)")
VERSION_PATTERN = re.compile(r"[-_]v\d+$")


def normalize(raw):
    """Reduce a model name to a comparable key."""
    s = raw.strip().lower()

    # "anthropic: claude opus 5.5" -> "claude opus 5.5"
    if ":" in s:
        s = s.split(":", 1)[1]

    # "anthropic/claude-opus-5.5" -> "claude-opus-5.5"
    if "/" in s:
        s = s.split("/", 1)[1]

    s = s.lstrip("~")

    # "claude 3.5 sonnet (october 2024)" -> "claude 3.5 sonnet"
    s = PAREN_PATTERN.sub("", s)

    # "command r+" -> "command rplus"
    s = s.replace("+", "plus")

    # strip trailing qualifiers, repeatedly
    changed = True
    while changed:
        changed = False
        s = s.strip()
        for suffix in EFFORT_SUFFIXES:
            if s.endswith(suffix):
                s = s[: -len(suffix)]
                changed = True
        for pattern in (CONTEXT_PATTERN, DATE_PATTERN, VERSION_PATTERN):
            if pattern.search(s):
                s = pattern.sub("", s)
                changed = True

    # drop everything that isn't a letter or digit
    return re.sub(r"[^a-z0-9]", "", s)


def scan_all(filter_expression):
    """Page through a filtered scan."""
    items = []
    kwargs = {"FilterExpression": filter_expression}
    while True:
        page = table.scan(**kwargs)
        items.extend(page.get("Items", []))
        if "LastEvaluatedKey" not in page:
            return items
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def lambda_handler(event, context):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # build the lookup: normalized key -> openrouter id
    lookup = {}
    for item in scan_all(Attr("SK").eq("META")):
        model_id = item["PK"].split("#", 1)[1]
        for candidate in (model_id, item.get("name", "")):
            if candidate:
                key = normalize(candidate)
                if key:
                    lookup.setdefault(key, model_id)

    best_scores = {}
    aliases = {}
    unmatched = []

    for item in scan_all(Attr("PK").begins_with("RAWSCORE#epoch#")):
        raw_name = item["raw_name"]
        key = normalize(raw_name)
        model_id = lookup.get(key)

        if not model_id:
            unmatched.append(raw_name)
            continue

        benchmark = item["benchmark"]
        try:
            score = float(item["score"])
        except (TypeError, ValueError):
            continue

        # same model evaluated at several reasoning efforts -> keep the best
        slot = (model_id, benchmark)
        if slot not in best_scores or score > best_scores[slot][0]:
            best_scores[slot] = (score, raw_name)

        aliases[raw_name] = model_id

    with table.batch_writer() as batch:
        for (model_id, benchmark), (score, raw_name) in best_scores.items():
            batch.put_item(
                Item={
                    "PK": f"MODEL#{model_id}",
                    "SK": f"SCORE#{benchmark}#{today}",
                    "score": str(score),
                    "benchmark": benchmark,
                    "resolved_from": raw_name,
                    "method": "rules",
                    "confidence": "1.0",
                    "source": "epoch",
                }
            )
        for raw_name, model_id in aliases.items():
            batch.put_item(
                Item={
                    "PK": f"ALIAS#{raw_name}",
                    "SK": "RESOLVED",
                    "model_id": model_id,
                    "method": "rules",
                    "confidence": "1.0",
                }
            )

    return {
        "lookup_keys": len(lookup),
        "matched_pairs": len(best_scores),
        "aliases_resolved": len(aliases),
        "unmatched": len(unmatched),
        "unmatched_sample": sorted(set(unmatched))[:30],
    }