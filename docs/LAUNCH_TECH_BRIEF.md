# Parad0x Labs — Launch Technical Brief
**Date:** 2026-06-19  
**For:** marketing agent / tweet threads / grant material  
**Note:** open repo content (nulla-local, dna-x402, Dark-Null-Protocol, web0 public) is already in context. This brief focuses on web0-internal — the full stack.

---

## What is the stack at a glance

```
Browser / Agent
    │
    ├─ null-resolver (Chrome/Firefox extension) ─ native .null in address bar
    ├─ null-doh (Cloudflare Worker)             ─ RFC 8484 DoH, forwards .null to Solana
    ├─ null-gateway (Cloudflare Worker)         ─ resolves .null → streams Arweave content
    │
    ├─ apps/null-portal (Next.js)               ─ register, bid, build, pay, receive, browse
    │       ├─ AI site builder (Claude via x402)
    │       ├─ sealed bid UI (commit/reveal)
    │       ├─ NullPay (stealth pay-to-.null-name)
    │       ├─ Private Inbox (ECIES-encrypted)
    │       └─ WorldPortal (Three.js galaxy view of .null universe)
    │
    ├─ packages/null-mcp                        ─ MCP server for AI agents (resolve + transact)
    ├─ packages/null-agent                      ─ agent-native SDK (resolve + fetch + pay)
    ├─ packages/web0-crypt                      ─ ECIES multi-recipient encryption (@parad0x_labs)
    ├─ packages/web0-tip                        ─ SPL tip primitive
    ├─ packages/null-frame                      ─ Farcaster frames for .null auctions/bids
    │
    ├─ programs/null_registrar (v2, Solana)     ─ .null domain state, 378-byte NullDomain account
    ├─ programs/null-auction (v3, Solana)       ─ sealed-bid with Poseidon commitments
    │
    └─ dark-zk/                                 ─ Groth16 circuits + on-chain verifiers (devnet)
            ├─ shielded_withdraw v3 (Circom)
            ├─ x402_access (Circom + Noir)
            ├─ registrar.circom / track_record.circom
            ├─ dark-groth16-core (Rust crate, 6 VKs embedded)
            ├─ dark-poseidon-real (Rust, Poseidon native BN254)
            └─ programs: dark_shielded_pool, dark_nullifier_record, dark_x402_access_gate,
                         dark_registrar, dark_reputation_gate, receipt_commitment_tree
```

---

## On-chain state — mainnet-beta (all live)

| Program | Address | Status |
|---------|---------|--------|
| `null_registrar v2` | `H4wbFJucY9shJt95N8Bra532Z4nnkKhGEfqWvLcYfuDm` | live, pilot=free |
| `null-auction v3` | same upgrade authority | live, sealed-bid |
| `receipt_anchor` | `6HSRGivdYR5D7yTDy1TFMCM8h3LzXxRtKU1RA3RnCMRN` | live |
| `dark_x402_access_gate` | `PmSCTuehX1MYxf8GNsGsUZySYTtqWAtuTt3N2xZLpw2` | live |
| `dark_nullifier_record` | `GCptvBYF8S6eVYoh15B7WAESc54FUHCpN1Ui6aHeQYZd` | live |
| `dark_semaphore` (BN254) | `Ev7HEFhhKTXk6kS2Y6ssbUcK9C7E6yZ589jJNjUrQV5p` | live |
| `$NULL` token mint | `8EeDdvCRmFAzVD4takkBrNNwkeUTUQh4MscRK5Fzpump` | live |

Upgrade authority (all programs): Squads v4 multisig `9M949AfyYCHp9hUk7crZZx3N6Y8sigyWBN6RM6tFq1q5`

Config PDA (fee schedule): `BQTxsYxocM2ZC3Wb2pVdnyzTPduBcNhKojhBenR6AXYG`  
Pilot config: `sol_fee_lamports=0`, `null_fee_amount=0` — registration costs rent only (~0.003 SOL).  
Go-live flip: one `SetConfig` tx sets `sol_fee_lamports≈0.007 SOL` → **~0.01 SOL all-in per standard domain**.

---

## .null domain — NullDomain v2 account layout

378-byte on-chain account, backward-compatible with v1 (314 bytes):

