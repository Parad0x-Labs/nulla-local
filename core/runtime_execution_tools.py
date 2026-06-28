from __future__ import annotations

import difflib
import fnmatch
import json
import os
import platform
import re
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from core import policy_engine
from core.execution.artifacts import (
    build_command_artifact,
    build_failure_artifact,
    build_file_diff_artifact,
    latest_workspace_mutation,
    mark_workspace_mutation_promoted,
    record_workspace_mutation,
    rollback_last_workspace_mutation,
)
from core.execution.git_tools import git_diff_workspace, git_status_workspace, git_summary_workspace
from core.execution.validation_tools import render_validation_result, runtime_validation_command, validation_command
from core.execution.workspace_tools import (
    apply_unified_diff_workspace,
    force_fresh_source_timestamp,
    list_tree_workspace,
    symbol_search_workspace,
)
from core.execution.workspace_tools import (
    relative_path as workspace_relative_path,
)
from core.execution.workspace_tools import (
    resolve_workspace_path as resolve_workspace_path_impl,
)
from core.execution_gate import ExecutionGate
from core.hardware_tier import probe_machine
from core.install_recommendations import install_recommendation_machine_summary
from core.knowledge_marketplace import purchase_knowledge, search_listings
from core.learning import promote_verified_procedure
from core.runtime_paths import resolve_workspace_root
from core.runtime_tool_contracts import runtime_tool_contract_map, runtime_tool_contracts
from core.web0_tools import (
    web0_add_block,
    web0_add_gated_section,
    web0_compile_preview,
    web0_create_project,
    web0_encrypt_whole_site,
    web0_fill_slots,
    web0_open_builder_draft,
    web0_publish,
)
from network.signer import get_local_peer_id
from sandbox.network_guard import parse_command
from sandbox.sandbox_runner import SandboxRunner
from tools.browser.browser_render import browser_render

_EXECUTION_REQUEST_MARKERS = (
    "run ",
    "execute ",
    "command",
    "shell",
    "terminal",
    "repo",
    "repository",
    "project",
    "workspace",
    "read file",
    "open file",
    "search code",
    "find in files",
    "edit file",
    "change file",
    "patch file",
    "write file",
    "write files",
    "replace in file",
    # Natural file-mutation phrasing real users type from the chat/API surface.
    # Without these, "create a file test.txt with hello" never trips the
    # execution gate, so the tool loop is skipped and workspace.write_file is
    # never reached even though the executor is fully wired.
    "create a file",
    "create the file",
    "create file",
    "write a file",
    "write to file",
    "write to a file",
    "make a file",
    "new file",
    "save a file",
    "save to file",
    "save to a file",
    "save it to",
    "save this to",
    "append to file",
    "append to a file",
    "add to file",
    "overwrite file",
    "overwrite a file",
    "folder",
    "directory",
    "mkdir",
    "start coding",
    "initial files",
    "bootstrap",
    "pytest",
    "rg ",
    "grep ",
    "proceed",
    "do it",
    "go ahead",
    "carry on",
    "start working",
    "just do it",
    "deliver it",
    "submit it",
)
_FILE_LINE_RE = re.compile(r"(?P<path>[A-Za-z0-9_./-]+\.[A-Za-z0-9_+-]+):(?P<line>\d+)")
_SAFE_MACHINE_DIRECTORY_NAMES = ("Desktop", "Downloads", "Documents")


@dataclass
class RuntimeExecutionResult:
    handled: bool
    ok: bool
    status: str
    response_text: str = ""
    details: dict[str, Any] = field(default_factory=dict)


def _tool_observation(
    *,
    intent: str,
    tool_surface: str,
    ok: bool,
    status: str,
    **payload: Any,
) -> dict[str, Any]:
    observation = {
        "schema": "tool_observation_v1",
        "intent": str(intent or "").strip(),
        "tool_surface": str(tool_surface or "").strip(),
        "ok": bool(ok),
        "status": str(status or "").strip(),
    }
    for key, value in payload.items():
        if value in (None, "", [], {}):
            continue
        observation[str(key)] = value
    return observation


def _diff_preview(*, before: str, after: str, path: str, limit: int = 1600) -> str:
    diff_lines = list(
        difflib.unified_diff(
            str(before or "").splitlines(),
            str(after or "").splitlines(),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="",
            n=2,
        )
    )
    if not diff_lines:
        return ""
    preview = "\n".join(diff_lines[:40]).strip()
    if len(preview) <= limit:
        return preview
    return preview[: max(1, limit - 3)].rstrip() + "..."


def _extract_failure_summary(*, command: str, stdout: str, stderr: str, returncode: int) -> str:
    if int(returncode or 0) == 0:
        return ""
    combined = "\n".join(part for part in (str(stderr or "").strip(), str(stdout or "").strip()) if part).splitlines()
    hot_markers = ("FAILED", "FAIL", "ERROR", "AssertionError", "Traceback", "Exception", "E   ")
    for raw_line in combined:
        line = " ".join(str(raw_line or "").split()).strip()
        if not line:
            continue
        if any(marker in line for marker in hot_markers):
            return line[:260]
    for raw_line in combined:
        line = " ".join(str(raw_line or "").split()).strip()
        if line:
            return line[:260]
    return f"`{command}` exited with code {int(returncode or 0)}"


def _extract_command_failure_followup(*, stdout: str, stderr: str) -> dict[str, Any]:
    combined = "\n".join(part for part in (str(stderr or "").strip(), str(stdout or "").strip()) if part).strip()
    file_match = _FILE_LINE_RE.search(combined)
    path = str(file_match.group("path") or "").strip() if file_match else ""
    line_number = int(file_match.group("line") or 0) if file_match else 0
    diagnostic_query = ""
    lines = [item.strip() for item in combined.splitlines() if item.strip()]
    for line in lines:
        lowered = line.lower()
        if "assert" not in lowered:
            continue
        normalized = line.lstrip("> ").strip()
        expression = normalized.partition("assert")[2].strip() or normalized
        call_match = re.search(r"([A-Za-z_][A-Za-z0-9_\.]*)\s*\(", expression)
        if call_match:
            diagnostic_query = f"{call_match.group(1)}("
            break
        left_side = re.split(r"\s*(==|!=|<=|>=| is | in )\s*", expression, maxsplit=1)[0].strip()
        if left_side:
            diagnostic_query = left_side[:160]
            break
    if not diagnostic_query:
        for line in lines:
            lowered = line.lower()
            if any(token in lowered for token in ("error", "failed", "exception", "traceback")):
                diagnostic_query = line[:160]
                break
    return {
        "error_path": path,
        "error_line": line_number,
        "diagnostic_query": diagnostic_query,
    }


def runtime_execution_capability_ledger() -> list[dict[str, Any]]:
    ledger: dict[str, dict[str, Any]] = {}
    for contract in runtime_tool_contracts():
        entry = ledger.setdefault(
            contract.capability_id,
            {
                "capability_id": contract.capability_id,
                "surface": contract.tool_surface,
                "claim": contract.capability_claim,
                "supported": bool(contract.supported),
                "unsupported_reason": contract.unsupported_reason,
                "intents": [],
                "public_tag": contract.capability_id,
            },
        )
        entry["supported"] = bool(entry["supported"]) or bool(contract.supported)
        entry["intents"].append(contract.intent)
    return list(ledger.values())


def runtime_execution_tool_specs() -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for contract in runtime_tool_contracts():
        if not contract.supported:
            continue
        specs.append(
            {
                "intent": contract.intent,
                "description": contract.description,
                "read_only": contract.read_only,
                "arguments": dict(contract.input_schema),
                "output_schema": dict(contract.output_schema),
                "side_effect_class": contract.side_effect_class,
                "approval_requirement": contract.approval_requirement,
                "timeout_policy": contract.timeout_policy,
                "retry_policy": contract.retry_policy,
                "artifact_emission": contract.artifact_emission,
                "error_contract": contract.error_contract,
            }
        )
    return specs


