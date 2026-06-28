from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from core.runtime_execution_tools import (
    extract_observation_followup_hints,
    looks_like_advice_only_execution_prompt,
    looks_like_execution_request,
)
from core.task_router import (
    build_task_envelope_for_request,
    looks_like_bounded_repo_repair_request,
    looks_like_explicit_lookup_request,
    looks_like_live_recency_lookup,
    looks_like_public_entity_lookup_request,
)

from .constants import (
    _APPEND_CONTENT_ONLY_RE,
    _APPEND_FILE_RE,
    _BUILDER_RESEARCH_MARKERS,
    _CREATE_EXACT_FILES_RE,
    _CREATE_NAMED_FILE_WITH_CONTENT_RE,
    _CREATE_PATH_RE,
    _DIRECTORY_CREATE_MARKERS,
    _ENTITY_LOOKUP_DROP_TOKENS,
    _ENTITY_LOOKUP_KEEP_SHORT_TOKENS,
    _EXACT_READBACK_RE,
    _EXPLICIT_WORKSPACE_READ_RE,
    _FILE_IN_FOLDER_SAYING_RE,
    _FOLDER_FIRST_CREATE_FILE_RE,
    _FOLDER_PATH_RE,
    _GENERIC_HIVE_TITLE_MARKERS,
    _HIVE_ACTION_PATTERNS,
    _HIVE_CREATE_PREFIXES,
    _IN_WORKSPACE_CREATE_FILE_RE,
    _INLINE_CREATE_FILE_RE,
    _INTEGRATION_DOMAIN_MARKERS,
    _INTO_PATH_RE,
    _LIVE_LOOKUP_MARKERS,
    _LOCAL_TOOL_MARKERS,
    _NAMED_PATH_RE,
    _OVERWRITE_FILE_RE,
    _PATH_STOP_WORDS,
    _PLAIN_CREATE_FILE_WITH_CONTENT_RE,
    _START_CODE_MARKERS,
    _TOOL_INVENTORY_MARKERS,
    _URL_RE,
    _VERB_NAME_FOLDER_RE,
)
from .models import WorkflowPlannerDecision

_WINDOWS_PARENT_DIRECTORY_RE = re.compile(
    r"\b(?:in|under|inside)\s+[`\"']?(?P<path>[A-Za-z]:[^\r\n]+?)(?=\s+(?:create|make|write|add|put|place|save|read|list|with)\b|$)",
    re.IGNORECASE,
)


def should_attempt_tool_intent(
    user_text: str,
    *,
    task_class: str,
    source_context: dict[str, Any] | None = None,
) -> bool:
    source_context = dict(source_context or {})
    surface = str(source_context.get("surface") or "").strip().lower()
    platform = str(source_context.get("platform") or "").strip().lower()
    tool_capable_surfaces = {"channel", "openclaw", "api", "cli", "terminal", "local", ""}
    tool_capable_platforms = {"openclaw", "telegram", "discord", "local", "cli", ""}
    if surface not in tool_capable_surfaces and platform not in tool_capable_platforms:
        return False

    text = str(user_text or "").strip()
    lowered = text.lower()
    if not lowered:
        return False
    if looks_like_advice_only_execution_prompt(lowered):
        return False
    if (
        task_class in {"integration_orchestration", "system_design", "research"}
        and any(marker in lowered for marker in _BUILDER_RESEARCH_MARKERS)
        and any(marker in lowered for marker in _INTEGRATION_DOMAIN_MARKERS)
    ):
        return False
    if task_class == "integration_orchestration":
        return True
    if _URL_RE.search(text):
        return True
    if looks_like_explicit_lookup_request(text) or looks_like_public_entity_lookup_request(text):
        return True
    if looks_like_live_recency_lookup(text):
        return True
    if any(marker in lowered for marker in _LIVE_LOOKUP_MARKERS):
        return True
    if any(marker in lowered for marker in _LOCAL_TOOL_MARKERS):
        return True
    if looks_like_execution_request(text, task_class=task_class):
        return True
    if any(marker in lowered for marker in _HIVE_ACTION_PATTERNS):
        return True
    if "hive" in lowered and any(word in lowered for word in ("claim", "topic", "progress", "result", "task")):
        return True
    padded = f" {lowered} "
    if any(
        marker in padded
        for marker in (
            " proceed ", " do it ", " do all ", " go ahead ", " carry on ",
            " start working ", " continue ", " yes proceed ", " yes do it ",
            " yes go ahead ", " yes continue ", " deliver it ", " submit it ",
            " execute ", " run it ", " just do it ",
        )
    ):
        return True
    compact = lowered.strip(" \t\n\r?!.,")
    return compact in {
        "proceed",
        "do it",
        "do all",
        "go ahead",
        "carry on",
        "continue",
        "start working",
        "yes",
        "yes proceed",
        "yes do it",
        "ok do it",
        "ok proceed",
        "ok go ahead",
        "deliver it",
        "submit it",
        "execute",
        "run it",
        "just do it",
        "yes pls",
        "yes please",
        "all good carry on",
        "proceed with next steps",
        "proceed with that",
    }


def _looks_like_followup_resume_request(text: str) -> bool:
    lowered = " ".join(str(text or "").strip().lower().split())
    if not lowered:
        return False
    padded = f" {lowered} "
    if any(
        marker in padded
        for marker in (
            " proceed ",
            " do it ",
            " do all ",
            " go ahead ",
            " carry on ",
            " continue ",
            " start working ",
            " yes proceed ",
            " yes do it ",
            " yes continue ",
            " execute ",
            " run it ",
            " just do it ",
        )
    ):
        return True
    compact = lowered.strip(" \t\n\r?!.,")
    return compact in {
        "proceed",
        "do it",
        "do all",
        "go ahead",
        "carry on",
        "continue",
        "start working",
        "yes",
        "yes proceed",
        "yes do it",
        "ok do it",
        "ok proceed",
        "ok go ahead",
        "deliver it",
        "submit it",
        "execute",
        "run it",
        "just do it",
        "yes pls",
        "yes please",
        "all good carry on",
        "proceed with next steps",
        "proceed with that",
    }


def _entity_lookup_query_variants(text: str) -> tuple[str, str]:
    normalized = " ".join(str(text or "").strip().lower().split())
    if not normalized:
        return "", ""

    tokens: list[str] = []
    for token in re.findall(r"[a-z0-9\.]+", normalized):
        clean = token.strip(".")
        if not clean or clean in _ENTITY_LOOKUP_DROP_TOKENS:
            continue
        if clean == "x.com":
            clean = "x"
        if len(clean) == 1 and clean not in _ENTITY_LOOKUP_KEEP_SHORT_TOKENS:
            continue
        tokens.append(clean)

    if not tokens:
        return normalized, normalized

    primary_tokens = list(dict.fromkeys(tokens))[:6]
    retry_tokens = [
        re.sub(r"(.)\1+", r"\1", token) if len(token) >= 3 and token not in {"solana", "twitter"} else token
        for token in primary_tokens
    ]
    retry_tokens = list(dict.fromkeys(token for token in retry_tokens if token))
    if retry_tokens == primary_tokens:
        if "x" in retry_tokens and "twitter" not in retry_tokens:
            retry_tokens.append("twitter")
        elif "profile" not in retry_tokens:
            retry_tokens.append("profile")

    primary_query = " ".join(primary_tokens).strip() or normalized
    retry_query = " ".join(retry_tokens).strip() or primary_query
    return primary_query, retry_query


def _looks_like_tool_inventory_request(text: str) -> bool:
    lowered = " ".join(str(text or "").strip().lower().split())
    if not lowered:
        return False
    return any(marker in lowered for marker in _TOOL_INVENTORY_MARKERS)


def _planned_git_payload(text: str) -> tuple[str, dict[str, Any]] | None:
    lowered = f" {' '.join(str(text or '').strip().lower().split())} "
    if not lowered.strip():
        return None
    if any(
        marker in lowered
        for marker in (" create ", " write ", " edit ", " change ", " patch ", " delete ", " remove ", " push ", " pull ", " merge ", " checkout ", " rebase ")
    ):
        if not any(marker in lowered for marker in (" git status ", " git diff ")):
            return None
    mentions_repo = any(marker in lowered for marker in (" git ", " repo ", " repository ", " branch ", " branches ", " commit ", " commits "))
    if not mentions_repo:
        return None
    if any(marker in lowered for marker in (" git diff ", " show diff ", " unstaged diff ", " staged diff ", " git patch ")):
        return ("planned_workspace_git_diff", {"intent": "workspace.git_diff", "arguments": {}})
    if any(marker in lowered for marker in (" git status ", " working tree ", " dirty ", " clean repo ", " clean working tree ", " untracked ", " staged ", " unstaged ")):
        return ("planned_workspace_git_status", {"intent": "workspace.git_status", "arguments": {}})
    recent_commits_match = re.search(r"\b(?:last|recent)\s+(?P<count>\d+)\s+commits?\b", lowered)
    if recent_commits_match is not None:
        try:
            recent_limit = max(1, min(int(recent_commits_match.group("count") or "3"), 10))
        except Exception:
            recent_limit = 3
        return ("planned_workspace_git_summary", {"intent": "workspace.git_summary", "arguments": {"recent_limit": recent_limit}})
    if any(
        marker in lowered
        for marker in (
            " how many branches ",
            " how many commits ",
            " branch count ",
            " commit count ",
            " commits today ",
            " commits yesterday ",
            " branch inventory ",
            " recent commits ",
            " git summary ",
            " git activity ",
            " current branch ",
            " head commit ",
        )
    ):
        return ("planned_workspace_git_summary", {"intent": "workspace.git_summary", "arguments": {}})
    if " what branch and commit " in lowered or (
        " branch " in lowered
        and " commit " in lowered
        and any(marker in lowered for marker in (" right now ", " running on ", " current ", " are you on "))
    ):
        return ("planned_workspace_git_summary", {"intent": "workspace.git_summary", "arguments": {}})
    return None


