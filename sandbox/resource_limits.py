from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ExecutionPolicy:
    workspace_root: Path
    writable_roots: tuple[Path, ...] = field(default_factory=tuple)
    max_seconds: int = 120
    max_output_kb: int = 256
    max_memory_mb: int = 512
    allow_network_egress: bool = False
    network_isolation_mode: str = "auto"  # auto | os_enforced | heuristic_only
    backend: str = "subprocess"


def normalize_policy(policy: ExecutionPolicy) -> ExecutionPolicy:
    writable_roots = tuple(Path(path).resolve() for path in (policy.writable_roots or (policy.workspace_root,)))
    return ExecutionPolicy(
        workspace_root=policy.workspace_root.resolve(),
        writable_roots=writable_roots,
        max_seconds=int(policy.max_seconds),
        max_output_kb=int(policy.max_output_kb),
        max_memory_mb=int(policy.max_memory_mb),
        allow_network_egress=bool(policy.allow_network_egress),
        network_isolation_mode=str(policy.network_isolation_mode or "auto"),
        backend=policy.backend,
    )


def path_within_roots(path: Path, roots: tuple[Path, ...]) -> bool:
    resolved = path.resolve()
    return any(resolved == root or root in resolved.parents for root in roots)


def _looks_like_path_arg(token: str) -> bool:
    """A token is treated as a filesystem path only when it clearly is one.

    We deliberately avoid flagging flags ("-c", "--prefix"), inline code/URLs,
    or bare relative names so that legitimate commands are not rejected. The
    escape vectors we must catch are (1) absolute paths and (2) relative paths
    that climb out of the cwd via ".." segments.
    """
    value = str(token or "")
    if not value:
        return False
    if value.startswith("-"):
        return False
    if value.startswith(("/", "~", "./", "../", ".\\", "..\\")) or value.startswith("\\"):
        return True
    if value[1:3] == ":\\" or value[1:3] == ":/":  # Windows drive paths, e.g. C:\
        return True
    # A relative token that escapes upward (e.g. "a/../../etc/passwd").
    parts = value.replace("\\", "/").split("/")
    return ".." in parts


def path_args_within_roots(argv: list[str], roots: tuple[Path, ...], *, cwd: Path) -> str | None:
    """Validate that every path-like argument resolves inside ``roots``.

    Returns ``None`` when all path-like arguments stay within the allowed roots,
    otherwise the offending raw token (so the caller can build an error). This is
    an OS-independent backstop in front of (not a replacement for) kernel
    sandboxing: it blocks reads/writes of absolute paths outside the workspace
    even on hosts with no Seatbelt/bwrap enforcement.
    """
    cwd_resolved = Path(cwd).resolve()
    for token in argv[1:]:
        if not _looks_like_path_arg(token):
            continue
        raw = str(token)
        expanded = Path(raw).expanduser()
        if expanded.is_absolute():
            candidate = expanded
        else:
            candidate = cwd_resolved / expanded
        if not path_within_roots(candidate, roots):
            return raw
    return None
