"""
core/x402/client.py
===================
Minimal x402 payment client for the NULLA compute rental mesh.

Implements the HTTP 402 payment-required flow for agent-to-agent USDC
settlements on Solana using the canonical x402 "exact" scheme against the
PayAI facilitator (https://facilitator.payai.network).

Modes
-----
stub    — default; deterministic fake receipt, no Solana calls. Existing
          tests pass unmodified. Safe for CI and offline development.
devnet  — real SPL-token transfer on Solana devnet, settled by the PayAI
          facilitator (network "solana-devnet"); requires a funded devnet
          wallet at config.keypair_path.
mainnet — production; same flow on network "solana" against the same facilitator.

Protocol (canonical x402 "exact" on Solana)
-------------------------------------------
1. Payment requirements (scheme/network/maxAmountRequired/payTo/asset/feePayer)
   come from the resource server's HTTP 402 (or are built directly here).
2. The client builds a v0 transaction — ComputeBudget limit+price, then an SPL
   TransferChecked of `asset` from the payer's ATA to payTo's ATA — with the
   facilitator's sponsored `feePayer` as the fee payer, and PARTIALLY signs it
   (the payer slot only; the facilitator fills the feePayer signature at settle).
3. The base64 transaction is wrapped as the x402 payment payload and POSTed to
   the facilitator /verify, then /settle.
4. /settle returns the on-chain Solana transaction signature → X402Receipt.
5. receipt.receipt_hash is included in WorkProof.signature for anchoring.

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


# PayAI facilitator — canonical x402 facilitator. ONE host for every network
# (the network is a field in the payment, not a subdomain). The old
# devnet.facilitator.payai.network subdomain does NOT resolve.
PAYAI_FACILITATOR = "https://facilitator.payai.network"

# Sponsored Solana fee payer the facilitator advertises at GET /supported. It is
# fetched live at runtime; this is only the fallback if that fetch fails.
PAYAI_SOLANA_FEEPAYER = "2wKupLR9q6wXYppw8Gr2NvWxKBUqm4PPJKkQfoxHDBg4"

# Canonical Solana program ids (universal — safe as literals, not "our" ids).
TOKEN_PROGRAM_ID            = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
ASSOCIATED_TOKEN_PROGRAM_ID = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"

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
    asset_mint: Optional[str] = None       # override the asset (default: cluster USDC)
    asset_decimals: int = USDC_DECIMALS    # decimals of the asset being transferred
    max_fee_usdc: float = 1.0

    @property
    def effective_rpc(self) -> str:
        if self.rpc_url:
            return self.rpc_url
        return SOLANA_RPC_DEVNET if self.mode == X402Mode.DEVNET else SOLANA_RPC_MAINNET

    @property
    def effective_facilitator(self) -> str:
        # Canonical x402 uses one facilitator host for every network.
        return self.facilitator_url or PAYAI_FACILITATOR

    @property
    def network_name(self) -> str:
        """x402 network id for this mode ("solana-devnet" / "solana")."""
        return "solana-devnet" if self.mode == X402Mode.DEVNET else "solana"

    @property
    def effective_usdc_mint(self) -> str:
        return USDC_MINT_DEVNET if self.mode == X402Mode.DEVNET else USDC_MINT_MAINNET

    @property
    def effective_asset(self) -> str:
        """The SPL mint to transfer (asset_mint override, else cluster USDC)."""
        return self.asset_mint or self.effective_usdc_mint


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
        Settle a real Solana payment via the canonical x402 "exact" flow.

        Flow
        ----
        1. Load keypair from config.keypair_path.
        2. Build payment requirements (network / asset / payTo / sponsored feePayer).
        3. Build a partially-signed v0 TransferChecked tx → x402 payment payload.
        4. POST the payment to {facilitator}/verify, then /settle.
        5. /settle returns the on-chain tx signature → X402Receipt.
        """
        try:
            return self._solana_pay(amount_usdc, recipient_wallet, session_id)
        except X402PaymentError:
            raise
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

    def _facilitator_fee_payer(self) -> str:
        """The sponsored Solana fee payer for this network, from GET /supported.

        Cached per client. Falls back to the advertised constant if the fetch
        fails so a transient /supported hiccup never blocks a payment.
        """
        cached = getattr(self, "_fee_payer_cache", None)
        if cached:
            return cached
        fee_payer = PAYAI_SOLANA_FEEPAYER
        try:
            import requests as _req
            r = _req.get(
                f"{self.config.effective_facilitator}/supported",
                headers={"User-Agent": "nulla-x402/1.0"}, timeout=15,
            )
            if r.status_code == 200:
                for kind in (r.json().get("kinds") or []):
                    if (kind.get("scheme") == "exact"
                            and kind.get("network") == self.config.network_name):
                        fp = (kind.get("extra") or {}).get("feePayer")
                        if fp:
                            fee_payer = fp
                            break
        except Exception:
            pass
        self._fee_payer_cache = fee_payer
        return fee_payer

    def _build_payment_requirements(
        self, amount_usdc: float, recipient_wallet: str, session_id: str,
    ) -> dict:
        """The x402 paymentRequirements a resource server would issue in its 402."""
        return {
            "scheme":            "exact",
            "network":           self.config.network_name,
            "maxAmountRequired": str(usdc_to_atomic(amount_usdc)),  # atomic units
            "resource":          f"https://nulla.local/x402/{session_id}",
            "description":       f"nulla x402 settlement {session_id}",
            "mimeType":          "application/json",
            "payTo":             recipient_wallet,
            "maxTimeoutSeconds": 120,
            "asset":             self.config.effective_asset,
            "extra":             {"feePayer": self._facilitator_fee_payer()},
        }

    def _solana_pay(
        self,
        amount_usdc: float,
        recipient_wallet: str,
        session_id: str,
    ) -> X402Receipt:
        """Canonical x402 "exact" settle on Solana via the PayAI facilitator."""
        import requests as _req

        # ── 1. Load keypair (validates keypair_path before heavier imports) ──
        payer = self._load_payer_keypair()

        # ── 2. Build payment requirements + the partially-signed payment ─────
        requirements = self._build_payment_requirements(
            amount_usdc, recipient_wallet, session_id
        )
        payment = build_solana_x402_payment(
            payer, requirements, self.config.effective_rpc,
            decimals=self.config.asset_decimals,
        )
        body = {
            "x402Version":         1,
            "paymentPayload":      payment,
            "paymentRequirements": requirements,
        }
        headers = {"Content-Type": "application/json", "User-Agent": "nulla-x402/1.0"}
        fac = self.config.effective_facilitator

        # ── 3. /verify ───────────────────────────────────────────────────────
        vr = _req.post(f"{fac}/verify", json=body, headers=headers, timeout=20)
        if vr.status_code not in (200, 201):
            raise X402PaymentError(f"x402 /verify HTTP {vr.status_code}: {vr.text[:200]}")
        vd = vr.json()
        if not vd.get("isValid"):
            raise X402PaymentError(f"x402 /verify rejected: {vd.get('invalidReason')}")

        # ── 4. /settle → on-chain signature ──────────────────────────────────
        sr = _req.post(f"{fac}/settle", json=body, headers=headers, timeout=45)
        if sr.status_code not in (200, 201):
            raise X402PaymentError(f"x402 /settle HTTP {sr.status_code}: {sr.text[:200]}")
        sd = sr.json()
        tx_sig = sd.get("transaction")
        if not sd.get("success") or not tx_sig:
            raise X402PaymentError(f"x402 /settle failed: {sd.get('errorReason') or sd}")

        # Canonical x402 /settle returns no receipt signature — the on-chain
        # tx (payment_tx) IS the proof. Leave facilitator_sig empty rather than
        # mislabel the verified-payer field as a signature.
        return X402Receipt(
            session_id=session_id,
            payment_tx=tx_sig,                      # real on-chain Solana signature
            amount_usdc=amount_usdc,
            recipient_wallet=recipient_wallet,
            facilitator_sig="",
            timestamp=time.time(),
            mode=self.config.mode.value,
        )


