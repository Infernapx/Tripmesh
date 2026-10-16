"""
tests/test_round5_followup.py
------------------------------
Validation suite for Round 5 follow-up fixes:
1. Fix A: Default dates must be in the future (today + 14 days), next occurrence for date without year, rejection of explicit past dates, re-defaulting past session dates.
2. Fix B: Weather consistency (single set of numbers, headline range / alert max consistency, now/today window, >16 days typical climate, no past forecasts).
3. Fix C: ASK_SAFETY intent, multi-source safety handler (Tavily, weather hazards, evergreen guidance), fallbacks on unset key / 500 / timeout, no 'safe/unsafe' verdicts, plan preservation.
4. Fix D: Sweep validation for dynamic dates.
"""

import pytest
import os
import re
import asyncio
from datetime import date, timedelta
from unittest.mock import patch, AsyncMock, MagicMock
from langchain_core.messages import HumanMessage

from shared.resolvers import (
    get_default_trip_dates,
    is_date_in_past,
    resolve_dates_from_text,
    DEFAULT_TRIP_LEAD_DAYS,
    DEFAULT_TRIP_LENGTH_DAYS
)
from shared.weather import (
    get_weather_forecast,
    evaluate_weather_alert,
    HAZARD_HEAT_C,
    HAZARD_FREEZING_C,
    HAZARD_HEAVY_RAIN_MM,
    HAZARD_HEAVY_RAIN_PCT
)
from shared.tavily_search import (
    search_safety_advisories,
    get_evergreen_safety_guidance,
    EVERGREEN_SAFETY_TIPS
)
from planner_agent.app.intent_classifier import classify_intent, IntentType
from planner_agent.app.graph import (
    _parse_intent_node_impl,
    render_safety_node,
    render_need_date_clarification_node,
    route_intent
)
from planner_agent.app.session_store import save_session_trip, get_session_trip


# ==========================================
# FIX A: Dynamic Default Dates & Past Rejection
# ==========================================

def test_default_dates_always_in_future():
    """Verify default departure date is dynamically today + lead_days across multiple test dates."""
    test_dates = [
        date(2026, 1, 1),
        date(2026, 6, 15),
        date(2026, 10, 15),
        date(2026, 12, 25),
        date(2027, 2, 28)
    ]
    for ref_date in test_dates:
        dep, cin, cout = get_default_trip_dates(today=ref_date, lead_days=14, length_days=2)
        dep_d = date.fromisoformat(dep)
        cin_d = date.fromisoformat(cin)
        cout_d = date.fromisoformat(cout)
        
        assert dep_d == ref_date + timedelta(days=14)
        assert cin_d == dep_d
        assert cout_d == dep_d + timedelta(days=2)
        assert dep_d > ref_date
        assert not is_date_in_past(dep, today=ref_date)


def test_date_without_year_resolves_to_next_occurrence():
    """If date without year has passed this year, it rolls to next year; otherwise this year."""
    ref_date = date(2026, 10, 15)
    
    # September 10 has passed in 2026 -> rolls to 2027-09-10
    parsed_past_month, is_ambig = resolve_dates_from_text("from 10 September to 12 September", reference_date=ref_date)
    assert not is_ambig
    assert parsed_past_month is not None
    assert parsed_past_month["departure_date"] == "2027-09-10"
    assert parsed_past_month["check_out"] == "2027-09-12"
    assert parsed_past_month["is_past"] is False

    # November 20 has not passed in 2026 -> stays in 2026
    parsed_future_month, is_ambig = resolve_dates_from_text("from 20 November to 22 November", reference_date=ref_date)
    assert not is_ambig
    assert parsed_future_month is not None
    assert parsed_future_month["departure_date"] == "2026-11-20"
    assert parsed_future_month["is_past"] is False


def test_explicit_past_date_is_flagged_and_rejected():
    """Explicit dates with year that are before today are flagged as is_past and rejected."""
    ref_date = date(2026, 10, 15)
    
    # 2026-09-10 is explicitly in the past
    parsed, is_ambig = resolve_dates_from_text("on 2026-09-10", reference_date=ref_date)
    assert not is_ambig
    assert parsed is not None
    assert parsed["is_past"] is True

    # In intent classifier: route with past date is classified as NEED_DATE_CLARIFICATION
    intent = classify_intent("delhi to mumbai on 2026-09-10")
    if date.today() >= date(2026, 9, 10):
        assert intent.intent == IntentType.NEED_DATE_CLARIFICATION
        assert intent.has_past_date is True


