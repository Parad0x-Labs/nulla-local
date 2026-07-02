from __future__ import annotations

from core.runtime_task_rail import render_runtime_task_rail_html
from core.runtime_task_rail_event_render import RUNTIME_TASK_RAIL_EVENT_RENDER_SCRIPT
from core.runtime_task_rail_trace_styles import RUNTIME_TASK_RAIL_TRACE_STYLES


def test_event_render_surfaces_lane_model_complexity_chips() -> None:
    js = RUNTIME_TASK_RAIL_EVENT_RENDER_SCRIPT
    # The rail must render the per-turn routing NULLA actually used.
    assert "event.lane" in js
    assert "event.complexity" in js
    assert "actual_adapter_model_id" in js
    assert "meta-chip route" in js
    # Fast-path (trivial "hi") turns have no model - show that honestly.
    assert "fast-path" in js
    # tokens/sec surfaced when present.
    assert "tokens_per_second" in js


def test_route_chip_style_is_defined() -> None:
    assert ".meta-chip.route" in RUNTIME_TASK_RAIL_TRACE_STYLES


def test_full_trace_page_includes_routing_chips_and_style() -> None:
    html = render_runtime_task_rail_html()
    assert "meta-chip route" in html
    assert ".meta-chip.route" in html
    assert "actual_adapter_model_id" in html
