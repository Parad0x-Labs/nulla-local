"""Ollama-compatible NULLA API entrypoint with an ASGI app factory.

The runtime bootstrap and route logic now live under ``core.web.api``.
This module remains the stable facade for callers and tests that still
import legacy helpers from ``apps.nulla_api_server``.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _prioritize_project_root_on_sys_path(*, project_root: Path = PROJECT_ROOT) -> None:
    root_text = str(project_root)
    with contextlib.suppress(ValueError):
        sys.path.remove(root_text)
    sys.path.insert(0, root_text)


if __package__ in {None, ""}:
    _prioritize_project_root_on_sys_path()

from core.nulla_workstation_ui import NULLA_WORKSTATION_DEPLOYMENT_VERSION
from core.runtime_capabilities import runtime_capability_snapshot
from core.runtime_provider_defaults import default_runtime_model_tag
from core.web.api.app import create_api_app
from core.web.api.runtime import (
    MODEL_NAME,
    RuntimeServices,
    bootstrap_runtime_services,
)
from core.web.api.runtime import (
    daemon_runtime_config as _daemon_runtime_config_impl,
)
from core.web.api.runtime import (
    default_workspace_root as _default_workspace_root_impl,
)
from core.web.api.runtime import (
    ensure_default_provider as _ensure_default_provider_impl,
)
from core.web.api.runtime import (
    format_runtime_event_text as _format_runtime_event_text_impl,
)
from core.web.api.runtime import (
    normalize_chat_history as _normalize_chat_history_impl,
)
from core.web.api.runtime import (
    parameter_count_for_model as _parameter_count_for_model_impl,
)
from core.web.api.runtime import (
    parameter_size_for_model as _parameter_size_for_model_impl,
)
from core.web.api.runtime import (
    run_agent as _run_agent_impl,
)
from core.web.api.runtime import (
    stable_openclaw_session_id as _stable_openclaw_session_id_impl,
)
from core.web.api.runtime import (
    stream_agent_with_events as _stream_agent_with_events_impl,
)
from core.web.api.service import apply_runtime_headers, dispatch_get, dispatch_post, json_response

logger = logging.getLogger("nulla.api")

NULLA_API_PORT = 11435

_runtime_services: RuntimeServices | None = None
_agent = None
_daemon = None


def _sync_runtime_aliases(runtime: RuntimeServices) -> None:
    global _agent, _daemon
    _agent = runtime.agent
    _daemon = runtime.daemon


def _compat_runtime_services() -> RuntimeServices:
    runtime = _runtime_services or RuntimeServices()
    runtime_model_tag = str(runtime.runtime_model_tag or "").strip() or default_runtime_model_tag()
    return RuntimeServices(
        agent=_agent if _agent is not None else runtime.agent,
        daemon=_daemon if _daemon is not None else runtime.daemon,
        display_name=str(runtime.display_name or "NULLA"),
        runtime_model_tag=runtime_model_tag,
        runtime_parameter_size=str(runtime.runtime_parameter_size or _parameter_size_for_model(runtime_model_tag)),
        runtime_started_at=str(runtime.runtime_started_at or ""),
        runtime_home=str(runtime.runtime_home or ""),
        runtime_version_stamp=dict(runtime.runtime_version_stamp or {}),
        public_hive_auth=dict(runtime.public_hive_auth or {}),
        provider_capability_truth=tuple(dict(item) for item in tuple(runtime.provider_capability_truth or ())),
    )


def _legacy_handler_runtime(server: object | None = None) -> RuntimeServices:
    attached_runtime = getattr(server, "nulla_runtime", None) if server is not None else None
    if isinstance(attached_runtime, RuntimeServices):
        return attached_runtime
    if isinstance(_runtime_services, RuntimeServices):
        return _runtime_services
    return RuntimeServices()


def _bootstrap() -> RuntimeServices:
    global _runtime_services
    _runtime_services = bootstrap_runtime_services(
        project_root=PROJECT_ROOT,
        workstation_version=NULLA_WORKSTATION_DEPLOYMENT_VERSION,
    )
    _sync_runtime_aliases(_runtime_services)
    return _runtime_services


def _daemon_runtime_config(*, capacity: int, local_worker_threads: int):
    return _daemon_runtime_config_impl(capacity=capacity, local_worker_threads=local_worker_threads)


def _ensure_default_provider(registry, model_tag: str) -> None:
    _ensure_default_provider_impl(registry, model_tag)


def _parameter_size_for_model(model_tag: str) -> str:
    return _parameter_size_for_model_impl(model_tag)


def _parameter_count_for_model(model_tag: str) -> int:
    return _parameter_count_for_model_impl(model_tag)


def _normalize_chat_history(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    return _normalize_chat_history_impl(messages)


def _stable_openclaw_session_id(*, body: dict[str, Any], history: list[dict[str, str]], headers: Any) -> str:
    normalized_headers = dict(headers.items()) if hasattr(headers, "items") else dict(headers or {})
    return _stable_openclaw_session_id_impl(body=body, history=history, headers=normalized_headers)


def _format_runtime_event_text(event: dict[str, Any]) -> str:
    return _format_runtime_event_text_impl(event)


def _default_workspace_root() -> str:
    return _default_workspace_root_impl()


def _run_agent(
    user_text: str,
    *,
    session_id: str | None = None,
    source_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _run_agent_impl(
        _compat_runtime_services(),
        user_text,
        session_id=session_id,
        source_context=source_context,
        workspace_root_provider=_default_workspace_root,
    )


def _stream_agent_with_events(
    user_text: str,
    *,
    session_id: str,
    source_context: dict[str, Any] | None,
    model: str,
    include_runtime_events: bool = False,
):
    return _stream_agent_with_events_impl(
        _compat_runtime_services(),
        user_text,
        session_id=session_id,
        source_context=source_context,
        model=model,
        include_runtime_events=include_runtime_events,
        run_agent_provider=lambda runtime, text, *, session_id=None, source_context=None: _run_agent(
            text,
            session_id=session_id,
            source_context=source_context,
        ),
    )


def _dispatch_get(*, path: str, query: dict[str, list[str]], runtime: RuntimeServices, model_name: str):
    return dispatch_get(
        path=path,
        query=query,
        runtime=runtime,
        model_name=model_name,
        capability_snapshot_provider=runtime_capability_snapshot,
    )


def _dispatch_post(
    *,
    path: str,
    body: dict[str, Any],
    headers: dict[str, Any],
    runtime: RuntimeServices,
    model_name: str,
    workspace_root_provider,
):
    return dispatch_post(
        path=path,
        body=body,
        headers=headers,
        runtime=runtime,
        model_name=model_name,
        workspace_root_provider=workspace_root_provider,
        normalize_chat_history_provider=_normalize_chat_history,
        stable_openclaw_session_id_provider=_stable_openclaw_session_id,
        run_agent_provider=lambda runtime, text, *, session_id=None, source_context=None, workspace_root_provider=None: _run_agent(
            text,
            session_id=session_id,
            source_context=source_context,
        ),
        stream_agent_with_events_provider=lambda runtime, text, *, session_id, source_context, model, include_runtime_events=False: _stream_agent_with_events(
            text,
            session_id=session_id,
            source_context=source_context,
            model=model,
            include_runtime_events=include_runtime_events,
        ),
    )


def create_app(runtime: RuntimeServices | None = None):
    return create_api_app(
        runtime=runtime or _legacy_handler_runtime(),
        model_name=MODEL_NAME,
        get_dispatcher=_dispatch_get,
        post_dispatcher=_dispatch_post,
        workspace_root_provider=_default_workspace_root,
    )


class NullaAPIHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        runtime = _legacy_handler_runtime(getattr(self, "server", None))
        response = _dispatch_get(
            path=parsed.path,
            query=parse_qs(parsed.query),
            runtime=runtime,
            model_name=MODEL_NAME,
        )
        self._write_response(response)

    def do_POST(self) -> None:
        runtime = _legacy_handler_runtime(getattr(self, "server", None))
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length <= 0:
            self._write_response(apply_runtime_headers(json_response(400, {"error": "empty body"}), runtime))
            return
        raw = self.rfile.read(content_length)
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            self._write_response(apply_runtime_headers(json_response(400, {"error": "invalid JSON"}), runtime))
            return
        response = _dispatch_post(
            path=self.path,
            body=body,
            headers=dict(self.headers.items()),
            runtime=runtime,
            model_name=MODEL_NAME,
            workspace_root_provider=_default_workspace_root,
        )
        self._write_response(response)

    def _write_response(self, response) -> None:
        self.send_response(int(response.status))
        self.send_header("Content-Type", str(response.content_type))
        for header, value in dict(response.headers or {}).items():
            self.send_header(str(header), str(value))
        if response.stream is None:
            payload = response.body or b""
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.end_headers()
        try:
            for chunk in response.stream:
                try:
                    self.wfile.write(chunk)
                    self.wfile.flush()
                except BrokenPipeError:
                    break
        finally:
            close = getattr(response.stream, "close", None)
            if callable(close):
                close()

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> int:
    parser = argparse.ArgumentParser(prog="nulla-api-server")
    parser.add_argument("--port", type=int, default=NULLA_API_PORT)
    parser.add_argument("--bind", default="127.0.0.1")
    args = parser.parse_args()

    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        with contextlib.suppress(Exception):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    logger.info("Bootstrapping NULLA runtime...")
    runtime: RuntimeServices | None = None
    try:
        runtime = _bootstrap()
        app = create_app(runtime)
        import uvicorn

        logger.info("NULLA API listening on http://%s:%s", args.bind, args.port)
        logger.info("OpenClaw can connect to this as an Ollama provider.")
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host=str(args.bind),
                port=int(args.port),
                access_log=False,
                log_level="info",
            )
        )
        server.run()
    finally:
        if runtime is not None:
            runtime.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
