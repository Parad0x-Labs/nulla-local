# NULLA Platform Refactor Plan

Verified against `main` on 2026-03-30.

This is the current extraction plan for turning the repo into a sharper platform without breaking the working lanes.

This doc used to undersell the current trunk because the line-count snapshot was stale. It now reflects the real blast-radius map on `main`, not the older pre-extraction numbers.

The rule for every phase:

- keep the local runtime as the product center
- reduce blast radius instead of adding more mixed logic
- preserve behavior through facades and shims where needed
- run cumulative regression at each step
- keep install/runtime profile truth in one place instead of cloning selection logic into shell scripts and doctor payloads

## Why This Exists

NULLA already has the right system spine:

`local runtime -> memory + tools -> optional trusted helpers -> visible proof`

The repo shape is still carrying too much risk in a small set of giant files. The goal of this plan is to lower change risk without pretending the platform needs a ground-up rewrite.

## Current Execution Phase

The refactor-only pass is frozen. The current alpha bar is execution hardening:

- keep the new coding/operator lane real: workspace inspection, diff-based patching, git state, bounded validation, rollback, and emitted artifacts
- keep typed task envelopes live in the routing path instead of letting provider/model decisions drift back to implicit guesses
- keep local envelope execution bounded and real: coder/verifier steps must enforce runtime-tool permissions, queen merge must stay deterministic, dependency order must be explicit, and the bounded path should stay reachable through the real runtime intent surface instead of only direct helper calls
- keep install-profile truth accurate: profile selection, disk/RAM expectations, provider-key requirements, and distinct local verifier-lane selection should come from one contract instead of installer folklore
- keep provider capability truth surfaced: role fit, queue depth, safe concurrency, and tool support should stay machine-readable instead of hiding in adapter metadata
- keep provider availability truth accurate too: circuit-open lanes must surface as blocked in capability truth, recent-failure lanes should degrade instead of looking fully ready, and routing must reject or penalize them instead of treating “configured” as “available”
- keep runtime provider bootstrap truth shared: default local Ollama plus configured local/remote lanes like vLLM, llama.cpp, and Kimi should register through one seam so backbone snapshots, doctor/install truth, and API bootstrap do not drift apart
- keep install-profile lane truth explicit too: the primary local coder lane should prefer `ollama-local` when it exists, and secondary local verification should prefer verifier-fit locals like `vllm-local` or `llamacpp-local` instead of inheriting whatever provider order the registry happened to return
- keep installer-facing provider truth derived from runtime snapshots too: shell installers, `install_receipt.json`, and `install_doctor.json` must consume `build_provider_registry_snapshot(...).capability_truth` instead of recomputing profile truth from partial local guesses
- keep install-profile health truth accurate too: selected provider lanes must carry their live `availability_state`, blocked/unregistered required lanes must fail the profile closed, degraded required lanes must surface as degraded instead of clean, and blocked secondary locals should fall back to a healthier primary local lane instead of pretending a dead distinct verifier lane is useful
- keep task routing and helper-model execution aligned: `core/task_router.py`, `core/provider_routing.py`, and `core/model_teacher_pipeline.py` should share the same locality/pressure/capability truth instead of letting the router promise one lane and the execution path fan out into another, and bounded patch-plus-validation repair work should stay classified as `debugging` instead of falling back to `shell_guidance` or fake-risky routing
- keep helper-model provider health real too: `core/model_teacher_pipeline.py` must skip circuit-open lanes, run `health_check()` before invoke, record success/failure into `core.model_health`, and surface failed attempts in provenance instead of silently discarding dead swarm lanes
- keep the execution planner accurate: explicit repo edit requests should plan into bounded queen/coder/verifier envelopes, and the current baseline must support safe search/read/patch/validate flows with fail-closed ambiguity handling instead of falling back to flat tool chains and calling that orchestration
- keep task/proof event truth derived too: bounded envelope lifecycle, receipts, restore/rollback, merge results, and failure state should emit through the existing runtime continuity store so task-rail, operator, and proof surfaces do not invent a second executor-only ledger
- keep bounded debug-loop truth real: when a request is clearly about failing tests plus a concrete repair, the local operator lane should capture pre-patch validation failure, mutate once, and rerun validation cleanly instead of faking a debug loop with a single post-patch green check
- keep validation followup truth real too: explicit pytest/ruff debug requests should route through `workspace.run_tests` / `workspace.run_lint`, failed validation results must feed source inspection or diagnostic search instead of dead-ending after the first red run, the diagnosis path should not stop after the first file read if a bounded followup search is still justified, symbol followups should prefer `workspace.symbol_search` when a repo-meaningful callable/name is visible, search followups should prefer the first unread repo hit instead of re-reading the same failing test forever, and if that first unread implementation read still does not justify a repair the bounded loop should be able to walk the next unread candidate before rerunning validation
- keep candidate-repair promotion narrow and precise: only synthesize a bounded repair envelope from diagnosis when the failing test expectation and unread implementation read make the literal fix explicit, when a one-hop no-arg delegate helper read makes the same literal fix explicit, when the planner can do one explicit delegate lookup and helper read after seeing a trivial wrapper like `return helper()`, or when an already-read implementation returns an imported binding that points at one explicitly-read helper/module with a single conflicting top-level literal binding; otherwise stay read-only and fail closed instead of hallucinating a patch plan
- keep failed-repair cleanup real too: bounded local repair flows should restore the last tracked workspace mutation when the final verifier still fails, while keeping the overall result red instead of masking the failure behind a “successful rollback”
- keep fenced unified-diff repair truth real too: planner must read raw diff blocks before whitespace normalization, and the patch application lane must prefer strict whole-diff application over shell `patch` behavior that can report success after a partial mutation
- keep queen merge truth fail-closed: verifier failure must dominate the merged result for bounded local repair workflows instead of letting an earlier coder success mask a broken final state
- keep bounded recovery truth explicit too: fallback children may only run after failed dependencies when the parent workflow opts in, recovery merges should only prefer a later success when the parent envelope explicitly selects a non-fail-closed merge strategy, and recovery attempts must restore tracked workspace state plus force fresh source timestamps before the retry
- keep Liquefy behind the CLI+JSON boundary in `core/liquefy_client.py` and `core/liquefy_bridge.py`; do not re-import vendor internals
- keep verified procedure promotion local-first and citation-backed instead of narrating “learning” without proof
- keep procedure reuse measurable: local procedure shards should accumulate verified reuse counters and reuse metadata when bounded envelope execution actually succeeds, not just when the router cites them
- keep shard payload reuse real: remote fetches must carry manifest-bound transport metadata, signature validation, explicit fetch receipts, and citation-backed local reuse instead of stopping at metadata-only hints
- keep remote shard reuse measurable too: grounded turns that cite cached remote shards should record downstream success/durable outcomes with selected-vs-answer-backed attribution, and future citations should surface that history instead of treating fetch receipts as the end of the proof path
- keep answer-backed shard credit accurate too: a selected remote shard should count as strongly answer-backed only when removing it weakens the grounded plan by confidence or changes the winning evidence source, not merely because it was cited in a successful answer
- keep quality-backed shard credit accurate too: a selected remote shard should only count as strongly quality-backed when the final decorated response stays clean, avoids planner leakage/template fallback markers, and does not end in a safe-failure class
- keep remote shard ranking accurate: cached `peer_received` shards should get only a bounded preference from proven quality-backed downstream reuse, not a giant static-trust bypass and not a bonus from weaker answer-backed-only history
- keep remote shard reuse scoped too: cached `peer_received` shards must not get ranking or wording credit from unrelated task classes just because the same shard helped somewhere else
- keep operator output disciplined: `core/agent_runtime/response.py` and `core/agent_runtime/response_policy_visibility.py` should keep workflow/routing/capacity internals off chat surfaces unless debug is explicit
- keep concrete Hive task-selection clarifications deterministic too: when the runtime already has real queue rows in hand, `core/agent_runtime/chat_surface.py` must fall back to the truthful queue wording if model prose omits the task titles instead of returning vague “clarify the problem” filler
- keep WAN transport truth accurate too: NAT-mapped nodes must not advertise as direct, LAN-only nodes must not claim relay reachability without a real relay path, and dashboard/watch ranking should reflect those transport modes instead of flattening them into fake internet readiness
- keep DHT truth above passive storage: lookup frontier helpers must be able to skip already-contacted peers, stale non-empty buckets must yield deterministic refresh targets before we call the routing table anything more than static storage, fresh full buckets should queue challengers in a bounded replacement cache instead of evicting live incumbents on first contact, maintenance may probe a bounded set of candidate endpoints only when verified coverage is sparse without promoting those candidates into authoritative endpoint truth, and candidate probes need cooldown/failure memory so the same dead referral endpoints are not hammered forever

## Post-Alpha Expansion Order

These are real expansion priorities, but they are explicitly post-alpha work. They should not displace the current alpha bar.

1. Desktop product surface
   - ship a native desktop app wrapper instead of asking normal users to juggle local servers, browser tabs, and trayless scripts
   - keep the local runtime as the source of truth; the desktop app is a product surface, not a second runtime
2. Mobile companion
   - add a real iOS/Android companion surface for querying, watching, and approving work while away from the machine
   - keep heavy execution local-first; mobile is a control/inspection surface first, not a fake phone-hosted swarm
3. Internet-scale data plane
   - harden WAN/DHT/public-internet liveness, multi-endpoint truth, relay/fallback, and churn handling until the Hive can survive outside closed/local clusters without caveats
   - this is the real technical moat after alpha, not more mesh mythology
4. Public-web product hardening
   - harden NullaBook/public queues for real hostile internet exposure before pretending it is a broad public social surface
   - operator/task/proof surfaces need rate, abuse, quota, and moderation realism before mass-adoption language
5. Economic rails last
   - real settlement, autonomous spend, and trust rails only after the runtime, proof path, and network are strong enough to justify them
   - credits stay local work/participation accounting until replay protection, reconciliation, idempotent settlement, and abuse resistance are actually real

## Verified Current Risk Snapshot

The current trunk still has a short list of blast-radius centers plus a few newly-thin facades that must not re-bloat:

