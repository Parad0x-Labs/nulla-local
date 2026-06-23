#!/usr/bin/env python3
"""Drive the FULL null:// dial round-trip against a live (tunneled) receiver.

Exercises the shipping ``core.null_dial.try_dial``: reach the agent's endpoint ->
get its x402 402 -> settle via the canonical client (memo'd) -> re-request with
the proof -> return the unlocked result. Devnet (the receiver's 402 picks the
network); the same flow runs on mainnet by funding a mainnet wallet + asset.

Env: ENDPOINT (the receiver's public URL), PAYER_KP (Solana JSON keypair).
"""
from __future__ import annotations

import json
import os

from solders.keypair import Keypair

from core.null_dial import try_dial
from core.null_resolver import NullDomainRecord

ENDPOINT = os.environ["ENDPOINT"]
PAYER_KP = os.environ["PAYER_KP"]
MAX_SPEND = float(os.environ.get("MAX_SPEND", "0.01"))


class _KPWallet:
    """A NullaWallet-shaped signer backed by a Solana JSON keypair (pubkey()/sign())."""

    def __init__(self, kp: Keypair) -> None:
        self._kp = kp

    def pubkey(self) -> str:
        return str(self._kp.pubkey())

    def sign(self, payload: bytes) -> bytes:
        return bytes(self._kp.sign_message(bytes(payload)))


def main() -> int:
    with open(PAYER_KP) as f:
        kp = Keypair.from_seed(bytes(json.load(f)[:32]))
    record = NullDomainRecord(
        name="web0", owner=str(kp.pubkey()), arweave_txid=None,
        x402_endpoint=ENDPOINT, passport_hash=None,
    )
    print(f"dialing {ENDPOINT} as {kp.pubkey()} (allow_spend, cap {MAX_SPEND})", flush=True)
    out = try_dial(
        "null://web0/summarize", "summarize web0.null",
        record=record, wallet=_KPWallet(kp),
        allow_spend=True, max_spend_usdc=MAX_SPEND,
    )
    print(json.dumps(out, indent=2, sort_keys=True))
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "roundtrip_result.json"), "w") as f:
        json.dump(out, f, indent=2, sort_keys=True)
    return 0 if isinstance(out, dict) and out.get("status") == "paid" else 1


if __name__ == "__main__":
    raise SystemExit(main())