def extract_observation_followup_hints(observation: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(observation or {})
    intent = str(payload.get("intent") or "").strip()
    if not intent:
        return {}
    if intent == "machine.list_directory":
        entries = [dict(item) for item in list(payload.get("entries") or []) if isinstance(item, dict)]
        return {
            "intent": intent,
            "path": str(payload.get("path") or "").strip(),
            "directories_only": bool(payload.get("directories_only", False)),
            "entry_count": int(payload.get("count") or len(entries)),
            "entries": entries,
            "paths": [str(item.get("path") or "").strip() for item in entries if str(item.get("path") or "").strip()],
        }
    if intent == "machine.inspect_specs":
        return {
            "intent": intent,
            "chip_name": str(payload.get("chip_name") or "").strip(),
            "os_name": str(payload.get("os_name") or "").strip(),
            "os_version": str(payload.get("os_version") or "").strip(),
            "cpu_cores": int(payload.get("cpu_cores") or 0),
            "ram_gb": float(payload.get("ram_gb") or 0.0),
            "gpu_name": str(payload.get("gpu_name") or "").strip(),
            "vram_gb": float(payload.get("vram_gb") or 0.0) if payload.get("vram_gb") is not None else None,
            "accelerator": str(payload.get("accelerator") or "").strip(),
            "recommended_model": str(payload.get("recommended_model") or "").strip(),
            "selected_tier": str(payload.get("selected_tier") or "").strip(),
            "capacity_bucket": str(payload.get("capacity_bucket") or "").strip(),
            "recommended_bundle_models": [
                str(item).strip()
                for item in list(payload.get("recommended_bundle_models") or [])
                if str(item).strip()
            ],
            "display_name": str(payload.get("display_name") or "").strip(),
            "display_native_resolution": str(payload.get("display_native_resolution") or "").strip(),
            "display_current_resolution": str(payload.get("display_current_resolution") or "").strip(),
            "screen_size": str(payload.get("screen_size") or "").strip(),
        }
    if intent == "machine.read_file":
        lines = [dict(item) for item in list(payload.get("lines") or []) if isinstance(item, dict)]
        return {
            "intent": intent,
            "path": str(payload.get("path") or "").strip(),
            "start_line": int(payload.get("start_line") or 1),
            "line_count": int(payload.get("line_count") or 0),
            "lines": lines,
            "content": "\n".join(str(item.get("text") or "") for item in lines),
            "verbatim": bool(payload.get("verbatim", False)),
        }
    if intent == "machine.ensure_directory":
        return {
            "intent": intent,
            "path": str(payload.get("path") or "").strip(),
            "action": str(payload.get("action") or "").strip(),
            "already_present": bool(payload.get("already_present", False)),
        }
    if intent == "machine.write_file":
        return {
            "intent": intent,
            "path": str(payload.get("path") or "").strip(),
            "line_count": int(payload.get("line_count") or 0),
            "action": str(payload.get("action") or "").strip(),
        }
    if intent == "web0.open_builder_draft":
        return {
            "intent": intent,
            "builder_url": str(payload.get("builder_url") or "").strip(),
            "template": str(payload.get("template") or "").strip(),
            "domain": str(payload.get("domain") or "").strip(),
            "title": str(payload.get("title") or "").strip(),
        }
    if intent == "workspace.search_text":
        matches = [dict(item) for item in list(payload.get("matches") or []) if isinstance(item, dict)]
        primary = dict((matches[:1] or [{}])[0] or {})
        return {
            "intent": intent,
            "match_count": int(payload.get("match_count") or len(matches)),
            "paths": [str(item.get("path") or "").strip() for item in matches if str(item.get("path") or "").strip()],
            "primary_path": str(primary.get("path") or "").strip(),
            "primary_line": int(primary.get("line") or 0) if str(primary.get("line") or "").strip() else 0,
            "primary_snippet": str(primary.get("snippet") or "").strip(),
        }
    if intent == "workspace.symbol_search":
        matches = [dict(item) for item in list(payload.get("matches") or []) if isinstance(item, dict)]
        primary = dict((matches[:1] or [{}])[0] or {})
        return {
            "intent": intent,
            "symbol": str(payload.get("symbol") or "").strip(),
            "match_count": int(payload.get("match_count") or len(matches)),
            "paths": [str(item.get("path") or "").strip() for item in matches if str(item.get("path") or "").strip()],
            "primary_path": str(primary.get("path") or "").strip(),
            "primary_line": int(primary.get("line") or 0) if str(primary.get("line") or "").strip() else 0,
            "primary_kind": str(primary.get("kind") or "").strip(),
        }
    if intent == "workspace.read_file":
        lines = [dict(item) for item in list(payload.get("lines") or []) if isinstance(item, dict)]
        return {
            "intent": intent,
            "path": str(payload.get("path") or "").strip(),
            "start_line": int(payload.get("start_line") or 1),
            "line_count": int(payload.get("line_count") or 0),
            "lines": lines,
            "content": "\n".join(str(item.get("text") or "") for item in lines),
            "verbatim": bool(payload.get("verbatim", False)),
        }
    if intent == "workspace.ensure_directory":
        return {
            "intent": intent,
            "path": str(payload.get("path") or "").strip(),
            "action": str(payload.get("action") or "").strip(),
            "already_present": bool(payload.get("already_present", False)),
        }
    if intent == "workspace.write_file":
        return {
            "intent": intent,
            "path": str(payload.get("path") or "").strip(),
            "line_count": int(payload.get("line_count") or 0),
            "action": str(payload.get("action") or "").strip(),
        }
    if intent == "workspace.apply_unified_diff":
        return {
            "intent": intent,
            "paths": [str(item).strip() for item in list(payload.get("paths") or []) if str(item).strip()],
            "engine": str(payload.get("engine") or "").strip(),
        }
    if intent == "workspace.replace_in_file":
        return {
            "intent": intent,
            "path": str(payload.get("path") or "").strip(),
            "replacements": int(payload.get("replacements") or 0),
        }
    if intent == "workspace.rollback_last_change":
        return {
            "intent": intent,
            "restored_paths": [str(item).strip() for item in list(payload.get("restored_paths") or []) if str(item).strip()],
            "removed_paths": [str(item).strip() for item in list(payload.get("removed_paths") or []) if str(item).strip()],
        }
    if intent in {"workspace.run_tests", "workspace.run_lint", "workspace.run_formatter"}:
        followup = _extract_command_failure_followup(
            stdout=str(payload.get("stdout") or ""),
            stderr=str(payload.get("stderr") or ""),
        )
        return {
            "intent": intent,
            "command": str(payload.get("command") or "").strip(),
            "cwd": str(payload.get("cwd") or "").strip(),
            "returncode": int(payload.get("returncode") or 0),
            "success": bool(payload.get("success", False)),
            "error_path": str(payload.get("error_path") or followup.get("error_path") or "").strip(),
            "error_line": int(payload.get("error_line") or followup.get("error_line") or 0),
            "diagnostic_query": str(payload.get("diagnostic_query") or followup.get("diagnostic_query") or "").strip(),
        }
    if intent in {"workspace.git_status", "workspace.git_diff"}:
        return {
            "intent": intent,
            "command": str(payload.get("command") or "").strip(),
            "cwd": str(payload.get("cwd") or "").strip(),
            "returncode": int(payload.get("returncode") or 0),
            "success": bool(payload.get("success", False)),
        }
    if intent == "workspace.git_summary":
        return {
            "intent": intent,
            "cwd": str(payload.get("cwd") or "").strip(),
            "branch": str(payload.get("branch") or "").strip(),
            "commit": str(payload.get("commit") or "").strip(),
            "dirty": bool(payload.get("dirty", False)),
            "local_branch_count": int(payload.get("local_branch_count") or 0),
            "remote_branch_count": int(payload.get("remote_branch_count") or 0),
            "total_branch_count": int(payload.get("total_branch_count") or 0),
            "today_date": str(payload.get("today_date") or "").strip(),
            "today_commit_count": int(payload.get("today_commit_count") or 0),
            "yesterday_date": str(payload.get("yesterday_date") or "").strip(),
            "yesterday_commit_count": int(payload.get("yesterday_commit_count") or 0),
            "recent_commits": [dict(item) for item in list(payload.get("recent_commits") or []) if isinstance(item, dict)],
            "commit_count_scope": str(payload.get("commit_count_scope") or "").strip(),
            "timezone_label": str(payload.get("timezone_label") or "").strip(),
        }
    if intent == "orchestration.execute_envelope":
        return {
            "intent": intent,
            "task_id": str(payload.get("task_id") or "").strip(),
            "task_role": str(payload.get("task_role") or "").strip(),
            "receipt_count": int(payload.get("receipt_count") or 0),
            "child_count": int(payload.get("child_count") or 0),
            "merged_strategy": str(payload.get("merged_strategy") or "").strip(),
        }
    if intent == "sandbox.run_command":
        followup = _extract_command_failure_followup(
            stdout=str(payload.get("stdout") or ""),
            stderr=str(payload.get("stderr") or ""),
        )
        return {
            "intent": intent,
            "command": str(payload.get("command") or "").strip(),
            "cwd": str(payload.get("cwd") or "").strip(),
            "returncode": int(payload.get("returncode") or 0),
            "success": bool(payload.get("success", False)),
            "error_path": str(followup.get("error_path") or "").strip(),
            "error_line": int(followup.get("error_line") or 0),
            "diagnostic_query": str(followup.get("diagnostic_query") or "").strip(),
        }
    if intent == "web.search":
        results = [dict(item) for item in list(payload.get("results") or []) if isinstance(item, dict)]
        primary = dict((results[:1] or [{}])[0] or {})
        return {
            "intent": intent,
            "result_count": int(payload.get("result_count") or len(results)),
            "primary_url": str(primary.get("url") or "").strip(),
            "primary_domain": str(primary.get("origin_domain") or primary.get("domain") or "").strip(),
            "domains": [
                str(item.get("origin_domain") or item.get("domain") or "").strip()
                for item in results
                if str(item.get("origin_domain") or item.get("domain") or "").strip()
            ],
        }
    if intent in {"web.fetch", "web.browser_render", "web.research"}:
        return {
            "intent": intent,
            "url": str(payload.get("url") or payload.get("final_url") or "").strip(),
            "final_url": str(payload.get("final_url") or payload.get("url") or "").strip(),
            "query": str(payload.get("query") or "").strip(),
            "status": str(payload.get("status") or "").strip(),
            "hit_count": int(payload.get("hit_count") or 0),
            "evidence_strength": str(payload.get("evidence_strength") or "").strip(),
            "title": str(payload.get("title") or "").strip(),
        }
    return {"intent": intent}


def looks_like_execution_request(user_text: str, *, task_class: str) -> bool:
    lowered = str(user_text or "").strip().lower()
    if not lowered:
        return False
    if looks_like_advice_only_execution_prompt(lowered):
        return False
    if task_class in {"debugging", "dependency_resolution", "config", "file_inspection", "shell_guidance"}:
        return True
    return any(marker in lowered for marker in _EXECUTION_REQUEST_MARKERS)


def looks_like_advice_only_execution_prompt(user_text: str) -> bool:
    lowered = f" {' '.join(str(user_text or '').strip().lower().split())} "
    if not lowered.strip():
        return False
    hard_stop_markers = (
        " do not edit ",
        " don't edit ",
        " do not write ",
        " don't write ",
        " do not modify ",
        " don't modify ",
        " no file changes ",
        " no files ",
        " advice only ",
        " plan only ",
        " just plan ",
        " explain only ",
    )
    if any(marker in lowered for marker in hard_stop_markers):
        return True
    advisory_markers = (
        " plan ",
        " explain ",
        " review ",
        " advise ",
        " recommend ",
        " design ",
        " think through ",
        " reason through ",
        " what should ",
        " should i ",
        " should we ",
    )
    execution_markers = (
        " run ",
        " execute ",
        " apply ",
        " edit ",
        " write ",
        " modify ",
        " create ",
        " delete ",
        " remove ",
        " commit ",
        " push ",
        " install ",
        " start working ",
        " carry on ",
        " go ahead ",
        " do it ",
    )
    return any(marker in lowered for marker in advisory_markers) and not any(
        marker in lowered for marker in execution_markers
    )


def execute_runtime_tool(
    intent: str,
    arguments: dict[str, Any],
    *,
    source_context: dict[str, Any] | None = None,
) -> RuntimeExecutionResult | None:
    contract = runtime_tool_contract_map().get(intent)
    if contract is None:
        return None
    if not contract.supported:
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="disabled",
            response_text=contract.unsupported_reason,
            details={
                "observation": _tool_observation(
                    intent=intent,
                    tool_surface=contract.tool_surface,
                    ok=False,
                    status="disabled",
                    reason=contract.unsupported_reason,
                ),
            },
        )
    workspace_root = _workspace_root(source_context)
    try:
        if intent == "workspace.list_files":
            return _list_files(arguments, workspace_root=workspace_root)
        if intent == "machine.list_directory":
            return _list_machine_directory(arguments)
        if intent == "machine.inspect_specs":
            return _inspect_machine_specs()
        if intent == "machine.read_file":
            return _read_machine_file(arguments)
        if intent == "machine.ensure_directory":
            return _ensure_machine_directory(arguments)
        if intent == "machine.write_file":
            return _write_machine_file(arguments)
        if intent == "web0.open_builder_draft":
            return _web0_open_builder_draft(arguments)
        if intent == "web0.create_project":
            return _web0_create_project(arguments)
        if intent == "web0.fill_slots":
            return _web0_fill_slots(arguments)
        if intent == "web0.add_block":
            return _web0_add_block(arguments)
        if intent == "web0.add_gated_section":
            return _web0_add_gated_section(arguments)
        if intent == "web0.encrypt_whole_site":
            return _web0_encrypt_whole_site(arguments)
        if intent == "web0.compile_preview":
            return _web0_compile_preview(arguments)
        if intent == "web0.publish":
            return _web0_publish(arguments, source_context=source_context)
        if intent == "marketplace.search_listings":
            return _marketplace_search_listings(arguments)
        if intent == "marketplace.purchase_knowledge":
            return _marketplace_purchase_knowledge(arguments)
        if intent == "web.fetch":
            return _web_fetch(arguments, source_context=source_context)
        if intent == "web.browser_render":
            return _web_browser_render(arguments, source_context=source_context)
        if intent == "workspace.list_tree":
            return _list_tree(arguments, workspace_root=workspace_root)
        if intent == "workspace.search_text":
            return _search_text(arguments, workspace_root=workspace_root)
        if intent == "workspace.symbol_search":
            return _symbol_search(arguments, workspace_root=workspace_root)
        if intent == "workspace.read_file":
            return _read_file(arguments, workspace_root=workspace_root)
        if intent == "workspace.ensure_directory":
            return _ensure_directory(arguments, workspace_root=workspace_root)
        if intent == "workspace.write_file":
            return _write_file(arguments, workspace_root=workspace_root, session_id=_runtime_session_id(source_context))
        if intent == "workspace.replace_in_file":
            return _replace_in_file(arguments, workspace_root=workspace_root, session_id=_runtime_session_id(source_context))
        if intent == "workspace.apply_unified_diff":
            return _apply_unified_diff(arguments, workspace_root=workspace_root, session_id=_runtime_session_id(source_context))
        if intent == "workspace.git_status":
            return _git_status(arguments, workspace_root=workspace_root)
        if intent == "workspace.git_diff":
            return _git_diff(arguments, workspace_root=workspace_root)
        if intent == "workspace.git_summary":
            return _git_summary(arguments, workspace_root=workspace_root)
        if intent == "workspace.rollback_last_change":
            return _rollback_last_change(arguments, workspace_root=workspace_root, session_id=_runtime_session_id(source_context))
        if intent == "workspace.run_tests":
            result = _run_validation("workspace.run_tests", arguments, workspace_root=workspace_root)
            return _attach_procedure_learning(
                result,
                validation_intent="workspace.run_tests",
                workspace_root=workspace_root,
                source_context=source_context,
            )
        if intent == "workspace.run_lint":
            result = _run_validation("workspace.run_lint", arguments, workspace_root=workspace_root)
            return _attach_procedure_learning(
                result,
                validation_intent="workspace.run_lint",
                workspace_root=workspace_root,
                source_context=source_context,
            )
        if intent == "workspace.run_formatter":
            result = _run_validation("workspace.run_formatter", arguments, workspace_root=workspace_root)
            return _attach_procedure_learning(
                result,
                validation_intent="workspace.run_formatter",
                workspace_root=workspace_root,
                source_context=source_context,
            )
        if intent == "orchestration.execute_envelope":
            return _execute_task_envelope_intent(
                arguments,
                workspace_root=workspace_root,
                session_id=_runtime_session_id(source_context),
                source_context=source_context,
            )
        if intent == "sandbox.run_command":
            return _run_command(arguments, workspace_root=workspace_root)
    except Exception as exc:
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="error",
            response_text=f"Execution tool `{intent}` failed: {exc}",
            details={
                "error": str(exc),
                "observation": _tool_observation(
                    intent=intent,
                    tool_surface="runtime_execution",
                    ok=False,
                    status="error",
                    error=str(exc),
                ),
            },
        )
    return RuntimeExecutionResult(
        handled=True,
        ok=False,
        status="unsupported",
        response_text=f"I won't fake it: `{intent}` is not supported by the runtime execution layer.",
        details={
            "observation": _tool_observation(
                intent=intent,
                tool_surface="runtime_execution",
                ok=False,
                status="unsupported",
            ),
        },
    )