# ---------------------------------------------------------------------------
# Canonical x402 "exact" Solana transaction builder
# ---------------------------------------------------------------------------

def _get_latest_blockhash(rpc_url: str) -> str:
    """Fetch a recent blockhash (base58) from a Solana RPC."""
    import requests as _req
    r = _req.post(
        rpc_url,
        json={"jsonrpc": "2.0", "id": 1, "method": "getLatestBlockhash",
              "params": [{"commitment": "finalized"}]},
        headers={"Content-Type": "application/json", "User-Agent": "nulla-x402/1.0"},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["result"]["value"]["blockhash"]


def build_solana_x402_payment(
    payer, payment_requirements: dict, rpc_url: str, *, decimals: int = USDC_DECIMALS,
    compute_unit_limit: int = 50_000, compute_unit_price: int = 1,
) -> dict:
    """Build the canonical x402 "exact" Solana payment payload.

    ``payer`` is a solders ``Keypair`` (the token sender). Constructs a v0
    transaction — ComputeBudget limit+price then an SPL ``TransferChecked`` of
    ``asset`` from the payer's ATA to ``payTo``'s ATA — with the facilitator's
    ``extra.feePayer`` as the transaction fee payer, PARTIALLY signs it (only the
    payer slot; the facilitator fills the feePayer signature at settle), and
    returns the x402 envelope ``{x402Version, scheme, network, payload:{transaction}}``.

    The destination ATA must already exist (the facilitator settles, it does not
    create accounts); pre-create it for a recipient that may not have one.

    ``compute_unit_limit`` is capped by the facilitator (it rejects an over-high
    sponsored limit with ``..._compute_limit_too_high``); 50k is comfortably under
    the cap and well above a TransferChecked's real consumption. ``compute_unit_price``
    is the priority fee in micro-lamports/CU (the facilitator caps this low).
    """
    import base64 as _b64

    from solders.compute_budget import set_compute_unit_limit, set_compute_unit_price
    from solders.hash import Hash
    from solders.instruction import AccountMeta, Instruction
    from solders.message import MessageV0, to_bytes_versioned
    from solders.pubkey import Pubkey
    from solders.signature import Signature
    from solders.transaction import VersionedTransaction

    token_program = Pubkey.from_string(TOKEN_PROGRAM_ID)
    ata_program   = Pubkey.from_string(ASSOCIATED_TOKEN_PROGRAM_ID)
    mint      = Pubkey.from_string(payment_requirements["asset"])
    pay_to    = Pubkey.from_string(payment_requirements["payTo"])
    fee_payer = Pubkey.from_string(payment_requirements["extra"]["feePayer"])
    amount    = int(payment_requirements["maxAmountRequired"])

    def _ata(owner: Pubkey) -> Pubkey:
        addr, _ = Pubkey.find_program_address(
            [bytes(owner), bytes(token_program), bytes(mint)], ata_program
        )
        return addr

    src_ata = _ata(payer.pubkey())
    dst_ata = _ata(pay_to)

    # SPL TransferChecked: instruction index 12, amount u64 LE, decimals u8.
    data = bytes([12]) + amount.to_bytes(8, "little") + bytes([decimals])
    transfer_ix = Instruction(
        token_program, data,
        [AccountMeta(src_ata, False, True),
         AccountMeta(mint, False, False),
         AccountMeta(dst_ata, False, True),
         AccountMeta(payer.pubkey(), True, False)],
    )
    ixs = [
        set_compute_unit_limit(compute_unit_limit),
        set_compute_unit_price(compute_unit_price),
        transfer_ix,
    ]

    blockhash = Hash.from_string(_get_latest_blockhash(rpc_url))
    msg = MessageV0.try_compile(fee_payer, ixs, [], blockhash)

    # Partial sign: payer signs its slot; the feePayer slot stays empty (default)
    # for the facilitator to fill at settle.
    sigs = [Signature.default()] * msg.header.num_required_signatures
    sigs[list(msg.account_keys).index(payer.pubkey())] = payer.sign_message(
        to_bytes_versioned(msg)
    )
    tx = VersionedTransaction.populate(msg, sigs)

    return {
        "x402Version": 1,
        "scheme":      payment_requirements["scheme"],
        "network":     payment_requirements["network"],
        "payload":     {"transaction": _b64.b64encode(bytes(tx)).decode()},
    }


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class X402PaymentError(RuntimeError):
    """Raised when a live x402 payment fails."""
