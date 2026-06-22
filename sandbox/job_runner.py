from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

# macOS Seatbelt profile: allow everything the job needs locally, deny all
# network (inbound, outbound, bind, system sockets). Gives the same "no network
# egress" guarantee as Linux `unshare -n` / `bwrap --unshare-net`, enforced by
# the kernel rather than by the static command heuristic.
_MACOS_DENY_NETWORK_PROFILE = "(version 1)(allow default)(deny network*)"

from sandbox.container_adapter import ExecutionResult
from sandbox.network_guard import command_uses_network
from sandbox.resource_limits import ExecutionPolicy, normalize_policy, path_within_roots


def _truncate(text: str, limit_kb: int) -> str:
    raw = text or ""
    encoded = raw.encode("utf-8")
    if len(encoded) <= limit_kb * 1024:
        return raw
    return encoded[-(limit_kb * 1024) :].decode("utf-8", errors="replace")


def _decode_partial(captured: object) -> str:
    # subprocess.TimeoutExpired.stdout/stderr may be None, str (text=True), or
    # bytes depending on how far the read got; normalize to a string.
    if captured is None:
        return ""
    if isinstance(captured, bytes):
        return captured.decode("utf-8", errors="replace")
    return str(captured)


class JobRunner:
    def __init__(self, policy: ExecutionPolicy):
        self.policy = normalize_policy(policy)

    def run(self, argv: list[str], *, cwd: str | Path | None = None) -> ExecutionResult:
        if not argv:
            raise ValueError("No command provided.")
        if cwd is None:
            cwd = self.policy.workspace_root
        cwd_path = Path(cwd).resolve()
        allowed_roots = (self.policy.workspace_root, *tuple(self.policy.writable_roots))
        if not path_within_roots(cwd_path, allowed_roots):
            raise ValueError("Execution cwd escapes allowed workspace roots.")
        if command_uses_network(argv) and not self.policy.allow_network_egress:
            raise ValueError("Network egress is disabled by execution policy.")
        argv = self._with_network_isolation(argv)

        env = os.environ.copy()
        env["NULLA_EXECUTION_BACKEND"] = self.policy.backend
        env["NO_PROXY"] = "*"
        env["no_proxy"] = "*"

        try:
            completed = subprocess.run(
                argv,
                cwd=str(cwd_path),
                capture_output=True,
                text=True,
                timeout=self.policy.max_seconds,
                shell=False,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            # The job exceeded its wall-clock budget. subprocess has already sent
            # SIGKILL and reaped the child; surface a graceful non-zero result
            # (with any captured partial output) instead of propagating, so a
            # slow/hung job is a normal failed run rather than a caller crash.
            partial_stdout = _decode_partial(exc.stdout)
            partial_stderr = _decode_partial(exc.stderr)
            timeout_note = f"Execution timed out after {self.policy.max_seconds}s and was terminated."
            combined_stderr = f"{partial_stderr}\n{timeout_note}".strip() if partial_stderr else timeout_note
            return ExecutionResult(
                returncode=124,
                stdout=_truncate(partial_stdout, self.policy.max_output_kb),
                stderr=_truncate(combined_stderr, self.policy.max_output_kb),
            )
        return ExecutionResult(
            returncode=int(completed.returncode),
            stdout=_truncate(completed.stdout, self.policy.max_output_kb),
            stderr=_truncate(completed.stderr, self.policy.max_output_kb),
        )

    def _with_network_isolation(self, argv: list[str]) -> list[str]:
        if self.policy.allow_network_egress:
            return argv
        mode = (self.policy.network_isolation_mode or "auto").strip().lower()
        if mode not in {"auto", "os_enforced", "heuristic_only"}:
            mode = "auto"
        if mode == "heuristic_only":
            return argv
        isolated = self._kernel_network_isolation_prefix(argv)
        if isolated is not None:
            return isolated
        # No OS-enforced backend available on this host. Fail closed by default
        # (both 'auto' and 'os_enforced'): macOS has sandbox-exec and Linux has
        # bwrap/unshare/firejail, so reaching here means no kernel enforcement is
        # possible. 'heuristic_only' remains the explicit, informed opt-in to the
        # weaker static-guard guarantee (e.g. on Windows).
        if mode in {"auto", "os_enforced"}:
            raise ValueError(
                "OS-level network isolation is required but unavailable "
                "(expected one of: bwrap, unshare, firejail on Linux; sandbox-exec on macOS). "
                "Set network_isolation_mode='heuristic_only' only for an explicit unsafe local override."
            )
        return argv

    def _kernel_network_isolation_prefix(self, argv: list[str]) -> list[str] | None:
        # Prefer hardened Linux isolation backends when present.
        isolated = self._linux_bwrap_prefix(argv)
        if isolated is not None:
            return isolated
        isolated = self._linux_unshare_prefix(argv)
        if isolated is not None:
            return isolated
        isolated = self._linux_firejail_prefix(argv)
        if isolated is not None:
            return isolated
        # macOS: real kernel-enforced network denial via Seatbelt (sandbox-exec).
        return self._macos_sandbox_exec_prefix(argv)

    def _macos_sandbox_exec_prefix(self, argv: list[str]) -> list[str] | None:
        if sys.platform != "darwin":
            return None
        sandbox_exec = shutil.which("sandbox-exec")
        if not sandbox_exec:
            return None
        return [sandbox_exec, "-p", _MACOS_DENY_NETWORK_PROFILE, "--", *list(argv)]

    def _linux_bwrap_prefix(self, argv: list[str]) -> list[str] | None:
        if os.name != "posix":
            return None
        if not sys.platform.startswith("linux"):
            return None
        bwrap = shutil.which("bwrap")
        if not bwrap:
            return None
        return [bwrap, "--unshare-net", "--", *list(argv)]

    def _linux_unshare_prefix(self, argv: list[str]) -> list[str] | None:
        if os.name != "posix":
            return None
        if not sys.platform.startswith("linux"):
            return None
        unshare = shutil.which("unshare")
        if not unshare:
            return None
        return [unshare, "-n", "--", *list(argv)]

    def _linux_firejail_prefix(self, argv: list[str]) -> list[str] | None:
        if os.name != "posix":
            return None
        if not sys.platform.startswith("linux"):
            return None
        firejail = shutil.which("firejail")
        if not firejail:
            return None
        return [firejail, "--net=none", "--quiet", "--", *list(argv)]
