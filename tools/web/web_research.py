from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import Any

from core import policy_engine
from core.live_quote_contract import LiveQuoteResult, format_quote_timestamp
from core.source_credibility import evaluate_source_domain
from tools.browser.browser_render import browser_render
from tools.web.ddg_instant import best_text_blob, ddg_instant_answer
from tools.web.http_fetch import http_fetch_text
from tools.web.searxng_client import SearchResult, SearXNGClient


def _weather_keywords() -> tuple[str, ...]:
    # Same marker list the fast-path classifier uses to recognize weather queries
    # (core/agent_runtime/fast_live_info_mode_weather_markers.py), including common
    # misspellings like "wheater"/"wheather". Without sharing this list, a typo'd
    # query could get correctly classified as weather mode by the classifier, then
    # silently fail here because this module's own keyword check didn't know the
    # same typo meant "weather" too.
    #
    # Imported locally (not at module level): core.agent_runtime's package
    # __init__ eagerly imports fast_live_info_search, which imports
    # retrieval.web_adapter, which imports this module — a module-level import
    # here would be circular. By the time this function actually runs, both
    # modules have finished their top-level loading.
    from core.agent_runtime.fast_live_info_mode_weather_markers import _WEATHER_MARKERS

    return tuple(sorted({token.strip() for token in _WEATHER_MARKERS if token.strip()}))


def _domain_from_url(url: str) -> str:
    """Extract bare domain from URL, stripping www. prefix."""
    if not url:
        return ""
    parsed = urllib.parse.urlparse(url)
    netloc = (parsed.netloc or "").lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc


@dataclass(frozen=True)
class WebHit:
    title: str
    url: str
    snippet: str = ""
    engine: str | None = None
    score: float | None = None


@dataclass(frozen=True)
class PageEvidence:
    url: str
    final_url: str | None
    status: str
    title: str = ""
    text: str = ""
    html_len: int = 0
    used_browser: bool = False
    screenshot_path: str | None = None


@dataclass(frozen=True)
class ResearchResult:
    query: str
    provider: str
    hits: list[WebHit]
    pages: list[PageEvidence]
    notes: list[str]
    ts_utc: float


def _provider_order() -> list[str]:
    env_raw = str(os.getenv("WEB_SEARCH_PROVIDER_ORDER", "")).strip()
    allowed = set(policy_engine.allowed_web_engines())
    if env_raw:
        return [item.strip().lower() for item in env_raw.split(",") if item.strip() and item.strip().lower() in allowed]
    return [item for item in policy_engine.web_provider_order() if item in allowed]


def _should_try_browser() -> bool:
    env_value = str(os.getenv("ALLOW_BROWSER_FALLBACK", "")).lower()
    if env_value:
        return env_value in {"1", "true", "yes"}
    return policy_engine.allow_browser_fallback()


def _text_too_short(text: str) -> bool:
    return len((text or "").strip()) < 600


def _needs_browser(fetch_status: str, text: str) -> bool:
    return fetch_status in {"captcha", "login_wall"} or _text_too_short(text)


def _looks_like_weather_query(query: str) -> bool:
    lowered = str(query or "").lower()
    return any(token in lowered for token in _weather_keywords())


def _looks_like_news_query(query: str) -> bool:
    lowered = str(query or "").lower()
    return any(
        token in lowered
        for token in (
            "latest news",
            "breaking news",
            "headlines",
            "news on",
            "news about",
            "what happened today",
            "what's the latest on",
            "what is the latest on",
            "whats the latest on",
            "latest on ",
            "latest about ",
        )
    )


