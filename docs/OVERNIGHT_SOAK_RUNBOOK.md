# Overnight Soak Runbook

## Purpose

This document is the operator runbook for a real overnight readiness pass on Decentralized NULLA.

It is not meant to prove hostile public-internet readiness.

It is meant to answer a narrower and more precise question:

Can NULLA behave like a stable local-first agent and trusted multi-machine runtime over one long uninterrupted session without wedging, silently drifting, poisoning memory, or hiding failures?

## Supporting Runtime Reports

The current repo now includes these operator-facing helpers:

- `docs/CLEAN_RUNTIME_SOAK_PREP.md`
- `ops/overnight_readiness_report.py`
- `ops/morning_after_audit_report.py`
- `ops/health_report.py`
- `ops/mobile_channel_preflight_report.py`
- `docs/LAN_PROOF_CHECKLIST.md`
- `docs/MOBILE_CHANNEL_TEST_CHECKLIST.md`

## Scope Of Tonight's Soak

Tonight's soak should judge the system against this bar:

- standalone local runtime stability,
- trusted LAN or small multi-node alpha behavior,
- knowledge-presence coherence,
- meet-and-greet scaffold sanity,
- tiered-context discipline,
- model execution fail-safe behavior,
- and enough observability to explain what happened by morning.

Do not use this soak to claim:

- public hostile-world readiness,
- trustless payments,
- final WAN routing proof,
- or public production hosting.

## Go / No-Go Gate

Use the overnight readiness report as the first gate.

Before that, use the clean-runtime prep note if the current runtime contains mixed historical state.

### GO

Accept `GO` or `GO_WITH_WARNINGS` only if:

- the warnings are understood,
- no blocking failures remain,
- and the warnings are not known runtime crash paths.

### NO-GO

Stop if:

- the report returns `NO_GO`,
- the event chain is dirty on clean untampered data,
- imports fail,
- required schema tables are missing,
- the meet service does not preserve safe bind posture,
- or the runtime directories are not writable.

## Pre-Soak Freeze

Before the overnight run:

- freeze code,
- freeze configs,
- freeze dependency changes,
- and do not patch mid-run.

If something breaks during the soak, record it first.

Then fix it after the run.

## Runtime Baseline Rule

Prefer a fresh runtime home for the soak.

If the overnight readiness report warns that the runtime baseline already contains operational history, treat that as a sign to use a clean `NULLA_HOME` before starting the real soak.

## Environment Capture

Record before the soak:

- machine names,
- platforms,
- Python version,
- branch and commit if available,
- active provider posture,
- safety mode,
- whether meet-node mode is enabled,
- whether local model provider is enabled,
- whether mobile or channel surfaces are in scope,
- and which machines act as primary brain versus meet nodes.

## Baseline Checks

These should all be true before the soak begins:

- local standalone boot is clean,
- LAN peer discovery has succeeded at least once,
- migrations are current,
- logs or runtime dirs are writable,
- event chain is clean,
- replay cache is not obviously broken,
- provider warnings are understood,
- and knowledge presence can be emitted locally.

## Workload Shape

Do not leave the system purely idle.

Use a structured mixed workload:

- light standalone tasks on intervals,
- periodic LAN parent-helper tasks,
- repeated presence refresh,
- repeated knowledge advert refresh,
- at least a few shard fetch or replicate cycles,
- a few messy-input tasks,
- a few model-backed tasks if providers are enabled,
- a few archive-sensitive or history-sensitive tasks,
- and meet snapshot or delta pulls if meet mode is enabled.

The point is stability under real-looking usage, not synthetic overload only.

## Failure Injection

Inject a small number of controlled failures during the run:

1. kill a helper mid-task,
2. briefly interrupt one machine or network path,
3. stop the local provider once if enabled,
4. stop one meet node once if running multiple meet nodes.

Each of these should leave behind:

- legal task state,
- understandable logs,
- and recoverable runtime posture.

## Things To Watch During The Run

Pay attention to:

- task states that never finish,
- leases that never expire,
- holder maps that diverge,
- candidate knowledge leaking into canonical memory,
- provider failure that hangs instead of failing fast,
- repeated cold-context opens with no justification,
- runaway log growth,
- runaway temp file growth,
- and meet sync that duplicates or corrupts state.

## Morning-After Audit

The morning-after audit should cover:

- process survival,
- unresolved task scan,
- knowledge state sanity,
- event chain verification,
- candidate-versus-canonical boundary sanity,
- and context-budget usage sanity.

Use the morning-after audit report as the first pass, then inspect deeper if it warns or fails.

## What Counts As A Pass

Minimum acceptable result:

- processes survive,
- tasks complete,
- induced failures fail safely,
- event chain stays valid,
- no illegal lifecycle accumulation appears,
- knowledge presence stays coherent,
- and logs are good enough to explain what happened.

Strong result:

- cross-machine replication works,
- lease expiry works,
- version propagation works,
- reconnect resync works,
- local model failover works,
- and context budgets remain disciplined across the soak.

## What Should Stop Tomorrow's Broader Testing

Do not expand testing if the soak shows any of these:

- repeated stuck tasks,
- broken lease expiry,
- broken holder convergence,
- candidate knowledge leaking into canonical memory,
- provider hangs on failure,
- meet snapshot or delta corruption,
- replay protection causing duplicate effects,
- or a broken event chain on untampered data.

## Bottom Line

The overnight run is the right test now because the remaining big questions are runtime-behavior questions, not architecture questions.

The goal is not to prove that NULLA is finished.

The goal is to prove that one long uninterrupted session leaves behind evidence that the runtime story is broadly true.
