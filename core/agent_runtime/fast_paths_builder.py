from __future__ import annotations

import re
from typing import Any


def looks_like_builder_request(lowered: str) -> bool:
    text = " ".join(str(lowered or "").split()).strip().lower()
    if not text:
        return False
    build_markers = (
        "build",
        "create",
        "scaffold",
        "implement",
        "generate",
        "start working",
        "start coding",
        "start putting code",
        "put code",
        "putting code",
        "setup folder",
        "set up folder",
        "setup directory",
        "set up directory",
        "bootstrap",
        "initial files",
        "starter files",
        "write the files",
        "create the files",
        "generate the code",
    )
    design_markers = (
        "design",
        "architecture",
        "best practice",
        "best practices",
        "framework",
        "stack",
    )
    source_markers = (
        "github",
        "repo",
        "repos",
        "docs",
        "documentation",
        "official docs",
    )
    return (
        any(marker in text for marker in build_markers)
        or (
            any(marker in text for marker in design_markers)
            and any(marker in text for marker in source_markers)
        )
    )


def looks_like_generic_workspace_bootstrap_request(agent: Any, lowered: str) -> bool:
    text = " ".join(str(lowered or "").split()).strip().lower()
    if not text:
        return False
    bootstrap_markers = (
        "start coding",
        "start putting code",
        "start building",
        "start creating",
        "put code",
        "putting code",
        "building the code",
        "build the code",
        "initial files",
        "starter files",
        "bootstrap",
        "set up",
        "setup",
        "write the files",
        "create the files",
        "generate the files",
        "generate the code",
        "start working",
        "launch local",
        "launch localhost",
        "run locally",
    )
    target_markers = (
        "folder",
        "directory",
        "dir",
        "src/",
        "/src",
        "api/",
    )
    return bool(
        any(marker in text for marker in bootstrap_markers)
        and (any(marker in text for marker in target_markers) or bool(agent._extract_requested_builder_root(text)))
    )


def looks_like_explicit_workspace_file_request(query_text: str) -> bool:
    text = f" {' '.join(str(query_text or '').split()).strip().lower()} "
    if not text.strip():
        return False
    text = re.sub(
        r"(?P<stem>[A-Za-z0-9_./-]+)\.\s+(?P<ext>py|js|ts|tsx|jsx|txt|md|json|yaml|yml|toml)\b",
        r"\g<stem>.\g<ext>",
        text,
    )
    file_name_markers = (".txt", ".md", ".json", ".yaml", ".yml", ".toml", ".py", ".ts", ".js")
    file_action_markers = (
        " create a file",
        " create file",
        " create ",
        " file named",
        " with exact text",
        " with exactly this content",
        " with exactly this code",
        " with this content",
        " with this code",
        " saying ",
        " that says:",
        " append ",
        " add one more line exactly",
        " overwrite ",
        " update ",
        " edit ",
        " change ",
        " patch ",
        " read the whole file",
        " read the file",
        " readback",
        " read it back",
        " read it exactly",
        " exactly three files",
        " inside it create",
        " inside this workspace create",
        " do not create anything else",
        " overwrite only",
        " respectively ",
        " list the folder contents",
        " list the directory contents",
        " list folder contents",
    )
    return (
        any(marker in text for marker in file_action_markers)
        and (" file" in text or " files" in text or any(marker in text for marker in file_name_markers))
    )


def looks_like_exact_workspace_readback_request(query_text: str) -> bool:
    text = f" {' '.join(str(query_text or '').split()).strip().lower()} "
    text = re.sub(r"[.!?]+", " ", text)
    if not text.strip():
        return False
    return any(
        marker in text
        for marker in (
            " read the whole file back exactly ",
            " read the file back exactly ",
            " read back exactly ",
            " readback exactly ",
            " read the whole file exactly ",
        )
    )


def extract_requested_builder_root(query_text: str) -> str:
    text = " ".join(str(query_text or "").split()).strip()
    if not text:
        return ""
    stop_words = {
        "a",
        "an",
        "the",
        "and",
        "folder",
        "directory",
        "dir",
        "path",
        "workspace",
        "repo",
        "repository",
        "this",
        "that",
        "there",
        "here",
        "code",
        "files",
    }
    patterns = (
        re.compile(
            r"\b(?:in|under|inside)\s+[`\"']?(?P<path>[A-Za-z]:[^\r\n]+?)(?=\s+(?:create|make|write|add|put|place|save|read|list|with)\b|$)",
            re.IGNORECASE,
        ),
        re.compile(r"\bnam(?:e|ed)\s+it\s+[`\"']?(?P<path>[A-Za-z0-9_./-]+(?:/[A-Za-z0-9_./-]+)*)", re.IGNORECASE),
        re.compile(r"\b(?:folder|directory|dir|path)\s+(?:called|named)\s+[`\"']?(?P<path>[A-Za-z0-9_./-]+)", re.IGNORECASE),
        re.compile(r"\b(?:called|named)\s+[`\"']?(?P<path>[A-Za-z0-9_][A-Za-z0-9_./-]*(?:/[A-Za-z0-9_./-]+)*)", re.IGNORECASE),
        re.compile(
            r"\b(?:create|make|setup|set up|bootstrap|mkdir)\s+(?:a|an|the)?\s*(?:folder|directory|dir|path)\s+(?:called|named)?\s*[`\"']?(?P<path>[A-Za-z0-9_./-]+(?:/[A-Za-z0-9_./-]+)*)",
            re.IGNORECASE,
        ),
        re.compile(r"\b(?:in|under|inside)\s+[`\"']?(?P<path>[A-Za-z0-9_./-]+(?:/[A-Za-z0-9_./-]+)*)[`\"']?", re.IGNORECASE),
    )
    for pattern in patterns:
        match = pattern.search(text)
        if not match:
            continue
        candidate = str(match.group("path") or "").strip().strip("`\"'").rstrip(".,!?")
        if not candidate:
            continue
        if candidate.startswith("/"):
            candidate = candidate.lstrip("/")
        candidate = candidate.lstrip("./")
        if not candidate or candidate.lower() in stop_words:
            continue
        if ".." in candidate.split("/"):
            continue
        return candidate
    return ""
