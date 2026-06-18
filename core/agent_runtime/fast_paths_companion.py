from __future__ import annotations

from typing import Any

from core.persistent_memory import (
    load_operator_dense_profile,
    search_relevant_memory,
    search_session_summaries,
    search_user_heuristics,
)
from core.web0_tools import web0_open_builder_draft

_LOCAL_PRODUCT_TOPICS = ("web0", "nulla", "openclaw", "context-capsule", "liquefy")
_LOCAL_PRODUCT_QUESTION_MARKERS = (
    "tell me about",
    "what is",
    "what's",
    "explain",
    "describe",
    "who are you",
)
_LIVE_LOOKUP_MARKERS = (
    "latest",
    "current",
    "today",
    "news",
    "price now",
    "weather",
    "search online",
    "check online",
    "look up",
    "browse",
)


def maybe_handle_companion_memory_fast_path(
    agent: Any,
    user_input: str,
    *,
    session_id: str,
    source_context: dict[str, object] | None,
) -> dict[str, Any] | None:
    source_surface = str((source_context or {}).get("surface", "cli")).lower()
    if source_surface not in {"channel", "openclaw", "api"}:
        return None
    clean = " ".join(str(user_input or "").split()).strip()
    if not clean:
        return None
    lowered = clean.lower()
    product_memory = render_local_product_memory_answer(query_text=clean, lowered=lowered)
    if product_memory:
        return agent._fast_path_result(
            session_id=session_id,
            user_input=clean,
            response=product_memory,
            confidence=0.9,
            source_context=source_context,
            reason="local_product_memory",
        )

    profile = load_operator_dense_profile()
    if not profile:
        return None

    if looks_like_companion_continuation_request(lowered):
        response = render_companion_continuation_response(
            session_id=session_id,
            query_text=clean,
            profile=profile,
        )
        if response:
            return agent._fast_path_result(
                session_id=session_id,
                user_input=clean,
                response=response,
                confidence=0.86,
                source_context=source_context,
                reason="companion_memory_continuation",
            )

    if looks_like_personalized_plan_request(lowered):
        response = render_personalized_plan_response(
            query_text=clean,
            profile=profile,
        )
        if response:
            return agent._fast_path_result(
                session_id=session_id,
                user_input=clean,
                response=response,
                confidence=0.83,
                source_context=source_context,
                reason="companion_memory_personalization",
            )
    return None


def maybe_handle_web0_builder_fast_path(
    agent: Any,
    user_input: str,
    *,
    session_id: str,
    source_context: dict[str, object] | None,
) -> dict[str, Any] | None:
    clean = " ".join(str(user_input or "").split()).strip()
    if not clean or not looks_like_web0_builder_request(clean.lower()):
        return None
    draft = web0_open_builder_draft(
        _web0_builder_title(clean),
        _web0_builder_code(clean),
    )
    builder_url = str(draft.get("builder_url") or "").strip()
    if not builder_url:
        return None
    return agent._fast_path_result(
        session_id=session_id,
        user_input=clean,
        response=(
            "Web0 local builder draft ready:\n"
            f"{builder_url}\n\n"
            "This opens in the local Web0 editor. Publishing to Arweave/mainnet still needs an explicit publish command and wallet confirmation."
        ),
        confidence=0.95,
        source_context=source_context,
        reason="web0_builder_fast_path",
        runtime_event_details={
            "web0_builder": {
                "builder_url": builder_url,
                "template": str(draft.get("template") or ""),
                "title": str(draft.get("title") or ""),
                "domain": str(draft.get("domain") or ""),
            }
        },
    )


def looks_like_web0_builder_request(lowered: str) -> bool:
    text = str(lowered or "")
    if "web0" not in text and "web0.null" not in text:
        return False
    build_markers = ("build", "create", "make", "generate", "draft", "open", "launch")
    web_markers = ("website", "site", "page", "landing", "builder", "web")
    return any(marker in text for marker in build_markers) and any(marker in text for marker in web_markers)