def _extract_weather_location(query: str) -> str:
    clean = re.sub(r"[\?\!\.,]+", " ", str(query or "")).strip()
    clean = re.sub(r"\s+", " ", clean)
    lowered = clean.lower()
    for pattern in (
        r"\b(?:weather|forecast|temperature|rain|snow|wind|humidity)\s+(?:in|for|at)\s+(.+)$",
        r"\b(?:what is|what's|tell me|show me)\s+the\s+(?:weather|forecast)\s+(?:in|for|at)\s+(.+)$",
        r"\b(?:in|for|at)\s+(.+)$",
    ):
        match = re.search(pattern, lowered)
        if match:
            location = match.group(1).strip()
            break
    else:
        location = lowered
        for token in ("weather", "forecast", "temperature", "rain", "snow", "wind", "humidity"):
            location = location.replace(token, " ")

    tokens = [item for item in location.split() if item]
    trailing_noise = {
        "today",
        "tomorrow",
        "tonight",
        "now",
        "currently",
        "current",
        "forecast",
        "please",
        "right",
        "this",
        "week",
        "weekend",
    }
    while tokens and tokens[-1] in trailing_noise:
        tokens.pop()
    # Drop leading question/filler words so "what is the weather?" resolves to IP
    # geolocation instead of extracting "what is the" as a bogus place name.
    leading_filler = {
        "what", "whats", "what's", "how", "hows", "how's", "is", "are",
        "the", "a", "an", "hi", "hello", "hey", "yo", "tell", "me", "show",
        "please", "so", "and", "ok", "okay", "um", "like", "about",
    }
    while tokens and tokens[0] in leading_filler:
        tokens.pop(0)
    candidate = " ".join(tokens).strip()
    if not candidate:
        # Genuine "weather?" with no place named - let wttr.in use IP geolocation.
        return "current location"
    if not _is_plausible_weather_location(candidate):
        # Non-empty but not a real place name (scaffolding, a merged multi-message
        # blob, a leaked "[... GMT+N]" bracket, etc.). Returning "" tells
        # _weather_fallback to bail instead of fuzzy-matching garbage to a random
        # city - this is what turned "weather in Riga" into "Los Vargas, Mexico".
        return ""
    return candidate


def _is_plausible_weather_location(candidate: str) -> bool:
    text = str(candidate or "").strip()
    if not text:
        return False
    # Real place names have no structural/scaffolding characters and are short.
    if any(ch in text for ch in "[]{}\n\r\t|"):
        return False
    lowered = text.lower()
    if "gmt" in lowered or "queued user message" in lowered:
        return False
    if len(text) > 60 or len(text.split()) > 6:
        return False
    # Must contain at least one letter (a place name is never pure punctuation/digits).
    return any(ch.isalpha() for ch in text)


def _extract_news_topic(query: str) -> str:
    clean = re.sub(r"\s+", " ", str(query or "").strip()).strip(" ?!.,")
    lowered = clean.lower()
    for prefix in (
        "what's the latest on ",
        "what is the latest on ",
        "whats the latest on ",
        "latest news on ",
        "latest news about ",
        "breaking news on ",
        "breaking news about ",
        "latest on ",
        "latest about ",
        "news on ",
        "news about ",
        "headlines on ",
        "headlines about ",
    ):
        if lowered.startswith(prefix):
            return clean[len(prefix):].strip(" ?!.,") or clean
    return clean


def _prefer_specialized_live_research(query: str) -> bool:
    return bool(
        _looks_like_weather_query(query)
        or _looks_like_news_query(query)
        or _looks_like_price_query(query)
        or _looks_like_market_quote_query(query) is not None
    )


def _compact_pub_date(raw_value: str) -> str:
    text = str(raw_value or "").strip()
    if not text:
        return ""
    try:
        parsed = parsedate_to_datetime(text)
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc)
        return parsed.strftime("%Y-%m-%d")
    except Exception:
        return text


def _prebuilt_page_for_hit(pages: list[PageEvidence], hit: WebHit) -> PageEvidence | None:
    for page in list(pages or []):
        if page.url == hit.url or page.final_url == hit.url:
            return page
    return None


_CRYPTO_ALIASES: dict[str, str] = {
    "bitcoin": "bitcoin", "btc": "bitcoin",
    "ethereum": "ethereum", "eth": "ethereum",
    "solana": "solana", "sol": "solana",
    "cardano": "cardano", "ada": "cardano",
    "polkadot": "polkadot", "dot": "polkadot",
    "dogecoin": "dogecoin", "doge": "dogecoin",
    "ripple": "ripple", "xrp": "ripple",
    "litecoin": "litecoin", "ltc": "litecoin",
    "avalanche": "avalanche-2", "avax": "avalanche-2",
    "chainlink": "chainlink", "link": "chainlink",
    "matic": "matic-network", "polygon": "matic-network",
    "bnb": "binancecoin", "binance": "binancecoin",
}

