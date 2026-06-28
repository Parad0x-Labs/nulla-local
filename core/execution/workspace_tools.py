from __future__ import annotations

import fnmatch
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .artifacts import build_file_diff_artifact, record_workspace_mutation

_SYMBOL_DEFINITION_TEMPLATES = (
    ("function_definition", r"^\s*def\s+{symbol}\b"),
    ("class_definition", r"^\s*class\s+{symbol}\b"),
    ("assignment", r"^\s*{symbol}\s*="),
    ("javascript_function", r"^\s*(?:export\s+)?function\s+{symbol}\b"),
    ("javascript_const", r"^\s*(?:export\s+)?(?:const|let|var)\s+{symbol}\b"),
)
_PATCH_TARGET_RE = re.compile(r"^(?:\+\+\+|---)\s+(?P<path>.+)$", re.MULTILINE)
_HUNK_HEADER_RE = re.compile(
    r"^@@\s+\-(?P<old_start>\d+)(?:,(?P<old_count>\d+))?\s+\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))?\s+@@"
)


def resolve_workspace_path(raw_path: str | None, *, workspace_root: Path) -> Path:
    raw = str(raw_path or "").strip()
    if not raw:
        return workspace_root
    candidate = Path(raw).expanduser()
    candidate = (workspace_root / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
    if candidate != workspace_root and workspace_root not in candidate.parents:
        raise ValueError("Path escapes the active workspace.")
    return candidate


def relative_path(path: Path, *, workspace_root: Path) -> str:
    try:
        return path.relative_to(workspace_root).as_posix() or "."
    except Exception:
        return path.as_posix()


def is_probably_text(path: Path) -> bool:
    try:
        sample = path.read_bytes()[:4096]
    except Exception:
        return False
    return b"\x00" not in sample


def force_fresh_source_timestamp(path: Path, *, minimum_mtime_ns: int = 0) -> None:
    if not path.exists() or not path.is_file():
        return
    stat_result = path.stat()
    target_ns = max(
        int(stat_result.st_mtime_ns) + 1,
        int(minimum_mtime_ns or 0) + 1_000_000_000,
        time.time_ns() + 1_000_000_000,
    )
    os.utime(path, ns=(target_ns, target_ns))


def iter_workspace_files(
    target: Path,
    *,
    workspace_root: Path,
    glob_pattern: str,
    limit: int,
) -> list[Path]:
    if target.is_file():
        return [target]
    matches: list[Path] = []
    for path in sorted(target.rglob("*")):
        if len(matches) >= limit:
            break
        if not path.is_file():
            continue
        relative = relative_path(path, workspace_root=workspace_root)
        if any(part.startswith(".") for part in Path(relative).parts):
            continue
        if glob_pattern not in {"", "*", "**", "**/*"} and not fnmatch.fnmatch(relative, glob_pattern) and not fnmatch.fnmatch(path.name, glob_pattern):
            continue
        matches.append(path)
    return matches


def list_tree_workspace(arguments: dict[str, Any], *, workspace_root: Path) -> dict[str, Any]:
    target = resolve_workspace_path(arguments.get("path"), workspace_root=workspace_root)
    limit = max(1, min(int(arguments.get("limit") or 60), 240))
    rows: list[dict[str, Any]] = []
    if target.is_file():
        rows.append({"path": relative_path(target, workspace_root=workspace_root), "kind": "file"})
    else:
        root_relative = relative_path(target, workspace_root=workspace_root)
        for path in sorted(target.rglob("*")):
            if len(rows) >= limit:
                break
            rel = relative_path(path, workspace_root=workspace_root)
            if any(part.startswith(".") for part in Path(rel).parts):
                continue
            rows.append({"path": rel, "kind": "directory" if path.is_dir() else "file"})
        if root_relative == "." and not rows:
            rows = []
    if not rows:
        return {
            "ok": True,
            "status": "no_results",
            "response_text": f"No files or directories matched inside `{relative_path(target, workspace_root=workspace_root)}`.",
            "details": {"path": relative_path(target, workspace_root=workspace_root), "entries": [], "truncated": False},
        }
    rendered = [f"Workspace tree under `{relative_path(target, workspace_root=workspace_root)}`:"]
    for row in rows:
        marker = "/" if row["kind"] == "directory" else ""
        rendered.append(f"- {row['path']}{marker}")
    return {
        "ok": True,
        "status": "executed",
        "response_text": "\n".join(rendered),
        "details": {
            "path": relative_path(target, workspace_root=workspace_root),
            "entries": rows,
            "truncated": len(rows) >= limit,
        },
    }


def symbol_search_workspace(arguments: dict[str, Any], *, workspace_root: Path) -> dict[str, Any]:
    symbol = str(arguments.get("symbol") or arguments.get("query") or "").strip()
    if not symbol:
        return {
            "ok": False,
            "status": "invalid_arguments",
            "response_text": "workspace.symbol_search needs a non-empty `symbol`.",
            "details": {"symbol": "", "matches": []},
        }
    target = resolve_workspace_path(arguments.get("path"), workspace_root=workspace_root)
    glob_pattern = str(arguments.get("glob") or "**/*").strip() or "**/*"
    limit = max(1, min(int(arguments.get("limit") or 20), 100))
    escaped = re.escape(symbol)
    patterns = [(kind, re.compile(template.format(symbol=escaped))) for kind, template in _SYMBOL_DEFINITION_TEMPLATES]
    matches: list[dict[str, Any]] = []
    for path in iter_workspace_files(target, workspace_root=workspace_root, glob_pattern=glob_pattern, limit=500):
        if not is_probably_text(path):
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        for index, line in enumerate(lines, start=1):
            kind = ""
            for candidate_kind, pattern in patterns:
                if pattern.search(line):
                    kind = candidate_kind
                    break
            if not kind and symbol not in line:
                continue
            if not kind:
                kind = "reference"
            rel = relative_path(path, workspace_root=workspace_root)
            matches.append({"path": rel, "line": index, "kind": kind, "snippet": line.strip()[:220]})
            if len(matches) >= limit:
                break
        if len(matches) >= limit:
            break
    if not matches:
        return {
            "ok": True,
            "status": "no_results",
            "response_text": f'No symbol matches for "{symbol}" were found in the workspace.',
            "details": {"symbol": symbol, "matches": []},
        }
    rendered = [f'Symbol matches for "{symbol}":']
    for row in matches:
        rendered.append(f"- {row['path']}:{row['line']} [{row['kind']}] {row['snippet']}")
    return {
        "ok": True,
        "status": "executed",
        "response_text": "\n".join(rendered),
        "details": {"symbol": symbol, "matches": matches, "match_count": len(matches)},
    }


def apply_unified_diff_workspace(
    arguments: dict[str, Any],
    *,
    workspace_root: Path,
    session_id: str | None,
) -> dict[str, Any]:
    patch_text = str(arguments.get("patch") or arguments.get("diff") or "").strip()
    if not patch_text:
        return {
            "ok": False,
            "status": "invalid_arguments",
            "response_text": "workspace.apply_unified_diff needs a non-empty `patch`.",
            "details": {"paths": []},
        }
    touched_paths = _extract_patch_paths(patch_text)
    if not touched_paths:
        return {
            "ok": False,
            "status": "invalid_arguments",
            "response_text": "No patch targets were found in the supplied unified diff.",
            "details": {"paths": []},
        }
    snapshots: list[dict[str, Any]] = []
    for relative in touched_paths:
        target = resolve_workspace_path(relative, workspace_root=workspace_root)
        existed_before = target.exists()
        before_text = target.read_text(encoding="utf-8", errors="replace") if existed_before and target.is_file() else ""
        snapshots.append(
            {
                "path": relative_path(target, workspace_root=workspace_root),
                "existed_before": existed_before,
                "before_text": before_text,
                "before_mtime_ns": int(target.stat().st_mtime_ns) if existed_before and target.is_file() else 0,
            }
        )

    applied_with = ""
    errors: list[str] = []
    git_available = shutil.which("git")
    if git_available:
        git_cmd = [git_available, "-C", str(workspace_root), "apply", "--whitespace=nowarn", "--recount", "-"]
        git_result = subprocess.run(git_cmd, input=patch_text, text=True, capture_output=True)
        if git_result.returncode == 0:
            applied_with = "git_apply"
        else:
            errors.append((git_result.stderr or git_result.stdout or "").strip())
    if not applied_with:
        try:
            _apply_unified_diff_python(patch_text, workspace_root=workspace_root)
            applied_with = "python_fallback"
        except Exception as exc:
            errors.append(str(exc).strip())
    if not applied_with:
        patch_available = shutil.which("patch")
        if patch_available:
            strip = "1" if "/dev/null" in patch_text or " a/" in patch_text or " b/" in patch_text or "\na/" in patch_text else "0"
            patch_cmd = [patch_available, f"-p{strip}", "-d", str(workspace_root), "--forward", "--batch"]
            patch_result = subprocess.run(patch_cmd, input=patch_text, text=True, capture_output=True)
            if patch_result.returncode == 0:
                applied_with = "patch"
            else:
                errors.append((patch_result.stderr or patch_result.stdout or "").strip())
    if not applied_with:
        error_text = "; ".join(item for item in errors if item) or "No supported patch engine was available."
        return {
            "ok": False,
            "status": "apply_failed",
            "response_text": f"I could not apply the unified diff: {error_text}",
            "details": {"paths": touched_paths, "errors": errors},
        }

    artifacts: list[dict[str, Any]] = []
    mutation_changes: list[dict[str, Any]] = []
    for snapshot in snapshots:
        target = resolve_workspace_path(snapshot["path"], workspace_root=workspace_root)
        exists_after = target.exists()
        after_text = target.read_text(encoding="utf-8", errors="replace") if exists_after and target.is_file() else ""
        if exists_after and snapshot["before_text"].endswith("\n") and after_text and not after_text.endswith("\n"):
            after_text = after_text + "\n"
            target.write_text(after_text, encoding="utf-8")
        if exists_after and target.is_file():
            force_fresh_source_timestamp(target, minimum_mtime_ns=int(snapshot.get("before_mtime_ns") or 0))
        action = "updated"
        if not snapshot["existed_before"] and exists_after:
            action = "created"
        elif snapshot["existed_before"] and not exists_after:
            action = "deleted"
        artifacts.append(
            build_file_diff_artifact(
                path=snapshot["path"],
                action=action,
                before=snapshot["before_text"],
                after=after_text,
                extra={"engine": applied_with},
            )
        )
        mutation_changes.append(
            {
                "path": snapshot["path"],
                "action": action,
                "existed_before": bool(snapshot["existed_before"]),
                "existed_after": bool(exists_after),
                "before_text": snapshot["before_text"],
                "after_text": after_text,
            }
        )
    mutation_record = record_workspace_mutation(
        session_id=session_id,
        workspace_root=workspace_root,
        intent="workspace.apply_unified_diff",
        changes=mutation_changes,
    )
    rendered = ["Applied unified diff:"]
    for path in touched_paths:
        rendered.append(f"- {path}")
    return {
        "ok": True,
        "status": "executed",
        "response_text": "\n".join(rendered),
        "details": {
            "paths": touched_paths,
            "artifacts": artifacts,
            "engine": applied_with,
            "mutation_record": {
                "mutation_id": str(mutation_record.get("mutation_id") or "").strip(),
                "session_key": str(mutation_record.get("session_key") or "").strip(),
            },
        },
    }


def _extract_patch_paths(patch_text: str) -> list[str]:
    candidates: list[str] = []
    for match in _PATCH_TARGET_RE.finditer(str(patch_text or "")):
        raw = str(match.group("path") or "").strip()
        if not raw or raw == "/dev/null":
            continue
        clean = raw
        if clean.startswith("a/") or clean.startswith("b/"):
            clean = clean[2:]
        if clean not in candidates:
            candidates.append(clean)
    return candidates


def _normalize_patch_path(raw_path: str) -> str:
    clean = str(raw_path or "").strip()
    if clean.startswith("a/") or clean.startswith("b/"):
        clean = clean[2:]
    return clean


def _apply_unified_diff_python(patch_text: str, *, workspace_root: Path) -> None:
    lines = str(patch_text or "").splitlines()
    index = 0
    applied_any = False
    while index < len(lines):
        line = lines[index]
        if not line.startswith("--- "):
            index += 1
            continue
        old_raw = str(line[4:] or "").strip()
        index += 1
        if index >= len(lines) or not lines[index].startswith("+++ "):
            raise ValueError("Malformed unified diff: missing `+++` header.")
        new_raw = str(lines[index][4:] or "").strip()
        index += 1
        target_raw = new_raw if new_raw != "/dev/null" else old_raw
        target_path = resolve_workspace_path(_normalize_patch_path(target_raw), workspace_root=workspace_root)
        before_lines = (
            target_path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
            if target_path.exists() and target_path.is_file()
            else []
        )
        cursor = 0
        rendered: list[str] = []
        saw_hunk = False
        while index < len(lines):
            current = lines[index]
            if current.startswith("--- "):
                break
            if not current.startswith("@@ "):
                index += 1
                continue
            match = _HUNK_HEADER_RE.match(current)
            if not match:
                raise ValueError(f"Malformed unified diff hunk header: {current}")
            saw_hunk = True
            old_start = max(0, int(match.group("old_start") or "0") - 1)
            if old_start < cursor:
                raise ValueError("Malformed unified diff: overlapping hunks are not supported.")
            rendered.extend(before_lines[cursor:old_start])
            cursor = old_start
            index += 1
            while index < len(lines):
                hunk_line = lines[index]
                if hunk_line.startswith("@@ ") or hunk_line.startswith("--- "):
                    break
                if hunk_line == r"\ No newline at end of file":
                    index += 1
                    continue
                prefix = hunk_line[:1]
                body = hunk_line[1:]
                if prefix == " ":
                    if cursor >= len(before_lines) or before_lines[cursor].rstrip("\n") != body:
                        raise ValueError(f"Unified diff context mismatch for `{target_path.name}`.")
                    rendered.append(before_lines[cursor])
                    cursor += 1
                elif prefix == "-":
                    if cursor >= len(before_lines) or before_lines[cursor].rstrip("\n") != body:
                        raise ValueError(f"Unified diff removal mismatch for `{target_path.name}`.")
                    cursor += 1
                elif prefix == "+":
                    rendered.append(body + "\n")
                else:
                    raise ValueError(f"Unsupported unified diff line: {hunk_line}")
                index += 1
        if not saw_hunk:
            raise ValueError("Malformed unified diff: no hunks were found.")
        rendered.extend(before_lines[cursor:])
        if new_raw == "/dev/null":
            if target_path.exists():
                target_path.unlink()
        else:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text("".join(rendered), encoding="utf-8")
        applied_any = True
    if not applied_any:
        raise ValueError("Malformed unified diff: no file headers were found.")
