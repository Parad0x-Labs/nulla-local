# NULLA/OpenClaw Final Local Acceleration Handover

Date: 2026-06-18
Scope: local only. No commits, no pushes, no GitHub writes.

## Repos

- OpenClaw: `/home/nulla-user/openclaw`
- NULLA: `/home/nulla-user/nulla`

## Hard Guardrails

- Do not modify Web0 code/tests unless explicitly authorized later.
- Tracked Web0 files checked after work:
  - `core/web0_tools.py`
  - `core/web0_gated_html.py`
  - `tests/test_web0_tools.py`
  - `tests/test_web0_gated_html.py`
- `git diff --name-only -- core/web0_tools.py core/web0_gated_html.py tests/test_web0_tools.py tests/test_web0_gated_html.py` returned no output.
- There are untracked Web0-looking files in the NULLA worktree from earlier local work. Leave them alone unless the user explicitly changes the Web0 rule.
- Do not commit or push.
- The root `IDENTITY.md` clutter file was moved out of the NULLA repo root to satisfy the hygiene check. Do not weaken `tests/test_repo_hygiene_check.py`.

## Final Running Services

At handover, these local services were restarted and listening:

- NULLA API: `http://127.0.0.1:11435`
- llama.cpp OpenAI-compatible server: `http://127.0.0.1:8090/v1`
- OpenClaw dashboard: `http://127.0.0.1:18789`
- OpenClaw gateway WebSocket: `ws://127.0.0.1:19001`

Service sessions:

- `screen -S nulla-llamacpp`
- `screen -S nulla-api`

Useful checks:

```bash
screen -ls
lsof -nP -iTCP:11435 -sTCP:LISTEN
lsof -nP -iTCP:8090 -sTCP:LISTEN
lsof -nP -iTCP:18789 -sTCP:LISTEN
lsof -nP -iTCP:19001 -sTCP:LISTEN
curl -sS http://127.0.0.1:11435/healthz
curl -sS http://127.0.0.1:11435/api/runtime/capabilities | python3 -m json.tool
```

## Final Runtime Truth

Final `/api/runtime/capabilities` summary after restart:

```json
{
  "browser_render": "ok",
  "backend_kv_cache": "active",
  "speculative_decoding": "active",
  "eagle_status": "unsupported_by_backend"
}
```

Important distinction:

- Backend KV/cache proof is active for the live llama.cpp server.
- Generic prompt-lookup speculative decoding is active for llama.cpp.
- EAGLE is not active. It is explicitly reported as `unsupported_by_backend`.

## Final Live Lane Proof

After restarting NULLA API, this live prompt was run:

```text
High-risk engineering task. Refactor adaptive lane proof. Verifier required. Answer with one sentence under 30 words.
```

Captured final `model_lane_proof`:

```json
{
  "lane": "deep",
  "phase": "completed",
  "provider_id": "llamacpp-local:qwen2.5:14b-gguf",
  "model_id": "qwen2.5:14b-gguf",
  "verifier_status": "independent_completed",
  "verifier_provider_id": "ollama-local:qwen3:8b",
  "verifier_model_id": "qwen3:8b",
  "kv_cache_status": "llama.cpp=cache_active",
  "speculative_status": "active",
  "eagle_status": "unsupported_by_backend"
}
```

Final explicit 35B prompt:

```text
Explicitly use qwen3.5:35b-a3b for this hard engineering analysis. Keep it short.
```

Captured proof:

```json
{
  "lane": "deep",
  "phase": "blocked",
  "planned_model_id": "qwen3.5:35b-a3b",
  "fallback_reason": "explicit_heavy_lane_unavailable",
  "verifier_status": "blocked_no_primary"
}
```

This proves `qwen3.5:35b-a3b` does not become a default and is not silently run.

## Browser and Workspace Proof

Browser render was proven with the runtime tool contract:

```python
from core.runtime_execution_tools import execute_runtime_tool

result = execute_runtime_tool(
    "web.browser_render",
    {"url": "https://example.com", "timeout_ms": 10000, "max_scroll": 0},
    source_context={"surface": "openclaw", "workspace_root": "/home/nulla-user/nulla"},
)
```

Result:

- `ok=True`
- `status=ok`
- title: `Example Domain`
- observation intent: `web.browser_render`

Workspace repo read was proven through `workspace.read_file`, not host machine reads:

Prompt:

```text
Using workspace tools only, read pyproject. toml and tell me the project name plus the Python version requirement.
```

Final response:

```text
Project name: `nulla-hive-mind`. Python requirement: `>=3.10`. Read via `workspace.read_file`.
```

This also fixed the live normalization issue where `pyproject.toml` became `pyproject. toml`.

## Major NULLA Changes

Main implemented areas:

- `core/backend_acceleration_truth.py`
  - New proof module for backend cache, speculative decoding, and EAGLE truth.
  - llama.cpp cache/spec active only with live generation probe.
  - Ollama cache truth remains `not_supported_keep_alive_only`.
  - EAGLE remains unsupported unless a real EAGLE backend appears.

- `core/runtime_capabilities.py`
  - Exposes browser status, workspace access, compaction config, backend KV cache proof, speculative decoding proof, EAGLE status, and lane defaults.

- `core/memory_first_router.py`
  - Emits live-probed `LaneProofV1`.
  - Lane proof now includes verifier provider/model, cache proof, speculative proof, and EAGLE proof.
  - Fix: local autopilot no longer overrides an explicitly ranked remote queen lane when paid/queen fallback is allowed.

- `core/local_inference_autopilot.py`
  - Deep lane now prefers live llama.cpp specialist for code/deep work.
  - Independent verifier now requires a different provider/model.
  - For this machine: llama.cpp 14B primary plus qwen3:8b verifier is the proven safe lane shape.
  - 35B stays explicit-only and blocked when unavailable/unhealthy.
  - Risk/verifier language such as `high-risk`, `verifier required`, `failure mode`, `refactor` now escalates to deep/verifier.

