from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from sandbox.container_adapter import ExecutionResult
from sandbox.network_guard import command_uses_network
from sandbox.resource_limits import (
    ExecutionPolicy,
    normalize_policy,
    path_args_within_roots,
    path_within_roots,
)


def _seatbelt_subpath_literal(path: Path) -> str:
    # Seatbelt string literals are double-quoted; escape embedded quotes/backslashes.
    escaped = str(path).replace("\\", "\\\\").replace('"', '\\"')
    return f'(subpath "{escaped}")'


def _macos_confined_profile(allowed_roots: tuple[Path, ...]) -> str:
    """Build a Seatbelt profile that denies all network egress AND confines
    file *writes* to the allowed workspace roots.

    Reads stay permitted so interpreters/tooling can load their stdlib and
    dylibs without bespoke allow-lists; the OS-independent path-argument guard
    in :meth:`JobRunner.run` is what blocks reads of absolute paths outside the
    workspace. Writes are denied by the kernel so a job cannot tamper with or
    persist outside its workspace even if the static guard is bypassed.
    """
    write_roots: list[Path] = []
    seen: set[str] = set()
    for root in allowed_roots:
        for variant in _path_variants(root):
            key = str(variant)
            if key not in seen:
                seen.add(key)
                write_roots.append(variant)
    allow_clauses = "".join(_seatbelt_subpath_literal(p) for p in write_roots)
    # /dev is needed for normal stdio (e.g. /dev/null, /dev/urandom).
    allow_clauses += '(subpath "/dev")'
    return (
        "(version 1)"
        "(allow default)"
        "(deny network*)"
        "(deny file-write*)"
        f"(allow file-write* {allow_clauses})"
    )


def _path_variants(path: Path) -> tuple[Path, ...]:
    # macOS aliases /tmp -> /private/tmp and /var -> /private/var. The policy
    # stores the resolved path; include the unresolved alias too so a job
    # launched with the /tmp form is still allowed by the kernel profile.
    resolved = path.resolve()
    variants = [resolved]
    text = str(resolved)
    if text.startswith("/private/tmp"):
        variants.append(Path(text.replace("/private/tmp", "/tmp", 1)))
    elif text.startswith("/private/var"):
        variants.append(Path(text.replace("/private/var", "/var", 1)))
    return tuple(variants)


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
        escaping = path_args_within_roots(argv, allowed_roots, cwd=cwd_path)
        if escaping is not None:
            raise ValueError(f"Path argument '{escaping}' escapes allowed workspace roots.")
        if command_uses_network(argv) and not self.policy.allow_network_egress:
            raise ValueError("Network egress is disabled by execution policy.")
        argv = self._with_network_isolation(argv, allowed_roots)

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

    def _with_network_isolation(
        self, argv: list[str], allowed_roots: tuple[Path, ...] = ()
    ) -> list[str]:
        if self.policy.allow_network_egress:
            return argv
        if not allowed_roots:
            allowed_roots = (self.policy.workspace_root, *tuple(self.policy.writable_roots))
        mode = (self.policy.network_isolation_mode or "auto").strip().lower()
        if mode not in {"auto", "os_enforced", "heuristic_only"}:
            mode = "auto"
        if mode == "heuristic_only":
            return argv
        isolated = self._kernel_network_isolation_prefix(argv, allowed_roots)
        if isolated is not None:
            return isolated
        # No OS-enforced backend available on this host. Fail closed by default
        # (both 'auto' and 'os_enforced'): macOS has sandbox-exec and Linux has
        # bwrap/unshare/firejail, so reaching here means no kernel enforcement is
        # possible. 'heuristic_only' remains the explicit, informed opt-in to the
        # weaker static-guard guarantee (e.g. on Windows).
        if mode in {"auto", "os_enforced"}:
            raise ValueError(self._no_kernel_isolation_message())
        return argv

    def _no_kernel_isolation_message(self) -> str:
        base = (
            "OS-level network isolation is required but unavailable "
            "(expected one of: bwrap, unshare, firejail on Linux; sandbox-exec on macOS)."
        )
        if os.name == "nt":
            # Native Windows has no kernel network-namespace backend, so a
            # no-network job fails closed here. Name the two real options so the
            # operator can make an informed choice instead of guessing.
            return (
                f"{base} Windows has no kernel network-isolation backend. "
                "Options: (1) run NULLA under WSL2/Linux (recommended) so bwrap/unshare/firejail "
                "provide kernel-enforced no-egress; or (2) set network_isolation_mode='heuristic_only' "
                "as an explicit, informed local override (static command guard only, no kernel isolation)."
            )
        return (
            f"{base} Set network_isolation_mode='heuristic_only' only for an explicit, informed local "
            "override (static command guard only, no kernel isolation)."
        )

    def _kernel_network_isolation_prefix(
        self, argv: list[str], allowed_roots: tuple[Path, ...]
    ) -> list[str] | None:
        # Prefer hardened Linux isolation backends when present.
        isolated = self._linux_bwrap_prefix(argv, allowed_roots)
        if isolated is not None:
            return isolated
        isolated = self._linux_unshare_prefix(argv)
        if isolated is not None:
            return isolated
        isolated = self._linux_firejail_prefix(argv)
        if isolated is not None:
            return isolated
        # macOS: real kernel-enforced network denial via Seatbelt (sandbox-exec).
        return self._macos_sandbox_exec_prefix(argv, allowed_roots)

    def _macos_sandbox_exec_prefix(
        self, argv: list[str], allowed_roots: tuple[Path, ...]
    ) -> list[str] | None:
        if sys.platform != "darwin":
            return None
        sandbox_exec = shutil.which("sandbox-exec")
        if not sandbox_exec:
            return None
        profile = _macos_confined_profile(allowed_roots)
        return [sandbox_exec, "-p", profile, "--", *list(argv)]

    def _linux_bwrap_prefix(
        self, argv: list[str], allowed_roots: tuple[Path, ...]
    ) -> list[str] | None:
        if os.name != "posix":
            return None
        if not sys.platform.startswith("linux"):
            return None
        bwrap = shutil.which("bwrap")
        if not bwrap:
            return None
        # FS isolation in addition to the network namespace: mount the whole
        # host read-only, then re-bind only the allowed workspace roots writable,
        # plus a private tmpfs for /tmp. 'unshare -n' alone gives NO filesystem
        # isolation, so bwrap is the preferred backend when present.
        cmd: list[str] = [bwrap, "--unshare-net", "--ro-bind", "/", "/", "--tmpfs", "/tmp"]
        for root in allowed_roots:
            resolved = str(Path(root).resolve())
            cmd += ["--bind", resolved, resolved]
        cmd += ["--", *list(argv)]
        return cmd

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
