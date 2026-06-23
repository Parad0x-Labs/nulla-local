# x402 payment — devnet-proven

NULLA's x402 pay path settles real payments on Solana using the **canonical x402
"exact" scheme** against the **PayAI facilitator** (`facilitator.payai.network`).
This is proven end-to-end on **devnet** through the shipping client.

## The proof

A real `solana-devnet` x402 settlement, driven by `X402Client.pay()` (DEVNET
mode), fee-sponsored by PayAI:

- **tx** [`4PL49Wio…E35z3e38`](https://explorer.solana.com/tx/4PL49WioTAcQng9HLqYhSb7RUYGu7n8YoHRKW54VNjMkV3Macbh4XTtfoT29peCMPDfrJjFMAuo1FhweE35z3e38?cluster=devnet) — `/verify` → `isValid: true`, `/settle` → `success: true`, on-chain balance moved payer −0.001 / payTo +0.001, fee paid by PayAI's sponsored feePayer.
- Full artifact bundle (transcript, `getTransaction`, `solana confirm`, balances, the driver): [`proofs/devnet/2026-06-23/`](../proofs/devnet/2026-06-23/README.md).

## How it works (canonical x402 "exact" on Solana)

1. Payment requirements (`scheme`, `network`, `maxAmountRequired` in atomic units,
   `payTo`, `asset` mint, `extra.feePayer`) come from a resource server's HTTP 402
   — or are built directly by the client for a client-initiated payment.
2. The client builds a **v0 transaction**: ComputeBudget limit + price, then an SPL
   `TransferChecked` of `asset` from the payer's ATA to `payTo`'s ATA, with the
   facilitator's sponsored `feePayer` as the transaction fee payer. The payer
   **partially signs** (its slot only); the facilitator fills the feePayer
   signature at settle.
3. The base64 transaction is wrapped as the x402 payment payload and POSTed to the
   facilitator `/verify`, then `/settle`. `/settle` returns the on-chain signature.

The builder is `core/x402/client.py::build_solana_x402_payment`; the orchestration
is `X402Client._solana_pay`. The destination ATA must already exist (the
facilitator settles, it does not create accounts) — pre-create it for a recipient
that may not have one.

## Configuration

```python
from core.x402.client import X402Client, X402Config, X402Mode

cfg = X402Config(
    mode=X402Mode.DEVNET,                 # or MAINNET; STUB (default) needs no network
    keypair_path="/path/to/payer.json",   # Solana CLI JSON keypair
    asset_mint=None,                       # default = cluster USDC; override for another SPL asset
)
receipt = X402Client(cfg).pay(amount_usdc=0.001, recipient_wallet="<payTo>")
# receipt.payment_tx is the real on-chain signature
```

- One facilitator host for every network (`PAYAI_FACILITATOR`); the network is a
  field in the payment, not a subdomain.
- The compute-unit limit is kept under the facilitator's sponsored-compute cap.
- The sponsored `feePayer` is read live from `GET /supported` (with a fallback).

## Notes

- **Devnet asset:** Circle's devnet USDC faucet is captcha-gated, so the devnet
  proof transfers a self-minted 6-decimal SPL token. The code path, wire format,
  transaction, and facilitator flow are identical to a mainnet USDC payment — only
  the `asset` mint differs, selected by `X402Config.asset_mint` (default = the
  cluster USDC mint).
- **`null://` dial:** the dial's pay step uses this client. The full
  dial → reach → pay round-trip additionally needs a public x402-gated receiver
  (the SSRF guard rejects loopback) and, for on-chain endpoint resolution on
  devnet, a devnet registrar (resolution is mainnet today). Those are the next
  step; the payment leg is devnet-proven here.