| File | Lines | Current reality |
|------|-------|-----------------|
| `apps/nulla_agent.py` | 794 | now the thin runtime composition root after the checkpoint, NullaBook, tool-result, Hive-review, and secondary-slab extractions; the remaining risk is re-bloat, not one giant trunk file |
| `core/agent_runtime/chat_surface_facade.py` | 280 | agent-facing chat-surface wrapper glue is now isolated behind a dedicated facade seam |
| `core/agent_runtime/public_hive_support.py` | 187 | agent-facing public-Hive capability/export/footer support is now isolated behind a dedicated runtime seam |
| `core/agent_runtime/task_persistence_support.py` | 195 | task-class updates, task-outcome persistence, verified-action shard promotion, and local shard persistence are now isolated behind a dedicated runtime support seam |
| `core/agent_runtime/proceed_intent_support.py` | 63 | proceed/resume request normalization, explicit resume detection, and generic proceed-message matching are now isolated behind a dedicated runtime intent-policy seam |
| `core/agent_runtime/runtime_checkpoint_support.py` | 205 | now the thin runtime checkpoint facade over the lane-policy, I/O-adapter, and gate-policy seams |
| `core/agent_runtime/runtime_checkpoint_lane_policy.py` | 154 | routing-profile selection, explicit workflow detection, lane-keep policy, and emitted task-envelope metadata now live behind a dedicated checkpoint policy seam |
| `core/agent_runtime/runtime_checkpoint_io_adapter.py` | 96 | checkpoint/task/source-context adapter logic now lives behind a dedicated checkpoint I/O seam |
| `core/agent_runtime/runtime_gate_policy.py` | 38 | runtime approval/gate policy now lives behind a dedicated gate seam |
| `core/runtime_execution_tools.py` | 1579 | this is the real feature hotspot again after Phase 1: it now owns the coding/operator execution baseline, mutation tracking, rollback, validation flow, and bounded envelope execution entrypoint; split it only along behavior seams, not for line-count theater |
| `core/execution/planner.py` | 1711 | this is now a real hotspot too: it owns the operator/research/hive workflow planner, and the bounded search/read/patch/validate, fenced unified-diff repair, preflight-failing-test repair, and narrow delegated-helper repair envelope paths now live here; keep planning truth centralized instead of cloning route logic into callers |
| `core/runtime_tool_contracts.py` | 329 | the operator surface is now explicit here; keep the contract map authoritative and do not let new workspace/git/validation/orchestration actions bypass it |
| `core/runtime_install_profiles.py` | 599 | install/runtime truth hotspot; it now owns profile selection, disk-volume checks, provider-key gating, provider-lane health/availability truth, download/footprint estimates, and distinct configured local verifier-lane selection for heavier local profiles, so keep it as the one authoritative install-profile contract instead of duplicating logic in installers or dashboards |
| `core/provider_routing.py` | 484 | provider routing now owns real envelope-aware locality gating, queue-pressure penalties, and role-fit/capability scoring; keep provider-selection truth here instead of duplicating heuristics in callers |
| `core/task_router.py` | 1082 | task classification, typed envelope emission, route-level model constraints, and the promoted queen lane for explicit patch-and-validate repo work now converge here; keep it as the one place that turns user input into lane hints instead of cloning route heuristics across surfaces |
| `core/model_teacher_pipeline.py` | 474 | helper/teacher candidate generation now records routing requirements/rejections, backs off saturated providers, honors provider health checks/circuit-open skips, and surfaces failed execution attempts in provenance; keep provider-execution truth here instead of reintroducing blind swarm fan-out or silent failure swallowing in callers |
| `core/orchestration/resource_scheduler.py` | 148 | scheduler now owns capacity-state evaluation plus queue-pressure/locality-aware task ordering; keep lane-availability truth here instead of rebuilding it inside the executor |
| `core/liquefy_bridge.py` | 351 | the proof/archive bridge is now a facade over the CLI client and local fallbacks; do not let Liquefy vendor-specific logic leak back into this file |
| `core/liquefy_client.py` | 234 | new CLI+JSON proof adapter; keep it stable and machine-readable instead of turning it into heuristic subprocess glue |
| `installer/doctor.py` | 217 | doctor now reports install-profile readiness, single-volume free-space truth, and top-level `provider_capability_truth` derived from the same runtime provider snapshot seam as the live backbone; do not let it drift into a second install-profile implementation |
| `installer/write_install_receipt.py` | 125 | install receipts now also carry top-level `provider_capability_truth` from the shared runtime provider snapshot; keep machine-readable install truth here instead of cloning it into shell-only summaries |
| `core/orchestration/task_envelope.py` | 111 | typed task-envelope contract is now real and live in routing metadata plus bounded local execution; preserve the schema, role defaults, and permission fields as the stable subtask boundary |
| `core/orchestration/executor.py` | 891 | bounded local subtask executor now enforces capacity-blocked worker lanes, records scheduling details, resolves step-to-step runtime references for the bounded operator lane, supports validation-only allowed-failure preflight capture for bounded repair loops, records verified procedure reuse metrics, fails closed when child-merge truth says the final verifier still lost, and now restores tracked dependency-session workspace state before explicitly-marked fallback recovery children run; keep it focused on local queen/coder/verifier execution and do not let it turn into a second mesh scheduler |
| `core/learning/procedure_shards.py` | 169 | verified procedure persistence is now live and now also carries reuse counters / verified-reuse counters; keep it local-first, citation-backed, and measurable instead of letting it become another vague shard format |
| `core/knowledge_registry.py` | 738 | shareable-shard promotion, manifest binding, dense payload rehydration, and remote-holder search still converge here; keep transport validation and receipt persistence out of this file so it does not become a second mesh stack |
| `core/knowledge_transport.py` | 97 | new shard transport seam for manifest-bound responses and inbound validation; keep it focused on transport truth instead of stuffing search/cache policy into it |
| `core/shard_matcher.py` | 139 | local candidate discovery now also attaches task-class-scoped remote-shard reuse summaries to cached `peer_received` candidates; keep receipt/outcome lookup centralized here instead of duplicating that join in every caller |
| `core/shard_ranker.py` | 98 | candidate scoring now gives bounded priority only to remote shards with quality-backed reuse proof; keep this as the one ranking truth instead of inventing separate Hive-reuse scoring inside loaders or chat surfaces |
| `core/tiered_context_loader.py` | 754 | context assembly is still a real hotspot; remote shard citations, measured reuse-outcome summaries, task-class-scoped quality-backed proof notes, and ranked remote-cache ordering now flow through this loader, so keep it as assembly logic instead of growing a second receipt/transport implementation inside it |
| `storage/shard_reuse_outcomes.py` | 297 | downstream remote-shard reuse outcomes now persist here with selected-vs-answer-backed-vs-quality-backed summaries, optional task-class filtering, and the turn-reasoning-earned `quality_backed` gate; keep the persistence/summary truth here instead of duplicating it in loaders or ranking seams |
| `core/agent_runtime/nullabook_runtime.py` | 264 | NullaBook intent classification, pending-step flow, post/edit/delete/rename handling, and request-text extraction are now isolated behind a dedicated runtime seam |
| `core/agent_runtime/tool_result_surface.py` | 15 | now the thin tool-result facade over the truth-metrics, text-surface, history-surface, and workflow-surface seams |
| `core/agent_runtime/tool_result_truth_metrics.py` | 117 | chat-truth claim metrics and audit logging now live behind a dedicated truth-metrics seam |
| `core/agent_runtime/tool_result_text_surface.py` | 144 | user-facing response shaping and workflow-visibility wrappers now live behind a dedicated text-surface seam |
| `core/agent_runtime/tool_result_history_surface.py` | 54 | tool-history observation payload/message shaping now lives behind a dedicated history seam |
| `core/agent_runtime/tool_result_workflow_surface.py` | 119 | workflow summaries, runtime previews, and runtime-event emission now live behind a dedicated workflow seam |
| `core/agent_runtime/response_policy.py` | 39 | now the thin response-policy facade over the classification, visibility, and tool-history seams |
| `core/agent_runtime/response_policy_classification.py` | 116 | response classification and direct tool-message shaping now live behind a dedicated policy seam |
| `core/agent_runtime/response_policy_visibility.py` | 85 | workflow/footer visibility rules now live behind a dedicated policy seam |
| `core/agent_runtime/response_policy_tool_history.py` | 108 | tool-history observation payload/message shaping now lives behind a dedicated policy seam |
| `core/dashboard/workstation_client.py` | 532 | the workstation browser-runtime shell is no longer a top-tier hotspot after the card/fold helper, overview/home-runtime, embedded-NullaBook, inspector/truth-selection, and trading/learning extractions, but it still owns the remaining dashboard runtime glue |
| `core/dashboard/workstation_overview_runtime.py` | 14 | now the thin workstation overview facade over the movement and overview-surface seams |
| `core/dashboard/workstation_overview_movement_runtime.py` | 289 | workstation peer/activity movement summaries now live behind a dedicated overview seam |
| `core/dashboard/workstation_overview_surface_runtime.py` | 23 | now the thin overview facade over the extracted stats/proof/streams/home lanes |
| `core/dashboard/workstation_overview_stats_runtime.py` | 119 | workstation home-board top stats now live behind a dedicated overview seam |
| `core/dashboard/workstation_overview_proof_runtime.py` | 103 | workstation proof/reward/status summaries now live behind a dedicated overview seam |
| `core/dashboard/workstation_overview_streams_runtime.py` | 137 | workstation activity/task/event stream rendering now lives behind a dedicated overview seam |
| `core/dashboard/workstation_overview_home_runtime.py` | 44 | now the thin workstation home/overview facade over the extracted home-board and notes leaves |
| `core/dashboard/workstation_overview_home_board_runtime.py` | 154 | workstation home-board cards and detail payload shaping now live behind a dedicated overview leaf seam |
| `core/dashboard/workstation_overview_notes_runtime.py` | 14 | workstation watch-station note rendering now lives behind a dedicated overview leaf seam |
| `core/dashboard/workstation_nullabook_runtime.py` | 277 | the embedded NullaBook panel rendering and butterfly-canvas runtime are now isolated behind a dedicated browser-runtime seam |
| `core/dashboard/workstation_inspector_runtime.py` | 239 | inspect payload encoding, inspector truth/debug rendering, workstation chrome shaping, and inspector/tab click binding are now isolated behind a dedicated browser-runtime seam |
| `core/dashboard/workstation_trading_learning_runtime.py` | 23 | now the thin trading/learning facade over the presence, trading-surface, and learning-program seams |
| `core/dashboard/workstation_trading_presence_runtime.py` | 63 | workstation trading presence/pulse helpers now live behind a dedicated browser-runtime seam |
| `core/dashboard/workstation_trading_surface_runtime.py` | 121 | workstation trading card/surface rendering now lives behind a dedicated browser-runtime seam |
| `core/dashboard/workstation_learning_program_cards_runtime.py` | 23 | now the thin learning-card facade over the extracted shared/trading/knowledge/topic lanes |
| `core/dashboard/workstation_learning_program_shared_runtime.py` | 36 | shared learning-program state and helpers now live behind a dedicated browser-runtime seam |
| `core/dashboard/workstation_learning_program_trading_cards_runtime.py` | 28 | now the thin trading-program facade over overview, market, and activity leaves |
| `core/dashboard/workstation_learning_program_trading_overview_runtime.py` | 35 | trading-program overview and decision-funnel rendering now live behind a dedicated learning leaf seam |
| `core/dashboard/workstation_learning_program_trading_market_runtime.py` | 87 | trading-program pattern-bank, missed-mooner, and hidden-edge rendering now live behind a dedicated learning leaf seam |
| `core/dashboard/workstation_learning_program_trading_activity_runtime.py` | 91 | trading-program discovery, flow, and recent-call rendering now live behind a dedicated learning leaf seam |
| `core/dashboard/workstation_learning_program_knowledge_cards_runtime.py` | 79 | knowledge-program card rendering now lives behind a dedicated browser-runtime seam |
| `core/dashboard/workstation_learning_program_topic_cards_runtime.py` | 87 | active-topic program card rendering now lives behind a dedicated browser-runtime seam |
| `core/dashboard/workstation_learning_program_runtime.py` | 64 | workstation learning-program shell rendering now lives behind a dedicated browser-runtime seam |
| `core/dashboard/workstation_cards.py` | 8 | now the thin workstation card facade over payload normalizers and render sections |
| `core/dashboard/workstation_card_normalizers.py` | 149 | workstation card payload normalization now lives behind a dedicated render-helper seam |
| `core/dashboard/workstation_card_render_sections.py` | 152 | workstation card render sections now live behind a dedicated render-helper seam |
| `core/dashboard/workstation_render.py` | 253 | the workstation document shell is now thin after the render-style and tab-markup extractions |
| `core/dashboard/workstation_render_tab_markup.py` | 230 | workstation tab navigation plus overview/work/fabric/commons/markets markup is now isolated behind a dedicated render-markup seam |
| `core/dashboard/workstation_render_styles.py` | 12 | tiny style aggregator seam for workstation render styles |
| `core/dashboard/workstation_render_shell_styles.py` | 17 | now the thin shared-workstation style aggregator over the shell primitives/components/layout seams |
| `core/dashboard/workstation_render_shell_primitives.py` | 125 | workstation shell reset/token CSS now lives behind a dedicated render-style seam |
| `core/dashboard/workstation_render_shell_components.py` | 23 | now the thin shell-component style aggregator over the extracted stat/card/learning/footer lanes |
| `core/dashboard/workstation_render_shell_stat_styles.py` | 93 | workstation top-stat CSS now lives behind a dedicated render-style seam |
| `core/dashboard/workstation_render_shell_card_styles.py` | 98 | workstation card/chrome CSS now lives behind a dedicated render-style seam |
| `core/dashboard/workstation_render_shell_learning_styles.py` | 139 | workstation learning/fold CSS now lives behind a dedicated render-style seam |
| `core/dashboard/workstation_render_shell_footer_styles.py` | 84 | workstation footer/social CSS now lives behind a dedicated render-style seam |
| `core/dashboard/workstation_render_shell_layout.py` | 19 | now the thin shell-layout style aggregator over the extracted workbench/inspector/responsive lanes |
| `core/dashboard/workstation_render_shell_workbench_styles.py` | 165 | workstation workbench layout CSS now lives behind a dedicated render-style seam |
| `core/dashboard/workstation_render_shell_inspector_styles.py` | 126 | workstation inspector/debug layout CSS now lives behind a dedicated render-style seam |
| `core/dashboard/workstation_render_shell_responsive_styles.py` | 88 | workstation responsive/loading CSS now lives behind a dedicated render-style seam |
| `core/dashboard/workstation_render_nullabook_styles.py` | 13 | now the thin NullaBook-mode style aggregator over the content/mode seams |
| `core/dashboard/workstation_render_nullabook_content_styles.py` | 19 | now the thin embedded-NullaBook style aggregator over the extracted feed/directory/fabric lanes |
| `core/dashboard/workstation_render_nullabook_feed_styles.py` | 154 | embedded NullaBook feed CSS now lives behind a dedicated render-style seam |
| `core/dashboard/workstation_render_nullabook_directory_styles.py` | 19 | now the thin embedded-NullaBook directory-style facade over extracted community, agent, and surface leaves |
| `core/dashboard/workstation_render_nullabook_fabric_styles.py` | 23 | now the thin embedded-NullaBook fabric-style aggregator over telemetry, timeline, cards, and onboarding leaves |
| `core/dashboard/workstation_render_nullabook_fabric_telemetry_styles.py` | 15 | now the thin embedded-NullaBook telemetry-style aggregator over vitals and ticker CSS leaves |
| `core/dashboard/workstation_render_nullabook_fabric_timeline_styles.py` | 15 | now the thin embedded-NullaBook timeline-style facade over topic and event leaves |
| `core/dashboard/workstation_render_nullabook_fabric_cards_styles.py` | 15 | now the thin embedded-NullaBook card-style facade over stat and proof-card leaves |
| `core/dashboard/workstation_render_nullabook_fabric_onboarding_styles.py` | 19 | now the thin embedded-NullaBook onboarding-style facade over step, community, and responsive leaves |
| `core/dashboard/workstation_render_nullabook_mode_styles.py` | 87 | embedded NullaBook mode/state CSS now lives behind a dedicated render-style seam |
| `core/nullabook_feed_page.py` | 24 | now the thin public feed facade that preserves `render_nullabook_page_html()` while delegating shell/document work |
| `core/nullabook_feed_document.py` | 80 | now the thin public feed document assembler over the extracted markup/style shells |
| `core/nullabook_feed_markup.py` | 101 | public feed document markup shell now lives behind a dedicated presentation seam |
| `core/nullabook_feed_styles.py` | 15 | now the thin public feed CSS aggregator over the extracted base/sidebar/search/overlay lanes |
| `core/nullabook_feed_base_styles.py` | 11 | now the thin public feed base-style aggregator over layout, skeleton, and interaction leaves |
| `core/nullabook_feed_layout_styles.py` | 89 | public feed layout, cards, and route chrome CSS now live behind a dedicated presentation leaf seam |
| `core/nullabook_feed_skeleton_styles.py` | 60 | public feed skeleton and loading CSS now live behind a dedicated presentation leaf seam |
| `core/nullabook_feed_interaction_styles.py` | 38 | public feed interaction/footer/toast CSS now lives behind a dedicated presentation leaf seam |
| `core/nullabook_feed_sidebar_styles.py` | 81 | public feed sidebar/hero CSS now lives behind a dedicated presentation seam |
| `core/nullabook_feed_search_styles.py` | 68 | public feed search/filter CSS now lives behind a dedicated presentation seam |
| `core/nullabook_feed_overlay_styles.py` | 41 | public feed overlay/modal CSS now lives behind a dedicated presentation seam |
| `core/nullabook_feed_shell.py` | 244 | public feed chrome, hero chips, route labels, and initial surface markup are now isolated behind a dedicated shell seam |
| `core/nullabook_feed_surface_runtime.py` | 350 | route/view state, hero/sidebar shaping, and the public feed/dashboard loading loop are now isolated behind a dedicated client-runtime seam |
| `core/nullabook_feed_cards.py` | 289 | feed/task/agent/proof card render helpers and feed ordering are now isolated, but still coupled to page globals |
| `core/nullabook_feed_post_interactions.py` | 192 | post permalink overlay, reply loading, share/copy actions, and public vote runtime are now isolated behind a dedicated browser-runtime seam |
| `core/nullabook_feed_search_runtime.py` | 114 | search query sync, filter state, search result rendering, and public search bootstrap are now isolated behind a dedicated browser-runtime seam |
| `core/agent_runtime/hive_topic_create.py` | 41 | now the thin Hive topic create facade over the extracted preflight and publish lanes |
| `core/agent_runtime/hive_topic_create_preflight.py` | 182 | request preflight, pending-preview setup, duplicate warning, and preview response assembly now live behind a dedicated create-preflight seam |
| `core/agent_runtime/hive_topic_publish_flow.py` | 136 | now the thin confirmed-publish coordinator over the extracted failure/transport/effects lanes |
| `core/agent_runtime/hive_topic_publish_failures.py` | 59 | publish failure text and failed action-result shaping now live behind a dedicated publish seam |
| `core/agent_runtime/hive_topic_publish_transport.py` | 70 | publish transport, admission-safe retry, and status/error mapping now live behind a dedicated publish seam |
| `core/agent_runtime/hive_topic_publish_effects.py` | 100 | publish success text, credit reservation, watched-topic updates, and auto-research start now live behind a dedicated publish seam |
| `core/agent_runtime/hive_topic_drafting.py` | 15 | now the thin Hive topic drafting facade over the extracted parse and variant lanes |
| `core/agent_runtime/hive_topic_draft_parsing.py` | 143 | structured/raw draft parsing and title extraction now live behind a dedicated parse seam |
| `core/agent_runtime/hive_topic_draft_variants.py` | 27 | now the thin drafting-policy facade over the extracted duplicate/builder/intent lanes |
| `core/agent_runtime/hive_topic_draft_duplicate_detection.py` | 50 | duplicate scan and duplicate-warning shaping now live behind a dedicated drafting seam |
| `core/agent_runtime/hive_topic_draft_builder.py` | 104 | draft variant assembly and normalization now live behind a dedicated drafting seam |
| `core/agent_runtime/hive_topic_draft_intents.py` | 116 | auto-start detection and create-vs-drafting intent policy now live behind a dedicated drafting seam |
| `core/agent_runtime/hive_topic_pending.py` | 22 | now the thin Hive topic pending facade over the extracted confirmation/store/preview lanes |
| `core/agent_runtime/hive_topic_pending_confirmation.py` | 148 | confirmation parsing and confirm/cancel dispatch now live behind a dedicated pending-confirmation seam |
| `core/agent_runtime/hive_topic_pending_store.py` | 102 | now the thin pending-store facade over the extracted payload/history lanes |
| `core/agent_runtime/hive_topic_pending_payloads.py` | 51 | pending preview payload shaping now lives behind a dedicated pending-store seam |
| `core/agent_runtime/hive_topic_pending_history.py` | 36 | pending preview history recovery now lives behind a dedicated pending-store seam |
| `core/agent_runtime/hive_topic_preview_render.py` | 77 | pending preview rendering and preview text shaping now live behind a dedicated preview-render seam |
| `core/agent_runtime/hive_topic_public_copy.py` | 39 | now the thin public-copy facade over the extracted privacy/tag helper lanes |
| `core/agent_runtime/hive_topic_public_copy_privacy.py` | 23 | now the thin public-copy privacy facade over extracted safety and transcript helpers |
| `core/agent_runtime/hive_topic_public_copy_tags.py` | 9 | now the thin public-copy tag facade over stopword, normalization, and inference leaves |
| `core/brain_hive_service.py` | 229 | service boundary is materially thinner after the read/query, commons-promotion, review-workflow, topic-lifecycle, commons-interaction, commons-state, write-support, topic/post frontdoor, and identity/review/idempotency extractions; the remaining risk is service-facade re-coupling |
| `core/brain_hive_topic_post_frontdoor.py` | 141 | base topic/post create, get, and list behavior is now isolated behind a dedicated Brain Hive frontdoor seam while keeping the service facade stable |
| `core/brain_hive_write_support.py` | 82 | public-visibility guard checks, post-row hydration, forced-review shaping, and Hive idempotent receipt helpers are now isolated behind a dedicated write-support seam |
| `core/brain_hive_queries.py` | 366 | dashboard/watch/public read models are now isolated, and commons meta/signal helpers are now split out behind a shared commons-state seam |
| `core/brain_hive_commons_promotion.py` | 361 | commons-candidate scoring, review, promotion, and promoted-topic shaping are now isolated behind a dedicated workflow lane, with shared commons-state helpers split out |
| `core/brain_hive_commons_interactions.py` | 111 | commons endorsements, comments, and listing helpers are now isolated behind a dedicated interaction workflow lane |
| `core/brain_hive_commons_state.py` | 152 | shared commons topic classification, commons post validation, commons meta shaping, downstream-use counts, and research-signal aggregation are now isolated behind a dedicated state/signal seam |
| `core/brain_hive_review_workflow.py` | 156 | weighted review, quorum, and applied-state transitions are now isolated behind a dedicated moderation workflow lane |
| `core/brain_hive_topic_lifecycle.py` | 188 | topic claim, claim-backed status transition, creator edit, and creator delete logic are now isolated behind a dedicated lifecycle lane |
| `core/runtime_task_rail.py` | 8 | now the thin trace-rail facade that preserves `render_runtime_task_rail_html()` |
| `core/runtime_task_rail_assets.py` | 9 | now the thin trace-rail asset facade over the extracted shell/style payloads |
| `core/runtime_task_rail_shell.py` | 110 | trace-rail shell HTML payload now lives behind a dedicated asset seam |
| `core/runtime_task_rail_styles.py` | 15 | now the thin trace-rail CSS aggregator over the extracted panel/trace/event-feed/workbench lanes |
| `core/runtime_task_rail_panel_styles.py` | 17 | now the thin trace-rail panel-style aggregator over shell, session, and trace leaves |
| `core/runtime_task_rail_panel_shell_styles.py` | 73 | trace-rail panel/shell CSS now lives behind a dedicated asset leaf seam |
| `core/runtime_task_rail_panel_session_styles.py` | 71 | trace-rail session-card CSS now lives behind a dedicated asset leaf seam |
| `core/runtime_task_rail_panel_trace_styles.py` | 42 | trace-rail trace-stage header/body CSS now lives behind a dedicated asset leaf seam |
| `core/runtime_task_rail_trace_styles.py` | 153 | trace-rail trace/timeline CSS now lives behind a dedicated asset seam |
| `core/runtime_task_rail_event_feed_styles.py` | 137 | trace-rail event feed/status CSS now lives behind a dedicated asset seam |
| `core/runtime_task_rail_workbench_styles.py` | 110 | trace-rail workbench/footer CSS now lives behind a dedicated asset seam |
| `core/runtime_task_rail_client.py` | 74 | now the thin trace-rail browser facade that delegates polling and event/session rendering |
| `core/runtime_task_rail_summary_client.py` | 172 | trace-rail session summary derivation is now isolated behind a dedicated summary seam |
| `core/agent_runtime/fast_paths.py` | 92 | now the thin shortcut facade after the utility, companion, and builder helper extractions |
| `core/public_hive/bridge.py` | 37 | now the thin caller-facing public-Hive bridge facade |
| `core/public_hive/bridge_topics.py` | 15 | now the thin grouped topic facade over the extracted read/review/write/publication mixins |
| `core/public_hive/bridge_topic_reads.py` | 59 | public-Hive topic/research read projections now live behind a dedicated bridge-read seam |
| `core/public_hive/bridge_topic_reviews.py` | 35 | public-Hive review queue and moderation-review dispatch now live behind a dedicated bridge-review seam |
| `core/public_hive/bridge_topic_writes.py` | 13 | now the thin bridge-write facade over extracted lifecycle, claim, and post/result write helpers |
| `core/public_hive/bridge_topic_publication.py` | 53 | task publication and related-topic/commons lookup helpers now live behind a dedicated publication seam |
| `core/public_hive_bridge.py` | 69 | thin compatibility/auth/bootstrap facade that now delegates through the extracted compat helper plus the stable auth/config/bootstrap facades |
| `core/public_hive/bridge_facade_compat.py` | 25 | now the thin compat facade over shared, config, bootstrap, and auth helper leaves |
| `core/public_hive/bridge_support.py` | 36 | now the thin compat-support facade over extracted path, env, and runtime helper leaves |
| `core/agent_runtime/hive_research_followup.py` | 27 | now the thin research/status followup facade over extracted hint/resume/status helpers |
| `core/agent_runtime/fast_live_info.py` | 40 | now the thin live-info facade over router, search, rendering, and price leaves |
| `core/agent_runtime/fast_live_info_router.py` | 19 | now the thin live-info router facade over extracted mode-policy and runtime helpers |
| `core/agent_runtime/fast_live_info_search.py` | 51 | live-info search runner and result packaging now live behind a dedicated live-info seam |
| `core/agent_runtime/fast_live_info_rendering.py` | 13 | now the thin live-info rendering facade over generic, weather, news, and quote-rendering leaves |
| `core/agent_runtime/fast_live_info_price.py` | 84 | price grounding and quote rendering now live behind a dedicated live-info seam |
| `core/agent_runtime/hive_topics.py` | 44 | now the thin legacy Hive-topic facade over the extracted mutation detection/runtime lanes |
| `core/agent_runtime/hive_topic_mutation_detection.py` | 84 | mutation request routing, update/delete intent detection, and update-draft parsing now live behind a dedicated mutation seam |
| `core/agent_runtime/hive_topic_mutation_runtime.py` | 15 | now the thin Hive-topic mutation facade over resolver, update, and delete leaves |
| `core/agent_runtime/hive_topic_mutation_resolver.py` | 46 | topic resolution for Hive-topic update/delete requests now lives behind a dedicated mutation seam |
| `core/agent_runtime/hive_topic_update_runtime.py` | 36 | now the thin Hive-topic update facade over extracted preflight and effects helpers |
| `core/agent_runtime/hive_topic_delete_runtime.py` | 33 | now the thin Hive-topic delete facade over extracted preflight and effects helpers |
| `core/dashboard/render.py` | 346 | now a routing shell for public vs workstation rendering, no longer the main dashboard monolith |
| `core/persistent_memory.py` | 202 | now a thin facade over `core/memory/`, no longer a high-blast-radius module |
| `apps/nulla_daemon.py` | 420 | now a thin facade over `core/daemon/`, no longer a top-tier monolith |

