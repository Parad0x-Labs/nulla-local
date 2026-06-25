# PROOF PASS REPORT

## Purpose

This document is the runtime proof gate for Decentralized NULLA.

It is not a vision document. It is a validation document.

A proof item only counts if it is:

- reproducible,
- traceable,
- tied to real artifacts,
- and judged against an explicit expected outcome.

This report separates:

- proof already demonstrated locally,
- proof partially demonstrated at component level,
- and proof still waiting for live multi-node validation.

## Current Proof Summary

| Proof Item | Status | Meaning |
|---|---|---|
| Cross-machine LAN full-cycle | INSTRUMENTED | The architecture supports it, but this report does not yet contain a completed evidence record. |
| Kill-helper-mid-task recovery | INSTRUMENTED | Timeout and state transitions exist, but there is not yet a full live failure evidence record here. |
| Conflicting helper outputs | PASS | Local verdict/conflict handling is covered by automated test evidence. |
| Replay rejection | PASS | Local ledger receipt replay is covered and protocol/signed-write nonce replay is now covered by concurrent acceptance/rejection regression tests. |
| Packet corruption / chunk integrity | PARTIAL | Chunk integrity and loss detection are covered locally; live retry behavior in the full running mesh is not yet recorded here. |
| Sandbox boundary enforcement | PASS | Local execution boundary and network/workspace restrictions are covered by automated tests. |
| Fraud trigger behavior | PASS | Local fraud/self-farm detection is covered by automated tests. |
| Event chain integrity / tamper detection | INSTRUMENTED | Event chaining exists, but this report does not yet include a deliberate tamper proof record. |
| Cross-machine knowledge advertisement | INSTRUMENTED | Presence and knowledge metadata are integrated, but live holder propagation is not yet evidenced here. |
| Replica acquisition and re-advertisement | INSTRUMENTED | The holder and fetch model exists, but a live two-node replication proof is not yet recorded here. |
| Presence lease expiry / offline prune | INSTRUMENTED | Lease expiry logic exists, but this report does not yet show an offline node being removed from the live map. |
| Knowledge version update propagation | INSTRUMENTED | Versioned holder metadata exists, but live supersession behavior is not yet evidenced here. |
| Knowledge resync after reconnect | INSTRUMENTED | The index is designed to recover, but a reconnect and resync trace is not yet recorded here. |

## Environment

The system under evaluation is the current local-first Decentralized NULLA codebase with:

- current local/full-suite baseline (2026-03-20): `976 passed, 11 skipped, 11 xfailed, 18 xpassed, 1 warning`,
- standalone local mode preserved,
- LAN mesh path preserved,
- optional sidecars still optional,
- verdict-based review logic present,
- task lifecycle tracing present,
- content-addressed storage present,
- append-only event logging present,
- and simulated economics explicitly separated from trustless claims.

This report intentionally avoids claiming hostile-public-internet readiness or trustless economic finality.

## Proof Item 1: Cross-Machine LAN Full-Cycle

### Why It Matters

This is the clearest proof that the mesh is real, not just local orchestration with distributed language around it.

### Expected Behavior

A parent task should move through decomposition, offer publication, helper claim, assignment, result return, review, and finalization across at least two live machines on the same local network.

### Actual Behavior

Not yet recorded in this report.

### Artifacts Collected

- Architecture support exists in the current daemon, assist router, task decomposition, review, and reassembly path.
- Task trace and state primitives now exist to support a future evidence record.

### Verdict

INSTRUMENTED

### Follow-Up Action

Capture one full trace-backed LAN run and record:

- parent task trace identifier,
- participating helper peer identifier,
- task state transitions,
- review outcome,
- and final response evidence.

## Proof Item 2: Kill-Helper-Mid-Task Recovery

### Why It Matters

A distributed system is not credible if it wedges on worker loss.

### Expected Behavior

If a helper disappears after assignment, the task should transition cleanly through timeout handling and remain recoverable rather than becoming permanently stuck.

### Actual Behavior

Timeout-oriented state behavior is present at component level, but a live helper-death proof record is not yet captured here.

### Artifacts Collected

- Task lifecycle state machine exists.
- Timeout states are supported.
- Local timeout transition coverage exists in automated tests.

### Verdict

INSTRUMENTED

### Follow-Up Action

Record a live failure run showing:

- assignment state,
- helper disappearance,
- timeout transition,
- parent recovery behavior,
- and whether reassignment or graceful failure occurred.

## Proof Item 3: Conflicting Helper Outputs

### Why It Matters

This validates that the system no longer pretends agreement equals truth and can handle disagreement accurately.

### Expected Behavior

Meaningfully conflicting helper responses should be classified as conflict or dispute rather than silently collapsed into a false consensus.

### Actual Behavior

The verdict engine classifies hard disagreement as disputed and soft disagreement as accepted-with-conflict. Automated local evidence confirms this behavior at component level.

### Artifacts Collected

- Automated test coverage exists for conflicting-result verdict handling.
- Verdict engine, evidence scorer, and conflict classifier are now present in the runtime.

### Verdict

PASS

### Follow-Up Action

Add one live two-helper trace showing an actual runtime dispute path, not only a local unit-level proof.

## Proof Item 4: Replay Rejection

### Why It Matters

Replays can poison accounting, scoring, and future economic claims.

### Expected Behavior

Duplicate receipts or duplicate replayed inputs should not be applied twice.

### Actual Behavior

