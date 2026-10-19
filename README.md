# LLM Benchmark & Pricing Pipeline

A serverless AWS pipeline that tracks the **cost-efficiency frontier** of large language models by joining two independent public datasets: capability benchmark scores and live per-token pricing.

The interesting problem isn't ingestion — it's that the two sources don't agree on what anything is called.

---

## The entity resolution problem

The same model appears as:

| Source | Identifier |
|---|---|
| Epoch AI (ECI) | `Claude Opus 5.5` |
| Epoch AI (GPQA) | `claude-opus-5-5_max` |
| OpenRouter (id) | `anthropic/claude-opus-5.5` |
| OpenRouter (name) | `Anthropic: Claude Opus 5.5` |

No shared key exists. Joining price to capability requires deciding which strings refer to the same underlying model.

### Approach: deterministic rules first, LLM on the residual

**Layer 1 — normalization rules.** Both sides are reduced to a comparable key by stripping, in order:

- organization prefixes (`anthropic/`, `Anthropic: `)
- reasoning-effort suffixes (`_max`, `_high`, `_none`, …)
- context-window suffixes (`_32K`, `_59K`)
- release date stamps (`-20250929`, `-2026-03-17`)
- version suffixes (`-v1`)
- disambiguating parentheticals (`(October 2024)`)
- all remaining non-alphanumerics, so `5-5` and `5.5` converge

**Layer 2 — Claude Haiku via Amazon Bedrock**, invoked only on names the rules missed, and only against a prefix-filtered shortlist of at most 15 candidates. The model returns a structured verdict with a confidence score; replies below 0.8, or naming a candidate not in the shortlist, are discarded.

### Results

| | |
|---|---|
| Epoch model names ingested | 597 |
| Resolved to an OpenRouter identifier | **345** |
| Model x benchmark score pairs written | 244 |
| Unresolved | 252 |

**The unresolved set was verified, not assumed.** Spot-checking showed it is dominated by models OpenRouter no longer serves — `Claude 3 Opus`, `Claude Instant`, `Baichuan1-7B`, `DeepSeek-R1-Distill-Qwen-14B`. A direct query confirmed zero `claude-3*` identifiers exist in the current OpenRouter catalog. These are **correct** non-matches: Epoch benchmarks models historically, OpenRouter lists only what is currently purchasable.

Distinguishing genuine absence from resolver failure is the point. A resolver that "matched" these would be wrong.

### Rules that were deliberately not added

`Amazon Nova Pro` does not resolve to `amazon/nova-pro-v1`, because Epoch embeds the organization in its display name while OpenRouter separates it into a prefix. The obvious fix — stripping trailing version numbers from display names — would also collapse `Claude Opus 4` to `claudeopus`, producing false matches against every Opus variant. **The rule was rejected as costing more precision than it bought.**

---

## Architecture

```
EventBridge (daily 09:00 UTC)
  |
  +-> SyncPricing --> S3        raw/openrouter/dt=<date>/models.json
                 \-> DynamoDB   MODEL#<id> / META
                                MODEL#<id> / PRICE#<date>

EventBridge (weekly Sun 10:00 UTC)
  |
  +-> SyncScores  --> S3        raw/epoch/dt=<date>/benchmark_data.zip
                 \-> DynamoDB   RAWSCORE#epoch#<name> / SCORE#<bench>#<date>

Resolve (on demand)
  reads   META + RAWSCORE#
  writes  MODEL#<id> / SCORE#<bench>#<date>
          ALIAS#<raw_name> / RESOLVED   (method + confidence)
```

### Single-table design

One DynamoDB table holds four item types, distinguished by key prefix:

| PK | SK | Holds |
|---|---|---|
| `MODEL#<id>` | `META` | display name, canonical slug |
| `MODEL#<id>` | `PRICE#<date>` | prompt cost, completion cost, context length |
| `MODEL#<id>` | `SCORE#<bench>#<date>` | resolved benchmark score |
| `ALIAS#<raw_name>` | `RESOLVED` | resolution target, method, confidence |

The partition key is hashed to distribute items across storage; the sort key keeps items within a partition ordered. One query against `MODEL#anthropic/claude-opus-5.5` returns metadata, every price snapshot, and every benchmark score together — no joins, one round trip.

Unresolved scores land under a separate `RAWSCORE#` namespace rather than being guessed into `MODEL#`. Raw and resolved data never mix.

---

## Data sources

| Source | Content | Access |
|---|---|---|
| [OpenRouter](https://openrouter.ai/api/v1/models) | 471 models, live per-token pricing, context length | public, keyless |
| [Epoch AI Benchmarking Hub](https://epoch.ai/benchmarks/use-this-data) | Epoch Capabilities Index, GPQA Diamond | public ZIP, CC BY 4.0 |

See [DATA_SOURCES.md](DATA_SOURCES.md) for required attribution.

---

## Design notes

**Raw data is written to S3 before parsing.** If parsing logic proves wrong later, the originals can be reprocessed. Pricing data in particular cannot be recovered retroactively — OpenRouter serves only the current snapshot, so a day not captured is a day lost.

**Scores and prices are stored as strings.** DynamoDB rejects Python floats via boto3, and float rounding on monetary values is a real correctness hazard. Conversion to numeric types happens in the analysis layer.

**Pricing is per single token**, not per million — `0.000004` is $4 per million prompt tokens. The multiplication belongs in presentation, not storage.

**Costs are bounded.** Lambda, DynamoDB on-demand, and S3 at this volume sit inside the AWS free tier. The Bedrock fallback is capped per run via the `MAX_LLM_CALLS` environment variable.

---

## Things that broke, and what they revealed

**`BatchWriteItem: Provided list of item keys contains duplicates`**

Epoch evaluates the same model at several reasoning efforts (`_max`, `_high`, `_low`). After normalization these collapse to one identifier, so a single batch tried to write the same key more than once. Fixed by accumulating into a dictionary keyed on `(model_id, benchmark)` and **keeping the highest score**, which matches leaderboard convention. The error surfaced a data-modelling decision that would otherwise have gone unmade.

**Bedrock reported 40 calls and zero matches.**

The fallback appeared to be working and simply finding nothing. It was in fact failing on every call — the chosen model id was not enabled for the account, and a bare `except Exception` was collapsing the error into the same return value used for "no match." Fixed by logging the exception before returning. A silent failure that reports success is worse than a crash.

---

## Stack

Python 3.12 · AWS Lambda · DynamoDB · S3 · EventBridge · Amazon Bedrock (Claude Haiku) · AWS SAM

Infrastructure is defined entirely in `template.yaml` and deployed with `sam deploy`. No resource was created by hand in the console.

## Repository layout

```
functions/
  sync_pricing/app.py    OpenRouter ingestion   (daily)
  sync_scores/app.py     Epoch ingestion        (weekly)
  resolve/app.py         entity resolution      (on demand)
template.yaml            all infrastructure
```

## Deploy

```bash
sam build && sam deploy --guided
```