def _web0_open_builder_draft(arguments: dict[str, Any]) -> RuntimeExecutionResult:
    result = web0_open_builder_draft(
        str(arguments.get("title") or "NULLA-built Web0 draft"),
        str(arguments.get("code") or ""),
        domain=str(arguments.get("domain") or ""),
    )
    builder_url = str(result.get("builder_url") or "")
    response = (
        "Web0 local builder draft ready:\n"
        f"{builder_url}\n\n"
        "This is local preview/edit only. Publishing to Arweave/mainnet still requires an explicit publish command and wallet confirmation."
    )
    return RuntimeExecutionResult(
        handled=True,
        ok=True,
        status="executed",
        response_text=response,
        details={
            **result,
            "observation": _tool_observation(
                intent="web0.open_builder_draft",
                tool_surface="web0",
                ok=True,
                status="executed",
                builder_url=builder_url,
                template=str(result.get("template") or ""),
                domain=str(result.get("domain") or ""),
                title=str(result.get("title") or ""),
            ),
        },
    )


def _web0_result(
    intent: str,
    result: dict[str, Any],
    *,
    ok_response: str,
    **observation_fields: Any,
) -> RuntimeExecutionResult:
    error = str(result.get("error") or "").strip()
    if error:
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="rejected",
            response_text=f"`{intent}` could not complete: {error}",
            details={
                **result,
                "observation": _tool_observation(
                    intent=intent, tool_surface="web0", ok=False, status="rejected", error=error
                ),
            },
        )
    return RuntimeExecutionResult(
        handled=True,
        ok=True,
        status="executed",
        response_text=ok_response,
        details={
            **result,
            "observation": _tool_observation(
                intent=intent,
                tool_surface="web0",
                ok=True,
                status="executed",
                project_id=str(result.get("project_id") or ""),
                result_status=str(result.get("status") or ""),
                **observation_fields,
            ),
        },
    )


def _web0_create_project(arguments: dict[str, Any]) -> RuntimeExecutionResult:
    result = web0_create_project(
        str(arguments.get("template_id") or ""),
        str(arguments.get("domain") or ""),
        str(arguments.get("project_name") or ""),
    )
    return _web0_result(
        "web0.create_project",
        result,
        ok_response=(
            f"Web0 builder project `{result.get('project_id', '')}` created for "
            f"{result.get('domain', '')} ({result.get('source', 'local_draft')})."
        ),
        domain=str(result.get("domain") or ""),
    )


def _web0_fill_slots(arguments: dict[str, Any]) -> RuntimeExecutionResult:
    slots = arguments.get("slots")
    result = web0_fill_slots(
        str(arguments.get("project_id") or ""),
        slots if isinstance(slots, dict) else {},
    )
    updated = result.get("updated_slots") or []
    return _web0_result(
        "web0.fill_slots",
        result,
        ok_response=f"Filled {len(updated)} slot(s): {', '.join(str(s) for s in updated)}.",
    )


def _web0_add_block(arguments: dict[str, Any]) -> RuntimeExecutionResult:
    block = arguments.get("block")
    result = web0_add_block(
        str(arguments.get("project_id") or ""),
        str(arguments.get("page_id") or "home"),
        block if isinstance(block, dict) else {},
        int(arguments.get("position", -1) or -1),
    )
    return _web0_result(
        "web0.add_block",
        result,
        ok_response=(
            f"Block added to page `{result.get('page_id', 'home')}` "
            f"({result.get('block_count', 0)} block(s) on the page)."
        ),
    )


def _coerce_str_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _web0_add_gated_section(arguments: dict[str, Any]) -> RuntimeExecutionResult:
    result = web0_add_gated_section(
        str(arguments.get("project_id") or ""),
        str(arguments.get("page_id") or "home"),
        str(arguments.get("content") or ""),
        _coerce_str_list(arguments.get("whitelist")),
        str(arguments.get("mode") or "whitelist"),
        str(arguments.get("label") or "Private content"),
        int(arguments.get("position", -1) or -1),
    )
    return _web0_result(
        "web0.add_gated_section",
        result,
        ok_response=(
            f"Gated section added (block `{result.get('block_id', '')}`, "
            f"{result.get('wallets_registered', 0)} wallet(s) whitelisted). Only the ciphertext ships."
        ),
        block_id=str(result.get("block_id") or ""),
    )


def _web0_encrypt_whole_site(arguments: dict[str, Any]) -> RuntimeExecutionResult:
    result = web0_encrypt_whole_site(
        str(arguments.get("project_id") or ""),
        _coerce_str_list(arguments.get("whitelist")),
        str(arguments.get("mode") or "whitelist"),
        str(arguments.get("label") or "Private content"),
    )
    return _web0_result(
        "web0.encrypt_whole_site",
        result,
        ok_response=(
            f"Encrypted {result.get('blocks_protected', 0)} block(s) behind the wallet gate; "
            "the plaintext is no longer present in the compiled page."
        ),
        blocks_protected=int(result.get("blocks_protected") or 0),
    )


def _web0_compile_preview(arguments: dict[str, Any]) -> RuntimeExecutionResult:
    result = web0_compile_preview(str(arguments.get("project_id") or ""))
    return _web0_result(
        "web0.compile_preview",
        result,
        ok_response=(
            f"Compiled a local preview ({result.get('size_kb', 0)} KB, "
            f"source={result.get('source', 'local_fallback')}). Nothing was published."
        ),
        size_kb=float(result.get("size_kb") or 0.0),
    )


def _web0_publish(
    arguments: dict[str, Any],
    *,
    source_context: dict[str, Any] | None,
) -> RuntimeExecutionResult:
    """Gated off by default. The autonomous layer never publishes on its own.

    BOTH gates are read from the trusted ``source_context``, never from the
    model-supplied ``arguments``: the ``allow_network_publish`` opt-in and the
    ``nulla_wallet``. The model can emit the intent but can neither flip the
    opt-in nor conjure a wallet, so a request alone is never sufficient to
    publish — only a trusted caller that has wired both can.
    """
    project_id = str(arguments.get("project_id") or "")
    ctx = source_context or {}
    allow_network_publish = bool(ctx.get("allow_network_publish", False))
    wallet = ctx.get("nulla_wallet")

    if not allow_network_publish or wallet is None:
        reason = (
            "No publish opt-in was given (allow_network_publish is off)."
            if not allow_network_publish
            else "No publishing wallet is wired into this runtime."
        )
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="requires_opt_in",
            response_text=(
                "I won't publish to Arweave from here. "
                f"{reason} Publishing uploads content permanently and needs an explicit "
                "opt-in plus a caller-supplied wallet — it never runs autonomously."
            ),
            details={
                "project_id": project_id,
                "status": "publish_gated_off",
                "allow_network_publish": allow_network_publish,
                "wallet_present": wallet is not None,
                "observation": _tool_observation(
                    intent="web0.publish",
                    tool_surface="web0",
                    ok=False,
                    status="requires_opt_in",
                    project_id=project_id,
                    allow_network_publish=allow_network_publish,
                    wallet_present=wallet is not None,
                ),
            },
        )

    result = web0_publish(project_id, wallet, allow_network_publish=True)
    return _web0_result(
        "web0.publish",
        result,
        ok_response=(
            f"Published to Arweave: {result.get('permanent_url', '')} "
            f"(tx {result.get('arweave_txid', '')})."
        ),
        arweave_txid=str(result.get("arweave_txid") or ""),
    )


def _marketplace_search_listings(arguments: dict[str, Any]) -> RuntimeExecutionResult:
    listings = search_listings(
        query=str(arguments.get("query") or ""),
        domain_tag=str(arguments.get("domain_tag") or ""),
        max_results=int(arguments.get("max_results", 50) or 50),
    )
    rows = [
        {
            "shard_id": item.shard_id,
            "title": item.title,
            "seller_peer_id": item.seller_peer_id,
            "price_credits": item.price_credits,
            "quality_score": item.quality_score,
            "purchase_count": item.purchase_count,
            "domain_tags": list(item.domain_tags),
        }
        for item in listings
    ]
    return RuntimeExecutionResult(
        handled=True,
        ok=True,
        status="executed",
        response_text=(f"Found {len(rows)} marketplace listing(s)." if rows else "No marketplace listings matched."),
        details={
            "listings": rows,
            "count": len(rows),
            "observation": _tool_observation(
                intent="marketplace.search_listings",
                tool_surface="marketplace",
                ok=True,
                status="executed",
                count=len(rows),
                shard_ids=[r["shard_id"] for r in rows][:20],
            ),
        },
    )


def _marketplace_purchase_knowledge(arguments: dict[str, Any]) -> RuntimeExecutionResult:
    """Buy a shard for the LOCAL node. The buyer is always this node's peer id —
    the model can pick a shard but cannot spend another peer's credits."""
    shard_id = str(arguments.get("shard_id") or "").strip()
    if not shard_id:
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="rejected",
            response_text="`marketplace.purchase_knowledge` needs a shard_id.",
            details={
                "observation": _tool_observation(
                    intent="marketplace.purchase_knowledge",
                    tool_surface="marketplace",
                    ok=False,
                    status="rejected",
                    error="missing_shard_id",
                ),
            },
        )

    buyer = get_local_peer_id()
    receipt_id = str(arguments.get("receipt_id") or "").strip() or None
    result = purchase_knowledge(buyer, shard_id, receipt_id=receipt_id)

    ok = bool(result.get("ok"))
    reason = str(result.get("reason") or "")
    charged = float(result.get("charged_credits") or 0.0)
    if ok and reason == "already_purchased":
        response = f"Already unlocked shard `{shard_id}` — no credits charged."
    elif ok:
        response = f"Purchased shard `{shard_id}` for {charged} credit(s); access unlocked."
    else:
        response = f"Could not buy shard `{shard_id}`: {reason}."
    status = "executed" if ok else "rejected"

    return RuntimeExecutionResult(
        handled=True,
        ok=ok,
        status=status,
        response_text=response,
        details={
            **result,
            "buyer_peer_id": buyer,
            "observation": _tool_observation(
                intent="marketplace.purchase_knowledge",
                tool_surface="marketplace",
                ok=ok,
                status=status,
                shard_id=shard_id,
                reason=reason,
                charged_credits=charged,
            ),
        },
    )


def _remote_fetch_allowed(source_context: dict[str, Any] | None) -> bool:
    if "allow_remote_fetch" in (source_context or {}):
        return bool((source_context or {}).get("allow_remote_fetch"))
    return bool(policy_engine.allow_web_fallback())