| Offset | Size | Field |
|--------|------|-------|
| 0 | 1 | disc `0x4E` |
| 1 | 64 | name (utf-8, null-padded) |
| 65 | 32 | owner pubkey |
| 97 | 32 | arweave_txid (current content) |
| 129 | 128 | x402_endpoint (pay-to-access URL) |
| 257 | 32 | passport_hash (Dark Passport commitment, slot only — verifier ships later) |
| 289 | 8 | registered_at (unix) |
| 297 | 8 | expires_at |
| 305 | 8 | null_paid |
| 313 | 1 | bump |
| **314** | **64** | **stealth_meta (spend_pub‖view_pub) — v2 NullPay field; all-zero=unset** |

Resolution is keyless: deterministic sha256 PDA derivation from the name, one `getAccountInfo` on any public RPC — no API key, no `getProgramAccounts`.

---

## Resolution stack — how a .null name becomes content

**Three paths, same destination:**

### 1. Browser extension (native feel)
`extensions/null-resolver` (Chrome) + `extensions/null-resolver-firefox` — intercepts navigation to `*.null`, resolves PDA on Solana, redirects to the Arweave content URL. The address bar shows `parad0x.null` — not a long Arweave hash. Deterministic PDA derivation lives in `pda.js` — no server in the loop.

### 2. DoH + Gateway (no extension required, nearly native)
Users point their browser's secure DNS at `https://doh.parad0xlabs.com/dns-query` (RFC 8484). Typing `parad0x.null`:
1. DoH Worker queries Solana, returns `CNAME parad0x.null.parad0xlabs.com`
2. Browser resolves that subdomain → `null-gateway` Cloudflare Worker
3. Gateway resolves `.null` on Solana → **reverse-proxies the Arweave content** (streams it, 200 response, URL stays clean)

### 3. Agent-native (`packages/null-agent`, `packages/null-mcp`)
`resolveNull("parad0x.null")` → returns `{ owner, arweaveTxid, contentUrl, x402Endpoint, registeredAt }`. MCP server exposes `resolve_null_domain` and `fetch_null_content` tools over stdio — plugs directly into Claude Desktop / Claude Code / any MCP client. An AI agent reads a `.null` site the same way a browser does; if the name has an `x402_endpoint`, the agent pays and retries with the payment proof.

---

## Sealed-bid auction — technical design

`null-auction v3` implements Dark NULL sealed-bid. 1–3 char premium names only (standard registration returns `NameTooShort 0x7006`).

**Commit phase:**
```
C = Poseidon3(bid_amount, blinding, bidder_secret)
bidder_nullifier = Poseidon2(bidder_secret, auction_id)
```
Only `C` and the nullifier go on-chain. `bid_amount` is invisible. `dark_nullifier_record` prevents the same bidder from submitting multiple commitments.

**Reveal phase:** bidder submits `(bid_amount, blinding)`. Program calls `sol_poseidon` (Solana native syscall, same BN254 constants) and verifies `Poseidon3(bid_amount, blinding, bidder_secret) == C`.

**Settle:** winner gets the domain via CPI to `null_registrar Transfer (0x04)`. Seller gets 95%, treasury 5% (`TREASURY_FEE_BPS`). Losers reclaim their bond. Losing bid amounts are **never revealed** — they stay as sealed commitments forever.

**Planned v2** (Dark NULL full proof): winner submits a Groth16 proof that their revealed bid is ≥ every other committed bid in the Merkle tree (`receipt_commitment_tree`). Circuit: `auction_winner.circom`. Zero loser bids ever surface, even in the reveal tx.

Settlement currency: USDC (v1). SOL + `$NULL` support in `AuctionState` for v1.1.

---

## NullPay — stealth payments to .null names

`SetStealthMeta (0x0C)` — owner writes a 64-byte meta-address `spend_pub‖view_pub` to offset 314 of their NullDomain account. No new program — the registrar handles it.

**Send flow (client-side, no custom program):**
```
r        = random scalar
R        = r·B
shared   = H("..." ‖ r·V)      // r·V = r·v·B; only view key needed
P        = S + shared·B         // one-time address, new per payment
```
Sender pays `P` directly (SystemProgram.transfer or SPL). Publishes `R` as a memo on the Resolve (0x05) instruction so the recipient can scan for their payments by watching `getSignaturesForAddress(domainPda)` — no crawling every memo on the chain.

**Recipient scans** with the view key only (spend key stays offline):
```
shared' = H("..." ‖ v·R)       // v·R = v·r·B = r·V  →  shared' == shared
P'      = S + shared'·B        // == P → this payment is mine
```