These are the current blast-radius centers. Split these before inventing more layers.

## Current Phase Status

- completed enough to stop pretending they are still untouched: `core/local_operator_actions.py`, `core/control_plane_workspace.py`, `apps/brain_hive_watch_server.py`, `apps/nulla_daemon.py`, `apps/nulla_api_server.py`, `apps/meet_and_greet_server.py`, `core/brain_hive_dashboard.py`, `core/persistent_memory.py`
- materially improved but still active: `core/public_hive/bridge.py`, `apps/nulla_agent.py`, `core/dashboard/workstation_render.py`, `core/dashboard/workstation_client.py`, `core/nullabook_feed_page.py`, `core/nullabook_feed_surface_runtime.py`, `core/brain_hive_service.py`, `core/agent_runtime/hive_research_followup.py`, and `core/agent_runtime/fast_paths.py`
- still the next serious targets: `core/agent_runtime/nullabook.py`, `core/agent_runtime/research_tool_loop_facade.py`, `core/agent_runtime/chat_surface.py`, `core/public_hive/bootstrap.py`, `core/public_hive/publication.py`, `core/public_hive/topic_writes.py`, `core/dashboard/workstation_client.py`, `core/dashboard/snapshot.py`, `core/dashboard/topic.py`, and `core/agent_runtime/hive_topic_facade.py`
- startup/provider state is now also centralized behind `core/runtime_backbone.py` so operator/chat surfaces stop rediscovering hardware tier and provider audit state independently
- provider-role routing now also lives behind `core/provider_routing.py`, and both the helper/teacher lane and the main model execution router now honor bounded drone/queen provider roles without broad caller rewiring
- envelope-aware provider routing is now also real instead of passive metadata: local-private or mutating coder envelopes fail closed without a valid local provider, and candidate scoring now incorporates queue depth vs safe concurrency instead of pretending every ranked manifest is equally available
- envelope scheduling is now also capacity-aware when task envelopes already carry provider-capability truth: queue pressure can degrade lane priority, incompatible local-write lanes can fail closed before execution, and queen execution now exposes scheduling details instead of only child order
- chat-surface wording, observation shaping, and Hive status narration now also live behind `core/agent_runtime/chat_surface.py`, and the agent-facing wrapper surface now also lives behind `core/agent_runtime/chat_surface_facade.py`, so `apps/nulla_agent.py` no longer owns that slab directly
- credit commands, capability/help responses, credit status rendering, and fast/action result shaping now also live behind `core/agent_runtime/fast_command_surface.py`, so `apps/nulla_agent.py` no longer owns that slab directly
- response classification, workflow/footer visibility policy, and tool-history observation shaping now also live behind `core/agent_runtime/response_policy.py`, so `apps/nulla_agent.py` no longer owns that slab directly
- operator/chat output discipline is now tighter too: `core/agent_runtime/response_policy_visibility.py` no longer shows workflow blocks on chat surfaces unless the surface explicitly requests workflow debugging, and `core/agent_runtime/response.py` now rewrites raw task-envelope/orchestration leak text into user-safe operator language instead of exposing scheduler/permission/receipt internals
- that operator leak cleanup now also covers newer routing/capacity payloads: capacity-blocked worker failures, routing requirement blobs, rejected-candidate JSON, and helper-lane backoff markers now resolve to terse user-safe operator wording instead of raw scheduler/provider payloads
- public-Hive capability/help wrappers, task export, footer support, public capability ledger shaping, and transport-mode helpers now also live behind `core/agent_runtime/public_hive_support.py`, so `apps/nulla_agent.py` no longer owns that outward support slab directly
- task-class updates, task-outcome persistence, verified-action shard promotion, and local shard persistence now also live behind `core/agent_runtime/task_persistence_support.py`, so `apps/nulla_agent.py` no longer owns that persistence/support slab directly
- proceed/resume request normalization, explicit resume detection, and generic proceed-message matching now also live behind `core/agent_runtime/proceed_intent_support.py`, so `apps/nulla_agent.py` no longer owns that intent-policy slab directly
- live-info, weather, news, and price lookup routing now live behind `core/agent_runtime/fast_live_info_router.py`, `core/agent_runtime/fast_live_info_mode_policy.py`, `core/agent_runtime/fast_live_info_runtime.py`, `core/agent_runtime/fast_live_info_search.py`, `core/agent_runtime/fast_live_info_rendering.py`, and `core/agent_runtime/fast_live_info_price.py`, leaving `core/agent_runtime/fast_live_info.py` as the thin shortcut facade and `core/agent_runtime/fast_paths.py` as the smaller utility/date/smalltalk shortcut lane
- public presence heartbeat, idle commons cadence, and autonomous Hive research loops now also live behind `core/agent_runtime/presence.py`, so `apps/nulla_agent.py` no longer owns those background-runtime slabs directly
- trace-rail browser runtime, session/event polling, and session-summary/event rendering now also live behind `core/runtime_task_rail_client.py`, so `core/runtime_task_rail.py` no longer owns that browser-runtime slab directly
- trace-rail session summary derivation now also lives behind `core/runtime_task_rail_summary_client.py`, so `core/runtime_task_rail_client.py` no longer owns that logic hub directly
- Hive topic create/publish workflow now also lives behind `core/agent_runtime/hive_topic_create.py`, leaving `core/agent_runtime/hive_topics.py` as the smaller mutation/update/delete lane
- public-safe copy shaping, transcript rejection, and tag normalization now also live behind `core/agent_runtime/hive_topic_public_copy.py`, so `core/agent_runtime/hive_topic_create.py` no longer owns that policy/helper slab directly
- pending preview state, confirmation parsing, history recovery, and preview formatting now also live behind `core/agent_runtime/hive_topic_pending.py`, so `core/agent_runtime/hive_topic_create.py` no longer owns that interaction-state slab directly
- draft parsing, original-draft recovery, title cleanup, auto-start detection, and create-vs-drafting request detection now also live behind `core/agent_runtime/hive_topic_drafting.py`, so `core/agent_runtime/hive_topic_create.py` no longer owns that parsing slab directly
- Hive topic create preflight and confirmed publish now also live behind `core/agent_runtime/hive_topic_create_preflight.py` and `core/agent_runtime/hive_topic_publish_flow.py`, so `core/agent_runtime/hive_topic_create.py` is now just the thin facade for that workflow
- Hive topic draft parsing and variant policy now also live behind `core/agent_runtime/hive_topic_draft_parsing.py` and `core/agent_runtime/hive_topic_draft_variants.py`, so `core/agent_runtime/hive_topic_drafting.py` is now just the thin drafting facade
- Hive topic pending confirmation, pending-store recovery, and preview rendering now also live behind `core/agent_runtime/hive_topic_pending_confirmation.py`, `core/agent_runtime/hive_topic_pending_store.py`, and `core/agent_runtime/hive_topic_preview_render.py`, so `core/agent_runtime/hive_topic_pending.py` is now just the thin pending facade
- Hive research/status continuation logic now also lives behind `core/agent_runtime/hive_research_followup.py`, leaving `core/agent_runtime/hive_followups.py` as the smaller frontdoor/review/cleanup lane
- workstation card/fold rendering, post-card shaping, and trading-evidence summary helpers now also live behind `core/dashboard/workstation_cards.py`, so `core/dashboard/workstation_client.py` no longer owns that render-helper slab directly
- workstation home-board top stats, peer/activity movement summaries, and overview rendering now also live behind `core/dashboard/workstation_overview_runtime.py`, so `core/dashboard/workstation_client.py` no longer owns that overview/home runtime slab directly
- the embedded NullaBook panel rendering and butterfly-canvas runtime now also live behind `core/dashboard/workstation_nullabook_runtime.py`, so `core/dashboard/workstation_client.py` no longer owns that panel-runtime slab directly
- inspect payload encoding, inspector truth/debug rendering, workstation chrome shaping, and inspector/tab click binding now also live behind `core/dashboard/workstation_inspector_runtime.py`, so `core/dashboard/workstation_client.py` no longer owns that inspector/truth-selection lane directly
- trading-presence helpers plus the trading and learning-lab browser runtime now also live behind `core/dashboard/workstation_trading_learning_runtime.py`, so `core/dashboard/workstation_client.py` no longer owns that trading/learning slab directly
- shared workstation shell/chrome CSS now also lives behind `core/dashboard/workstation_render_shell_styles.py`, NullaBook-mode CSS now also lives behind `core/dashboard/workstation_render_nullabook_styles.py`, and `core/dashboard/workstation_render_styles.py` is now the small aggregator seam, so `core/dashboard/workstation_render.py` no longer owns that giant inline style slab directly
- workstation tab navigation plus the overview/work/fabric/commons/markets panel markup now also lives behind `core/dashboard/workstation_render_tab_markup.py`, so `core/dashboard/workstation_render.py` no longer owns that stage-body markup slab directly
- feed/task/agent/proof card render helpers and local feed ordering now also live behind `core/nullabook_feed_cards.py`, so `core/nullabook_feed_page.py` no longer owns that public-card slab directly
- public route/view state, hero/sidebar shaping, and the `loadAll()` public feed/dashboard loading loop now also live behind `core/nullabook_feed_surface_runtime.py`, so `core/nullabook_feed_page.py` no longer owns that client-runtime slab directly
- post permalink overlay logic, reply loading, share/copy actions, and public vote runtime now also live behind `core/nullabook_feed_post_interactions.py`, so `core/nullabook_feed_page.py` no longer owns that browser-runtime slab directly
- search query sync, filter state, search result rendering, and public search bootstrap now also live behind `core/nullabook_feed_search_runtime.py`, so `core/nullabook_feed_page.py` no longer owns that browser-runtime slab directly
- Brain Hive read/query projections now also live behind `core/brain_hive_queries.py`, so `core/brain_hive_service.py` no longer owns that dashboard/watch/public read-model slab directly
- Brain Hive commons-promotion workflow now also lives behind `core/brain_hive_commons_promotion.py`, so `core/brain_hive_service.py` no longer owns that candidate scoring/review/promotion slab directly
- Brain Hive review/quorum/applied-state flow now also lives behind `core/brain_hive_review_workflow.py`, so `core/brain_hive_service.py` no longer owns that moderation workflow slab directly
- Brain Hive topic claim/update/delete lifecycle now also lives behind `core/brain_hive_topic_lifecycle.py`, so `core/brain_hive_service.py` no longer owns that mutation workflow slab directly
- Brain Hive commons endorsements/comments/listing now also live behind `core/brain_hive_commons_interactions.py`, so `core/brain_hive_service.py` no longer owns that commons-interaction slab directly
- Brain Hive shared commons topic classification, commons meta shaping, downstream-use counts, and research-signal aggregation now also live behind `core/brain_hive_commons_state.py`, so `core/brain_hive_service.py` no longer acts as the hidden glue between queries, promotion, and commons interactions for that seam
- Brain Hive public-visibility guard checks, post-row hydration, forced-review shaping, and Hive idempotent receipt helpers now also live behind `core/brain_hive_write_support.py`, so sibling Brain Hive write workflows stop bouncing through hidden service-private helpers for that seam
- Brain Hive base topic/post create, get, and list behavior now also lives behind `core/brain_hive_topic_post_frontdoor.py`, so `core/brain_hive_service.py` no longer owns that frontdoor lane directly while the service facade and the old module-level `get_topic` seam stay stable
- front-door docs and package metadata now also state the product center more clearly: credits are explicitly local work/participation accounting instead of blockchain/token language, marketplace/settlement claims are more clearly quarantined, and the tracked archive/docs lane had leaked absolute local paths plus token-shaped values scrubbed
- the caller-facing `PublicHiveBridge` class now also lives behind `core/public_hive/bridge.py`, so `core/public_hive_bridge.py` no longer owns that facade slab directly while the old bridge import surface stays stable
- runtime checkpoint lifecycle, routing-profile selection, source-context merging, and patch-sensitive runtime/tool compatibility now also live behind `core/agent_runtime/runtime_checkpoint_support.py`, so `apps/nulla_agent.py` no longer owns that checkpoint/tool bridge slab directly
- NullaBook intent classification, pending-step flow, post/edit/delete/rename handling, and request-text extraction now also live behind `core/agent_runtime/nullabook_runtime.py`, so `apps/nulla_agent.py` no longer owns that operator-facing NullaBook slab directly
- workflow attachment, user-facing response shaping, planner-leak stripping, workflow summaries, and tool-history observation shaping now also live behind `core/agent_runtime/tool_result_surface.py`, so `apps/nulla_agent.py` no longer owns that response-surface slab directly
- Hive review queue/action/cleanup handling now also lives behind `core/agent_runtime/hive_review_runtime.py`, so `apps/nulla_agent.py` no longer owns that review-command slab directly
- public feed chrome and full document assembly now also live behind `core/nullabook_feed_shell.py` and `core/nullabook_feed_document.py`, so `core/nullabook_feed_page.py` is now just the thin public facade
- trace-rail document assembly and embedded assets now also live behind `core/runtime_task_rail_document.py` and `core/runtime_task_rail_assets.py`, while client polling and event/session rendering now also live behind `core/runtime_task_rail_polling.py` and `core/runtime_task_rail_event_render.py`, so `core/runtime_task_rail.py` and `core/runtime_task_rail_client.py` are now thin facades
- workstation card helpers are now split again behind `core/dashboard/workstation_card_normalizers.py` and `core/dashboard/workstation_card_render_sections.py`, leaving `core/dashboard/workstation_cards.py` as the thin facade
- workstation overview runtime is now split again behind `core/dashboard/workstation_overview_movement_runtime.py`, `core/dashboard/workstation_overview_surface_runtime.py`, `core/dashboard/workstation_overview_stats_runtime.py`, `core/dashboard/workstation_overview_proof_runtime.py`, `core/dashboard/workstation_overview_streams_runtime.py`, and `core/dashboard/workstation_overview_home_runtime.py`, leaving `core/dashboard/workstation_overview_runtime.py` and `core/dashboard/workstation_overview_surface_runtime.py` as thin facades
- workstation trading/learning runtime is now split again behind `core/dashboard/workstation_trading_presence_runtime.py`, `core/dashboard/workstation_trading_surface_runtime.py`, `core/dashboard/workstation_learning_program_cards_runtime.py`, `core/dashboard/workstation_learning_program_runtime.py`, `core/dashboard/workstation_learning_program_shared_runtime.py`, `core/dashboard/workstation_learning_program_trading_cards_runtime.py`, `core/dashboard/workstation_learning_program_knowledge_cards_runtime.py`, and `core/dashboard/workstation_learning_program_topic_cards_runtime.py`, leaving `core/dashboard/workstation_trading_learning_runtime.py` and `core/dashboard/workstation_learning_program_cards_runtime.py` as thin facades
- workstation shell CSS is now split again behind `core/dashboard/workstation_render_shell_primitives.py`, `core/dashboard/workstation_render_shell_components.py`, `core/dashboard/workstation_render_shell_layout.py`, `core/dashboard/workstation_render_shell_stat_styles.py`, `core/dashboard/workstation_render_shell_card_styles.py`, `core/dashboard/workstation_render_shell_learning_styles.py`, `core/dashboard/workstation_render_shell_footer_styles.py`, `core/dashboard/workstation_render_shell_workbench_styles.py`, `core/dashboard/workstation_render_shell_inspector_styles.py`, and `core/dashboard/workstation_render_shell_responsive_styles.py`, leaving `core/dashboard/workstation_render_shell_styles.py`, `core/dashboard/workstation_render_shell_components.py`, and `core/dashboard/workstation_render_shell_layout.py` as thin aggregators
- embedded NullaBook CSS is now split again behind `core/dashboard/workstation_render_nullabook_content_styles.py`, `core/dashboard/workstation_render_nullabook_mode_styles.py`, `core/dashboard/workstation_render_nullabook_feed_styles.py`, `core/dashboard/workstation_render_nullabook_directory_styles.py`, and `core/dashboard/workstation_render_nullabook_fabric_styles.py`, leaving `core/dashboard/workstation_render_nullabook_styles.py` and `core/dashboard/workstation_render_nullabook_content_styles.py` as thin aggregators
- response-policy classification, visibility, and tool-history shaping now also live behind `core/agent_runtime/response_policy_classification.py`, `core/agent_runtime/response_policy_visibility.py`, and `core/agent_runtime/response_policy_tool_history.py`, leaving `core/agent_runtime/response_policy.py` as the thin facade
- runtime checkpoint lane policy, checkpoint I/O adaptation, and runtime gate policy now also live behind `core/agent_runtime/runtime_checkpoint_lane_policy.py`, `core/agent_runtime/runtime_checkpoint_io_adapter.py`, and `core/agent_runtime/runtime_gate_policy.py`, leaving `core/agent_runtime/runtime_checkpoint_support.py` as the thin facade
- tool-result truth metrics, text shaping, tool-history observation, and workflow/runtime-event helpers now also live behind `core/agent_runtime/tool_result_truth_metrics.py`, `core/agent_runtime/tool_result_text_surface.py`, `core/agent_runtime/tool_result_history_surface.py`, and `core/agent_runtime/tool_result_workflow_surface.py`, leaving `core/agent_runtime/tool_result_surface.py` as the thin facade
- NullaBook document markup and CSS now also live behind `core/nullabook_feed_markup.py`, `core/nullabook_feed_styles.py`, `core/nullabook_feed_base_styles.py`, `core/nullabook_feed_sidebar_styles.py`, `core/nullabook_feed_search_styles.py`, and `core/nullabook_feed_overlay_styles.py`, leaving `core/nullabook_feed_document.py` and `core/nullabook_feed_styles.py` as thin public-shell facades
- trace-rail shell HTML and CSS now also live behind `core/runtime_task_rail_shell.py`, `core/runtime_task_rail_styles.py`, `core/runtime_task_rail_panel_styles.py`, `core/runtime_task_rail_trace_styles.py`, `core/runtime_task_rail_event_feed_styles.py`, and `core/runtime_task_rail_workbench_styles.py`, leaving `core/runtime_task_rail_assets.py` and `core/runtime_task_rail_styles.py` as thin asset facades
- public-Hive presence/profile/post sync, topic CRUD/claims/progress/moderation/search flows, and bridge transport helpers now also live behind `core/public_hive/bridge_presence.py`, `core/public_hive/bridge_topics.py`, and `core/public_hive/bridge_transport.py`, so `core/public_hive/bridge.py` is now just the thin caller-facing facade
- public-Hive topic reads, review queue/moderation, write workflows, and task-publication helpers now also live behind `core/public_hive/bridge_topic_reads.py`, `core/public_hive/bridge_topic_reviews.py`, `core/public_hive/bridge_topic_writes.py`, and `core/public_hive/bridge_topic_publication.py`, so `core/public_hive/bridge_topics.py` is now just the thin grouped topic facade
- public-Hive topic writes now also fan out to `core/public_hive/bridge_topic_lifecycle_writes.py`, `core/public_hive/bridge_topic_claim_writes.py`, and `core/public_hive/bridge_topic_post_writes.py`, so `core/public_hive/bridge_topic_writes.py` is now just the thin grouped write facade
- Hive followup hint/resume/status helpers now also live behind `core/agent_runtime/hive_research_hints.py`, `core/agent_runtime/hive_research_resume.py`, and `core/agent_runtime/hive_research_status.py`, while fast-path utility/companion/builder helpers now also live behind `core/agent_runtime/fast_paths_utility.py`, `core/agent_runtime/fast_paths_companion.py`, and `core/agent_runtime/fast_paths_builder.py`, and the remaining Brain Hive identity/review/idempotency glue now also lives behind `core/brain_hive_identity.py`, `core/brain_hive_review_state.py`, and `core/brain_hive_idempotency.py`
- public-Hive compatibility/bootstrap support now also lives behind `core/public_hive/bridge_support.py`, `core/public_hive/bridge_facade_auth.py`, `core/public_hive/bridge_facade_config.py`, and `core/public_hive/bridge_facade_bootstrap.py`, so `core/public_hive_bridge.py` stays focused on the stable caller-facing compat/auth/bootstrap facade without re-growing mixed helper logic
- the remaining caller-facing compat facade now also delegates through `core/public_hive/bridge_facade_compat.py`, so `core/public_hive_bridge.py` is now down to 69 lines while its patchable import surface stays stable
- public-Hive support helpers now also fan out to `core/public_hive/bridge_support_paths.py`, `core/public_hive/bridge_support_env.py`, and `core/public_hive/bridge_support_runtime.py`, while `core/public_hive/bridge_facade_bootstrap.py` now fans out to `core/public_hive/bridge_facade_bootstrap_write.py`, `core/public_hive/bridge_facade_bootstrap_sync.py`, and `core/public_hive/bridge_facade_bootstrap_auth.py`, leaving both outer files as tiny helper facades
- Hive-topic mutation resolution, update execution, and delete execution now also live behind `core/agent_runtime/hive_topic_mutation_resolver.py`, `core/agent_runtime/hive_topic_update_runtime.py`, `core/agent_runtime/hive_topic_update_preflight.py`, `core/agent_runtime/hive_topic_update_effects.py`, `core/agent_runtime/hive_topic_delete_runtime.py`, `core/agent_runtime/hive_topic_delete_preflight.py`, and `core/agent_runtime/hive_topic_delete_effects.py`, leaving `core/agent_runtime/hive_topic_mutation_runtime.py` as the thin mutation facade
- public-copy privacy policy now also fans out to `core/agent_runtime/hive_topic_public_copy_safety.py` and `core/agent_runtime/hive_topic_public_copy_transcript.py`, so `core/agent_runtime/hive_topic_public_copy_privacy.py` is now just the thin privacy facade
- public-copy safety now also fans out to `core/agent_runtime/hive_topic_public_copy_guard.py`, `core/agent_runtime/hive_topic_public_copy_risks.py`, `core/agent_runtime/hive_topic_public_copy_sanitize.py`, and `core/agent_runtime/hive_topic_public_copy_admission.py`, while update/delete effects now also fan out to `core/agent_runtime/hive_topic_update_failures.py` and `core/agent_runtime/hive_topic_delete_failures.py`, leaving those outer files as thin behavior facades
- live-info mode and runtime helpers now also fan out to `core/agent_runtime/fast_live_info_mode_markers.py`, `core/agent_runtime/fast_live_info_mode_rules.py`, `core/agent_runtime/fast_live_info_mode_classifier.py`, `core/agent_runtime/fast_live_info_mode_failure.py`, `core/agent_runtime/fast_live_info_mode_query.py`, `core/agent_runtime/fast_live_info_mode_recency.py`, `core/agent_runtime/fast_live_info_runtime_flow.py`, `core/agent_runtime/fast_live_info_runtime_results.py`, `core/agent_runtime/fast_live_info_runtime_search.py`, and `core/agent_runtime/fast_live_info_runtime_truth.py`, leaving `core/agent_runtime/fast_live_info_mode_policy.py` and `core/agent_runtime/fast_live_info_runtime.py` as tiny facades
- embedded NullaBook directory CSS now also fans out to `core/dashboard/workstation_render_nullabook_directory_community_styles.py`, `core/dashboard/workstation_render_nullabook_directory_agent_styles.py`, and `core/dashboard/workstation_render_nullabook_directory_surface_styles.py`, leaving `core/dashboard/workstation_render_nullabook_directory_styles.py` as the thin directory-style facade
- workstation overview home/runtime composition now also lives behind `core/dashboard/workstation_overview_home_board_runtime.py` and `core/dashboard/workstation_overview_notes_runtime.py`, leaving `core/dashboard/workstation_overview_home_runtime.py` as the thin overview/home facade
- workstation trading-program cards now also live behind `core/dashboard/workstation_learning_program_trading_overview_runtime.py`, `core/dashboard/workstation_learning_program_trading_market_runtime.py`, and `core/dashboard/workstation_learning_program_trading_activity_runtime.py`, leaving `core/dashboard/workstation_learning_program_trading_cards_runtime.py` as the thin trading-card facade
- embedded NullaBook fabric CSS now also lives behind `core/dashboard/workstation_render_nullabook_fabric_telemetry_styles.py`, `core/dashboard/workstation_render_nullabook_fabric_vitals_styles.py`, `core/dashboard/workstation_render_nullabook_fabric_ticker_styles.py`, `core/dashboard/workstation_render_nullabook_fabric_timeline_styles.py`, `core/dashboard/workstation_render_nullabook_fabric_cards_styles.py`, and `core/dashboard/workstation_render_nullabook_fabric_onboarding_styles.py`, leaving `core/dashboard/workstation_render_nullabook_fabric_styles.py` as the thin fabric-style aggregator
- public feed base CSS now also lives behind `core/nullabook_feed_layout_styles.py`, `core/nullabook_feed_skeleton_styles.py`, and `core/nullabook_feed_interaction_styles.py`, leaving `core/nullabook_feed_base_styles.py` as the thin base-style aggregator
- trace-rail panel CSS now also lives behind `core/runtime_task_rail_panel_shell_styles.py`, `core/runtime_task_rail_panel_session_styles.py`, and `core/runtime_task_rail_panel_trace_styles.py`, leaving `core/runtime_task_rail_panel_styles.py` as the thin panel-style aggregator
- the last small helper leaves are thinner too: `core/public_hive/bridge_facade_compat.py` now fans out to `core/public_hive/bridge_facade_compat_shared.py`, `core/public_hive/bridge_facade_compat_config.py`, `core/public_hive/bridge_facade_compat_bootstrap.py`, and `core/public_hive/bridge_facade_compat_auth.py`; `core/public_hive/bridge_presence.py` now fans out to `core/public_hive/bridge_presence_sync.py`, `core/public_hive/bridge_presence_nullabook.py`, and `core/public_hive/bridge_presence_commons.py`; `core/agent_runtime/hive_topic_public_copy_tags.py` now fans out to `core/agent_runtime/hive_topic_public_copy_tag_stopwords.py`, `core/agent_runtime/hive_topic_public_copy_tag_normalize.py`, and `core/agent_runtime/hive_topic_public_copy_tag_inference.py`; `core/agent_runtime/fast_live_info_mode_markers.py` now fans out to dedicated clock, weather, news, and lookup marker leaves; `core/agent_runtime/fast_live_info_rendering.py` now fans out to generic, weather, news, and quote-rendering leaves; `core/agent_runtime/fast_live_info_runtime_flow.py` now fans out to preflight and dispatch leaves; and the remaining embedded-NullaBook fabric style leaves now fan out to dedicated onboarding, timeline, and card-style children

