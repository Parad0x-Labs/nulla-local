# Local LLM Proof Dossier

This document is the public, sanitized proof dossier for NULLA's local LLM work.

The story is simple: NULLA can run locally, choose models according to the
machine, execute real tool work, expose proof of what happened, and keep the
default path reliable instead of forcing a heavy model onto every consumer box.

## What We Proved

Published proof in this repo covers:

- NULLA can run a local Ollama-backed runtime acceptance lane.
- The local runtime can answer, use bounded tools, perform file tasks, perform live
  lookup checks, and degrade cleanly when lookup is disabled.
- The proof stack records provider truth, runtime capability truth, latency,
  concurrency, and exact regression targets.
- A 24 GB Apple Silicon class machine can run the local lane with smaller Ollama
  models and pass the tracked acceptance profile.

Outside this public dossier:

- 32B-class models as the hot interactive runtime lane on a 24 GB machine.
- Internet-scale public mesh reliability.
- Trustless economics or public marketplace settlement.
- Remote provider quality as part of the local-only acceptance run.

## Public Evidence

Primary proof files:

- [LOCAL_ACCEPTANCE.md](LOCAL_ACCEPTANCE.md)
- [LLM_ACCEPTANCE_REPORT.md](LLM_ACCEPTANCE_REPORT.md)
- [PROOF_PATH.md](PROOF_PATH.md)
- [reports/greenloop/summary.md](../reports/greenloop/summary.md)
- [reports/greenloop/final_signoff.md](../reports/greenloop/final_signoff.md)
- [reports/greenloop/latency.csv](../reports/greenloop/latency.csv)
- [reports/greenloop/concurrency.csv](../reports/greenloop/concurrency.csv)
- [reports/greenloop/provider_snapshot.json](../reports/greenloop/provider_snapshot.json)
- [config/acceptance/local_ollama_bundle_profile.json](../config/acceptance/local_ollama_bundle_profile.json)

The latest tracked live acceptance snapshot is intentionally marked as a snapshot,
not live current-head truth. Future claims should rerun the commands in
[LOCAL_ACCEPTANCE.md](LOCAL_ACCEPTANCE.md).

## What Is Actually Impressive

### 1. Local runtime proof is machine-readable

The greenloop provider snapshot records:

- active provider: `ollama-local:qwen2.5:7b`
- active runtime locality: `local`
- install profile: `local-only`
- provider privacy class: `local_private`
- runtime capability truth for memory, tools, workspace writes, sandbox execution,
  helper mesh state, public Hive surface state, simulated payments, and partial WAN
  mesh state

That matters because NULLA is not just saying "local-first." The runtime exposes
what provider it used and what capabilities were actually enabled.

Evidence:

- [reports/greenloop/provider_snapshot.json](../reports/greenloop/provider_snapshot.json)

### 2. Acceptance covers real agent behaviors, not just chat

The tracked LLM acceptance snapshot passed:

- recent 48h regression
- live runtime acceptance
- context discipline
- research quality
- Hive integrity
- NullaBook provenance

The scenarios include stale context purging, active task follow-up, fresh lookup
routing, offline accuracy, spoofed write rejection, reward finalization ordering,
and provenance checks.

Evidence:

- [LLM_ACCEPTANCE_REPORT.md](LLM_ACCEPTANCE_REPORT.md)

### 3. Tool and task latency is captured, not hand-waved

The archived local acceptance run measured:

- `P0.2_local_file_create`: `3.639s`
- `P0.3_append`: `0.463s`
- `P0.3b_readback`: `0.440s`
- `P0.5_tool_chain`: `0.687s`
- `P0.4_live_lookup`: `0.293s`
- `offline_honesty`: `0.030s`

Cold start was much slower at `93.813s`, which is why cold start remains an
explicit acceptance metric instead of being hidden.

Evidence:

- [reports/greenloop/latency.csv](../reports/greenloop/latency.csv)
- [LLM_ACCEPTANCE_REPORT.md](LLM_ACCEPTANCE_REPORT.md)

### 4. Concurrency was measured with success rates

The tracked concurrency probe recorded:

| Workers | Requests | Success rate | Throughput |
| ---: | ---: | ---: | ---: |
| 1 | 4 | `1.0` | `0.949 rps` |
| 2 | 8 | `1.0` | `1.377 rps` |
| 4 | 16 | `1.0` | `1.364 rps` |

This is not a claim of internet-scale throughput. It proves that the local
acceptance runtime survived bounded concurrent probing without request failures.

Evidence:

- [reports/greenloop/concurrency.csv](../reports/greenloop/concurrency.csv)

### 5. The installer has a hardware-aware local model path

The public README exposes local install profiles:

- `local-only` / `ollama-only`
- `local-max` / `ollama-max`

The public acceptance profile currently targets a hardware-aware local Ollama
bundle with:

- general model: `qwen3:8b`
- reasoning model: `deepseek-r1:8b`
- fallback bundle: `qwen3:8b`, `gemma3:4b`
- advanced optional profile: `local-max`

Evidence:

- [README.md](../README.md)
- [config/acceptance/local_ollama_bundle_profile.json](../config/acceptance/local_ollama_bundle_profile.json)

## Heavy Model Finding

The important lesson from local heavy-model experiments is product-shaped:

- 32B-class models can be useful for slower research, synthesis, review, and
  offline oracle work.
- They are not the right first-run default for a 24 GB consumer machine.
- The default local profile should prefer a smaller reliable model, then expose
  heavier models as optional or scheduled lanes.

That is a win, not a retreat. A local agent that chooses the wrong model by
default feels broken. A local agent that measures the machine and chooses a
reliable model first is a product.

The practical architecture is a split model stack:

- smaller local model for the hot runtime/controller lane
- heavier local model for slower research or review work
- 32B-class model only when latency and memory pressure are acceptable

This is why `ollama-only` and `ollama-max` exist as profile choices instead of
one forced default for every machine.

## Reproducible Commands

Fast deterministic LLM gate:

```bash
python3 ops/llm_eval.py \
  --skip-live-runtime \
  --output-root reports/llm_eval/latest \
  --baseline-root reports/llm_eval/baselines
```

Full live local acceptance gate:

```bash
python3 ops/llm_eval.py \
  --output-root reports/llm_eval/latest \
  --baseline-root reports/llm_eval/baselines \
  --live-run-root artifacts/acceptance_runs/llm_eval_live \
  --base-url http://127.0.0.1:18080
```

Locked local acceptance profile:

```bash
python3 ops/run_local_acceptance.py full \
  --run-root artifacts/acceptance_runs/$(date -u +%Y-%m-%d)-local-ollama-bundle \
  --profile config/acceptance/local_ollama_bundle_profile.json
```

Full local regression gate:

```bash
python3 ops/pytest_shards.py --workers 6 --pytest-arg=--tb=short
```

## What To Say Publicly

Strong wording:

> NULLA has a reproducible local Ollama acceptance path that proves local runtime,
> local provider truth, bounded tool execution, context discipline, live lookup
> accuracy, provenance checks, and concurrency survival on consumer Apple Silicon.

Do not sell it as:

> NULLA runs a 32B model as the whole real-time agent brain on 24 GB RAM.

The stronger story is the one the proof supports: NULLA is local-first, measured,
hardware-aware, and realistic about when to use heavier models.

## Evidence Position

The local runtime proof is real and worth showing.

The 32B story is a useful engineering finding: heavy models belong in the
advanced lane unless the machine can carry them comfortably. The public headline
should be local-first runtime proof with hardware-aware model selection.