def _web_url(raw_url: object) -> str:
    url = str(raw_url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("web tools require an absolute http(s) URL")
    return url


def _web_fetch(arguments: dict[str, Any], *, source_context: dict[str, Any] | None) -> RuntimeExecutionResult:
    url = _web_url(arguments.get("url"))
    if not _remote_fetch_allowed(source_context):
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="disabled_by_policy",
            response_text=f"Web fetch is disabled by policy for `{url}`.",
            details={
                "observation": _tool_observation(
                    intent="web.fetch",
                    tool_surface="web",
                    ok=False,
                    status="disabled_by_policy",
                    url=url,
                ),
            },
        )
    timeout_seconds = max(1.0, min(float(arguments.get("timeout_seconds") or 12.0), 30.0))
    max_bytes = max(16_384, min(int(policy_engine.max_fetch_bytes()), 2_000_000))
    request = urllib.request.Request(url, headers={"User-Agent": "NULLA-runtime/1.0", "Accept": "text/html,text/plain,*/*"})
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            final_url = str(response.geturl() or url)
            content_type = str(response.headers.get("content-type") or "").strip()
            body = response.read(max_bytes + 1)
    except urllib.error.HTTPError as exc:
        status = "login_wall" if int(exc.code or 0) in {401, 403} else "error"
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status=status,
            response_text=f"Web fetch for `{url}` failed with HTTP {int(exc.code or 0)}.",
            details={
                "observation": _tool_observation(
                    intent="web.fetch",
                    tool_surface="web",
                    ok=False,
                    status=status,
                    url=url,
                    http_status=int(exc.code or 0),
                ),
            },
        )
    except Exception as exc:
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="error",
            response_text=f"Web fetch for `{url}` failed: {exc}",
            details={
                "observation": _tool_observation(
                    intent="web.fetch",
                    tool_surface="web",
                    ok=False,
                    status="error",
                    url=url,
                    error=str(exc),
                ),
            },
        )
    truncated = len(body) > max_bytes
    text = body[:max_bytes].decode("utf-8", errors="replace")
    return RuntimeExecutionResult(
        handled=True,
        ok=True,
        status="ok",
        response_text=f"Fetched `{final_url}` ({len(text)} chars{', truncated' if truncated else ''}).",
        details={
            "url": url,
            "final_url": final_url,
            "content_type": content_type,
            "text": text,
            "truncated": truncated,
            "observation": _tool_observation(
                intent="web.fetch",
                tool_surface="web",
                ok=True,
                status="ok",
                url=url,
                final_url=final_url,
                content_type=content_type,
                text=text,
                truncated=truncated,
            ),
        },
    )


def _web_browser_render(arguments: dict[str, Any], *, source_context: dict[str, Any] | None) -> RuntimeExecutionResult:
    url = _web_url(arguments.get("url"))
    if not _remote_fetch_allowed(source_context):
        status = "disabled_by_policy"
        rendered = {"status": status, "final_url": url}
    else:
        rendered = browser_render(
            url,
            timeout_ms=max(1000, min(int(arguments.get("timeout_ms") or 20_000), 60_000)),
            max_scroll=max(0, min(int(arguments.get("max_scroll") or 2), 8)),
        )
        status = str(rendered.get("status") or "error").strip() or "error"
    ok = status == "ok"
    final_url = str(rendered.get("final_url") or url)
    if ok:
        response_text = f"Rendered `{final_url}` in browser."
    elif status in {"disabled_by_policy", "missing_dependency", "captcha", "login_wall"}:
        response_text = f"Browser render for `{url}` is `{status}`."
    else:
        response_text = f"Browser render for `{url}` failed."
    return RuntimeExecutionResult(
        handled=True,
        ok=ok,
        status=status,
        response_text=response_text,
        details={
            **dict(rendered),
            "url": url,
            "observation": _tool_observation(
                intent="web.browser_render",
                tool_surface="web",
                ok=ok,
                status=status,
                url=url,
                final_url=final_url,
                title=str(rendered.get("title") or ""),
                text=str(rendered.get("text") or ""),
                links=list(rendered.get("links") or []),
            ),
        },
    )


def _workspace_root(source_context: dict[str, Any] | None) -> Path:
    raw = str((source_context or {}).get("workspace") or (source_context or {}).get("workspace_root") or "").strip()
    return resolve_workspace_root(raw or None)


def _resolve_workspace_path(raw_path: str | None, *, workspace_root: Path) -> Path:
    return resolve_workspace_path_impl(raw_path, workspace_root=workspace_root)


def _relative_path(path: Path, *, workspace_root: Path) -> str:
    return workspace_relative_path(path, workspace_root=workspace_root)


def _truncate(text: str, *, limit: int = 1800) -> str:
    value = str(text or "")
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "\n...[truncated]"


def _home_relative_label(path: Path) -> str:
    home = _machine_home().resolve()
    try:
        relative = path.resolve().relative_to(home)
        return "~" if str(relative) in {"", "."} else f"~/{relative.as_posix()}"
    except Exception:
        return path.as_posix()


def _safe_machine_roots() -> tuple[Path, ...]:
    home = _machine_home().resolve()
    return tuple((home / name).resolve() for name in _SAFE_MACHINE_DIRECTORY_NAMES)


def _machine_home() -> Path:
    try:
        return Path.home()
    except Exception:
        pass
    for key in ("HOME", "USERPROFILE"):
        value = str(os.environ.get(key) or "").strip()
        if value:
            return Path(value).expanduser()
    return Path("~").expanduser()


def _expand_machine_user(raw: str) -> Path:
    value = str(raw or "").strip()
    if value in {"~", "~/", "~\\"}:
        return _machine_home()
    if value.startswith("~/") or value.startswith("~\\"):
        return _machine_home() / value[2:]
    return Path(value).expanduser()


def _resolve_machine_directory(raw_path: str | None) -> Path:
    raw = str(raw_path or "").strip()
    if not raw:
        return _safe_machine_roots()[0]
    lowered = raw.lower().strip()
    alias_map = {
        "desktop": "Desktop",
        "my desktop": "Desktop",
        "~/desktop": "Desktop",
        "downloads": "Downloads",
        "my downloads": "Downloads",
        "~/downloads": "Downloads",
        "documents": "Documents",
        "my documents": "Documents",
        "~/documents": "Documents",
        "docs": "Documents",
    }
    if lowered in alias_map:
        return (_machine_home() / alias_map[lowered]).resolve()
    candidate = _expand_machine_user(raw)
    if not candidate.is_absolute():
        candidate = (_machine_home() / candidate).resolve()
    else:
        candidate = candidate.resolve()
    for root in _safe_machine_roots():
        if candidate == root or root in candidate.parents:
            return candidate
    allowed = ", ".join(_home_relative_label(root) for root in _safe_machine_roots())
    raise ValueError(f"I can only inspect safe local directories in this lane: {allowed}.")


def _runtime_session_id(source_context: dict[str, Any] | None) -> str:
    return str((source_context or {}).get("session_id") or "").strip()


def _result_from_payload(
    *,
    handled: bool = True,
    ok: bool,
    status: str,
    response_text: str,
    details: dict[str, Any],
) -> RuntimeExecutionResult:
    return RuntimeExecutionResult(
        handled=handled,
        ok=ok,
        status=status,
        response_text=response_text,
        details=details,
    )


def _iter_workspace_files(
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
        relative = _relative_path(path, workspace_root=workspace_root)
        if any(part.startswith(".") for part in Path(relative).parts):
            continue
        if glob_pattern not in {"", "*", "**", "**/*"} and not fnmatch.fnmatch(relative, glob_pattern) and not fnmatch.fnmatch(path.name, glob_pattern):
            continue
        matches.append(path)
    return matches


def _is_probably_text(path: Path) -> bool:
    try:
        sample = path.read_bytes()[:4096]
    except Exception:
        return False
    return b"\x00" not in sample


def _list_files(arguments: dict[str, Any], *, workspace_root: Path) -> RuntimeExecutionResult:
    target = _resolve_workspace_path(arguments.get("path"), workspace_root=workspace_root)
    limit = max(1, min(int(arguments.get("limit") or 50), 200))
    glob_pattern = str(arguments.get("glob") or "**/*").strip() or "**/*"
    if target.is_file():
        rows = [target]
    else:
        rows = _iter_workspace_files(target, workspace_root=workspace_root, glob_pattern=glob_pattern, limit=limit)
    if not rows:
        return RuntimeExecutionResult(
            handled=True,
            ok=True,
            status="no_results",
            response_text=f"No files matched inside `{_relative_path(target, workspace_root=workspace_root)}`.",
            details={
                "path": _relative_path(target, workspace_root=workspace_root),
                "observation": _tool_observation(
                    intent="workspace.list_files",
                    tool_surface="workspace",
                    ok=True,
                    status="no_results",
                    path=_relative_path(target, workspace_root=workspace_root),
                    paths=[],
                    count=0,
                ),
            },
        )
    relative_rows = [_relative_path(path, workspace_root=workspace_root) for path in rows[:limit]]
    lines = [f"Workspace files under `{_relative_path(target, workspace_root=workspace_root)}`:"]
    for relative in relative_rows:
        lines.append(f"- {relative}")
    return RuntimeExecutionResult(
        handled=True,
        ok=True,
        status="executed",
        response_text="\n".join(lines),
        details={
            "path": _relative_path(target, workspace_root=workspace_root),
            "count": len(relative_rows),
            "paths": relative_rows,
            "observation": _tool_observation(
                intent="workspace.list_files",
                tool_surface="workspace",
                ok=True,
                status="executed",
                path=_relative_path(target, workspace_root=workspace_root),
                count=len(relative_rows),
                paths=relative_rows,
            ),
        },
    )


def _list_machine_directory(arguments: dict[str, Any]) -> RuntimeExecutionResult:
    try:
        target = _resolve_machine_directory(arguments.get("path"))
    except ValueError:
        allowed_roots = [_home_relative_label(root) for root in _safe_machine_roots()]
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="not_allowed",
            response_text=f"I can only inspect safe local directories in this lane: {', '.join(allowed_roots)}.",
            details={
                "observation": _tool_observation(
                    intent="machine.list_directory",
                    tool_surface="machine",
                    ok=False,
                    status="not_allowed",
                    allowed_roots=allowed_roots,
                ),
            },
        )
    if not target.exists() or not target.is_dir():
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="not_found",
            response_text=f"Local directory `{_home_relative_label(target)}` does not exist.",
            details={
                "path": _home_relative_label(target),
                "observation": _tool_observation(
                    intent="machine.list_directory",
                    tool_surface="machine",
                    ok=False,
                    status="not_found",
                    path=_home_relative_label(target),
                ),
            },
        )
    directories_only = bool(arguments.get("directories_only", False))
    limit = max(1, min(int(arguments.get("limit") or 50), 200))
    entries: list[dict[str, Any]] = []
    for child in sorted(target.iterdir(), key=lambda item: item.name.lower()):
        if len(entries) >= limit:
            break
        if child.name.startswith("."):
            continue
        if directories_only and not child.is_dir():
            continue
        entries.append(
            {
                "name": child.name,
                "path": _home_relative_label(child),
                "type": "directory" if child.is_dir() else "file",
            }
        )
    if not entries:
        noun = "folders" if directories_only else "entries"
        return RuntimeExecutionResult(
            handled=True,
            ok=True,
            status="no_results",
            response_text=f"No visible {noun} matched in `{_home_relative_label(target)}`.",
            details={
                "path": _home_relative_label(target),
                "count": 0,
                "entries": [],
                "directories_only": directories_only,
                "observation": _tool_observation(
                    intent="machine.list_directory",
                    tool_surface="machine",
                    ok=True,
                    status="no_results",
                    path=_home_relative_label(target),
                    count=0,
                    directories_only=directories_only,
                    entries=[],
                ),
            },
        )
    label = "Visible folders" if directories_only else "Visible entries"
    lines = [f"{label} under `{_home_relative_label(target)}`:"]
    for entry in entries:
        suffix = "/" if entry["type"] == "directory" else ""
        lines.append(f"- {entry['name']}{suffix}")
    return RuntimeExecutionResult(
        handled=True,
        ok=True,
        status="executed",
        response_text="\n".join(lines),
        details={
            "path": _home_relative_label(target),
            "count": len(entries),
            "entries": entries,
            "directories_only": directories_only,
            "observation": _tool_observation(
                intent="machine.list_directory",
                tool_surface="machine",
                ok=True,
                status="executed",
                path=_home_relative_label(target),
                count=len(entries),
                directories_only=directories_only,
                entries=entries,
            ),
        },
    )


