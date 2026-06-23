# TDL (Technical Debt + Open Work)

## Scope

This file tracks real open risks that still matter for closed production-style testing and for the later public-network hardening path.

The rule is simple:

- if it is still partial, simulated, or not wired end-to-end, it belongs here.
- if it is done, remove it or mark it completed with evidence.

## Priority Legend

- `P0`: must close before broader external closed testing.
- `P1`: can proceed in closed testing with guardrails, but must close before public exposure.
- `P2`: important hardening and scale improvements.

## Fresh Open Note (2026-03-08)

- `P1` OpenClaw/gateway chat still leaks internal workflow/system chatter into the main user conversation.
- Current truth: workflow summaries and follow-up/status text can still show up on the primary chat surface in ways that feel like system noise instead of useful assistant replies.
- Gap: these messages burn tokens, vertical space, and user attention while adding little or no value for normal chat.
- Done when:
- default user-facing chat hides internal workflow/system messages unless the user explicitly asks for them or enables debug-style visibility.
- follow-up/research notices are rewritten as short human-facing prompts or suppressed entirely when they are not actionable.
- OpenClaw/default chat UX stops looking like a log console for routine conversation.

## Shipped (2026-06-23) — was queued, now done with on-chain evidence

- `null://` dial — resolve a `.null` name, reach the named agent's x402 endpoint, pay it (gated), return the result. Off by default; SSRF-guarded; pays through the canonical x402 engine.
  - Evidence: mainnet settlement [`37o7An4A…`](https://explorer.solana.com/tx/37o7An4AUJDQqyZK2LvdGARHFpEQK3ZJiZSJwwADoeqeMHYUtSv9uddMzCcueBLU5p1S3fWDBb2FoWAMBCMn7F5a); self-describing memo'd mainnet [`4JBYfRff…`](https://explorer.solana.com/tx/4JBYfRffsA3xh6gWDTi4ybuDxrKSf8U9Vvn8xipwxmESPJt8T26jsWnUYMR1SLJ2YCUyPzTw9gALUHurWExT3ets); full devnet round-trip [`WR2syULn…`](https://explorer.solana.com/tx/WR2syULnAAyfnbBjjvwXrAuexiGnDH88KLyKe5c2PogRidoHE7xs7MLWU1frs1Bzn5WAXCUZ6jvKdnhiAxs66hz?cluster=devnet) (`verified: true`). See `proofs/mainnet/2026-06-23/` + `proofs/devnet/2026-06-23/ROUNDTRIP.md`.
- `.nullpass` — portable, offline-verifiable work credential (recompute proof + payment hashes, verify ed25519 signature, optional on-chain settlement confirm). 14-case forgery matrix green. Evidence: `proofs/devnet/2026-06-23/NULLPASS.md`.
- **WorkProof-bound mesh reward** — the mesh helper reward now requires a verified commit/reveal WorkProof (`accept_bid` issues a secret per-task challenge; `commit_result` enforces commit-before-reveal; `submit_result` rewards only on a verified proof). The old self-computable `sha256(task_id+result+worker)` earns nothing. 9-case adversarial matrix: `tests/test_mesh_workproof_reward.py`.

## Next Feature Explorations (candidate builds — all local-buildable)

Each is its own branch, same discipline: grounded build → adversarial review → tested green → push to `main` on the user's word.

1. `P2` Entity-graph memory — make `MemoryNode.linked_node_ids` live with 1-hop recall expansion.
- Current truth: links are stored but recall does not traverse them.
- Done when: recall optionally expands one hop along `linked_node_ids` (ranked), with a held-out recall delta measured.

2. `P1` Bind WorkProof to reward paths — **SHIPPED 2026-06-23 for the mesh reward (see Shipped above).** Remaining: the local `task_completion` self-award (`core/web/api/service.py`) and presence-credit path are not yet proof-gated — lower risk (local self-award), revisit if mesh-relayed.
- Done when: every credit-granting path that rewards remote work requires a verified WorkProof; a forged or absent proof is rejected (adversarial test). Mesh path done; local self-award paths remain.

3. `P2` Two-NULLA local handshake — compose `build_task_capsule` + `verify_task_capsule` into a signed-capsule-in → signed-receipt-out exchange between two local nodes.
- Done when: node A sends a signed capsule, node B verifies and returns a signed receipt, A verifies it; tamper is rejected.

4. `P2` Expose the full web0 page builder — wire the remaining `web0_*` builder intents (gated sections, encrypt, publish) beyond the current subset.
- Done when: the full builder intent set is callable and tested, CSP-safe.

5. `P2` Knowledge-marketplace buy side — the supply side auto-lists; add search plus a credit-burning purchase.
- Done when: search returns listings; a purchase burns credits and unlocks the item; insufficient-credit and double-spend are rejected.

6. `P1` Embedder unification + recall eval — fix the 768-vs-384-dim mismatch that silently zeroes recall; ship a held-out recall benchmark.
- Done when: one embedder dimension across the recall path (no silent zero), with a committed recall benchmark and baseline.

## Open Items

1. `P0` Multi-helper speculative reasoning is still partial.
- Current truth: helper execution now supports model-backed reasoning with deterministic fallback.
- Gap: helper fanout still does not run structured speculative multi-path reasoning (same capsule across multiple model backends with evaluator scoring).
- Done when:
- helper orchestration can request N model-backed helper paths for the same capsule.
- evaluator scoring and verdict weighting are persisted as first-class review signals.

2. `P1` Observability remains baseline only.
- Current truth: structured logging bootstrap and meet `/metrics` counters now exist.
- Gap: no distributed tracing across task lifecycle, no alert policy, and no node-level metrics exporter outside meet HTTP surface.
- Done when:
- trace IDs are stitched into end-to-end spans with per-stage timing.
- alertable SLO metrics exist for latency, error rate, peer churn, and stalled tasks.

3. `P1` UDP control-plane remains active by design.
- Current truth: the mesh now has stream-first oversized transfer plus UDP fragmentation/reassembly fallback, but control-plane signaling still uses UDP.
- Current mitigation: split datagram/message limits, stream transfer threshold policy, and receiver reassembly are now active.
- Gap: delivery guarantees for control-plane packets are still best-effort UDP.
- Done when:
- critical control signals have explicit ACK/retry semantics with bounded queueing.

4. `P1` Stream transport needs broader runtime proof under churn.
- Current truth: stream-first transfer for oversized payloads is now wired in send path with UDP fragmentation fallback.
- Gap: live multi-node retry/reassembly and failover evidence is still limited to local automated coverage.
- Done when:
- proof reports show sustained multi-node stream/fragment recovery under packet loss and helper churn.

5. `P1` Task-offer and market broadcast pressure needs stronger scaling controls.
- Current truth: task offers are helper-targeted (not blind subnet broadcast).
- Gap: high fanout and repeated offer/credit broadcasts can still create chatter under churn.
- Done when:
- adaptive fanout caps, jittered rebroadcast suppression, and backpressure metrics are enabled.
- swarm chaos tests show stable behavior at higher node counts.

6. `P1` Consensus quality remains partial for subjective/creative reasoning.
- Current truth: verdict engine supports conflict states and semantic fallback hooks.
- Gap: semantic judge is optional/offline and fallback still relies on lightweight token overlap in many paths.
- Done when:
- semantic/contract-aware comparison is enabled by default where appropriate.
- disputed/low-evidence paths consistently trigger verification workflows.

7. `P1` Capability advertising is still coarse for heterogeneous hardware.
- Current truth: capability ads include `compute_class`, `supported_models`, and `capacity`.
- Gap: no rich runtime metrics like VRAM, quant level, tokens/sec, context limits.
- Done when:
- benchmark-driven capability ads include richer measured fields.
- scheduler selects helpers using those richer constraints.

8. `P1` WAN stack remains experimental.
- Current truth: STUN, NAT probe, hole-punch, relay fallback, and proof tooling exist.
- Gap: no full hardened public-routing behavior and no proven multi-hop DHT routing.
- Done when:
- WAN proof pack runs against real distributed nodes with partition/churn cases.
- convergence and reachability pass criteria are met across regions.

9. `P1` DHT implementation is still minimal.
- Current truth: `network/dht.py` now uses bounded XOR-distance bucket partitions with stale-node pruning.
- Gap: no full Kademlia lookup/refresh walk semantics, liveness probes, or hardened multi-hop forwarding.
- Done when:
- DHT module reaches production-shaped lookup/refresh semantics and passes stress simulation.

10. `P1` Contribution-backed credit control is still too weak for the target world-AI commons.
- Current truth: credits can be awarded, burned, transferred, and starter balances exist; insufficient balance can fall back to free/background tier; paid top-up remains disabled and settlement is still simulated.
- Gap: credits are not yet the real scheduling and budget control lane. Dispatch is still too advisory, contribution minting from accepted useful work is not yet enforced tightly enough, abuse/slashing cost is incomplete, and paid top-up must stay off until the rails are honest.
- Done when:
- credits enforce real per-user, per-device, and per-task budgets plus priority control for swarm work.
- minting is tied to accepted useful work, trust, and review quality instead of noise.
- optional paid top-up, if ever enabled later, sits on top of a working contribution-first budget system instead of defining it.

11. `P2` Large-scale content durability is partial.
- Current truth: local CAS + dedup exists.
- Gap: no erasure coding/distributed durability strategy for large content at scale.
- Done when:
- content durability policy (replica count/erasure mode) is implemented and tested in multi-node failure scenarios.

12. `P2` Adversarial simulation depth needs expansion.
- Current truth: deterministic adversarial/convergence proof tooling exists.
- Gap: not yet a full 10–50 adversarial-node simulator with churn, Sybil patterns, and loss profiles.
- Done when:
- simulator suite runs reproducible multi-node hostile scenarios with pass/fail artifacts.

13. `P2` Backend surface for heterogeneous inference is incomplete.
- Current truth: model execution layer and adapters exist; local-first policy is in place.
- Gap: no explicit first-class vLLM/llama.cpp backend path in production test matrix.
- Done when:
- backend adapters and capability descriptors cover these runtimes with health/failover tests.

14. `P1` Relay strategy must become TURN-grade for symmetric NAT cases.
- Current truth: STUN, NAT classification, hole-punch attempts, and relay-mode selection logic exist.
- Gap: no explicit TURN-grade relay service path is wired and proven for hard NAT environments.
- Done when:
- relay infrastructure is deployable as a real fallback path for symmetric NAT users.
- WAN tests prove connectivity for nodes where direct hole punching fails.

15. `P2` Wire format remains JSON-heavy for mesh messages.
- Current truth: strict JSON envelopes and schema validation are in place.
- Gap: no compact binary wire encoding (MessagePack/Protobuf) for bandwidth-sensitive mesh traffic.
- Done when:
- protocol supports optional compact encoding with compatibility negotiation.
- payload size and throughput improvements are measured in proof reports.

16. `P1` Anti-abuse blacklist propagation needs explicit gossip semantics.
- Current truth: anti-abuse signals, trust slashing, local quarantine, and a baseline REPORT_ABUSE gossip path with dedupe, TTL, and fanout now exist.
- Gap: no end-to-end signed high-severity abuse convergence policy across wider meet or cross-region federation.
- Done when:
- critical abuse events replicate across meet or mesh layers with TTL and signature checks.
- peers converge on high-severity offender state within defined time bounds.

17. `P1` Sybil-cost policy exists but is still too light for hostile settings.
- Current truth: genesis PoW nonce is generated and validated on capability advertisements with policy-driven minimum difficulty.
- Gap: fixed low difficulty and no adaptive identity cost policy by trust tier or abuse pressure.
- Done when:
- identity-cost policy is adaptive (difficulty or stake/deposit tiering) and measurable.
- repeat identity churn attacks are penalized by policy, not only by trust reset.

18. `CLOSED 2026-03-07` Release and legal distribution pack.
- Current truth at the 2026-03-07 checkpoint: the closed-test release manifest contained real versions, artifact paths, and SHA256 hashes, top-level license texts were committed, and release readiness returned `READY`.
- Evidence:
- `config/release/update_channel.json` is generated from the latest installer bundle.
- `ops/release_readiness_report.py` reported no warnings at that checkpoint. Current branch health must still be verified against the live CI/build lane.

19. `CLOSED 2026-03-07` Signing-key repository hygiene.
- Current truth: repo-local signing-key material has been removed from source state, and repo hygiene checks now fail if signing-key artifacts or placeholder distribution inputs reappear.
- Evidence:
- `ops/repo_hygiene_check.py` returns `CLEAN`.
- CI and bundle-build flow now run the hygiene check before proceeding.

20. `P0` Action plane is not yet end-to-end real.
- Current truth: planning, gating, sandbox, and helper scaffolding exist, and multiple bounded local workflows now run end to end: disk inspection, approval-gated temp cleanup, process inspection, service/startup inspection, approval-gated move/archive actions, and approval-gated calendar-event creation with verification and procedure-shard promotion.
- Gap: broader capability validation still fails closed for general execution, helper surfaces are still reasoning-only, and the real action lane is still bounded rather than general-purpose.
- Done when:
- capability-bearing bounded actions can execute through a working gate + sandbox path with audit logs.
- at least one real safe action workflow is exercised by tests and soak evidence instead of returning `advice_only`.

21. `P1` Daemon and API network posture still rely too much on operator discipline.
- Current truth: meet public-bind posture now has runtime guardrails, and mesh encryption plus TLS support exist for closed clusters.
- Gap: daemon and API startup paths still commonly bind to non-loopback interfaces without forcing secure/authenticated posture, so accidental exposure is still easy.
- Done when:
- single-node defaults prefer loopback/private-safe binds.
- non-loopback daemon or API startup requires explicit insecure override or configured auth/TLS/PSK posture.

22. `P1` Local-first privacy posture is not enforceable across all entry paths.
- Current truth: a single `system.local_only_mode` policy switch now disables web fallback, remote-only backend mode, remote model-provider selection, and API-surface remote-fetch allowance.
- Gap: helper surfaces and hard network-egress proof are still incomplete, so "local-only" is stronger now but not yet fully proven across every auxiliary path.
- Done when:
- one policy switch can enforce no-remote-fetch and no-cloud-fallback across CLI, API, channel, and helper surfaces.
- regression tests prove that local-only mode produces no external egress.

23. `P1` Install and bootstrap reproducibility remain weak.
- Current truth: installer bootstrap now auto-detects the recommended local Qwen tier, checks or installs Ollama, attempts OpenClaw configuration, patches NULLA into OpenClaw agent config, and writes an install receipt for support/debugging.
- Gap: first-run behavior still depends on broad runtime installs and live network pulls unless a prepared wheelhouse/runtime payload is bundled, so setup is friendlier but still variable and network-dependent.
- Done when:
- dependency sets are pinned and distributable as a reproducible lockfile or wheelhouse.
- model/runtime bootstrap is explicit and can be completed from prepared artifacts without surprise downloads.

24. `P1` Learning loop is still unproven in live runtime evidence.
- Current truth: memory, shard registration, manifest sync, and knowledge-presence plumbing are implemented.
- Gap: current runtime summary still shows zero local learning shards and zero peer-received learning shards, so there is no proof yet of durable learn-retain-reuse behavior.
- Done when:
- soak and closed-test reports show shards being created, promoted, reused on later tasks, and shared across nodes.
- learning metrics include retrieval hit rate, reuse count, promotion reason, and decay or replacement behavior.

25. `P1` Idle curiosity is still bounded note-taking, not autonomous improvement.
- Current truth: curiosity can queue and execute bounded topic-following passes and store candidate outputs.
- Gap: there is no curriculum, replay, verification, or promotion loop that turns idle exploration into durable capability gains.
- Done when:
- idle curiosity runs on explicit budgets with verifier and promotion stages.
- curiosity outputs demonstrably improve later task quality or retrieval usefulness instead of staying as isolated candidate notes.

26. `P1` Shared swarm memory is metadata-strong but payload and reuse weak.
- Current truth: manifests, holder records, metadata search, payload fetch/validation, and bounded selected-vs-answer-backed reuse accounting are implemented across the mesh.
- Gap: the index layer is ahead of the actual fetch, validation, and reuse path, so collective memory is stronger as a catalog than as a working shared brain.
- Done when:
- helpers and parent tasks can fetch, validate, cache, and cite remote shard payloads end to end.
- cross-node tests prove that retrieved swarm knowledge materially changes downstream reasoning or task completion.

27. `P1` Helper autonomy and self-model remain shallow.
- Current truth: persona, memory, summaries, and helper coordination exist, and helpers can contribute bounded reasoning.
- Gap: helpers are still explicitly non-executable, and the node does not maintain a strong self-model of goals, uncertainty, maintenance needs, or active learning agenda.
- Done when:
- self-state includes explicit goals, uncertainty, capability inventory, and maintenance queues that influence behavior.
- helper roles can safely accept auditable bounded action capabilities instead of staying reasoning-only.

28. `P0` OpenClaw and local tool coverage are not yet complete enough for real operator tasks.
- Current truth: runtime can now enumerate a concrete tool inventory, and the real adapter surface includes disk inspection, approval-gated temp cleanup, process inspection, service/startup inspection, approval-gated move/archive actions, calendar-event outbox creation, and Discord/Telegram posting through configured bridges.
- Gap: there is still no proven end-to-end coverage for service control on Windows, package/app management, email/provider-backed calendar integrations, and broader browser/workflow automation across Windows, macOS, and Linux.
- Priority examples that must work:
- Windows: inspect disk bloat on `C:\`, identify temp/cache/log waste, clean approved temporary files, inspect startup/process/service offenders, and report before/after free-space impact.
- Cross-platform: find large files/folders, remove safe temp/cache material after approval, archive/move clutter, send messages, schedule meetings, and perform bounded browser or web-research workflows through OpenClaw.
- Done when:
- runtime can enumerate available tools and capabilities from the active OpenClaw profile and OS environment.
- representative tasks like "find disk bloat", "clean temporary files", "send a message", and "schedule a meeting" execute through real adapters with dry-run, approval, execution, and verification stages.

29. `P0` Learn-by-doing procedure acquisition is still missing.
- Current truth: memory, feedback, and learning-shard primitives exist, and verified temp-cleanup, move/archive, and calendar-event execution now promote reusable local procedure shards.
- Gap: learn-by-doing is still narrow and not yet generalized across the operator task matrix with richer preconditions, postconditions, rollback notes, and cross-node reuse semantics.
- Done when:
- executed workflows are promoted into reusable procedure shards or skill records after verified success.
- later similar tasks measurably improve by reusing those learned procedures instead of re-deriving them from scratch every time.

30. `P1` Destructive local-environment actions need OS-specific guardrails and verification.
- Current truth: doctrine says read-only actions may proceed and side-effect actions require explicit confirmation.
- Gap: there is no production-grade OS-aware safety layer for actions like temp cleanup, cache deletion, process termination, or file removal, especially on Windows where path scope, recycle-bin behavior, and protected locations matter.
- Done when:
- Windows, macOS, and Linux action adapters enforce protected-path rules, preview mode, approval checkpoints, and post-action verification.
- every destructive workflow has a bounded rollback or at least a concrete before/after audit report attached to the task record.

31. `P0` Cold-start operator competence must work before accumulated learning exists.
- Current truth: the system can reason about tasks, emit plans, describe tool usage, and now complete multiple bounded cold-start workflows: inspect storage, preview and execute temp cleanup, inspect live processes, inspect services/startup agents, preview and execute bounded move/archive actions, preview and create a calendar event, and post outbound Discord/Telegram messages through configured bridges.
- Gap: this is still not the broader full baseline for the target operator task matrix, especially around service control, package management, and richer OpenClaw-native tool execution.
- Done when:
- a fresh runtime with zero learned procedures can still inspect, propose, request approval, execute, and verify the target operator task matrix through real tools.
- repeat runs on similar tasks become faster, more accurate, or require less prompting because verified procedure knowledge was learned and reused.

32. `P0` Commons signal funnel is not yet production-credible.
- Current truth: useful-output canonicalization, Commons schema/store/service/routes, and training-eligibility gating now exist; unreviewed Commons content does not auto-feed adaptation, research priority now sees Commons steering signals, and reviewed/promoted Commons posts now carry stronger downstream adaptation weight than generic content.
- Gap: one known Commons promotion behavior mismatch remains, Commons endpoint/auth/quota coverage is incomplete, live VM/OpenClaw deployment is untested, and there is still no measured long-horizon proof that Commons materially improves adaptation outcomes over time.
- Done when:
- the focused Commons suite is fully green and route tests cover read, write, auth, and quota behavior.
- watch and trace surfaces show live Commons queue behavior on deployed runtime.
- Commons promotion measurably affects research priority or produces durable reviewed signal that later helps adaptation.

33. `P0` Adaptation loop is structurally closed but still starved of durable signal.
- Current truth: adaptation rails are installed, corpus curation now distinguishes proof-backed/finalized task results from still-pending work, and reviewed Commons posts now score above unreviewed/public noise when building training data.
- Gap: there is still not enough durable reviewed corpus from real accepted work to rerun adaptation honestly, compare against baseline, and promote a better candidate. The loop is structurally stricter now, but still mostly idle.
- Done when:
- useful-output and training-eligible counts stay above policy thresholds from real accepted work rather than synthetic chat.
- an adapted candidate is evaluated against baseline under canary and is either promoted with evidence or rejected with clear metrics.

34. `P1` Public commons identity and quota tiers are still missing for world-scale rollout.
- Current truth: rate limiting, signed writes, moderation review states, trust scores, and abuse-report gating exist.
- Gap: there are still no clear anonymous/provisional/trusted/operator write tiers, no reputation-coupled write or compute quotas, and no credit or stake reservation for higher-value public claims, so the public commons lane is still too easy to game.
- Done when:
- write and compute tiers plus reputation-coupled quotas are enforced by policy with tests.
- higher-risk writes or larger tasks reserve budget, and low-trust nodes face tighter envelopes by default.

## Research Quality Findings (2026-03-15)

Identified improvements for autonomous topic research and Hive research quality:

- **artifact_missing bottleneck**: Artifact creation and resolution is a bottleneck; research quality status often degrades to `artifact_missing` when artifact refs are unresolved. Fix creation and resolution flow so `artifact_missing` is rare.
- **Query limits**: Increase `max_queries_per_topic` and `max_snippets_per_query` in curiosity policy; increase derived-research-question caps in packet and autonomous research.
- **Artifact flow**: Ensure artifact creation and resolution are robust so research bundles reliably reference resolvable artifacts.
- **Search results**: Increase web results per query (snippets per query) for stronger evidence.
- **Multi-pass**: Run refinement pass when first pass is `partial`; already partially implemented.
- **Stricter solved**: Only mark `solved` when quality is `grounded`; already implemented in autonomous_topic_research.
- **Source quality**: Prefer authoritative domains in search and promotion.
- **Timeouts**: Increase model timeouts for research query generation and refinement where needed.

**Priority**: Smart locally, max efficient in Hive. Shards deferred.

## Audit Triage Notes (2026-03-07)

- The bounded local operator action lane now also covers service/startup inspection and approval-gated move/archive actions with verification and local procedure-shard promotion.
- At the 2026-03-07 checkpoint, release readiness reached `READY`, with a real closed-test update manifest, committed license texts, and repo hygiene checks wired into CI and installer-bundle generation.
- Repo hygiene is now clean: no repo-local signing-key artifact remains in source state, and `ops/repo_hygiene_check.py` returns `CLEAN`.
- The bounded local operator action lane now covers disk inspection, approval-gated temp cleanup, process inspection, service/startup inspection, tool inventory reporting, approval-gated move/archive actions, and approval-gated calendar-event creation, with before/after verification where applicable and local procedure-shard promotion on verified side effects.
- A single `system.local_only_mode` switch now disables web fallback, remote-only backend mode, remote model-provider selection, and API remote-fetch allowance.
- `ExecutionGate` now exposes a real `evaluate_command(...)` path, so `sandbox_runner` no longer targets a non-existent gate API.
- Installer bootstrap is now significantly friendlier for closed testing: it auto-selects a hardware-tier model, attempts OpenClaw configuration through Ollama, patches NULLA into OpenClaw config directly, and writes `install_receipt.json`.
- The new workflows are real enough for guarded closed testing, but they do not close the broader `P0` cluster by themselves; general execution, wider tool coverage, and generalized learn-by-doing remain open.

## Audit Triage Notes (2026-03-06)

- Local runtime summary currently reports `total_learning_shards: 0`, `local_generated_shards: 0`, and `peer_received_shards: 0`, so real learn-retain-reuse behavior is still unproven in live evidence.
- Execution-path review found a real wiring defect, not just conservative policy: `sandbox_runner` calls `ExecutionGate.evaluate_command(...)`, but the gate currently exposes `evaluate(...)`.
- Local provider summary currently exposes only `qwen-local` plus `cloud-fallback-http`, so heterogeneous inference in live runtime remains shallower than the architecture documents imply.
- On 2026-03-06, release readiness still returned warnings because release artifacts and license texts were placeholders and repo-local signing-key material still existed.

## Audit Triage Notes (2026-03-05)

- External claim: "`<50KB UDP limit`". Current runtime default is stricter (`32KB`) and still a real risk.
- External claim: "SQLite concurrency panic due to leaked shared connections". This is outdated relative to current code:
- current DB layer opens fresh connections per operation and closes them.
- WAL mode is explicitly enabled.
- Residual risk still exists under high write contention and should be observed in soak reports.
- External claim: "consensus still exact-string truth". Partially outdated:
- verdict flow now includes conflict states and semantic fallback hooks.
- quality for open-ended tasks is still partial and tracked above.

## Recently Closed (2026-03-05)

- `CLOSED` Credit burn race window:
  - ledger burn now uses `BEGIN IMMEDIATE` plus single-statement guarded insert.
  - award path now also uses immediate transaction and safe rollback on failure.
- `CLOSED` Nonce-cache unbounded growth concern:
  - nonce pruning now enforces both age limit and maximum row count.
- `CLOSED` Parent task prefix collision risk:
  - parent matching now prefers exact IDs and only uses prefix fallback for legacy short refs.
- `CLOSED` Heavy JSON scan on verification and parent-subtask lookups:
  - SQL prefilters now reduce capsule parse loops on hot paths.
- `CLOSED` Hard UDP payload wall for larger messages:
  - oversized payloads now use stream-first transfer with UDP fragmentation fallback.
- `CLOSED` No encrypted mesh wire mode:
  - optional PSK-based AES-GCM mesh payload encryption is now available for closed-test clusters.
- `CLOSED` No TLS support on meet service:
  - meet server now supports optional TLS cert/key wrapping and HTTPS replication client trust configuration.
- `CLOSED` Sandbox network isolation only heuristic:
  - Linux network-namespace isolation path now exists, with fail-closed `os_enforced` mode.
- `CLOSED` Helper worker NameError path on model-success:
  - helper execution flow now initializes context deterministically and uses model-or-template reasoning without undefined variable fallthrough.
- `CLOSED` Meet GET metadata exposure on public binds:
  - protected `/v1/*` GET routes now require auth token on non-loopback hosts.
- `CLOSED` Public meet-node TLS posture gap:
  - non-loopback meet-node deployments now require TLS cert/key unless explicitly marked insecure for closed testing.
- `CLOSED` Meet internal error leakage:
  - dispatch and signed-write failures now return sanitized client errors and keep details in audit logs.
- `CLOSED` UDP shared-socket timeout race:
  - send path now uses per-call sockets; mutable global send socket removed.
- `CLOSED` Fragment datagram ceiling fragility:
  - fragmentation uses a conservative datagram cap (`system.max_fragment_datagram_bytes`, default `1400`) to improve cross-platform reliability.
- `CLOSED` Transport daemon startup crash (`threading` missing):
  - transport module now imports threading and runtime tests pass on the fragmented payload path.
- `CLOSED` Pooled DB connection transaction leakage:
  - pooled wrapper now rolls back active transactions on `close()` and supports context manager semantics.
- `CLOSED` Packaging script entrypoint gaps:
  - `main()` entrypoints now exist for `nulla-agent`, `nulla-daemon`, and `nulla-meet`.
- `CLOSED` Runtime DB artifact contamination in tree:
  - stale `storage/nulla_web0_v2.db*` artifacts removed.
- `CLOSED` Parent-ref prefix contamination in reassembler:
  - reassembler now resolves child offers by exact `parent_task_ref` with bounded legacy fallback.
- `CLOSED` Assist replay double-validation blocked local assignment/review:
  - daemon follow-up envelope decode now supports replay-safe revalidation (signature + payload) without mutating nonce cache twice.
  - regression test covers second-pass replay-safe decode behavior.
- `CLOSED` FIND_BLOCK self-advertisement used placeholder endpoint:
  - block-host responses now advertise the registered local endpoint instead of `0.0.0.0:49152`.
- `CLOSED` Timeout policy was not wired into maintenance:
  - maintenance loop now runs a stale-subtask reaper and marks overdue subtasks timed out while reopening eligible offers.
- `CLOSED` Non-assist daemon path had no per-peer rate limiting:
  - non-assist decoded envelopes now pass through the same peer-level rate limiter gate.
- `CLOSED` Local helper execution thread fanout was unbounded:
  - daemon now uses bounded local worker concurrency with explicit capacity-exhausted audit events.
- `CLOSED` Single-machine helper pool lacked practical sizing controls:
  - local worker capacity now supports auto-detection (CPU/RAM and optional CUDA VRAM signal), policy target override, and explicit startup warnings on aggressive manual overrides.
  - orchestration now scales subtask width against local worker-pool policy so one machine can fan out multiple local helper lanes.
- `CLOSED` Control-plane send reliability was single-attempt:
  - critical control messages now use bounded retry attempts before failing and logging.
- `CLOSED` DHT routing table was a flat dictionary only:
  - routing table now uses bounded K-bucket-like partitions with XOR-distance ranking and stale-node pruning.
- `CLOSED` Fragment reassembly lacked post-join size guard:
  - reassembly now drops oversized joined payloads above `system.max_message_bytes`.
- `CLOSED` Stream send path was single-attempt:
  - stream transport now performs bounded retry attempts before falling back.
- `CLOSED` Daemon health endpoint auth gap on public binds:
  - non-loopback health endpoints now require `health_auth_token`; requests must provide `X-Nulla-Health-Token`.
- `CLOSED` Replay protection race in protocol and signed HTTP writes:
  - nonce consume is now atomic (`INSERT OR IGNORE`) for both mesh envelope validation and signed-write unwrap.
  - concurrent replay regression tests now prove single acceptance under race.
- `CLOSED` Meet replication/watch auth token gap on protected APIs:
  - replication HTTP client now forwards `X-Nulla-Meet-Token` with global or per-upstream token mapping.
  - watch-edge upstream dashboard fetch now forwards the same token model.
  - `do_ip_first_bootstrap` now provisions a shared cluster meet token across seeds + watch config.
- `CLOSED` Stream DoS surface from unbounded frame/client handling:
  - stream server now enforces bounded concurrent clients, socket timeouts, and maximum frame length.
  - transfer manager now enforces incoming transfer count/bytes TTL bounds.
- `CLOSED` Docker deployment env drift and secret context hygiene:
  - compose now uses `NULLA_HOME` and `NULLA_MEET_AUTH_TOKEN`.
  - `.dockerignore` now excludes runtime state and key artifacts (`.nulla_local*`, `data/keys/*`, DB artifacts).
- `CLOSED` Ops report scripts required manual `PYTHONPATH` bootstrap:
  - key ops reports now self-bootstrap project root into `sys.path` for direct execution.
- `CLOSED` Meet query `limit` hardening gap:
  - query integer parsing now clamps to a policy-driven max (`meet.max_query_limit`).

## Closed-Test Guardrails (while items remain open)

- Keep deployment posture as closed trusted testing, not hostile public network.
- Keep paid top-up disabled, and keep payments and DEX explicitly labeled simulated.
- Keep watcher/read surfaces separated from meet write surfaces.
- Keep signed writes, replay checks, and identity lifecycle enforcement enabled everywhere.

## Review Cadence

- Re-check this TDL before every new cross-machine test cycle.
- Do not mark items complete without test evidence (not just module presence).
