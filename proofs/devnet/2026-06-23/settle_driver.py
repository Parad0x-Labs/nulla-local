#!/usr/bin/env python3
"""Devnet proof driver for the NULLA x402 pay path (canonical x402 "exact").

Runs the SHIPPING client — ``core.x402.client.X402Client.pay()`` in DEVNET mode
— to settle one real SPL-token transfer on Solana devnet via the PayAI
facilitator (/verify + /settle), and captures the proof bundle alongside it.

It does NOT reimplement the payment; it taps ``requests`` to record the exact
/supported, /verify and /settle traffic the shipping code makes, then reads the
on-chain result back from devnet so the proof is chain-truth, not self-reported.

Prerequisites (one-time setup, see README.md):
  * a funded devnet payer keypair (SOL for ATA rent; the facilitator sponsors fees)
  * a 6-decimal SPL mint the payer holds a balance of
  * the recipient's associated token account pre-created

Config comes from the environment so no secret is ever embedded here:
  PAYER_KP   path to the payer's Solana JSON keypair  (never committed)
  MINT       the SPL mint being transferred (the asset)
  PAYTO      the recipient wallet (owner) base58
  AMOUNT     decimal token amount to transfer (default 0.001)
  OUTDIR     where to write artifacts (default: this file's directory)

Reproduce:  PAYER_KP=... MINT=... PAYTO=... python settle_driver.py
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import time

import requests
from solders.keypair import Keypair
from solders.pubkey import Pubkey

from core.x402.client import (
    PAYAI_FACILITATOR,
    X402Client,
    X402Config,
    X402Mode,
    usdc_to_atomic,
)

PAYER_KP = os.environ["PAYER_KP"]
MINT     = os.environ["MINT"]
PAYTO    = os.environ["PAYTO"]
AMOUNT   = float(os.environ.get("AMOUNT", "0.001"))
OUTDIR   = os.environ.get("OUTDIR", os.path.dirname(os.path.abspath(__file__)))
RPC      = "https://api.devnet.solana.com"
TOKEN_PROGRAM = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
ATA_PROGRAM   = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")


def _write(name: str, obj) -> None:
    path = os.path.join(OUTDIR, name)
    with open(path, "w") as f:
        if isinstance(obj, str):
            f.write(obj)
        else:
            json.dump(obj, f, indent=2, sort_keys=True)
    print(f"  wrote {name}")


def _ata(owner: str) -> str:
    addr, _ = Pubkey.find_program_address(
        [bytes(Pubkey.from_string(owner)), bytes(TOKEN_PROGRAM),
         bytes(Pubkey.from_string(MINT))], ATA_PROGRAM)
    return str(addr)


def _rpc(method: str, params: list):
    r = requests.post(RPC, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                      headers={"Content-Type": "application/json", "User-Agent": "nulla-x402/1.0"},
                      timeout=20)
    r.raise_for_status()
    return r.json().get("result")


def _token_balance(ata: str):
    try:
        v = _rpc("getTokenAccountBalance", [ata, {"commitment": "confirmed"}])
        return v["value"]["uiAmountString"]
    except Exception as exc:
        return f"(none: {exc})"


def main() -> int:
    with open(PAYER_KP) as f:
        payer_owner = str(Keypair.from_seed(bytes(json.load(f)[:32])).pubkey())
    src_ata, dst_ata = _ata(payer_owner), _ata(PAYTO)
    print(f"payer={payer_owner}\n  asset(mint)={MINT}\n  payTo={PAYTO}\n  amount={AMOUNT} ({usdc_to_atomic(AMOUNT)} atomic)")

    # 1) the facilitator's advertised solana-devnet support
    supported = requests.get(f"{PAYAI_FACILITATOR}/supported",
                             headers={"User-Agent": "nulla-x402/1.0"}, timeout=20).json()
    sol_devnet = [k for k in supported.get("kinds", [])
                  if k.get("network") == "solana-devnet" and k.get("scheme") == "exact"]
    _write("supported_solana_devnet.json", sol_devnet)

    # 2) before-balances (chain truth)
    before = {"payer_ata": src_ata, "payTo_ata": dst_ata,
              "payer_token_before": _token_balance(src_ata),
              "payTo_token_before": _token_balance(dst_ata)}

    # 3) tap requests so we record exactly what the SHIPPING client sends/receives
    transcript: list = []
    real_post = requests.post

    def tap_post(url, *a, **kw):
        resp = real_post(url, *a, **kw)
        if "facilitator.payai.network" in url:
            with contextlib.suppress(Exception):
                transcript.append({"method": "POST", "url": url,
                                   "request": kw.get("json"),
                                   "status": resp.status_code, "response": resp.json()})
        return resp
    requests.post = tap_post

    # 4) settle via the SHIPPING code path
    cfg = X402Config(mode=X402Mode.DEVNET, keypair_path=PAYER_KP,
                     asset_mint=MINT, asset_decimals=6)
    client = X402Client(cfg)
    t0 = time.time()
    try:
        receipt = client.pay(amount_usdc=AMOUNT, recipient_wallet=PAYTO,
                             session_id=f"devnet-proof-{int(t0)}")
    finally:
        requests.post = real_post
    elapsed_ms = round((time.time() - t0) * 1000)

    _write("facilitator_transcript.json", transcript)
    _write("x402_receipt.json", {**receipt.to_dict(), "settle_ms": elapsed_ms})
    tx_sig = receipt.payment_tx
    explorer = f"https://explorer.solana.com/tx/{tx_sig}?cluster=devnet"
    _write("settle_tx.txt", f"{tx_sig}\n{explorer}\n")
    print(f"\nSETTLED devnet tx: {tx_sig}\n  {explorer}\n  ({elapsed_ms} ms)")

    # 5) read the settled tx back from the chain (jsonParsed, finalized)
    time.sleep(8)  # let it finalize
    gettx = _rpc("getTransaction", [tx_sig, {"encoding": "jsonParsed",
                 "commitment": "confirmed", "maxSupportedTransactionVersion": 0}])
    _write("getTransaction.json", gettx or {"error": "not found yet"})

    after = {"payer_token_after": _token_balance(src_ata),
             "payTo_token_after": _token_balance(dst_ata)}
    _write("balances.json", {**before, **after, "amount": AMOUNT})
    print(f"balances: payer {before['payer_token_before']} -> {after['payer_token_after']}, "
          f"payTo {before['payTo_token_before']} -> {after['payTo_token_after']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
