# Windows Local OpenClaw Setup

This is the Windows local-only setup contract for running NULLA behind OpenClaw.

## Local-only runtime

Required environment:

```bat
set OLLAMA_MODELS=<drive>\Ollama\models
set OLLAMA_API_KEY=ollama-local
set NULLA_HOME=<drive>\.nulla_runtime
set NULLA_OLLAMA_MODEL=<provider-probe-recommended-model>
```

On older NVIDIA cards that crash Ollama CUDA warmup, force CPU execution:

```bat
set OLLAMA_LLM_LIBRARY=cpu
```

The CPU path is stable but slow. Treat it as a fallback, not a performance target.

Required local Ollama models are reported by the provider probe. On the current GTX 1080 / 8 GiB RAM Windows host, the recommended local bundle is:

```bat
ollama pull gemma3:4b
ollama pull nomic-embed-text
```

`gemma3:4b` is the recommended chat model for this constrained CPU-fallback host. `qwen2.5:7b` is still a valid installed legacy fallback, but it is slower and heavier here. `nomic-embed-text` is the local embedding model used by OpenClaw memory search.

## Hardware and model scan

Use the provider probe before pulling more models:

```bat
python installer\provider_probe.py
```

The probe is the local source of truth for:

- CPU, RAM, GPU, VRAM, accelerator status, and acceleration advice
- recommended install profile and local model bundle
- installed local models versus missing recommended models
- exact `ollama pull ...` commands for the current PC
- safe disk floor for the recommended local bundle, measured against the Ollama model store volume

On Windows legacy NVIDIA CUDA devices such as GTX 10-series cards, the scanner keeps the GPU visible but sizes local models as CPU-only unless `NULLA_ALLOW_LEGACY_CUDA=1` is set after a successful Ollama warmup. This prevents dead CUDA VRAM from making the installer recommend a bundle that looks good on paper but fails or crashes at runtime.

## OpenClaw registration

Register NULLA after the API environment is set:

```bat
python installer\register_openclaw_agent.py "%CD%" "%NULLA_HOME%" "%NULLA_OLLAMA_MODEL%" "NULLA"
```

The registration code writes a local-only OpenClaw config:

- default `nulla` agent points at `nulla/nulla`
- `models.providers.nulla.baseUrl` points at `http://127.0.0.1:11435`
- `tools.web.search.enabled` is `false`
- `agents.defaults.memorySearch.provider` is `ollama`
- `agents.defaults.memorySearch.model` is `nomic-embed-text`
- `agents.defaults.memorySearch.fallback` is `none`
- stale `plugins.entries.ollama` config is removed

This avoids the invalid `tools.web.search.provider = ollama` config. Ollama is a model provider, not an OpenClaw web-search provider.

## Gateway startup

On Windows, prefer direct OpenClaw gateway startup:

```bat
openclaw gateway run --force --port 18789
```

The Windows launcher now uses that path first. `ollama launch openclaw` remains only a fallback because it can fail to start the gateway on native Windows.

## Windows fork changes

This pass hardens the Windows local path in these areas:

- launchers repair stale `OLLAMA_MODELS`, set `OLLAMA_API_KEY`, preserve the selected `NULLA_OLLAMA_MODEL`, and prefer direct OpenClaw gateway startup
- OpenClaw registration writes schema-valid local-only config, disables hosted web search, removes stale missing-plugin entries, and configures local Ollama memory embeddings
- hardware/model scanning reports accelerator viability, ignores CPU-fallback GPU VRAM for sizing, checks the real Ollama model-store drive, skips stale missing-drive env paths, and emits exact missing-model pull commands
- installers pull the recommended local model bundle and the OpenClaw memory embedding model when OpenClaw is enabled
- runtime path handling accepts Windows absolute paths while preserving POSIX-style relative workspace paths in tool output
- sandbox/job execution resolves Windows executables and path separators correctly
- installer doctor, install-profile validation, runtime provider truth, and local acceptance checks avoid Unix-only assumptions
- NULLA API exposes `/healthz` for launcher readiness and OpenClaw checks
- NullaBook writes use deterministic ordering when timestamps collide on Windows
- repo hygiene emits POSIX repo-relative paths and keeps generated local context out of tracked root files

## Verification

Expected local checks:

```bat
openclaw config validate
openclaw doctor --non-interactive --no-workspace-suggestions
openclaw gateway health
openclaw agents list
openclaw memory status --deep
openclaw memory search --agent nulla --query "workspace memory notes" --json
```

A complete OpenClaw-to-NULLA smoke test:

```bat
openclaw agent --agent nulla --message "Reply exactly OPENCLAW_NULLA_OK" --json --timeout 240
```

Current honest weakness: the local model path can return extra conversational text after the requested marker. That proves routing works, but it is not strict response control.