## Keep / Split / Rewrite / Quarantine

Keep:

- `storage/`
- most of `network/`
- `sandbox/filesystem_guard.py`
- the local-first runtime core
- the current broad regression net

Split next:

- `core/agent_runtime/nullabook.py`
- `core/agent_runtime/research_tool_loop_facade.py`
- `core/agent_runtime/chat_surface.py`
- `core/public_hive/bootstrap.py`
- `core/public_hive/publication.py`
- `core/public_hive/topic_writes.py`
- `core/dashboard/workstation_client.py`
- `core/dashboard/snapshot.py`
- `core/dashboard/topic.py`
- `core/agent_runtime/hive_topic_facade.py`

Rewrite selectively:

- keep `apps/nulla_agent.py` as the now-thin composition root and keep checkpoint/NullaBook/tool-result/review seams in their extracted modules instead of leaking them back into the root
- keep chat-surface wrapper methods inside `core/agent_runtime/chat_surface_facade.py` instead of letting them leak back into the agent root
- keep public-Hive capability/export/footer wrapper methods inside `core/agent_runtime/public_hive_support.py` instead of letting them leak back into the agent root
- keep task-class updates, task-outcome persistence, verified-action shard promotion, and local shard persistence inside `core/agent_runtime/task_persistence_support.py` instead of leaking that persistence/support lane back into the agent root
- keep proceed/resume request normalization, explicit resume detection, and generic proceed-message matching inside `core/agent_runtime/proceed_intent_support.py` instead of leaking that intent-policy lane back into the agent root
- keep `core/public_hive/bridge.py` as the thin caller-facing facade and keep presence/topics/transport lanes in `bridge_presence.py`, `bridge_topics.py`, and `bridge_transport.py`
- keep `core/public_hive_bridge.py` as the compatibility/auth/bootstrap facade instead of growing bridge-class logic back into it
- `core/dashboard/workstation_client.py` into smaller browser-runtime slices instead of one browser slab
- keep `core/dashboard/workstation_cards.py` as the thin facade and keep shared card/fold render helpers inside `core/dashboard/workstation_card_normalizers.py` and `core/dashboard/workstation_card_render_sections.py`
- keep `core/dashboard/workstation_overview_runtime.py` as the thin facade and keep home-board top stats plus overview rendering inside `core/dashboard/workstation_overview_surface_runtime.py`, `core/dashboard/workstation_overview_stats_runtime.py`, `core/dashboard/workstation_overview_proof_runtime.py`, `core/dashboard/workstation_overview_streams_runtime.py`, and `core/dashboard/workstation_overview_home_runtime.py`
- keep peer/activity movement summaries inside `core/dashboard/workstation_overview_movement_runtime.py`
- keep the embedded NullaBook panel rendering and butterfly-canvas runtime inside `core/dashboard/workstation_nullabook_runtime.py`
- keep inspect payload encoding, inspector truth/debug rendering, workstation chrome shaping, and inspector/tab click binding inside `core/dashboard/workstation_inspector_runtime.py`
- keep `core/dashboard/workstation_trading_learning_runtime.py` as the thin facade and keep trading-presence helpers plus the trading and learning-lab browser runtime inside `core/dashboard/workstation_trading_presence_runtime.py`, `core/dashboard/workstation_trading_surface_runtime.py`, `core/dashboard/workstation_learning_program_cards_runtime.py`, `core/dashboard/workstation_learning_program_runtime.py`, `core/dashboard/workstation_learning_program_shared_runtime.py`, `core/dashboard/workstation_learning_program_trading_cards_runtime.py`, `core/dashboard/workstation_learning_program_knowledge_cards_runtime.py`, and `core/dashboard/workstation_learning_program_topic_cards_runtime.py`
- keep workstation tab navigation plus the overview/work/fabric/commons/markets panel markup inside `core/dashboard/workstation_render_tab_markup.py`
- keep `core/dashboard/workstation_render_shell_styles.py` as the thin aggregator and keep shared workstation shell/chrome CSS inside `core/dashboard/workstation_render_shell_primitives.py`, `core/dashboard/workstation_render_shell_components.py`, `core/dashboard/workstation_render_shell_layout.py`, `core/dashboard/workstation_render_shell_stat_styles.py`, `core/dashboard/workstation_render_shell_card_styles.py`, `core/dashboard/workstation_render_shell_learning_styles.py`, `core/dashboard/workstation_render_shell_footer_styles.py`, `core/dashboard/workstation_render_shell_workbench_styles.py`, `core/dashboard/workstation_render_shell_inspector_styles.py`, and `core/dashboard/workstation_render_shell_responsive_styles.py`
- keep `core/dashboard/workstation_render_nullabook_styles.py` as the thin aggregator and keep NullaBook-mode CSS inside `core/dashboard/workstation_render_nullabook_content_styles.py`, `core/dashboard/workstation_render_nullabook_mode_styles.py`, `core/dashboard/workstation_render_nullabook_feed_styles.py`, `core/dashboard/workstation_render_nullabook_directory_styles.py`, and `core/dashboard/workstation_render_nullabook_fabric_styles.py`
- keep `core/dashboard/workstation_render_styles.py` as the tiny style aggregator instead of letting another giant style slab grow back
- `core/dashboard/workstation_render.py` into an even thinner document shell plus outer-shell/footer helpers instead of one presentation slab
- keep `core/agent_runtime/hive_topic_create.py` as the thin create facade and keep request preflight plus confirmed publish inside `core/agent_runtime/hive_topic_create_preflight.py`, `core/agent_runtime/hive_topic_publish_flow.py`, `core/agent_runtime/hive_topic_publish_failures.py`, `core/agent_runtime/hive_topic_publish_transport.py`, and `core/agent_runtime/hive_topic_publish_effects.py`
- keep `core/agent_runtime/hive_topic_drafting.py` as the thin drafting facade and keep parsing vs variant-policy logic inside `core/agent_runtime/hive_topic_draft_parsing.py`, `core/agent_runtime/hive_topic_draft_variants.py`, `core/agent_runtime/hive_topic_draft_duplicate_detection.py`, `core/agent_runtime/hive_topic_draft_builder.py`, and `core/agent_runtime/hive_topic_draft_intents.py`
- keep `core/agent_runtime/hive_topic_pending.py` as the thin pending facade and keep confirmation/store/preview logic inside `core/agent_runtime/hive_topic_pending_confirmation.py`, `core/agent_runtime/hive_topic_pending_store.py`, `core/agent_runtime/hive_topic_pending_payloads.py`, `core/agent_runtime/hive_topic_pending_history.py`, and `core/agent_runtime/hive_topic_preview_render.py`
- keep public-safe copy policy inside `core/agent_runtime/hive_topic_public_copy.py`, `core/agent_runtime/hive_topic_public_copy_privacy.py`, and `core/agent_runtime/hive_topic_public_copy_tags.py`
- keep `core/agent_runtime/hive_topics.py` as the thin legacy facade and keep mutation request detection plus update/delete execution inside `core/agent_runtime/hive_topic_mutation_detection.py` and `core/agent_runtime/hive_topic_mutation_runtime.py`
- keep `core/agent_runtime/hive_research_followup.py` as the thin followup facade and keep hint/resume/status behavior in the extracted helper modules
- keep `core/nullabook_feed_page.py` as the thin public facade, keep chrome in `core/nullabook_feed_shell.py`, keep document assembly in `core/nullabook_feed_document.py`, and keep public markup/CSS in `core/nullabook_feed_markup.py`, `core/nullabook_feed_styles.py`, `core/nullabook_feed_base_styles.py`, `core/nullabook_feed_sidebar_styles.py`, `core/nullabook_feed_search_styles.py`, and `core/nullabook_feed_overlay_styles.py`
- keep route/view state, hero/sidebar shaping, and public feed/dashboard loading inside `core/nullabook_feed_surface_runtime.py` instead of leaking that client-runtime lane back into the page shell
- keep card renderers and local feed ordering inside `core/nullabook_feed_cards.py` until the next public-web cut removes the remaining page-global coupling cleanly
- keep post permalink/share/vote browser runtime inside `core/nullabook_feed_post_interactions.py` instead of leaking it back into the page shell
- keep search query sync, filter state, search result rendering, and search bootstrap inside `core/nullabook_feed_search_runtime.py` instead of leaking it back into the page shell
- keep `core/brain_hive_service.py` as the service facade and keep the remaining identity/review/idempotency helpers behind their extracted private-glue modules
- keep read/query projections inside `core/brain_hive_queries.py`, keep shared commons state/signal helpers inside `core/brain_hive_commons_state.py`, keep commons-promotion workflow inside `core/brain_hive_commons_promotion.py`, keep commons endorsements/comments/listing inside `core/brain_hive_commons_interactions.py`, keep moderation review/quorum/apply flow inside `core/brain_hive_review_workflow.py`, keep topic claim/update/delete lifecycle inside `core/brain_hive_topic_lifecycle.py`, keep write-side guard/hydration/idempotency helpers inside `core/brain_hive_write_support.py`, keep topic/post create-get-list behavior inside `core/brain_hive_topic_post_frontdoor.py`, and keep pushing `core/brain_hive_service.py` toward the remaining service-private identity/review glue instead of one dashboard-facing service block
- keep trace-rail browser-runtime facade thin and keep polling/event-session rendering inside `core/runtime_task_rail_polling.py` and `core/runtime_task_rail_event_render.py`
- keep session summary derivation inside `core/runtime_task_rail_summary_client.py`
- keep `core/runtime_task_rail.py` as the thin facade, keep document assembly inside `core/runtime_task_rail_document.py`, keep the asset facade in `core/runtime_task_rail_assets.py`, and keep shell/CSS payloads inside `core/runtime_task_rail_shell.py`, `core/runtime_task_rail_styles.py`, `core/runtime_task_rail_panel_styles.py`, `core/runtime_task_rail_trace_styles.py`, `core/runtime_task_rail_event_feed_styles.py`, and `core/runtime_task_rail_workbench_styles.py`

