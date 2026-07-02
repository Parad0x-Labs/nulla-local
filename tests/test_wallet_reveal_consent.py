from __future__ import annotations

from pathlib import Path

import pytest

from core.nulla_wallet import (
    b58decode,
    get_or_create_wallet,
    reveal_wallet_secret_key_base58,
)
from core.os_consent_gate import set_consent_override_for_tests


@pytest.fixture(autouse=True)
def _clear_override():
    set_consent_override_for_tests(None)
    yield
    set_consent_override_for_tests(None)


def test_reveal_returns_64_byte_secret_key_after_consent(tmp_path: Path) -> None:
    home = tmp_path / "runtime"
    wallet = get_or_create_wallet(runtime_home=str(home))
    pubkey = wallet.pubkey

    set_consent_override_for_tests(lambda reason: True)
    secret_b58 = reveal_wallet_secret_key_base58(runtime_home=str(home))

    raw = b58decode(secret_b58)
    # Solana secret key format is 64 bytes: 32-byte seed + 32-byte public key.
    assert len(raw) == 64
    # The trailing 32 bytes must be the wallet's public key.
    assert b58decode(pubkey) == raw[32:]


def test_reveal_denied_consent_yields_no_key(tmp_path: Path) -> None:
    home = tmp_path / "runtime"
    get_or_create_wallet(runtime_home=str(home))

    set_consent_override_for_tests(lambda reason: False)
    with pytest.raises(PermissionError):
        reveal_wallet_secret_key_base58(runtime_home=str(home))


def test_reveal_gate_runs_before_decryption(tmp_path: Path, monkeypatch) -> None:
    # Security property: if consent is denied, the wallet must never even be loaded
    # (decrypted). Assert get_or_create_wallet is not reached when consent fails.
    home = tmp_path / "runtime"
    get_or_create_wallet(runtime_home=str(home))

    import core.nulla_wallet as wallet_mod

    def _must_not_be_called(**_kwargs):
        raise AssertionError("wallet must not be decrypted when consent is denied")

    set_consent_override_for_tests(lambda reason: False)
    monkeypatch.setattr(wallet_mod, "get_or_create_wallet", _must_not_be_called)

    with pytest.raises(PermissionError):
        reveal_wallet_secret_key_base58(runtime_home=str(home))


def test_reveal_passes_reason_to_the_gate(tmp_path: Path) -> None:
    home = tmp_path / "runtime"
    get_or_create_wallet(runtime_home=str(home))

    seen = {}

    def _capture(reason: str) -> bool:
        seen["reason"] = reason
        return True

    set_consent_override_for_tests(_capture)
    reveal_wallet_secret_key_base58(runtime_home=str(home), reason="Back up wallet for cold storage")
    assert seen["reason"] == "Back up wallet for cold storage"
