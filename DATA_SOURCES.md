# Data Sources & Attribution

This project ingests data from two public sources. Both are used in accordance with their licenses.

---

## Epoch AI — Capabilities & Benchmarking Hub

**Used for:** Epoch Capabilities Index (ECI) scores and GPQA Diamond benchmark results.

**Accessed via:** `https://epoch.ai/data/benchmark_data.zip`

**License:** [Creative Commons Attribution 4.0 (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/)

Epoch AI's data is free to use, distribute, and reproduce provided the source and authors are credited.

**Citation:**

> Epoch AI, 'Capabilities & benchmarking'. Published online at epoch.ai. Retrieved from
> https://epoch.ai/benchmarks/use-this-data [online resource]. Accessed 18 October 2026.

**BibTeX:**

```bibtex
@misc{EpochLLMBenchmarkingHub2024,
  title  = {{Capabilities \& benchmarking}},
  author = {{Epoch AI}},
  year   = {2026},
  month  = {10},
  url    = {https://epoch.ai/benchmarks/use-this-data},
  note   = {Accessed: 18 Oct 2026}
}
```

**Note:** The Epoch hub aggregates both internally-run evaluations and results collected from
external projects, which retain their original licensing. This project uses the Epoch Capabilities
Index and GPQA Diamond files, both from Epoch AI internal runs. Benchmark questions and answers
remain the property of their respective creators and are not reproduced here — only aggregate
scores are stored.

---

## OpenRouter — Models API

**Used for:** per-token pricing, context window length, and model metadata.

**Accessed via:** `https://openrouter.ai/api/v1/models` (public, no authentication required)

Data is retrieved from OpenRouter's public API endpoint. No API key is used and no authenticated
or rate-limited endpoint is accessed. Pricing figures are stored as point-in-time snapshots and
are not represented as current except on the date recorded.

---

## Scope of stored data

This repository stores no raw benchmark questions, model outputs, or evaluation transcripts.
Stored records are limited to aggregate scores, pricing figures, model identifiers, and the
mapping between identifiers across sources.