def _read_machine_file(arguments: dict[str, Any]) -> RuntimeExecutionResult:
    if not policy_engine.get("filesystem.allow_read_workspace", True):
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="disabled",
            response_text="Safe local file reads are disabled by policy.",
            details={
                "observation": _tool_observation(
                    intent="machine.read_file",
                    tool_surface="machine",
                    ok=False,
                    status="disabled",
                ),
            },
        )
    try:
        target = _resolve_machine_directory(arguments.get("path"))
    except ValueError:
        allowed_roots = [_home_relative_label(root) for root in _safe_machine_roots()]
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="not_allowed",
            response_text=(
                "I cannot read that path in this lane. "
                f"I can only read files inside: {', '.join(allowed_roots)}. "
                "For repo or project paths, use workspace.read_file against the active workspace root."
            ),
            details={
                "observation": _tool_observation(
                    intent="machine.read_file",
                    tool_surface="machine",
                    ok=False,
                    status="not_allowed",
                    allowed_roots=allowed_roots,
                ),
            },
        )
    if not target.exists() or not target.is_file():
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="not_found",
            response_text=f"Local file `{_home_relative_label(target)}` does not exist.",
            details={
                "path": _home_relative_label(target),
                "observation": _tool_observation(
                    intent="machine.read_file",
                    tool_surface="machine",
                    ok=False,
                    status="not_found",
                    path=_home_relative_label(target),
                ),
            },
        )
    if not _is_probably_text(target):
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="binary_file",
            response_text=f"Local file `{_home_relative_label(target)}` does not look like readable text.",
            details={
                "path": _home_relative_label(target),
                "observation": _tool_observation(
                    intent="machine.read_file",
                    tool_surface="machine",
                    ok=False,
                    status="binary_file",
                    path=_home_relative_label(target),
                ),
            },
        )
    start_line = max(1, int(arguments.get("start_line") or 1))
    max_lines = max(1, min(int(arguments.get("max_lines") or 160), 400))
    verbatim = bool(arguments.get("verbatim", False))
    content = target.read_text(encoding="utf-8", errors="replace").splitlines()
    chunk = content[start_line - 1 : start_line - 1 + max_lines]
    if not chunk:
        return RuntimeExecutionResult(
            handled=True,
            ok=True,
            status="empty_slice",
            response_text=f"Local file `{_home_relative_label(target)}` has no lines in that range.",
            details={
                "path": _home_relative_label(target),
                "start_line": start_line,
                "line_count": 0,
                "lines": [],
                "observation": _tool_observation(
                    intent="machine.read_file",
                    tool_surface="machine",
                    ok=True,
                    status="empty_slice",
                    path=_home_relative_label(target),
                    start_line=start_line,
                    line_count=0,
                    lines=[],
                    verbatim=verbatim,
                ),
            },
        )
    numbered = [f"{start_line + offset}: {line}" for offset, line in enumerate(chunk)]
    line_rows = [
        {"line_number": start_line + offset, "text": line}
        for offset, line in enumerate(chunk)
    ]
    rendered_body = "\n".join(chunk) if verbatim else "\n".join(numbered)
    response_text = (
        rendered_body
        if verbatim
        else f"Local file `{_home_relative_label(target)}`:\n" + rendered_body
    )
    return RuntimeExecutionResult(
        handled=True,
        ok=True,
        status="executed",
        response_text=response_text,
        details={
            "path": _home_relative_label(target),
            "start_line": start_line,
            "line_count": len(chunk),
            "lines": line_rows,
            "verbatim": verbatim,
            "observation": _tool_observation(
                intent="machine.read_file",
                tool_surface="machine",
                ok=True,
                status="executed",
                path=_home_relative_label(target),
                start_line=start_line,
                line_count=len(chunk),
                lines=line_rows,
                verbatim=verbatim,
            ),
        },
    )


def _machine_os_details() -> tuple[str, str]:
    system = str(platform.system() or "").strip()
    if system == "Darwin":
        version = str(platform.mac_ver()[0] or "").strip() or str(platform.release() or "").strip()
        return "macOS", version
    if system == "Windows":
        return "Windows", str(platform.version() or platform.release() or "").strip()
    if system:
        return system, str(platform.release() or "").strip()
    return "Unknown", ""


def _machine_chip_name() -> str:
    system = str(platform.system() or "").strip().lower()
    try:
        if system == "darwin":
            completed = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            brand = str(completed.stdout or "").strip()
            if brand:
                return brand
    except Exception:
        pass
    processor = str(platform.processor() or "").strip()
    if processor:
        return processor
    machine = str(platform.machine() or "").strip()
    if machine:
        return machine
    return "unknown"


def _inspect_machine_specs() -> RuntimeExecutionResult:
    probe = probe_machine()
    recommendation_summary = install_recommendation_machine_summary(probe=probe)
    recommended_model = str(recommendation_summary.get("ollama_model") or "").strip() or "qwen3:8b"
    recommended_bundle_models = tuple(
        str(item).strip()
        for item in recommendation_summary.get("recommended_bundle_models") or ()
        if str(item).strip()
    )
    selected_tier = str(recommendation_summary.get("selected_tier") or "").strip()
    capacity_bucket = str(recommendation_summary.get("capacity_bucket") or "").strip()
    os_name, os_version = _machine_os_details()
    chip_name = _machine_chip_name()
    display = _machine_display_details()
    response_lines = [
        "Machine specs for this host:",
        f"- OS: {os_name}{f' {os_version}' if os_version else ''}",
        f"- Chip: {chip_name}",
        f"- CPU cores: {probe.cpu_cores}",
        f"- RAM: {round(probe.ram_gb, 1)} GiB",
        f"- Accelerator: {probe.accelerator or 'cpu'}",
        f"- GPU: {probe.gpu_name or 'none'}",
    ]
    accelerator = str(probe.accelerator or "").strip().lower()
    accelerator_status = str(getattr(probe, "accelerator_status", "") or "").strip()
    if not accelerator_status:
        accelerator_status = "cpu" if accelerator == "cpu" else "usable"
    accelerator_advice = str(getattr(probe, "accelerator_advice", "") or "").strip()
    if accelerator_status and accelerator_status not in {"usable", "cpu"}:
        response_lines.append(f"- Accelerator status: {accelerator_status}")
    if accelerator_advice:
        response_lines.append(f"- Accelerator advice: {accelerator_advice}")
    if probe.vram_gb is not None:
        label = "Unified memory" if str(probe.accelerator or "").strip().lower() == "mps" else "VRAM"
        response_lines.append(f"- {label}: {round(probe.vram_gb, 1)} GiB")
    if str(display.get("name") or "").strip():
        response_lines.append(f"- Display: {display['name']}")
    if str(display.get("native_resolution") or "").strip():
        response_lines.append(f"- Native display resolution: {display['native_resolution']}")
    if str(display.get("current_resolution") or "").strip():
        response_lines.append(f"- Current display mode: {display['current_resolution']}")
    if str(display.get("screen_size") or "").strip():
        response_lines.append(f"- Screen size: {display['screen_size']}")
    response_lines.append(f"- Recommended local model: {recommended_model}")
    if recommended_bundle_models:
        response_lines.append(f"- Recommended local bundle: {', '.join(recommended_bundle_models)}")
    observation = _tool_observation(
        intent="machine.inspect_specs",
        tool_surface="machine",
        ok=True,
        status="executed",
        chip_name=chip_name,
        os_name=os_name,
        os_version=os_version,
        cpu_cores=probe.cpu_cores,
        ram_gb=round(probe.ram_gb, 1),
        gpu_name=probe.gpu_name or "",
        vram_gb=round(probe.vram_gb, 1) if probe.vram_gb is not None else None,
        accelerator=probe.accelerator,
        accelerator_status=accelerator_status,
        accelerator_advice=accelerator_advice,
        recommended_model=recommended_model,
        selected_tier=selected_tier,
        capacity_bucket=capacity_bucket,
        recommended_bundle_models=list(recommended_bundle_models),
        display_name=str(display.get("name") or "").strip(),
        display_native_resolution=str(display.get("native_resolution") or "").strip(),
        display_current_resolution=str(display.get("current_resolution") or "").strip(),
        screen_size=str(display.get("screen_size") or "").strip(),
    )
    return RuntimeExecutionResult(
        handled=True,
        ok=True,
        status="executed",
        response_text="\n".join(response_lines),
        details={
            "chip_name": chip_name,
            "os_name": os_name,
            "os_version": os_version,
            "cpu_cores": probe.cpu_cores,
            "ram_gb": round(probe.ram_gb, 1),
            "gpu_name": probe.gpu_name or "",
            "vram_gb": round(probe.vram_gb, 1) if probe.vram_gb is not None else None,
            "accelerator": probe.accelerator,
            "accelerator_status": accelerator_status,
            "accelerator_advice": accelerator_advice,
            "recommended_model": recommended_model,
            "selected_tier": selected_tier,
            "capacity_bucket": capacity_bucket,
            "recommended_bundle_models": list(recommended_bundle_models),
            "display_name": str(display.get("name") or "").strip(),
            "display_native_resolution": str(display.get("native_resolution") or "").strip(),
            "display_current_resolution": str(display.get("current_resolution") or "").strip(),
            "screen_size": str(display.get("screen_size") or "").strip(),
            "observation": observation,
        },
    )


def _machine_display_details() -> dict[str, str]:
    if str(platform.system() or "").strip().lower() != "darwin":
        return {}
    try:
        completed = subprocess.run(
            ["system_profiler", "SPDisplaysDataType", "-json"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        if int(completed.returncode or 0) != 0:
            return {}
        payload = json.loads(str(completed.stdout or "").strip() or "{}")
    except Exception:
        return {}
    for gpu in list(payload.get("SPDisplaysDataType") or []):
        for display in list((gpu or {}).get("spdisplays_ndrvs") or []):
            if not isinstance(display, dict):
                continue
            name = str(display.get("_name") or "").strip()
            native_resolution = str(
                display.get("_spdisplays_pixels")
                or display.get("spdisplays_pixelresolution")
                or ""
            ).strip()
            current_resolution = str(display.get("spdisplays_resolution") or "").strip()
            screen_size = ""
            if name.lower() == "imac" and native_resolution == "4480 x 2520":
                screen_size = "24-inch (inferred from Apple 4.5K iMac panel)"
            return {
                "name": name,
                "native_resolution": native_resolution,
                "current_resolution": current_resolution,
                "screen_size": screen_size,
            }
    return {}


def _ensure_machine_directory(arguments: dict[str, Any]) -> RuntimeExecutionResult:
    if not policy_engine.get("filesystem.allow_write_workspace", False):
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="disabled",
            response_text="Safe local machine writes are disabled by policy.",
            details={
                "observation": _tool_observation(
                    intent="machine.ensure_directory",
                    tool_surface="machine",
                    ok=False,
                    status="disabled",
                ),
            },
        )
    try:
        target = _resolve_machine_directory(arguments.get("path"))
    except ValueError as exc:
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="not_allowed",
            response_text=str(exc).replace("inspect safe local directories in", "write inside"),
            details={
                "observation": _tool_observation(
                    intent="machine.ensure_directory",
                    tool_surface="machine",
                    ok=False,
                    status="not_allowed",
                    allowed_roots=[_home_relative_label(root) for root in _safe_machine_roots()],
                ),
            },
        )
    already_present = target.exists()
    target.mkdir(parents=True, exist_ok=True)
    status = "already_exists" if already_present else "executed"
    action = "already_present" if already_present else "created"
    return RuntimeExecutionResult(
        handled=True,
        ok=True,
        status=status,
        response_text=(
            f"Directory `{_home_relative_label(target)}` already existed."
            if already_present
            else f"Created directory `{_home_relative_label(target)}`."
        ),
        details={
            "path": _home_relative_label(target),
            "action": action,
            "already_present": already_present,
            "observation": _tool_observation(
                intent="machine.ensure_directory",
                tool_surface="machine",
                ok=True,
                status=status,
                path=_home_relative_label(target),
                action=action,
                already_present=already_present,
            ),
        },
    )