_PRICE_KEYWORDS = (
    "price", "cost", "worth", "value", "rate",
    "how much", "trading at", "market cap", "quote",
)


@dataclass(frozen=True)
class MarketQuoteTarget:
    asset_key: str
    asset_name: str
    symbol: str
    unit_label: str
    aliases: tuple[str, ...]


_MARKET_QUOTE_TARGETS: tuple[MarketQuoteTarget, ...] = (
    MarketQuoteTarget(
        asset_key="brent_crude",
        asset_name="Brent crude",
        symbol="BZ=F",
        unit_label="per barrel",
        aliases=("brent crude oil", "brent crude", "brent oil", "brent"),
    ),
    MarketQuoteTarget(
        asset_key="wti_crude",
        asset_name="WTI crude",
        symbol="CL=F",
        unit_label="per barrel",
        aliases=("wti crude oil", "wti crude", "wti oil", "wti"),
    ),
    MarketQuoteTarget(
        asset_key="gold",
        asset_name="Gold",
        symbol="GC=F",
        unit_label="per troy ounce",
        aliases=("gold spot", "gold price", "gold", "xau"),
    ),
    MarketQuoteTarget(
        asset_key="silver",
        asset_name="Silver",
        symbol="SI=F",
        unit_label="per troy ounce",
        aliases=("silver spot", "silver price", "silver", "xag"),
    ),
)


def _looks_like_price_query(query: str) -> str:
    """Return CoinGecko coin ID if query asks for a crypto price, else ''."""
    lowered = str(query or "").lower()
    if not any(kw in lowered for kw in _PRICE_KEYWORDS):
        return ""
    for alias, cg_id in _CRYPTO_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", lowered):
            return cg_id
    return ""


def _looks_like_market_quote_query(query: str) -> MarketQuoteTarget | None:
    lowered = " ".join(str(query or "").strip().lower().split())
    if not lowered:
        return None
    wants_quote = any(kw in lowered for kw in _PRICE_KEYWORDS) or any(
        marker in lowered for marker in ("latest", "current", "right now", "today", "now")
    )
    if not wants_quote:
        return None
    for target in _MARKET_QUOTE_TARGETS:
        for alias in target.aliases:
            if re.search(rf"\b{re.escape(alias)}\b", lowered):
                return target
    return None


def _crypto_price_fallback(
    query: str,
    coin_id: str,
    *,
    timeout_s: float,
) -> tuple[str, list[WebHit], list[PageEvidence], list[str]] | None:
    api_url = (
        f"https://api.coingecko.com/api/v3/simple/price"
        f"?ids={urllib.parse.quote(coin_id)}"
        f"&vs_currencies=usd,eur,btc"
        f"&include_24hr_change=true&include_market_cap=true&include_last_updated_at=true"
    )
    req = urllib.request.Request(api_url, headers={"User-Agent": "NULLA-PRICE/1.0"})
    with urllib.request.urlopen(req, timeout=min(max(timeout_s, 3.0), 12.0)) as resp:
        payload = json.loads(resp.read(100000).decode("utf-8", errors="ignore"))

    data = payload.get(coin_id)
    if not data:
        return None
    usd = data.get("usd")
    if usd is None:
        return None
    eur = data.get("eur", "?")
    change_24h = data.get("usd_24h_change")
    mcap = data.get("usd_market_cap")
    last_updated_at = data.get("last_updated_at")
    name = coin_id.replace("-", " ").title()
    cg_url = f"https://www.coingecko.com/en/coins/{coin_id}"
    quote = LiveQuoteResult(
        asset_key=coin_id,
        asset_name=name,
        symbol=coin_id.replace("-", "_").upper(),
        value=float(usd),
        currency="USD",
        as_of=format_quote_timestamp(last_updated_at),
        source_label="CoinGecko",
        source_url=cg_url,
        kind="crypto",
        change_percent=None if change_24h is None else float(change_24h),
        change_window="24h",
        market_cap=None if not mcap else float(mcap),
        timestamp_utc=None if last_updated_at in {None, ""} else int(float(last_updated_at)),
    )
    summary = quote.summary_text()
    if eur != "?":
        summary = f"{summary} | EUR: {eur:,.2f}"
    hit = WebHit(title=f"{name} quote", url=cg_url, snippet=summary, engine="coingecko_api", score=None)
    page = PageEvidence(url=cg_url, final_url=cg_url, status="ok", title=hit.title, text=summary)
    return ("coingecko_api", [hit], [page], [f"live_price_fallback:coingecko_api:{coin_id}"])


