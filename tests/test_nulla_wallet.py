from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from core.nulla_wallet import NullaWallet, decode_solana_pubkey, verify_wallet_signature


def test_wallet_generates_encrypted_keypair_and_loads_roundtrip(tmp_path: Path) -> None:
    wallet = NullaWallet(runtime_home=tmp_path / "runtime", derivation_key=b"w" * 32)

    pubkey = wallet.generate_and_save()
    loaded = NullaWallet(runtime_home=tmp_path / "runtime", derivation_key=b"w" * 32).load()
    signature = loaded.sign_message("hello wallet")

    assert len(decode_solana_pubkey(pubkey)) == 32
    assert loaded.pubkey == pubkey
    assert verify_wallet_signature(wallet_pubkey=pubkey, message="hello wallet", signature=signature)
    assert loaded.export_safe(include_balances=False) == {"pubkey": pubkey}
    envelope = json.loads(wallet.wallet_path.read_text(encoding="utf-8"))
    assert envelope["pubkey"] == pubkey
    assert "ciphertext_b64" in envelope
    assert "private" not in envelope
    if os.name == "posix":
        assert (wallet.wallet_path.stat().st_mode & 0o777) == 0o600


def test_wallet_rejects_wrong_derivation_key(tmp_path: Path) -> None:
    wallet = NullaWallet(runtime_home=tmp_path / "runtime", derivation_key=b"a" * 32)
    wallet.generate_and_save()

    with pytest.raises(RuntimeError):
        NullaWallet(runtime_home=tmp_path / "runtime", derivation_key=b"b" * 32).load()


def test_wallet_balance_export_uses_injected_rpc_without_leaking_key(tmp_path: Path) -> None:
    def fake_rpc(method: str, params: list[object]) -> dict[str, object]:
        if method == "getBalance":
            return {"value": 2_500_000_000}
        if method == "getTokenAccountsByOwner":
            return {
                "value": [
                    {
                        "account": {
                            "data": {
                                "parsed": {
                                    "info": {
                                        "tokenAmount": {"uiAmount": 12.75},
                                    }
                                }
                            }
                        }
                    }
                ]
            }
        raise AssertionError(method)

    wallet = NullaWallet(runtime_home=tmp_path / "runtime", derivation_key=b"r" * 32, rpc_call=fake_rpc)
    wallet.generate_and_save()

    exported = wallet.export_safe()

    assert exported["pubkey"] == wallet.pubkey
    assert exported["sol_balance"] == 2.5
    assert exported["usdc_balance"] == 12.75
    assert "private" not in exported
    assert "seed" not in exported
