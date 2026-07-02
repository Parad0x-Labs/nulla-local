from __future__ import annotations

import json
from pathlib import Path

from installer.initialize_agent_wallet import initialize_agent_wallet, main


def _is_base58_pubkey(value: str) -> bool:
    # Solana base58 pubkeys are 32-44 chars, base58 alphabet (no 0 O I l).
    alphabet = set("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")
    return 32 <= len(value) <= 44 and all(ch in alphabet for ch in value)


def test_initialize_creates_encrypted_wallet_and_returns_pubkey(tmp_path: Path) -> None:
    home = tmp_path / "runtime"
    pubkey = initialize_agent_wallet(str(home))

    assert _is_base58_pubkey(pubkey)
    wallet_file = home / "data" / "keys" / "solana_wallet.enc"
    assert wallet_file.exists()

    envelope = json.loads(wallet_file.read_text(encoding="utf-8"))
    assert envelope["cipher"] == "AES-256-GCM"
    assert envelope["pubkey"] == pubkey
    assert envelope.get("ciphertext_b64")
    # The at-rest file must carry no plaintext private material.
    assert "private" not in wallet_file.read_text(encoding="utf-8").lower()


def test_initialize_is_idempotent_across_reinstall(tmp_path: Path) -> None:
    home = tmp_path / "runtime"
    first = initialize_agent_wallet(str(home))
    second = initialize_agent_wallet(str(home))
    assert first == second, "re-running install must reuse the existing wallet, not regenerate it"


def test_main_prints_only_the_pubkey(tmp_path, capsys) -> None:
    home = tmp_path / "runtime"
    exit_code = main([str(home)])
    assert exit_code == 0

    captured = capsys.readouterr()
    printed = captured.out.strip()
    # stdout must be exactly one line: the public key. No key material, no extra chatter.
    assert "\n" not in printed
    assert _is_base58_pubkey(printed)
    assert captured.err.strip() == ""
    assert "private" not in captured.out.lower()
    assert "seed" not in captured.out.lower()


def test_main_fails_closed_without_leaking_on_bad_runtime_home(tmp_path, capsys, monkeypatch) -> None:
    # Force wallet creation to blow up and confirm the error path prints to stderr only,
    # returns non-zero, and never emits key material.
    import installer.initialize_agent_wallet as mod

    def _boom(**_kwargs):
        raise RuntimeError("simulated wallet failure")

    monkeypatch.setattr(mod, "get_or_create_wallet", _boom, raising=False)
    # get_or_create_wallet is imported inside initialize_agent_wallet(); patch at source.
    monkeypatch.setattr("core.nulla_wallet.get_or_create_wallet", _boom)

    exit_code = main([str(tmp_path / "runtime")])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out.strip() == ""
    assert "ERROR" in captured.err
    assert "private" not in captured.err.lower()