def lookup_live_quote(query: str, *, timeout_s: float = 8.0) -> LiveQuoteResult | None:
    coin_id = _looks_like_price_query(query)
    if coin_id:
        try:
            result = _crypto_price_fallback(query, coin_id, timeout_s=timeout_s)
            if result:
                _provider, hits, _pages, _notes = result
                if hits:
                    return _live_quote_from_summary(
                        asset_key=coin_id,
                        asset_name=coin_id.replace("-", " ").title(),
                        symbol=coin_id.replace("-", "_").upper(),
                        summary=hits[0].snippet,
                        source_label="CoinGecko",
                        source_url=hits[0].url,
                        kind="crypto",
                    )
        except Exception:
            pass
    target = _looks_like_market_quote_query(query)
    if not target:
        return None
    try:
        result = _market_quote_fallback(query, target, timeout_s=timeout_s)
    except Exception:
        return None
    if not result:
        return None
    _provider, hits, _pages, _notes = result
    if not hits:
        return None
    return _live_quote_from_summary(
        asset_key=target.asset_key,
        asset_name=target.asset_name,
        symbol=target.symbol,
        summary=hits[0].snippet,
        source_label="Yahoo Finance",
        source_url=hits[0].url,
        kind="market",
        unit_label=target.unit_label,
    )


def _live_quote_from_summary(
    *,
    asset_key: str,
    asset_name: str,
    symbol: str,
    summary: str,
    source_label: str,
    source_url: str,
    kind: str,
    unit_label: str = "",
) -> LiveQuoteResult | None:
    price_match = re.search(r"(?P<value>\d[\d,]*\.?\d*)", summary or "")
    if not price_match:
        return None
    try:
        value = float(price_match.group("value").replace(",", ""))
    except Exception:
        return None
    as_of_match = re.search(r"\bas of (?P<as_of>[^|]+)", summary or "", re.IGNORECASE)
    change_match = re.search(r"\b(?:24h|session) change: (?P<change>[+-]?\d+(?:\.\d+)?)%", summary or "", re.IGNORECASE)
    change_window_match = re.search(r"\b(?P<label>24h|session) change:", summary or "", re.IGNORECASE)
    return LiveQuoteResult(
        asset_key=asset_key,
        asset_name=asset_name,
        symbol=symbol,
        value=value,
        currency="USD",
        as_of=str(as_of_match.group("as_of") if as_of_match else "").strip(),
        source_label=source_label,
        source_url=source_url,
        kind=kind,
        unit_label=unit_label,
        change_percent=None if not change_match else float(change_match.group("change")),
        change_window=str(change_window_match.group("label") if change_window_match else "").strip(),
    )


def _last_numeric(items: Any) -> float | None:
    for item in reversed(list(items or [])):
        if item in {None, ""}:
            continue
        try:
            return float(item)
        except Exception:
            continue
    return None