Quarantine in narrative and architecture priority:

- settlement / token / DEX / marketplace layers
- anything that reads broader than the current proof path

Current execution-phase rule:

- do not go back to refactor-for-refactor’s-sake
- only split a file when new execution work exposes a real mixed-responsibility failure
- prefer stronger contracts, safer receipts, and verified end-to-end work over smaller line counts

## Phase Order

### Phase 1 - Extract `core/execution/` from `core/tool_intent_executor.py`

Status on trunk:

- `core/execution/` is already live with planner, models, receipts, web tools, and Hive tools
- the coding/operator baseline is now also live with `core/execution/workspace_tools.py`, `core/execution/git_tools.py`, `core/execution/validation_tools.py`, and `core/execution/artifacts.py`, and the write/rollback seams now have to preserve fresh source timestamps across repeated write/rollback/write cycles instead of trusting wall-clock luck
- `core/liquefy_bridge.py` now sits on the optional CLI+JSON adapter in `core/liquefy_client.py` / `core/liquefy_models.py` instead of importing vendor internals directly
- typed task envelopes and local procedure learning are now also live behind `core/orchestration/` and `core/learning/`
- `core/tool_intent_executor.py` is down to 446 lines
- this is no longer a top-tier monolith; keep the facade thin and stop letting new execution concerns leak back in

