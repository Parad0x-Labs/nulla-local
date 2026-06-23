#!/usr/bin/env python3
"""Minimal x402-gated resource server for the null:// dial round-trip proof.

Behaviour (canonical x402, server-verified unlock):
  * POST without an ``X-PAYMENT-RECEIPT`` header  -> HTTP 402 + {"accepts": [requirements]}
  * POST with ``X-PAYMENT-RECEIPT: <tx-sig>``      -> verify the settlement on-chain
    (the tx succeeded and moved >= the asked amount of ``asset`` to ``payTo``),
    then HTTP 200 + the unlocked service result.

It is the "named agent" the dial reaches. Run it behind a public tunnel
(cloudflared) so the dial's SSRF guard — which rejects loopback/private hosts —
accepts the endpoint.

Config via env:
  PORT, PAYTO, ASSET (mint), AMOUNT_ATOMIC, NETWORK (solana-devnet|solana),
  FEEPAYER, RPC, MEMO, SERVICE_RESULT
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(os.environ.get("PORT", "8799"))
PAYTO = os.environ["PAYTO"]
ASSET = os.environ["ASSET"]
AMOUNT_ATOMIC = int(os.environ.get("AMOUNT_ATOMIC", "1000"))
NETWORK = os.environ.get("NETWORK", "solana-devnet")
FEEPAYER = os.environ.get("FEEPAYER", "2wKupLR9q6wXYppw8Gr2NvWxKBUqm4PPJKkQfoxHDBg4")
RPC = os.environ.get("RPC", "https://api.devnet.solana.com")
MEMO = os.environ.get("MEMO", "NULLA null:// dial — x402 'exact' agent payment")
SERVICE_RESULT = os.environ.get(
    "SERVICE_RESULT",
    "web0.null summary: a permissionless, name-addressed agent web — resolve a "
    ".null name, reach the agent, pay it over x402, get the result.",
)


def _requirements() -> dict:
    return {
        "scheme": "exact",
        "network": NETWORK,
        "maxAmountRequired": str(AMOUNT_ATOMIC),
        "resource": "https://nulla.agent/x402/demo",
        "description": "NULLA demo agent — pays-per-call over x402",
        "mimeType": "application/json",
        "payTo": PAYTO,
        "maxTimeoutSeconds": 120,
        "asset": ASSET,
        "extra": {"feePayer": FEEPAYER, "memo": MEMO},
    }


def _rpc(method: str, params: list):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(
        RPC, data=body,
        headers={"Content-Type": "application/json", "User-Agent": "nulla-receiver/1.0"},
        method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode()).get("result")


def _payment_confirmed(sig: str) -> bool:
    """True once the tx is on-chain, succeeded, and credited >= the ask to payTo."""
    for _ in range(8):
        try:
            tx = _rpc("getTransaction", [sig, {"encoding": "jsonParsed",
                      "commitment": "confirmed", "maxSupportedTransactionVersion": 0}])
        except Exception:
            tx = None
        if tx and tx.get("meta") and tx["meta"].get("err") is None:
            meta = tx["meta"]
            pre = {b["accountIndex"]: b for b in meta.get("preTokenBalances", [])
                   if b.get("mint") == ASSET and b.get("owner") == PAYTO}
            post = {b["accountIndex"]: b for b in meta.get("postTokenBalances", [])
                    if b.get("mint") == ASSET and b.get("owner") == PAYTO}
            for idx, pb in post.items():
                before = int((pre.get(idx, {}).get("uiTokenAmount", {}) or {}).get("amount", "0"))
                after = int(pb["uiTokenAmount"]["amount"])
                if after - before >= AMOUNT_ATOMIC:
                    return True
        time.sleep(4)
    return False


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, payload: dict) -> None:
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length:
            self.rfile.read(length)
        receipt = self.headers.get("X-PAYMENT-RECEIPT", "").strip()
        if not receipt:
            self._send(402, {"error": "payment_required", "accepts": [_requirements()]})
            return
        if _payment_confirmed(receipt):
            self._send(200, {"result": SERVICE_RESULT, "paid_tx": receipt, "verified": True})
        else:
            self._send(402, {"error": "payment_not_confirmed", "accepts": [_requirements()]})

    def log_message(self, *_a):  # quiet
        return


if __name__ == "__main__":
    print(f"receiver on :{PORT} | payTo={PAYTO} asset={ASSET} amount={AMOUNT_ATOMIC} network={NETWORK}", flush=True)
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