The local credit ledger rejects duplicate receipt application. This is proven. Protocol-level envelope replay and signed-write replay are now also covered with concurrent-race regression tests that enforce single acceptance and deterministic replay rejection.

### Artifacts Collected

- Automated test coverage exists for duplicate receipt rejection.
- Automated concurrent-race coverage now exists for:
- protocol `decode_and_validate` nonce acceptance/rejection.
- signed HTTP write envelope nonce acceptance/rejection.

### Verdict

PASS

### Follow-Up Action

Keep replay regression tests in the blocking gate for every merge.

## Proof Item 5: Packet Corruption / Chunk Integrity

### Why It Matters

Large-payload transport is only credible if corruption and loss are detected cleanly.

### Expected Behavior

Missing or corrupted chunks should not produce accepted output. Reassembly should fail clearly when integrity is broken.

### Actual Behavior

Chunk integrity and missing-chunk detection are proven locally through automated tests. Full live retry-and-recovery behavior in the running mesh is not yet recorded in this report.

### Artifacts Collected

- Automated test coverage exists for out-of-order reassembly and packet loss detection.
- Chunk protocol, transfer manager, and content-addressed storage now exist.

### Verdict

PARTIAL

### Follow-Up Action

Capture a live transfer proof showing:

- interrupted delivery,
- retry or recovery behavior,
- integrity preservation,
- and refusal to accept corrupted payloads.

## Proof Item 6: Sandbox Boundary Enforcement

### Why It Matters

Execution safety is one of the core trust boundaries in the system.

### Expected Behavior

Blocked network egress and blocked workspace escape attempts should fail cleanly instead of executing.

### Actual Behavior

The hardened local job runner rejects disallowed network usage and rejects execution outside the allowed workspace policy. This is proven at automated local test level.

### Artifacts Collected

- Automated test coverage exists for network command blocking.
- Automated test coverage exists for workspace escape blocking.
- Execution policy, job runner, and network guard are now present.

### Verdict

PASS

### Follow-Up Action

Expand proof scope later to include heavier malicious-input execution attempts under the same policy model.

## Proof Item 7: Fraud Trigger Behavior

### Why It Matters

The anti-abuse story only matters if suspicious patterns actually trigger defense logic.

### Expected Behavior

Obvious self-farm behavior should be rejected and produce the expected anti-abuse reasoning.

### Actual Behavior

Self-farm rejection is proven locally through automated test evidence.

### Artifacts Collected

- Automated test coverage exists for self-farm detection.
- Fraud assessment and anti-abuse signal machinery exist in the runtime.

### Verdict

PASS

### Follow-Up Action

Add additional recorded proofs for:

- pair farming,
- ring behavior,
- and duplicate-result abuse paths.

## Proof Item 8: Event Chain Integrity / Tamper Detection

### Why It Matters

Audit claims are weak unless tampering is detectable.

### Expected Behavior

The event hash chain should verify cleanly when intact and fail verification when deliberately altered.

### Actual Behavior

The event log and event hash chain exist, but this report does not yet contain a deliberate tamper proof record.

### Artifacts Collected

- Append-only event log exists.
- Event hash chain exists.
- Trace-aware audit logging is now integrated.

### Verdict

INSTRUMENTED

### Follow-Up Action

Run and record an explicit tamper proof:

- clean verification,
- forced mutation,
- failed verification result,
- and preserved evidence of the failure.

## What This Report Proves Today

Today this report supports the following accurate claim:

Decentralized NULLA has real local proof for verdict handling, sandbox boundary enforcement, fraud trigger activation, and local replay-safe ledger behavior, with partial proof for chunk integrity and explicit readiness for live LAN and tamper-evidence validation.

That is already enough to say the project has moved beyond architecture-only claims and into runtime validation.

## What This Report Does Not Yet Prove

This report does not yet prove:

- a completed cross-machine LAN trace-backed full-cycle proof,
- live helper-death recovery evidence,
- live transport retry evidence inside the running mesh,
- full protocol replay proof record,
- a completed event-chain tamper demonstration,
- live knowledge advertisement convergence across machines,
- live replica growth after fetch and re-advertisement,
- live lease expiry cleanup for offline holders,
- live shard-version supersession handling,
- or live reconnect and resync recovery for the shared knowledge index.

Those are the next runtime proof targets.

## Release Gate Interpretation

The system can now be described accurately as:

- a real standalone local intelligence,
- a real LAN-capable distributed agent platform,
- a partially hardened pre-production orchestration mesh,
- and a system with simulated economics rather than proven trustless settlement.

It should still not be described as:

- a trustless public compute economy,
- a hostile-public-internet-ready coordination mesh,
- or a production-grade decentralized settlement network.

## Next Evidence Additions

The next strongest additions to this report are:

1. One fully recorded cross-machine LAN parent/helper trace.
2. One live helper-death and timeout-recovery trace.
3. One protocol replay rejection trace.
4. One deliberate event-chain tamper-and-detect proof.
5. One full knowledge-presence proof pack covering advertise, fetch, replicate, expire, and resync.

## Knowledge-Presence Proof Pack

Before the meet-and-greet server is treated as runtime-ready, this report should also contain five live knowledge-index proofs:

1. Cross-machine knowledge advertisement.
2. Replica acquisition and re-advertisement.
3. Presence lease expiry and offline pruning.
4. Knowledge version update propagation.
5. Reconnect and resync recovery.

Those proofs matter because the meet-and-greet layer will depend on the shared knowledge index being correct under churn, not just present in local code.