def _write_machine_file(arguments: dict[str, Any]) -> RuntimeExecutionResult:
    if not policy_engine.get("filesystem.allow_write_workspace", False):
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="disabled",
            response_text="Safe local machine writes are disabled by policy.",
            details={
                "observation": _tool_observation(
                    intent="machine.write_file",
                    tool_surface="machine",
                    ok=False,
                    status="disabled",
                ),
            },
        )
    try:
        target = _resolve_machine_directory(arguments.get("path"))
    except ValueError as exc:
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="not_allowed",
            response_text=str(exc).replace("inspect safe local directories in", "write inside"),
            details={
                "observation": _tool_observation(
                    intent="machine.write_file",
                    tool_surface="machine",
                    ok=False,
                    status="not_allowed",
                    allowed_roots=[_home_relative_label(root) for root in _safe_machine_roots()],
                ),
            },
        )
    content = str(arguments.get("content") or "")
    target.parent.mkdir(parents=True, exist_ok=True)
    already_present = target.exists()
    target.write_text(content, encoding="utf-8")
    action = "updated" if already_present else "created"
    status = "updated" if already_present else "executed"
    line_count = len(content.splitlines()) if content else 0
    return RuntimeExecutionResult(
        handled=True,
        ok=True,
        status=status,
        response_text=(
            f"Updated file `{_home_relative_label(target)}`."
            if already_present
            else f"Created file `{_home_relative_label(target)}`."
        ),
        details={
            "path": _home_relative_label(target),
            "line_count": line_count,
            "action": action,
            "observation": _tool_observation(
                intent="machine.write_file",
                tool_surface="machine",
                ok=True,
                status=status,
                path=_home_relative_label(target),
                line_count=line_count,
                action=action,
            ),
        },
    )


def _list_tree(arguments: dict[str, Any], *, workspace_root: Path) -> RuntimeExecutionResult:
    payload = list_tree_workspace(arguments, workspace_root=workspace_root)
    details = dict(payload.get("details") or {})
    details["observation"] = _tool_observation(
        intent="workspace.list_tree",
        tool_surface="workspace",
        ok=bool(payload.get("ok")),
        status=str(payload.get("status") or ""),
        path=str(details.get("path") or "").strip(),
        entries=list(details.get("entries") or []),
        truncated=bool(details.get("truncated", False)),
    )
    return _result_from_payload(
        ok=bool(payload.get("ok")),
        status=str(payload.get("status") or ""),
        response_text=str(payload.get("response_text") or ""),
        details=details,
    )


def _search_text(arguments: dict[str, Any], *, workspace_root: Path) -> RuntimeExecutionResult:
    query = str(arguments.get("query") or "").strip()
    if not query:
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="invalid_arguments",
            response_text="workspace.search_text needs a non-empty `query`.",
            details={
                "observation": _tool_observation(
                    intent="workspace.search_text",
                    tool_surface="workspace",
                    ok=False,
                    status="invalid_arguments",
                ),
            },
        )
    target = _resolve_workspace_path(arguments.get("path"), workspace_root=workspace_root)
    limit = max(1, min(int(arguments.get("limit") or 20), 100))
    glob_pattern = str(arguments.get("glob") or "**/*").strip() or "**/*"
    matches: list[str] = []
    match_rows: list[dict[str, Any]] = []
    lowered = query.lower()
    for path in _iter_workspace_files(target, workspace_root=workspace_root, glob_pattern=glob_pattern, limit=500):
        if not _is_probably_text(path):
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        for index, line in enumerate(lines, start=1):
            if lowered not in line.lower():
                continue
            relative = _relative_path(path, workspace_root=workspace_root)
            snippet = line.strip()[:220]
            matches.append(f"- {relative}:{index} {snippet}")
            match_rows.append({"path": relative, "line": index, "snippet": snippet})
            if len(matches) >= limit:
                break
        if len(matches) >= limit:
            break
    if not matches:
        return RuntimeExecutionResult(
            handled=True,
            ok=True,
            status="no_results",
            response_text=f'No text matches for "{query}" were found in the workspace.',
            details={
                "query": query,
                "matches": [],
                "observation": _tool_observation(
                    intent="workspace.search_text",
                    tool_surface="workspace",
                    ok=True,
                    status="no_results",
                    query=query,
                    matches=[],
                    match_count=0,
                ),
            },
        )
    return RuntimeExecutionResult(
        handled=True,
        ok=True,
        status="executed",
        response_text=f'Search matches for "{query}":\n' + "\n".join(matches),
        details={
            "query": query,
            "match_count": len(match_rows),
            "matches": match_rows,
            "observation": _tool_observation(
                intent="workspace.search_text",
                tool_surface="workspace",
                ok=True,
                status="executed",
                query=query,
                match_count=len(match_rows),
                matches=match_rows,
            ),
        },
    )


def _symbol_search(arguments: dict[str, Any], *, workspace_root: Path) -> RuntimeExecutionResult:
    payload = symbol_search_workspace(arguments, workspace_root=workspace_root)
    details = dict(payload.get("details") or {})
    details["observation"] = _tool_observation(
        intent="workspace.symbol_search",
        tool_surface="workspace",
        ok=bool(payload.get("ok")),
        status=str(payload.get("status") or ""),
        symbol=str(details.get("symbol") or "").strip(),
        match_count=int(details.get("match_count") or len(list(details.get("matches") or []))),
        matches=list(details.get("matches") or []),
    )
    return _result_from_payload(
        ok=bool(payload.get("ok")),
        status=str(payload.get("status") or ""),
        response_text=str(payload.get("response_text") or ""),
        details=details,
    )


def _read_file(arguments: dict[str, Any], *, workspace_root: Path) -> RuntimeExecutionResult:
    target = _resolve_workspace_path(arguments.get("path"), workspace_root=workspace_root)
    if not target.exists() or not target.is_file():
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="not_found",
            response_text=f"File `{_relative_path(target, workspace_root=workspace_root)}` does not exist.",
            details={
                "path": _relative_path(target, workspace_root=workspace_root),
                "observation": _tool_observation(
                    intent="workspace.read_file",
                    tool_surface="workspace",
                    ok=False,
                    status="not_found",
                    path=_relative_path(target, workspace_root=workspace_root),
                ),
            },
        )
    if not _is_probably_text(target):
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="binary_file",
            response_text=f"File `{_relative_path(target, workspace_root=workspace_root)}` does not look like readable text.",
            details={
                "path": _relative_path(target, workspace_root=workspace_root),
                "observation": _tool_observation(
                    intent="workspace.read_file",
                    tool_surface="workspace",
                    ok=False,
                    status="binary_file",
                    path=_relative_path(target, workspace_root=workspace_root),
                ),
            },
        )
    start_line = max(1, int(arguments.get("start_line") or 1))
    max_lines = max(1, min(int(arguments.get("max_lines") or 160), 400))
    verbatim = bool(arguments.get("verbatim", False))
    content = target.read_text(encoding="utf-8", errors="replace").splitlines()
    chunk = content[start_line - 1 : start_line - 1 + max_lines]
    if not chunk:
        return RuntimeExecutionResult(
            handled=True,
            ok=True,
            status="empty_slice",
            response_text=f"File `{_relative_path(target, workspace_root=workspace_root)}` has no lines in that range.",
            details={
                "path": _relative_path(target, workspace_root=workspace_root),
                "start_line": start_line,
                "line_count": 0,
                "lines": [],
                "observation": _tool_observation(
                    intent="workspace.read_file",
                    tool_surface="workspace",
                    ok=True,
                    status="empty_slice",
                    path=_relative_path(target, workspace_root=workspace_root),
                    start_line=start_line,
                    line_count=0,
                    lines=[],
                ),
            },
        )
    numbered = [f"{start_line + offset}: {line}" for offset, line in enumerate(chunk)]
    line_rows = [
        {"line_number": start_line + offset, "text": line}
        for offset, line in enumerate(chunk)
    ]
    rendered_body = "\n".join(chunk) if verbatim else "\n".join(numbered)
    response_text = (
        rendered_body
        if verbatim
        else f"File `{_relative_path(target, workspace_root=workspace_root)}`:\n" + rendered_body
    )
    return RuntimeExecutionResult(
        handled=True,
        ok=True,
        status="executed",
        response_text=response_text,
        details={
            "path": _relative_path(target, workspace_root=workspace_root),
            "start_line": start_line,
            "line_count": len(chunk),
            "lines": line_rows,
            "verbatim": verbatim,
            "observation": _tool_observation(
                intent="workspace.read_file",
                tool_surface="workspace",
                ok=True,
                status="executed",
                path=_relative_path(target, workspace_root=workspace_root),
                start_line=start_line,
                line_count=len(chunk),
                lines=line_rows,
                verbatim=verbatim,
            ),
        },
    )


def _ensure_directory(arguments: dict[str, Any], *, workspace_root: Path) -> RuntimeExecutionResult:
    if not policy_engine.get("filesystem.allow_write_workspace", False):
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="disabled",
            response_text="Workspace writes are disabled by policy.",
            details={
                "observation": _tool_observation(
                    intent="workspace.ensure_directory",
                    tool_surface="workspace",
                    ok=False,
                    status="disabled",
                ),
            },
        )
    target = _resolve_workspace_path(arguments.get("path"), workspace_root=workspace_root)
    relative_path = _relative_path(target, workspace_root=workspace_root)
    already_present = target.exists()
    target.mkdir(parents=True, exist_ok=True)
    status = "already_exists" if already_present else "executed"
    action = "confirmed" if already_present else "created"
    return RuntimeExecutionResult(
        handled=True,
        ok=True,
        status=status,
        response_text=(
            f"Directory `{relative_path}` already existed."
            if already_present
            else f"Created directory `{relative_path}`."
        ),
        details={
            "path": relative_path,
            "action": action,
            "already_present": already_present,
            "observation": _tool_observation(
                intent="workspace.ensure_directory",
                tool_surface="workspace",
                ok=True,
                status=status,
                path=relative_path,
                action=action,
                already_present=already_present,
            ),
        },
    )


def _write_file(arguments: dict[str, Any], *, workspace_root: Path, session_id: str) -> RuntimeExecutionResult:
    if not policy_engine.get("filesystem.allow_write_workspace", False):
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="disabled",
            response_text="Workspace writes are disabled by policy.",
            details={
                "observation": _tool_observation(
                    intent="workspace.write_file",
                    tool_surface="workspace",
                    ok=False,
                    status="disabled",
                ),
            },
        )
    target = _resolve_workspace_path(arguments.get("path"), workspace_root=workspace_root)
    content = str(arguments.get("content") or "")
    target.parent.mkdir(parents=True, exist_ok=True)
    existed = target.exists()
    previous_mtime_ns = int(target.stat().st_mtime_ns) if existed and target.is_file() else 0
    previous = target.read_text(encoding="utf-8", errors="replace") if existed else ""
    target.write_text(content, encoding="utf-8")
    force_fresh_source_timestamp(target, minimum_mtime_ns=previous_mtime_ns)
    line_count = len(content.splitlines()) or (1 if content else 0)
    relative_path = _relative_path(target, workspace_root=workspace_root)
    diff_artifact = build_file_diff_artifact(
        path=relative_path,
        action="updated" if existed else "created",
        before=previous,
        after=content,
        extra={"line_count": line_count},
    )
    mutation_record = record_workspace_mutation(
        session_id=session_id,
        workspace_root=workspace_root,
        intent="workspace.write_file",
        changes=[
            {
                "path": relative_path,
                "action": "updated" if existed else "created",
                "existed_before": existed,
                "existed_after": True,
                "before_text": previous,
                "after_text": content,
            }
        ],
    )
    return RuntimeExecutionResult(
        handled=True,
        ok=True,
        status="executed",
        response_text=(
            f"{'Updated' if existed else 'Created'} file `{relative_path}` "
            f"with {line_count} lines."
        ),
        details={
            "path": relative_path,
            "line_count": line_count,
            "action": "updated" if existed else "created",
            "artifacts": [diff_artifact],
            "mutation_record": {
                "mutation_id": str(mutation_record.get("mutation_id") or "").strip(),
                "session_key": str(mutation_record.get("session_key") or "").strip(),
            },
            "observation": _tool_observation(
                intent="workspace.write_file",
                tool_surface="workspace",
                ok=True,
                status="executed",
                path=relative_path,
                line_count=line_count,
                action="updated" if existed else "created",
                diff_preview=str(diff_artifact.get("diff_preview") or ""),
            ),
        },
    )