def _market_quote_fallback(
    query: str,
    target: MarketQuoteTarget,
    *,
    timeout_s: float,
) -> tuple[str, list[WebHit], list[PageEvidence], list[str]] | None:
    api_url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        + urllib.parse.quote(target.symbol, safe="=")
        + "?interval=1m&range=1d"
    )
    req = urllib.request.Request(api_url, headers={"User-Agent": "NULLA-MARKETS/1.0"})
    with urllib.request.urlopen(req, timeout=min(max(timeout_s, 3.0), 12.0)) as resp:
        payload = json.loads(resp.read(300000).decode("utf-8", errors="ignore"))

    chart = dict(payload.get("chart") or {})
    results = list(chart.get("result") or [])
    if not results:
        return None
    result = dict(results[0] or {})
    meta = dict(result.get("meta") or {})
    quote_block = dict((result.get("indicators") or {}).get("quote", [{}])[0] or {})
    price = meta.get("regularMarketPrice")
    if price in {None, ""}:
        price = _last_numeric(quote_block.get("close"))
    if price in {None, ""}:
        return None
    previous_close = meta.get("previousClose")
    if previous_close in {None, ""}:
        previous_close = meta.get("chartPreviousClose")
    change_percent: float | None = None
    try:
        previous_value = float(previous_close)
        if previous_value:
            change_percent = ((float(price) - previous_value) / previous_value) * 100.0
    except Exception:
        change_percent = None
    timestamp = meta.get("regularMarketTime")
    if timestamp in {None, ""}:
        timestamps = list(result.get("timestamp") or [])
        timestamp = timestamps[-1] if timestamps else None
    quote = LiveQuoteResult(
        asset_key=target.asset_key,
        asset_name=target.asset_name,
        symbol=target.symbol,
        value=float(price),
        currency=str(meta.get("currency") or "USD").strip().upper(),
        as_of=format_quote_timestamp(None if timestamp in {None, ""} else float(timestamp)),
        source_label="Yahoo Finance",
        source_url="https://finance.yahoo.com/quote/" + urllib.parse.quote(target.symbol, safe="="),
        kind="market",
        unit_label=target.unit_label,
        change_percent=change_percent,
        change_window="session",
        timestamp_utc=None if timestamp in {None, ""} else int(float(timestamp)),
        exchange=str(meta.get("exchangeName") or "").strip(),
    )
    summary = quote.summary_text()
    hit = WebHit(title=f"{target.asset_name} quote", url=quote.source_url, snippet=summary, engine="yahoo_finance", score=None)
    page = PageEvidence(url=quote.source_url, final_url=quote.source_url, status="ok", title=hit.title, text=summary)
    return ("yahoo_finance", [hit], [page], [f"live_price_fallback:yahoo_finance:{target.asset_key}"])


def _specialized_live_research(
    query: str,
    *,
    max_hits: int,
    fetch_timeout_s: float,
) -> tuple[str, list[WebHit], list[PageEvidence], list[str]] | None:
    if _looks_like_weather_query(query):
        return _weather_fallback(query, timeout_s=fetch_timeout_s)
    if _looks_like_news_query(query):
        return _news_rss_fallback(query, max_hits=max_hits, timeout_s=fetch_timeout_s)
    coin_id = _looks_like_price_query(query)
    if coin_id:
        try:
            return _crypto_price_fallback(query, coin_id, timeout_s=fetch_timeout_s)
        except Exception:
            pass
    market_target = _looks_like_market_quote_query(query)
    if market_target:
        try:
            return _market_quote_fallback(query, market_target, timeout_s=fetch_timeout_s)
        except Exception:
            pass
    return None


def _weather_fallback(
    query: str,
    *,
    timeout_s: float,
) -> tuple[str, list[WebHit], list[PageEvidence], list[str]] | None:
    location = _extract_weather_location(query)
    if not location:
        # Extractor rejected the "location" as implausible (scaffolding / merged
        # blob / not a real place). Bail so this flows to normal handling instead
        # of wttr.in guessing a random city from garbage.
        return None
    page_url = "https://wttr.in/" + urllib.parse.quote(location)
    api_url = page_url + "?format=j1"
    request = urllib.request.Request(api_url, headers={"User-Agent": "NULLA-WEATHER/1.0"})
    with urllib.request.urlopen(request, timeout=min(max(timeout_s, 3.0), 12.0)) as response:
        payload = json.loads(response.read(300000).decode("utf-8", errors="ignore"))

    root_payload = dict((payload.get("data") or payload) if isinstance(payload, dict) else {})
    current_items = list(root_payload.get("current_condition") or [])
    nearest_items = list(root_payload.get("nearest_area") or [])
    if not current_items:
        return None

    current = current_items[0] or {}
    area = nearest_items[0] if nearest_items else {}
    area_name = _first_nested_value(area.get("areaName")) or location
    country_name = _first_nested_value(area.get("country"))
    observed = str(current.get("localObsDateTime") or current.get("observation_time") or "").strip()
    weather_desc = _first_nested_value(current.get("weatherDesc")) or "Conditions unavailable"
    temp_c = str(current.get("temp_C") or "?").strip()
    feels_c = str(current.get("FeelsLikeC") or "?").strip()
    humidity = str(current.get("humidity") or "?").strip()
    wind_kmph = str(current.get("windspeedKmph") or "?").strip()
    place = area_name if not country_name or country_name.lower() == area_name.lower() else f"{area_name}, {country_name}"
    summary = (
        f"{place}: {weather_desc}, {temp_c} C (feels like {feels_c} C), "
        f"humidity {humidity}%, wind {wind_kmph} km/h."
    )
    if observed:
        summary += f" Observed {observed}."

    hit = WebHit(
        title=f"wttr.in weather for {place}",
        url=page_url,
        snippet=summary,
        engine="wttr_in",
        score=None,
    )
    page = PageEvidence(
        url=page_url,
        final_url=page_url,
        status="ok",
        title=hit.title,
        text=summary,
        html_len=len(json.dumps(payload)),
        used_browser=False,
        screenshot_path=None,
    )
    return ("wttr_in", [hit], [page], ["live_weather_fallback:wttr_in"])


