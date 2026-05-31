# NULLA Hive Mind

NULLA is a local-first agent runtime. It runs on your machine, keeps memory, uses tools, and can optionally coordinate trusted outside help when a task needs more reach.

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Status: Alpha](https://img.shields.io/badge/Status-Alpha-orange.svg)](docs/STATUS.md)
[![Python: 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://python.org)
[![CI](https://github.com/Parad0x-Labs/nulla-hive-mind/actions/workflows/ci.yml/badge.svg)](https://github.com/Parad0x-Labs/nulla-hive-mind/actions/workflows/ci.yml)

The public web, Hive, and OpenClaw are access and inspection surfaces around that runtime. They are not separate products.

`main` is now the real alpha trunk again. The shipped repo is no longer lagging behind a stale side-branch story.

Current state:

- Real alpha now: local runtime, memory, tools, bounded research, bounded local operator execution with append-only task/proof events, Hive task flow, and public proof/work surfaces.
- Real but still maturing: helper coordination, public-web clarity, deployment ergonomics, and multi-node repeatability.
- Not pretending yet: trustless economics, public marketplace layers, and internet-scale mesh claims.
- Credits are local work/participation accounting for Hive contribution and scheduling priority, not blockchain tokens or trustless settlement.

The main lane is simple:

`local NULLA agent -> memory + tools -> optional trusted helpers -> results`

Everything else in this repo should be understood as a surface or supporting system around that lane.

### How this fits the Parad0x stack

Parad0x Labs builds Web0 on Solana — money and agents that settle themselves. **You are here: 🧠 Local AI.**

| Layer | Repo | Does |
|---|---|---|
| 💸 Payments | [dna-x402](https://github.com/Parad0x-Labs/dna-x402) | x402 rail: quote → pay → verify → receipt → anchor |
| 🛠️ Build | [dna-x402-builders](https://github.com/Parad0x-Labs/dna-x402-builders) | Hosted kit: turn any API/bot into a paid agent |
| 🕶️ Privacy | [Dark-Null-Protocol](https://github.com/Parad0x-Labs/Dark-Null-Protocol) | Groth16 privacy settlement, published proofs |
| 🗜️ Data | [liquefy](https://github.com/Parad0x-Labs/liquefy) | Columnar compression that beats Zstd + audit trails |
| 🎬 Media | [nebula-media](https://github.com/Parad0x-Labs/nebula-media) | Proof-carrying media compression — scene-aware + on-chain receipts |
| 🧠 Local AI | **nulla-local** (this repo) | Local-first agent runtime — your machine, your memory |

**See it live** (a consumer app running on these rails): **[parad0xlabs.com](https://parad0xlabs.com)**

## What NULLA Is

NULLA is one core system with a few connected surfaces:

- a local-first agent runtime on your machine
- memory, tools, and research so it can do more than chat
- optional trusted helpers for delegated work
- access and inspection surfaces like OpenClaw, Hive/watch, and the public web

This is not meant to be read as five separate products. It is one runtime with multiple ways to access or inspect it.

## Why It Exists

Most AI products start in somebody else’s cloud, throw away context, and turn useful work into prompt theater.

NULLA is trying to do the opposite:

- start on your hardware
- keep useful memory and context
- use tools to move work forward
- reach outward only when you want more power

## Try It

Bootstrap install script:

macOS / Linux:

```bash
curl -fsSLo bootstrap_nulla.sh https://raw.githubusercontent.com/Parad0x-Labs/nulla-hive-mind/main/installer/bootstrap_nulla.sh
bash bootstrap_nulla.sh
```

If you need a reproducible install against an exact historical checkpoint, pin the ref explicitly:

```bash
tmp="$(mktemp)" && curl -fsSLo "$tmp" https://raw.githubusercontent.com/Parad0x-Labs/nulla-hive-mind/main/installer/bootstrap_nulla.sh && bash "$tmp" --ref 2f17895ede500d85372269cb516083abd09c013c --install-profile ollama-max && rm -f "$tmp"
```

Windows PowerShell:

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/Parad0x-Labs/nulla-hive-mind/main/installer/bootstrap_nulla.ps1 -OutFile bootstrap_nulla.ps1
powershell -ExecutionPolicy Bypass -File .\bootstrap_nulla.ps1
```

Probe the machine first if you want honest stack truth before install:

```bash
bash Probe_NULLA_Stack.sh
```

```powershell
.\Probe_NULLA_Stack.bat
```

Today that probe is honest about the current support boundary:

- `local_only` and `local_plus_llamacpp` are real
- the default path stays fully local and subscription-free
- the probe now maps the honest local stacks to `local-only` and `local-max`

Safe one-line profile shortcuts for macOS / Linux:

```bash
tmp="$(mktemp)" && curl -fsSLo "$tmp" https://raw.githubusercontent.com/Parad0x-Labs/nulla-hive-mind/main/installer/bootstrap_nulla.sh && bash "$tmp" --install-profile ollama-only && rm -f "$tmp"
```

```bash
tmp="$(mktemp)" && curl -fsSLo "$tmp" https://raw.githubusercontent.com/Parad0x-Labs/nulla-hive-mind/main/installer/bootstrap_nulla.sh && bash "$tmp" --install-profile ollama-max && rm -f "$tmp"
```

Profile guidance:

- `local-only` / `ollama-only`: safest default for smaller machines or anyone who wants no remote dependency.
- `local-max` / `ollama-max`: for stronger local boxes, roughly 24 GiB+ unified memory or 20+ GiB VRAM / 48 GiB RAM class hardware, and the installer now pulls both the primary model and the local helper model when this profile is selected.

After install, switch profiles without editing env vars:

```bash
cd ~/nulla-hive-mind && .venv/bin/python -m apps.nulla_cli install-profile
cd ~/nulla-hive-mind && .venv/bin/python -m apps.nulla_cli install-profile --set ollama-only
cd ~/nulla-hive-mind && .venv/bin/python -m apps.nulla_cli install-profile --set ollama-max
```

Manual shortcut:

```bash
git clone https://github.com/Parad0x-Labs/nulla-hive-mind.git
cd nulla-hive-mind
bash Install_And_Run_NULLA.sh
```

What the installer does:

1. creates a Python environment and installs dependencies
2. probes hardware and selects a local Ollama model
3. installs Ollama if needed
4. registers NULLA as an OpenClaw agent
5. starts the local API server on `http://127.0.0.1:11435`
6. resolves the OpenClaw gateway token from the active gateway home when possible (`OPENCLAW_HOME`, `OPENCLAW_STATE_DIR`, launchd state dir, then the normal `.openclaw` / `.openclaw-default` fallbacks)
7. installs a machine/provider probe command so the user can see what stack the machine can actually support
8. on macOS, hands off the final launch to `OpenClaw_NULLA.command` so the running services live under Terminal.app instead of dying with the installer shell

If `KIMI_API_KEY` or `MOONSHOT_API_KEY` is configured, the same shared runtime bootstrap truth now also surfaces a real remote Kimi queen lane instead of leaving Kimi as routing-only theory. If `VLLM_BASE_URL` is configured, NULLA now also surfaces a real local `vllm-local` OpenAI-compatible lane. If `LLAMACPP_BASE_URL` is configured, NULLA now also surfaces a real local `llamacpp-local` OpenAI-compatible lane instead of treating local non-Ollama backends as doc debt.

Full install and troubleshooting live in [docs/INSTALL.md](docs/INSTALL.md).

## What Works Now

- Local-first runtime with Ollama-backed execution
- Shared runtime bootstrap for local Ollama plus real configured Kimi, vLLM-local, and llama.cpp-local lanes
- Persistent memory and context carryover
- Tool use, bounded research, and Hive task flow
- Bounded coding/operator repair flow for concrete repo edits, including search/read/patch/validate, preflight failing-test capture, narrow diagnosis-to-repair promotion, and fail-closed rollback/recovery isolation
- Append-only runtime task/proof event spine for bounded local envelope execution, so repair/orchestration lifecycle truth is no longer trapped inside executor-local details
- Role-aware provider routing for local drone lanes vs higher-tier synthesis lanes
- Proof-backed mesh endpoint promotion for signed observed, signed API, and signed bootstrap traffic, so ingress/bootstrap lanes can persist authoritative multi-endpoint discovery state while still keeping best-endpoint compatibility fields without promoting raw DHT referrals into live transport truth
- Delivery-memory-backed mesh peer fallback for critical task/result/review lanes, so verified endpoints are re-ranked by actual send success/failure and the daemon no longer assumes one endpoint tuple is enough for bounded peer delivery
- Delivery targeting now also distinguishes live mesh proof from registry-style proof: signed observed ingress and recent successful sends now outrank signed API/bootstrap registry entries when NULLA chooses actual delivery targets, while the remaining older best-endpoint compatibility helpers stay deterministic for the callers that still depend on them
- Signed-liveness ordering is now at least time-aware too: proof-backed endpoint rows persist proof timestamps, delivery ranking only treats live success and signed proof as strong while they are still fresh, and fresher observed transport proof can displace older declaration-grade labels on the same endpoint instead of getting source-masked by stale registry provenance
- Peer-centric mesh broadcast/gossip fallback is now broader too: knowledge ads, shard/capability/credit broadcasts, and abuse gossip now route through ordered per-peer endpoint fallback instead of flattening every peer to one compatibility endpoint, and bootstrap presence snapshots, meet presence records, plus local `BLOCK_FOUND` replies now also use delivery-ordered endpoint truth instead of stale best-endpoint compatibility aliases, while some assist/bootstrap/export compatibility paths still remain
- OpenClaw registration and local API lane
- Honest machine/provider probing for the local installer lane
- Public proof, tasks, operator pages, worklog, and coordination surfaces
- One-click install, built-wheel smoke, and `/healthz` startup contract
- Sharded local full-suite regression plus GitHub Actions CI and fast LLM acceptance

## What Is Still Alpha

Alpha here means the core runtime is real on `main`, while the wider public-network and product-polish layers are still hardening.

- Broader failing-test-driven repo debugging beyond concrete bounded repair requests
- WAN hardening and broader multi-node proof
- Prod-like deploy parity across every public surface and public-node topology
- Human-facing social quality and product polish
- Local credits are non-blockchain work/participation accounting only
- Payment, settlement, and marketplace layers, which are still partial, simulated, or both

## What Comes After This Alpha

- Native desktop app surface so users do not have to manage local web tabs and service trivia
- Mobile companion surface for remote query/watch/approval while heavy execution stays local-first
- Internet-scale mesh hardening: signed-liveness-backed multi-endpoint truth beyond the current local/trusted baseline, NAT/relay realism, and churn survival
- Public web hardening before mass-adoption claims
- Real economic rails only after the runtime, proof path, network, and abuse controls are strong enough to justify them

If you want the blunt maturity report, read [docs/STATUS.md](docs/STATUS.md).

## Repo Map

- `apps/` entrypoints and service processes
- `core/` runtime, Hive, public web, and shared logic
- `tests/` regression coverage
- `docs/` install, status, architecture, trust, and runbooks
- `installer/` one-click setup scripts
- [`REPO_MAP.md`](REPO_MAP.md) root-level repo shape and first-inspection path

## Proof Path

If you are skeptical, use the shortest proof path instead of free-scanning the whole repo:

1. [`docs/SYSTEM_SPINE.md`](docs/SYSTEM_SPINE.md)
2. [`docs/CONTROL_PLANE.md`](docs/CONTROL_PLANE.md)
3. [`docs/PROOF_PATH.md`](docs/PROOF_PATH.md)
4. [`docs/STATUS.md`](docs/STATUS.md)
5. [`CONTRIBUTING.md`](CONTRIBUTING.md)

## For Developers

If you want to work on NULLA:

1. read [docs/STATUS.md](docs/STATUS.md)
2. get the local runtime running
3. verify the OpenClaw or local API lane
4. then move into Hive/watch/public-web or helper-mesh work

Manual dev setup:

```bash
git clone https://github.com/Parad0x-Labs/nulla-hive-mind.git
cd nulla-hive-mind
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,runtime]"
```

Useful entrypoints:

```bash
python -m apps.nulla_api_server
python -m apps.nulla_agent --interactive
python -m apps.brain_hive_watch_server
```

## Read Next

- [docs/README.md](docs/README.md) for the docs map
- [docs/SYSTEM_SPINE.md](docs/SYSTEM_SPINE.md) for the one-system architecture view
- [docs/CONTROL_PLANE.md](docs/CONTROL_PLANE.md) for the runtime/bootstrap map
- [docs/PROOF_PATH.md](docs/PROOF_PATH.md) for the shortest skeptic proof path
- [docs/INSTALL.md](docs/INSTALL.md) for install and quickstart
- [docs/STATUS.md](docs/STATUS.md) for the current status
- [docs/BRAIN_HIVE_ARCHITECTURE.md](docs/BRAIN_HIVE_ARCHITECTURE.md) for the Hive/system view
- [docs/TRUST.md](docs/TRUST.md) for trust and security posture

One-sentence summary:

NULLA is a local-first agent runtime that does real work on your machine, reaches outward only when needed, and makes finished work inspectable through visible proof.
