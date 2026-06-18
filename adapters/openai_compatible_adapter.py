from __future__ import annotations

import json
import logging
import os
from typing import Any

import requests

from adapters.base_adapter import ModelAdapter, ModelRequest, ModelResponse, ModelStreamChunk
from core.compute_mode import get_active_compute_budget
from core.memory_prompt_builder import apply_memory_prefix_to_messages

logger = logging.getLogger("nulla.model_adapter")


class OpenAICompatibleAdapter(ModelAdapter):
    def supports_streaming(self) -> bool:
        return True

    def validate_runtime(self) -> list[str]:
        warnings: list[str] = []
        base_url = str(self.manifest.runtime_config.get("base_url") or "").strip()
        if not base_url:
            warnings.append(f"{self.manifest.provider_id}: missing runtime_config.base_url")
        return warnings

    def health_check(self) -> dict[str, Any]:
        base_url = str(self.manifest.runtime_config.get("base_url") or "").rstrip("/")
        if not base_url:
            return {"ok": False, "provider_id": self.manifest.provider_id, "error": "missing_base_url"}
        health_path = str(self.manifest.runtime_config.get("health_path") or "/v1/models")
        timeout_seconds = float(self.manifest.runtime_config.get("health_timeout_seconds") or 3.0)
        try:
            response = requests.get(f"{base_url}{health_path}", headers=self._headers(), timeout=timeout_seconds)
            response.raise_for_status()
            return {"ok": True, "provider_id": self.manifest.provider_id, "status_code": response.status_code}
        except Exception as exc:
            return {"ok": False, "provider_id": self.manifest.provider_id, "error": str(exc)}

    def prewarm(self) -> dict[str, Any]:
        prewarm_config = dict(self.manifest.runtime_config.get("prewarm") or {})
        if not prewarm_config:
            return super().prewarm()

        strategy = str(prewarm_config.get("strategy") or "").strip().lower()
        if strategy not in {"ollama_generate", "ollama_chat"}:
            return {
                "ok": False,
                "provider_id": self.manifest.provider_id,
                "status": "error",
                "error": f"unsupported_prewarm_strategy:{strategy or 'missing'}",
            }

        runtime_family = str(self.manifest.metadata.get("runtime_family") or "").strip().lower()
        if runtime_family != "ollama":
            return {
                "ok": True,
                "provider_id": self.manifest.provider_id,
                "status": "skipped",
                "reason": "not_ollama_runtime",
                "strategy": strategy,
            }

        base_url = str(self.manifest.runtime_config.get("base_url") or "").rstrip("/")
        if not base_url:
            return {
                "ok": False,
                "provider_id": self.manifest.provider_id,
                "status": "error",
                "error": "missing_base_url",
                "strategy": strategy,
            }

        endpoint, payload = self._ollama_prewarm_request(
            base_url=base_url,
            strategy=strategy,
            prewarm_config=prewarm_config,
        )

        timeout_seconds = float(
            prewarm_config.get("timeout_seconds")
            or self.manifest.runtime_config.get("health_timeout_seconds")
            or 15.0
        )
        try:
            response = requests.post(
                endpoint,
                json=payload,
                headers=self._headers(),
                timeout=timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
            return {
                "ok": True,
                "provider_id": self.manifest.provider_id,
                "status": "prewarmed",
                "strategy": strategy,
                "keep_alive": payload["keep_alive"],
                "load_duration": data.get("load_duration"),
                "total_duration": data.get("total_duration"),
            }
        except requests.exceptions.Timeout:
            return {
                "ok": True,
                "provider_id": self.manifest.provider_id,
                "status": "timed_out",
                "strategy": strategy,
                "reason": "cold_start_timeout",
                "keep_alive": payload["keep_alive"],
                "timeout_seconds": timeout_seconds,
            }
        except Exception as exc:
            return {
                "ok": False,
                "provider_id": self.manifest.provider_id,
                "status": "error",
                "strategy": strategy,
                "error": str(exc),
            }

    def _ollama_prewarm_request(
        self,
        *,
        base_url: str,
        strategy: str,
        prewarm_config: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        keep_alive = prewarm_config.get("keep_alive", "10m")
        native_base_url = _native_ollama_base_url(base_url)
        if strategy == "ollama_chat":
            message = prewarm_config.get("message")
            if message is None:
                message = prewarm_config.get("prompt")
            options = dict(prewarm_config.get("options") or {})
            options.setdefault("num_predict", 1)
            payload: dict[str, Any] = {
                "model": self.manifest.model_name,
                "messages": [{"role": "user", "content": " " if message is None else str(message)}],
                "stream": False,
                "keep_alive": keep_alive,
                "options": options,
            }
            if self._ollama_thinking_disabled():
                payload["think"] = False
            return f"{native_base_url}/api/chat", payload

        prompt = prewarm_config.get("prompt")
        payload = {
            "model": self.manifest.model_name,
            "prompt": " " if prompt is None else str(prompt),
            "stream": False,
            "keep_alive": keep_alive,
        }
        if "raw" in prewarm_config:
            payload["raw"] = bool(prewarm_config.get("raw"))
        if isinstance(prewarm_config.get("options"), dict) and prewarm_config.get("options"):
            payload["options"] = dict(prewarm_config["options"])
        return f"{native_base_url}/api/generate", payload

    def run_text_task(self, request: ModelRequest) -> ModelResponse:
        return self._invoke_http(request, force_json=False)

    def run_structured_task(self, request: ModelRequest) -> ModelResponse:
        return self._invoke_http(request, force_json=True)

    def stream_text_task(self, request: ModelRequest):
        if self._uses_native_ollama_chat():
            return self._stream_ollama_chat(request)
        return self._stream_openai_compatible(request)

    def invoke(self, request: ModelRequest) -> ModelResponse:
        force_json = request.output_mode in {"json_object", "action_plan", "tool_intent", "summary_block"}
        return self._invoke_http(request, force_json=force_json)

    def _invoke_http(self, request: ModelRequest, *, force_json: bool) -> ModelResponse:
        if self._uses_native_ollama_chat():
            return self._invoke_ollama_chat(request, force_json=force_json)
        return self._invoke_openai_compatible(request, force_json=force_json)

    def _invoke_openai_compatible(self, request: ModelRequest, *, force_json: bool) -> ModelResponse:
        base_url = str(self.manifest.runtime_config.get("base_url") or "").rstrip("/")
        if not base_url:
            raise RuntimeError(f"{self.manifest.provider_id}: missing runtime_config.base_url")
        api_path = str(self.manifest.runtime_config.get("api_path") or "/v1/chat/completions")
        payload = self._build_openai_payload(request, force_json=force_json, stream=False)
        timeout_seconds = float(self.manifest.runtime_config.get("timeout_seconds") or 30.0)
        response = requests.post(
            f"{base_url}{api_path}",
            json=payload,
            headers=self._headers(),
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        output_text = _extract_openai_text(data)
        usage = dict(data.get("usage") or {})
        return ModelResponse(
            output_text=output_text,
            confidence=float(self.manifest.metadata.get("confidence_baseline") or 0.65),
            raw_response=data,
            usage=usage,
            provider_id=self.manifest.provider_id,
            model_name=self.manifest.model_name,
            output_mode=request.output_mode,
        )

    def _invoke_ollama_chat(self, request: ModelRequest, *, force_json: bool) -> ModelResponse:
        base_url = _native_ollama_base_url(str(self.manifest.runtime_config.get("base_url") or "").rstrip("/"))
        if not base_url:
            raise RuntimeError(f"{self.manifest.provider_id}: missing runtime_config.base_url")
        payload = self._build_ollama_payload(request, force_json=force_json, stream=False)
        timeout_seconds = float(self.manifest.runtime_config.get("timeout_seconds") or 30.0)
        response = requests.post(
            f"{base_url}/api/chat",
            json=payload,
            headers=self._headers(),
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        output_text = _extract_ollama_chat_text(data)
        usage = {
            "prompt_eval_count": data.get("prompt_eval_count"),
            "eval_count": data.get("eval_count"),
            "total_duration": data.get("total_duration"),
            "load_duration": data.get("load_duration"),
        }
        return ModelResponse(
            output_text=output_text,
            confidence=float(self.manifest.metadata.get("confidence_baseline") or 0.65),
            raw_response=data,
            usage={key: value for key, value in usage.items() if value is not None},
            provider_id=self.manifest.provider_id,
            model_name=self.manifest.model_name,
            output_mode=request.output_mode,
        )

    def _stream_openai_compatible(self, request: ModelRequest):
        base_url = str(self.manifest.runtime_config.get("base_url") or "").rstrip("/")
        if not base_url:
            raise RuntimeError(f"{self.manifest.provider_id}: missing runtime_config.base_url")
        api_path = str(self.manifest.runtime_config.get("api_path") or "/v1/chat/completions")
        payload = self._build_openai_payload(request, force_json=False, stream=True)
        timeout_seconds = float(self.manifest.runtime_config.get("timeout_seconds") or 30.0)
        response = requests.post(
            f"{base_url}{api_path}",
            json=payload,
            headers=self._headers(),
            timeout=timeout_seconds,
            stream=True,
        )
        response.raise_for_status()
        try:
            for raw_line in response.iter_lines(decode_unicode=True):
                line = _normalize_stream_line(raw_line)
                if not line:
                    continue
                event = _parse_stream_line(line)
                if event is None:
                    continue
                if event == "__DONE__":
                    break
                delta_text = _extract_stream_delta_text(event)
                if delta_text:
                    yield ModelStreamChunk(delta_text=delta_text, raw_event=event, done=False)
        finally:
            response.close()
        yield ModelStreamChunk(delta_text="", done=True)

    def _stream_ollama_chat(self, request: ModelRequest):
        base_url = _native_ollama_base_url(str(self.manifest.runtime_config.get("base_url") or "").rstrip("/"))
        if not base_url:
            raise RuntimeError(f"{self.manifest.provider_id}: missing runtime_config.base_url")
        payload = self._build_ollama_payload(request, force_json=False, stream=True)
        timeout_seconds = float(self.manifest.runtime_config.get("timeout_seconds") or 30.0)
        response = requests.post(
            f"{base_url}/api/chat",
            json=payload,
            headers=self._headers(),
            timeout=timeout_seconds,
            stream=True,
        )
        response.raise_for_status()
        try:
            for raw_line in response.iter_lines(decode_unicode=True):
                line = _normalize_stream_line(raw_line)
                if not line:
                    continue
                event = _parse_stream_line(line)
                if not isinstance(event, dict):
                    continue
                delta_text = _extract_ollama_chat_text(event)
                if delta_text:
                    yield ModelStreamChunk(delta_text=delta_text, raw_event=event, done=False)
                if bool(event.get("done")):
                    break
        finally:
            response.close()
        yield ModelStreamChunk(delta_text="", done=True)

    def _build_openai_payload(self, request: ModelRequest, *, force_json: bool, stream: bool) -> dict[str, Any]:
        generation_profile = dict(request.metadata.get("generation_profile") or {})
        messages = _request_messages_with_memory(request)
        payload: dict[str, Any] = {
            "model": self.manifest.model_name,
            "messages": messages,
            "temperature": request.temperature
            if request.temperature is not None
            else generation_profile.get("temperature", self.manifest.runtime_config.get("temperature", 0.2)),
        }
        if stream:
            payload["stream"] = True
        if request.max_output_tokens is not None:
            payload["max_tokens"] = int(request.max_output_tokens)
        elif generation_profile.get("max_output_tokens") is not None:
            payload["max_tokens"] = int(generation_profile["max_output_tokens"])
        if generation_profile.get("top_p") is not None:
            payload["top_p"] = float(generation_profile["top_p"])
        if self._runtime_family() == "ollama":
            budget = get_active_compute_budget()
            payload["options"] = {
                "num_thread": int(max(1, budget.cpu_threads)),
            }
            context_window = self._ollama_context_window()
            if context_window > 0:
                payload["options"]["num_ctx"] = context_window
        stop_sequences = [str(item) for item in list(generation_profile.get("stop_sequences") or []) if str(item or "").strip()]
        if stop_sequences:
            payload["stop"] = stop_sequences
        if force_json and bool(self.manifest.runtime_config.get("supports_json_mode", False)):
            payload["response_format"] = {"type": "json_object"}
        return payload

    def _build_ollama_payload(self, request: ModelRequest, *, force_json: bool, stream: bool) -> dict[str, Any]:
        generation_profile = dict(request.metadata.get("generation_profile") or {})
        options: dict[str, Any] = {
            "temperature": request.temperature
            if request.temperature is not None
            else generation_profile.get("temperature", self.manifest.runtime_config.get("temperature", 0.2)),
        }
        if request.max_output_tokens is not None:
            options["num_predict"] = int(request.max_output_tokens)
        elif generation_profile.get("max_output_tokens") is not None:
            options["num_predict"] = int(generation_profile["max_output_tokens"])
        if generation_profile.get("top_p") is not None:
            options["top_p"] = float(generation_profile["top_p"])
        stop_sequences = [str(item) for item in list(generation_profile.get("stop_sequences") or []) if str(item or "").strip()]
        if stop_sequences:
            options["stop"] = stop_sequences
        budget = get_active_compute_budget()
        options["num_thread"] = int(max(1, budget.cpu_threads))
        context_window = self._ollama_context_window()
        if context_window > 0:
            options["num_ctx"] = context_window
        messages = _request_messages_with_memory(request)
        payload: dict[str, Any] = {
            "model": self.manifest.model_name,
            "messages": messages,
            "stream": bool(stream),
            "options": options,
        }
        if self._ollama_thinking_disabled():
            payload["think"] = False
        keep_alive = str(self.manifest.runtime_config.get("keep_alive") or "").strip()
        if keep_alive:
            payload["keep_alive"] = keep_alive
        if force_json and bool(self.manifest.runtime_config.get("supports_json_mode", False)):
            payload["format"] = "json"
        return payload

    def _uses_native_ollama_chat(self) -> bool:
        if self._runtime_family() != "ollama":
            return False
        return not bool(str(self.manifest.runtime_config.get("api_path") or "").strip())

    def _runtime_family(self) -> str:
        return str(self.manifest.metadata.get("runtime_family") or "").strip().lower()

    def _ollama_context_window(self) -> int:
        raw = self.manifest.runtime_config.get("context_window") or self.manifest.metadata.get("context_window") or 0
        try:
            return max(0, int(raw or 0))
        except (TypeError, ValueError):
            return 0

    def _ollama_thinking_disabled(self) -> bool:
        return self.manifest.runtime_config.get("think") is False

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        headers.update({str(k): str(v) for k, v in dict(self.manifest.runtime_config.get("headers") or {}).items()})
        api_key_env = str(self.manifest.runtime_config.get("api_key_env") or "").strip()
        if api_key_env and os.getenv(api_key_env):
            headers["Authorization"] = f"Bearer {os.getenv(api_key_env)}"
        return headers


def _build_messages(system_prompt: str | None, prompt: str, *, attachments: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    attachment_entries = list(attachments or [])
    if not attachment_entries:
        messages.append({"role": "user", "content": prompt})
        return messages

    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for attachment in attachment_entries:
        kind = str(attachment.get("kind") or "").lower()
        if kind == "image":
            url = str(attachment.get("url") or attachment.get("path") or "").strip()
            if url:
                content.append({"type": "image_url", "image_url": {"url": url}})
        elif kind == "video":
            transcript = str(attachment.get("transcript") or attachment.get("caption") or "").strip()
            label = str(attachment.get("label") or "Video evidence").strip()
            if transcript:
                content.append({"type": "text", "text": f"{label} transcript: {transcript}"})
            else:
                content.append({"type": "text", "text": f"{label}: video evidence provided but no transcript was available."})
        else:
            snippet = str(attachment.get("text") or attachment.get("caption") or "").strip()
            if snippet:
                content.append({"type": "text", "text": snippet})
    messages.append({"role": "user", "content": content})
    return messages


def _request_messages_with_memory(request: ModelRequest) -> list[dict[str, Any]]:
    messages = request.messages or _build_messages(request.system_prompt, request.prompt, attachments=request.attachments)
    return apply_memory_prefix_to_messages(messages, request)


def _extract_openai_text(payload: dict[str, Any]) -> str:
    choices = list(payload.get("choices") or [])
    if not choices:
        raise RuntimeError("OpenAI-compatible response did not include choices.")
    message = dict(choices[0].get("message") or {})
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(item.get("text") or "") for item in content if isinstance(item, dict)).strip()
    raise RuntimeError("OpenAI-compatible response did not include textual content.")


def _extract_ollama_chat_text(payload: dict[str, Any]) -> str:
    message = dict(payload.get("message") or {})
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(item.get("text") or "") for item in content if isinstance(item, dict)).strip()
    return ""


def _parse_stream_line(line: str) -> dict[str, Any] | str | None:
    text = str(line or "").strip()
    if not text:
        return None
    if text.startswith("data:"):
        text = text[5:].strip()
    if text == "[DONE]":
        return "__DONE__"
    try:
        return json.loads(text)
    except Exception:
        return None


def _extract_stream_delta_text(payload: dict[str, Any]) -> str:
    choices = list(payload.get("choices") or [])
    if not choices:
        return ""
    choice = dict(choices[0] or {})
    delta = dict(choice.get("delta") or {})
    content = delta.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(item.get("text") or "") for item in content if isinstance(item, dict)).strip()
    message = dict(choice.get("message") or {})
    fallback = message.get("content")
    if isinstance(fallback, str):
        return fallback
    return ""


def _normalize_stream_line(raw_line: Any) -> str:
    if raw_line is None:
        return ""
    if isinstance(raw_line, bytes):
        return raw_line.decode("utf-8", errors="replace").strip()
    return str(raw_line).strip()


def _native_ollama_base_url(base_url: str) -> str:
    clean = str(base_url or "").rstrip("/")
    if clean.endswith("/v1"):
        return clean[:-3]
    return clean