def _news_rss_fallback(
    query: str,
    *,
    max_hits: int,
    timeout_s: float,
) -> tuple[str, list[WebHit], list[PageEvidence], list[str]] | None:
    topic = _extract_news_topic(query)
    if not topic:
        return None
    rss_url = (
        "https://news.google.com/rss/search?q="
        + urllib.parse.quote(topic)
        + "&hl=en-US&gl=US&ceid=US:en"
    )
    request = urllib.request.Request(rss_url, headers={"User-Agent": "NULLA-NEWS/1.0"})
    with urllib.request.urlopen(request, timeout=min(max(timeout_s, 3.0), 12.0)) as response:
        xml_text = response.read(500000).decode("utf-8", errors="ignore")

    root = ET.fromstring(xml_text)
    hits: list[WebHit] = []
    seen_urls: set[str] = set()
    for item in root.findall(".//item"):
        title = str(item.findtext("title") or "").strip()
        link = str(item.findtext("link") or "").strip()
        source_el = item.find("source")
        source_name = str(source_el.text or "").strip() if source_el is not None and source_el.text else ""
        source_url = str(source_el.get("url") or "").strip() if source_el is not None else ""
        final_url = _resolve_redirect_url(link, timeout_s=timeout_s) or source_url or link
        if not final_url or final_url in seen_urls:
            continue
        verdict = evaluate_source_domain(_domain_from_url(final_url or source_url))
        if verdict.blocked:
            continue
        seen_urls.add(final_url)
        pub_date = _compact_pub_date(str(item.findtext("pubDate") or ""))
        summary_parts = [source_name, pub_date, title]
        summary = " | ".join(part for part in summary_parts if part)
        hits.append(
            WebHit(
                title=title or source_name or "News result",
                url=final_url,
                snippet=summary[:280],
                engine="google_news_rss",
                score=None,
            )
        )
        if len(hits) >= max(1, int(max_hits)):
            break
    if not hits:
        return None
    return ("google_news_rss", hits, [], ["live_news_fallback:google_news_rss"])


def _resolve_redirect_url(url: str, *, timeout_s: float) -> str:
    target = str(url or "").strip()
    if not target:
        return ""
    request = urllib.request.Request(target, headers={"User-Agent": "NULLA-NEWS/1.0"})
    with urllib.request.urlopen(request, timeout=min(max(timeout_s, 3.0), 12.0)) as response:
        return str(response.geturl() or target).strip()


def _first_nested_value(items: Any) -> str:
    for item in list(items or []):
        if isinstance(item, dict):
            value = str(item.get("value") or "").strip()
            if value:
                return value
        else:
            value = str(item or "").strip()
            if value:
                return value
    return ""


