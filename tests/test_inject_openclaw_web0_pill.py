from __future__ import annotations

from pathlib import Path

from installer.inject_openclaw_web0_pill import (
    _PILL_JS,
    END,
    PILL_JS_NAME,
    START,
    _strip_existing,
    apply,
    inject_into_html,
)

_BASE_HTML = (
    "<!doctype html>\n<html>\n  <head><title>OpenClaw Control</title></head>\n"
    "  <body>\n    <openclaw-app></openclaw-app>\n  </body>\n</html>\n"
)


def test_injects_external_script_reference_not_inline() -> None:
    out = inject_into_html(_BASE_HTML)
    assert out.count(START) == 1 and out.count(END) == 1
    assert out.rfind(START) < out.lower().rfind("</body>")
    # CSP blocks inline scripts, so the injected reference MUST be an external src.
    assert f'src="./{PILL_JS_NAME}"' in out
    assert "<script>" not in out.split(START, 1)[1].split(END, 1)[0]
    assert "<openclaw-app></openclaw-app>" in out  # app shell untouched


def test_injection_is_byte_stable_and_never_doubles() -> None:
    once = inject_into_html(_BASE_HTML)
    twice = inject_into_html(once)
    assert twice == once
    assert twice.count(START) == 1


def test_removal_restores_the_original_exactly() -> None:
    injected = inject_into_html(_BASE_HTML)
    assert _strip_existing(injected) == _BASE_HTML


def test_pill_js_targets_web0_and_relabels_docs() -> None:
    # The sidecar logic must point at the browser and relabel the Docs nav item.
    assert "http://127.0.0.1:11435/web0" in _PILL_JS
    assert "docs.openclaw.ai" in _PILL_JS
    assert 'replace("Docs", "Web0")' in _PILL_JS


def test_apply_writes_sidecar_js_and_round_trips(tmp_path: Path) -> None:
    index = tmp_path / "index.html"
    index.write_text(_BASE_HTML, encoding="utf-8")
    pill_js = tmp_path / PILL_JS_NAME

    assert apply(index) == "injected"
    assert pill_js.exists()
    assert "http://127.0.0.1:11435/web0" in pill_js.read_text(encoding="utf-8")
    assert f'src="./{PILL_JS_NAME}"' in index.read_text(encoding="utf-8")
    assert apply(index) == "already-correct"  # idempotent

    assert apply(index, remove=True) == "removed"
    assert index.read_text(encoding="utf-8") == _BASE_HTML
    assert not pill_js.exists()
    assert apply(index, remove=True) == "already-correct"


def test_apply_rewrites_stale_sidecar_js(tmp_path: Path) -> None:
    index = tmp_path / "index.html"
    index.write_text(_BASE_HTML, encoding="utf-8")
    pill_js = tmp_path / PILL_JS_NAME
    apply(index)
    pill_js.write_text("// stale content", encoding="utf-8")
    # Re-applying must refresh the sidecar to the current logic.
    assert apply(index) == "injected"
    assert pill_js.read_text(encoding="utf-8") == _PILL_JS
