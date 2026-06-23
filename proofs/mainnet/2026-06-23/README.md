# x402 MAINNET settlement proof — 2026-06-23

A real **canonical x402 "exact"** payment settled on **Solana mainnet** through the
shipping NULLA CLI (`nulla x402-pay … --mainnet --allow-spend` →
`core/x402/client.py` `X402Client.pay()`), against the live **PayAI facilitator**
(`/verify` then `/settle`). Real USDC moved on-chain; the facilitator sponsored
the transaction fee.

## The settlement

| | |
|---|---|
| **Transaction** | [`37o7An4AUJDQqyZK2LvdGARHFpEQK3ZJiZSJwwADoeqeMHYUtSv9uddMzCcueBLU5p1S3fWDBb2FoWAMBCMn7F5a`](https://explorer.solana.com/tx/37o7An4AUJDQqyZK2LvdGARHFpEQK3ZJiZSJwwADoeqeMHYUtSv9uddMzCcueBLU5p1S3fWDBb2FoWAMBCMn7F5a) |
| Network | `solana` (mainnet; x402 v1, scheme `exact`) |
| Asset | `EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v` — real mainnet USDC |
| Amount | 0.001 USDC (1000 atomic units) |
| Fee payer | `2wKupLR9q6wXYppw8Gr2NvWxKBUqm4PPJKkQfoxHDBg4` — PayAI's sponsored feePayer (paid the SOL fee; the payer did not) |
| USDC delta | payer `J7kAWMC6…` 0.02 → 0.019 (−0.001) ; recipient `9vDnXsPo…` 0.034593 → 0.035593 (+0.001) |
| Slot | 428346414 — `err: None`, version 0 |

The payer was a throwaway burner funded with a sliver of USDC + a little SOL; the
0.001 round-tripped to the operator's own wallet. The burner key never entered the
repo and was deleted after the run — only the public signature/artifacts are here.

## Artifacts

| file | what it shows |
|---|---|
| `settle_tx.txt` | the settled mainnet signature + explorer link |
| `getTransaction.json` | full mainnet `getTransaction` (jsonParsed) — pre/post USDC balances, sponsored fee payer |
| `solana_confirm.txt` | independent `solana confirm -v <sig>` (`Status: Ok`) |
| `verify.txt` | parsed on-chain check: success, v0, sponsored fee payer, ±0.001 USDC delta |

## What this proves

The NULLA x402 pay path settles **real USDC on mainnet** via the canonical x402
`exact` scheme — the same shipping code (`X402Client.pay`) proven on devnet under
[`../../devnet/2026-06-23/`](../../devnet/2026-06-23/README.md). Mainnet uses real
USDC and the `solana` network; the only difference from the devnet run is the
network + asset mint, both selected by config.