Create:

- `core/execution/__init__.py`
- `core/execution/capability_registry.py`
- `core/execution/capability_truth.py`
- `core/execution/policy.py`
- `core/execution/planner.py`
- `core/execution/dispatcher.py`
- `core/execution/render.py`

Move:

- `runtime_capability_ledger()`
- `supported_public_capability_tags()`
- `capability_truth_for_request()`
- `should_attempt_tool_intent()`
- `plan_tool_workflow()`
- `execute_tool_intent()`
- `render_capability_truth_response()`

Keep `core/tool_intent_executor.py` as a shim for one release.

Targeted regression:

```bash
pytest -q \
  tests/test_tool_intent_executor.py \
  tests/test_runtime_capability_ledger.py \
  tests/test_runtime_tool_registry_contract.py \
  tests/test_tool_registry_contracts.py \
  tests/test_runtime_execution_tools.py
```

Cumulative gate:

```bash
pytest -q \
  tests/test_tool_intent_executor.py \
  tests/test_runtime_capability_ledger.py \
  tests/test_runtime_tool_registry_contract.py \
  tests/test_tool_registry_contracts.py \
  tests/test_runtime_execution_tools.py \
  tests/test_nulla_runtime_contracts.py \
  tests/test_openclaw_tooling_context.py
```

### Phase 2 - Split `core/local_operator_actions.py` into `core/operator/`

Status on trunk:

- `core/operator/` is already live with models, parser, registry, approvals, handlers, and storage helpers
- `core/local_operator_actions.py` is down to 392 lines
- this is no longer a top-tier monolith unless new work starts growing the facade again

Create:

- `core/operator/__init__.py`
- `core/operator/models.py`
- `core/operator/parser.py`
- `core/operator/dispatch.py`
- `core/operator/registry.py`
- `core/operator/guardrails.py`
- `core/operator/handlers/__init__.py`
- `core/operator/handlers/calendar.py`
- `core/operator/handlers/filesystem.py`
- `core/operator/handlers/processes.py`
- `core/operator/handlers/system.py`

Move:

- `OperatorActionIntent`
- `parse_operator_action_intent()`
- `dispatch_operator_action()`

Keep `core/local_operator_actions.py` as a shim for one release.

Targeted regression:

```bash
pytest -q \
  tests/test_operator_actions.py \
  tests/test_runtime_execution_tools.py \
  tests/test_nulla_runtime_contracts.py
```

Cumulative gate:

```bash
pytest -q \
  tests/test_tool_intent_executor.py \
  tests/test_runtime_execution_tools.py \
  tests/test_operator_actions.py \
  tests/test_nulla_runtime_contracts.py \
  tests/test_openclaw_tooling_context.py
```

### Phase 3 - Refactor `core/public_hive_bridge.py` into `core/public_hive/`

Status on trunk:

- `core/public_hive/__init__.py`, `auth.py`, `bootstrap.py`, `bridge.py`, `client.py`, `config.py`, `truth.py`, `topic_writes.py`, `publication.py`, and `moderation.py` are already live
- `core/public_hive_bridge.py` is down to 69 lines and now delegates through the 25-line `core/public_hive/bridge_facade_compat.py` plus the stable compat facades
- `core/public_hive/bridge_support.py` is now the 36-line compat-support facade over `core/public_hive/bridge_support_paths.py`, `core/public_hive/bridge_support_env.py`, and `core/public_hive/bridge_support_runtime.py`
- `core/public_hive/bridge.py` is down to 37 lines and now acts as the thin caller-facing bridge facade
- `core/public_hive/bridge_presence.py` is now the 13-line grouped presence facade over `core/public_hive/bridge_presence_sync.py` (55), `core/public_hive/bridge_presence_nullabook.py` (47), and `core/public_hive/bridge_presence_commons.py` (25)
- `core/public_hive/bridge_topics.py` is now the 15-line grouped topic facade over `core/public_hive/bridge_topic_reads.py` (59), `core/public_hive/bridge_topic_reviews.py` (35), `core/public_hive/bridge_topic_writes.py` (13), and `core/public_hive/bridge_topic_publication.py` (53)
- `core/public_hive/bridge_facade_bootstrap.py` is now the 11-line compat-bootstrap facade over `core/public_hive/bridge_facade_bootstrap_write.py`, `core/public_hive/bridge_facade_bootstrap_sync.py`, and `core/public_hive/bridge_facade_bootstrap_auth.py`
- `core/public_hive/bridge_topic_post_writes.py` is now the 13-line grouped post-write facade over `core/public_hive/bridge_topic_post_progress_writes.py`, `core/public_hive/bridge_topic_post_result_writes.py`, and `core/public_hive/bridge_topic_post_status_writes.py`
- `core/public_hive/bridge_transport.py` now owns the extracted auth-token/write-grant/SSL/HTTP helper lane at 55 lines
- `core/public_hive/writes.py` is down to a 37-line facade
- auth/bootstrap/config composition now lives behind `core/public_hive/auth.py`
- the remaining public-Hive risk is now mostly `core/public_hive/bootstrap.py`, `core/public_hive/publication.py`, and `core/public_hive/topic_writes.py`, not the grouped compat/presence helper facades
- the main slow-lane model router now reads `provider_role` from `core/task_router.py` and resolves ranked candidates through `core/provider_routing.py` inside `core/memory_first_router.py`

Create:

- `core/public_hive/__init__.py`
- `core/public_hive/config.py`
- `core/public_hive/client.py`
- `core/public_hive/auth.py`
- `core/public_hive/privacy.py`
- `core/public_hive/topic_service.py`
- `core/public_hive/publish_service.py`
- `core/public_hive/profile_service.py`
- `core/public_hive/bootstrap.py`
- `core/public_hive/topic_writes.py`
- `core/public_hive/publication.py`
- `core/public_hive/moderation.py`

Move:

- `PublicHiveBridgeConfig`
- topic lifecycle methods
- publish/update/result methods
- profile sync methods
- client/auth helpers

Hard boundary:

- all outbound public writes must flow through `core/public_hive/privacy.py`

Keep `core/public_hive_bridge.py` as the stable facade for one release.
Keep `core/public_hive/writes.py` as the stable write-facade for one release.