def _planned_workspace_search_payload(text: str) -> tuple[str, dict[str, Any]] | None:
    normalized = " ".join(str(text or "").strip().split())
    if not normalized:
        return None
    patterns = (
        re.compile(
            r"\b(?:find|search|look for)\s+(?:a\s+)?file(?:s)?\s+(?:in|inside|under)\s+(?:this\s+)?(?:workspace|repo(?:sitory)?|project)\s+(?:mentioning|containing|matching)\s+[`\"']?(?P<query>.+?)[`\"']?$",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bsearch\s+(?:this\s+)?(?:workspace|repo(?:sitory)?|project)\s+for\s+[`\"']?(?P<query>.+?)[`\"']?$",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:look\s+through|scan)\s+(?:the\s+)?(?:workspace|repo(?:sitory)?|project)\s+and\s+find\s+[`\"']?(?P<query>.+?)[`\"']?$",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bshow\s+me\s+(?:a\s+)?file(?:s)?\s+(?:in|inside|under)\s+(?:this\s+)?(?:workspace|repo(?:sitory)?|project)\s+(?:that\s+mentions?|matching|containing)\s+[`\"']?(?P<query>.+?)[`\"']?$",
            re.IGNORECASE,
        ),
    )
    for pattern in patterns:
        match = pattern.search(normalized)
        if not match:
            continue
        query = str(match.group("query") or "").strip().strip("`\"'")
        if not query:
            continue
        return (
            "planned_workspace_search_text",
            {"intent": "workspace.search_text", "arguments": {"query": query, "limit": 10}},
        )
    return None


def _clean_workspace_path(candidate: str) -> str:
    clean = str(candidate or "").strip().strip("`\"'").strip().rstrip(".,!?")
    if not clean:
        return ""
    if clean.lower() in _PATH_STOP_WORDS:
        return ""
    if clean.startswith("/"):
        clean = clean.lstrip("/")
    clean = clean.lstrip("./")
    if not clean or clean.lower() in _PATH_STOP_WORDS:
        return ""
    if ".." in clean.split("/"):
        return ""
    return clean


def _clean_workspace_directory_path(candidate: str, *, workspace_root: str = "") -> str:
    raw = str(candidate or "").strip().strip("`\"'")
    clean = ""
    if raw and workspace_root:
        try:
            resolved_workspace_root = Path(workspace_root).expanduser().resolve()
            resolved_candidate = Path(raw).expanduser().resolve()
            if resolved_candidate == resolved_workspace_root:
                return ""
            if resolved_workspace_root in resolved_candidate.parents:
                clean = resolved_candidate.relative_to(resolved_workspace_root).as_posix()
        except Exception:
            clean = ""
    if not clean:
        clean = _clean_workspace_path(candidate)
    if clean in {"", "."}:
        return ""
    return clean.rstrip("/")


def _extract_workspace_bootstrap_path(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    lowered = " ".join(raw.lower().split())
    file_like_prompt = (
        any(marker in lowered for marker in (" file ", " files ", " text file ", ".txt", ".md", ".json", ".yaml", ".yml", ".toml", ".py", ".ts", ".js"))
        and not any(marker in lowered for marker in _START_CODE_MARKERS)
    )
    patterns = (
        (_NAMED_PATH_RE, _VERB_NAME_FOLDER_RE, _INTO_PATH_RE)
        if file_like_prompt
        else (_NAMED_PATH_RE, _VERB_NAME_FOLDER_RE, _FOLDER_PATH_RE, _CREATE_PATH_RE, _INTO_PATH_RE)
    )
    for pattern in patterns:
        match = pattern.search(raw)
        if not match:
            continue
        clean = _clean_workspace_path(match.group("path"))
        if clean:
            return clean
    return ""


def _extract_workspace_parent_directory(text: str, *, workspace_root: str = "") -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    match = _WINDOWS_PARENT_DIRECTORY_RE.search(raw) or _INTO_PATH_RE.search(raw)
    if not match:
        return ""
    return _clean_workspace_directory_path(
        str(match.group("path") or "").strip(),
        workspace_root=workspace_root,
    )


def _extract_safe_machine_directory_listing(text: str) -> dict[str, Any] | None:
    lowered = " ".join(str(text or "").strip().lower().split())
    if not lowered:
        return None
    if any(marker in lowered for marker in ("create ", "make ", "mkdir", "write ", "edit ", "change ", "delete ")):
        return None
    wants_listing = any(
        marker in lowered
        for marker in (
            "list ",
            "show ",
            "what are ",
            "what's on",
            "what is on",
            "contents of",
            "what do we have on",
            "tell me what",
            "can you see",
        )
    )
    if not wants_listing:
        return None
    path = ""
    if "desktop" in lowered:
        path = "~/Desktop"
    elif "downloads" in lowered:
        path = "~/Downloads"
    elif "documents" in lowered or "docs" in lowered:
        path = "~/Documents"
    if not path:
        return None
    mentions_directory_words = any(token in lowered for token in (" folder", " folders", " directory", " directories"))
    mentions_files = any(token in lowered for token in (" file", " files", " entry", " entries", " contents"))
    directories_only = mentions_directory_words and not mentions_files
    return {"path": path, "directories_only": directories_only, "limit": 200}


def _extract_safe_machine_directory_create(text: str) -> dict[str, Any] | None:
    lowered = " ".join(str(text or "").strip().lower().split())
    if not lowered:
        return None
    if not any(marker in lowered for marker in _DIRECTORY_CREATE_MARKERS):
        return None
    if any(marker in lowered for marker in (" write ", " file ", " append ", " edit ", " change ", " delete ", " remove ", " rename ", " move ")):
        return None
    if not any(marker in lowered for marker in (" folder", " directory", " dir ", "mkdir")):
        return None
    root = ""
    if "desktop" in lowered:
        root = "~/Desktop"
    elif "downloads" in lowered:
        root = "~/Downloads"
    elif "documents" in lowered or "docs" in lowered:
        root = "~/Documents"
    if not root:
        return None
    relative_path = _extract_workspace_bootstrap_path(text)
    if not relative_path:
        return None
    return {"path": f"{root.rstrip('/')}/{relative_path}".replace("//", "/")}


def _extract_machine_specs_request(text: str) -> dict[str, Any] | None:
    lowered = " ".join(str(text or "").strip().lower().split())
    if not lowered:
        return None
    if any(
        marker in lowered
        for marker in (
            "what can you do",
            "what can you actually do",
            "what are your capabilities",
            "what can you help with",
            "actual local powers",
        )
    ):
        return None
    if any(marker in lowered for marker in ("write ", "create ", "edit ", "change ", "delete ", "remove ", "rename ", "move ")):
        return None
    spec_markers = (
        "machine specs",
        "machine spec",
        "our machine",
        "this machine",
        "what machine",
        "what is machine",
        "system specs",
        "hardware specs",
        "cpu",
        "ram",
        "memory",
        "gpu",
        "vram",
        "chip",
        "cores",
        "screen size",
        "display size",
        "display resolution",
        "screen resolution",
        "monitor resolution",
    )
    mentions_machine_shape = any(marker in lowered for marker in spec_markers)
    mentions_running_host = "running on" in lowered and "machine" in lowered
    if not mentions_machine_shape and not mentions_running_host:
        return None
    wants_inspection = any(
        marker in lowered
        for marker in (
            "what",
            "tell me",
            "show me",
            "check",
            "inspect",
            "which",
            "how much",
            "running on",
        )
    )
    if any(marker in lowered for marker in ("screen size", "display size", "display resolution", "screen resolution", "monitor resolution")):
        wants_inspection = True
    return {} if wants_inspection else None


def _looks_like_workspace_bootstrap_request(text: str) -> bool:
    lowered = " ".join(str(text or "").strip().lower().split())
    if not lowered:
        return False
    if (
        any(marker in lowered for marker in (" file ", " files ", " text file ", ".txt", ".md", ".json", ".yaml", ".yml", ".toml", ".py", ".ts", ".js"))
        and not any(marker in lowered for marker in _START_CODE_MARKERS)
    ):
        return False
    creates_folder = any(marker in lowered for marker in _DIRECTORY_CREATE_MARKERS)
    starts_code = any(marker in lowered for marker in _START_CODE_MARKERS)
    mentions_workspace_target = any(marker in lowered for marker in ("folder", "directory", "dir", "workspace", "repo", "repository"))
    has_path = bool(_extract_workspace_bootstrap_path(text))
    fuzzy_create_verb = any(v in lowered for v in ("create", "make", "mkdir", "crate", "creat"))
    return bool(
        (creates_folder and (starts_code or has_path))
        or (starts_code and mentions_workspace_target)
        or (starts_code and has_path)
        or (fuzzy_create_verb and mentions_workspace_target and has_path)
    )


def _clean_workspace_file_path(candidate: str, *, base_dir: str = "", workspace_root: str = "") -> str:
    raw = str(candidate or "").strip().strip("`\"'")
    clean = ""
    if raw and workspace_root:
        try:
            resolved_workspace_root = Path(workspace_root).expanduser().resolve()
            resolved_candidate = Path(raw).expanduser().resolve()
            if resolved_candidate == resolved_workspace_root or resolved_workspace_root in resolved_candidate.parents:
                clean = resolved_candidate.relative_to(resolved_workspace_root).as_posix()
        except Exception:
            clean = ""
    if not clean:
        clean = _clean_workspace_path(candidate)
    if not clean or "." not in Path(clean).name:
        return ""
    if base_dir and "/" not in clean:
        clean = f"{base_dir.rstrip('/')}/{clean}"
    return clean


def _history_messages(source_context: dict[str, Any] | None) -> list[dict[str, Any]]:
    return [dict(item) for item in list((source_context or {}).get("conversation_history") or []) if isinstance(item, dict)]


def _extract_history_observation_payload(message: dict[str, Any]) -> dict[str, Any] | None:
    content = str(message.get("content") or "").strip()
    if not content.startswith("Grounding observations for this turn."):
        return None
    start = content.find("{")
    if start < 0:
        return None
    try:
        payload = json.loads(content[start:])
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _recover_last_workspace_path_from_history(source_context: dict[str, Any] | None) -> str:
    history = _history_messages(source_context)
    workspace_root = str((source_context or {}).get("workspace") or (source_context or {}).get("workspace_root") or "").strip()
    for message in reversed(history[-12:]):
        observation = _extract_history_observation_payload(message)
        if observation is not None:
            intent = str(observation.get("intent") or "").strip()
            if intent in {"workspace.write_file", "workspace.read_file", "workspace.replace_in_file"}:
                path = _clean_workspace_file_path(
                    str(observation.get("path") or "").strip(),
                    workspace_root=workspace_root,
                )
                if path:
                    return path
        if str(message.get("role") or "").strip().lower() != "user":
            continue
        content = str(message.get("content") or "")
        history_writes, _history_directory = _extract_workspace_writes_from_text(
            content,
            workspace_root=workspace_root,
        )
        if history_writes:
            return str(history_writes[-1].get("path") or "").strip()
    return ""


def _extract_workspace_writes_from_text(
    text: str,
    *,
    workspace_root: str = "",
) -> tuple[list[dict[str, Any]], str]:
    raw = str(text or "").strip()
    if not raw:
        return [], ""

    base_dir = _extract_workspace_parent_directory(raw, workspace_root=workspace_root)
    if not base_dir and any(marker in raw.lower() for marker in _DIRECTORY_CREATE_MARKERS):
        base_dir = _extract_workspace_bootstrap_path(raw)

    folder_first_match = _FOLDER_FIRST_CREATE_FILE_RE.search(raw)
    if folder_first_match is not None:
        directory = _clean_workspace_directory_path(
            str(folder_first_match.group("directory") or "").strip(),
            workspace_root=workspace_root,
        )
        path = _clean_workspace_file_path(
            str(folder_first_match.group("path") or "").strip(),
            base_dir=directory,
            workspace_root=workspace_root,
        )
        content = str(folder_first_match.group("content") or "").strip()
        if path and content:
            return ([{"path": path, "content": content, "mode": "write"}], directory)

    workspace_root_create_match = _IN_WORKSPACE_CREATE_FILE_RE.search(raw)
    if workspace_root_create_match is not None:
        path = _clean_workspace_file_path(
            str(workspace_root_create_match.group("path") or "").strip(),
            base_dir="",
            workspace_root=workspace_root,
        )
        content = str(workspace_root_create_match.group("content") or "").strip()
        if path and content:
            return ([{"path": path, "content": content, "mode": "write"}], "")

    file_in_folder_match = _FILE_IN_FOLDER_SAYING_RE.search(raw)
    if file_in_folder_match is not None:
        directory = _clean_workspace_directory_path(
            str(file_in_folder_match.group("directory") or "").strip(),
            workspace_root=workspace_root,
        )
        path = _clean_workspace_file_path(
            str(file_in_folder_match.group("path") or "").strip(),
            base_dir=directory,
            workspace_root=workspace_root,
        )
        content = str(file_in_folder_match.group("content") or "").strip()
        if path and content:
            return ([{"path": path, "content": content, "mode": "write"}], directory)

    writes: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for pattern in (
        _OVERWRITE_FILE_RE,
        _CREATE_NAMED_FILE_WITH_CONTENT_RE,
        _INLINE_CREATE_FILE_RE,
        _PLAIN_CREATE_FILE_WITH_CONTENT_RE,
    ):
        for match in pattern.finditer(raw):
            path = _clean_workspace_file_path(
                str(match.group("path") or "").strip(),
                base_dir=base_dir,
                workspace_root=workspace_root,
            )
            content = str(match.group("content") or "").strip()
            # Multiple patterns can match the same prompt (e.g. a "with the line"
            # phrasing hits both the named and plain regexes). Keep only the first
            # write per path so the higher-priority pattern's content wins and a
            # conjoined two-file request yields exactly one write per distinct file.
            if path and content and path not in seen_paths:
                seen_paths.add(path)
                writes.append({"path": path, "content": content, "mode": "write"})
    return writes, base_dir


def _extract_workspace_file_plan(
    text: str,
    *,
    source_context: dict[str, Any] | None,
) -> dict[str, Any] | None:
    original = str(text or "").strip()
    if not original:
        return None
    raw = re.sub(
        r"(?P<stem>[A-Za-z0-9_./-]+)\.\s+(?P<ext>py|js|ts|tsx|jsx|txt|md|json|yaml|yml|toml)\b",
        r"\g<stem>.\g<ext>",
        original,
    )
    compact = " ".join(raw.split()).strip()
    workspace_root = str((source_context or {}).get("workspace") or (source_context or {}).get("workspace_root") or "").strip()
    base_dir = _extract_workspace_parent_directory(compact, workspace_root=workspace_root)
    if not base_dir and any(marker in compact.lower() for marker in _DIRECTORY_CREATE_MARKERS):
        base_dir = _extract_workspace_bootstrap_path(compact)
    list_requested = any(
        marker in compact.lower()
        for marker in (
            "list the folder contents",
            "list the directory contents",
            "list folder contents",
            "list directory contents",
            "list the contents",
        )
    )

    exact_multi = _CREATE_EXACT_FILES_RE.search(raw)
    if exact_multi is not None:
        paths = [_clean_workspace_file_path(item.strip(), base_dir=base_dir) for item in str(exact_multi.group("paths") or "").split(",")]
        contents = [item.strip() for item in str(exact_multi.group("contents") or "").split(",")]
        writes = [
            {"path": path, "content": contents[index], "mode": "write"}
            for index, path in enumerate(paths)
            if path and index < len(contents) and contents[index]
        ]
        if writes:
            list_path = base_dir
            if list_requested and not list_path:
                parent = str(Path(str(writes[0].get("path") or "")).parent)
                list_path = "" if parent in {"", "."} else parent
            return {"directory": "", "writes": writes, "read_path": "", "verbatim_read": False, "list_path": list_path}

    writes, planned_directory = _extract_workspace_writes_from_text(
        raw,
        workspace_root=workspace_root,
    )
    if writes:
        list_path = planned_directory or base_dir
        if list_requested and not list_path:
            parent = str(Path(str(writes[0].get("path") or "")).parent)
            list_path = "" if parent in {"", "."} else parent
        return {"directory": planned_directory, "writes": writes, "read_path": "", "verbatim_read": False, "list_path": list_path}

    append_match = _APPEND_FILE_RE.search(raw)
    if append_match is not None:
        path = _clean_workspace_file_path(
            str(append_match.group("path") or "").strip() or _recover_last_workspace_path_from_history(source_context),
            base_dir=base_dir,
        )
        content = str(append_match.group("content") or "").strip()
        if path and content:
            list_path = base_dir
            if list_requested and not list_path:
                parent = str(Path(path).parent)
                list_path = "" if parent in {"", "."} else parent
            return {"directory": "", "writes": [{"path": path, "content": content, "mode": "append"}], "read_path": "", "verbatim_read": False, "list_path": list_path}
    append_content_only_match = _APPEND_CONTENT_ONLY_RE.search(raw)
    if append_content_only_match is not None:
        path = _clean_workspace_file_path(
            _recover_last_workspace_path_from_history(source_context),
            base_dir=base_dir,
        )
        content = str(append_content_only_match.group("content") or "").strip()
        if path and content:
            list_path = base_dir
            if list_requested and not list_path:
                parent = str(Path(path).parent)
                list_path = "" if parent in {"", "."} else parent
            return {"directory": "", "writes": [{"path": path, "content": content, "mode": "append"}], "read_path": "", "verbatim_read": False, "list_path": list_path}

    explicit_read_match = _EXPLICIT_WORKSPACE_READ_RE.search(raw)
    if explicit_read_match is not None:
        path = _clean_workspace_file_path(
            str(explicit_read_match.group("path") or "").strip(),
            base_dir=base_dir,
            workspace_root=workspace_root,
        )
        if path:
            return {"directory": "", "writes": [], "read_path": path, "verbatim_read": True, "list_path": ""}

    if _EXACT_READBACK_RE.search(raw):
        path = _recover_last_workspace_path_from_history(source_context)
        if path:
            return {"directory": "", "writes": [], "read_path": path, "verbatim_read": True, "list_path": ""}
    return None


def _pending_workspace_writes(plan: dict[str, Any], steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    writes = [dict(item) for item in list(plan.get("writes") or []) if isinstance(item, dict)]
    if not writes:
        return []
    completed: list[tuple[str, str]] = []
    for step in list(steps or []):
        tool_name = str(step.get("tool_name") or "").strip()
        if tool_name != "workspace.write_file":
            continue
        args = dict(step.get("arguments") or {})
        completed.append((str(args.get("path") or "").strip(), "write"))
        completed.append((str(args.get("path") or "").strip(), "append"))
    return [item for item in writes if (str(item.get("path") or "").strip(), str(item.get("mode") or "write").strip()) not in completed]


def _normalize_hive_title_candidate(text: str) -> str:
    normalized = " ".join(str(text or "").split()).strip().strip("`\"'").strip().strip(".!?")
    normalized = normalized.lstrip("-:–—/ ").strip()
    return normalized


def _is_generic_hive_title_candidate(text: str) -> bool:
    normalized = _normalize_hive_title_candidate(text).lower()
    if normalized in _GENERIC_HIVE_TITLE_MARKERS or len(normalized) < 4:
        return True
    tokens = [token for token in re.split(r"[^a-z0-9]+", normalized) if token]
    if not tokens:
        return True
    generic_tokens = {"create", "creating", "new", "task", "tasks", "topic", "topics", "hive", "mind", "the", "this", "these"}
    return all(token in generic_tokens for token in tokens)


def _recover_hive_create_from_history(source_context: dict[str, Any] | None) -> tuple[str, str] | None:
    history = [dict(item) for item in list((source_context or {}).get("conversation_history") or []) if isinstance(item, dict)]
    for message in reversed(history[-8:]):
        if str(message.get("role") or "").strip().lower() != "user":
            continue
        content = " ".join(str(message.get("content") or "").split()).strip()
        lowered = content.lower()
        if not any(marker in lowered for marker in _HIVE_ACTION_PATTERNS) and " task:" not in lowered:
            continue
        sections = {
            "task": re.search(r"\btask\b\s*[:=-]\s*(.+?)(?=(?:\b(?:goal|summary)\b\s*[:=-])|(?:\b(?:topic tags?|tags?)\b\s*[:=-])|$)", content, re.IGNORECASE),
            "title": re.search(r"\b(?:name it|title|call it|called)\b\s*[:=-]?\s*(.+?)(?=(?:\bsummary\b\s*[:=-])|(?:\b(?:topic tags?|tags?)\b\s*[:=-])|$)", content, re.IGNORECASE),
            "goal": re.search(r"\bgoal\b\s*[:=-]\s*(.+?)(?=(?:\bsummary\b\s*[:=-])|(?:\b(?:topic tags?|tags?)\b\s*[:=-])|$)", content, re.IGNORECASE),
            "summary": re.search(r"\bsummary\b\s*[:=-]\s*(.+?)(?=(?:\b(?:topic tags?|tags?)\b\s*[:=-])|$)", content, re.IGNORECASE),
        }
        raw_title = ""
        if sections["title"] is not None:
            raw_title = str(sections["title"].group(1) or "")
        elif sections["task"] is not None:
            raw_title = str(sections["task"].group(1) or "")
        else:
            raw_title = content
            for prefix in _HIVE_CREATE_PREFIXES:
                if raw_title.lower().startswith(prefix):
                    raw_title = raw_title[len(prefix):].strip().lstrip("-:–").strip()
                    break
            if ":" in raw_title and raw_title.count(":") == 1:
                raw_title = raw_title.split(":", 1)[-1].strip()
        title = _normalize_hive_title_candidate(raw_title[:180])
        if _is_generic_hive_title_candidate(title):
            continue
        summary = ""
        if sections["summary"] is not None:
            summary = _normalize_hive_title_candidate(str(sections["summary"].group(1) or "")[:4000])
        elif sections["goal"] is not None:
            summary = _normalize_hive_title_candidate(str(sections["goal"].group(1) or "")[:4000])
        summary = summary or title
        return title, summary
    return None


def _recover_lookup_followup_from_history(source_context: dict[str, Any] | None) -> str:
    history = [dict(item) for item in list((source_context or {}).get("conversation_history") or []) if isinstance(item, dict)]
    for message in reversed(history[-8:]):
        if str(message.get("role") or "").strip().lower() != "user":
            continue
        content = " ".join(str(message.get("content") or "").split()).strip()
        if not content:
            continue
        if _looks_like_followup_resume_request(content):
            continue
        if looks_like_explicit_lookup_request(content) or looks_like_public_entity_lookup_request(content):
            return content
        break
    return ""


def _workflow_step_exists(steps: list[dict[str, Any]], intent: str, *, key: str | None = None, value: str | None = None) -> bool:
    normalized_intent = str(intent or "").strip()
    normalized_value = str(value or "").strip()
    for step in list(steps or []):
        if str(step.get("tool_name") or "").strip() != normalized_intent:
            continue
        if key is None:
            return True
        arguments = dict(step.get("arguments") or {})
        step_value = str(arguments.get(key) or "").strip()
        if step_value == normalized_value:
            return True
    return False


def _last_command_from_steps(steps: list[dict[str, Any]]) -> str:
    for step in reversed(list(steps or [])):
        if str(step.get("tool_name") or "").strip() != "sandbox.run_command":
            continue
        command = str(dict(step.get("arguments") or {}).get("command") or "").strip()
        if command:
            return command
    return ""


def _workflow_retry_already_happened(steps: list[dict[str, Any]], command: str) -> bool:
    normalized = str(command or "").strip()
    if not normalized:
        return False
    count = 0
    for step in list(steps or []):
        if str(step.get("tool_name") or "").strip() != "sandbox.run_command":
            continue
        step_command = str(dict(step.get("arguments") or {}).get("command") or "").strip()
        if step_command == normalized:
            count += 1
    return count >= 2


def _latest_failed_validation_hints(steps: list[dict[str, Any]]) -> dict[str, Any]:
    for step in reversed(list(steps or [])):
        observation = dict(step.get("observation") or {})
        intent = str(observation.get("intent") or "").strip()
        if intent not in {"workspace.run_tests", "workspace.run_lint", "workspace.run_formatter"}:
            if intent == "orchestration.execute_envelope":
                _, hints, rollback = _failed_validation_from_orchestration_step(step)
                if hints and bool(rollback.get("ok", False)):
                    return hints
            continue
        hints = extract_observation_followup_hints(observation)
        if int(hints.get("returncode") or 0) != 0:
            return hints
    return {}


def _latest_failed_validation_observation(steps: list[dict[str, Any]]) -> dict[str, Any]:
    for step in reversed(list(steps or [])):
        observation = dict(step.get("observation") or {})
        intent = str(observation.get("intent") or "").strip()
        if intent not in {"workspace.run_tests", "workspace.run_lint", "workspace.run_formatter"}:
            if intent == "orchestration.execute_envelope":
                nested_observation, hints, rollback = _failed_validation_from_orchestration_step(step)
                if nested_observation and hints and bool(rollback.get("ok", False)):
                    return nested_observation
            continue
        hints = extract_observation_followup_hints(observation)
        if int(hints.get("returncode") or 0) != 0:
            return observation
    return {}


def _failed_validation_from_orchestration_step(step: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    details = dict(step.get("details") or {})
    merged_result = dict(details.get("merged_result") or {})
    winner = dict(merged_result.get("winner") or {})
    winner_details = dict(winner.get("details") or {})
    rollback = dict(winner_details.get("failure_rollback") or {})
    if rollback and not bool(rollback.get("ok", False)):
        return {}, {}, {}
    if not rollback:
        return {}, {}, {}
    for step_payload in reversed(list(winner_details.get("step_results") or [])):
        if not isinstance(step_payload, dict):
            continue
        step_details = dict(step_payload.get("details") or {})
        observation = dict(step_details.get("observation") or {})
        intent = str(observation.get("intent") or "").strip()
        if intent not in {"workspace.run_tests", "workspace.run_lint", "workspace.run_formatter"}:
            continue
        hints = extract_observation_followup_hints(observation)
        if int(hints.get("returncode") or 0) != 0:
            return observation, hints, rollback
    return {}, {}, {}


def _latest_read_file_hints(steps: list[dict[str, Any]], *, path: str) -> dict[str, Any]:
    normalized_path = str(path or "").strip()
    if not normalized_path:
        return {}
    for step in reversed(list(steps or [])):
        observation = dict(step.get("observation") or {})
        if str(observation.get("intent") or "").strip() != "workspace.read_file":
            continue
        hints = extract_observation_followup_hints(observation)
        if str(hints.get("path") or "").strip() == normalized_path:
            return hints
    return {}


def _workflow_validation_command_already_attempted(steps: list[dict[str, Any]], command: str) -> bool:
    normalized = str(command or "").strip()
    if not normalized:
        return False
    for step in reversed(list(steps or [])):
        observation = dict(step.get("observation") or {})
        intent = str(observation.get("intent") or "").strip()
        if intent in {"workspace.run_tests", "workspace.run_lint", "workspace.run_formatter"}:
            step_command = str(dict(step.get("arguments") or {}).get("command") or "").strip()
            if step_command == normalized:
                return True
            hints = extract_observation_followup_hints(observation)
            if str(hints.get("command") or "").strip() == normalized:
                return True
            continue
        if intent == "orchestration.execute_envelope":
            _, _, rollback = _failed_validation_from_orchestration_step(step)
            if not bool(rollback.get("ok", False)):
                continue
            details = dict(step.get("details") or {})
            merged_result = dict(details.get("merged_result") or {})
            winner = dict(merged_result.get("winner") or {})
            winner_details = dict(winner.get("details") or {})
            for step_payload in reversed(list(winner_details.get("step_results") or [])):
                if not isinstance(step_payload, dict):
                    continue
                nested_intent = str(step_payload.get("intent") or "").strip()
                if nested_intent not in {"workspace.run_tests", "workspace.run_lint", "workspace.run_formatter"}:
                    continue
                nested_arguments = dict(step_payload.get("arguments") or {})
                nested_command = str(nested_arguments.get("command") or "").strip()
                if nested_command == normalized:
                    return True
                nested_details = dict(step_payload.get("details") or {})
                nested_observation = dict(nested_details.get("observation") or {})
                hints = extract_observation_followup_hints(nested_observation)
                if str(hints.get("command") or "").strip() == normalized:
                    return True
    return False


def _iter_read_file_hints(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hints_list: list[dict[str, Any]] = []
    for step in reversed(list(steps or [])):
        observation = dict(step.get("observation") or {})
        if str(observation.get("intent") or "").strip() != "workspace.read_file":
            continue
        hints = extract_observation_followup_hints(observation)
        if hints:
            hints_list.append(hints)
    return hints_list


def _latest_lookup_hints(steps: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    for step in reversed(list(steps or [])):
        observation = dict(step.get("observation") or {})
        intent = str(observation.get("intent") or "").strip()
        if intent not in {"workspace.symbol_search", "workspace.search_text"}:
            continue
        return intent, extract_observation_followup_hints(observation)
    return "", {}


def _next_unread_lookup_path(
    *,
    steps: list[dict[str, Any]],
    hints: dict[str, Any],
    current_path: str,
) -> tuple[str, int]:
    primary_path = str(hints.get("primary_path") or "").strip()
    primary_line = int(hints.get("primary_line") or 0)
    candidate_paths: list[str] = []
    for candidate in [primary_path, *list(hints.get("paths") or [])]:
        normalized = str(candidate or "").strip()
        if normalized and normalized not in candidate_paths:
            candidate_paths.append(normalized)
    for candidate in candidate_paths:
        if candidate == current_path:
            continue
        if _workflow_step_exists(steps, "workspace.read_file", key="path", value=candidate):
            continue
        return candidate, primary_line if candidate == primary_path else 0
    return "", 0


def _diagnostic_symbol_query(query: str) -> str:
    normalized = str(query or "").strip()
    if not normalized:
        return ""
    call_match = re.search(r"([A-Za-z_][A-Za-z0-9_\.]*)\s*\(", normalized)
    candidate = str(call_match.group(1) or "").strip() if call_match else ""
    if not candidate:
        full_match = re.fullmatch(r"[A-Za-z_][A-Za-z0-9_\.]*", normalized)
        candidate = str(full_match.group(0) or "").strip() if full_match else ""
    if not candidate:
        return ""
    symbol = candidate.split(".")[-1].strip()
    if not symbol:
        return ""
    if symbol.lower() in {"assertionerror", "traceback", "exception", "error", "failed"}:
        return ""
    return symbol


def _planned_diagnostic_lookup_followup(
    *,
    steps: list[dict[str, Any]],
    diagnostic_query: str,
    symbol_reason: str,
    search_reason: str,
) -> WorkflowPlannerDecision | None:
    symbol = _diagnostic_symbol_query(diagnostic_query)
    if symbol and not _workflow_step_exists(steps, "workspace.symbol_search", key="symbol", value=symbol):
        return WorkflowPlannerDecision(
            handled=True,
            reason=symbol_reason,
            next_payload={"intent": "workspace.symbol_search", "arguments": {"symbol": symbol, "limit": 10}},
        )
    if diagnostic_query and not _workflow_step_exists(steps, "workspace.search_text", key="query", value=diagnostic_query):
        return WorkflowPlannerDecision(
            handled=True,
            reason=search_reason,
            next_payload={"intent": "workspace.search_text", "arguments": {"query": diagnostic_query, "limit": 10}},
        )
    return None


def _planned_validation_failure_followup(
    *,
    steps: list[dict[str, Any]],
    hints: dict[str, Any],
    inspect_reason: str,
    symbol_reason: str,
    search_reason: str,
    stop_reason: str,
) -> WorkflowPlannerDecision:
    error_path = str(hints.get("error_path") or "").strip()
    error_line = int(hints.get("error_line") or 0)
    if error_path and not _workflow_step_exists(steps, "workspace.read_file", key="path", value=error_path):
        return WorkflowPlannerDecision(
            handled=True,
            reason=inspect_reason,
            next_payload={
                "intent": "workspace.read_file",
                "arguments": {
                    "path": error_path,
                    "start_line": max(1, error_line - 8) if error_line else 1,
                    "max_lines": 60,
                },
            },
        )
    diagnostic_query = str(hints.get("diagnostic_query") or "").strip()
    if diagnostic_query:
        lookup_followup = _planned_diagnostic_lookup_followup(
            steps=steps,
            diagnostic_query=diagnostic_query,
            symbol_reason=symbol_reason,
            search_reason=search_reason,
        )
        if lookup_followup is not None:
            return lookup_followup
    return WorkflowPlannerDecision(handled=True, reason=stop_reason, stop_after=True)


def _can_plan_candidate_repair_envelope(steps: list[dict[str, Any]]) -> bool:
    envelope_steps = [
        step
        for step in list(steps or [])
        if str(dict(step.get("observation") or {}).get("intent") or "").strip() == "orchestration.execute_envelope"
    ]
    if not envelope_steps:
        return True
    if len(envelope_steps) > 1:
        return False
    last_envelope = dict(envelope_steps[-1] or {})
    _, hints, rollback = _failed_validation_from_orchestration_step(last_envelope)
    if not hints or not bool(rollback.get("ok", False)):
        return False
    observation = dict(last_envelope.get("observation") or {})
    return not bool(observation.get("ok", False))


def _has_retryable_failed_envelope(steps: list[dict[str, Any]]) -> bool:
    envelope_steps = [
        step
        for step in list(steps or [])
        if str(dict(step.get("observation") or {}).get("intent") or "").strip() == "orchestration.execute_envelope"
    ]
    if not envelope_steps:
        return False
    last_envelope = dict(envelope_steps[-1] or {})
    _, hints, rollback = _failed_validation_from_orchestration_step(last_envelope)
    if not hints or not bool(rollback.get("ok", False)):
        return False
    observation = dict(last_envelope.get("observation") or {})
    return not bool(observation.get("ok", False))


def _infer_literal_candidate_repair(
    *,
    user_text: str,
    steps: list[dict[str, Any]],
    current_read_hints: dict[str, Any],
) -> dict[str, str] | None:
    latest_validation = _latest_failed_validation_observation(steps)
    if str(latest_validation.get("intent") or "").strip() != "workspace.run_tests":
        return None
    if not _looks_like_failing_test_repair_request(user_text, validation_step=latest_validation):
        return None
    error_path, function_name, expected_literal = _failing_test_expectation_from_steps(steps)
    current_path = str(current_read_hints.get("path") or "").strip()
    if not error_path or not current_path or current_path == error_path:
        return None
    implementation_content = str(current_read_hints.get("content") or "").strip()
    if not function_name or not expected_literal or not implementation_content:
        return None

    direct_repair = _literal_return_candidate_repair(
        lines=_read_hint_lines(current_read_hints),
        function_name=function_name,
        expected_literal=expected_literal,
        current_path=current_path,
    )
    if direct_repair is not None:
        return direct_repair
    binding_repair = _literal_binding_candidate_repair(
        lines=_read_hint_lines(current_read_hints),
        function_name=function_name,
        expected_literal=expected_literal,
        current_path=current_path,
    )
    if binding_repair is not None:
        return binding_repair

    current_lines = _read_hint_lines(current_read_hints)
    if not current_lines:
        return None
    for prior_read_hints in _iter_read_file_hints(steps):
        prior_path = str(prior_read_hints.get("path") or "").strip()
        if not prior_path or prior_path in {current_path, error_path}:
            continue
        imported_binding_name = _imported_binding_name_from_path(
            lines=_read_hint_lines(prior_read_hints),
            function_name=function_name,
            current_path=current_path,
        )
        if imported_binding_name:
            imported_binding_repair = _top_level_literal_binding_candidate_repair(
                lines=current_lines,
                binding_name=imported_binding_name,
                expected_literal=expected_literal,
                current_path=current_path,
            )
            if imported_binding_repair is not None:
                return imported_binding_repair
        delegate_function = _single_delegate_function_name(
            lines=_read_hint_lines(prior_read_hints),
            function_name=function_name,
        )
        if not delegate_function:
            continue
        delegated_repair = _literal_return_candidate_repair(
            lines=current_lines,
            function_name=delegate_function,
            expected_literal=expected_literal,
            current_path=current_path,
        )
        if delegated_repair is not None:
            return delegated_repair
        delegated_binding_repair = _literal_binding_candidate_repair(
            lines=current_lines,
            function_name=delegate_function,
            expected_literal=expected_literal,
            current_path=current_path,
        )
        if delegated_binding_repair is not None:
            return delegated_binding_repair
    return None


def _failing_test_expectation_from_steps(steps: list[dict[str, Any]]) -> tuple[str, str, str]:
    latest_validation = _latest_failed_validation_observation(steps)
    error_path = str(latest_validation.get("error_path") or "").strip()
    if not error_path:
        return "", "", ""
    test_read_hints = _latest_read_file_hints(steps, path=error_path)
    test_content = str(test_read_hints.get("content") or "").strip()
    if not test_content:
        return error_path, "", ""
    expected_match = re.search(
        r"assert\s+(?P<call>[A-Za-z_][A-Za-z0-9_\.]*)\s*\(\s*\)\s*==\s*(?P<expected>-?\d+|True|False|None|'[^']*'|\"[^\"]*\")",
        test_content,
    )
    if not expected_match:
        return error_path, "", ""
    function_name = str(expected_match.group("call") or "").strip().split(".")[-1]
    expected_literal = str(expected_match.group("expected") or "").strip()
    return error_path, function_name, expected_literal


def _read_hint_lines(read_hints: dict[str, Any]) -> list[str]:
    return [str(item.get("text") or "") for item in list(read_hints.get("lines") or []) if isinstance(item, dict)]


def _single_function_body_line(lines: list[str], function_name: str) -> str:
    function_start = None
    for index, raw_line in enumerate(list(lines or [])):
        if re.match(rf"^\s*def\s+{re.escape(function_name)}\s*\(", raw_line):
            function_start = index
            break
    if function_start is None:
        return ""
    body_lines: list[str] = []
    for raw_line in list(lines or [])[function_start + 1 :]:
        if re.match(r"^\s*(def|class)\s+", raw_line):
            break
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        body_lines.append(raw_line)
    if len(body_lines) != 1:
        return ""
    return str(body_lines[0] or "")


def _literal_return_candidate_repair(
    *,
    lines: list[str],
    function_name: str,
    expected_literal: str,
    current_path: str,
) -> dict[str, str] | None:
    body_line = _single_function_body_line(lines, function_name)
    if not body_line:
        return None
    return_match = re.match(r"^(?P<indent>\s*)return\s+(?P<value>-?\d+|True|False|None|'[^']*'|\"[^\"]*\")\s*$", body_line)
    if not return_match:
        return None
    actual_literal = str(return_match.group("value") or "").strip()
    if actual_literal == expected_literal:
        return None
    return {
        "path": current_path,
        "old_text": body_line.strip(),
        "new_text": f"return {expected_literal}",
    }


def _literal_binding_candidate_repair(
    *,
    lines: list[str],
    function_name: str,
    expected_literal: str,
    current_path: str,
) -> dict[str, str] | None:
    body_line = _single_function_body_line(lines, function_name)
    if not body_line:
        return None
    binding_return_match = re.match(r"^\s*return\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*$", body_line)
    if not binding_return_match:
        return None
    binding_name = str(binding_return_match.group("name") or "").strip()
    if not binding_name:
        return None
    return _top_level_literal_binding_candidate_repair(
        lines=lines,
        binding_name=binding_name,
        expected_literal=expected_literal,
        current_path=current_path,
    )


def _top_level_literal_binding_candidate_repair(
    *,
    lines: list[str],
    binding_name: str,
    expected_literal: str,
    current_path: str,
) -> dict[str, str] | None:
    binding_matches: list[tuple[str, str]] = []
    binding_pattern = re.compile(
        rf"^(?P<name>{re.escape(binding_name)})\s*=\s*(?P<value>-?\d+|True|False|None|'[^']*'|\"[^\"]*\")\s*$"
    )
    for raw_line in list(lines or []):
        if raw_line.startswith((" ", "\t")):
            continue
        match = binding_pattern.match(raw_line)
        if not match:
            continue
        binding_matches.append((raw_line, str(match.group("value") or "").strip()))
    if len(binding_matches) != 1:
        return None
    binding_line, actual_literal = binding_matches[0]
    if actual_literal == expected_literal:
        return None
    return {
        "path": current_path,
        "old_text": binding_line.strip(),
        "new_text": f"{binding_name} = {expected_literal}",
    }


def _imported_binding_name_from_path(
    *,
    lines: list[str],
    function_name: str,
    current_path: str,
) -> str:
    body_line = _single_function_body_line(lines, function_name)
    if not body_line:
        return ""
    binding_return_match = re.match(r"^\s*return\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*$", body_line)
    if not binding_return_match:
        return ""
    local_binding_name = str(binding_return_match.group("name") or "").strip()
    if not local_binding_name:
        return ""
    path_value = str(current_path or "").strip()
    if not path_value:
        return ""
    module_stem = Path(path_value).with_suffix("").as_posix().replace("/", ".").strip(".")
    module_variants = {module_stem}
    if "." in module_stem:
        module_variants.add(module_stem.split(".")[-1])
    import_pattern = re.compile(
        r"^\s*from\s+(?P<module>[A-Za-z_][A-Za-z0-9_\.]*)\s+import\s+(?P<imports>.+?)\s*$"
    )
    alias_pattern = re.compile(
        r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s+as\s+(?P<alias>[A-Za-z_][A-Za-z0-9_]*)$"
    )
    for raw_line in list(lines or []):
        if raw_line.startswith((" ", "\t")):
            continue
        match = import_pattern.match(raw_line)
        if not match:
            continue
        module_name = str(match.group("module") or "").strip()
        if module_name not in module_variants:
            continue
        imports_text = str(match.group("imports") or "").strip()
        for part in [item.strip() for item in imports_text.split(",") if item.strip()]:
            alias_match = alias_pattern.match(part)
            if alias_match:
                imported_name = str(alias_match.group("name") or "").strip()
                local_name = str(alias_match.group("alias") or "").strip()
                if local_name == local_binding_name:
                    return imported_name
                continue
            if part == local_binding_name:
                return part
    return ""


def _single_delegate_function_name(*, lines: list[str], function_name: str) -> str:
    body_line = _single_function_body_line(lines, function_name)
    if not body_line:
        return ""
    delegate_match = re.match(r"^\s*return\s+(?P<callee>[A-Za-z_][A-Za-z0-9_]*)\s*\(\s*\)\s*$", body_line)
    if not delegate_match:
        return ""
    return str(delegate_match.group("callee") or "").strip()


def _planned_delegate_lookup_after_read(
    *,
    user_text: str,
    steps: list[dict[str, Any]],
    current_read_hints: dict[str, Any],
) -> WorkflowPlannerDecision | None:
    latest_validation = _latest_failed_validation_observation(steps)
    if str(latest_validation.get("intent") or "").strip() != "workspace.run_tests":
        return None
    if not _looks_like_failing_test_repair_request(user_text, validation_step=latest_validation):
        return None
    error_path, function_name, _ = _failing_test_expectation_from_steps(steps)
    current_path = str(current_read_hints.get("path") or "").strip()
    if not error_path or not function_name or not current_path or current_path == error_path:
        return None
    delegate_function = _single_delegate_function_name(
        lines=_read_hint_lines(current_read_hints),
        function_name=function_name,
    )
    if not delegate_function:
        return None
    if _workflow_step_exists(steps, "workspace.symbol_search", key="symbol", value=delegate_function):
        return None
    return WorkflowPlannerDecision(
        handled=True,
        reason="planned_symbol_search_after_delegate_inspection",
        next_payload={"intent": "workspace.symbol_search", "arguments": {"symbol": delegate_function, "limit": 10}},
    )


def _explicit_replace_request(user_text: str) -> dict[str, str] | None:
    text = str(user_text or "").strip()
    if not text:
        return None
    fenced = re.search(
        r"replace\s+`(?P<old>[^`]+)`\s+with\s+`(?P<new>[^`]+)`(?:\s+in\s+(?P<path>[A-Za-z0-9_./-]+\.[A-Za-z0-9_+-]+))?",
        text,
        re.IGNORECASE,
    )
    if fenced:
        return {
            "old_text": str(fenced.group("old") or "").strip(),
            "new_text": str(fenced.group("new") or "").strip(),
            "path": _normalize_inline_path(str(fenced.group("path") or "").strip()),
        }
    plain = re.search(
        r"replace\s+(?P<old>[A-Za-z0-9_.:/-]+)\s+with\s+(?P<new>[A-Za-z0-9_.:/-]+)(?:\s+in\s+(?P<path>[A-Za-z0-9_./-]+\.[A-Za-z0-9_+-]+))?",
        text,
        re.IGNORECASE,
    )
    if plain:
        return {
            "old_text": str(plain.group("old") or "").strip(),
            "new_text": str(plain.group("new") or "").strip(),
            "path": _normalize_inline_path(str(plain.group("path") or "").strip()),
        }
    return None


def _explicit_unified_diff_request(user_text: str) -> str:
    text = str(user_text or "")
    if not text.strip():
        return ""
    fenced = re.search(
        r"```(?:diff|patch)\s*\n(?P<patch>.*?)(?:\n```|```)",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if fenced:
        patch = str(fenced.group("patch") or "").strip("\n")
        if patch and "--- " in patch and "+++ " in patch and "@@ " in patch:
            return patch
    return ""


def _explicit_command_request(user_text: str) -> str:
    text = str(user_text or "").strip()
    if not text:
        return ""
    fenced = re.search(r"(?:run|execute|retry|rerun)\s+`(?P<command>[^`]+)`", text, re.IGNORECASE)
    if fenced:
        return _normalize_inline_command(str(fenced.group("command") or "").strip())
    common = re.search(
        r"\b(?:run|execute|retry|rerun)\s+(?P<command>(?:pytest(?:\s+-[A-Za-z0-9-]+)*(?:\s+[A-Za-z0-9_./:-]+)*)|(?:python3?\s+[A-Za-z0-9_./:-]+(?:\s+[A-Za-z0-9_./:=+-]+)*)|(?:npm\s+(?:test|run\s+[A-Za-z0-9:_-]+))|(?:cargo\s+test(?:\s+[A-Za-z0-9_./:-]+)*))",
        text,
        re.IGNORECASE,
    )
    if common:
        return _normalize_inline_command(str(common.group("command") or "").strip())
    return ""


def _normalize_inline_command(command: str) -> str:
    clean = " ".join(str(command or "").split()).strip()
    if not clean:
        return ""
    clean = re.sub(r"(?P<stem>[A-Za-z0-9_/-]+)\.\s+(?P<ext>py|js|ts|json|yaml|yml|toml|sh|md)\b", r"\g<stem>.\g<ext>", clean)
    return clean


def _normalize_inline_path(path: str) -> str:
    return _normalize_inline_command(path)


def _validation_step_from_request(*, user_text: str, explicit_command: str) -> dict[str, Any] | None:
    lowered = f" {' '.join(str(user_text or '').lower().split())} "
    command = _normalize_inline_command(explicit_command)
    if command:
        normalized = command.lower()
        if re.match(r"^(?:python\d?(?:\.\d+)?\s+-m\s+)?pytest\b", normalized):
            return {"intent": "workspace.run_tests", "arguments": {"command": command}}
        if re.match(r"^(?:python\d?(?:\.\d+)?\s+-m\s+)?ruff\s+check\b", normalized):
            return {"intent": "workspace.run_lint", "arguments": {"command": command}}
        if re.match(r"^(?:python\d?(?:\.\d+)?\s+-m\s+)?ruff\s+format\b", normalized):
            return {
                "intent": "workspace.run_formatter",
                "arguments": {
                    "command": command,
                    "apply": " --check" not in normalized,
                },
            }
        return None
    if any(marker in lowered for marker in (" run tests ", " rerun tests ", " pytest ")):
        return {"intent": "workspace.run_tests", "arguments": {}}
    if any(marker in lowered for marker in (" run lint ", " rerun lint ", " lint it ", " ruff check ")):
        return {"intent": "workspace.run_lint", "arguments": {}}
    if any(marker in lowered for marker in (" format it ", " run formatter ", " check formatting ", " ruff format ")):
        return {
            "intent": "workspace.run_formatter",
            "arguments": {
                "apply": any(marker in lowered for marker in (" format it ", " apply formatting ", " run formatter ")),
            },
        }
    return None


def _looks_like_failing_test_repair_request(
    user_text: str,
    *,
    validation_step: dict[str, Any] | None,
) -> bool:
    if not isinstance(validation_step, dict):
        return False
    if str(validation_step.get("intent") or "").strip() != "workspace.run_tests":
        return False
    lowered = re.sub(r"[^a-z0-9]+", " ", str(user_text or "").lower())
    lowered = f" {' '.join(lowered.split())} "
    failure_markers = (
        " failing test ",
        " failing tests ",
        " tests are failing ",
        " test is failing ",
        " broken test ",
        " broken tests ",
        " pytest is failing ",
        " traceback ",
        " stack trace ",
        " assertionerror ",
        " fix the test ",
        " fix the tests ",
    )
    return any(marker in lowered for marker in failure_markers)


def _planned_command_payload(*, user_text: str, command: str) -> tuple[str, dict[str, Any]] | None:
    validation_step = _validation_step_from_request(user_text=user_text, explicit_command=command)
    if validation_step is not None:
        return ("planned_validation_run", validation_step)
    normalized_command = _normalize_inline_command(command)
    if not normalized_command:
        return None
    return (
        "planned_command_run",
        {"intent": "sandbox.run_command", "arguments": {"command": normalized_command}},
    )


def _planned_orchestrated_operator_payload(
    *,
    user_text: str,
    task_class: str,
    source_context: dict[str, Any] | None,
    replacement: dict[str, Any] | None,
    patch_text: str,
    explicit_command: str,
) -> dict[str, Any] | None:
    source_context = dict(source_context or {})
    workspace = str(source_context.get("workspace") or source_context.get("workspace_root") or "").strip()
    if not workspace:
        return None
    replace_payload = dict(replacement or {})
    normalized_patch = str(patch_text or "").strip()
    path = str(replace_payload.get("path") or "").strip()
    old_text = str(replace_payload.get("old_text") or "").strip()
    new_text = str(replace_payload.get("new_text") or "").strip()
    if not normalized_patch and not (path and old_text and new_text):
        if not (old_text and new_text):
            return None
    validation_step = _validation_step_from_request(user_text=user_text, explicit_command=explicit_command)
    if validation_step is None:
        return None
    bounded_repo_repair = looks_like_bounded_repo_repair_request(user_text)
    normalized_request = re.sub(r"[^a-z0-9]+", " ", str(user_text or "").lower())
    normalized_request = f" {' '.join(normalized_request.split())} "
    if not normalized_patch and not any(
        marker in normalized_request
        for marker in (" apply ", " replace ", " patch ", " edit ", " change ", " fix ")
    ):
        return None
    normalized_task_class = str(task_class or "").strip().lower()
    if normalized_task_class not in {
        "unknown",
        "debugging",
        "dependency_resolution",
        "config",
        "security_hardening",
        "integration_orchestration",
    } and not bounded_repo_repair:
        return None
    planned_task_class = "debugging" if bounded_repo_repair else (normalized_task_class or "debugging")
    from core.orchestration import build_task_envelope

    task_suffix = hashlib.sha1(
        f"{planned_task_class}|{path}|{old_text}|{new_text}|{normalized_patch}|{explicit_command}".encode()
    ).hexdigest()[:12]
    queen_id = f"queen-{task_suffix}"
    privacy_class = str(source_context.get("share_scope") or "local_only")
    preflight_verifier = None
    preflight_task_id = f"preflight-verify-{task_suffix}"
    final_verifier_dependencies = [f"coder-{task_suffix}"]
    if _looks_like_failing_test_repair_request(user_text, validation_step=validation_step):
        preflight_step = {
            "step_id": "capture-failing-validation",
            **dict(validation_step),
            "allow_failure": True,
        }
        preflight_verifier = build_task_envelope(
            role="verifier",
            task_id=preflight_task_id,
            parent_task_id=queen_id,
            goal="Capture the current failing test state before any workspace mutation.",
            inputs={
                "task_class": planned_task_class,
                "runtime_tools": [preflight_step],
            },
            required_receipts=("tool_receipt", "validation_result"),
            privacy_class=privacy_class,
        )
    if normalized_patch:
        coder_tools = [
            {
                "step_id": "apply-patch",
                "intent": "workspace.apply_unified_diff",
                "arguments": {"patch": normalized_patch},
            },
        ]
    elif path:
        coder_tools = [
            {"step_id": "inspect-target", "intent": "workspace.read_file", "arguments": {"path": path, "start_line": 1, "max_lines": 240}},
            {
                "step_id": "apply-replacement",
                "intent": "workspace.replace_in_file",
                "arguments": {
                    "path": path,
                    "old_text": old_text,
                    "new_text": new_text,
                    "replace_all": True,
                },
            },
        ]
    else:
        path_ref = {
            "$from_step": "locate-replacement-target",
            "$path": "observation.primary_path",
            "$require_single_match": True,
        }
        coder_tools = [
            {
                "step_id": "locate-replacement-target",
                "intent": "workspace.search_text",
                "arguments": {"query": old_text, "limit": 2},
            },
            {
                "step_id": "inspect-target",
                "intent": "workspace.read_file",
                "arguments": {"path": dict(path_ref), "start_line": 1, "max_lines": 240},
            },
            {
                "step_id": "apply-replacement",
                "intent": "workspace.replace_in_file",
                "arguments": {
                    "path": dict(path_ref),
                    "old_text": old_text,
                    "new_text": new_text,
                    "replace_all": True,
                },
            },
        ]
    coder = build_task_envelope(
        role="coder",
        task_id=f"coder-{task_suffix}",
        parent_task_id=queen_id,
        goal=(
            "Apply the requested unified diff inside the active workspace."
            if normalized_patch
            else f"Apply the requested workspace change in `{path}`."
            if path
            else "Locate the requested workspace change target, inspect it, and apply the requested replacement."
        ),
        inputs={
            "task_class": planned_task_class,
            "depends_on": [preflight_task_id] if preflight_verifier is not None else [],
            "runtime_tools": coder_tools,
        },
        required_receipts=("tool_receipt",),
        privacy_class=privacy_class,
    )
    verifier = build_task_envelope(
        role="verifier",
        task_id=f"verify-{task_suffix}",
        parent_task_id=queen_id,
        goal="Validate the requested workspace change.",
        inputs={
            "task_class": planned_task_class,
            "depends_on": final_verifier_dependencies,
            "rollback_on_failure": True,
            "runtime_tools": [validation_step],
        },
        required_receipts=("tool_receipt", "validation_result"),
        privacy_class=privacy_class,
    )
    queen = build_task_envelope_for_request(
        user_text,
        context={"share_scope": privacy_class},
        task_id=queen_id,
        chat_surface=False,
        planner_style_requested=False,
    )
    queen_payload = {
        **queen.to_dict(),
        "role": "queen",
        "inputs": {
            **dict(queen.inputs or {}),
            "task_class": planned_task_class,
            "planner_source": "execution_planner",
            "subtasks": [
                *( [preflight_verifier.to_dict()] if preflight_verifier is not None else [] ),
                coder.to_dict(),
                verifier.to_dict(),
            ],
        },
        "merge_strategy": "highest_score",
        "required_receipts": [],
    }
    return {"intent": "orchestration.execute_envelope", "arguments": {"task_envelope": queen_payload}}


def plan_tool_workflow(
    *,
    user_text: str,
    task_class: str,
    executed_steps: list[dict[str, Any]],
    source_context: dict[str, Any] | None,
) -> WorkflowPlannerDecision:
    raw_text = str(user_text or "")
    text = " ".join(raw_text.split()).strip()
    lowered = f" {text.lower()} "
    followup_resume = _looks_like_followup_resume_request(text)
    steps = [dict(step) for step in list(executed_steps or []) if isinstance(step, dict)]
    replacement = _explicit_replace_request(raw_text)
    patch_text = _explicit_unified_diff_request(raw_text)
    explicit_command = _explicit_command_request(raw_text)
    compare_or_verify = any(marker in lowered for marker in (" compare ", " versus ", " vs ", " verify ", " confirm ", " is it true "))
    lookup_followup_text = ""
    if followup_resume and not (looks_like_explicit_lookup_request(text) or looks_like_public_entity_lookup_request(text)):
        lookup_followup_text = _recover_lookup_followup_from_history(source_context)
    research_text = lookup_followup_text or text
    public_entity_lookup = looks_like_public_entity_lookup_request(research_text)
    explicit_lookup = looks_like_explicit_lookup_request(research_text) or public_entity_lookup
    tool_inventory_request = _looks_like_tool_inventory_request(text)
    machine_directory_list = _extract_safe_machine_directory_listing(text)
    machine_directory_create = _extract_safe_machine_directory_create(text)
    machine_specs_request = _extract_machine_specs_request(text)
    git_payload = _planned_git_payload(text)
    workspace_search_payload = _planned_workspace_search_payload(text)
    workspace_bootstrap_path = _extract_workspace_bootstrap_path(text)
    workspace_bootstrap_request = _looks_like_workspace_bootstrap_request(text)
    workspace_file_plan = _extract_workspace_file_plan(raw_text, source_context=source_context)
    entity_query, entity_retry_query = _entity_lookup_query_variants(research_text)
    last_step = dict(steps[-1] or {}) if steps else {}
    last_intent = str(last_step.get("tool_name") or "").strip()
    research_flow = bool(
        explicit_lookup
        or compare_or_verify
        or task_class in {"research", "chat_research", "system_design"}
        or any(marker in lowered for marker in (" latest ", " current ", " docs ", " documentation ", " research ", " source "))
        or (followup_resume and last_intent in {"web.search", "web.fetch", "web.research"})
    )

    create_task_markers = (
        " create task ", " create new task ", " new task for ", " add task ", " add to hive ", " add to the hive ",
        " create these tasks ", " create them ", " create these ", " yes create ", " yes create them ",
    )

    def _create_task_fuzzy(lo: str) -> bool:
        return ("create" in lo and "task" in lo) or ("create" in lo and ("hive" in lo or "topic" in lo))

    def _proceed_with_task(lo: str) -> bool:
        return any(m in lo for m in (" proceed ", " do it ", " do all ", " start working ", " go ahead ", " carry on ")) and (
            "task" in lo or "hive" in lo or "create" in lo
        )

    if not steps:
        if lookup_followup_text and explicit_lookup:
            if public_entity_lookup:
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_entity_lookup_search",
                    next_payload={"intent": "web.search", "arguments": {"query": entity_query or research_text, "limit": 4}},
                )
            if research_flow:
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_research_search",
                    next_payload={"intent": "web.search", "arguments": {"query": research_text, "limit": 4}},
                )
        if tool_inventory_request:
            return WorkflowPlannerDecision(
                handled=True,
                reason="planned_operator_tool_inventory",
                next_payload={"intent": "operator.list_tools", "arguments": {}},
            )
        if machine_specs_request is not None:
            return WorkflowPlannerDecision(
                handled=True,
                reason="planned_machine_specs_inspection",
                next_payload={"intent": "machine.inspect_specs", "arguments": machine_specs_request},
            )
        if machine_directory_list:
            return WorkflowPlannerDecision(
                handled=True,
                reason="planned_safe_machine_directory_list",
                next_payload={"intent": "machine.list_directory", "arguments": machine_directory_list},
            )
        if machine_directory_create:
            return WorkflowPlannerDecision(
                handled=True,
                reason="planned_safe_machine_directory_create",
                next_payload={"intent": "machine.ensure_directory", "arguments": machine_directory_create},
            )
        if git_payload is not None:
            reason, next_payload = git_payload
            return WorkflowPlannerDecision(
                handled=True,
                reason=reason,
                next_payload=next_payload,
            )
        if workspace_search_payload is not None:
            reason, next_payload = workspace_search_payload
            return WorkflowPlannerDecision(
                handled=True,
                reason=reason,
                next_payload=next_payload,
            )
        if workspace_file_plan is not None:
            pending_writes = _pending_workspace_writes(workspace_file_plan, steps)
            planned_directory = str(workspace_file_plan.get("directory") or "").strip()
            if planned_directory:
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_workspace_directory_bootstrap",
                    next_payload={"intent": "workspace.ensure_directory", "arguments": {"path": planned_directory}},
                )
            if pending_writes:
                first_write = dict(pending_writes[0] or {})
                if str(first_write.get("mode") or "").strip() == "append":
                    return WorkflowPlannerDecision(
                        handled=True,
                        reason="planned_read_before_append",
                        next_payload={"intent": "workspace.read_file", "arguments": {"path": first_write["path"], "start_line": 1, "max_lines": 400, "verbatim": True}},
                    )
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_workspace_write_file",
                    next_payload={"intent": "workspace.write_file", "arguments": {"path": first_write["path"], "content": first_write["content"]}},
                )
            read_path = str(workspace_file_plan.get("read_path") or "").strip()
            if read_path:
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_workspace_readback",
                    next_payload={"intent": "workspace.read_file", "arguments": {"path": read_path, "start_line": 1, "max_lines": 400, "verbatim": bool(workspace_file_plan.get("verbatim_read", False))}},
                )
        if workspace_bootstrap_request and workspace_bootstrap_path:
            wants_desktop = any(m in lowered for m in (" desktop ", " on my desktop", " my desktop", " on desktop", "~/desktop"))
            wants_home = any(m in lowered for m in (" home ", " home/", " my machine", " this machine", "~/", "folder in my machine"))
            home_dir = str(Path.home())
            if wants_desktop:
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_desktop_directory_create",
                    next_payload={"intent": "sandbox.run_command", "arguments": {"command": f"mkdir -p {home_dir}/Desktop/{workspace_bootstrap_path}"}},
                )
            if wants_home:
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_home_directory_create",
                    next_payload={"intent": "sandbox.run_command", "arguments": {"command": f"mkdir -p {home_dir}/{workspace_bootstrap_path}"}},
                )
            return WorkflowPlannerDecision(
                handled=True,
                reason="planned_workspace_directory_bootstrap",
                next_payload={"intent": "workspace.ensure_directory", "arguments": {"path": workspace_bootstrap_path}},
            )
        if any(marker in lowered for marker in create_task_markers) or _create_task_fuzzy(lowered) or _proceed_with_task(lowered):
            raw_title = text.strip()
            for prefix in _HIVE_CREATE_PREFIXES:
                if raw_title.lower().startswith(prefix):
                    raw_title = raw_title[len(prefix):].strip().lstrip("-:–").strip()
                    break
            if "task:" in lowered:
                task_match = re.search(r"\btask\b\s*[:=-]\s*(.+?)(?=(?:\b(?:goal|summary)\b\s*[:=-])|(?:\b(?:topic tags?|tags?)\b\s*[:=-])|$)", text, re.IGNORECASE)
                if task_match is not None:
                    raw_title = str(task_match.group(1) or "").strip()
            title = _normalize_hive_title_candidate(raw_title[:180])
            if _is_generic_hive_title_candidate(title):
                recovered = _recover_hive_create_from_history(source_context)
                if recovered is None:
                    return WorkflowPlannerDecision(handled=False, reason="no_workflow_plan")
                title, recovered_summary = recovered
                summary = recovered_summary[:4000] or title
            else:
                summary = text.strip()[:4000] or title
            return WorkflowPlannerDecision(
                handled=True,
                reason="planned_hive_create_topic",
                next_payload={
                    "intent": "hive.create_topic",
                    "arguments": {"title": title, "summary": summary, "topic_tags": ["research"]},
                },
            )
        orchestrated_payload = _planned_orchestrated_operator_payload(
            user_text=raw_text,
            task_class=task_class,
            source_context=source_context,
            replacement=replacement,
            patch_text=patch_text,
            explicit_command=explicit_command,
        )
        if orchestrated_payload is not None:
            return WorkflowPlannerDecision(
                handled=True,
                reason="planned_orchestrated_operator_envelope",
                next_payload=orchestrated_payload,
            )
        if explicit_command and any(marker in lowered for marker in (" retry ", " then retry", " then rerun", " rerun ")):
            command_payload = _planned_command_payload(user_text=raw_text, command=explicit_command)
            if command_payload is not None:
                reason, next_payload = command_payload
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_diagnose_run" if reason == "planned_command_run" else reason,
                    next_payload=next_payload,
                )
        if replacement is not None:
            path = str(replacement.get("path") or "").strip()
            if path:
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_read_before_edit",
                    next_payload={"intent": "workspace.read_file", "arguments": {"path": path, "start_line": 1, "max_lines": 120}},
                )
            query = str(replacement.get("old_text") or "").strip()
            if query:
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_search_before_edit",
                    next_payload={"intent": "workspace.search_text", "arguments": {"query": query, "limit": 10}},
                )
        if public_entity_lookup:
            return WorkflowPlannerDecision(
                handled=True,
                reason="planned_entity_lookup_search",
                next_payload={"intent": "web.search", "arguments": {"query": entity_query or text, "limit": 4}},
            )
        if research_flow:
            return WorkflowPlannerDecision(
                handled=True,
                reason="planned_research_search",
                next_payload={"intent": "web.search", "arguments": {"query": research_text, "limit": 4}},
            )
        if explicit_command:
            command_payload = _planned_command_payload(user_text=raw_text, command=explicit_command)
            if command_payload is not None:
                reason, next_payload = command_payload
                return WorkflowPlannerDecision(handled=True, reason=reason, next_payload=next_payload)
        return WorkflowPlannerDecision(handled=False, reason="no_workflow_plan")

    last_observation = dict(last_step.get("observation") or {})
    hints = extract_observation_followup_hints(last_observation)

    if research_flow:
        if last_intent == "web.search":
            current_query = str(dict(last_step.get("arguments") or {}).get("query") or "").strip()
            result_count = int(hints.get("result_count") or 0)
            if public_entity_lookup and result_count <= 0:
                if entity_retry_query and entity_retry_query != current_query and not _workflow_step_exists(steps, "web.search", key="query", value=entity_retry_query):
                    return WorkflowPlannerDecision(
                        handled=True,
                        reason="planned_entity_lookup_retry",
                        next_payload={"intent": "web.search", "arguments": {"query": entity_retry_query, "limit": 4}},
                    )
                if not _workflow_step_exists(steps, "web.research"):
                    return WorkflowPlannerDecision(
                        handled=True,
                        reason="planned_entity_lookup_research",
                        next_payload={"intent": "web.research", "arguments": {"query": entity_retry_query or entity_query or research_text}},
                    )
            if not compare_or_verify and int(hints.get("result_count") or 0) >= 2:
                return WorkflowPlannerDecision(handled=True, reason="research_enough_after_search", stop_after=True)
            primary_url = str(hints.get("primary_url") or "").strip()
            if primary_url and not _workflow_step_exists(steps, "web.fetch", key="url", value=primary_url):
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_fetch_after_search",
                    next_payload={"intent": "web.fetch", "arguments": {"url": primary_url}},
                )
            if compare_or_verify and not _workflow_step_exists(steps, "web.research"):
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_verify_after_search",
                    next_payload={"intent": "web.research", "arguments": {"query": research_text}},
                )
            if public_entity_lookup and not _workflow_step_exists(steps, "web.research") and result_count < 2:
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_entity_lookup_verify",
                    next_payload={"intent": "web.research", "arguments": {"query": entity_retry_query or entity_query or research_text}},
                )
            return WorkflowPlannerDecision(handled=True, reason="research_stop_after_search", stop_after=True)
        if last_intent == "web.fetch":
            if compare_or_verify and not _workflow_step_exists(steps, "web.research"):
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_research_after_fetch",
                    next_payload={"intent": "web.research", "arguments": {"query": research_text}},
                )
            return WorkflowPlannerDecision(handled=True, reason="research_stop_after_fetch", stop_after=True)
        if last_intent == "web.research":
            return WorkflowPlannerDecision(handled=True, reason="research_stop_after_verify", stop_after=True)

    if last_intent == "workspace.search_text":
        path = str(hints.get("primary_path") or "").strip()
        line = int(hints.get("primary_line") or 0)
        candidate_paths = []
        for candidate in [path, *list(hints.get("paths") or [])]:
            normalized = str(candidate or "").strip()
            if normalized and normalized not in candidate_paths:
                candidate_paths.append(normalized)
        next_path = ""
        next_line = 0
        for candidate in candidate_paths:
            if _workflow_step_exists(steps, "workspace.read_file", key="path", value=candidate):
                continue
            next_path = candidate
            next_line = line if candidate == path else 0
            break
        if next_path:
            return WorkflowPlannerDecision(
                handled=True,
                reason="planned_read_after_search",
                next_payload={
                    "intent": "workspace.read_file",
                    "arguments": {
                        "path": next_path,
                        "start_line": max(1, next_line - 8) if next_line else 1,
                        "max_lines": 60,
                    },
                },
            )
        return WorkflowPlannerDecision(handled=True, reason="workspace_stop_after_search", stop_after=True)

    if last_intent == "workspace.symbol_search":
        path = str(hints.get("primary_path") or "").strip()
        line = int(hints.get("primary_line") or 0)
        candidate_paths = []
        for candidate in [path, *list(hints.get("paths") or [])]:
            normalized = str(candidate or "").strip()
            if normalized and normalized not in candidate_paths:
                candidate_paths.append(normalized)
        next_path = ""
        next_line = 0
        for candidate in candidate_paths:
            if _workflow_step_exists(steps, "workspace.read_file", key="path", value=candidate):
                continue
            next_path = candidate
            next_line = line if candidate == path else 0
            break
        if next_path:
            return WorkflowPlannerDecision(
                handled=True,
                reason="planned_read_after_symbol_search",
                next_payload={
                    "intent": "workspace.read_file",
                    "arguments": {
                        "path": next_path,
                        "start_line": max(1, next_line - 8) if next_line else 1,
                        "max_lines": 60,
                    },
                },
            )
        latest_validation_hints = _latest_failed_validation_hints(steps)
        diagnostic_query = str(latest_validation_hints.get("diagnostic_query") or "").strip()
        if diagnostic_query:
            fallback = _planned_diagnostic_lookup_followup(
                steps=steps,
                diagnostic_query=diagnostic_query,
                symbol_reason="planned_symbol_search_after_validation_inspection",
                search_reason="planned_search_after_symbol_search",
            )
            if fallback is not None and fallback.next_payload["intent"] == "workspace.search_text":
                return fallback
        return WorkflowPlannerDecision(handled=True, reason="workspace_stop_after_symbol_search", stop_after=True)

    if last_intent == "workspace.read_file":
        read_path = str(hints.get("path") or "").strip()
        latest_validation_hints = _latest_failed_validation_hints(steps)
        effective_replacement = None if _has_retryable_failed_envelope(steps) else replacement
        pending_writes = _pending_workspace_writes(workspace_file_plan or {}, steps)
        if workspace_file_plan is not None and pending_writes:
            next_write = dict(pending_writes[0] or {})
            if str(next_write.get("mode") or "").strip() == "append" and read_path == str(next_write.get("path") or "").strip():
                existing_content = str(hints.get("content") or "")
                if existing_content:
                    content = existing_content + ("\n" if not existing_content.endswith("\n") else "") + str(next_write.get("content") or "")
                else:
                    content = str(next_write.get("content") or "")
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_append_after_read",
                    next_payload={"intent": "workspace.write_file", "arguments": {"path": read_path, "content": content}},
                )
        if effective_replacement is not None:
            target_path = str(effective_replacement.get("path") or read_path).strip()
            old_text = str(effective_replacement.get("old_text") or "").strip()
            new_text = str(effective_replacement.get("new_text") or "").strip()
            if target_path and old_text and new_text and not _workflow_step_exists(steps, "workspace.replace_in_file", key="path", value=target_path):
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_edit_after_read",
                    next_payload={
                        "intent": "workspace.replace_in_file",
                        "arguments": {
                            "path": target_path,
                            "old_text": old_text,
                            "new_text": new_text,
                            "replace_all": True,
                        },
                    },
                )
        if effective_replacement is None and not patch_text and _can_plan_candidate_repair_envelope(steps):
            candidate_repair = _infer_literal_candidate_repair(
                user_text=user_text,
                steps=steps,
                current_read_hints=hints,
            )
            if candidate_repair is not None:
                validation_command = str(
                    explicit_command
                    or _latest_failed_validation_observation(steps).get("command")
                    or ""
                ).strip()
                orchestrated_payload = _planned_orchestrated_operator_payload(
                    user_text=user_text,
                    task_class=task_class,
                    source_context=source_context,
                    replacement=candidate_repair,
                    patch_text="",
                    explicit_command=validation_command,
                )
                if orchestrated_payload is not None:
                    return WorkflowPlannerDecision(
                        handled=True,
                        reason="planned_candidate_repair_after_validation_diagnosis",
                        next_payload=orchestrated_payload,
                    )
        latest_lookup_intent, latest_lookup_hints = _latest_lookup_hints(steps)
        if str(latest_validation_hints.get("diagnostic_query") or "").strip() and latest_lookup_hints:
            next_lookup_path, next_lookup_line = _next_unread_lookup_path(
                steps=steps,
                hints=latest_lookup_hints,
                current_path=read_path,
            )
            if next_lookup_path:
                return WorkflowPlannerDecision(
                    handled=True,
                    reason=(
                        "planned_next_read_after_symbol_diagnosis"
                        if latest_lookup_intent == "workspace.symbol_search"
                        else "planned_next_read_after_search_diagnosis"
                    ),
                    next_payload={
                        "intent": "workspace.read_file",
                        "arguments": {
                            "path": next_lookup_path,
                            "start_line": max(1, next_lookup_line - 8) if next_lookup_line else 1,
                            "max_lines": 60,
                        },
                    },
                )
        delegate_lookup = _planned_delegate_lookup_after_read(
            user_text=user_text,
            steps=steps,
            current_read_hints=hints,
        )
        if delegate_lookup is not None:
            return delegate_lookup
        if explicit_command and not (
            _workflow_step_exists(steps, "sandbox.run_command", key="command", value=explicit_command)
            or _workflow_validation_command_already_attempted(steps, explicit_command)
        ):
            command_payload = _planned_command_payload(user_text=user_text, command=explicit_command)
            if command_payload is not None:
                reason, next_payload = command_payload
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_command_after_read" if reason == "planned_command_run" else "planned_validation_after_read",
                    next_payload=next_payload,
                )
        diagnostic_query = str(latest_validation_hints.get("diagnostic_query") or "").strip()
        if diagnostic_query:
            lookup_followup = _planned_diagnostic_lookup_followup(
                steps=steps,
                diagnostic_query=diagnostic_query,
                symbol_reason="planned_symbol_search_after_validation_inspection",
                search_reason="planned_search_after_validation_inspection",
            )
            if lookup_followup is not None:
                return lookup_followup
        return WorkflowPlannerDecision(handled=True, reason="workspace_stop_after_read", stop_after=True)

    if last_intent == "workspace.ensure_directory":
        pending_writes = _pending_workspace_writes(workspace_file_plan or {}, steps)
        if workspace_file_plan is not None and pending_writes:
            next_write = dict(pending_writes[0] or {})
            if str(next_write.get("mode") or "").strip() == "append":
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_read_before_append",
                    next_payload={"intent": "workspace.read_file", "arguments": {"path": next_write["path"], "start_line": 1, "max_lines": 400, "verbatim": True}},
                )
            return WorkflowPlannerDecision(
                handled=True,
                reason="planned_workspace_write_after_bootstrap",
                next_payload={"intent": "workspace.write_file", "arguments": {"path": next_write["path"], "content": next_write["content"]}},
            )
        return WorkflowPlannerDecision(handled=True, reason="workspace_stop_after_directory_bootstrap", stop_after=True)

    if last_intent == "workspace.write_file":
        pending_writes = _pending_workspace_writes(workspace_file_plan or {}, steps)
        if workspace_file_plan is not None and pending_writes:
            next_write = dict(pending_writes[0] or {})
            if str(next_write.get("mode") or "").strip() == "append":
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_read_before_append",
                    next_payload={"intent": "workspace.read_file", "arguments": {"path": next_write["path"], "start_line": 1, "max_lines": 400, "verbatim": True}},
                )
            return WorkflowPlannerDecision(
                handled=True,
                reason="planned_workspace_next_write",
                next_payload={"intent": "workspace.write_file", "arguments": {"path": next_write["path"], "content": next_write["content"]}},
            )
        list_path = str((workspace_file_plan or {}).get("list_path") or "").strip()
        if list_path and not _workflow_step_exists(steps, "workspace.list_files", key="path", value=list_path):
            return WorkflowPlannerDecision(
                handled=True,
                reason="planned_workspace_list_after_write",
                next_payload={"intent": "workspace.list_files", "arguments": {"path": list_path, "limit": 200}},
            )
        if explicit_command and not (
            _workflow_step_exists(steps, "sandbox.run_command", key="command", value=explicit_command)
            or _workflow_validation_command_already_attempted(steps, explicit_command)
        ):
            command_payload = _planned_command_payload(user_text=user_text, command=explicit_command)
            if command_payload is not None:
                reason, next_payload = command_payload
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_command_after_write" if reason == "planned_command_run" else "planned_validation_after_write",
                    next_payload=next_payload,
                )
        return WorkflowPlannerDecision(handled=True, reason="workspace_stop_after_write", stop_after=True)

    if last_intent == "workspace.list_files":
        return WorkflowPlannerDecision(handled=True, reason="workspace_stop_after_list", stop_after=True)

    if last_intent == "machine.list_directory":
        return WorkflowPlannerDecision(handled=True, reason="machine_stop_after_list", stop_after=True)

    if last_intent == "machine.inspect_specs":
        return WorkflowPlannerDecision(handled=True, reason="machine_stop_after_specs", stop_after=True)

    if last_intent in {"workspace.git_status", "workspace.git_diff", "workspace.git_summary"}:
        return WorkflowPlannerDecision(handled=True, reason="workspace_stop_after_git_inspection", stop_after=True)

    if last_intent == "workspace.replace_in_file":
        retry_command = _last_command_from_steps(steps) or explicit_command
        if retry_command and not _workflow_retry_already_happened(steps, retry_command):
            command_payload = _planned_command_payload(user_text=user_text, command=retry_command)
            if command_payload is not None:
                _, next_payload = command_payload
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_retry_after_edit",
                    next_payload=next_payload,
                )
        return WorkflowPlannerDecision(handled=True, reason="workspace_stop_after_edit", stop_after=True)

    if last_intent == "orchestration.execute_envelope":
        _, envelope_validation_hints, rollback = _failed_validation_from_orchestration_step(last_step)
        if envelope_validation_hints and bool(rollback.get("ok", False)):
            return _planned_validation_failure_followup(
                steps=steps,
                hints=envelope_validation_hints,
                inspect_reason="planned_inspect_after_envelope_failure",
                symbol_reason="planned_symbol_search_after_envelope_failure",
                search_reason="planned_search_after_envelope_failure",
                stop_reason="orchestration_stop_after_failed_envelope",
            )
        return WorkflowPlannerDecision(handled=True, reason="orchestration_stop_after_envelope", stop_after=True)

    if last_intent == "sandbox.run_command":
        returncode = int(hints.get("returncode") or 0)
        if returncode == 0:
            return WorkflowPlannerDecision(handled=True, reason="command_stop_after_success", stop_after=True)
        error_path = str(hints.get("error_path") or "").strip()
        error_line = int(hints.get("error_line") or 0)
        if error_path and not _workflow_step_exists(steps, "workspace.read_file", key="path", value=error_path):
            return WorkflowPlannerDecision(
                handled=True,
                reason="planned_inspect_after_command_failure",
                next_payload={
                    "intent": "workspace.read_file",
                    "arguments": {
                        "path": error_path,
                        "start_line": max(1, error_line - 8) if error_line else 1,
                        "max_lines": 60,
                    },
                },
            )
        if replacement is not None:
            target_path = str(replacement.get("path") or "").strip()
            if target_path and not _workflow_step_exists(steps, "workspace.read_file", key="path", value=target_path):
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_explicit_inspect_after_command_failure",
                    next_payload={"intent": "workspace.read_file", "arguments": {"path": target_path, "start_line": 1, "max_lines": 120}},
                )
        diagnostic_query = str(hints.get("diagnostic_query") or "").strip()
        if diagnostic_query:
            lookup_followup = _planned_diagnostic_lookup_followup(
                steps=steps,
                diagnostic_query=diagnostic_query,
                symbol_reason="planned_symbol_search_after_command_failure",
                search_reason="planned_search_after_command_failure",
            )
            if lookup_followup is not None:
                return lookup_followup
        return WorkflowPlannerDecision(handled=True, reason="command_stop_after_failure", stop_after=True)

    if last_intent in {"workspace.run_tests", "workspace.run_lint", "workspace.run_formatter"}:
        returncode = int(hints.get("returncode") or 0)
        if returncode == 0:
            return WorkflowPlannerDecision(handled=True, reason="validation_stop_after_success", stop_after=True)
        return _planned_validation_failure_followup(
            steps=steps,
            hints=hints,
            inspect_reason="planned_inspect_after_validation_failure",
            symbol_reason="planned_symbol_search_after_validation_failure",
            search_reason="planned_search_after_validation_failure",
            stop_reason="validation_stop_after_failure",
        )

    return WorkflowPlannerDecision(handled=False, reason="no_followup_plan")
