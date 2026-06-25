# Local Acceptance

This is the locked local acceptance bar for NULLA on the hardware-aware local Ollama bundle.

Canonical profile:
- [`config/acceptance/local_ollama_bundle_profile.json`](../config/acceptance/local_ollama_bundle_profile.json)

Canonical command:

```bash
python3 ops/run_local_acceptance.py full \
  --run-root artifacts/acceptance_runs/$(date -u +%Y-%m-%d)-local-ollama-bundle \
  --profile config/acceptance/local_ollama_bundle_profile.json
```

What `full` does:
1. starts local NULLA on the current checked-out commit
2. runs the online acceptance suite
3. captures a manual BTC spot-check against the locked source
4. restarts with web lookup disabled and runs the offline accuracy gate
5. restores normal online mode
6. renders the final report under `artifacts/acceptance_runs/<stamp>/evidence/`

Locked gate:
- cold start must stay under `120s`
- simple-prompt median must stay under `8s`
- file-task median must stay under `15s`
- live-lookup median must stay under `45s`
- chained-task median must stay under `60s`
- consistency must stay at `>= 2/3`
- all P0 checks must pass
- offline accuracy must pass
- manual BTC verification must pass

This is a real gate, not a vanity report. If a future run wants to call itself green, it should pass this profile or a stricter one.

## LLM Evaluation Commands

Fast deterministic acceptance gate:

```bash
python3 ops/llm_eval.py \
  --skip-live-runtime \
  --output-root reports/llm_eval/latest \
  --baseline-root reports/llm_eval/baselines
```

Full live acceptance gate:

```bash
python3 ops/llm_eval.py \
  --output-root reports/llm_eval/latest \
  --baseline-root reports/llm_eval/baselines \
  --live-run-root artifacts/acceptance_runs/llm_eval_live \
  --base-url http://127.0.0.1:18080
```

If you explicitly want to refresh the tracked docs snapshot too:

```bash
python3 ops/llm_eval.py \
  --output-root reports/llm_eval/latest \
  --baseline-root reports/llm_eval/baselines \
  --live-run-root artifacts/acceptance_runs/llm_eval_live \
  --base-url http://127.0.0.1:18080 \
  --docs-report-path docs/LLM_ACCEPTANCE_REPORT.md
```

What the fast gate proves:
- rerun of the last 48h LLM/runtime regression pack
- context-discipline scenarios
- research-quality scenarios
- Hive integrity scenarios
- NullaBook provenance scenarios

What only the live gate proves:
- real local runtime boot on the current commit
- real latency numbers for the locked local profile
- live lookup/manual verification and offline accuracy on the same runtime

## Current Branch Truth

Current working head: `467e739` (`codex/honest-ollama-prewarm-bootstrap`).

Freshly verified on this head:

- `python3 ops/cumulative_stabilization.py --through G` passed on 2026-03-30
- targeted cumulative packs: `193 passed, 2 skipped, 1 warning in 22.32s`
- appended full suite: `1811 passed, 11 skipped, 13 xfailed, 15 xpassed, 3 warnings in 89.10s`

What has not been rerun on this head:

- `python3 ops/run_local_acceptance.py full --run-root ... --profile config/acceptance/local_ollama_bundle_profile.json`
- live `python3 ops/llm_eval.py ... --docs-report-path docs/LLM_ACCEPTANCE_REPORT.md`

Do not treat the archived latency numbers below as current-head proof until those commands are rerun.

## Latest Archived Live Checkpoint

Greenloop rerun on `15948c7`:

- cold start: `6.592s`
- simple prompt median: `3.309s`
- file task median: `0.461s`
- live lookup median: `0.173s`
- chained task median: `0.635s`

Measured concurrency on the same archived local `qwen2.5:7b` lane:

- worker `1`: success `1.0`, throughput `0.26 req/s`
- worker `2`: success `1.0`, throughput `0.242 req/s`
- worker `4`: success `1.0`, throughput `0.229 req/s`

Artifacts:

- [`docs/LLM_ACCEPTANCE_REPORT.md`](./LLM_ACCEPTANCE_REPORT.md)
- [`reports/greenloop/summary.md`](../reports/greenloop/summary.md)
- [`reports/greenloop/concurrency.csv`](../reports/greenloop/concurrency.csv)

Note:

- `llm_eval` no longer rewrites [`docs/LLM_ACCEPTANCE_REPORT.md`](./LLM_ACCEPTANCE_REPORT.md) unless `--docs-report-path` is passed explicitly, so normal proof runs stop dirtying the checkout just to publish a docs snapshot.
