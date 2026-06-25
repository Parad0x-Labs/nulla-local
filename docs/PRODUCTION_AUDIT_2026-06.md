# Production Audit — June 2026

Whole-stack adversarial audit of the NULLA local-first runtime, run after the
June 2026 feature batch landed (embedder unification, local WorkProof reward,
entity-graph recall, two-NULLA handshake, Web0 builder intent set, knowledge
marketplace buy side).

## Method

Four dimension finders ran in parallel at high reasoning effort — **security**,
**correctness**, **claims/docs**, **test-coverage** — each scoped to the freshly
changed code and the money paths. Every raw finding then went to an independent
**skeptic verifier** that opened the cited code and tried to refute it; only
findings backed by concrete code and a concrete trigger were kept. Severities
were re-graded by the verifier (for example, the mesh findings were lowered from
P1 to P2 because that path is not wired into any live entry point today).

- Raw findings: **21**
- Confirmed after adversarial verification: **21** (0 refuted)
- P0: 0 · P1: 2 · P2: 5 · P3: 14

Fixes land on branch `fix/production-audit-2026-06`: code + tests in `a4e6f15`,
the documentation language sweep in `22a00f0`.

## Test posture

Full suite: **2431 passing** (excludes one machine-local llama.cpp acceptance
test that depends on a local draft GGUF; CI skips it too). The audit added **14**
regression tests pinning each behavioural fix.

---

## Confirmed findings

| # | Dim | Sev | Title | Status |
|---|-----|-----|-------|--------|
| 1 | security/coverage | P1 | Marketplace concurrent buys double-debit the buyer | **Fixed** |
| 2 | claims | P1 | Banned-word framing in public LAUNCH_TECH_BRIEF | **Fixed** |
| 3 | security | P2 | Mesh `accept_bid` escrow fails open (wrong import, swallowed) | **Fixed** |
| 4 | security | P2 | Mesh `TaskBid` signature never verified | **Fixed** |
| 5 | security | P2 | `web0.publish` opt-in read from model arguments | **Fixed** |
| 6 | security | P2 | Gate endpoint uses replayable v1 challenge; CORS open | **Deferred** |
| 7 | claims | P2 | Banned-word in STATUS / README / INSTALL / proof docs | **Fixed** |
| 8 | security | P3 | `task_completion` self-credit gated by a self-computable hash | **Deferred** |
| 9 | security | P3 | Knowledge purchase burned the buyer but never paid the seller | **Fixed** |
| 10 | correctness | P3 | BM25 keyword leg scores 0 on a single match | **Fixed** |
| 11 | correctness | P3 | `record_purchase` rating average divided by purchase count | **Fixed** |
| 12 | correctness | P3 | Mixed embedding dimensions in one table (fragile) | **Deferred** |
| 13 | claims | P3 | Banned-word in PLATFORM_REFACTOR_PLAN | **Fixed** |
| 14 | claims | P3 | Banned-word in INSTALL_PROVIDER_EXECUTION_PLAN | **Fixed** |
| 15 | claims | P3 | Banned-word in TDL / soak / preflight / stabilization | **Fixed** |
| 16 | claims | P3 | Code-derived metric identifiers embedding the banned word | **Deferred** |
| 17 | coverage | P3 | Free-listing (price 0) purchase path untested | **Fixed** |
| 18 | coverage | P3 | Concurrent-purchase debit-once untested | **Fixed** |
| 19 | coverage | P3 | `record_purchase` rating average untested | **Fixed** |
| 20 | coverage | P3 | `usdc_to_atomic` tie + `_allocate_pool_atomic` shares>pool untested | **Fixed** |
| 21 | coverage | P3 | Embedding cosine zero-vector / shorter-vector untested | **Fixed** |

---

## Fixed

### P1 — Marketplace concurrent double-debit (and seller never paid)