**Recipient spends**: derives one-time scalar `p = (s + shared) mod L`, signs with raw-scalar EdDSA (RFC 8032). Solana verifies with stock ed25519 — no CPI, no custom program.

Two payments to the same `.null` name never reuse the same `P`. The sender's wallet is visible; the recipient's main wallet never appears.

Math is byte-compatible with Rust crate `dark-stealth-ed25519` and browser port `lib/nullpay.ts`.

---

## web0-crypt — encrypted .null content

`@parad0x_labs/web0-crypt` — ECIES multi-recipient encryption. Standard primitives: X25519 + HKDF-SHA256 + AES-256-GCM. **No server, no key-release service, non-custodial.**

**Encryption identity:** X25519 keypair derived from a deterministic `signMessage` over `IDENTITY_MESSAGE` with the user's wallet. Same wallet → same identity forever. Phantom and `tweetnacl` both produce RFC 8032-deterministic signatures.

**Encrypt to N recipients:**
```
AES-key  = random
content  = AES-256-GCM(AES-key, plaintext)
wraps[i] = ECDH-wrap(AES-key, recipient_i.publicKey)  // X25519 ECDH + HKDF
```
Wraps are **shuffled and unlabeled** — no address appears in the envelope. Outsider view: ciphertext + N unlabeled blobs. Membership is unguessable without the wallet's secret key (guessing a pubkey doesn't let you test if it's a recipient).

**Decrypt:** wallet re-derives identity, trial-unwraps all blobs — only the matching wrap decrypts. If not on the list, every wrap fails.

**Padded audiences:** `--pad N` adds N-1 decoy wraps to obscure recipient count.

**What it doesn't hide:** content size (bounded by ciphertext length), that a thing exists, the `.null` name it's published under. For predicate access ("anyone holding NFT X") → planned ZK membership + key-release on the Dark NULL stack. Out of scope today.

**Live in null-portal as `PrivatePay` and `PrivateInbox`** — encrypt DMs, private links, sealed content to your followers' pubkeys.

---

## x402 payment rail — full flow + security

`dna-x402`, 319 commits, CI green as of today. TypeScript, MIT.

**Flow:**
```
GET /quote?resource=/inference
→ 200 { quoteId, amountAtomic, mint, recipient, ttl }

POST /commit { quoteId, payerCommitment32B }
→ 201 { commitId }

POST /finalize { commitId, paymentProof: { settlement, txSignature } }
→ 200 { receiptId }

GET /inference  (header: x-dnp-commit-id: <commitId>)
→ 200 + resource content + receipt issued
```

**Receipt chain** (NDJSON, one per turn):
```json
{
  "receiptId": "...", "quoteId": "...", "commitId": "...",
  "settlement": "transfer", "amountAtomic": "1000", "mint": "USDC",
  "recipient": "<wallet>", "txSignature": "...",
  "requestDigest": "<sha256>", "responseDigest": "<sha256>",
  "signerPublicKey": "...", "signature": "...",
  "prevHash": "...", "receiptHash": "..."
}
```
Every receipt hashes the previous (`prevHash`) → verifiable chain per shop. `receipt_anchor` program stores SHA-256 of each receipt on-chain in 54-byte buckets, 400-year collision resistance at current Solana throughput.

**Security fix shipped today (P0 — spend ceiling identity forgery):**  
`guardActorFromRequest()` previously read `x-dna-buyer-id` from HTTP headers (forgeable). A malicious actor could exhaust another buyer's spend ceiling by forging their ID. Fix: when `identitySource === "header"`, ceiling check is skipped entirely; a `GUARD_UNVERIFIED_IDENTITY` audit event fires (severity=warn, domain=system). Ceiling enforcement only activates for `identitySource === "payment_proof"` (derived from the on-chain payment — unforgeable). Closes quota exhaustion attack vector.

**Liquefy telemetry bridge**: audit events emit as `liquefy.dna.telemetry.v1` NDJSON, proof artifacts as `liquefy.dna.proof.v1`. Drop the adapter into Liquefy's `patterns/community/`.

---

## NULLA mesh — AI worker runtime (nulla-local, shipping tonight)

**Worker registry** (SQLite-backed, TTL=300s):
- `POST /v1/workers/announce` — upsert on `worker_id`; expires in 300s; re-announce at boot
- `GET /v1/workers` — sorted by `top_tps DESC`; eviction: DELETE WHERE `expires_at ≤ now()`
- `GET /v1/workers/{id}` — full capability manifest: provider_ids, top_tps, tier, context_window, tools, price_per_token_usdc, privacy_mode