- `core/agent_runtime/fast_paths_utility.py`
  - Workspace file fast path handles spaced extensions like `pyproject. toml`.
  - pyproject metadata prompt returns extracted facts instead of dumping the whole file.
  - Explicit 35B request gets an early blocked response before model planning.

- `core/agent_runtime/fast_command_surface.py`
  - Explicit heavy block emits a deep/blocked `model_lane_proof` with `planned_model_id=qwen3.5:35b-a3b`.

- `core/agent_runtime/turn_frontdoor.py`
  - Explicit heavy block runs before other planner/model work.

- `installer/llamacpp_local.py` and `installer/install_nulla.sh`
  - llama.cpp local provisioning now carries cache/spec flags.

- `core/runtime_provider_defaults.py`
  - llama.cpp manifest refreshes stale base URL/context/concurrency instead of accepting stale provider manifests.

- `core/web/api/runtime.py`
  - `NULLA_SKIP_PROVIDER_PREWARM=1` skip path avoids API startup blocking on model prewarm.

## Major OpenClaw Changes

Main touched UI areas:

- `ui/src/ui/views/chat.ts`
  - Swarm rail renders lane proof truth, verifier provider/model, cache/spec state, and EAGLE status.

- `ui/src/ui/views/chat.test.ts`
  - Added/updated tests for independent verifier, cache active, speculative active, EAGLE unsupported, fallback/blocked/mismatch states.

There are additional existing dirty OpenClaw UI/session files from prior local NULLA/OpenClaw integration work. Do not revert user-owned or prior-agent dirty files blindly.

## Local Model/Backend State

llama.cpp specialist model:

```text
/home/nulla-user/.nulla_runtime/models/llamacpp/qwen2.5-coder-14b-instruct-q4_k_m.gguf
```

Runtime env config files used:

```text
/home/nulla-user/.nulla_runtime/config/provider-env.sh
/home/nulla-user/nulla/.nulla_local/config/provider-env.sh
```

Important env values:

```bash
LLAMACPP_BASE_URL=http://127.0.0.1:8090/v1
NULLA_LLAMACPP_CONTEXT_WINDOW=4096
NULLA_LLAMACPP_CACHE=1
NULLA_LLAMACPP_CACHE_TYPE=ram
NULLA_LLAMACPP_DRAFT_MODEL=prompt-lookup-decoding
NULLA_LLAMACPP_DRAFT_MODEL_NUM_PRED_TOKENS=10
NULLA_SKIP_PROVIDER_PREWARM=1
```

llama.cpp start script:

```text
/tmp/start-nulla-llamacpp.sh
```

NULLA API start script:

```text
/tmp/start-nulla-api-screen.sh
```

## Tests Run and Final Results

NULLA full suite:

```bash
python3 -m pytest -q tests/
```

Final result:

```text
1992 passed, 11 skipped, 13 xfailed, 15 xpassed, 3 warnings
```

OpenClaw touched UI/session tests:

```bash
node scripts/run-vitest.mjs ui/src/ui/views/chat.test.ts ui/src/ui/views/sessions.test.ts ui/src/ui/thinking.ts
```

Final result:

```text
603 passed
```

Targeted checks also passed during work:

```bash
python3 -m pytest -q \
  tests/test_agent_runtime_fast_command_surface.py \
  tests/test_agent_runtime_turn_frontdoor.py \
  tests/test_local_inference_autopilot.py \
  tests/test_runtime_capabilities.py \
  tests/test_nulla_api_server.py \
  tests/test_runtime_execution_tools.py \
  tests/test_openclaw_tooling_context.py \
  tests/test_repo_hygiene_check.py
```

## Known Caveats

- EAGLE is still not active. This is correct and accurate for current backend support.
- llama.cpp prompt-lookup speculative decoding is active, but it is not EAGLE.
- Some live answers are semantically weak because the local model is small/specialized. The routing/proof layer is now real; answer quality still depends on model capacity.
- Ollama currently may keep `qwen3:0.6b` and `qwen3:8b` resident after verifier/classifier runs. This is expected. 35B was not loaded in final proof.
- `screenlog.0` exists in NULLA repo root from screen logging. Treat it as local runtime log clutter; do not commit it.
- The NULLA worktree has substantial pre-existing dirty state and untracked files. Do not assume every dirty file is from the final acceleration pass.

## If Picking Up Next

1. Start by checking services:

```bash
screen -ls
curl -sS http://127.0.0.1:11435/api/runtime/capabilities | python3 -m json.tool
lsof -nP -iTCP:11435 -sTCP:LISTEN
lsof -nP -iTCP:8090 -sTCP:LISTEN
lsof -nP -iTCP:18789 -sTCP:LISTEN
lsof -nP -iTCP:19001 -sTCP:LISTEN
```

2. Re-run the hard/verifier proof prompt through OpenClaw or `/api/chat` and confirm:

```text
lane=deep
provider_id=llamacpp-local:qwen2.5:14b-gguf
verifier_status=independent_completed
verifier_provider_id=ollama-local:qwen3:8b
kv_cache_status=llama.cpp=cache_active
speculative_status=active
eagle_status=unsupported_by_backend
```

3. Re-run full suites after any meaningful change:

```bash
cd /home/nulla-user/nulla
python3 -m pytest -q tests/

cd /home/nulla-user/openclaw
node scripts/run-vitest.mjs ui/src/ui/views/chat.test.ts ui/src/ui/views/sessions.test.ts ui/src/ui/thinking.ts
```

4. Keep Web0 untouched unless the user explicitly authorizes it.