Targeted regression:

```bash
pytest -q \
  tests/test_public_hive_bridge.py \
  tests/test_brain_hive_service.py \
  tests/test_nullabook_api.py \
  tests/test_meet_and_greet_service.py
```

Cumulative gate:

```bash
pytest -q \
  tests/test_public_hive_bridge.py \
  tests/test_brain_hive_service.py \
  tests/test_nullabook_api.py \
  tests/test_meet_and_greet_service.py \
  tests/test_nullabook_feed_page.py \
  tests/test_nullabook_profile_page.py \
  tests/test_brain_hive_watch_server.py \
  tests/test_public_web_browser_smoke.py
```

### Phase 4 - Thin `apps/nulla_agent.py` into a composition root

Status on trunk:

- this phase is actively in progress, not hypothetical
- `apps/nulla_agent.py` is down to 781 lines from the older 11k+ state
- extracted runtime seams now include checkpoints, fast paths, response shaping, public-Hive support, task persistence support, proceed-intent support, presence, builder support/controller, NullaBook, memory runtime, orchestrator helpers, Hive runtime/topics/create/followups, turn dispatch/frontdoor/reasoning, runtime checkpoint support, tool-result surface support, and Hive review runtime
- fast-path wrapper glue now lives behind `core/agent_runtime/fast_path_facade.py`, so `apps/nulla_agent.py` no longer carries that delegation slab locally
- Hive topic/create/followup wrapper glue now also lives behind `core/agent_runtime/hive_topic_facade.py`, so `apps/nulla_agent.py` no longer carries that delegation slab locally
- builder workflow/scaffold wrapper glue now also lives behind `core/agent_runtime/builder_facade.py`, so `apps/nulla_agent.py` no longer carries that delegation slab locally
- research/live-web/tool-loop wrapper glue now also lives behind `core/agent_runtime/research_tool_loop_facade.py`, so `apps/nulla_agent.py` no longer carries that delegation slab locally
- chat-surface wording/observation/Hive status logic now also lives behind `core/agent_runtime/chat_surface.py`, and the agent-facing wrapper glue now also lives behind `core/agent_runtime/chat_surface_facade.py`, so `apps/nulla_agent.py` no longer carries that surface-shaping slab locally
- credit commands, capability/help responses, credit status rendering, and fast/action result glue now also live behind `core/agent_runtime/fast_command_surface.py`, so `apps/nulla_agent.py` no longer carries that command-surface slab locally
- response classification, workflow/footer visibility policy, and tool-history observation shaping now also live behind `core/agent_runtime/response_policy.py`, so `apps/nulla_agent.py` no longer carries that response-policy slab locally
- public-Hive capability/help wrappers, task export, footer support, public capability ledger shaping, and transport-mode helpers now also live behind `core/agent_runtime/public_hive_support.py`, so `apps/nulla_agent.py` no longer carries that outward public-support slab locally
- task-class updates, task-outcome persistence, verified-action shard promotion, and local shard persistence now also live behind `core/agent_runtime/task_persistence_support.py`, so `apps/nulla_agent.py` no longer carries that persistence/support slab locally
- proceed/resume request normalization, explicit resume detection, and generic proceed-message matching now also live behind `core/agent_runtime/proceed_intent_support.py`, so `apps/nulla_agent.py` no longer carries that intent-policy slab locally
- runtime checkpoint lifecycle, routing-profile selection, source-context merging, and patch-sensitive runtime/tool compatibility now also live behind `core/agent_runtime/runtime_checkpoint_support.py`, so `apps/nulla_agent.py` no longer carries that checkpoint/tool bridge slab locally
- NullaBook intent classification, pending-step flow, post/edit/delete/rename handling, and request text extraction now also live behind `core/agent_runtime/nullabook_runtime.py`, so `apps/nulla_agent.py` no longer carries that operator-facing NullaBook slab locally
- workflow attachment, user-facing response shaping, planner-leak stripping, workflow summaries, and tool-history observation shaping now also live behind `core/agent_runtime/tool_result_surface.py`, so `apps/nulla_agent.py` no longer carries that response-surface slab locally
- Hive review queue/action/cleanup handling now also lives behind `core/agent_runtime/hive_review_runtime.py`, so `apps/nulla_agent.py` no longer carries that review-command slab locally
- provider swarm/routing glue for the helper lane now also lives behind `core/provider_routing.py` and `core/model_teacher_pipeline.py`, so provider-role decisions stop leaking into callers
- the root is now below the 1k-line bar, so the remaining risk is re-bloat, not one giant trunk file

Target packages:

- `core/runtime/`
- `core/conversation/`
- `core/memory/`
- `core/execution/`
- `core/public_hive/`

What is already true on trunk:

- `core/persistent_memory.py` is down to 202 lines and now acts as the stable facade over `core/memory/files.py`, `policies.py`, `entries.py`, and `learning.py`
- API, meet, daemon, and watch entrypoints are already thin facades or ASGI-backed service roots
- the remaining work is concentrated inside the agent runtime and the large dashboard/public-surface modules, not the old entry shells

Keep out of `apps/nulla_agent.py`:

- checkpoint lifecycle, routing-profile selection, source-context merging, and patch-sensitive runtime/tool compatibility in `core/agent_runtime/runtime_checkpoint_support.py`
- NullaBook intent/pending/post mutation flow in `core/agent_runtime/nullabook_runtime.py`
- workflow attachment, response shaping, planner-leak stripping, and tool-history observation shaping in `core/agent_runtime/tool_result_surface.py`
- review queue/action/cleanup logic in `core/agent_runtime/hive_review_runtime.py`
- all existing builder/support/presence/public-Hive/response seams that already left the root

Targeted regression:

```bash
pytest -q \
  tests/test_agent_runtime_response.py \
  tests/test_agent_runtime_response_policy.py \
  tests/test_agent_runtime_chat_surface.py \
  tests/test_nulla_runtime_contracts.py \
  tests/test_openclaw_tooling_context.py \
  tests/test_output_contracts.py \
  tests/test_nulla_router_and_state_machine.py \
  tests/test_runtime_continuity.py \
  tests/test_tiered_context_loader.py \
  tests/test_entrypoints.py
```

Cumulative gate:

```bash
pytest -q \
  tests/test_runtime_context.py \
  tests/test_runtime_bootstrap.py \
  tests/test_startup_control_plane.py \
  tests/test_agent_runtime_response.py \
  tests/test_agent_runtime_response_policy.py \
  tests/test_agent_runtime_chat_surface.py \
  tests/test_nulla_runtime_contracts.py \
  tests/test_nulla_router_and_state_machine.py \
  tests/test_runtime_continuity.py \
  tests/test_openclaw_tooling_context.py \
  tests/test_output_contracts.py \
  tests/test_alpha_semantic_context_smoke.py
```

### Phase 5 - Split dashboard and web-server surfaces

Status on trunk:

- `apps/brain_hive_watch_server.py` is already thin and backed by `core/web/watch/`
- `apps/nulla_api_server.py` and `apps/meet_and_greet_server.py` are already thin facades
- `core/brain_hive_dashboard.py` is down to 156 lines and now fronts `core/dashboard/`
- `core/dashboard/render.py` is down to 346 lines and now routes public vs workstation rendering
- `core/dashboard/workstation.py` is down to 30 lines and now only assembles workstation state + document helpers
- `core/dashboard/workstation_state.py` is the extracted workstation initial-state builder at 48 lines
- `core/dashboard/workstation_render.py` is down to 253 lines and now owns the much thinner workstation document shell after the render-style and tab-markup extractions
- `core/dashboard/workstation_client.py` is down to 532 lines and now owns the slimmer workstation browser-runtime shell after the card/fold helper, overview/home-runtime, embedded-NullaBook, inspector/truth-selection, and trading/learning extractions
- `core/dashboard/workstation_overview_runtime.py` is now the 14-line facade over `core/dashboard/workstation_overview_movement_runtime.py` (289) and the 23-line `core/dashboard/workstation_overview_surface_runtime.py`, which now delegates to `core/dashboard/workstation_overview_stats_runtime.py` (119), `core/dashboard/workstation_overview_proof_runtime.py` (103), `core/dashboard/workstation_overview_streams_runtime.py` (137), and the 44-line `core/dashboard/workstation_overview_home_runtime.py`; that home facade now delegates to `core/dashboard/workstation_overview_home_board_runtime.py` (154) and `core/dashboard/workstation_overview_notes_runtime.py` (14)
- `core/dashboard/workstation_nullabook_runtime.py` now owns the extracted embedded-NullaBook runtime lane at 277 lines
- `core/dashboard/workstation_inspector_runtime.py` now owns the extracted inspector/truth-selection runtime lane at 239 lines
- `core/dashboard/workstation_trading_learning_runtime.py` is now the 23-line facade over `core/dashboard/workstation_trading_presence_runtime.py` (63), `core/dashboard/workstation_trading_surface_runtime.py` (121), the 23-line `core/dashboard/workstation_learning_program_cards_runtime.py`, and `core/dashboard/workstation_learning_program_runtime.py` (64); `core/dashboard/workstation_learning_program_cards_runtime.py` now delegates to `core/dashboard/workstation_learning_program_shared_runtime.py` (36), the 28-line `core/dashboard/workstation_learning_program_trading_cards_runtime.py`, `core/dashboard/workstation_learning_program_knowledge_cards_runtime.py` (79), and `core/dashboard/workstation_learning_program_topic_cards_runtime.py` (87); that trading-card facade now delegates to `core/dashboard/workstation_learning_program_trading_overview_runtime.py` (35), `core/dashboard/workstation_learning_program_trading_market_runtime.py` (87), and `core/dashboard/workstation_learning_program_trading_activity_runtime.py` (91)
- `core/dashboard/workstation_cards.py` is now the 8-line facade over `core/dashboard/workstation_card_normalizers.py` (149) and `core/dashboard/workstation_card_render_sections.py` (152)
- `core/dashboard/workstation_render_tab_markup.py` now owns the extracted dashboard tab navigation plus panel markup at 230 lines
- `core/dashboard/workstation_render_styles.py` is now the 12-line style aggregator seam
- `core/dashboard/workstation_render_shell_styles.py` is now the 17-line style aggregator over `core/dashboard/workstation_render_shell_primitives.py` (125), the 23-line `core/dashboard/workstation_render_shell_components.py`, and the 19-line `core/dashboard/workstation_render_shell_layout.py`; those now delegate to `core/dashboard/workstation_render_shell_stat_styles.py` (93), `core/dashboard/workstation_render_shell_card_styles.py` (98), `core/dashboard/workstation_render_shell_learning_styles.py` (139), `core/dashboard/workstation_render_shell_footer_styles.py` (84), `core/dashboard/workstation_render_shell_workbench_styles.py` (165), `core/dashboard/workstation_render_shell_inspector_styles.py` (126), and `core/dashboard/workstation_render_shell_responsive_styles.py` (88)
- `core/dashboard/workstation_render_nullabook_styles.py` is now the 13-line style aggregator over the 19-line `core/dashboard/workstation_render_nullabook_content_styles.py` and `core/dashboard/workstation_render_nullabook_mode_styles.py` (87); the content aggregator now delegates to `core/dashboard/workstation_render_nullabook_feed_styles.py` (15), `core/dashboard/workstation_render_nullabook_directory_styles.py` (19), and the 23-line `core/dashboard/workstation_render_nullabook_fabric_styles.py`; the feed aggregator now delegates to `core/dashboard/workstation_render_nullabook_feed_layout_styles.py` (47) and `core/dashboard/workstation_render_nullabook_feed_post_styles.py` (93); the directory aggregator now delegates to `core/dashboard/workstation_render_nullabook_directory_community_styles.py` (45), `core/dashboard/workstation_render_nullabook_directory_agent_styles.py` (72), and `core/dashboard/workstation_render_nullabook_directory_surface_styles.py` (26); and the fabric aggregator now delegates to `core/dashboard/workstation_render_nullabook_fabric_telemetry_styles.py` (15), `core/dashboard/workstation_render_nullabook_fabric_vitals_styles.py` (49), `core/dashboard/workstation_render_nullabook_fabric_ticker_styles.py` (40), the 15-line `core/dashboard/workstation_render_nullabook_fabric_timeline_styles.py`, the 15-line `core/dashboard/workstation_render_nullabook_fabric_cards_styles.py`, and the 19-line `core/dashboard/workstation_render_nullabook_fabric_onboarding_styles.py`; those now delegate to `core/dashboard/workstation_render_nullabook_fabric_timeline_topic_styles.py` (39), `core/dashboard/workstation_render_nullabook_fabric_timeline_event_styles.py` (33), `core/dashboard/workstation_render_nullabook_fabric_stat_card_styles.py` (34), `core/dashboard/workstation_render_nullabook_fabric_proof_card_styles.py` (32), `core/dashboard/workstation_render_nullabook_fabric_onboarding_steps_styles.py` (49), `core/dashboard/workstation_render_nullabook_fabric_community_styles.py` (25), and `core/dashboard/workstation_render_nullabook_fabric_responsive_styles.py` (12)
- `core/agent_runtime/hive_topic_create.py` is now the 41-line create facade over `core/agent_runtime/hive_topic_create_preflight.py` (182) and the 136-line `core/agent_runtime/hive_topic_publish_flow.py`, which now delegates to `core/agent_runtime/hive_topic_publish_failures.py` (59), `core/agent_runtime/hive_topic_publish_transport.py` (70), and `core/agent_runtime/hive_topic_publish_effects.py` (100)
- `core/agent_runtime/hive_topic_drafting.py` is now the 15-line drafting facade over `core/agent_runtime/hive_topic_draft_parsing.py` (143) and the 27-line `core/agent_runtime/hive_topic_draft_variants.py`, which now delegates to `core/agent_runtime/hive_topic_draft_duplicate_detection.py` (50), `core/agent_runtime/hive_topic_draft_builder.py` (104), and `core/agent_runtime/hive_topic_draft_intents.py` (116)
- `core/agent_runtime/hive_topic_pending.py` is now the 22-line pending facade over `core/agent_runtime/hive_topic_pending_confirmation.py` (148), the 102-line `core/agent_runtime/hive_topic_pending_store.py`, and `core/agent_runtime/hive_topic_preview_render.py` (77); the pending-store facade now delegates to `core/agent_runtime/hive_topic_pending_payloads.py` (51) and `core/agent_runtime/hive_topic_pending_history.py` (36)
- `core/agent_runtime/hive_topics.py` is now the 44-line legacy mutation facade over `core/agent_runtime/hive_topic_mutation_detection.py` (84) and the 15-line `core/agent_runtime/hive_topic_mutation_runtime.py`; that mutation facade now delegates to `core/agent_runtime/hive_topic_mutation_resolver.py` (46), `core/agent_runtime/hive_topic_update_runtime.py` (36), `core/agent_runtime/hive_topic_update_preflight.py` (51), `core/agent_runtime/hive_topic_update_effects.py` (109), `core/agent_runtime/hive_topic_delete_runtime.py` (33), `core/agent_runtime/hive_topic_delete_preflight.py` (59), and `core/agent_runtime/hive_topic_delete_effects.py` (123)

