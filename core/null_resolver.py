"""Read-only on-chain resolution of .null domain names.

A NULLA node can resolve a .null name to its on-chain record — owner, Arweave
content id, and (the part that matters for the compute market) the x402 payment
endpoint — straight from the deployed NULL registrar over the compliant
publicnode RPC. No write, no signing, no key.

The byte layout is the authoritative on-chain `NullDomain` struct
(programs/null_registrar/src/state.rs v1); offsets are kept in lockstep with the
web0-resolver browser extension's codec.js. Resolution uses getProgramAccounts +
memcmp on the stored bytes (exact match, no client-side PDA derivation, so no
ed25519 curve dependency is needed).
"""
from __future__ import annotations

import base64
from dataclasses import dataclass

# Reuse the ONE compliant RPC path (publicnode endpoints only — never the
# Origin-403 api.mainnet-beta endpoint) and the base58 codec, so there is no
# second place an endpoint list could drift out of policy.
from core.nulla_wallet import _rpc_call, b58encode
from core.x402.client import NULL_REGISTRAR_MAINNET

# --- authoritative NullDomain layout (314 bytes) ---------------------------
NULL_DOMAIN_SIZE = 314
NULL_DOMAIN_DISC = 0x4E  # 'N'
_OFF_NAME = 1
_OFF_OWNER = 65
_OFF_ARWEAVE_TXID = 97
_OFF_X402_ENDPOINT = 129
_OFF_PASSPORT_HASH = 257
_NAME_LEN = 64
_X402_ENDPOINT_LEN = 128
_PUBKEY_LEN = 32


@dataclass(frozen=True)
class NullDomainRecord:
    """A resolved .null domain. `x402_endpoint` is "" when unset; `arweave_txid`
    and `passport_hash` are None when the on-chain field is all-zero."""
    name: str
    owner: str  # base58 pubkey
    arweave_txid: str | None  # base64url (Arweave tx id) or None
    x402_endpoint: str  # "" when unset
    passport_hash: str | None  # hex or None


def pad_name64(name: str) -> bytes | None:
    """UTF-8 name as the on-chain 64-byte null-padded field; None if it overflows."""
    utf8 = name.encode("utf-8")
    if len(utf8) > _NAME_LEN:
        return None
    return utf8 + b"\x00" * (_NAME_LEN - len(utf8))


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def decode_null_domain(raw: bytes) -> NullDomainRecord | None:
    """Decode a raw NullDomain account blob. None if too short / wrong discriminator."""
    if len(raw) < NULL_DOMAIN_SIZE or raw[0] != NULL_DOMAIN_DISC:
        return None
    name = raw[_OFF_NAME : _OFF_NAME + _NAME_LEN].split(b"\x00", 1)[0].decode("utf-8", "replace")
    owner = b58encode(raw[_OFF_OWNER : _OFF_OWNER + _PUBKEY_LEN])
    txid_raw = raw[_OFF_ARWEAVE_TXID : _OFF_ARWEAVE_TXID + 32]
    arweave_txid = _b64url(txid_raw) if any(txid_raw) else None
    ep = raw[_OFF_X402_ENDPOINT : _OFF_X402_ENDPOINT + _X402_ENDPOINT_LEN]
    cut = ep.find(b"\x00")
    x402_endpoint = ep[: (cut if cut != -1 else len(ep))].decode("utf-8", "replace")
    ph = raw[_OFF_PASSPORT_HASH : _OFF_PASSPORT_HASH + 32]
    passport_hash = ph.hex() if any(ph) else None
    return NullDomainRecord(
        name=name, owner=owner, arweave_txid=arweave_txid,
        x402_endpoint=x402_endpoint, passport_hash=passport_hash,
    )


def domain_filters(name: str) -> list[dict] | None:
    """getProgramAccounts memcmp filters that uniquely select a NullDomain by name."""
    padded = pad_name64(name)
    if padded is None:
        return None
    return [
        {"dataSize": NULL_DOMAIN_SIZE},
        {"memcmp": {"offset": 0, "bytes": b58encode(bytes([NULL_DOMAIN_DISC]))}},
        {"memcmp": {"offset": _OFF_NAME, "bytes": b58encode(padded)}},
    ]


def resolve_null_domain(
    name: str, *, program_id: str = NULL_REGISTRAR_MAINNET, timeout: float = 5.0
) -> NullDomainRecord | None:
    """Resolve a .null name on mainnet (read-only). None if unresolved or RPC unreachable."""
    filters = domain_filters(name)
    if filters is None:
        return None
    result = _rpc_call(
        "getProgramAccounts",
        [program_id, {"encoding": "base64", "filters": filters}],
        timeout=timeout,
    )
    if not result:
        return None
    for entry in result:
        try:
            data_field = entry["account"]["data"]
            b64 = data_field[0] if isinstance(data_field, list) else data_field
            raw = base64.b64decode(b64)
        except (KeyError, TypeError, ValueError):
            continue
        rec = decode_null_domain(raw)
        if rec is not None and rec.name == name:
            return rec
    return None


def resolve_x402_endpoint(name: str, **kwargs) -> str | None:
    """Convenience: a .null name -> its x402 payment endpoint URL, or None."""
    rec = resolve_null_domain(name, **kwargs)
    if rec is None or not rec.x402_endpoint:
        return None
    return rec.x402_endpoint


__all__ = [
    "NULL_DOMAIN_SIZE",
    "NullDomainRecord",
    "decode_null_domain",
    "domain_filters",
    "pad_name64",
    "resolve_null_domain",
    "resolve_x402_endpoint",
]
