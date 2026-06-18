# Install And Provider Execution Plan

Updated 2026-03-26 after live local-machine audit on Apple M4 / 24 GiB RAM.

This is not a roadmap fantasy. It is the exact execution plan for making NULLA install, provider setup, and first-run truth stop lying.

## Current Truth

- Local Ollama install path is real.
- Clean one-line reinstall on this Mac is now real for the local Ollama + OpenClaw lane.
- The repo has a real locked local acceptance gate in [`docs/LOCAL_ACCEPTANCE.md`](/home/nulla-user/nulla/docs/LOCAL_ACCEPTANCE.md).
- The repo does **not** yet have a first-class Kimi install/bootstrap lane.
- The repo does **not** yet have a first-class Tether/QVAC lane.
- The runtime does have a generic OpenAI-compatible adapter, so Kimi should land as a named profile on that adapter instead of as a parallel architecture.

## Machine Truth From This Audit

- CPU: Apple M4
- RAM: 24 GiB unified memory
- Cores: 10
- Installed Ollama models:
  - `qwen2.5:14b`
  - `qwen2.5:7b`
- Remote provider env on this machine:
  - Kimi: not configured
  - Tether: not configured
  - QVAC: not configured

## Product Goal

One command should:

1. inspect the machine honestly
2. detect installed local runtimes and models
3. detect which remote provider credentials are actually present
4. recommend a supported provider stack
5. let the user choose a supported stack
6. install or pull the missing local pieces
7. write the runtime/provider config
8. prove the resulting stack through health checks and acceptance

If any of those steps are fake, the install story is fake.

## What Must Exist

### 1. Machine / Provider Probe Command

Create a first-class probe command that works on macOS, Linux, and Windows shells.

Minimum output:

- machine hardware summary
- recommended local Ollama tier
- installed Ollama models
- OpenClaw presence
- Liquefy presence
- remote provider credential presence (without printing secrets)
- recommended provider stack
- supported provider stacks for this machine
- missing requirements for each unavailable stack

This command must support:

- human-readable output
- JSON output for the installer

### 2. Provider Stack Contract

Define explicit supported stacks:

- `local_only`
- `local_dual_ollama`
- `local_plus_remote_openai_compatible`
- `local_plus_kimi`

Do not add `tether` or `qvac` as supported stacks until there is real adapter/runtime truth for them.

### 3. Installer Selection Flow

Installer must:

- run the probe command first
- print the recommendation
- support explicit non-interactive selection flags
- support interactive selection when the user wants it
- pull missing Ollama models automatically
- refuse unsupported stacks honestly

### 4. Runtime Provider Registration

Installer/bootstrap must register manifests for the selected stack.

That means:

- local Ollama primary manifest
- optional local helper/verifier manifest
- optional generic OpenAI-compatible remote manifest
- optional Kimi profile built on the generic OpenAI-compatible adapter

### 5. Acceptance And Proof

After install, the selected stack must pass:

- health checks
- provider health checks
- OpenClaw connectivity
- locked local acceptance for the local lane
- extra stack-specific checks for any selected remote lane

## Exact Slice Order

### Slice 1

Build `machine/provider probe` command and tests.

Status: done locally on 2026-03-26.

Evidence:

- `Probe_NULLA_Stack.sh`
- `Probe_NULLA_Stack.bat`
- `installer/provider_probe.py`
- probe regression tests are green
- live probe on the Apple M4 / 24 GiB machine reports:
  - `local_only`: ready
  - `local_dual_ollama`: ready and recommended
  - `local_plus_kimi`: real when `KIMI_API_KEY` is configured; still optional, not the default local-first lane
  - `local_plus_tether`: not implemented yet
  - `local_plus_qvac`: not implemented yet

### Slice 2

Build provider stack selection contract and installer flags.

### Slice 3

Register selected provider manifests into runtime bootstrap.

### Slice 4

Re-run clean uninstall -> one-line install -> OpenClaw -> local acceptance.

Status: done locally on 2026-03-26 against the current checked-out commit.

Evidence:

- previous NULLA install, runtime home, and isolated OpenClaw home were trashed first
- bootstrap replay completed into a fresh `~/nulla-hive-mind`
- live endpoints returned `200`:
  - `http://127.0.0.1:11435/healthz`
  - `http://127.0.0.1:11435/trace`
  - `http://127.0.0.1:18789/`
- direct runtime checks succeeded after reinstall:
  - Desktop folder listing
  - Hive task truth
- locked local acceptance under [`docs/LOCAL_ACCEPTANCE.md`](/home/nulla-user/nulla/docs/LOCAL_ACCEPTANCE.md) finished `GREEN`

### Slice 5

Add Kimi-on-openai-compatible profile, then prove it with health checks and a focused acceptance pass.

### Slice 6

Only after that: decide whether Tether/QVAC deserve real first-class work or should stay out of the supported stack story.

## Test Rule

Every slice must be tested cumulatively:

- changed-surface tests
- previously working installer/bootstrap/OpenClaw tests
- provider/manifest/runtime tests
- locked local acceptance after the install path is touched materially

## Current Blocking Defects

- installer is still too Ollama-only in its first-run truth
- doctor does not own provider-stack truth yet
- Kimi is not a first-class supported install/runtime profile
- Tether/QVAC are not real supported providers yet
- there is no single machine/provider probe command the user can trust before install

## Honest Beta Bar For This Lane

This lane is ready when a new machine can:

1. run one command
2. see an honest machine and provider report
3. choose a supported stack
4. let NULLA install and configure it
5. open OpenClaw successfully
6. pass the locked acceptance bar without manual rescue

Until then, the install/provider story is still alpha.