**Task market** (atomic claim):
- `GET /v1/tasks/queue` — open offers
- `POST /v1/tasks/{id}/claim` — SQLite `UPDATE status='claimed' WHERE status='open'`; 409 on race loss
- `POST /v1/tasks/{id}/complete` — releases escrow to helper; credits awarded

**Credit ledger**: `award_credits()` fires after every completed turn. `GET /v1/credits/balance`, `POST /v1/credits/settle`.

**Background daemon**: every 30s — pops `global_order_book`, gates on `HelperScheduler.can_accept_mesh_task()`, claims matching offer.

**Earnings panel** (`GET /earnings`): dark monospace dashboard polling every 10s — wallet pubkey, SOL/USDC balance, credit balance + ledger, open tasks + bids, mesh worker count, TPS/tier/price per worker, recent receipts.

**Solana anchor** (`NULLA_ANCHOR_RECEIPTS=1`): `anchor_vault_proof(session_id, result_hash, confidence=1.0)` → `receipt_anchor` on mainnet-beta after every turn. Safe stub when `solders` not installed.

CI: 2088 tests passing.

---

## Dark-Null-Protocol — ZK circuits (devnet)

Canonical program: `2stas3cZYnBiWpndcTXQDGLXwfQ7kjEYYrW52DsUAcxF`

**Circuits compiled (Circom/snarkjs):**
- `shielded_withdraw_v3.circom` — spend proof with Merkle membership + range check + nullifier
- `x402_access.circom` — prove balance clears a tier threshold without revealing amount or wallet
- `x402_access` (Noir) — same logic in Aztec's Noir for future backend flexibility
- `registrar.circom` — ownership proof for anonymous `.null` registration
- `track_record.circom` — reputation accumulation proof (GhostScore)

**Rust crate `dark-groth16-core`**: 6 embedded VKs (null_proof, registrar, shielded_withdraw_v2, shielded_withdraw_v3, track_record, x402_access). Used by the on-chain verifiers to check proofs without fetching the VK from chain.

**`dark-poseidon-real`**: native Rust Poseidon BN254, byte-identical to Solana's `sol_poseidon` syscall. Reference vectors generated with `gen_ref.mjs` and cross-checked against the on-chain output.

**Groth16 verify cost**: alt_bn128 path on Solana ≈ $0.0007 per proof. Already priced in.

**`MAINNET_READY = false` — honest:** the Powers of Tau ceremony had zero independent contributors. Every other component is production-grade. The Solana Foundation grant funds: (1) independent ceremony participants, (2) external audit of the on-chain verifier, (3) formal verification of the nullifier uniqueness property. We're not hiding the gap; it's the whole reason for the grant.

---

## null-portal — the full web app

`apps/null-portal` (Next.js 14, TypeScript, Tailwind, Three.js):

**Pages:**
- `/` — domain search + register
- `/browse` — browse all registered `.null` domains
- `/my-names` — your domains, renewal, content updates
- `/sell` + `/buy` + `/unlock` — sealed-bid auction flows
- `/pay` — send NullPay stealth payment to a `.null` name
- `/receive` — scan incoming NullPay payments (view key only)
- `/verify` — verify a VerifyBadge (passport_hash on-chain)
- `/world` — Three.js galaxy view of the .null universe: planets = domains, rings = activity, particle fields
- `/build` — AI site builder (Claude via x402, live canvas editor, 80+ templates)
- `/templates` — full template gallery
- `/visit` — browse and load any `.null` site in-portal

**AI builder** (`lib/ai/`): prompts go through x402 — each AI turn costs `amountAtomic` USDC, receipted and anchored. `apply.ts` diffs the canvas blocks and applies the model's JSON patch without reloading the whole site. `ops.ts` splits "create from scratch" vs "edit existing" vs "add section" into separate prompt strategies.

**Template library** (80+ templates in `lib/templates/`): airdrop, alpha-calls, alpha-group, auction, audit-report, based-profile, blog, brand, club, code, collab, commission, community, consulting, crew, cv, dao-gov, defi, design-studio, digital-store, directory, docs-site, drop-shop, drops, event-page, filmmaker, fintech, fomo-machine, fund, gallery, gm-page, investor-report, journal, law-firm, linkhub, longform, magazine, manifesto, memecoin, music, newsletter, nft-collection, nonprofit, photo, portfolio, profile, qr, research-paper, saas, services, startup-landing, storefront, subscription, tech-company, thread, token-launch, trading-signals, treasury, waitlist, wealth, wojak, yield-vault, zine (and more). Each template is a typed `SiteTemplate` with slot definitions — the builder patches slots; it never regenerates the whole HTML.

