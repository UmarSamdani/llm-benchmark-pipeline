import os
import re
from datetime import UTC, datetime

import boto3
from boto3.dynamodb.conditions import Attr

TABLE = os.environ["TABLE_NAME"]
table = boto3.resource("dynamodb").Table(TABLE)
bedrock = boto3.client("bedrock-runtime")

MODEL_ID = "us.anthropic.claude-haiku-5-5"
MAX_LLM_CALLS = int(os.environ.get("MAX_LLM_CALLS", "40"))

EFFORT_SUFFIXES = (
    "_max", "_xhigh", "_high", "_medium", "_low", "_minimal", "_none",
    "_instant", "_pro", "_batch",
)
DATE_PATTERN = re.compile(r"[-_](20\d{2}[-_]?\d{2}[-_]?\d{2})$")
CONTEXT_PATTERN = re.compile(r"_\d+k$", re.IGNORECASE)
PAREN_PATTERN = re.compile(r"\s*\([^)]*\)")

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
        for pattern in (CONTEXT_PATTERN, DATE_PATTERN):
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


def ask_llm(raw_name, candidates):
    """Ask Haiku to pick a match. Returns (model_id, confidence) or (None, 0.0)."""
    listing = "\n".join(f"- {c}" for c in candidates)
    prompt = (
        "You are matching AI model names across two data sources.\n\n"
        f'Source name: "{raw_name}"\n\n'
        "Candidate identifiers:\n"
        f"{listing}\n\n"
        "If one candidate is the SAME underlying model, reply with exactly:\n"
        "MATCH|<candidate>|<confidence 0.0-1.0>\n"
        "If none is the same model, reply with exactly:\n"
        "NONE\n\n"
        "A different size, version, or distillation is NOT the same model. "
        "Reply with nothing else."
    )

    try:
        response = bedrock.converse(
            modelId=MODEL_ID,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 100, "temperature": 0},
        )
        reply = response["output"]["message"]["content"][0]["text"].strip()
    except Exception as exc:  # noqa: BLE001 - any Bedrock failure must not kill the run
        print(f"bedrock error for {raw_name!r}: {exc}")
        return None, 0.0
    if not reply.startswith("MATCH|"):
        return None, 0.0

    parts = reply.split("|")
    if len(parts) != 3:
        return None, 0.0

    candidate = parts[1].strip()
    if candidate not in candidates:
        return None, 0.0

    try:
        confidence = float(parts[2])
    except ValueError:
        return None, 0.0

    return (candidate, confidence) if confidence >= 0.8 else (None, confidence)


def lambda_handler(event, context):
    today = datetime.now(UTC).strftime("%Y-%m-%d")

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

    # pass 1: deterministic rules
    for item in scan_all(Attr("PK").begins_with("RAWSCORE#epoch#")):
        raw_name = item["raw_name"]
        key = normalize(raw_name)
        model_id = lookup.get(key)

        if not model_id:
            unmatched.append((raw_name, item))
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

        aliases[raw_name] = (model_id, "rules", "1.0")

    # pass 2: LLM fallback on the residual only
    all_ids = sorted(set(lookup.values()))
    still_unmatched = []
    llm_calls = 0

    for raw_name, item in unmatched:
        if llm_calls >= MAX_LLM_CALLS:
            still_unmatched.append(raw_name)
            continue

        key = normalize(raw_name)
        shortlist = [m for m in all_ids if key[:6] and key[:6] in normalize(m)][:15]
        if not shortlist:
            still_unmatched.append(raw_name)
            continue

        llm_calls += 1
        model_id, confidence = ask_llm(raw_name, shortlist)
        if not model_id:
            still_unmatched.append(raw_name)
            continue

        benchmark = item["benchmark"]
        try:
            score = float(item["score"])
        except (TypeError, ValueError):
            continue

        slot = (model_id, benchmark)
        if slot not in best_scores or score > best_scores[slot][0]:
            best_scores[slot] = (score, raw_name)

        aliases[raw_name] = (model_id, "llm", str(confidence))

    with table.batch_writer() as batch:
        for (model_id, benchmark), (score, raw_name) in best_scores.items():
            batch.put_item(
                Item={
                    "PK": f"MODEL#{model_id}",
                    "SK": f"SCORE#{benchmark}#{today}",
                    "score": str(score),
                    "benchmark": benchmark,
                    "resolved_from": raw_name,
                    "source": "epoch",
                }
            )
        for raw_name, (model_id, method, confidence) in aliases.items():
            batch.put_item(
                Item={
                    "PK": f"ALIAS#{raw_name}",
                    "SK": "RESOLVED",
                    "model_id": model_id,
                    "method": method,
                    "confidence": confidence,
                }
            )

    return {
        "lookup_keys": len(lookup),
        "matched_pairs": len(best_scores),
        "aliases_resolved": len(aliases),
        "llm_calls": llm_calls,
        "unmatched": len(still_unmatched),
        "unmatched_sample": sorted(set(still_unmatched))[:30],
    }