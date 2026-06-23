from __future__ import annotations

import base64
import json
import os
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from core.runtime_paths import active_nulla_home

WALLET_FILENAME = "solana_wallet.enc"
WALLET_VERSION = 1
_WALLET_AAD_PREFIX = b"nulla-solana-wallet:v1:"
_RPC_ENDPOINTS = (
    "https://solana-rpc.publicnode.com",
    "https://solana.publicnode.com",
    "https://solana.api.onfinality.io/public",
)
_USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
_BASE58_ALPHABET = b"123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_BASE58_INDEX = {char: index for index, char in enumerate(_BASE58_ALPHABET)}


def _chmod_safe(path: Path, mode: int) -> None:
    if os.name != "posix":
        return
    try:
        path.chmod(mode)
    except Exception:
        return


def _ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _chmod_safe(path, 0o700)


def _atomic_write_private(path: Path, text: str) -> None:
    _ensure_private_dir(path.parent)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent), delete=False) as handle:
        tmp_path = Path(handle.name)
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    _chmod_safe(tmp_path, 0o600)
    tmp_path.replace(path)
    _chmod_safe(path, 0o600)


def b58encode(data: bytes) -> str:
    value = int.from_bytes(data, "big")
    encoded = bytearray()
    while value:
        value, remainder = divmod(value, 58)
        encoded.append(_BASE58_ALPHABET[remainder])
    leading_zeroes = len(data) - len(data.lstrip(b"\x00"))
    encoded.extend(b"1" * leading_zeroes)
    if not encoded:
        encoded.append(_BASE58_ALPHABET[0])
    return bytes(reversed(encoded)).decode("ascii")


def b58decode(value: str) -> bytes:
    text = str(value or "").strip()
    if not text:
        raise ValueError("empty base58 value")
    number = 0
    for byte in text.encode("ascii"):
        if byte not in _BASE58_INDEX:
            raise ValueError("invalid base58 character")
        number = number * 58 + _BASE58_INDEX[byte]
    raw = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    leading_zeroes = len(text) - len(text.lstrip("1"))
    return (b"\x00" * leading_zeroes) + raw


def decode_solana_pubkey(pubkey: str) -> bytes:
    raw = b58decode(pubkey)
    if len(raw) != 32:
        raise ValueError("Solana public keys must decode to exactly 32 bytes")
    return raw


def is_solana_pubkey(pubkey: str) -> bool:
    try:
        decode_solana_pubkey(pubkey)
        return True
    except Exception:
        return False


def _pubkey_bytes(private_key: Ed25519PrivateKey) -> bytes:
    return private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def _private_seed(private_key: Ed25519PrivateKey) -> bytes:
    return private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _wallet_aad(pubkey: str) -> bytes:
    return _WALLET_AAD_PREFIX + str(pubkey).encode("ascii")


def _default_wallet_encryption_key() -> bytes:
    from network.signer import derive_local_secret

    return derive_local_secret("nulla-solana-wallet-encryption-v1", length=32)


