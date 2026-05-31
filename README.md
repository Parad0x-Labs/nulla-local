# nulla-local

Local-first agent runtime. Runs on your machine. Keeps memory. Uses tools. Optionally delegates work to trusted helpers when a task needs more reach.

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Status: Alpha](https://img.shields.io/badge/Status-Alpha-orange.svg)](docs/STATUS.md)
[![Python: 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://python.org)

`main` is the real alpha trunk.

**Current state:**
- **Working**: local runtime, memory, tools, bounded research, local operator execution with append-only task/proof events, Hive task flow, public proof/work surfaces.
- **Maturing**: helper coordination, deployment ergonomics, multi-node repeatability.
- **Not yet**: trustless economics, public marketplace layers, internet-scale mesh.
- Credits are local work/participation accounting for Hive scheduling priority — not blockchain tokens or trustless settlement.

### How this fits the Parad0x stack

Parad0x Labs builds Web0 on Solana — money and agents that settle themselves. **You are here: Local AI.**

| Layer | Repo | Does |
|---|---|---|
| Payments | [dna-x402](https://github.com/Parad0x-Labs/dna-x402) | x402 rail: quote → pay → verify → receipt → anchor |
| Build | [dna-x402-builders](https://github.com/Parad0x-Labs/dna-x402-builders) | Hosted kit: turn any API/bot into a paid agent |
| Privacy | [Dark-Null-Protocol](https://github.com/Parad0x-Labs/Dark-Null-Protocol) | Groth16 privacy settlement, published proofs |
| Data | [liquefy](https://github.com/Parad0x-Labs/liquefy) | Columnar compression that beats Zstd + audit trails |
| Media | [nebula-media](https://github.com/Parad0x-Labs/nebula-media) | Perceptual video re-encoding, VMAF quality proofs |
| Local AI | [nulla-local](https://github.com/Parad0x-Labs/nulla-local) (this repo) | Local-first agent runtime — your machine, your memory |

**See it live**: parad0xlabs.com

## Install

**macOS / Linux:**
```bash
curl -fsSLo bootstrap_nulla.sh https://raw.githubusercontent.com/Parad0x-Labs/nulla-local/main/installer/bootstrap_nulla.sh
bash bootstrap_nulla.sh
```

**Windows PowerShell:**
```powershell
Invoke-WebRequest https://raw.githubusercontent.com/Parad0x-Labs/nulla-local/main/installer/bootstrap_nulla.ps1 -OutFile bootstrap_nulla.ps1
powershell -ExecutionPolicy Bypass -File .\bootstrap_nulla.ps1
```

MIT License — © 2026 Parad0x Labs
