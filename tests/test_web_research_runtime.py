from __future__ import annotations

import json
import unittest
from unittest import mock

from core.live_quote_contract import format_quote_timestamp
from tools.web.web_research import (
    PageEvidence,
    WebHit,
    _extract_weather_location,
    _is_plausible_weather_location,
    _looks_like_news_query,
    _looks_like_price_query,
    _weather_fallback,
    web_research,
)


class WeatherLocationGuardTests(unittest.TestCase):
    def test_extracts_clean_place_names(self) -> None:
        self.assertEqual(_extract_weather_location("whats the weather in Riga?"), "riga")
        self.assertEqual(_extract_weather_location("wheater in vilnius?"), "vilnius")
        self.assertEqual(_extract_weather_location("weather in new york today"), "new york")

    def test_no_place_named_falls_back_to_current_location(self) -> None:
        self.assertEqual(_extract_weather_location("what is the weather?"), "current location")
        self.assertEqual(_extract_weather_location("weather?"), "current location")
        self.assertEqual(_extract_weather_location("hows the weather"), "current location")

    def test_scaffolding_and_garbage_locations_are_rejected(self) -> None:
        # Regression: the OpenClaw queued-turn scaffolding blob used to be handed to
        # wttr.in as a location and fuzzy-matched to "Los Vargas, Mexico" for a
        # "weather in Riga" question. It (and any bracket/GMT/over-long blob) must
        # now extract to "" so the weather fast-path bails.
        blob = "[Queued user message that arrived while the previous turn was still active] whats the riga? hi"
        self.assertEqual(_extract_weather_location(blob), "")
        self.assertEqual(_extract_weather_location("weather in [Wed 2026-07-01 18:07 GMT+3] riga"), "")
        self.assertEqual(
            _extract_weather_location("weather in foo bar baz qux quux corge grault garply waldo"),
            "",
        )

    def test_plausibility_predicate(self) -> None:
        for good in ("Riga", "new york", "san francisco", "current location"):
            self.assertTrue(_is_plausible_weather_location(good), good)
        for bad in ("", "[queued]", "a b c d e f g h", "weather GMT+3 blah", "12345"):
            self.assertFalse(_is_plausible_weather_location(bad), bad)

    def test_weather_fallback_bails_on_rejected_location_without_network(self) -> None:
        # If the location is rejected, _weather_fallback must return None BEFORE any
        # network call - assert by making urlopen explode if it's ever reached.
        with mock.patch(
            "tools.web.web_research.urllib.request.urlopen",
            side_effect=AssertionError("must not hit wttr.in with a rejected location"),
        ):
            blob = "[Queued user message that arrived while the previous turn was still active] whats the riga? hi"
            self.assertIsNone(_weather_fallback(blob, timeout_s=5.0))