def _rpc_call(method: str, params: list[Any], *, timeout: float = 5.0) -> Any:
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode("utf-8")
    for url in _RPC_ENDPOINTS:
        try:
            request = urllib.request.Request(
                url,
                data=payload,
                # publicnode rejects the default Python-urllib User-Agent with a
                # 403, which the except below would swallow to None — silently
                # killing every on-chain read (resolution, balance, dial). Send a
                # plain app UA so the RPC actually answers.
                headers={"Content-Type": "application/json", "User-Agent": "nulla/1.0"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
            if isinstance(data, dict) and "error" not in data:
                return data.get("result")
        except (OSError, urllib.error.URLError, json.JSONDecodeError):
            continue
    return None


@dataclass(frozen=True)
class WalletInfo:
    pubkey: str
    sol_balance: float
    usdc_balance: float


class NullaWallet:
    """NULLA's local Solana-style Ed25519 wallet.

    The private seed is encrypted with AES-256-GCM under a secret derived from
    the node signing key. Only the public key is safe to expose.
    """

    def __init__(
        self,
        *,
        runtime_home: str | Path | None = None,
        derivation_key: bytes | None = None,
        rpc_call: Callable[..., Any] | None = None,
    ) -> None:
        self._runtime_home = Path(runtime_home).expanduser().resolve() if runtime_home else active_nulla_home()
        self._keys_dir = (self._runtime_home / "data" / "keys").resolve()
        self._wallet_path = self._keys_dir / WALLET_FILENAME
        self._derivation_key = bytes(derivation_key) if derivation_key is not None else None
        self._rpc_call = rpc_call or _rpc_call
        self._private_seed_bytes: bytes | None = None
        self._public_bytes: bytes | None = None
        self._pubkey: str | None = None

    @property
    def wallet_path(self) -> Path:
        return self._wallet_path

    def exists(self) -> bool:
        return self._wallet_path.exists()

    def generate_and_save(self, *, overwrite: bool = False) -> str:
        if self.exists() and not overwrite:
            raise RuntimeError(f"Solana wallet already exists at {self._wallet_path}")
        private_key = Ed25519PrivateKey.generate()
        private_seed = _private_seed(private_key)
        public_bytes = _pubkey_bytes(private_key)
        pubkey = b58encode(public_bytes)
        nonce = os.urandom(12)
        ciphertext = AESGCM(self._encryption_key()).encrypt(nonce, private_seed, _wallet_aad(pubkey))
        envelope = {
            "version": WALLET_VERSION,
            "cipher": "AES-256-GCM",
            "pubkey": pubkey,
            "nonce_b64": base64.b64encode(nonce).decode("ascii"),
            "ciphertext_b64": base64.b64encode(ciphertext).decode("ascii"),
        }
        _atomic_write_private(self._wallet_path, json.dumps(envelope, sort_keys=True) + "\n")
        self._private_seed_bytes = private_seed
        self._public_bytes = public_bytes
        self._pubkey = pubkey
        return pubkey

    def load(self) -> NullaWallet:
        if not self.exists():
            raise RuntimeError(f"No Solana wallet exists at {self._wallet_path}")
        try:
            envelope = json.loads(self._wallet_path.read_text(encoding="utf-8"))
            version = int(envelope.get("version"))
            pubkey = str(envelope.get("pubkey") or "").strip()
            nonce = base64.b64decode(str(envelope.get("nonce_b64") or ""), validate=True)
            ciphertext = base64.b64decode(str(envelope.get("ciphertext_b64") or ""), validate=True)
        except Exception:
            raise RuntimeError("Solana wallet record is malformed")
        if version != WALLET_VERSION:
            raise RuntimeError(f"Unsupported Solana wallet version: {version}")
        decode_solana_pubkey(pubkey)
        try:
            private_seed = AESGCM(self._encryption_key()).decrypt(nonce, ciphertext, _wallet_aad(pubkey))
        except Exception:
            raise RuntimeError("Solana wallet decryption failed")
        if len(private_seed) != 32:
            raise RuntimeError("Solana wallet seed has invalid length")
        private_key = Ed25519PrivateKey.from_private_bytes(private_seed)
        public_bytes = _pubkey_bytes(private_key)
        derived_pubkey = b58encode(public_bytes)
        if derived_pubkey != pubkey:
            raise RuntimeError("Solana wallet integrity check failed")
        self._private_seed_bytes = private_seed
        self._public_bytes = public_bytes
        self._pubkey = pubkey
        _chmod_safe(self._wallet_path, 0o600)
        return self

    @property
    def pubkey(self) -> str:
        if self._pubkey is None:
            raise RuntimeError("Solana wallet is not loaded")
        return self._pubkey

    @property
    def public_key_bytes(self) -> bytes:
        if self._public_bytes is None:
            raise RuntimeError("Solana wallet is not loaded")
        return self._public_bytes

    def sign(self, payload: bytes) -> bytes:
        if self._private_seed_bytes is None:
            raise RuntimeError("Solana wallet is not loaded")
        return Ed25519PrivateKey.from_private_bytes(self._private_seed_bytes).sign(payload)

    def sign_message(self, message: bytes | str) -> bytes:
        payload = message.encode("utf-8") if isinstance(message, str) else bytes(message)
        return self.sign(payload)

    def sign_transaction(self, transaction_message: bytes) -> bytes:
        return self.sign(transaction_message)

    def get_sol_balance(self) -> float:
        result = self._rpc_call("getBalance", [self.pubkey])
        if not isinstance(result, dict):
            return 0.0
        try:
            return float(result.get("value") or 0) / 1_000_000_000
        except Exception:
            return 0.0

    def get_usdc_balance(self) -> float:
        result = self._rpc_call(
            "getTokenAccountsByOwner",
            [
                self.pubkey,
                {"mint": _USDC_MINT},
                {"encoding": "jsonParsed"},
            ],
        )
        if not isinstance(result, dict):
            return 0.0
        # A wallet can hold the same mint across multiple associated token
        # accounts; the balance is the SUM over all of them. Returning only the
        # first account's amount undercounts wallets with more than one USDC ATA.
        total = 0.0
        for account in list(result.get("value") or []):
            if not isinstance(account, dict):
                continue
            info = account.get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
            amount = info.get("tokenAmount", {}).get("uiAmount")
            if amount is None:
                continue
            try:
                total += float(amount)
            except Exception:
                continue
        return total

    def info(self) -> WalletInfo:
        return WalletInfo(
            pubkey=self.pubkey,
            sol_balance=self.get_sol_balance(),
            usdc_balance=self.get_usdc_balance(),
        )

    def export_safe(self, *, include_balances: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {"pubkey": self.pubkey}
        if include_balances:
            info = self.info()
            payload["sol_balance"] = info.sol_balance
            payload["usdc_balance"] = info.usdc_balance
        return payload

    def _encryption_key(self) -> bytes:
        if self._derivation_key is not None:
            if len(self._derivation_key) != 32:
                raise RuntimeError("Wallet derivation key must be exactly 32 bytes")
            return self._derivation_key
        return _default_wallet_encryption_key()

    def __repr__(self) -> str:
        return f"NullaWallet(pubkey={self._pubkey[:8]}...)" if self._pubkey else "NullaWallet(not loaded)"


def verify_wallet_signature(*, wallet_pubkey: str, message: bytes | str, signature: bytes) -> bool:
    payload = message.encode("utf-8") if isinstance(message, str) else bytes(message)
    try:
        Ed25519PublicKey.from_public_bytes(decode_solana_pubkey(wallet_pubkey)).verify(signature, payload)
        return True
    except Exception:
        return False


def get_or_create_wallet(
    *,
    runtime_home: str | Path | None = None,
    derivation_key: bytes | None = None,
) -> NullaWallet:
    wallet = NullaWallet(runtime_home=runtime_home, derivation_key=derivation_key)
    if not wallet.exists():
        wallet.generate_and_save()
    return wallet.load()


__all__ = [
    "NullaWallet",
    "WalletInfo",
    "b58decode",
    "b58encode",
    "decode_solana_pubkey",
    "get_or_create_wallet",
    "is_solana_pubkey",
    "verify_wallet_signature",
]