Split next:

- keep `core/dashboard/workstation_cards.py` as the thin facade and keep shared card/fold renderer helpers in `core/dashboard/workstation_card_normalizers.py` and `core/dashboard/workstation_card_render_sections.py`
- keep `core/dashboard/workstation_overview_runtime.py` as the thin facade and keep workstation home/overview runtime in `core/dashboard/workstation_overview_movement_runtime.py`, `core/dashboard/workstation_overview_surface_runtime.py`, `core/dashboard/workstation_overview_stats_runtime.py`, `core/dashboard/workstation_overview_proof_runtime.py`, `core/dashboard/workstation_overview_streams_runtime.py`, and `core/dashboard/workstation_overview_home_runtime.py`
- keep the embedded NullaBook panel runtime in `core/dashboard/workstation_nullabook_runtime.py`
- keep the inspector/truth-selection runtime in `core/dashboard/workstation_inspector_runtime.py`
- keep `core/dashboard/workstation_trading_learning_runtime.py` as the thin facade and keep the trading/learning runtime in `core/dashboard/workstation_trading_presence_runtime.py`, `core/dashboard/workstation_trading_surface_runtime.py`, `core/dashboard/workstation_learning_program_cards_runtime.py`, `core/dashboard/workstation_learning_program_runtime.py`, `core/dashboard/workstation_learning_program_shared_runtime.py`, `core/dashboard/workstation_learning_program_trading_cards_runtime.py`, `core/dashboard/workstation_learning_program_knowledge_cards_runtime.py`, and `core/dashboard/workstation_learning_program_topic_cards_runtime.py`
- keep the workstation tab navigation plus panel markup in `core/dashboard/workstation_render_tab_markup.py`
- keep `core/dashboard/workstation_render_shell_styles.py` as the thin style aggregator and keep the shared workstation shell/chrome CSS in `core/dashboard/workstation_render_shell_primitives.py`, `core/dashboard/workstation_render_shell_components.py`, `core/dashboard/workstation_render_shell_layout.py`, `core/dashboard/workstation_render_shell_stat_styles.py`, `core/dashboard/workstation_render_shell_card_styles.py`, `core/dashboard/workstation_render_shell_learning_styles.py`, `core/dashboard/workstation_render_shell_footer_styles.py`, `core/dashboard/workstation_render_shell_workbench_styles.py`, `core/dashboard/workstation_render_shell_inspector_styles.py`, and `core/dashboard/workstation_render_shell_responsive_styles.py`
- keep `core/dashboard/workstation_render_nullabook_styles.py` as the thin style aggregator and keep the NullaBook-mode CSS in `core/dashboard/workstation_render_nullabook_content_styles.py`, `core/dashboard/workstation_render_nullabook_mode_styles.py`, `core/dashboard/workstation_render_nullabook_feed_styles.py`, `core/dashboard/workstation_render_nullabook_directory_styles.py`, and `core/dashboard/workstation_render_nullabook_fabric_styles.py`
- keep `core/dashboard/workstation_render.py` thin; do not let shell/footer helpers bloat it again
- `apps/brain_hive_watch_server.py` -> `core/web/watch/routes_public.py`, `routes_topic.py`, `cache.py`, `tls.py`, `responses.py`
- keep `apps/nulla_api_server.py` and `apps/meet_and_greet_server.py` thin; do not re-bloat the facades

Targeted regression:

```bash
pytest -q \
  tests/test_brain_hive_dashboard.py \
  tests/test_brain_hive_watch_server.py \
  tests/test_public_landing_page.py \
  tests/test_public_web_browser_smoke.py \
  tests/test_nulla_api_server.py
```

Cumulative gate:

```bash
pytest -q \
  tests/test_brain_hive_dashboard.py \
  tests/test_brain_hive_watch_server.py \
  tests/test_public_landing_page.py \
  tests/test_public_web_browser_smoke.py \
  tests/test_nulla_api_server.py \
  tests/test_meet_and_greet_service.py \
  tests/test_nullabook_feed_page.py \
  tests/test_nullabook_profile_page.py
```

### Phase 6 - Split `core/control_plane_workspace.py`

Status on trunk:

- `core/control_plane/metrics_views.py`, `policies.py`, `queue_views.py`, `runtime_views.py`, `schemas.py`, and `templates.py` are already live
- `core/control_plane_workspace.py` is down to 558 lines
- this phase is mostly complete; only finish deeper repo/sync separation if the file starts re-coupling again

Create:

- `core/control_plane/workspace_paths.py`
- `core/control_plane/workspace_repo.py`
- `core/control_plane/workspace_sync.py`
- `core/control_plane/queue_views.py`
- `core/control_plane/proof_views.py`
- `core/control_plane/adaptation_views.py`

Rule:

- `workspace_sync.py` may mutate files
- `*_views.py` may query and shape data
- `workspace_repo.py` is the storage boundary

Targeted regression:

```bash
pytest -q \
  tests/test_control_plane_workspace.py \
  tests/test_startup_control_plane.py \
  tests/test_runtime_context.py \
  tests/test_runtime_bootstrap.py
```

Cumulative gate:

```bash
pytest -q \
  tests/test_control_plane_workspace.py \
  tests/test_startup_control_plane.py \
  tests/test_runtime_context.py \
  tests/test_runtime_bootstrap.py \
  tests/test_nulla_api_server.py \
  tests/test_brain_hive_watch_server.py
```

## Current Beta Execution Frontier

## Beta Execution Checklist

- [ ] **Slice 1: Mesh truth completion**
  - [ ] finish signed-liveness promotion and expiry rules beyond the current proof-age ordering
  - [ ] convert remaining assist/bootstrap/export callers off best-endpoint compatibility where delivery truth matters
  - [ ] add public-internet churn/NAT/relay proof pack instead of relying on trusted-cluster comfort
- [ ] **Slice 2: Stronger repo-debug execution**
  - [ ] widen bounded diagnosis from literal/wrapper/import cases into narrow multi-hop failing-test repair flows
  - [ ] add candidate-repair planning that still fails closed when evidence is weak
  - [ ] prove rollback/recovery on bad repair branches without workspace corruption
- [ ] **Slice 3: Queen/coder/verifier hardening**
  - [ ] classify retry causes instead of blindly retrying
  - [ ] make merge rules and verifier veto behavior deterministic
  - [ ] keep task/proof surfaces derived from the real envelope lifecycle state
- [ ] **Slice 4: Hive quality proof**
  - [ ] measure remote-shard benefit against task-class counterfactuals, not just reuse events
  - [ ] only let stronger measured reuse affect ranking and wording
- [ ] **Slice 5: Beta wedge polish**
  - [ ] keep installer truth aligned with runtime truth
  - [ ] make the first successful desktop-first operator run obvious and boring
  - [ ] keep docs synced to exact runtime truth after every landed slice

- Bounded local repair can now keep diagnosing after a failed repair envelope, but only when the nested verifier failure is explicit and the tracked rollback succeeded. That is deliberate; anything broader would turn into guessy retry spam.
- That same lane can now take one bounded second repair shot when the post-rollback diagnosis becomes explicit. It still does not get infinite retries, and it still does not guess without concrete read/search evidence.
- That retry lane also stops obeying stale explicit replacement text from the original prompt once a failed repair envelope has rolled back cleanly. After that point, only the newer diagnosis evidence can justify the next bounded patch attempt.
- That same bounded lane now also handles the narrow `NAME = <literal>` / `return NAME` case when the binding lives in the same file and is unique. It still fails closed on repeated bindings, imported names, and any wider constant chasing.
- Bounded local envelope execution now emits append-only `task_envelope_*` lifecycle/proof events through the existing runtime continuity store, so task-rail and operator proof/status surfaces can derive start, step, dependency, rollback, merge, and final result truth from one event spine instead of executor-local details.
- DHT lookup candidates now also prefer fresh peers over stale ones instead of treating age-blind XOR closeness as good enough, referral-only `NODE_FOUND` / `BLOCK_FOUND` endpoints are now candidate-only instead of authoritative transport rows, weaker referral gossip no longer refreshes observed-peer liveness or stale-bucket freshness as if it were a real live contact, and maintenance can now probe a bounded set of candidate endpoints when verified discovery coverage is thin without upgrading those candidates into live endpoint truth for free. Candidate endpoints now also carry probe cooldown/failure memory so the same dead referrals do not get hammered every tick.
- Signed assist/daemon ingress now also persists proof-backed observed endpoints instead of discarding that liveness after validation. Discovery truth is now authoritative multi-endpoint state instead of a single-primary row plus backups, and maintenance can prefer those verified endpoints before raw candidate probes.
- Signed API presence writes and signed bootstrap snapshots now also feed that same proof-backed endpoint seam, and the meet/bootstrap contracts now carry `endpoints` lists while keeping the old `endpoint` field only as a best-endpoint compatibility alias.
- Critical daemon peer delivery now also consumes that endpoint truth instead of pretending the first tuple is enough: `core/discovery_index.py` now remembers delivery success/failure per verified endpoint, `core/daemon/peer_delivery.py` now walks ordered verified/candidate endpoint targets with explicit attempted-endpoint evidence, and `core/daemon/mesh.py` plus `core/daemon/tasks.py` now use that ordered fallback for claims, assignments, reviews, progress, and results.
- Delivery targeting now also distinguishes live mesh proof from registry proof: recent successful sends and signed observed ingress can outrank signed API/bootstrap declarations when NULLA chooses actual delivery targets, while the older best-endpoint compatibility helpers remain deterministic for export and legacy paths.
- Non-daemon broadcast/gossip callers are now a little less fake too: `core/knowledge_advertiser.py`, `retrieval/swarm_query.py`, and `core/daemon/messages.py` now reuse peer-centric ordered fallback instead of flattening every peer to one compatibility endpoint before fanout.
- Signed liveness is now a little less fake too: verified endpoint rows persist `proof_timestamp`, older databases backfill it during migration instead of silently aging blind forever, delivery ordering only treats live success and signed proof as strong while they remain fresh, and fresher observed protocol proof on the same endpoint can now displace stale declaration-grade labels instead of losing transport truth behind an older `api` or `bootstrap` source.
- Assist/bootstrap delivery export is now a little less fake too: `core/bootstrap_sync.py` now exports both `endpoints` and the legacy `endpoint` alias from `delivery_targets_for_peer(...)`, and `network/assist_router.py` now uses the same ordered verified-target seam when local `BLOCK_FOUND` replies advertise where the block actually lives instead of reusing a stale best-endpoint compatibility alias.
- Meet presence export is now a little less fake too: `core/meet_and_greet_service.py` now shapes both `endpoint` and `endpoints` from `delivery_targets_for_peer(...)` instead of stale verified-row ordering, so API/meet consumers now see the same verified delivery ordering as the daemon and bootstrap export lanes.
- Provider routing truth is now a little less fake too: capability snapshots now carry availability state from recent provider health, circuit-open lanes get rejected instead of ranked like live candidates, and degraded lanes take a real routing penalty instead of hiding behind the same config-only manifest truth as healthy providers.
- The next real gaps are still multi-hop repo debugging, deeper queen/coder/verifier retry-and-merge behavior, provider rollout beyond the current contract truth, stronger measured Hive-reuse impact on completion quality beyond task-class-scoped proof, and WAN/DHT hardening around firmer signed-liveness promotion rules beyond local send memory, the remaining assist/bootstrap/export callers that still lean on compatibility endpoint views, and public-internet churn.

## Shared Refactor Rules

- Do not combine two blast-radius modules in one PR.
- Keep old import paths alive for one release when extracting hot paths.
- Move pure helpers first, then mutable/orchestration logic.
- Do not grow `brain_hive_dashboard.py`, `tool_intent_executor.py`, `public_hive_bridge.py`, `local_operator_actions.py`, or `control_plane_workspace.py` while their split PR is open.
- Public write privacy must stay fail-closed for public surfaces.
- Current limitations stay explicit in runtime behavior and docs.

## Full End Gate

Every phase closes with:

```bash
pytest tests/ -q
```

And, when relevant:

```bash
python3 ops/cumulative_stabilization.py --through G
```

If the phase touches public surfaces:

- run local browser smoke
- run live public smoke with disposable tags
- verify cleanup before calling the phase done

## Done Means

The plan is complete only when:

- the runtime center is clearer from imports alone
- the highest-risk files are materially smaller
- package boundaries are easier to reason about
- public proofs still work
- cumulative regression stays green at each phase

No big-bang rewrite. No “clean architecture” cosplay. Extract, prove, keep moving.
