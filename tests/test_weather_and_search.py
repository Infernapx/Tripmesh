"""
tests/test_weather_and_search.py
--------------------------------
Comprehensive unit tests for:
1. Real weather forecast (Open-Meteo) & proactive alert banner logic
2. Road conditions via Tavily web search & citation discipline
3. Destination news filtering for travel relevance & notable alert triggers
4. Local guide live web fallback for uncovered cities with distinct labeling
5. Graceful honest degradation when API keys or network calls are unavailable
"""

from __future__ import annotations

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

# Ensure tripmesh root is in sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shared.weather import get_weather_forecast, evaluate_weather_alert
from shared.tavily_search import (
    search_road_conditions,
    search_destination_news,
    filter_travel_relevant_news,
    search_local_guide_fallback,
)


# ---------------------------------------------------------------------------
# 1. Weather Tests (Open-Meteo)
# ---------------------------------------------------------------------------

def test_weather_forecast_for_destination():
    """Mock Open-Meteo response, assert correct forecast retrieved and formatted for a real query."""
    async def _run():
        mock_data = {
            "daily": {
                "time": ["2026-09-30", "2026-10-01"],
                "temperature_2m_max": [24.5, 26.0],
                "temperature_2m_min": [15.2, 16.0],
                "precipitation_sum": [0.0, 2.5],
                "wind_speed_10m_max": [12.0, 15.0]
            }
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_data

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp

        with patch("shared.weather.geocode_destination_coords", return_value=(35.0116, 135.7681, "Kyoto")):
            result = await get_weather_forecast("Kyoto", client=mock_client)

        assert result["success"] is True
        assert result["success"] is True
        assert result["destination_name"] == "Kyoto"
        assert len(result["forecast"]) == 2
        assert result["forecast"][0]["temp_max"] == 24.5
        assert result["forecast"][0]["temp_min"] == 15.2
        assert "Weather Forecast for Kyoto" in result["summary"]
        assert "24" in result["summary"]

    asyncio.run(_run())


def test_weather_alert_triggers_on_extreme_conditions():
    """Evaluate weather alert triggers on extreme conditions vs mild conditions."""
    # Extreme heat (>=38°C)
    alert_heat = evaluate_weather_alert(max_high=38.5, min_low=26.0)
    assert alert_heat is not None
    assert "Heat alert" in alert_heat
    assert "39°C" in alert_heat or "38°C" in alert_heat

    # Extreme cold (<=0°C)
    alert_cold = evaluate_weather_alert(max_high=-2.0, min_low=-8.5)
    assert alert_cold is not None
    assert "Freezing alert" in alert_cold

    # Heavy precipitation (>=15mm or >=70%)
    alert_rain = evaluate_weather_alert(max_high=22.0, min_low=17.0, max_precip=28.0, rain_prob_pct=80)
    assert alert_rain is not None
    assert "Heavy rain alert" in alert_rain

    # Mild, unremarkable weather -> No banner!
    alert_mild = evaluate_weather_alert(max_high=24.0, min_low=14.0, max_precip=1.0, rain_prob_pct=10)
    assert alert_mild is None


def test_weather_missing_key_or_api_failure_degrades_honestly():
    """Unresolvable destination degrades honestly without fabricating weather."""
    async def _run():
        mock_client = AsyncMock()
        mock_client.get.side_effect = Exception("Connection timed out")

        with patch("shared.weather.geocode_destination_coords", return_value=None):
            result = await get_weather_forecast("", client=mock_client)

        assert result["success"] is False
        assert result["alert"] is None
        assert "could not find weather or coordinates" in result["summary"].lower()
        assert result["forecast"] == []

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 2. Road Conditions Tests (Tavily)
# ---------------------------------------------------------------------------

def test_road_conditions_cites_real_sources():
    """Mock Tavily response, assert the synthesized answer includes actual cited source(s), not an unattributed claim."""
    async def _run():
        mock_tavily = MagicMock()
        mock_tavily.search.return_value = {
            "results": [
                {
                    "title": "Highways England M1 Travel Update",
                    "url": "https://nationalhighways.co.uk/travel-updates/m1-closure",
                    "content": "Lane restrictions active between junction 14 and 15 due to overnight resurfacing works."
                },
                {
                    "title": "Traffic Scotland M6 Live Traffic",
                    "url": "https://trafficscotland.org/current-incidents",
                    "content": "M6 northbound moving well with typical delays around peak hours."
                }
            ]
        }

        result = await search_road_conditions("London to Edinburgh", client=mock_tavily)

        assert result["success"] is True
        assert len(result["sources"]) >= 2
        # Assert actual cited source URLs appear in synthesis
        assert "https://nationalhighways.co.uk/travel-updates/m1-closure" in result["summary"]
        assert "nationalhighways.co.uk" in result["summary"]
        assert "trafficscotland.org" in result["summary"]

    asyncio.run(_run())


def test_road_conditions_no_results_says_so_honestly():
    """Mock an empty Tavily response, assert an honest 'couldn't find' message."""
    async def _run():
        mock_tavily = MagicMock()
        mock_tavily.search.return_value = {"results": []}

        result = await search_road_conditions("Rural Route 42", client=mock_tavily)

        assert result["success"] is True
        assert "couldn't find current road condition info" in result["summary"].lower()
        assert result["sources"] == []

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 3. Destination News Tests (Tavily)
# ---------------------------------------------------------------------------

def test_destination_news_filters_for_relevance():
    """Mock a Tavily news response mixing relevant (advisory/strike) and irrelevant (routine local headline) items, assert only genuinely travel-relevant news surfaces."""
    raw_articles = [
        {
            "title": "National Rail strike announced for October 12",
            "content": "Train operators expect major transport disruptions across the region due to a 24-hour strike.",
            "url": "https://bbc.com/news/uk-strike"
        },
        {
            "title": "Local bakery wins golden croissant award",
            "content": "A neighborhood bakery in central district has won best pastry in the annual culinary competition.",
            "url": "https://localgazette.com/croissant"
        },
        {
            "title": "Travel safety advisory issued after coastal flooding",
            "content": "Civil protection authorities advise travelers to avoid coastal roads due to high storm surge.",
            "url": "https://gov.pt/weather-warning"
        },
        {
            "title": "City council approves new parking meter design",
            "content": "Municipal officials selected blue paint for newly installed downtown parking meters.",
            "url": "https://localgazette.com/meters"
        }
    ]

    filtered = filter_travel_relevant_news(raw_articles)

    assert len(filtered) == 2
    titles = [a["title"] for a in filtered]
    assert "National Rail strike announced for October 12" in titles
    assert "Travel safety advisory issued after coastal flooding" in titles
    assert "Local bakery wins golden croissant award" not in titles
    assert "City council approves new parking meter design" not in titles


def test_destination_news_alert_only_when_notable():
    """Mock notable news, assert alert banner appears; mock no notable news, assert no banner (and no forced 'nothing happening' claim)."""
    async def _run():
        # 1. Notable news present -> Alert banner should appear
        mock_notable_tavily = MagicMock()
        mock_notable_tavily.search.return_value = {
            "results": [
                {
                    "title": "Airport ground staff strike delays flights in Lisbon",
                    "content": "Ground crew walkout expected to cause flight delays and cancellations this weekend.",
                    "url": "https://reuters.com/world/europe/lisbon-strike"
                }
            ]
        }

        res_notable = await search_destination_news("Lisbon", client=mock_notable_tavily)
        assert res_notable["success"] is True
        assert res_notable["is_notable"] is True
        assert res_notable["alert"] is not None
        assert "⚠️ Travel Advisory for Lisbon" in res_notable["alert"]
        assert "reuters.com" in res_notable["alert"]

        # 2. Only unremarkable local headlines -> No alert banner!
        mock_quiet_tavily = MagicMock()
        mock_quiet_tavily.search.return_value = {
            "results": [
                {
                    "title": "Sunny weather expected for the weekend garden walk",
                    "content": "Botanical garden invites guests to enjoy blooming hydrangeas.",
                    "url": "https://localgardens.org/weekend"
                }
            ]
        }

        res_quiet = await search_destination_news("Lisbon", client=mock_quiet_tavily)
        assert res_quiet["success"] is True
        assert res_quiet["is_notable"] is False
        assert res_quiet["alert"] is None

    asyncio.run(_run())


def test_destination_news_cites_real_sources():
    """Same citation-discipline check as road conditions, applied to news."""
    async def _run():
        mock_tavily = MagicMock()
        mock_tavily.search.return_value = {
            "results": [
                {
                    "title": "Wildfire warning issued in regional park",
                    "content": "Forestry department issued safety warning for outdoor hikers due to dry conditions.",
                    "url": "https://safety.gov.gr/wildfire-advisory"
                }
            ]
        }

        result = await search_destination_news("Santorini", client=mock_tavily)

        assert result["success"] is True
        assert "safety.gov.gr" in result["summary"]
        assert "https://safety.gov.gr/wildfire-advisory" in result["summary"]
        assert len(result["sources"]) == 1
        assert result["sources"][0]["site"] == "safety.gov.gr"

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 4. Local Guide Fallback & Distinct Labeling
# ---------------------------------------------------------------------------

def test_local_guide_web_fallback_labeled_distinctly():
    """For an uncovered city, assert the response includes both the honest 'no seeded match' line AND a clearly-labeled web-search-sourced follow-up, never merged indistinguishably."""
    async def _run():
        mock_tavily = MagicMock()
        mock_tavily.search.return_value = {
            "results": [
                {
                    "title": "Desert Botanical Garden",
                    "url": "https://dbg.org/visit",
                    "content": "50,000 desert plants nestled amid the red rocks of the Papago Buttes."
                },
                {
                    "title": "Camelback Mountain",
                    "url": "https://phoenix.gov/parks/camelback",
                    "content": "Iconic summit hike offering 360-degree views of the valley."
                }
            ]
        }

        fallback_resp = await search_local_guide_fallback("Phoenix", client=mock_tavily)
        assert fallback_resp["success"] is True
        assert len(fallback_resp["results"]) == 2

        # Simulate Local Guide Agent Executor synthesis logic
        uncovered_city = "Phoenix"
        seed_notice = f"No strong match found in local guide knowledge base for '{uncovered_city}'."
        web_summary = fallback_resp["summary"]
        combined_message = f"{seed_notice}\n\n{web_summary}"

        # Assert honest no-match line exists
        assert f"No strong match found in local guide knowledge base for '{uncovered_city}'." in combined_message

        # Assert distinct labeling: "🌐 Live web result" marker
        assert "🌐 Live web result" in combined_message
        assert "dbg.org" in combined_message
        assert "https://dbg.org/visit" in combined_message

        # Assert distinct from Wikivoyage notation
        assert "Wikivoyage" not in web_summary

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 5. Missing Tavily Key Graceful Degradation
# ---------------------------------------------------------------------------

def test_missing_tavily_key_degrades_gracefully(monkeypatch):
    """With TAVILY_API_KEY unset, road-conditions and web-fallback features should say they can't check live info right now — never crash the request."""
    async def _run():
        # Ensure TAVILY_API_KEY is unset
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)

        # 1. Road conditions
        rc_result = await search_road_conditions("London to Paris")
        assert rc_result["success"] is False
        assert "can't check live road conditions" in rc_result["summary"].lower()

        # 2. Local Guide fallback
        fallback_result = await search_local_guide_fallback("UncoveredCity")
        assert fallback_result["success"] is False
        assert "live web search is not configured" in fallback_result["summary"].lower()

        # 3. Destination news
        news_result = await search_destination_news("Tokyo")
        assert news_result["success"] is False
        assert "couldn't fetch live news" in news_result["summary"].lower() or "can't check" in news_result["summary"].lower()
        assert news_result["alert"] is None

    asyncio.run(_run())