def _replace_in_file(arguments: dict[str, Any], *, workspace_root: Path, session_id: str) -> RuntimeExecutionResult:
    if not policy_engine.get("filesystem.allow_write_workspace", False):
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="disabled",
            response_text="Workspace writes are disabled by policy.",
            details={
                "observation": _tool_observation(
                    intent="workspace.replace_in_file",
                    tool_surface="workspace",
                    ok=False,
                    status="disabled",
                ),
            },
        )
    target = _resolve_workspace_path(arguments.get("path"), workspace_root=workspace_root)
    if not target.exists() or not target.is_file():
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="not_found",
            response_text=f"File `{_relative_path(target, workspace_root=workspace_root)}` does not exist.",
            details={
                "path": _relative_path(target, workspace_root=workspace_root),
                "observation": _tool_observation(
                    intent="workspace.replace_in_file",
                    tool_surface="workspace",
                    ok=False,
                    status="not_found",
                    path=_relative_path(target, workspace_root=workspace_root),
                ),
            },
        )
    old_text = str(arguments.get("old_text") or "")
    new_text = str(arguments.get("new_text") or "")
    replace_all = bool(arguments.get("replace_all", False))
    if not old_text:
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="invalid_arguments",
            response_text="workspace.replace_in_file needs non-empty `old_text`.",
            details={
                "path": _relative_path(target, workspace_root=workspace_root),
                "observation": _tool_observation(
                    intent="workspace.replace_in_file",
                    tool_surface="workspace",
                    ok=False,
                    status="invalid_arguments",
                    path=_relative_path(target, workspace_root=workspace_root),
                ),
            },
        )
    content = target.read_text(encoding="utf-8", errors="replace")
    previous_mtime_ns = int(target.stat().st_mtime_ns)
    occurrences = content.count(old_text)
    if occurrences <= 0:
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="no_match",
            response_text=f"`old_text` was not found in `{_relative_path(target, workspace_root=workspace_root)}`.",
            details={
                "path": _relative_path(target, workspace_root=workspace_root),
                "replacements": 0,
                "observation": _tool_observation(
                    intent="workspace.replace_in_file",
                    tool_surface="workspace",
                    ok=False,
                    status="no_match",
                    path=_relative_path(target, workspace_root=workspace_root),
                    replacements=0,
                ),
            },
        )
    if replace_all:
        updated = content.replace(old_text, new_text)
        replaced = occurrences
    else:
        updated = content.replace(old_text, new_text, 1)
        replaced = 1
    target.write_text(updated, encoding="utf-8")
    force_fresh_source_timestamp(target, minimum_mtime_ns=previous_mtime_ns)
    relative_path = _relative_path(target, workspace_root=workspace_root)
    diff_artifact = build_file_diff_artifact(
        path=relative_path,
        action="replaced",
        before=content,
        after=updated,
        extra={
            "replacements": replaced,
            "old_text_preview": _truncate(old_text, limit=180),
            "new_text_preview": _truncate(new_text, limit=180),
        },
    )
    mutation_record = record_workspace_mutation(
        session_id=session_id,
        workspace_root=workspace_root,
        intent="workspace.replace_in_file",
        changes=[
            {
                "path": relative_path,
                "action": "replaced",
                "existed_before": True,
                "existed_after": True,
                "before_text": content,
                "after_text": updated,
            }
        ],
    )
    return RuntimeExecutionResult(
        handled=True,
        ok=True,
        status="executed",
        response_text=(
            f"Applied {replaced} replacement{'s' if replaced != 1 else ''} in "
            f"`{relative_path}`."
        ),
        details={
            "path": relative_path,
            "replacements": replaced,
            "artifacts": [diff_artifact],
            "mutation_record": {
                "mutation_id": str(mutation_record.get("mutation_id") or "").strip(),
                "session_key": str(mutation_record.get("session_key") or "").strip(),
            },
            "observation": _tool_observation(
                intent="workspace.replace_in_file",
                tool_surface="workspace",
                ok=True,
                status="executed",
                path=relative_path,
                replacements=replaced,
                diff_preview=str(diff_artifact.get("diff_preview") or ""),
            ),
        },
    )


def _apply_unified_diff(arguments: dict[str, Any], *, workspace_root: Path, session_id: str) -> RuntimeExecutionResult:
    if not policy_engine.get("filesystem.allow_write_workspace", False):
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="disabled",
            response_text="Workspace writes are disabled by policy.",
            details={
                "observation": _tool_observation(
                    intent="workspace.apply_unified_diff",
                    tool_surface="workspace",
                    ok=False,
                    status="disabled",
                ),
            },
        )
    payload = apply_unified_diff_workspace(arguments, workspace_root=workspace_root, session_id=session_id)
    details = dict(payload.get("details") or {})
    details["observation"] = _tool_observation(
        intent="workspace.apply_unified_diff",
        tool_surface="workspace",
        ok=bool(payload.get("ok")),
        status=str(payload.get("status") or ""),
        paths=list(details.get("paths") or []),
        engine=str(details.get("engine") or ""),
    )
    return _result_from_payload(
        ok=bool(payload.get("ok")),
        status=str(payload.get("status") or ""),
        response_text=str(payload.get("response_text") or ""),
        details=details,
    )


def _git_status(arguments: dict[str, Any], *, workspace_root: Path) -> RuntimeExecutionResult:
    payload = git_status_workspace(arguments, workspace_root=workspace_root)
    details = dict(payload.get("details") or {})
    details["observation"] = _tool_observation(
        intent="workspace.git_status",
        tool_surface="workspace",
        ok=bool(payload.get("ok")),
        status=str(payload.get("status") or ""),
        cwd=str(details.get("cwd") or ""),
        stdout=str(details.get("stdout") or ""),
        stderr=str(details.get("stderr") or ""),
        returncode=int(details.get("returncode") or 0),
    )
    return _result_from_payload(
        ok=bool(payload.get("ok")),
        status=str(payload.get("status") or ""),
        response_text=str(payload.get("response_text") or ""),
        details=details,
    )


def _git_diff(arguments: dict[str, Any], *, workspace_root: Path) -> RuntimeExecutionResult:
    payload = git_diff_workspace(arguments, workspace_root=workspace_root)
    details = dict(payload.get("details") or {})
    details["observation"] = _tool_observation(
        intent="workspace.git_diff",
        tool_surface="workspace",
        ok=bool(payload.get("ok")),
        status=str(payload.get("status") or ""),
        cwd=str(details.get("cwd") or ""),
        stdout=str(details.get("stdout") or ""),
        stderr=str(details.get("stderr") or ""),
        returncode=int(details.get("returncode") or 0),
    )
    return _result_from_payload(
        ok=bool(payload.get("ok")),
        status=str(payload.get("status") or ""),
        response_text=str(payload.get("response_text") or ""),
        details=details,
    )


def _git_summary(arguments: dict[str, Any], *, workspace_root: Path) -> RuntimeExecutionResult:
    payload = git_summary_workspace(arguments, workspace_root=workspace_root)
    details = dict(payload.get("details") or {})
    details["observation"] = _tool_observation(
        intent="workspace.git_summary",
        tool_surface="workspace",
        ok=bool(payload.get("ok")),
        status=str(payload.get("status") or ""),
        cwd=str(details.get("cwd") or ""),
        branch=str(details.get("branch") or ""),
        commit=str(details.get("commit") or ""),
        dirty=bool(details.get("dirty", False)),
        local_branch_count=int(details.get("local_branch_count") or 0),
        remote_branch_count=int(details.get("remote_branch_count") or 0),
        total_branch_count=int(details.get("total_branch_count") or 0),
        today_date=str(details.get("today_date") or ""),
        today_commit_count=int(details.get("today_commit_count") or 0),
        yesterday_date=str(details.get("yesterday_date") or ""),
        yesterday_commit_count=int(details.get("yesterday_commit_count") or 0),
        recent_commits=[dict(item) for item in list(details.get("recent_commits") or []) if isinstance(item, dict)],
        commit_count_scope=str(details.get("commit_count_scope") or ""),
        timezone_label=str(details.get("timezone_label") or ""),
    )
    return _result_from_payload(
        ok=bool(payload.get("ok")),
        status=str(payload.get("status") or ""),
        response_text=str(payload.get("response_text") or ""),
        details=details,
    )


def _rollback_last_change(arguments: dict[str, Any], *, workspace_root: Path, session_id: str) -> RuntimeExecutionResult:
    del arguments
    rollback = rollback_last_workspace_mutation(session_id=session_id, workspace_root=workspace_root)
    if rollback is None:
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="no_tracked_change",
            response_text="There is no NULLA-tracked workspace mutation to roll back in this session.",
            details={
                "restored_paths": [],
                "removed_paths": [],
                "observation": _tool_observation(
                    intent="workspace.rollback_last_change",
                    tool_surface="workspace",
                    ok=False,
                    status="no_tracked_change",
                    restored_paths=[],
                    removed_paths=[],
                ),
            },
        )
    restored_paths = [str(item).strip() for item in list(rollback.get("restored_paths") or []) if str(item).strip()]
    removed_paths = [str(item).strip() for item in list(rollback.get("removed_paths") or []) if str(item).strip()]
    lines = ["Rolled back the last NULLA-tracked workspace change."]
    for path in restored_paths:
        lines.append(f"- restored `{path}`")
    for path in removed_paths:
        lines.append(f"- removed `{path}`")
    return RuntimeExecutionResult(
        handled=True,
        ok=True,
        status="executed",
        response_text="\n".join(lines),
        details={
            "restored_paths": restored_paths,
            "removed_paths": removed_paths,
            "mutation_id": str(rollback.get("mutation_id") or "").strip(),
            "observation": _tool_observation(
                intent="workspace.rollback_last_change",
                tool_surface="workspace",
                ok=True,
                status="executed",
                restored_paths=restored_paths,
                removed_paths=removed_paths,
            ),
        },
    )


def _run_validation(intent: str, arguments: dict[str, Any], *, workspace_root: Path) -> RuntimeExecutionResult:
    command = runtime_validation_command(validation_command(intent, arguments))
    command_arguments = dict(arguments)
    command_arguments["command"] = command
    command_arguments["_trusted_local_only"] = True
    runtime_result = _run_command(command_arguments, workspace_root=workspace_root)
    payload = render_validation_result(
        intent,
        command=command,
        cwd=str(runtime_result.details.get("cwd") or "."),
        runner_result={
            "status": runtime_result.status,
            "stdout": runtime_result.details.get("stdout"),
            "stderr": runtime_result.details.get("stderr"),
            "returncode": runtime_result.details.get("returncode"),
            "success": runtime_result.details.get("success"),
        },
        label={
            "workspace.run_tests": "Validation test run",
            "workspace.run_lint": "Validation lint run",
            "workspace.run_formatter": "Validation formatter run",
        }[intent],
    )
    details = dict(payload.get("details") or {})
    followup = _extract_command_failure_followup(
        stdout=str(details.get("stdout") or ""),
        stderr=str(details.get("stderr") or ""),
    )
    details["error_path"] = str(followup.get("error_path") or "").strip()
    details["error_line"] = int(followup.get("error_line") or 0)
    details["diagnostic_query"] = str(followup.get("diagnostic_query") or "").strip()
    details["observation"] = _tool_observation(
        intent=intent,
        tool_surface="workspace",
        ok=bool(payload.get("ok")),
        status=str(payload.get("status") or ""),
        command=str(details.get("command") or ""),
        cwd=str(details.get("cwd") or ""),
        returncode=int(details.get("returncode") or 0),
        success=bool(details.get("success", False)),
        failure_summary=str(details.get("failure_summary") or ""),
        error_path=str(details.get("error_path") or ""),
        error_line=int(details.get("error_line") or 0),
        diagnostic_query=str(details.get("diagnostic_query") or ""),
    )
    return _result_from_payload(
        ok=bool(payload.get("ok")),
        status=str(payload.get("status") or ""),
        response_text=str(payload.get("response_text") or ""),
        details=details,
    )


