from __future__ import annotations

import os
import subprocess
from pathlib import Path

from core.execution_gate import ExecutionGate
from core.liquefy_bridge import apply_local_execution_safety
from sandbox.job_runner import JobRunner
from sandbox.network_guard import parse_command
from sandbox.resource_limits import ExecutionPolicy


def _base_command_name(raw: str) -> str:
    name = Path(str(raw or "")).name.lower()
    if os.name == "nt" and name.endswith(".exe"):
        return name[:-4]
    return name


def _network_isolation_mode_from_env() -> str:
    raw = str(os.environ.get("NULLA_SANDBOX_NETWORK_MODE") or "").strip().lower()
    if raw in {"os_enforced", "heuristic_only"}:
        return raw
    unsafe = str(os.environ.get("NULLA_SANDBOX_UNSAFE_EXPLICIT") or "").strip().lower()
    if unsafe in {"1", "true", "yes", "on"}:
        return "heuristic_only"
    return "auto"


class SandboxRunner:
    """
    Acts as the single point of OS-level command execution.
    Requires an explicit allowance from the local ExecutionGate prior to launching any process.
    """

    ALLOWED_COMMANDS = [
        "dir", "ls", "pwd",
        "echo", "cat", "type", "head", "tail", "wc",
        "rg", "grep", "find", "sed",
        "npm", "npx", "yarn", "pnpm",
        "cargo", "python", "python3", "pytest",
        "ruff",
        "pip", "pip3",
        "git", "node", "nodejs", "env",
        "mkdir", "touch", "cp", "mv",
    ]

    _PACKAGE_INSTALL_COMMANDS = {"pip", "pip3", "npm", "npx", "yarn", "pnpm", "cargo"}

    def __init__(self, gate: ExecutionGate, workspace_path: str, *, network_isolation_mode: str | None = None):
        self.gate = gate
        self.workspace = workspace_path
        self._workspace_path = Path(workspace_path).resolve()
        self.job_runner = JobRunner(
            ExecutionPolicy(
                workspace_root=self._workspace_path,
                writable_roots=(self._workspace_path,),
                max_seconds=120,
                max_output_kb=256,
                allow_network_egress=False,
                network_isolation_mode=network_isolation_mode or _network_isolation_mode_from_env(),
            )
        )

    def run_command(self, cmd: str, *, cwd: str | None = None) -> dict:
        """
        Takes an arbitrary shell command, asks the Execution Gate, and runs it if allowed.
        """
        # Step 1: Pre-flight check with the ExecutionGate
        target_cwd = cwd or self.workspace
        if not apply_local_execution_safety(sandbox_context={"workspace": target_cwd}, payload={"cmd": cmd}):
            return {"error": "Liquefy safety guard blocked execution", "status": "blocked_by_policy"}

        gate_result = self.gate.evaluate_command(cmd)

        if gate_result["decision"] == "blocked":
            return {"error": gate_result["reason"], "status": "blocked_by_policy"}

        if gate_result["decision"] == "advice_only":
            return {"error": gate_result["reason"], "status": "user_action_required"}

        if gate_result["decision"] == "simulate_only":
            return {"error": gate_result["reason"], "status": "simulate_only"}

        # Optional Simulator check could go here if decision == "simulate"

        # Step 2: Validate whitelist
        allowed = False
        argv = parse_command(cmd)
        if not argv:
            return {"error": "Empty command.", "status": "blocked_by_policy"}
        base_cmd = _base_command_name(str(argv[0] or ""))
        if base_cmd == "env":
            index = 1
            while index < len(argv):
                token = str(argv[index] or "")
                if "=" in token and not token.startswith("-"):
                    index += 1
                    continue
                base_cmd = _base_command_name(str(argv[index] or "")) if index < len(argv) else "env"
                break
        if base_cmd in self.ALLOWED_COMMANDS:
            allowed = True

        if not allowed:
            return {
                "error": f"Base command '{base_cmd}' not in Sandbox whitelist.",
                "status": "blocked_by_policy",
                "allowed": self.ALLOWED_COMMANDS,
            }

        # Step 3: Proceed with execution
        try:
            runner = self.job_runner
            if base_cmd in self._PACKAGE_INSTALL_COMMANDS:
                runner = JobRunner(
                    ExecutionPolicy(
                        workspace_root=self._workspace_path,
                        writable_roots=(self._workspace_path,),
                        max_seconds=180,
                        max_output_kb=512,
                        allow_network_egress=True,
                    )
                )
            print(f"  [SANDBOX RUN] Executing: {cmd}")
            result = runner.run(argv, cwd=target_cwd)
            return {
                "cmd": cmd,
                "cwd": str(target_cwd),
                "returncode": result.returncode,
                "stdout": result.stdout[-4000:] if result.stdout else "",
                "stderr": result.stderr[-2000:] if result.stderr else "",
                "success": (result.returncode == 0),
                "status": "executed",
                "base_command": base_cmd,
            }
        except subprocess.TimeoutExpired:
            return {"error": f"Command timed out after 120s: {cmd}"}
        except ValueError as e:
            return {"error": f"Sandbox execution blocked: {e!s}", "status": "blocked_by_policy"}
        except Exception as e:
            return {"error": f"Sandbox execution failure: {e!s}"}
