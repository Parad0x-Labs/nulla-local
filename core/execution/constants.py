from __future__ import annotations

import re

_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_LIVE_LOOKUP_MARKERS = (
    "latest",
    "current",
    "today",
    "recent",
    "release notes",
    "version",
    "status page",
    "search online",
    "check online",
    "look up",
    "fetch",
    "pull",
    "open",
    "browse",
    "render",
    "show me",
    "on x",
    "on twitter",
    "on the web",
    "on web",
    "google",
    "find",
    "check",
)
_LOCAL_TOOL_MARKERS = (
    "process",
    "service",
    "disk",
    "space",
    "cleanup",
    "clean temp",
    "move",
    "archive",
    "calendar",
    "meeting",
    "schedule",
    "tool",
    "folder",
    "directory",
    "mkdir",
    "desktop",
    "downloads",
    "documents",
    "machine",
    "hardware",
    "specs",
    "cpu",
    "cores",
    "ram",
    "memory",
    "gpu",
    "vram",
    "chip",
)
_TOOL_INVENTORY_MARKERS = (
    "list tools",
    "show tools",
    "what tools do you have",
    "what can you execute",
    "what actions can you take",
    "what tools do you need",
    "which tools do you need",
    "what would you use",
)
_SELF_TOOL_REQUEST_MARKERS = (
    "create your own tool",
    "create your own tools",
    "make your own tool",
    "make your own tools",
    "build your own tool",
    "build your own tools",
    "register a new tool",
    "register new tools",
)
_DIRECTORY_CREATE_MARKERS = (
    "create folder",
    "create a folder",
    "create new folder",
    "create a new folder",
    "create directory",
    "create a directory",
    "create new directory",
    "create a new directory",
    "make folder",
    "make a folder",
    "set up folder",
    "setup folder",
    "set up directory",
    "setup directory",
    "mkdir",
)
_START_CODE_MARKERS = (
    "start coding",
    "start putting code",
    "put code",
    "putting code",
    "write the initial files",
    "initial files",
    "starter files",
    "bootstrap",
)
_NAMED_PATH_RE = re.compile(
    r"(?:named?|called|call)\s+(?:it\s+)?[`\"']?(?P<path>[A-Za-z0-9_./-]+(?:/[A-Za-z0-9_./-]+)*)[`\"']?",
    re.IGNORECASE,
)
_VERB_NAME_FOLDER_RE = re.compile(
    r"\b(?:create|make|crate|creat|mkdir)\s+(?:the\s+|a\s+|an\s+)?(?P<path>[A-Za-z0-9_./-]+(?:/[A-Za-z0-9_./-]+)*)\s+(?:folder|directory|dir)\b",
    re.IGNORECASE,
)
_FOLDER_PATH_RE = re.compile(
    r"\b(?:folder|directory|dir|path)\s+(?:called|named)?\s*[`\"']?(?P<path>[A-Za-z0-9_./-]+)",
    re.IGNORECASE,
)
_CREATE_PATH_RE = re.compile(
    r"\b(?:create|make|setup|set up|bootstrap|mkdir)\s+(?P<path>[A-Za-z0-9_./-]+(?:/[A-Za-z0-9_./-]+)*)\b",
    re.IGNORECASE,
)
_INTO_PATH_RE = re.compile(
    r"\b(?:in|under|inside)\s+[`\"']?(?P<path>[A-Za-z0-9_./-]+(?:/[A-Za-z0-9_./-]+)*)[`\"']?",
    re.IGNORECASE,
)
_WORKSPACE_FILE_RE = r"[A-Za-z0-9_./-]+\.[A-Za-z0-9_+-]+"
_CREATE_NAMED_FILE_WITH_CONTENT_RE = re.compile(
    rf"\bcreate\s+(?:a\s+)?file(?:\s+named)?\s+[`\"']?(?P<path>{_WORKSPACE_FILE_RE})[`\"']?(?:\s+in\s+[^:]+?)?\s+with(?:\s+exactly)?(?:\s+(?:this|the))?\s+(?:line|content|code)(?:(?:\s*,?\s*[^:\n.]+?)\s*:|:\s*|\s+)(?P<content>.+)$",
    re.IGNORECASE | re.DOTALL,
)
_INLINE_CREATE_FILE_RE = re.compile(
    rf"\bcreate\s+(?:a\s+file(?:\s+named)?\s+)?[`\"']?(?P<path>{_WORKSPACE_FILE_RE})[`\"']?\s+(?:with(?:\s+exactly)?(?:\s+(?:this|the))?(?:\s+(?:line|content|code))(?:(?:\s*,?\s*[^:\n.]+?)\s*:|:\s*|\s+)|that\s+says:)\s*(?P<content>.+?)(?=(?:\.\s*(?:Then|Now|Inside it|Do not)\b)|$)",
    re.IGNORECASE | re.DOTALL,
)
_FOLDER_FIRST_CREATE_FILE_RE = re.compile(
    rf"\b(?:pls\s+)?(?:make|create|setup|set up)\s+(?:a\s+)?folder\s+(?P<directory>[A-Za-z0-9_./-]+)"
    r"(?:\s+(?:here|in\s+this\s+workspace))?"
    r"\s+(?:and\s+)?(?:inside\s+it\s+)?(?:save|put|write|create)\s+[`\"']?(?P<path>"
    rf"{_WORKSPACE_FILE_RE})[`\"']?(?:\s+inside)?\s+with(?:\s+exact(?:ly)?)?(?:\s+(?:this|the))?\s+text\s*:\s*(?P<content>.+)$",
    re.IGNORECASE | re.DOTALL,
)
_IN_WORKSPACE_CREATE_FILE_RE = re.compile(
    r"\binside\s+this\s+workspace\s+create\s+[`\"']?(?P<path>[^`\"']+?\.[A-Za-z0-9_+-]+)[`\"']?"
    r"\s+with(?:\s+exact(?:ly)?)?(?:\s+(?:this|the))?\s+text\s*:\s*(?P<content>.+)$",
    re.IGNORECASE | re.DOTALL,
)
_FILE_IN_FOLDER_SAYING_RE = re.compile(
    r"\bcreate\s+(?:a\s+)?file\s+[`\"']?(?P<path>[^`\"']+?\.[A-Za-z0-9_+-]+)[`\"']?"
    r"\s+in\s+(?:the\s+)?(?P<directory>[A-Za-z0-9 _./-]+?)\s+folder\s+saying\s+(?P<content>.+)$",
    re.IGNORECASE | re.DOTALL,
)
_APPEND_FILE_RE = re.compile(
    rf"\bappend(?:\s+a)?(?:\s+\w+)?\s+line\s+to\s+[`\"']?(?P<path>{_WORKSPACE_FILE_RE})[`\"']?\s*:\s*(?P<content>.+)$",
    re.IGNORECASE | re.DOTALL,
)
_APPEND_CONTENT_ONLY_RE = re.compile(
    r"\b(?:append|add)\s+(?:(?:a|one)\s+more\s+|another\s+|a\s+second\s+|second\s+)?line(?:\s+exactly)?\s*:?\s*(?P<content>.+)$",
    re.IGNORECASE | re.DOTALL,
)
_OVERWRITE_FILE_RE = re.compile(
    rf"\boverwrite(?:\s+only)?\s+[`\"']?(?P<path>{_WORKSPACE_FILE_RE})[`\"']?\s+with\s+(?P<content>.+)$",
    re.IGNORECASE | re.DOTALL,
)
_CREATE_EXACT_FILES_RE = re.compile(
    rf"\bcreate\s+exactly\s+\w+\s+files:\s*(?P<paths>{_WORKSPACE_FILE_RE}(?:\s*,\s*{_WORKSPACE_FILE_RE})+)\.\s*put\s+(?P<contents>.+?)\s+respectively\b",
    re.IGNORECASE | re.DOTALL,
)
_EXPLICIT_WORKSPACE_READ_RE = re.compile(
    r"\b(?:read|open|quote)\s+(?:the\s+file\s+)?[`\"']?(?P<path>[^`\"']+?\.[A-Za-z0-9_+-]+)[`\"']?"
    r"(?:\s+back)?\s+(?:exactly|verbatim)\b",
    re.IGNORECASE | re.DOTALL,
)
_EXACT_READBACK_RE = re.compile(
    r"\b(?:read(?:\s+(?:the\s+whole\s+file|the\s+file|it))?\s+back\s+exactly(?:\s+first)?|read(?:\s+it)?\s+exactly(?:\s+first)?|quote(?:\s+it)?\s+exactly)\b",
    re.IGNORECASE,
)
_PATH_STOP_WORDS = {
    "a",
    "an",
    "the",
    "for",
    "me",
    "my",
    "this",
    "that",
    "it",
    "on",
    "in",
    "folder",
    "directory",
    "dir",
    "path",
    "workspace",
    "repo",
    "repository",
    "there",
    "here",
    "code",
    "files",
    "machine",
    "computer",
    "desktop",
    "please",
    "pls",
}
_BUILDER_RESEARCH_MARKERS = (
    "build",
    "design",
    "architecture",
    "best practice",
    "best practices",
    "framework",
    "stack",
    "github",
    "repo",
    "repos",
    "docs",
    "documentation",
    "compare",
    "example",
    "examples",
)
_INTEGRATION_DOMAIN_MARKERS = (
    "telegram",
    "discord",
    "bot",
    "api",
    "integration",
    "webhook",
)
_HIVE_ACTION_PATTERNS = (
    "claim task",
    "claim this task",
    "claim topic",
    "take this task",
    "take this topic",
    "create topic",
    "create task",
    "create new task",
    "create hive mind task",
    "create hive task",
    "new task",
    "add task",
    "add to hive",
    "add to the hive",
    "open topic",
    "post progress",
    "update progress",
    "submit result",
    "submit findings",
    "submit verdict",
    "research packet",
    "research queue",
    "search artifacts",
    "research this topic",
)
_ENTITY_LOOKUP_DROP_TOKENS = frozenset(
    {
        "who",
        "is",
        "he",
        "she",
        "they",
        "them",
        "tell",
        "me",
        "about",
        "what",
        "do",
        "you",
        "know",
        "check",
        "find",
        "look",
        "up",
        "lookup",
        "search",
        "google",
        "in",
        "on",
        "the",
        "web",
        "pls",
        "please",
    }
)
_ENTITY_LOOKUP_KEEP_SHORT_TOKENS = frozenset({"x", "ai"})
_READ_ONLY_OPERATOR_INTENTS = {
    "operator.list_tools",
    "operator.inspect_processes",
    "operator.inspect_services",
    "operator.inspect_disk_usage",
}
_MUTATING_OPERATOR_INTENTS = {
    "operator.cleanup_temp_files",
    "operator.move_path",
    "operator.schedule_calendar_event",
}
_WEB_TOOL_INTENTS = {
    "web.search",
    "web.fetch",
    "web.research",
    "browser.render",
}
_PAYMENT_TOOL_INTENTS = {
    "pay.x402",
    "sell.quote",
}
_HIVE_TOOL_INTENTS = {
    "hive.list_available",
    "hive.list_research_queue",
    "hive.export_research_packet",
    "hive.search_artifacts",
    "hive.research_topic",
    "hive.create_topic",
    "hive.claim_task",
    "hive.post_progress",
    "hive.submit_result",
    "nullabook.get_profile",
    "nullabook.update_profile",
}
_SUPPORTED_OPERATOR_TOOL_IDS = {
    "list_tools",
    "inspect_processes",
    "inspect_services",
    "inspect_disk_usage",
    "cleanup_temp_files",
    "move_path",
    "schedule_calendar_event",
}
_CAPABILITY_QUERY_PREFIXES = (
    "can you ",
    "could you ",
    "are you able to ",
    "do you have a way to ",
    "do you know how to ",
    "are you wired to ",
)
_IMPOSSIBLE_REQUEST_MARKERS = (
    "read my mind",
    "mind read",
    "teleport",
    "physically cook",
    "cook dinner",
    "taste this",
    "smell this",
    "touch this",
    "be physically there",
    "drive over",
    "hack a bank",
    "steal a password",
)
_PARTIAL_BUILD_MARKERS = (
    "full app",
    "entire app",
    "end to end app",
    "end-to-end app",
    "full product",
    "ship the whole app",
    "ios app",
    "android app",
    "mobile app",
)
_SWARM_DELEGATION_MARKERS = (
    "talk to other agents",
    "delegate to other agents",
    "delegate this to agents",
    "helper lane",
    "merge helper outputs",
    "swarm delegates",
    "other hive agents",
)
_EMAIL_SEND_MARKERS = (
    "send email",
    "send an email",
    "email this",
    "mail this",
    "reply by email",
)
_NEARBY_CAPABILITY_IDS = {
    "workspace.read": ["web.live_lookup"],
    "workspace.write": ["workspace.read", "sandbox.command"],
    "sandbox.command": ["workspace.read", "workspace.write"],
    "hive.write": ["hive.read"],
    "operator.discord_post": ["operator.telegram_send"],
    "operator.telegram_send": ["operator.discord_post"],
    "workspace.build_scaffold": ["workspace.write", "sandbox.command"],
}
_HIVE_CREATE_PREFIXES = (
    "create hive mind task",
    "create hive task",
    "create new task for research",
    "create new task for",
    "create new task",
    "create task for research",
    "create task for",
    "create task",
    "new task for research",
    "new task for",
    "new task",
    "add to the hive a new task",
    "add to hive a new task",
    "add to the hive",
    "add to hive",
    "add task",
    "create these tasks",
    "create them",
    "create these",
    "yes create",
    "yes create them",
    "do all and start working",
    "proceed with",
    "do it",
    "do all",
    "start working",
    "go ahead",
    "carry on",
)
_GENERIC_HIVE_TITLE_MARKERS = {
    "",
    "it",
    "them",
    "these",
    "this",
    "task",
    "tasks",
    "topic",
    "topics",
    "hive task",
    "hive tasks",
    "hive topic",
    "hive topics",
    "the task",
    "this task",
    "these tasks",
    "create task",
    "create tasks",
    "creating task",
    "creating tasks",
    "new task",
    "new tasks",
    "on hive",
    "on the hive",
    "on hive mind",
}