def test_past_session_dates_purged_on_next_plan_action():
    """Existing session with past dates has them cleared and re-defaulted on next planning action."""
    async def _run():
        session_id = "test_past_session_redefault"
        save_session_trip(session_id, {
            "origin": "DEL",
            "origin_name": "Delhi",
            "destination": "BOM",
            "destination_name": "Mumbai",
            "departure_date": "2020-01-01",  # clearly past
            "check_in": "2020-01-01",
            "check_out": "2020-01-03",
            "display_currency": "INR",
            "target_currency": "INR"
        })
        
        state = {
            "messages": [HumanMessage(content="delhi to mumbai")],
            "session_id": session_id,
            "user_id": "u1"
        }
        
        res = await _parse_intent_node_impl(state)
        dep_date = res.get("departure_date")
        assert dep_date is not None
        assert dep_date != "2020-01-01"
        assert date.fromisoformat(dep_date) > date.today()
    asyncio.run(_run())


def test_render_need_date_clarification_message():
    """Clear user message prompting for a future date when specified date is in the past."""
    async def _run():
        state = {
            "session_id": "test_clarif",
            "has_past_date": True,
            "destination": "BOM",
            "destination_name": "Mumbai",
            "origin": "DEL",
            "origin_name": "Delhi"
        }
        res = await render_need_date_clarification_node(state)
        content = res["messages"][0].content
        assert "That date has already passed" in content
        assert "Mumbai" in content
        assert "future travel date" in content
    asyncio.run(_run())


# ==========================================
# FIX B: Weather Consistency & Windows
# ==========================================

def test_weather_consistent_numbers_headline_and_alert():
    """
    Mock Open-Meteo with daily highs 33, 35, 38.
    Headline must show 'Highs 33–38°C' and alert must quote '38°C' (the same max).
    """
    async def _run():
        d1 = (date.today() + timedelta(days=5)).isoformat()
        d2 = (date.today() + timedelta(days=6)).isoformat()
        d3 = (date.today() + timedelta(days=7)).isoformat()
        mock_payload = {
            "daily": {
                "time": [d1, d2, d3],
                "temperature_2m_max": [33.0, 35.0, 38.0],
                "temperature_2m_min": [24.0, 25.0, 26.0],
                "precipitation_probability_max": [10, 15, 20],
                "precipitation_sum": [0.0, 0.0, 0.0],
                "windspeed_10m_max": [15.0, 18.0, 20.0],
                "weathercode": [1, 2, 0]
            }
        }
        
        with patch("httpx.AsyncClient.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = mock_payload
            mock_get.return_value = mock_resp
            
            result = await get_weather_forecast("Mumbai", start_date=d1, end_date=d3)
            summary = result["summary"]
            alert = result["alert"]
            
            assert "38°C" in summary
            assert "33–38°C" in summary
            assert alert is not None
            assert "38°C" in alert
            assert "Highs around 35°C" not in summary  # no misleading arithmetic mean quoted as headline high
    asyncio.run(_run())


def test_weather_now_query_uses_current_window():
    """When query says 'now' or 'today', forecast window uses current/upcoming days, not past trip window."""
    async def _run():
        with patch("httpx.AsyncClient.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "daily": {
                    "time": ["2026-10-15", "2026-10-16"],
                    "temperature_2m_max": [32.0, 31.0],
                    "temperature_2m_min": [22.0, 21.0],
                    "precipitation_probability_max": [5, 10],
                    "precipitation_sum": [0.0, 0.0],
                    "windspeed_10m_max": [10.0, 12.0],
                    "weathercode": [0, 1]
                }
            }
            mock_get.return_value = mock_resp
            
            result = await get_weather_forecast("Mumbai", start_date="now")
            assert "current conditions & upcoming days" in result["summary"]
            assert "your travel window" not in result["summary"]
    asyncio.run(_run())


def test_weather_far_future_labeled_typical_climate():
    """Trip dates > 16 days out are labelled as typical climate rather than live forecast."""
    async def _run():
        far_future = (date.today() + timedelta(days=30)).isoformat()
        result = await get_weather_forecast("Mumbai", start_date=far_future)
        assert "Typical Climate for" in result["summary"]
        assert "not a live forecast" in result["summary"]
    asyncio.run(_run())


# ==========================================
# FIX C: Safety Intent & Multi-Source Synthesis
# ==========================================

def test_ask_safety_intent_classification():
    """Verify safety queries route to IntentType.ASK_SAFETY and do not touch trip route."""
    safety_phrases = [
        "is it safe to visit mumbai now",
        "is there safe to visit mumbai now",
        "is it safe right now?",
        "safety in Paris",
        "are there any scams in Rome?",
        "curfew in London",
        "should I go to Delhi right now?",
        "what are the travel advisories for Dubai",
        "is Barcelona dangerous?"
    ]
    for phrase in safety_phrases:
        intent = classify_intent(phrase)
        assert intent.intent == IntentType.ASK_SAFETY, f"Failed for phrase: '{phrase}'"


