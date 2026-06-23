# x402 devnet settlement proof — 2026-06-23

A real **canonical x402 "exact"** payment settled on **Solana devnet** through the
shipping client (`core/x402/client.py` → `X402Client.pay()` in DEVNET mode),
against the live **PayAI facilitator** (`/verify` then `/settle`). The payment
moved real tokens on-chain; the facilitator sponsored the transaction fee.

## The settlement

| | |
|---|---|
| **Transaction** | [`4PL49WioTAcQng9HLqYhSb7RUYGu7n8YoHRKW54VNjMkV3Macbh4XTtfoT29peCMPDfrJjFMAuo1FhweE35z3e38`](https://explorer.solana.com/tx/4PL49WioTAcQng9HLqYhSb7RUYGu7n8YoHRKW54VNjMkV3Macbh4XTtfoT29peCMPDfrJjFMAuo1FhweE35z3e38?cluster=devnet) |
| Network | `solana-devnet` (x402 v1, scheme `exact`) |
| Payer | `HREKZMe5KyqMA46k5thX2CA1CdCP6rjKj9vXFtmUpM13` |
| Recipient (payTo) | `Bpd1AZSRpPR9XRDbiS9n9i4WpqsxyswJNE3A4fnJmtw7` |
| Asset (mint) | `GdHN5TcjZ4vDbacspJ9TUBquNrirozGAeLZFda3SrL4e` (6-decimal devnet test token) |
| Amount | 0.001 (1000 atomic units) |
| Fee payer | `2wKupLR9q6wXYppw8Gr2NvWxKBUqm4PPJKkQfoxHDBg4` — PayAI's sponsored feePayer (paid the SOL fee, not the payer) |
| Settle latency | ~2.9 s |

**Verified on-chain** (see `verify.txt`): tx succeeded (`err: None`), it is a v0
versioned transaction, `accountKeys[0]` (the fee payer) is PayAI's sponsored
account, and the token balances moved payer −0.001 / payTo +0.001 for the asset
mint. The facilitator returned `isValid: true` then `success: true`.

## Artifacts

| file | what it shows |
|---|---|
| `settle_tx.txt` | the settled devnet signature + explorer link |
| `facilitator_transcript.json` | the exact `/verify` and `/settle` request/response the shipping client made |
| `x402_receipt.json` | the `X402Receipt` the client returned (real `payment_tx`, `mode: devnet`) |
| `getTransaction.json` | full devnet `getTransaction` (jsonParsed) — pre/post token balances, fee payer |
| `solana_confirm.txt` | independent `solana confirm -v <sig> --url devnet` (`Status: Ok`) |
| `verify.txt` | parsed on-chain check: success, v0, sponsored fee payer, ±0.001 delta |
| `balances.json` | payer/payTo token balances before and after |
| `supported_solana_devnet.json` | PayAI `/supported` solana-devnet entry (advertised feePayer) |
| `settle_driver.py` | the committed driver that produced this bundle |

## Scope of this proof

The asset is a self-minted 6-decimal SPL token, not Circle's devnet USDC
(`4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU`) — Circle's devnet-USDC faucet is
captcha-gated, so a self-minted 6-decimal token stands in for the proof. The code
path, the x402 wire format, the v0 `TransferChecked` transaction, the partial-sign
flow, and the `/verify`+`/settle` calls are **identical** to a mainnet USDC
payment; only the `asset` mint differs, and it is selected by config
(`X402Config.asset_mint`, default = the cluster USDC mint).

## Reproduce

Prerequisites: a funded devnet payer keypair (a little SOL for ATA rent — the
facilitator sponsors the transaction fee), a 6-decimal SPL mint the payer holds a
balance of, and the recipient's associated token account pre-created.

One-time setup (Solana CLI):

```bash
PAYER=~/.config/solana/<payer>.json
PUB=$(solana-keygen pubkey "$PAYER")
# fund the payer with a little devnet SOL (e.g. transfer from a funded wallet)
MINT=$(spl-token create-token --url devnet --fee-payer "$PAYER" --mint-authority "$PUB" --decimals 6 | awk '/^Address:/{print $2}')
spl-token create-account "$MINT" --url devnet --fee-payer "$PAYER" --owner "$PUB"
PAYER_ATA=$(spl-token address --token "$MINT" --owner "$PUB" --url devnet --verbose | awk '/Associated/{print $NF}')
spl-token mint "$MINT" 100 "$PAYER_ATA" --url devnet --fee-payer "$PAYER" --mint-authority "$PAYER"
# recipient + its ATA
solana-keygen new --no-bip39-passphrase --outfile /tmp/payto.json --force
PAYTO=$(solana-keygen pubkey /tmp/payto.json)
spl-token create-account "$MINT" --url devnet --fee-payer "$PAYER" --owner "$PAYTO"
```

Then settle through the shipping client:

```bash
PAYER_KP="$PAYER" MINT="$MINT" PAYTO="$PAYTO" AMOUNT=0.001 \
  .venv/bin/python proofs/devnet/2026-06-23/settle_driver.py
```

The keypair files stay outside the repo and are never committed — only public
artifacts (pubkeys, signatures, transcripts) live here.
