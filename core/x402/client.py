"""
core/x402/client.py
===================
Minimal x402 payment client for the NULLA compute rental mesh.

Implements the HTTP 402 payment-required flow for agent-to-agent
USDC settlements on Solana, matching the dna-x402 public-beta protocol.

Modes
-----
stub    — default; deterministic fake receipt, no Solana calls. Existing
          tests pass unmodified. Safe for CI and offline development.
devnet  — real USDC transfer on Solana devnet via a live x402 facilitator
          (requires funded devnet wallet at config.keypair_path).
mainnet — production; same flow against mainnet RPC + PayAI facilitator.

Protocol (x402 Solana path)
---------------------------
1. client POSTs to {endpoint}/x402/quote → 402 with payment details
   (or client builds quote from known price + recipient directly)
2. client constructs USDC SPL transfer tx: client_ata → facilitator_ata
3. client signs + submits tx to Solana RPC
4. client POSTs tx sig to facilitator → signed X402Receipt
5. receipt.receipt_hash is included in WorkProof.signature for anchoring

Usage
-----
    from core.x402.client import X402Client, X402Config, X402Mode

    cfg = X402Config(mode=X402Mode.STUB)
    client = X402Client(cfg)
    receipt = client.pay(amount_usdc=0.001, recipient_wallet="<pubkey>",
                         session_id="sess-abc123")
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

# ---------------------------------------------------------------------------
# USDC constants
# ---------------------------------------------------------------------------

USDC_DECIMALS = 6                                     # USDC has 6 decimal places
USDC_MINT_MAINNET = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDC_MINT_DEVNET  = "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU"

def usdc_to_atomic(amount_usdc: float) -> int:
    """Convert a USDC amount to atomic units (6 decimals), rounding to nearest.

    Truncating (``int(amount_usdc * 10**6)``) undercounts: 0.0000019 USDC would
    floor to 1 atomic unit instead of 2, and float artefacts like
    1.999999... * 10**6 would drop a whole unit. Rounding keeps the on-chain
    transfer amount consistent with the rounded values sent to the facilitator
    /quote and /receipt endpoints (both ``round(amount_usdc, 6)``).
    """
    return round(amount_usdc * (10 ** USDC_DECIMALS))


# PayAI facilitator — primary Solana x402 facilitator (public beta)
PAYAI_FACILITATOR_MAINNET = "https://facilitator.payai.network"
PAYAI_FACILITATOR_DEVNET  = "https://devnet.facilitator.payai.network"

# Solana RPC endpoints. Mainnet uses the keyless publicnode endpoint — the
# api.mainnet-beta endpoint 403s on requests carrying an Origin header and is
# banned for this stack; this constant is what effective_rpc broadcasts against.
SOLANA_RPC_MAINNET = "https://solana-rpc.publicnode.com"
SOLANA_RPC_DEVNET  = "https://api.devnet.solana.com"

# ---------------------------------------------------------------------------
# Parad0x / dna-x402 on-chain program IDs (mainnet-beta)
# Multisig upgrade authority: 9M949AfyYCHp9hUk7crZZx3N6Y8sigyWBN6RM6tFq1q5
# Source: configs/mainnet.commercial.json
# ---------------------------------------------------------------------------

# Core receipt / ZK programs (2026-05-29 batch, under Squads multisig)
RECEIPT_ANCHOR_PROGRAM_MAINNET   = "6HSRGivdYR5D7yTDy1TFMCM8h3LzXxRtKU1RA3RnCMRN"
DARK_PROOF_GATE_LITE_MAINNET     = "PmSCTuehX1MYxf8GNsGsUZySYTtqWAtuTt3N2xZLpw2"
DARK_BN254_GATE_MAINNET          = "GCptvBYF8S6eVYoh15B7WAESc54FUHCpN1Ui6aHeQYZd"
DARK_SEMAPHORE_MAINNET           = "Ev7HEFhhKTXk6kS2Y6ssbUcK9C7E6yZ589jJNjUrQV5p"
DARK_SECP256R1_VAULT_MAINNET     = "3hbbtjeSrTVYXq6eRwjeofDe2DCPh3n8cfN6kZcQfewi"
DARK_SECP256K1_AUTH_MAINNET      = "AqwBbV13AoczhoELwP8oxT3nDqB6MsLWXauNzHkssZ9B"
NULL_TOKEN_HOOK_MAINNET          = "14ivonrNRmaMbJMQkGdHVVTcqZYhNvchULWxveazhW2g"
NULL_LOTTERY_MAINNET             = "3t5c2Trk4SFK7hvKVjsmmC2xQtasFnK9pJQRdwPHqxbG"
NULL_MINT_GATE_MAINNET           = "5jduvBZggszFeE7uxxNrvZAp8pJxzqtgzBGqg12fKhC1"

# NULL ecosystem
NULL_REGISTRAR_MAINNET           = "NXgQhepFpDCu935H1D4g34g59ZYbo1jR4tBCZWhV8Np"
DNA_X402_MAIN_MAINNET            = "6HSRGivdYR5D7yTDy1TFMCM8h3LzXxRtKU1RA3RnCMRN"

# $NULL token mint (Token-2022)
NULL_TOKEN_MINT_MAINNET          = "8EeDdvCRmFAzVD4takkBrNNwkeUTUQh4MscRK5Fzpump"

# Squads multisig that controls the 2026-05-29 batch
PARAD0X_UPGRADE_AUTHORITY        = "9M949AfyYCHp9hUk7crZZx3N6Y8sigyWBN6RM6tFq1q5"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class X402Mode(str, Enum):
    STUB    = "stub"    # no real Solana calls; deterministic fake receipt
    DEVNET  = "devnet"  # real devnet USDC payment
    MAINNET = "mainnet" # real mainnet USDC payment


@dataclass
class X402Config:
    """
    Configuration for the x402 payment client.

    Parameters
    ----------
    mode : X402Mode
        STUB (default) | DEVNET | MAINNET
    keypair_path : str | None
        Path to a Solana JSON keypair file (required for DEVNET / MAINNET).
    facilitator_url : str | None
        Override the default PayAI facilitator URL.
    rpc_url : str | None
        Override the default Solana RPC URL.
    max_fee_usdc : float
        Refuse payments above this amount (safety guard). Default 1.0 USDC.
    """
    mode: X402Mode = X402Mode.STUB
    keypair_path: Optional[str] = None
    facilitator_url: Optional[str] = None
    rpc_url: Optional[str] = None
    max_fee_usdc: float = 1.0

    @property
    def effective_rpc(self) -> str:
        if self.rpc_url:
            return self.rpc_url
        return SOLANA_RPC_DEVNET if self.mode == X402Mode.DEVNET else SOLANA_RPC_MAINNET

    @property
    def effective_facilitator(self) -> str:
        if self.facilitator_url:
            return self.facilitator_url
        return PAYAI_FACILITATOR_DEVNET if self.mode == X402Mode.DEVNET else PAYAI_FACILITATOR_MAINNET

    @property
    def effective_usdc_mint(self) -> str:
        return USDC_MINT_DEVNET if self.mode == X402Mode.DEVNET else USDC_MINT_MAINNET


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class X402Quote:
    """Payment details returned by the x402 endpoint / derived from listing."""
    amount_usdc: float
    recipient_wallet: str       # node operator's Solana wallet (base58)
    facilitator_url: str
    usdc_mint: str
    quote_hash: str             # sha256 of canonical quote fields
    expires_at: float           # unix timestamp


@dataclass
class X402Receipt:
    """
    Signed proof that a payment was made.

    In stub mode the `payment_tx` and `facilitator_sig` are placeholders.
    In devnet/mainnet mode they are real Solana tx signatures and ECDSA sigs
    from the facilitator.
    """
    session_id: str
    payment_tx: str             # Solana tx signature (or "stub-{uuid}")
    amount_usdc: float
    recipient_wallet: str
    facilitator_sig: str        # facilitator's signature over the receipt
    timestamp: float
    mode: str                   # "stub" | "devnet" | "mainnet"
    receipt_hash: str = field(init=False)

    def __post_init__(self) -> None:
        self.receipt_hash = self._compute_hash()

    def _compute_hash(self) -> str:
        """SHA-256 over canonical receipt fields (deterministic, order-fixed)."""
        canonical = json.dumps({
            "session_id":       self.session_id,
            "payment_tx":       self.payment_tx,
            "amount_usdc":      round(self.amount_usdc, 8),
            "recipient_wallet": self.recipient_wallet,
            "timestamp":        round(self.timestamp, 3),
            "mode":             self.mode,
        }, sort_keys=True)
        return hashlib.sha256(canonical.encode()).hexdigest()

    def to_dict(self) -> dict:
        return {
            "session_id":       self.session_id,
            "payment_tx":       self.payment_tx,
            "amount_usdc":      self.amount_usdc,
            "recipient_wallet": self.recipient_wallet,
            "facilitator_sig":  self.facilitator_sig,
            "timestamp":        self.timestamp,
            "mode":             self.mode,
            "receipt_hash":     self.receipt_hash,
        }


# ---------------------------------------------------------------------------
# X402Client
# ---------------------------------------------------------------------------

class X402Client:
    """
    Minimal x402 payment client.

    The public API is a single method: `pay()`. Internally it dispatches
    to either the stub path or the live Solana path depending on config.mode.
    """

    def __init__(self, config: Optional[X402Config] = None) -> None:
        self.config: X402Config = config or X402Config(mode=X402Mode.STUB)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def pay(
        self,
        amount_usdc: float,
        recipient_wallet: str,
        session_id: Optional[str] = None,
    ) -> X402Receipt:
        """
        Execute an x402 USDC payment.

        Parameters
        ----------
        amount_usdc : float
            Amount to pay in USDC (e.g. 0.001 for 1 milli-USDC).
        recipient_wallet : str
            Solana wallet address (base58) of the node operator being paid.
        session_id : str | None
            Rental session ID to bind to this receipt. Auto-generated if None.

        Returns
        -------
        X402Receipt
            A receipt with a canonical receipt_hash ready for WorkProof.

        Raises
        ------
        ValueError
            If amount_usdc exceeds config.max_fee_usdc (safety guard).
        X402PaymentError
            If the live payment fails (devnet/mainnet modes only).
        """
        if amount_usdc <= 0:
            raise ValueError(f"amount_usdc must be > 0, got {amount_usdc}")
        if amount_usdc > self.config.max_fee_usdc:
            raise ValueError(
                f"amount_usdc={amount_usdc:.6f} exceeds max_fee_usdc="
                f"{self.config.max_fee_usdc:.6f} safety limit"
            )

        sid = session_id or f"sess-{uuid.uuid4().hex[:12]}"

        if self.config.mode == X402Mode.STUB:
            return self._stub_pay(amount_usdc, recipient_wallet, sid)
        else:
            return self._live_pay(amount_usdc, recipient_wallet, sid)

    def quote(
        self,
        amount_usdc: float,
        recipient_wallet: str,
    ) -> X402Quote:
        """Build a payment quote (no Solana call in stub mode)."""
        canonical = json.dumps({
            "amount_usdc":      round(amount_usdc, 8),
            "recipient_wallet": recipient_wallet,
            "facilitator":      self.config.effective_facilitator,
            "mint":             self.config.effective_usdc_mint,
        }, sort_keys=True)
        quote_hash = hashlib.sha256(canonical.encode()).hexdigest()

        return X402Quote(
            amount_usdc=amount_usdc,
            recipient_wallet=recipient_wallet,
            facilitator_url=self.config.effective_facilitator,
            usdc_mint=self.config.effective_usdc_mint,
            quote_hash=quote_hash,
            expires_at=time.time() + 300,  # 5-minute quote TTL
        )

    # ------------------------------------------------------------------
    # Stub path — no Solana calls
    # ------------------------------------------------------------------

    def _stub_pay(
        self,
        amount_usdc: float,
        recipient_wallet: str,
        session_id: str,
    ) -> X402Receipt:
        """Return a deterministic fake receipt. Used in STUB mode."""
        fake_tx = f"stub-tx-{uuid.uuid4().hex}"
        return X402Receipt(
            session_id=session_id,
            payment_tx=fake_tx,
            amount_usdc=amount_usdc,
            recipient_wallet=recipient_wallet,
            facilitator_sig=f"stub-fac-sig-{uuid.uuid4().hex[:16]}",
            timestamp=time.time(),
            mode="stub",
        )

    # ------------------------------------------------------------------
    # Live path — real Solana USDC transfer
    # ------------------------------------------------------------------

    def _live_pay(
        self,
        amount_usdc: float,
        recipient_wallet: str,
        session_id: str,
    ) -> X402Receipt:
        """
        Execute a real USDC transfer on Solana and obtain a facilitator receipt.

        Flow
        ----
        1. Load keypair from config.keypair_path.
        2. Derive client USDC ATA.
        3. POST {facilitator}/quote to get the facilitator's escrow ATA.
        4. Build + sign + submit SPL token transfer tx.
        5. POST {facilitator}/receipt with tx sig → get signed receipt.
        """
        try:
            return self._solana_pay(amount_usdc, recipient_wallet, session_id)
        except Exception as exc:
            raise X402PaymentError(
                f"x402 live payment failed: {exc}"
            ) from exc

    def _load_payer_keypair(self):
        """Load the payer's solders Keypair from the configured JSON keypair.

        A Solana CLI JSON keypair is the 64-byte secret = 32-byte Ed25519 seed
        || 32-byte pubkey. solders' ``Keypair.from_bytes`` expects all 64 bytes;
        the first 32 are the seed. We build from the seed (the source of truth)
        via ``from_seed`` so solders derives the matching pubkey. Slicing to
        ``[:32]`` and feeding it to ``from_bytes`` raises "expected a sequence of
        length 64" and breaks signing on the live (devnet/mainnet) path.

        Returns the solders ``Keypair``. Imports only solders so the keypair
        path can be exercised without the heavier ``solana`` / ``spl`` packages.
        """
        import json as _json

        if not self.config.keypair_path:
            raise X402PaymentError(
                "keypair_path is required for DEVNET/MAINNET mode"
            )

        from solders.keypair import Keypair as SoldersKeypair  # type: ignore

        with open(self.config.keypair_path) as f:
            kp_data = _json.load(f)
        return SoldersKeypair.from_seed(bytes(kp_data[:32]))

    def _solana_pay(
        self,
        amount_usdc: float,
        recipient_wallet: str,
        session_id: str,
    ) -> X402Receipt:
        """Inner Solana payment implementation."""
        # ── 1. Load keypair (validates keypair_path before heavier imports) ──
        payer = self._load_payer_keypair()
        payer_pubkey = payer.pubkey()

        import requests as _req
        from solana.rpc.api import Client as SolanaClient  # type: ignore
        from solana.transaction import Transaction  # type: ignore
        from solders.pubkey import Pubkey  # type: ignore
        from spl.token.instructions import (  # type: ignore
            TransferParams,
            get_associated_token_address,
        )
        from spl.token.instructions import (  # type: ignore
            transfer as spl_transfer,
        )

        # ── 2. Get facilitator quote (escrow ATA + fee breakdown) ────────
        resp = _req.post(
            f"{self.config.effective_facilitator}/quote",
            json={
                "amount_usdc":      round(amount_usdc, 6),
                "recipient_wallet": recipient_wallet,
                "payer_wallet":     str(payer_pubkey),
                "session_id":       session_id,
                "mint":             self.config.effective_usdc_mint,
            },
            timeout=10,
        )
        if resp.status_code not in (200, 201):
            raise X402PaymentError(
                f"Facilitator quote failed: {resp.status_code} {resp.text[:200]}"
            )
        quote_data = resp.json()
        escrow_ata: str = quote_data["escrow_ata"]
        lamports_atomic: int = usdc_to_atomic(amount_usdc)

        # ── 3. Build SPL token transfer ──────────────────────────────────
        usdc_mint_pk   = Pubkey.from_string(self.config.effective_usdc_mint)
        payer_ata      = get_associated_token_address(payer_pubkey, usdc_mint_pk)
        escrow_ata_pk  = Pubkey.from_string(escrow_ata)

        from spl.token.constants import TOKEN_PROGRAM_ID  # type: ignore
        transfer_ix = spl_transfer(
            TransferParams(
                program_id=TOKEN_PROGRAM_ID,
                source=payer_ata,
                dest=escrow_ata_pk,
                owner=payer_pubkey,
                amount=lamports_atomic,
                signers=[],
            )
        )

        solana_client = SolanaClient(self.config.effective_rpc)
        blockhash_resp = solana_client.get_latest_blockhash()
        recent_blockhash = blockhash_resp.value.blockhash

        tx = Transaction(recent_blockhash=recent_blockhash)
        tx.add(transfer_ix)
        tx.sign(payer)

        # ── 4. Submit to Solana ──────────────────────────────────────────
        tx_resp = solana_client.send_raw_transaction(bytes(tx.serialize()))
        tx_sig = str(tx_resp.value)

        # ── 5. Get facilitator receipt ───────────────────────────────────
        receipt_resp = _req.post(
            f"{self.config.effective_facilitator}/receipt",
            json={
                "tx_signature": tx_sig,
                "session_id":   session_id,
                "amount_usdc":  round(amount_usdc, 6),
                "recipient":    recipient_wallet,
            },
            timeout=15,
        )
        if receipt_resp.status_code not in (200, 201):
            raise X402PaymentError(
                f"Facilitator receipt failed: {receipt_resp.status_code} "
                f"{receipt_resp.text[:200]}"
            )
        receipt_data = receipt_resp.json()

        return X402Receipt(
            session_id=session_id,
            payment_tx=tx_sig,
            amount_usdc=amount_usdc,
            recipient_wallet=recipient_wallet,
            facilitator_sig=receipt_data.get("facilitator_sig", ""),
            timestamp=time.time(),
            mode=self.config.mode.value,
        )


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class X402PaymentError(RuntimeError):
    """Raised when a live x402 payment fails."""
