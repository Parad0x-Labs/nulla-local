from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from urllib.parse import parse_qs

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.routing import Route

from ..request_ids import log_http_request, resolve_request_id, response_headers_with_request_id
from .runtime import RuntimeServices, default_workspace_root
from .service import ApiResponse, dispatch_get, dispatch_post, json_response

logger = logging.getLogger("nulla.api.http")


def _starlette_response(response: ApiResponse) -> Response:
    if response.stream is not None:
        return StreamingResponse(response.stream, status_code=response.status, media_type=response.content_type, headers=response.headers)
    payload = response.body or b""
    return Response(payload, status_code=response.status, media_type=response.content_type, headers=response.headers)


async def _dispatch(request: Request) -> Response:
    request_id = resolve_request_id(dict(request.headers.items()))
    started = time.perf_counter()
    runtime: RuntimeServices = request.app.state.runtime
    model_name: str = request.app.state.model_name
    get_dispatcher: Callable[..., ApiResponse] = getattr(request.app.state, "get_dispatcher", dispatch_get)
    post_dispatcher: Callable[..., ApiResponse] = getattr(request.app.state, "post_dispatcher", dispatch_post)
    workspace_root_provider: Callable[[], str] = getattr(
        request.app.state,
        "workspace_root_provider",
        default_workspace_root,
    )
    response: ApiResponse
    if request.method == "GET":
        response = await run_in_threadpool(
            get_dispatcher,
            path=request.url.path,
            query=parse_qs(request.url.query),
            runtime=runtime,
            model_name=model_name,
        )
    elif request.method == "POST":
        raw_body = await request.body()
        if not raw_body:
            response = json_response(400, {"error": "empty body"})
        else:
            try:
                body = json.loads(raw_body)
            except json.JSONDecodeError:
                response = json_response(400, {"error": "invalid JSON"})
            else:
                response = await run_in_threadpool(
                    post_dispatcher,
                    path=request.url.path,
                    body=body,
                    headers=dict(request.headers.items()),
                    runtime=runtime,
                    model_name=model_name,
                    workspace_root_provider=workspace_root_provider,
                )
    elif request.method == "OPTIONS":
        if request.url.path.rstrip("/") == "/gate/unlock":
            from core.web0_gated_html import gate_cors_headers

            response = ApiResponse(
                204,
                content_type="text/plain; charset=utf-8",
                body=b"",
                headers=gate_cors_headers(),
            )
        else:
            response = json_response(404, {"error": "not found"})
    else:
        response = json_response(404, {"error": "not found"})
    response.headers = response_headers_with_request_id(response.headers, request_id=request_id)
    latency_ms = (time.perf_counter() - started) * 1000.0
    log_http_request(
        logger,
        component="api",
        method=request.method,
        path=request.url.path,
        status_code=response.status,
        latency_ms=latency_ms,
        request_id=request_id,
    )
    return _starlette_response(response)


def create_api_app(
    *,
    runtime: RuntimeServices,
    model_name: str,
    get_dispatcher: Callable[..., ApiResponse] = dispatch_get,
    post_dispatcher: Callable[..., ApiResponse] = dispatch_post,
    workspace_root_provider: Callable[[], str] = default_workspace_root,
) -> Starlette:
    app = Starlette(
        debug=False,
        routes=[
            Route("/", _dispatch, methods=["GET", "POST", "OPTIONS"]),
            Route("/{path:path}", _dispatch, methods=["GET", "POST", "OPTIONS"]),
        ],
    )
    app.state.runtime = runtime
    app.state.model_name = model_name
    app.state.get_dispatcher = get_dispatcher
    app.state.post_dispatcher = post_dispatcher
    app.state.workspace_root_provider = workspace_root_provider
    return app
