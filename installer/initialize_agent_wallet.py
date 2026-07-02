"""Create (or load) the agent's local Solana wallet during install.

Run once by install_nulla.bat AFTER the runtime identity is seeded (the wallet
encryption key is derived from the node signing key, so identity must exist
first). Prints ONLY the wallet's public key to stdout - never the private seed,
which stays encrypted at rest with AES-256-GCM under a locally-derived key.

Usage:
    python -m installer.initialize_agent_wallet <runtime_home>

Exit codes:
    0  wallet ready; public key printed to stdout (single line)
    1  wallet could not be created/loaded (details on stderr)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def initialize_agent_wallet(runtime_home: str) -> str:
    """Create-or-load the wallet and return its public key (base58). Never the seed."""
    from core.nulla_wallet import get_or_create_wallet

    wallet = get_or_create_wallet(runtime_home=runtime_home or None)
    return wallet.pubkey


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    runtime_home = args[0] if args else ""
    try:
        pubkey = initialize_agent_wallet(runtime_home)
    except Exception as exc:
        print(f"ERROR: agent wallet initialization failed: {exc}", file=sys.stderr)
        return 1
    # stdout carries ONLY the public key so the caller can capture it for the receipt.
    print(pubkey)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