class WebResearchRuntimeTests(unittest.TestCase):
    @staticmethod
    def _json_response(payload: dict[str, object]):
        class _Response:
            def __init__(self, body: bytes):
                self._body = body

            def read(self, _limit: int | None = None) -> bytes:
                return self._body

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        return _Response(json.dumps(payload).encode("utf-8"))

    def test_ddg_instant_empty_falls_through_to_duckduckgo_html(self) -> None:
        with mock.patch(
            "tools.web.web_research._provider_order",
            return_value=["ddg_instant", "duckduckgo_html"],
        ), mock.patch(
            "tools.web.web_research.ddg_instant_answer",
            return_value={},
        ), mock.patch(
            "tools.web.web_research._duckduckgo_html_hits",
            return_value=[
                WebHit(
                    title="Telegram Bot API",
                    url="https://core.telegram.org/bots/api",
                    snippet="HTTP-based interface for building Telegram bots.",
                    engine="duckduckgo_html",
                )
            ],
        ), mock.patch(
            "tools.web.web_research.http_fetch_text",
            return_value={"status": "ok", "text": "Useful docs text " * 60, "html": "<html></html>"},
        ), mock.patch(
            "tools.web.web_research._should_try_browser",
            return_value=False,
        ):
            result = web_research("Telegram Bot API docs", max_hits=1, max_pages=1)

        self.assertEqual(result.provider, "duckduckgo_html")
        self.assertIn("ddg_instant_empty", result.notes)
        self.assertTrue(result.hits)
        self.assertEqual(result.hits[0].url, "https://core.telegram.org/bots/api")

    def test_crypto_price_query_uses_word_boundaries(self) -> None:
        self.assertEqual(_looks_like_price_query("ETH price now?"), "ethereum")
        self.assertEqual(_looks_like_price_query("what is Seth price?"), "")

    def test_browser_disabled_keeps_http_text_without_claiming_browser_use(self) -> None:
        with mock.patch(
            "tools.web.web_research._provider_order",
            return_value=["duckduckgo_html"],
        ), mock.patch(
            "tools.web.web_research._duckduckgo_html_hits",
            return_value=[
                WebHit(
                    title="Telegram Bot API",
                    url="https://core.telegram.org/bots/api",
                    snippet="Canonical Telegram docs.",
                    engine="duckduckgo_html",
                )
            ],
        ), mock.patch(
            "tools.web.web_research.http_fetch_text",
            return_value={"status": "ok", "text": "short docs text", "html": "<html>short</html>"},
        ), mock.patch(
            "tools.web.web_research._should_try_browser",
            return_value=True,
        ), mock.patch(
            "tools.web.web_research.browser_render",
            return_value={"status": "disabled", "final_url": "https://core.telegram.org/bots/api"},
        ):
            result = web_research("Telegram Bot API docs", max_hits=1, max_pages=1)

        self.assertEqual(result.provider, "duckduckgo_html")
        self.assertTrue(result.pages)
        self.assertEqual(result.pages[0].status, "empty")
        self.assertEqual(result.pages[0].text, "short docs text")
        self.assertFalse(result.pages[0].used_browser)

    def test_weather_query_uses_specialized_live_fallback_when_search_providers_fail(self) -> None:
        with mock.patch(
            "tools.web.web_research._provider_order",
            return_value=["ddg_instant", "duckduckgo_html"],
        ), mock.patch(
            "tools.web.web_research.ddg_instant_answer",
            return_value={},
        ), mock.patch(
            "tools.web.web_research._duckduckgo_html_hits",
            side_effect=RuntimeError("duckduckgo_anomaly_challenge"),
        ), mock.patch(
            "tools.web.web_research._specialized_live_research",
            return_value=(
                "wttr_in",
                [
                    WebHit(
                        title="wttr.in weather for London",
                        url="https://wttr.in/London",
                        snippet="London: Rain, 12 C.",
                        engine="wttr_in",
                    )
                ],
                [
                    PageEvidence(
                        url="https://wttr.in/London",
                        final_url="https://wttr.in/London",
                        status="ok",
                        title="wttr.in weather for London",
                        text="London: Rain, 12 C.",
                        html_len=128,
                        used_browser=False,
                        screenshot_path=None,
                    )
                ],
                ["live_weather_fallback:wttr_in"],
            ),
        ), mock.patch(
            "tools.web.web_research.http_fetch_text",
            side_effect=AssertionError("prebuilt weather page should skip refetch"),
        ):
            result = web_research("what is the weather in London today?", max_hits=1, max_pages=1)

        self.assertEqual(result.provider, "wttr_in")
        self.assertIn("live_weather_fallback:wttr_in", result.notes)
        self.assertEqual(result.pages[0].text, "London: Rain, 12 C.")

    def test_weather_fallback_accepts_nested_data_payload(self) -> None:
        payload = {
            "data": {
                "current_condition": [
                    {
                        "localObsDateTime": "2026-03-17 11:32 PM",
                        "weatherDesc": [{"value": "Overcast"}],
                        "temp_C": "4",
                        "FeelsLikeC": "2",
                        "humidity": "86",
                        "windspeedKmph": "5",
                    }
                ],
                "nearest_area": [
                    {
                        "areaName": [{"value": "Vilnius"}],
                        "country": [{"value": "Lithuania"}],
                    }
                ],
            }
        }
        with mock.patch(
            "tools.web.web_research.urllib.request.urlopen",
            return_value=self._json_response(payload),
        ):
            result = web_research("what is weather in Vilnius now?", max_hits=1, max_pages=1)

        self.assertEqual(result.provider, "wttr_in")
        self.assertIn("live_weather_fallback:wttr_in", result.notes)
        self.assertIn("Vilnius, Lithuania: Overcast, 4 C", result.hits[0].snippet)

    def test_weather_query_prefers_specialized_live_fallback_over_generic_provider_hits(self) -> None:
        with mock.patch(
            "tools.web.web_research._provider_order",
            return_value=["duckduckgo_html"],
        ), mock.patch(
            "tools.web.web_research._specialized_live_research",
            return_value=(
                "wttr_in",
                [
                    WebHit(
                        title="wttr.in weather for Vilnius",
                        url="https://wttr.in/Vilnius",
                        snippet="Vilnius, Lithuania: Clear, 8 C.",
                        engine="wttr_in",
                    )
                ],
                [
                    PageEvidence(
                        url="https://wttr.in/Vilnius",
                        final_url="https://wttr.in/Vilnius",
                        status="ok",
                        title="wttr.in weather for Vilnius",
                        text="Vilnius, Lithuania: Clear, 8 C.",
                        html_len=128,
                        used_browser=False,
                        screenshot_path=None,
                    )
                ],
                ["live_weather_fallback:wttr_in"],
            ),
        ), mock.patch(
            "tools.web.web_research._duckduckgo_html_hits",
            side_effect=AssertionError("weather queries should not prefer generic provider hits over structured live weather"),
        ), mock.patch(
            "tools.web.web_research.http_fetch_text",
            side_effect=AssertionError("prebuilt weather page should skip refetch"),
        ):
            result = web_research("what is weather in Vilnius now?", max_hits=1, max_pages=1)

        self.assertEqual(result.provider, "wttr_in")
        self.assertIn("live_weather_fallback:wttr_in", result.notes)
        self.assertEqual(result.pages[0].text, "Vilnius, Lithuania: Clear, 8 C.")

    def test_news_query_uses_specialized_live_fallback_and_preserves_final_url(self) -> None:
        with mock.patch(
            "tools.web.web_research._provider_order",
            return_value=["ddg_instant", "duckduckgo_html"],
        ), mock.patch(
            "tools.web.web_research.ddg_instant_answer",
            return_value={},
        ), mock.patch(
            "tools.web.web_research._duckduckgo_html_hits",
            side_effect=RuntimeError("duckduckgo_anomaly_challenge"),
        ), mock.patch(
            "tools.web.web_research._specialized_live_research",
            return_value=(
                "google_news_rss",
                [
                    WebHit(
                        title="OpenAI to acquire Promptfoo",
                        url="https://news.google.com/rss/articles/demo",
                        snippet="OpenAI | 2026-03-09 | OpenAI to acquire Promptfoo",
                        engine="google_news_rss",
                    )
                ],
                [],
                ["live_news_fallback:google_news_rss"],
            ),
        ), mock.patch(
            "tools.web.web_research.http_fetch_text",
            return_value={
                "status": "ok",
                "text": "OpenAI announced it will acquire Promptfoo.",
                "html": "<html>OpenAI announced it will acquire Promptfoo.</html>",
                "final_url": "https://openai.com/index/openai-to-acquire-promptfoo/",
            },
        ), mock.patch(
            "tools.web.web_research._should_try_browser",
            return_value=False,
        ):
            result = web_research("latest news on OpenAI", max_hits=1, max_pages=1)

        self.assertEqual(result.provider, "google_news_rss")
        self.assertIn("live_news_fallback:google_news_rss", result.notes)
        self.assertEqual(result.pages[0].final_url, "https://openai.com/index/openai-to-acquire-promptfoo/")

    def test_latest_on_query_is_treated_as_news(self) -> None:
        self.assertTrue(_looks_like_news_query("What's the latest on Iran war?"))

    def test_market_quote_query_uses_specialized_live_fallback_when_search_providers_fail(self) -> None:
        yahoo_payload = {
            "chart": {
                "result": [
                    {
                        "meta": {
                            "symbol": "BZ=F",
                            "currency": "USD",
                            "exchangeName": "NYM",
                            "regularMarketPrice": 102.36,
                            "previousClose": 100.21,
                            "regularMarketTime": 1773763816,
                        },
                        "timestamp": [1773763740, 1773763800, 1773763816],
                        "indicators": {
                            "quote": [
                                {
                                    "close": [102.82, 102.63, 102.36],
                                }
                            ]
                        },
                    }
                ]
            }
        }

        with mock.patch(
            "tools.web.web_research._provider_order",
            return_value=["ddg_instant", "duckduckgo_html"],
        ), mock.patch(
            "tools.web.web_research.ddg_instant_answer",
            return_value={},
        ), mock.patch(
            "tools.web.web_research._duckduckgo_html_hits",
            side_effect=RuntimeError("duckduckgo_anomaly_challenge"),
        ), mock.patch(
            "tools.web.web_research.urllib.request.urlopen",
            return_value=self._json_response(yahoo_payload),
        ), mock.patch(
            "tools.web.web_research.http_fetch_text",
            side_effect=AssertionError("prebuilt market quote page should skip refetch"),
        ):
            result = web_research("Brent crude price now?", max_hits=1, max_pages=1)

        self.assertEqual(result.provider, "yahoo_finance")
        self.assertIn("live_price_fallback:yahoo_finance:brent_crude", result.notes)
        self.assertEqual(result.hits[0].title, "Brent crude quote")
        self.assertIn("Brent crude: $102.36 USD per barrel", result.hits[0].snippet)
        self.assertIn("session change", result.pages[0].text)
        self.assertIn(f"as of {format_quote_timestamp(1773763816)}", result.pages[0].text)


if __name__ == "__main__":
    unittest.main()