def _web0_builder_title(user_text: str) -> str:
    lowered = str(user_text or "").lower()
    if "hello" in lowered:
        return "Hello, Web0"
    if "nully" in lowered:
        return "Nully Web0 draft"
    return "NULLA-built Web0 draft"


def _web0_builder_code(user_text: str) -> str:
    title = _web0_builder_title(user_text)
    return f"""<style>
body{{margin:0;min-height:100vh;background:radial-gradient(circle at 30% 15%,#063c6e 0,#02050a 44%,#000 100%);color:#e8fbff;font-family:ui-sans-serif,system-ui,sans-serif;overflow:hidden}}
.wrap{{max-width:920px;margin:0 auto;padding:15vh 32px;position:relative}}
.badge{{display:inline-flex;border:1px solid rgba(86,231,255,.35);border-radius:999px;padding:8px 12px;color:#56e7ff;font:700 12px ui-monospace,monospace;letter-spacing:.14em;text-transform:uppercase;background:rgba(2,8,18,.55)}}
h1{{font-size:clamp(56px,9vw,122px);line-height:.82;letter-spacing:-.07em;margin:24px 0 18px}}
.rent{{text-decoration:line-through;color:#708394}}
.web0{{color:#4fffbf;text-shadow:0 0 34px rgba(79,255,191,.35)}}
p{{max-width:650px;color:#b8cad3;font-size:20px;line-height:1.55}}
.orb{{position:absolute;right:8%;top:16%;width:190px;height:190px;border-radius:44% 56% 52% 48%;background:linear-gradient(135deg,#0858ff,#59f2ff);filter:drop-shadow(0 0 42px #117dff);opacity:.78;animation:float 5s ease-in-out infinite}}
@keyframes float{{50%{{transform:translateY(22px) rotate(8deg)}}}}
</style>
<main class="wrap">
  <div class="orb"></div>
  <div class="badge">NULLA local draft -> Web0 builder</div>
  <h1>{title}<br><span class="rent">with rent</span><br><span class="web0">without landlords.</span></h1>
  <p>Web0 pages are wallet-owned, permanent, and agent-callable. This draft is local-only until you explicitly publish.</p>
</main>"""


def render_local_product_memory_answer(*, query_text: str, lowered: str) -> str:
    if not looks_like_local_product_memory_request(lowered):
        return ""
    hits = search_relevant_memory(query_text, limit=6)
    hit_text = "\n".join(str(row.get("text") or "") for row in hits)
    if "web0" in lowered and "web0" in hit_text.lower():
        return (
            "- Web0 is Parad0x Labs' local-first/private web stack.\n"
            "- Its local memory describes .null identity/names, durable content, x402-style payments, "
            "Dark Null discovery, NULLA agent nodes, and OpenClaw operations.\n"
            "- I should answer Web0 questions from sanitized local memory first, without exposing private paths, "
            "credentials, repo internals, or raw logs."
        )
    return ""


def looks_like_local_product_memory_request(lowered: str) -> bool:
    text = str(lowered or "")
    if not any(topic in text for topic in _LOCAL_PRODUCT_TOPICS):
        return False
    if any(marker in text for marker in _LIVE_LOOKUP_MARKERS):
        return False
    return any(marker in text for marker in _LOCAL_PRODUCT_QUESTION_MARKERS)


def looks_like_companion_continuation_request(lowered: str) -> bool:
    markers = (
        "where we left off",
        "where we left it",
        "pick up where",
        "you know the project",
        "continue from",
    )
    return sum(1 for marker in markers if marker in str(lowered or "")) >= 1


def looks_like_personalized_plan_request(lowered: str) -> bool:
    text = str(lowered or "")
    if "bot" not in text and "agent" not in text and "service" not in text:
        return False
    return any(marker in text for marker in ("sketch", "outline", "plan", "approach"))


