"""Build (but never sign) a null_registrar v2 Register instruction for a `.null` name.

Increment 1 of the direct-sign registration feature: this module is PURE + read-only.
It derives the on-chain accounts, decodes the live RegistryConfig to quote the exact cost,
and constructs the *unsigned* Register instruction bytes. It does NOT sign, submit, hold a
key, or move any SOL — signing + broadcast (behind the two-phase consent gate + OS-consent)
is a separate module.

Byte-exact spec sourced from web0-internal `programs/null_registrar` (matches the deployed
mainnet program NXgQ…): see the [[null-registrar-v2-register-spec]] memory.

  Register (tag 0x02) data:  [0x02][name:64][arweave_txid:32][currency:1]   (currency 1=SOL)
  Register accounts (SOL):   payer(signer,writable), domain PDA(writable),
                             config PDA(writable), system program, treasury(writable)
  Domain PDA seeds:  [b"null-domain", sha256(pad_name64(name))]
  Config PDA seeds:  [b"null-registry"]
"""

from __future__ import annotations

from dataclasses import dataclass

from core.null_resolver import (
    NULL_REGISTRAR_MAINNET,
    derive_domain_pda,
    find_program_address,
    pad_name64,
)

IX_REGISTER = 0x02
CURRENCY_SOL = 0x01
CURRENCY_NULL = 0x03

REGISTRY_SEED = b"null-registry"
SYSTEM_PROGRAM_ID = "11111111111111111111111111111111"

# RegistryConfig (v2) layout — from state.rs, size 122.
_RC_DISC = 0x52  # 'R'
_RC_OFF_DISC = 0
_RC_OFF_SOL_FEE = 33
_RC_OFF_TREASURY = 81
REGISTRY_CONFIG_SIZE = 122

# NullDomain account size (v2), used to quote rent when the RPC's rent estimate is absent.
NULL_DOMAIN_SIZE = 314
_ARWEAVE_TXID_LEN = 32


def derive_config_pda(program_id: str = NULL_REGISTRAR_MAINNET) -> str | None:
    """The RegistryConfig PDA: seeds [b"null-registry"]."""
    found = find_program_address([REGISTRY_SEED], program_id)
    return found[0] if found else None


@dataclass(frozen=True)
class RegistryConfig:
    sol_fee_lamports: int
    treasury: str  # base58 pubkey


def decode_registry_config(account_data: bytes) -> RegistryConfig | None:
    """Decode the SOL fee + treasury from raw RegistryConfig account bytes.

    Returns None if the bytes are not a valid v2 config (wrong size or discriminator),
    so a caller never quotes a cost from garbage.
    """
    if account_data is None or len(account_data) < REGISTRY_CONFIG_SIZE:
        return None
    if account_data[_RC_OFF_DISC] != _RC_DISC:
        return None
    sol_fee = int.from_bytes(account_data[_RC_OFF_SOL_FEE : _RC_OFF_SOL_FEE + 8], "little")
    treasury_bytes = bytes(account_data[_RC_OFF_TREASURY : _RC_OFF_TREASURY + 32])
    try:
        from solders.pubkey import Pubkey  # type: ignore

        treasury = str(Pubkey(treasury_bytes))
    except Exception:
        return None
    return RegistryConfig(sol_fee_lamports=sol_fee, treasury=treasury)


def build_register_instruction_data(name: str, *, arweave_txid: bytes | None = None, currency: int = CURRENCY_SOL) -> bytes | None:
    """The exact Register instruction data: [0x02][name:64][arweave_txid:32][currency:1].

    Returns None if the name overflows the 64-byte field. arweave_txid defaults to 32 zero
    bytes (a bare name with no content pointer yet).
    """
    padded = pad_name64(name)
    if padded is None or len(padded) != 64:
        return None
    txid = bytes(arweave_txid or b"")
    if len(txid) == 0:
        txid = b"\x00" * _ARWEAVE_TXID_LEN
    if len(txid) != _ARWEAVE_TXID_LEN:
        return None
    if currency not in (CURRENCY_SOL, CURRENCY_NULL):
        return None
    return bytes([IX_REGISTER]) + padded + txid + bytes([currency])


@dataclass(frozen=True)
class AccountMeta:
    pubkey: str
    is_signer: bool
    is_writable: bool


def build_register_accounts(*, payer: str, domain_pda: str, config_pda: str, treasury: str) -> list[AccountMeta]:
    """The Register account list in program order for the SOL path."""
    return [
        AccountMeta(payer, is_signer=True, is_writable=True),
        AccountMeta(domain_pda, is_signer=False, is_writable=True),
        AccountMeta(config_pda, is_signer=False, is_writable=True),
        AccountMeta(SYSTEM_PROGRAM_ID, is_signer=False, is_writable=False),
        AccountMeta(treasury, is_signer=False, is_writable=True),
    ]


@dataclass(frozen=True)
class RegisterPlan:
    """Everything needed to preview (and later sign) a registration — but not signed."""

    name: str
    program_id: str
    domain_pda: str
    config_pda: str
    owner: str
    treasury: str
    sol_fee_lamports: int
    rent_lamports: int
    instruction_data_hex: str
    accounts: list[AccountMeta]
    network: str = "mainnet-beta"

    @property
    def total_lamports(self) -> int:
        return int(self.sol_fee_lamports) + int(self.rent_lamports)

    @property
    def total_sol(self) -> float:
        return self.total_lamports / 1_000_000_000


def build_register_plan(
    *,
    name: str,
    owner_pubkey: str,
    config: RegistryConfig,
    rent_lamports: int,
    program_id: str = NULL_REGISTRAR_MAINNET,
) -> RegisterPlan | None:
    """Assemble a complete, unsigned registration plan for `name`.

    `config` (treasury + live SOL fee) and `rent_lamports` are read on-chain by the caller
    and passed in, so this stays pure and unit-testable. Returns None if the name is invalid
    or the PDAs can't be derived.
    """
    data = build_register_instruction_data(name, currency=CURRENCY_SOL)
    if data is None:
        return None
    domain_pda = derive_domain_pda(name, program_id=program_id)
    config_pda = derive_config_pda(program_id)
    if not domain_pda or not config_pda:
        return None
    accounts = build_register_accounts(
        payer=owner_pubkey, domain_pda=domain_pda, config_pda=config_pda, treasury=config.treasury
    )
    return RegisterPlan(
        name=name,
        program_id=program_id,
        domain_pda=domain_pda,
        config_pda=config_pda,
        owner=owner_pubkey,
        treasury=config.treasury,
        sol_fee_lamports=int(config.sol_fee_lamports),
        rent_lamports=int(rent_lamports),
        instruction_data_hex=data.hex(),
        accounts=accounts,
    )
