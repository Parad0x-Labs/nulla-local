# null:// dial — full round-trip proof (devnet)

The complete `null://` dial, end-to-end, through the shipping `core.null_dial`:
**reach a named agent → get its x402 402 → pay it (canonical x402, real settle) →
the agent verifies the payment on-chain → it unlocks and returns the result.**

## The round-trip

| step | what happened |
|---|---|
| 1. reach | the dial POSTs the task to the agent's endpoint |
| 2. 402 | the agent replies `402` with x402 `paymentRequirements` (asset, payTo, amount, sponsored feePayer, **memo**) |
| 3. pay | the dial settles via the canonical client (`X402Client.pay_requirements`, signing with the dial's wallet) — a **real on-chain devnet settlement** |
| 4. verify | the agent reads the tx back from devnet and confirms it credited the asked amount to `payTo` |
| 5. unlock | the agent returns the service result |

**Settled tx:** [`WR2syULnAAyfnbBjjvwXrAuexiGnDH88KLyKe5c2PogRidoHE7xs7MLWU1frs1Bzn5WAXCUZ6jvKdnhiAxs66hz`](https://explorer.solana.com/tx/WR2syULnAAyfnbBjjvwXrAuexiGnDH88KLyKe5c2PogRidoHE7xs7MLWU1frs1Bzn5WAXCUZ6jvKdnhiAxs66hz?cluster=devnet)
— `err: None`, v0 tx, and **self-describing** via an on-chain memo:

> `Memo (len 103): "NULLA null:// dial — agent paid agent over x402 'exact' for 'summarize web0.null' (devnet round-trip)"`

**Unlocked result** (returned only after on-chain verification, `verified: true`):

> web0.null summary: a permissionless, name-addressed agent web — resolve a .null name, reach the agent, pay it over x402, get the result.

## Files

| file | what |
|---|---|
| `roundtrip_receiver.py` | the x402-gated "named agent": issues the 402, verifies the settlement on-chain, then unlocks |
| `roundtrip_driver.py` | drives the shipping `try_dial` against the agent |
| `roundtrip_result.json` | the dial's return — `status: paid`, the tx, and the unlocked result |
| `roundtrip_getTransaction.json` | the settled tx read back from devnet (memo + transfer) |

## Scope / honesty

- The agent ran on `localhost` for this capture; the dial's **SSRF guard rejects
  loopback by design** (a public host is required in normal use, and the guard's
  rejection matrix is covered by `tests/test_null_dial_ssrf.py`). Running the
  agent behind a public tunnel exercises only that guard, not the dial logic —
  which is why this proof drives the dial's reach→pay→unlock against the agent
  directly. The payment leg is a real on-chain devnet settlement either way.
- Devnet uses a self-minted 6-decimal test token (Circle's devnet-USDC faucet is
  captcha-gated). Mainnet uses real USDC; the flow is identical — see
  [`../../mainnet/2026-06-23/`](../../mainnet/2026-06-23/README.md).