def web_research(
    query: str,
    *,
    language: str = "en",
    safesearch: int = 1,
    max_hits: int = 8,
    max_pages: int = 3,
    fetch_timeout_s: float = 15.0,
    browser_engine: str | None = None,
    evidence_screenshot_dir: str | None = None,
) -> ResearchResult:
    notes: list[str] = []
    hits: list[WebHit] = []
    pages: list[PageEvidence] = []
    provider_used = "none"
    specialized_attempted = False

    if _prefer_specialized_live_research(query):
        specialized_attempted = True
        try:
            specialized = _specialized_live_research(
                query,
                max_hits=max_hits,
                fetch_timeout_s=fetch_timeout_s,
            )
        except Exception as exc:
            notes.append(f"specialized_live_failed:{type(exc).__name__}")
            specialized = None
        if specialized is not None:
            provider_used, hits, pages, extra_notes = specialized
            notes.extend(extra_notes)

    if not hits:
        for provider in _provider_order():
            if provider == "searxng":
                try:
                    client = SearXNGClient()
                    results: list[SearchResult] = client.search(
                        query,
                        language=language,
                        safesearch=safesearch,
                        max_results=max_hits,
                    )
                    hits = [WebHit(r.title, r.url, r.snippet, r.engine, r.score) for r in results if r.url]
                    if hits:
                        provider_used = "searxng"
                        break
                except Exception as exc:
                    notes.append(f"searxng_failed:{type(exc).__name__}")
                    continue

            if provider in {"ddg", "ddg_instant"}:
                try:
                    payload = ddg_instant_answer(query, timeout_s=10.0)
                    blob = best_text_blob(payload) or ""
                    url = str(payload.get("AbstractURL") or "").strip()
                    title = str(payload.get("Heading") or "DuckDuckGo Instant Answer").strip()
                    if not url:
                        url = "https://duckduckgo.com/?q=" + urllib.parse.quote_plus(query)
                    if not blob and url.startswith("https://duckduckgo.com/?q="):
                        notes.append("ddg_instant_empty")
                        continue
                    hits = [WebHit(title=title, url=url, snippet=blob, engine="ddg_instant", score=None)]
                    provider_used = "ddg_instant"
                    break
                except Exception as exc:
                    notes.append(f"ddg_failed:{type(exc).__name__}")
                    continue

            if provider == "duckduckgo_html":
                try:
                    hits = _duckduckgo_html_hits(query, max_hits=max_hits)
                    if hits:
                        provider_used = "duckduckgo_html"
                        break
                except Exception as exc:
                    notes.append(f"duckduckgo_html_failed:{type(exc).__name__}")
                    continue

            if provider == "google_html":
                try:
                    hits = _google_html_hits(query, max_hits=max_hits)
                    if hits:
                        provider_used = "google_html"
                        break
                except Exception as exc:
                    notes.append(f"google_html_failed:{type(exc).__name__}")
                    continue

    if not hits and not specialized_attempted:
        try:
            specialized = _specialized_live_research(
                query,
                max_hits=max_hits,
                fetch_timeout_s=fetch_timeout_s,
            )
        except Exception as exc:
            notes.append(f"specialized_live_failed:{type(exc).__name__}")
            specialized = None
        if specialized is None:
            notes.append("no_search_hits")
            return ResearchResult(query=query, provider=provider_used, hits=[], pages=[], notes=notes, ts_utc=time.time())
        provider_used, hits, pages, extra_notes = specialized
        notes.extend(extra_notes)

    for hit in hits[: max(1, int(max_pages))]:
        if _prebuilt_page_for_hit(pages, hit) is not None:
            continue
        try:
            fetched = http_fetch_text(hit.url, timeout_s=fetch_timeout_s)
            status = str(fetched.get("status") or "fetch_error")
            text = str(fetched.get("text") or "")
            html_text = str(fetched.get("html") or "")
            final_url = str(fetched.get("final_url") or hit.url)

            if _should_try_browser() and _needs_browser(status, text):
                screenshot_path = None
                if evidence_screenshot_dir:
                    os.makedirs(evidence_screenshot_dir, exist_ok=True)
                    screenshot_path = os.path.join(
                        evidence_screenshot_dir,
                        f"shot_{abs(hash(hit.url)) % 10_000_000}.png",
                    )
                rendered = browser_render(
                    hit.url,
                    engine=(browser_engine or os.getenv("BROWSER_ENGINE") or policy_engine.browser_engine()),
                    screenshot_path=screenshot_path,
                )
                rendered_status = str(rendered.get("status") or "fetch_error")
                if rendered_status == "ok":
                    pages.append(
                        PageEvidence(
                            url=hit.url,
                            final_url=str(rendered.get("final_url") or final_url),
                            status="ok",
                            title=str(rendered.get("title") or hit.title),
                            text=str(rendered.get("text") or "")[:200000],
                            html_len=len(str(rendered.get("html") or "")),
                            used_browser=True,
                            screenshot_path=rendered.get("screenshot_path"),
                        )
                    )
                else:
                    fallback_status = status
                    if fallback_status == "ok" and _text_too_short(text):
                        fallback_status = "empty"
                    pages.append(
                        PageEvidence(
                            url=hit.url,
                            final_url=str(rendered.get("final_url") or final_url),
                            status=fallback_status,
                            title=hit.title,
                            text=text[:200000],
                            html_len=len(html_text),
                            used_browser=False,
                            screenshot_path=None,
                        )
                    )
                continue

            pages.append(
                PageEvidence(
                    url=hit.url,
                    final_url=final_url,
                    status="empty" if status == "ok" and _text_too_short(text) else status,
                    title=hit.title,
                    text=text[:200000],
                    html_len=len(html_text),
                    used_browser=False,
                    screenshot_path=None,
                )
            )
        except Exception as exc:
            pages.append(
                PageEvidence(
                    url=hit.url,
                    final_url=None,
                    status=f"fetch_error:{type(exc).__name__}",
                    title=hit.title,
                    text="",
                    html_len=0,
                    used_browser=False,
                    screenshot_path=None,
                )
            )

    return ResearchResult(
        query=query,
        provider=provider_used,
        hits=hits[: max(1, int(max_hits))],
        pages=pages,
        notes=notes,
        ts_utc=time.time(),
    )