def render_companion_continuation_response(
    *,
    session_id: str,
    query_text: str,
    profile: dict[str, Any],
) -> str:
    active_projects = [str(item).strip() for item in list(profile.get("active_projects") or []) if str(item).strip()]
    source_prefs = {str(item).strip().lower() for item in list(profile.get("source_preferences") or [])}
    preferred_stacks = [str(item).strip() for item in list(profile.get("preferred_stacks") or []) if str(item).strip()]
    topic_hints = [project.replace(" build", "").lower() for project in active_projects[:2]]
    query_seed = " ".join([query_text, *active_projects, *preferred_stacks]).strip()
    summaries = search_session_summaries(
        query_seed or query_text,
        topic_hints=topic_hints,
        limit=2,
        exclude_session_id=session_id,
    )
    summary_text = str((summaries[0] if summaries else {}).get("summary") or "").strip()
    project_label = active_projects[0] if active_projects else "current project"
    if project_label == "Telegram bot build":
        lead = "Continuing the Telegram bot build."
    elif project_label == "OpenClaw/NULLA runtime work":
        lead = "Continuing the OpenClaw/NULLA runtime work."
    else:
        lead = f"Continuing the {project_label.lower()}."
    preference_bits: list[str] = []
    if preferred_stacks:
        preference_bits.append(preferred_stacks[0].upper() if len(preferred_stacks[0]) <= 4 else preferred_stacks[0])
    if "official_docs_first" in source_prefs:
        preference_bits.append("official docs first")
    if "github_references" in source_prefs:
        preference_bits.append("strong GitHub references after the docs")
    middle = ""
    if preference_bits:
        middle = "Working memory says: " + ", ".join(preference_bits) + "."
    if not summary_text and not active_projects:
        return ""
    next_step = dense_memory_next_step(
        project_label=project_label,
        summary_text=summary_text,
        preferred_stack=preferred_stacks[0] if preferred_stacks else "",
    )
    parts = [lead]
    if middle:
        parts.append(middle)
    if summary_text:
        parts.append(f"Latest carried context: {summary_text[:220]}.")
    if next_step:
        parts.append(f"Next step: {next_step}")
    return " ".join(part.strip() for part in parts if part.strip())


def render_personalized_plan_response(*, query_text: str, profile: dict[str, Any]) -> str:
    del profile
    heuristics = search_user_heuristics(query_text, topic_hints=[], limit=6)
    source_prefs = {str(item.get("signal") or "").strip().lower() for item in heuristics if str(item.get("category") or "") == "source_preference"}
    stacks = [str(item.get("signal") or "").strip().lower() for item in heuristics if str(item.get("category") or "") == "preferred_stack"]
    style_signals = {str(item.get("signal") or "").strip().lower() for item in heuristics if str(item.get("category") or "") == "response_style"}
    if not source_prefs and not stacks and not style_signals:
        return ""
    lines: list[str] = []
    if "official_docs" in source_prefs:
        lines.append("Official docs first.")
    if stacks:
        lines.append(f"Use {stacks[0]} as the baseline stack.")
    if "github_repos" in source_prefs:
        lines.append("Pull 1-2 strong GitHub repos only after the docs, as implementation references.")
    lines.append("Build the smallest working bot loop, then test the core flow end to end.")
    if "concise_direct" not in style_signals and "brutal_honest" not in style_signals:
        return " ".join(lines)
    return "\n".join(lines[:4])


def dense_memory_next_step(*, project_label: str, summary_text: str, preferred_stack: str) -> str:
    lowered_project = str(project_label or "").lower()
    lowered_summary = str(summary_text or "").lower()
    stack = str(preferred_stack or "").strip().lower()
    if "telegram" in lowered_project or "telegram" in lowered_summary or "bot" in lowered_summary:
        stack_text = f"{stack} " if stack else ""
        return f"lock the {stack_text}bot skeleton, verify the command flow against the official docs, then run an end-to-end smoke."
    if "runtime" in lowered_project or "openclaw" in lowered_project or "nulla" in lowered_project:
        return "inspect the current failing runtime surface, verify it against live state, then patch and retest."
    if summary_text:
        return summary_text[:180]
    return ""