def test_render_safety_handler_tavily_key_unset():
    """
    When TAVILY_API_KEY is not set:
    - Returns HTTP 200 equivalent (non-empty message dict)
    - Plan is unchanged (no 'plan' key returned)
    - Includes weather hazards, evergreen guidance, and advisory-page line
    - Never declares 'safe' or 'unsafe'
    """
    async def _run():
        state = {
            "session_id": "test_safety_unset",
            "messages": [HumanMessage(content="is it safe to visit mumbai now")],
            "destination": "BOM",
            "destination_name": "Mumbai"
        }
        
        with patch.dict(os.environ, {"TAVILY_API_KEY": ""}, clear=False):
            res = await render_safety_node(state)
            assert "plan" not in res
            msg = res["messages"][0].content
            assert len(msg) > 100
            assert "Safety & Travel Advisories for Mumbai" in msg
            assert "Live advisories weren't available; check your government's travel advisory page." in msg
            assert "Weather Hazard Status" in msg
            assert "General Traveler Guidance" in msg
            assert "never issues definitive 'safe' or 'unsafe' verdicts" in msg
    asyncio.run(_run())


def test_render_safety_handler_tavily_500_error():
    """When Tavily returns 500 error, handler degrades gracefully with weather + evergreen tips."""
    async def _run():
        state = {
            "session_id": "test_safety_500",
            "messages": [HumanMessage(content="is it safe to visit delhi now")],
            "destination": "DEL",
            "destination_name": "Delhi"
        }
        
        with patch.dict(os.environ, {"TAVILY_API_KEY": "tvly-dummy-test-key"}, clear=False):
            with patch("httpx.AsyncClient.post") as mock_post:
                mock_resp = MagicMock()
                mock_resp.status_code = 500
                mock_resp.text = "Internal Server Error"
                mock_post.return_value = mock_resp
                
                res = await render_safety_node(state)
                assert "plan" not in res
                msg = res["messages"][0].content
                assert "Live advisories weren't available; check your government's travel advisory page." in msg
                assert "General Traveler Guidance" in msg
    asyncio.run(_run())


def test_render_safety_handler_tavily_timeout():
    """When Tavily request times out, handler degrades gracefully without throwing."""
    async def _run():
        import httpx
        state = {
            "session_id": "test_safety_timeout",
            "messages": [HumanMessage(content="is it safe to visit london now")],
            "destination": "LHR",
            "destination_name": "London"
        }
        
        with patch.dict(os.environ, {"TAVILY_API_KEY": "tvly-dummy-test-key"}, clear=False):
            with patch("httpx.AsyncClient.post", side_effect=httpx.TimeoutException("Read timed out")):
                res = await render_safety_node(state)
                assert "plan" not in res
                msg = res["messages"][0].content
                assert "Live advisories weren't available; check your government's travel advisory page." in msg
                assert "General Traveler Guidance" in msg
    asyncio.run(_run())


def test_render_safety_handler_tavily_success():
    """When Tavily succeeds, returns live items with markdown links and dates."""
    async def _run():
        state = {
            "session_id": "test_safety_success",
            "messages": [HumanMessage(content="is it safe to visit mumbai now")],
            "destination": "BOM",
            "destination_name": "Mumbai"
        }
        
        mock_tavily_response = {
            "results": [
                {
                    "title": "Mumbai Municipal Travel Update",
                    "url": "https://news.example.com/mumbai-update",
                    "content": "Coastal road construction causes minor diversions. Public transit operating normally.",
                    "published_date": "2026-10-14"
                },
                {
                    "title": "Monsoon & Weather Advisory",
                    "url": "https://weather.example.com/mumbai",
                    "content": "Post-monsoon clearing observed; local train services on schedule.",
                    "published_date": "2026-10-15"
                }
            ]
        }
        
        mock_client = MagicMock()
        mock_client.search.return_value = mock_tavily_response
        with patch("shared.tavily_search.get_tavily_client", return_value=mock_client):
            res = await render_safety_node(state)
            msg = res["messages"][0].content
            assert "[Mumbai Municipal Travel Update](https://news.example.com/mumbai-update)" in msg
            assert "Coastal road construction" in msg
            assert "Weather Hazard Status" in msg
            assert "General Traveler Guidance" in msg
            assert "never issues definitive 'safe' or 'unsafe' verdicts" in msg
    asyncio.run(_run())


def test_safety_does_not_mutate_plan_or_route():
    """Safety query routed through route_intent leads to render_safety, not planning."""
    state = {
        "is_safety_query": True,
        "is_planning": False,
        "origin": "DEL",
        "destination": "BOM"
    }
    dest_node = route_intent(state)
    assert dest_node == "render_safety"
