from __future__ import annotations

import pytest

from core.null_registrar import (
    CURRENCY_SOL,
    IX_REGISTER,
    NULL_DOMAIN_SIZE,
    RegistryConfig,
    build_register_accounts,
    build_register_instruction_data,
    build_register_plan,
    decode_registry_config,
    derive_config_pda,
)

solders = pytest.importorskip("solders")
from solders.pubkey import Pubkey


def _config_bytes(sol_fee: int, treasury: Pubkey) -> bytes:
    buf = bytearray(122)
    buf[0] = 0x52  # 'R' discriminator
    buf[33:41] = int(sol_fee).to_bytes(8, "little")  # sol_fee_lamports @33
    buf[81:113] = bytes(treasury)  # treasury @81
    buf[121] = 255  # bump
    return bytes(buf)


def test_register_instruction_data_is_byte_exact() -> None:
    data = build_register_instruction_data("test", currency=CURRENCY_SOL)
    assert data is not None
    assert len(data) == 1 + 64 + 32 + 1  # tag + name + arweave_txid + currency
    assert data[0] == IX_REGISTER  # 0x02
    assert data[1:5] == b"test"
    assert data[5:65] == b"\x00" * 60  # name null-padded to 64
    assert data[65:97] == b"\x00" * 32  # bare name → zero arweave_txid
    assert data[97] == CURRENCY_SOL  # 0x01


def test_register_instruction_data_rejects_bad_inputs() -> None:
    assert build_register_instruction_data("a" * 65) is None  # overflows 64-byte field
    assert build_register_instruction_data("test", currency=0x09) is None  # unknown currency
    assert build_register_instruction_data("test", arweave_txid=b"\x01" * 31) is None  # wrong txid len


def test_register_instruction_data_carries_content_pointer() -> None:
    txid = bytes(range(32))
    data = build_register_instruction_data("mysite", arweave_txid=txid)
    assert data is not None
    assert data[65:97] == txid


def test_register_accounts_order_and_flags() -> None:
    metas = build_register_accounts(payer="Payer11111", domain_pda="Dom11", config_pda="Cfg11", treasury="Trez11")
    assert [m.pubkey for m in metas] == ["Payer11111", "Dom11", "Cfg11", "11111111111111111111111111111111", "Trez11"]
    assert metas[0].is_signer and metas[0].is_writable  # payer signs + pays
    assert not metas[1].is_signer and metas[1].is_writable  # domain PDA writable
    assert not metas[2].is_signer and metas[2].is_writable  # config PDA writable
    assert not metas[3].is_signer and not metas[3].is_writable  # system program
    assert not metas[4].is_signer and metas[4].is_writable  # treasury receives fee


def test_decode_registry_config_reads_fee_and_treasury() -> None:
    treasury = Pubkey.from_string("So11111111111111111111111111111111111111112")
    cfg = decode_registry_config(_config_bytes(sol_fee=7000, treasury=treasury))
    assert cfg is not None
    assert cfg.sol_fee_lamports == 7000
    assert cfg.treasury == str(treasury)


def test_decode_registry_config_rejects_bad_data() -> None:
    treasury = Pubkey.from_string("So11111111111111111111111111111111111111112")
    good = bytearray(_config_bytes(sol_fee=0, treasury=treasury))
    good[0] = 0x00  # wrong discriminator
    assert decode_registry_config(bytes(good)) is None
    assert decode_registry_config(b"\x52" + b"\x00" * 10) is None  # too short
    assert decode_registry_config(None) is None


def test_derive_config_pda_is_deterministic_off_curve() -> None:
    a = derive_config_pda()
    b = derive_config_pda()
    assert a and a == b  # stable
    # It's a real base58 pubkey and off-curve (a PDA), i.e. find_program_address succeeded.
    assert Pubkey.from_string(a) is not None


def test_build_register_plan_ties_it_together_with_cost() -> None:
    treasury = Pubkey.from_string("So11111111111111111111111111111111111111112")
    config = RegistryConfig(sol_fee_lamports=0, treasury=str(treasury))  # pilot: free fee
    plan = build_register_plan(
        name="parad0x",
        owner_pubkey=str(Pubkey.from_string("So11111111111111111111111111111111111111112")),
        config=config,
        rent_lamports=2_700_000,  # ~0.0027 SOL for the 314-byte account
    )
    assert plan is not None
    assert plan.network == "mainnet-beta"
    assert plan.treasury == str(treasury)
    assert plan.sol_fee_lamports == 0
    assert plan.total_lamports == 2_700_000  # rent only in pilot
    assert 0.002 < plan.total_sol < 0.004
    assert plan.instruction_data_hex.startswith("02")  # Register tag
    assert plan.accounts[0].is_signer  # owner signs
    assert NULL_DOMAIN_SIZE == 314