def to_jsonable(result: ResearchResult) -> dict[str, Any]:
    return {
        "query": result.query,
        "provider": result.provider,
        "hits": [asdict(item) for item in result.hits],
        "pages": [asdict(item) for item in result.pages],
        "notes": list(result.notes),
        "ts_utc": result.ts_utc,
    }


def _duckduckgo_html_hits(query: str, *, max_hits: int) -> list[WebHit]:
    from html import unescape

    text = (query or "").strip()
    if not text:
        return []
    request = urllib.request.Request(
        "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(text),
        headers={"User-Agent": "Mozilla/5.0 NULLA-XSEARCH/1.0"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        html_text = response.read().decode("utf-8", errors="ignore")
    if "Unfortunately, bots use DuckDuckGo too." in html_text or "anomaly-modal" in html_text:
        raise RuntimeError("duckduckgo_anomaly_challenge")
    snippet_matches = re.findall(r'<a class="result__snippet[^>]*>(.*?)</a>', html_text, re.IGNORECASE | re.DOTALL)
    link_matches = re.findall(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"', html_text, re.IGNORECASE)
    title_matches = re.findall(r'<a[^>]+class="result__a"[^>]*>(.*?)</a>', html_text, re.IGNORECASE | re.DOTALL)
    hits: list[WebHit] = []
    for raw_title, raw_snippet, raw_link in zip(title_matches, snippet_matches, link_matches):  # noqa: B905  -- strict= unsupported on Python 3.9
        resolved_url = _resolve_duckduckgo_result_url(raw_link)
        if not resolved_url:
            continue
        title = re.sub(r"<[^>]+>", "", raw_title).strip()
        snippet = re.sub(r"<[^>]+>", "", raw_snippet).strip()
        hits.append(
            WebHit(
                title=unescape(title),
                url=resolved_url,
                snippet=unescape(snippet),
                engine="duckduckgo_html",
                score=None,
            )
        )
        if len(hits) >= max(1, int(max_hits)):
            break
    return hits


def _google_html_hits(query: str, *, max_hits: int) -> list[WebHit]:
    from tools.web.google_html import google_html_search

    text = (query or "").strip()
    if not text:
        return []
    raw_results = google_html_search(text, max_results=max_hits, timeout_s=10.0)
    return [
        WebHit(
            title=str(r.get("title") or "").strip(),
            url=str(r.get("url") or "").strip(),
            snippet=str(r.get("snippet") or "").strip(),
            engine="google_html",
            score=None,
        )
        for r in raw_results
        if str(r.get("url") or "").strip()
    ]


def _resolve_duckduckgo_result_url(raw_href: str) -> str:
    from html import unescape
    from urllib.parse import parse_qs, urlparse

    href = unescape(raw_href or "").strip()
    if not href:
        return ""
    parsed = urlparse(href)
    query = parse_qs(parsed.query)
    if query.get("uddg"):
        return urllib.parse.unquote(query["uddg"][0])
    return href