`purchase_knowledge` guarded idempotency with a `has_entitlement` read, then
burned the price under a fresh per-call UUID receipt. Two concurrent buys of the
same `(buyer, shard)` both passed the entitlement check and both burned — with
distinct receipts the ledger replay guard did not collapse them, so the buyer
was charged twice while only one entitlement was granted. Separately, the price
was *burned* rather than transferred, so the seller was never paid.

**Fix** (`core/knowledge_marketplace.py`): the price now moves buyer → seller in
one atomic `transfer_credits` call, keyed on a **deterministic** `(buyer, shard)`
receipt. A second concurrent buy hits the ledger replay guard and collapses to a
single debit; the post-transfer path re-checks the entitlement and the new
`credit_ledger.receipt_exists` helper to return an idempotent `already_purchased`
without a second charge. A free (price 0) listing unlocks with no ledger
movement. Regression: `test_concurrent_purchase_same_shard_debits_once`,
`test_purchase_actually_pays_the_seller`, `test_free_listing_unlocks_without_charge`.

### P2 — Mesh `accept_bid` escrow failed open

`accept_bid` imported `CreditLedger` from `core.credit_ledger` — a module that
defines no such class — so the import raised on every call, the bare `except`
logged a warning, and assignment proceeded with no funds hold. The real control
(`CreditLedger.spend` raises on insufficient balance) never ran.

**Fix** (`core/mesh/task_router.py`): import the real
`core.mesh.credit_ledger.CreditLedger` and **fail closed** — on any escrow
failure return `{"assigned": False, "reason": "escrow_failed"}` and skip the
challenge and the winner notification. A genuinely free (0-credit) bid skips the
hold. Regression: `test_accept_bid_fails_closed_when_escrow_cannot_be_funded`,
plus the existing challenge test now funds the poster first.

### P2 — Mesh `TaskBid` signature never verified

`_solicit_bid` validated only field presence and the task id; the `signature`
field and `canonical_payload()` existed but were never checked, so a peer (or an
on-path rewrite of the unauthenticated HTTP response) could forge `bidder_node_id`
and `credits_requested`.

**Fix**: `_solicit_bid` now verifies the ed25519 signature over the canonical
payload against the claimed `bidder_node_id` and drops the bid on failure, before
selection or escrow. Regression: `test_solicit_bid_rejects_an_unsigned_or_forged_bid`.

### P2 — `web0.publish` opt-in read from model arguments

The publish handler read `allow_network_publish` from the model-supplied
`arguments`, so a model that could emit the intent could also set its own opt-in;
only the wallet came from the trusted `source_context`.

**Fix** (`core/runtime_execution_tools.py`, `core/runtime_tool_contracts.py`):
both gates — the opt-in and the wallet — now come from `source_context`. The
opt-in was removed from the model-facing input schema. A request alone is never
sufficient to publish. Regression: `test_web0_publish_ignores_model_supplied_optin`.

### P3 — Knowledge purchase now pays the seller

Folded into the P1 fix: `transfer_credits` credits the seller atomically instead
of burning the price.

### P3 — BM25 keyword leg scored 0 on a single match

When exactly one node matched the FTS query, the score span collapsed and the
sole match received 0.0 instead of the full keyword boost. **Fix**
(`core/nulla_memory.py`): a single match (or all-equal ranks) now gets 1.0 — any
MATCH row is a genuine keyword hit. Regression:
`test_bm25_single_match_scores_full_not_zero`.

### P3 — Rating average divided by purchase count

`record_purchase` computed the running rating mean using `purchase_count`, so
unrated purchases diluted each rating. **Fix**: a dedicated `rating_count` column
(with a defensive `ALTER TABLE` for existing databases) now drives the mean.
Regression: `test_record_purchase_rating_average_is_over_ratings_not_purchases`.

### Claims — Banned-word language sweep (P1 / P2 / P3)

The banned candor qualifier (the adjective and its adverb form) appeared in
prose across the active git-tracked docs, including the public-facing launch
brief. **Fix** (`22a00f0`): 56 prose occurrences across 20 files were rewritten
to neutral, accurate wording (accurate / precise / sound / clean / open) that
preserves meaning. Verified: zero prose occurrences remain in the active set.

