from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from adapters.base_adapter import ModelRequest
from adapters.openai_compatible_adapter import OpenAICompatibleAdapter


def _ollama_adapter(*, provider_id: str = "ollama-local:qwen2.5:7b", model_name: str = "qwen2.5:7b") -> OpenAICompatibleAdapter:
    return OpenAICompatibleAdapter(
        SimpleNamespace(
            provider_id=provider_id,
            model_name=model_name,
            metadata={"runtime_family": "ollama"},
            runtime_config={"base_url": "http://127.0.0.1:11434/v1", "timeout_seconds": 5.0},
        )
    )


def _request(prompt: str = "hello") -> ModelRequest:
    return ModelRequest(task_kind="chat", prompt=prompt, messages=[{"role": "user", "content": prompt}])


def test_invoke_ollama_chat_records_live_benchmark_from_real_response_fields() -> None:
    # core.local_inference_autopilot's live per-message routing scores providers on
    # tokens_per_second, but that field stays static/zero unless a real Ollama call
    # actually records a benchmark. This is the missing write-side wiring.
    adapter = _ollama_adapter()
    response = mock.Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "message": {"content": "hi there"},
        "eval_count": 40,
        "eval_duration": 2_000_000_000,  # 2s -> 20 tok/s
    }

    with mock.patch("adapters.openai_compatible_adapter.requests.post", return_value=response), mock.patch(
        "core.local_inference_evidence.record_ollama_generate_benchmark"
    ) as record_mock:
        result = adapter.run_text_task(_request("hello there"))

    assert result.output_text == "hi there"
    record_mock.assert_called_once()
    _, kwargs = record_mock.call_args
    assert kwargs["provider_id"] == "ollama-local:qwen2.5:7b"
    assert kwargs["model_id"] == "qwen2.5:7b"
    assert kwargs["prompt"] == "hello there"
    assert kwargs["response_payload"]["eval_count"] == 40


def test_stream_ollama_chat_records_benchmark_on_final_done_event() -> None:
    adapter = _ollama_adapter()
    response = mock.Mock()
    response.raise_for_status.return_value = None
    response.iter_lines.return_value = [
        '{"message": {"content": "hi"}, "done": false}',
        '{"message": {"content": ""}, "done": true, "eval_count": 8, "eval_duration": 500000000}',
    ]

    with mock.patch("adapters.openai_compatible_adapter.requests.post", return_value=response), mock.patch(
        "core.local_inference_evidence.record_ollama_generate_benchmark"
    ) as record_mock:
        list(adapter.stream_text_task(_request("stream this")))

    record_mock.assert_called_once()
    _, kwargs = record_mock.call_args
    assert kwargs["response_payload"]["eval_count"] == 8


def test_benchmark_recording_failure_never_breaks_the_chat_response() -> None:
    adapter = _ollama_adapter()
    response = mock.Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"message": {"content": "still works"}, "eval_count": 5, "eval_duration": 1_000_000_000}

    with mock.patch("adapters.openai_compatible_adapter.requests.post", return_value=response), mock.patch(
        "core.local_inference_evidence.record_ollama_generate_benchmark",
        side_effect=RuntimeError("db unavailable"),
    ):
        result = adapter.run_text_task(_request("hello"))

    assert result.output_text == "still works"
