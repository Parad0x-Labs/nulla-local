# NULLA

**Local-first AI agent runtime. Your machine. Your memory. Your mesh.**

NULLA runs on your hardware, remembers everything across sessions, uses tools to do real engineering work, and coordinates trusted helpers over a peer mesh when a task needs more reach. Nothing leaves your box unless you say so.

It's also a node in Web0 — the direction where tasks decompose, agents bid, compute gets rented, and work settles over the x402 payment rail.

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Status: Alpha](https://img.shields.io/badge/Status-Alpha-orange.svg)](docs/STATUS.md)
[![Python: 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://python.org)
[![CI](https://github.com/Parad0x-Labs/nulla-hive-mind/actions/workflows/ci.yml/badge.svg)](https://github.com/Parad0x-Labs/nulla-hive-mind/actions/workflows/ci.yml)

<p align="center">
  <img src="./docs/assets/github-header-nulla-local.png" alt="Parad0x Labs" width="100%" />
</p>

```
local NULLA agent → memory + tools → optional trusted helpers → mesh task market → results
```

---

## What makes NULLA different

### Tool-use agent loop — not prompt theater

NULLA runs a real agent loop: call LLM → parse tool intent → execute (read files, run tests, write code, search web) → feed result back → repeat until done. It doesn't hand you a one-shot guess and call it a day.

**Benchmark on real engineering tasks (5 tasks requiring tool use):**

| | Score | Notes |
|---|---|---|
| **NULLA** (14b + tools + loop) | **5/5** | Iterates, fixes, verifies |
| Ollama 14b single-shot | 4/5 | Fails cross-file rename — no iteration |

Tasks were specifically designed to be impossible without tool use: bugs only visible at runtime, multi-file changes that require reading before editing. The benchmark is in `tests/benchmarks/agent_capability_bench.py` — run it yourself.

### Three-tier memory that actually works

Most local LLM setups either blow up the context window or chop off the beginning and lose everything. NULLA compresses without forgetting.

**Memory benchmark (30-turn conversation, 5 facts planted early):**

| Mode | Recall | Peak tokens |
|---|---|---|
| Raw (no compression) | 5/5 (100%) | 528 |
| Sliding window (10) | 0/5 **(0%)** | 362 (-31%) |
| **NULLA ContextWindow** | **5/5 (100%)** | **335 (-36%)** |

Sliding window is the naive approach every other local stack uses. It cuts tokens by just forgetting everything old — including your passwords, deadlines, and API keys. NULLA cuts 36% of tokens and remembers everything.

The three tiers:
- **L1** — recent turns verbatim (always in context)
- **L2** — LLM-compressed structured summary of older turns (Key Facts / Decisions / Open Questions / Context — exact values preserved word-for-word)
- **L3** — semantic memory nodes in SQLite, retrieved by embedding similarity with `nomic-embed-text`

Smart retrieval: before injecting L3 nodes, NULLA checks whether the content is already covered in L2. No token bloat from re-injecting facts the summary already has.

### Importance scoring

Every turn gets scored before being stored in L3:

```
password / API key  → 0.6–0.95
port / date         → 0.45–0.50
decision / deadline → 0.40–0.45
generic explanation → 0.20
```

High-importance turns are prioritised during retrieval. Your `sk-prod-xxxx` stays findable. "Can you explain async/await?" does not crowd it out.

### Semantic search with real embeddings

Plugs into `nomic-embed-text` via Ollama (274MB, 768-dim). Falls back to a hash bag-of-words if not installed. The same embedding service backs L3 retrieval across sessions — ask something in session 2, get a relevant fact from session 1.

### Honest capability reporting

`GET /api/runtime/capabilities` tells you exactly what is implemented, what is simulated, and what is disabled — per feature, no marketing spin. `/healthz` reports commit + dirty bit. The runtime surfaces its own truth.

---

## How this fits the Parad0x stack

Parad0x Labs builds Web0 on Solana — money and agents that settle themselves. **You are here: 🧠 Local AI (the runtime that consumes every layer).**

| Layer | Repo | Does |
|---|---|---|
| 💸 Payments | [dna-x402](https://github.com/Parad0x-Labs/dna-x402) | x402 rail: quote → pay → verify → receipt → anchor |
| 🛠️ Build | [dna-x402-builders](https://github.com/Parad0x-Labs/dna-x402-builders) | Hosted kit: turn any API/bot into a paid agent |
| 🕶️ Privacy | [Dark-Null-Protocol](https://github.com/Parad0x-Labs/Dark-Null-Protocol) | Groth16 privacy settlement, published proofs |
| 🗜️ Data | [liquefy](https://github.com/Parad0x-Labs/liquefy) | Columnar compression that beats Zstd |
| 🛡️ Audit | [liquefy-openclaw-integration](https://github.com/Parad0x-Labs/liquefy-openclaw-integration) | Flight recorder: 24 engines + Solana-anchored audit trails |
| 🎬 Media | [nebula-media](https://github.com/Parad0x-Labs/nebula-media) | Proof-carrying media compression — scene-aware + on-chain receipts |
| 🧠 Local AI | **nulla-local** (this repo) | Local-first agent runtime — your machine, your memory |

**See it live:** **[parad0xlabs.com](https://parad0xlabs.com)**

---

## Install

macOS / Linux:

```bash
curl -fsSLo bootstrap_nulla.sh https://raw.githubusercontent.com/Parad0x-Labs/nulla-hive-mind/main/installer/bootstrap_nulla.sh
bash bootstrap_nulla.sh
```

Windows PowerShell:

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/Parad0x-Labs/nulla-hive-mind/main/installer/bootstrap_nulla.ps1 -OutFile bootstrap_nulla.ps1
powershell -ExecutionPolicy Bypass -File .\bootstrap_nulla.ps1
```

Profiles:

```bash
# Safest — smaller machines, zero remote dependency
bash bootstrap_nulla.sh --install-profile ollama-only

# Full local power — 24 GiB+ unified memory or equivalent
bash bootstrap_nulla.sh --install-profile ollama-max
```

After install:

```bash
cd ~/nulla-hive-mind && .venv/bin/python -m apps.nulla_cli install-profile --set ollama-max
```

Full install docs: [docs/INSTALL.md](docs/INSTALL.md)

---

## What works right now

- **Agent loop** — LLM → tool call → execute → iterate → done. Not a single-shot wrapper.
- **Three-tier memory** — L1 verbatim + L2 structured compression + L3 semantic SQLite. 36% fewer tokens, 100% recall.
- **Embedding service** — nomic-embed-text (768-dim) with hash-BoW fallback. Cross-session retrieval.
- **Importance scoring** — passwords, keys, dates, decisions tagged and prioritised in memory.
- **Stress-tested at scale** — benchmark supports `--turns 100` and `--turns 200` scenarios.
- **Persistent memory across sessions** — NullaMemory SQLite backend.
- **Bounded coding/operator flow** — search → read → patch → validate → rollback if broken.
- **Append-only task/proof spine** — every repair and orchestration step is inspectable, not locked inside the executor.
- **Mesh task market** — decompose → escrow → offer → claim → execute → review → reward. Ed25519-signed credit settlement. Single-node and loopback verified end-to-end.
- **3-layer anti-cheat proof-of-work credits** — challenge-response, staking, ZK-proof path.
- **Compute-rental market** — prices your real hardware, welds x402 receipt hash into tamper-evident `WorkProof`.
- **Role-aware provider routing** — local drone lanes vs synthesis lanes, local llama.cpp, vLLM, and Kimi lanes when configured.
- **Honest capability API** — `GET /api/runtime/capabilities` — implemented / simulated / disabled, per feature.
- **CI** — sharded local regression + GitHub Actions + fast LLM acceptance suite.

---

## Run the benchmarks

```bash
# Agent capability: NULLA tool loop vs Ollama single-shot
python -m tests.benchmarks.agent_capability_bench

# Memory compression: recall vs token budget at 30 / 100 / 200 turns
python -m tests.benchmarks.memory_compression_bench
python -m tests.benchmarks.memory_compression_bench --turns 100
python -m tests.benchmarks.memory_compression_bench --turns 200

# Provider comparison across 4 models × 4 task categories
python -m tests.benchmarks.nulla_vs_standard
```

---

## Repo map

- `core/` — agent runtime, memory, tools, mesh, credits, compute, Hive, web
  - `core/context_window.py` — three-tier memory manager
  - `core/conversation_summarizer.py` — structured LLM compression
  - `core/embedding_service.py` — nomic-embed-text + hash-BoW fallback
  - `core/nulla_memory.py` — SQLite-backed persistent memory
  - `core/agent_runtime/` — turn loop, fast paths, research loop
- `apps/` — API server, CLI, agent entrypoints
- `tests/` — regression coverage + benchmarks
- `installer/` — one-click setup
- `docs/` — architecture, status, trust, runbooks

Full map: [`REPO_MAP.md`](REPO_MAP.md)

---

## For developers

```bash
git clone https://github.com/Parad0x-Labs/nulla-hive-mind.git
cd nulla-hive-mind
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,runtime]"
python3 -m apps.nulla_api_server
```

Useful entry points:

```bash
python3 -m apps.nulla_api_server        # local API on :11435
python3 -m apps.nulla_agent --interactive
curl http://127.0.0.1:11435/api/runtime/capabilities
```

Proof path for skeptics: [docs/PROOF_PATH.md](docs/PROOF_PATH.md)

Architecture: [docs/SYSTEM_SPINE.md](docs/SYSTEM_SPINE.md) · [docs/CONTROL_PLANE.md](docs/CONTROL_PLANE.md) · [docs/STATUS.md](docs/STATUS.md)

---

*NULLA is alpha. The core runtime and memory system are real and working on `main`. Mesh economics and live settlement are still hardening. `GET /api/runtime/capabilities` tells you the exact truth at any moment.*

<p align="center">
  <img src="./docs/assets/github-footer-parad0xlabs.png" alt="NULLA — Parad0x Labs open source systems" width="100%" />
</p>