### Coverage — added regression tests

Free-listing purchase, concurrent debit-once, rating average, `usdc_to_atomic`
banker's-rounding tie, `_allocate_pool_atomic` shares > pool, and embedding
cosine zero-vector / shorter-vector edge cases are now pinned by tests
(`tests/test_audit_fix_regressions.py` and the marketplace/mesh suites).

---

## Deferred (with rationale and recommendation)

These are genuine findings whose correct fix needs a coordinated or higher-risk
change that does not belong in an audit-remediation pass. Each is recorded here
so it is tracked, not lost.

### P2 — Gate endpoint replayable v1 challenge + open CORS

The shipping `/gate/unlock` route uses the static v1 challenge; the hardened
`GateChallengeStore` (single-use, TTL-bound v2 nonces) is implemented but not
wired in, and the gate CORS allows any origin. A captured unlock tuple can be
replayed to unlock the same gated block.

**Why deferred:** wiring the store to *require* v2 server-side would break the
live browser-extension and portal clients (a separate repo) that still send v1,
and the open CORS supports gated blocks embedded on arbitrary Arweave-served
domains. This needs a coordinated client + server upgrade, not a one-sided
change.

**Recommendation:** ship a `/gate/challenge` issue endpoint plus the client
change that requests, signs, and consumes a v2 nonce in the same release; scope
the gate CORS to the portal origin in that change.

### P3 — `task_completion` self-credit proof is self-computable

The local self-award verifies a SHA-256 receipt that the issuer can recompute;
the award is bounded to the local peer, rate-limited to 30 per 60s, and
idempotent per receipt.

**Why deferred:** the abuse ceiling is bounded self-minting to the local node's
own balance with no cross-peer redemption. The correct fix — ed25519-signing the
work receipt and verifying it — ripples through `Web0WorkReceipt`, `ProofReceipt`,
`nullpass`, and the CLI receipt surface.

**Recommendation:** sign the work receipt (mirroring `task_capsule` /
`two_nulla_handshake`) in a focused change; lower the per-window cap if these
credits ever become broadly transferable.

### P3 — Mixed embedding dimensions in one table

The 384-dim chat embedder and the 64-dim fact embedder can in principle write
vectors of different lengths into one `memory_nodes` table.

**Why deferred:** no live path co-mingles them — they run on different `agent_id`
partitions — and the shared cosine helper already projects mismatched lengths and
logs once instead of crashing or silently zeroing. The fix (pin the dimension per
agent and reject mismatches, or route both writers through the canonical `embed()`)
changes stored vectors and is best done as a dedicated migration.

**Recommendation:** record the embedding dimension per agent/database and reject
out-of-dimension writes; migrate existing rows in the same pass.

### P3 — Code-derived metric identifiers that embed the banned word

The acceptance and proof docs reference snake_case latency/scenario metric
identifiers whose suffix embeds the banned word (the `offline_*`, `failure_*`,
`freshness_*`, `ultra_fresh_*`, `empty_lookup_*` gate names), plus the historical
alpha-hardening branch name that also embeds it.

**Why deferred:** these are machine identifiers tied to code, JSON artifact
filenames, and the acceptance harness; renaming them in the docs alone would
desync the docs from the code.

**Recommendation:** rename the identifiers in code, tests, and docs together
(suffix them `_accuracy_gate` instead of the banned suffix), then update the
proof docs to match.

### Left untouched by design

- `docs/archive/**` — historical handovers and audit records; rewriting them would
  alter the record of what was written at the time.
- A vendored third-party model card under `data/trainable_models/` — not project
  prose.

---

## Areas confirmed clean

The verifier explicitly cleared the production swarm/HIVE dispatch money path
(`escrow_credits_for_task` — balance-gated, atomic, fail-closed), the
commit/reveal WorkProof reward gate in `submit_result`, the AES-GCM content
encryption (per-block key, nonce, AAD binding), and the RPC allowlist (no banned
`api.mainnet-beta` URL in shipping code).
