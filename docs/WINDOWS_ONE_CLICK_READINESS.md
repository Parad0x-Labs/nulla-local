# Windows One-Click Readiness

This is the current readiness contract for a Windows-first NULLA/OpenClaw fork.

## Working local path

- `Install_And_Run_NULLA.ps1` provides a guided Windows front-end with profile selection, runtime-home selection, probe action, log path, and headless `-AutoYes` mode.
- `Install_And_Run_NULLA.bat` remains the plain double-click fallback and forwards install-profile arguments into the Windows installer.
- `installer/build_windows_package.ps1` creates a checksumed Windows zip package and optionally Authenticode-signs PowerShell scripts when `NULLA_WINDOWS_SIGNING_CERT_THUMBPRINT` or `-SigningCertificateThumbprint` is supplied.
- `installer/install_nulla.bat` detects hardware, selects the local install profile, ranks mounted drives for `OLLAMA_MODELS`, pulls the recommended local model bundle, and registers OpenClaw when enabled.
- `installer/provider_probe.py` reports CPU, RAM, all detected GPU candidates, selected accelerator, installed/missing Ollama models, drive headroom, exact pull commands, and the recommended model store path.
- `python installer\provider_probe.py --benchmark --benchmark-timeout 240` performs an opt-in live local generation check for the selected Ollama model.
- Legacy NVIDIA CUDA devices on Windows are visible in the scan but do not count as usable VRAM unless `NULLA_ALLOW_LEGACY_CUDA=1` is explicitly set after a successful warmup.
- The OpenClaw registration path writes local-only provider and memory settings and avoids hosted web-search defaults.

## Still not consumer-grade

- There is no signed `.exe` or MSIX package. The current one-click path is still PowerShell/batch, not a signed native Windows package.
- The installer has a guided PowerShell UI, but not a full native desktop installer with progress telemetry, rollback, or Windows app identity.
- The guided installer runs the live local model check by default after install; use `-SkipBenchmark` only for intentionally offline or slow validation runs.
- Exact-marker API responses are clamped at the compatibility boundary, but broader instruction-following quality still depends on the selected local model.
- Multi-GPU inventory is detection and recommendation logic. It does not yet implement explicit per-model GPU placement, parallel scheduling, or mixed-GPU load balancing.
- The optional benchmark is warmup-oriented and includes model-load time. It is useful for first-run validation, not stable throughput ranking.

## Windows PR bar

Before proposing the Windows fork PR, run the full cumulative gauntlet on a
fresh Windows host or VM:

```bat
Test_NULLA_Windows_Gauntlet.cmd -InstallProfile auto-recommended
```

For CI or a machine that already has the repo installed, use the non-installing
mode:

```bat
Test_NULLA_Windows_Gauntlet.cmd -SkipInstall -SkipBenchmark -Json
```

The gauntlet writes JSON reports under `dist\windows-gauntlet\` and covers the
installer, focused Windows regression tests, provider probe, optional live local
model benchmark, Windows package build, and optional OpenClaw live checks.

To require the OpenClaw live checks on a host that should already have the CLI
configured:

```bat
Test_NULLA_Windows_Gauntlet.cmd -RequireOpenClaw
```

The lower-level checks remain useful for diagnosis:

```bat
python -m pytest tests\test_hardware_tier.py tests\test_model_store_planner.py tests\test_provider_probe.py tests\test_install_script_contract.py tests\test_install_surface_contracts.py tests\test_provider_probe_contract.py
python installer\provider_probe.py --json
python installer\provider_probe.py --benchmark --benchmark-timeout 240
powershell -NoProfile -ExecutionPolicy Bypass -File installer\build_windows_package.ps1 -PackageVersion local-test
```

For a real host acceptance run, also verify:

```bat
openclaw config validate
openclaw doctor --non-interactive --no-workspace-suggestions
openclaw gateway health
openclaw agents list
openclaw memory status --deep
openclaw agent --agent nulla --message "Reply exactly OPENCLAW_NULLA_OK" --json --timeout 240
```

## Stack fork handoff

Before copying the Windows branches for fork pushes, run the stack handoff gate
from this checkout:

```bat
Test_NULLA_Windows_Stack.cmd
```

That fast profile validates the Windows entrypoints across the sibling stack
repos and writes JSON plus command logs under `dist\windows-stack-handoff\`.

For a heavier release pass that reruns the installer/test path for each supported
repo:

```bat
Test_NULLA_Windows_Stack.cmd -Profile release
```
