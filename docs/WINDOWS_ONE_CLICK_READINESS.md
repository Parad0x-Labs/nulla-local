# Windows One-Click Readiness

This is the current readiness contract for a Windows-first NULLA/OpenClaw fork.

## Working local path

- `Install_And_Run_NULLA.bat` forwards install-profile arguments into the Windows installer.
- `installer/install_nulla.bat` detects hardware, selects the local install profile, ranks mounted drives for `OLLAMA_MODELS`, pulls the recommended local model bundle, and registers OpenClaw when enabled.
- `installer/provider_probe.py` reports CPU, RAM, all detected GPU candidates, selected accelerator, installed/missing Ollama models, drive headroom, exact pull commands, and the recommended model store path.
- `python installer\provider_probe.py --benchmark --benchmark-timeout 240` performs an opt-in live local generation check for the selected Ollama model.
- Legacy NVIDIA CUDA devices on Windows are visible in the scan but do not count as usable VRAM unless `NULLA_ALLOW_LEGACY_CUDA=1` is explicitly set after a successful warmup.
- The OpenClaw registration path writes local-only provider and memory settings and avoids hosted web-search defaults.

## Still not consumer-grade

- There is no signed `.exe` or MSIX package. The current one-click path is batch/PowerShell, not a polished Windows installer.
- The installer is CLI-first. It does not yet provide a beginner-safe GUI for choosing the recommended drive when multiple drives are available.
- The local model live check proves routing and generation, but it is not strict response-control validation. A model can still return extra text around a requested marker.
- Multi-GPU inventory is detection and recommendation logic. It does not yet implement explicit per-model GPU placement, parallel scheduling, or mixed-GPU load balancing.
- The optional benchmark is warmup-oriented and includes model-load time. It is useful for first-run validation, not stable throughput ranking.
- GitHub push depends on valid user authentication. Local commits can be prepared without it, but updating the remote fork requires a working token/session.

## Windows PR bar

Before proposing the Windows fork PR, run the full cumulative checks:

```bat
python -m pytest tests\test_hardware_tier.py tests\test_model_store_planner.py tests\test_provider_probe.py tests\test_install_script_contract.py tests\test_install_surface_contracts.py tests\test_provider_probe_contract.py
python installer\provider_probe.py --json
python installer\provider_probe.py --benchmark --benchmark-timeout 240
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