def _execute_task_envelope_intent(
    arguments: dict[str, Any],
    *,
    workspace_root: Path,
    session_id: str,
    source_context: dict[str, Any] | None,
) -> RuntimeExecutionResult:
    payload = arguments.get("task_envelope")
    if not isinstance(payload, dict):
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="invalid_arguments",
            response_text="orchestration.execute_envelope requires a `task_envelope` object.",
            details={
                "observation": _tool_observation(
                    intent="orchestration.execute_envelope",
                    tool_surface="orchestration",
                    ok=False,
                    status="invalid_arguments",
                ),
            },
        )
    from core.orchestration import execute_task_envelope, task_envelope_from_dict

    try:
        envelope = task_envelope_from_dict(payload)
    except Exception as exc:
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="invalid_arguments",
            response_text=f"Invalid task envelope payload: {exc}",
            details={
                "error": str(exc),
                "observation": _tool_observation(
                    intent="orchestration.execute_envelope",
                    tool_surface="orchestration",
                    ok=False,
                    status="invalid_arguments",
                ),
            },
        )
    envelope_result = execute_task_envelope(
        envelope,
        workspace_root=str(workspace_root),
        session_id=session_id or envelope.task_id,
        source_context=source_context,
    )
    details = {
        "task_envelope": envelope.to_dict(),
        "envelope_result": envelope_result.merge_payload(),
        "receipts": [dict(item) for item in envelope_result.receipts],
        "graph": list(envelope_result.details.get("graph") or []),
        "scheduled_children": list(envelope_result.details.get("scheduled_children") or []),
        "merged_result": dict(envelope_result.details.get("merged_result") or {}),
        "step_results": list(envelope_result.details.get("step_results") or []),
        "observation": _tool_observation(
            intent="orchestration.execute_envelope",
            tool_surface="orchestration",
            ok=envelope_result.ok,
            status=envelope_result.status,
            task_id=envelope.task_id,
            task_role=envelope.role,
            receipt_count=len(envelope_result.receipts),
            child_count=len(list(envelope.inputs.get("subtasks") or [])),
            merged_strategy=envelope.merge_strategy if envelope.role == "queen" else "",
        ),
    }
    return RuntimeExecutionResult(
        handled=True,
        ok=envelope_result.ok,
        status=envelope_result.status,
        response_text=envelope_result.output_text,
        details=details,
    )


def _attach_procedure_learning(
    result: RuntimeExecutionResult,
    *,
    validation_intent: str,
    workspace_root: Path,
    source_context: dict[str, Any] | None,
) -> RuntimeExecutionResult:
    if not result.ok:
        return result
    session_id = _runtime_session_id(source_context)
    mutation = latest_workspace_mutation(
        session_id=session_id,
        workspace_root=workspace_root,
        require_unpromoted=True,
    )
    if not mutation:
        return result
    changed_paths = [str(item.get("path") or "").strip() for item in list(mutation.get("changes") or []) if str(item.get("path") or "").strip()]
    validation_details = dict(result.details or {})
    task_envelope = dict((source_context or {}).get("task_envelope") or {})
    envelope_inputs = dict(task_envelope.get("inputs") or {})
    task_class = str(
        (source_context or {}).get("task_class")
        or (source_context or {}).get("execution_task_class")
        or envelope_inputs.get("task_class")
        or "coding_operator"
    ).strip() or "coding_operator"
    shard = promote_verified_procedure(
        task_class=task_class,
        title=_procedure_title(task_class=task_class, validation_intent=validation_intent, changed_paths=changed_paths),
        preconditions=[
            "workspace is writable",
            "NULLA tracked a mutation in the active session",
        ],
        steps=[
            _procedure_step_for_intent(str(mutation.get("intent") or "").strip()),
            _procedure_step_for_intent(validation_intent),
        ],
        tool_receipts=[
            {
                "intent": str(mutation.get("intent") or "").strip(),
                "mutation_id": str(mutation.get("mutation_id") or "").strip(),
                "paths": changed_paths,
            },
            {
                "intent": validation_intent,
                "command": str(validation_details.get("command") or "").strip(),
                "returncode": int(validation_details.get("returncode") or 0),
            },
        ],
        validation={
            "ok": True,
            "tool": validation_intent,
            "command": str(validation_details.get("command") or "").strip(),
            "returncode": int(validation_details.get("returncode") or 0),
        },
        rollback={
            "intent": "workspace.rollback_last_change",
            "mutation_id": str(mutation.get("mutation_id") or "").strip(),
        },
        privacy_class="local_private",
        shareability="local_only",
        liquefy_bundle_ref=str((source_context or {}).get("liquefy_bundle_ref") or "").strip(),
    )
    if shard is None:
        return result
    mark_workspace_mutation_promoted(
        session_id=session_id,
        workspace_root=workspace_root,
        mutation_id=str(mutation.get("mutation_id") or "").strip(),
        procedure_id=shard.procedure_id,
    )
    result.details["procedure_shard"] = shard.to_dict()
    result.details["procedure_reuse"] = {
        "procedure_id": shard.procedure_id,
        "title": shard.title,
        "task_class": shard.task_class,
    }
    return result


def _procedure_title(*, task_class: str, validation_intent: str, changed_paths: list[str]) -> str:
    if changed_paths:
        return f"{task_class}: validate {', '.join(changed_paths[:2])} with {validation_intent}"
    return f"{task_class}: verify workspace mutation with {validation_intent}"


def _procedure_step_for_intent(intent: str) -> str:
    mapping = {
        "workspace.write_file": "write the target file",
        "workspace.replace_in_file": "replace the targeted text in place",
        "workspace.apply_unified_diff": "apply the code patch as a unified diff",
        "workspace.run_tests": "run the bounded test command",
        "workspace.run_lint": "run the linter for the workspace",
        "workspace.run_formatter": "run the formatter check for the workspace",
    }
    return mapping.get(str(intent or "").strip(), str(intent or "").strip() or "run the verified workflow step")


def _run_command(arguments: dict[str, Any], *, workspace_root: Path) -> RuntimeExecutionResult:
    command = str(arguments.get("command") or arguments.get("cmd") or "").strip()
    if not command:
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="invalid_arguments",
            response_text="sandbox.run_command needs a non-empty `command`.",
            details={
                "observation": _tool_observation(
                    intent="sandbox.run_command",
                    tool_surface="sandbox",
                    ok=False,
                    status="invalid_arguments",
                ),
            },
        )
    raw_cwd = arguments.get("cwd")
    cwd = _resolve_workspace_path(raw_cwd, workspace_root=workspace_root) if raw_cwd else workspace_root
    if cwd.is_file():
        cwd = cwd.parent
    runner = SandboxRunner(
        ExecutionGate(),
        str(workspace_root),
        network_isolation_mode=_trusted_local_network_mode(command, arguments=arguments),
    )
    result = runner.run_command(command, cwd=str(cwd))
    relative_cwd = _relative_path(cwd, workspace_root=workspace_root)
    status = str(result.get("status") or ("blocked_by_policy" if result.get("error") else ""))
    if status and status != "executed":
        stdout = _truncate(str(result.get("stdout") or ""), limit=2400)
        stderr = _truncate(str(result.get("stderr") or ""), limit=1600)
        command_artifact = build_command_artifact(
            command=command,
            cwd=relative_cwd,
            returncode=int(result.get("returncode", 0) or 0),
            stdout=stdout,
            stderr=stderr,
            status=status,
        )
        failure_summary = _extract_failure_summary(
            command=command,
            stdout=stdout,
            stderr=stderr,
            returncode=int(result.get("returncode", 0) or 0),
        )
        failure_artifacts = []
        if failure_summary:
            failure_artifacts.append(
                build_failure_artifact(
                    command=command,
                    cwd=relative_cwd,
                    returncode=int(result.get("returncode", 0) or 0),
                    stdout=stdout,
                    stderr=stderr,
                    summary=failure_summary,
                )
            )
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status=status,
            response_text=str(result.get("error") or f"Command could not run: {status}"),
            details={
                **dict(result),
                "artifacts": [command_artifact, *failure_artifacts],
                "observation": _tool_observation(
                    intent="sandbox.run_command",
                    tool_surface="sandbox",
                    ok=False,
                    status=status,
                    command=command,
                    cwd=relative_cwd,
                    returncode=int(result.get("returncode", 0) or 0),
                    stdout=stdout,
                    stderr=stderr,
                    error=str(result.get("error") or ""),
                    failure_summary=failure_summary,
                ),
            },
        )
    stdout = _truncate(str(result.get("stdout") or ""), limit=2400)
    stderr = _truncate(str(result.get("stderr") or ""), limit=1600)
    command_artifact = build_command_artifact(
        command=command,
        cwd=relative_cwd,
        returncode=int(result.get("returncode", 0) or 0),
        stdout=stdout,
        stderr=stderr,
        status="executed",
    )
    failure_summary = _extract_failure_summary(
        command=command,
        stdout=stdout,
        stderr=stderr,
        returncode=int(result.get("returncode", 0) or 0),
    )
    failure_artifacts = []
    if failure_summary:
        failure_artifacts.append(
            build_failure_artifact(
                command=command,
                cwd=relative_cwd,
                returncode=int(result.get("returncode", 0) or 0),
                stdout=stdout,
                stderr=stderr,
                summary=failure_summary,
            )
        )
    lines = [
        f"Command executed in `{relative_cwd}`:",
        f"$ {command}",
        f"- Exit code: {int(result.get('returncode', 0) or 0)}",
    ]
    if stdout:
        lines.append(f"- Stdout:\n{stdout}")
    if stderr:
        lines.append(f"- Stderr:\n{stderr}")
    return RuntimeExecutionResult(
        handled=True,
        ok=True,
        status="executed",
        response_text="\n".join(lines),
        details={
            "command": command,
            "cwd": relative_cwd,
            "returncode": int(result.get("returncode", 0) or 0),
            "success": bool(result.get("success", False)),
            "stdout": stdout,
            "stderr": stderr,
            "artifacts": [command_artifact, *failure_artifacts],
            "observation": _tool_observation(
                intent="sandbox.run_command",
                tool_surface="sandbox",
                ok=True,
                status="executed",
                command=command,
                cwd=relative_cwd,
                returncode=int(result.get("returncode", 0) or 0),
                success=bool(result.get("success", False)),
                stdout=stdout,
                stderr=stderr,
                failure_summary=failure_summary,
            ),
        },
    )


def _trusted_local_network_mode(command: str, *, arguments: dict[str, Any]) -> str | None:
    if not bool(arguments.get("_trusted_local_only", False)):
        return None
    argv = parse_command(command)
    if not argv:
        return None
    base = Path(str(argv[0] or "")).name.lower()
    if os.name == "nt" and base.endswith(".exe"):
        base = base[:-4]
    if base in {"pytest", "ruff"}:
        return "heuristic_only"
    if base not in {"python", "python3"} or len(argv) < 3:
        return None
    if argv[1] != "-m":
        return None
    module = str(argv[2] or "").lower()
    if module in {"pytest", "ruff"}:
        return "heuristic_only"
    if len(argv) >= 4 and argv[1:4] == ["-m", "compileall", "-q"]:
        return "heuristic_only"
    return None
