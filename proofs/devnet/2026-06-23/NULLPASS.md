# Portable `.nullpass` — credential proof

A `.nullpass` is a self-contained, **offline-verifiable** work credential: a
`Web0WorkReceipt` + the issuer's ed25519 signature in one JSON blob. Anyone can
verify it **without NULLA's database and without the network**:

1. recompute the execution **proof hash** from the proof fields,
2. recompute the x402 **payment receipt hash** from the payment fields,
3. verify the issuer's **ed25519 signature** over the canonical receipt,
4. check the proof **binds the same result** the receipt claims,

and, optionally and online, **confirm the payment settled on-chain**.

## Live lifecycle (devnet)

Settle a real payment inside a work receipt → mint a credential → verify it both
ways:

- **Backing settlement:** [`4XEPQP84…`](https://explorer.solana.com/tx/4XEPQP84s1M9eW62pDzMWQ3zNtDbcwo6wKgkQeq6kxCPfv6qPi4fUGiFDdyxexyJNoimWLJGMnLhBjtLKMQPWzpL?cluster=devnet) (real devnet x402 settlement)
- **Offline verify:** `valid: true` — `proof_hash ✓ payment_receipt_hash ✓ signature ✓ result_binding ✓`
- **On-chain verify** (`--confirm-onchain`): `valid: true` + `settlement: true`
- Credential: [`nullpass_demo.nullpass`](nullpass_demo.nullpass)

## Forgery resistance

Two defence layers, both covered by `tests/test_nullpass.py`:

- The **issuer signature** catches any change to a signed receipt (an attacker has
  no issuer key).
- The **recomputed hashes** catch a *self-signed* forgery — an attacker who
  re-signs a doctored receipt with their own key still can't make the internal
  proof/payment hashes lie. Tamper any field → verification fails closed.

## Use it

```bash
# mint one (signed by your local wallet)
nulla nullpass issue <task-id> --result "<text>" --out work.nullpass

# verify one — offline crypto, or add --confirm-onchain to re-derive settlement
nulla nullpass verify work.nullpass [--confirm-onchain]
```

API: `core/nullpass.py` — `build_nullpass(receipt, signer=…)` / `verify_nullpass(bundle, confirm_onchain=…, rpc_call=…)`.