**World (Three.js):** `ExploreCanvas.tsx` / `LivingGalaxy.tsx` / `GalaxyBackdrop.tsx`. Planets are positioned by `galaxyLayout.ts` with galaxy seed JSON. Each planet = a `.null` domain. Click a planet → domain details fly in via `GlassPanel`. Comet trails = recent transactions. Data rings = activity volume. Star shader is a custom GLSL fragment.

---

## Web0 social substrate (design + phase 0 live)

Every entity in Web0 — person, agent, post, skill, `.null` site — is the same shape:  
**wallet-owned, signed, nameable (`.null`), tippable (x402), reputation-bearing (Dark NULL).**

**The record:**
```json
{
  "v": 1,
  "author": "<wallet pubkey>",
  "kind": "post|reply|skill|tip|rep|flag",
  "target": "<canonical id or .null board>",
  "body": "<arweave txid | inline>",
  "parent": "<record id | null>",
  "ts": 1750000000,
  "sig": "<author signature>"
}
```

**Phase 0 (live):** records stored as Arweave uploads tagged `(App=web0-social, kind, target, parent, author)`. Arweave GraphQL is the query backend — nothing new to deploy.  
**Phase 1:** tipping live (SPL transfer + memo `w0tip:1:<targetId>`). 100% to recipient — no protocol skim.  
**Phase 2 (next):** Solana state-compression — records as Merkle leaves (~$0.0001/record), DAS API for queries.

Honest: if the indexer dies, the forum is reconstructable from on-chain + Arweave data. Posts are signed records on permanent ledger. Reddit model fails this test; web0 social passes it.

---

## null-sdk (`@parad0x_labs/null-sdk`) — public API

Shipping tonight with the web0 public repo:

```typescript
// Register a .null domain
const registrar = new NullRegistrar(connection, wallet);
await registrar.register("agent.null");
await registrar.resolve("agent.null"); // → owner pubkey

// Bid in a sealed-bid auction
const auction = new NullAuction(connection, wallet);
const { commitment, secret } = poseidonCommit(bidLamports);
await auction.commitBid("hotname.null", commitment);
await auction.revealBid("hotname.null", bidLamports, secret);

// PDA derivation (deterministic, no server)
const pda = deriveNameRecord("parad0x.null");

// Poseidon commit (same hash as on-chain sol_poseidon)
const { commitment, secret } = poseidonCommit(amount);
```

MIT. All PDA derivations deterministic (sha256 name seed). Poseidon helper byte-identical to `sol_poseidon` syscall. No API key needed anywhere.

---

## All open source (MIT)

| Repo | What's in it | Commits |
|------|-------------|---------|
| `nulla-local` | NULLA AI runtime, web0 mesh, task market, credit ledger | private, 2088 tests |
| `dna-x402` | x402 payment rail, audit logger, Liquefy bridge | 319 |
| `Dark-Null-Protocol` | Groth16 circuits, Solana programs (devnet) | 60 |
| `web0` (public tonight) | null-sdk, workspace root | 10 new files |
| `web0-internal` | null-portal, dark-zk, null-mcp, web0-crypt, extensions, workers, programs | — |

All program IDs verifiable at explorer.solana.com. No hand-wavy "coming soon" program IDs — every address above either resolves on mainnet-beta right now or has a live devnet deployment with the exact ID committed.

---

## Grant call context (Solana Foundation, next week)

**What we applied for, 23 weeks ago:** x402 + Dark-Null-Protocol.  
**What happened since:** everything except the ceremony shipped to mainnet.  
**The one honest gap:** `MAINNET_READY = false` in `dark_shielded_pool`. The Groth16 circuit works. The on-chain verifier works. The math is sound. But the trusted setup ceremony had zero independent contributors — meaning you're trusting us. That's the grant: independent ceremony participants + external audit + formal verification.

That gap is not a blocker for x402, for `.null` domains, for sealed-bid auctions, for NullPay, for the receipt anchor, or for the NULLA mesh. It's a blocker for fully trustless ZK private settlement. We ship what's ready; we're honest about what needs the ceremony.

---

*Raw brief. Marketing agent: pull facts, ignore structure, make it hit.*
