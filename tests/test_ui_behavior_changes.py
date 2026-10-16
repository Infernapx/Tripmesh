"""
tests/test_ui_behavior_changes.py
---------------------------------
Validation suite for the 3 UI/behavior changes:
Change 1: Automatic one-line weather tip with every plan
Change 2: "Change Dates" opens calendar & sends structured change_dates payload
Change 3: Hide Budget Boundary Status unless budget requested/exists
"""

import pytest
import asyncio
from datetime import date, timedelta
from unittest.mock import patch, AsyncMock, MagicMock
from langchain_core.messages import HumanMessage

from shared.weather import (
    generate_weather_tip,
    get_weather_forecast,
)
from shared.resolvers import get_default_trip_dates
from planner_agent.app.structured_classifier import classify_with_rules
from planner_agent.app.graph import (
    _parse_intent_node_impl,
    _render_final_plan_node_impl,
    check_budget_node,
    route_budget_decision,
)
from planner_agent.app.session_store import save_session_trip, get_session_trip, reset_session_trip


# =========================================================================
# CHANGE 1: Deterministic One-Line Weather Tip
# =========================================================================

def test_weather_tip_extreme_heat():
    """Extreme heat (>= 38°C) triggers hydration & avoid midday tip."""
    tip = generate_weather_tip(
        destination="Mumbai",
        max_high=38.5,
        min_high=35.0,
        min_low=30.0,
        max_low=32.0,
        rain_prob=10,
        max_precip=0.0,
        is_typical_climate=False
    )
    assert tip is not None
    assert "Mumbai" in tip
    assert "38" in tip or "39" in tip
    assert "hydrate" in tip.lower() or "midday" in tip.lower()
    assert len(tip) <= 110


def test_weather_tip_moderate_heat():
    """Heat (>= 32°C and < 38°C) triggers light clothes & sunscreen tip."""
    tip = generate_weather_tip(
        destination="Mumbai",
        max_high=35.0,
        min_high=33.0,
        min_low=26.0,
        max_low=28.0,
        rain_prob=15,
        max_precip=0.0,
        is_typical_climate=False
    )
    assert tip is not None
    assert "Mumbai" in tip
    assert "cotton" in tip.lower() or "sunscreen" in tip.lower()


def test_weather_tip_rain_priority():
    """Rain probability >= 40% triggers umbrella tip."""
    tip = generate_weather_tip(
        destination="London",
        max_high=20.0,
        min_high=18.0,
        min_low=14.0,
        max_low=15.0,
        rain_prob=70,
        max_precip=12.0,
        is_typical_climate=False
    )
    assert tip is not None
    assert "umbrella" in tip.lower() or "rain" in tip.lower()


def test_weather_tip_cold():
    """Cold (<= 10°C) triggers warm layers tip."""
    tip = generate_weather_tip(
        destination="Paris",
        max_high=9.0,
        min_high=7.0,
        min_low=2.0,
        max_low=4.0,
        rain_prob=10,
        max_precip=0.0,
        is_typical_climate=False
    )
    assert tip is not None
    assert "chilly" in tip.lower() or "layers" in tip.lower() or "Paris" in tip


def test_weather_tip_far_future_uses_usually_climate():
    """Beyond forecast window (~16 days) phrases as 'Usually...' so it does not claim to be a live forecast."""
    tip = generate_weather_tip(
        destination="Mumbai",
        max_high=38.0,
        min_high=34.0,
        min_low=30.0,
        max_low=32.0,
        rain_prob=10,
        max_precip=0.0,
        is_typical_climate=True,
        month_name="June"
    )
    assert tip is not None
    assert tip.startswith("Usually")
    assert "June" in tip


def test_weather_tip_same_temperature_as_detailed_answer():
    """Verifies single source of truth: weather_tip uses the exact same max temp as detailed forecast."""
    async def _run():
        mock_data = {
            "daily": {
                "time": ["2026-11-01", "2026-11-02"],
                "temperature_2m_max": [35.4, 38.0],
                "temperature_2m_min": [24.1, 25.2],
                "precipitation_sum": [0.0, 0.0],
                "precipitation_probability_max": [10, 15],
                "weathercode": [0, 0]
            }
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_data

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp

        with patch("shared.weather.geocode_destination_coords", return_value=(38.7223, -9.1393, "Lisbon")):
            res = await get_weather_forecast("Lisbon", start_date="2026-11-01", end_date="2026-11-02", client=mock_client)
            assert "weather_tip" in res
            tip = res["weather_tip"]
            summary = res["summary"]
            assert "38°C" in tip
            assert "38°C" in summary
    asyncio.run(_run())


def test_weather_api_failure_does_not_break_plan():
    """If Open-Meteo helper fails/times out, plan rendering succeeds with weather_tip = None."""
    async def _run():
        session_id = "test-fail-weather"
        reset_session_trip(session_id)
        save_session_trip(session_id, {
            "origin": "DEL",
            "origin_name": "Delhi",
            "destination": "BOM",
            "destination_name": "Mumbai",
            "departure_date": "2026-11-10",
            "check_in": "2026-11-10",
            "check_out": "2026-11-14",
        })

        state = {
            "session_id": session_id,
            "messages": [HumanMessage(content="Plan Delhi to Mumbai")],
            "origin": "DEL",
            "origin_name": "Delhi",
            "destination": "BOM",
            "destination_name": "Mumbai",
            "departure_date": "2026-11-10",
            "check_in": "2026-11-10",
            "check_out": "2026-11-14",
            "budget_requested": False,
            "budget_cap": None,
            "target_currency": "INR",
            "display_currency": "INR",
            "cached_transport_results": [{
                "carrier": "Air India",
                "price": {"amount": 5000, "currency": "INR"}
            }],
            "cached_hotel_results": [{
                "name": "Taj Mahal Palace",
                "price_per_night": {"amount": 10000, "currency": "INR"},
                "total_price": {"amount": 40000, "currency": "INR"}
            }],
            "cached_local_guide_results": [],
            "cost_line_items": [
                {"amount": 5000, "currency": "INR"},
                {"amount": 40000, "currency": "INR"}
            ]
        }

        with patch("shared.weather.get_weather_forecast", side_effect=Exception("Timeout 504")):
            res = await _render_final_plan_node_impl(state)
            assert "plan" in res
            plan = res["plan"]
            assert plan is not None
            assert plan.get("weather_tip") is None
            assert plan.get("origin") == "DEL"
            assert plan.get("destination") == "BOM"
    asyncio.run(_run())


# =========================================================================
# CHANGE 2: Calendar Date Picker & structured change_dates payload
# =========================================================================

def test_change_dates_structured_payload_updates_plan():
    """Applying valid range from date picker sends {type: 'change_dates'} and updates departure & return dates."""
    async def _run():
        session_id = "test-change-dates-valid"
        reset_session_trip(session_id)
        save_session_trip(session_id, {
            "origin": "DEL",
            "origin_name": "Delhi",
            "destination": "BOM",
            "destination_name": "Mumbai",
            "departure_date": "2026-11-10",
            "check_in": "2026-11-10",
            "check_out": "2026-11-13",
            "budget_requested": False,
            "budget_cap": None,
        })

        future_dep = (date.today() + timedelta(days=25)).isoformat()
        future_ret = (date.today() + timedelta(days=29)).isoformat()

        state = {
            "session_id": session_id,
            "messages": [HumanMessage(content=f"Dates changed to {future_dep} - {future_ret}")],
            "structured_payload": {
                "type": "change_dates",
                "departure": future_dep,
                "return": future_ret
            }
        }

        parsed = await _parse_intent_node_impl(state)
        assert parsed.get("departure_date") == future_dep
        assert parsed.get("check_out") == future_ret
    asyncio.run(_run())


def test_change_dates_rejects_past_date():
    """Server validation rejects departure dates in the past with a clear message and leaves plan unchanged."""
    async def _run():
        session_id = "test-change-dates-past"
        reset_session_trip(session_id)
        original_plan = {
            "origin": "DEL",
            "destination": "BOM",
            "departure_date": "2026-11-10",
            "check_out": "2026-11-15",
        }
        save_session_trip(session_id, {
            **original_plan,
            "confirmed_plan": original_plan
        })

        past_date = (date.today() - timedelta(days=3)).isoformat()
        future_ret = (date.today() + timedelta(days=5)).isoformat()

        state = {
            "session_id": session_id,
            "messages": [HumanMessage(content="Dates changed to past")],
            "structured_payload": {
                "type": "change_dates",
                "departure": past_date,
                "return": future_ret
            }
        }

        parsed = await _parse_intent_node_impl(state)
        assert "past" in parsed["messages"][0].content.lower()
        # Plan is preserved and not updated
        assert parsed.get("plan") is not None
        assert parsed["plan"].get("departure_date") == "2026-11-10"
    asyncio.run(_run())


def test_change_dates_rejects_inverted_range():
    """Server validation rejects return date before departure date with clear error message."""
    async def _run():
        session_id = "test-change-dates-inverted"
        reset_session_trip(session_id)
        original_plan = {
            "origin": "DEL",
            "destination": "BOM",
            "departure_date": "2026-11-10",
            "check_out": "2026-11-15",
        }
        save_session_trip(session_id, {
            **original_plan,
            "confirmed_plan": original_plan
        })

        dep_date = (date.today() + timedelta(days=20)).isoformat()
        inverted_ret = (date.today() + timedelta(days=15)).isoformat()

        state = {
            "session_id": session_id,
            "messages": [HumanMessage(content="Dates changed to inverted")],
            "structured_payload": {
                "type": "change_dates",
                "departure": dep_date,
                "return": inverted_ret
            }
        }

        parsed = await _parse_intent_node_impl(state)
        assert "earlier" in parsed["messages"][0].content.lower() or "return" in parsed["messages"][0].content.lower()
        # Plan preserved
        assert parsed.get("plan") is not None
        assert parsed["plan"].get("departure_date") == "2026-11-10"
    asyncio.run(_run())


# =========================================================================
# CHANGE 3: Hide Budget Boundary Status unless budget requested
# =========================================================================

def test_plan_with_no_budget_has_no_budget_fields():
    """Initial plan for 'delhi to mumbai' without budget has budget=None and budget_status=None."""
    async def _run():
        session_id = "test-no-budget-plan"
        reset_session_trip(session_id)

        state = {
            "session_id": session_id,
            "messages": [HumanMessage(content="delhi to mumbai")],
        }
        parsed = await _parse_intent_node_impl(state)
        assert parsed.get("budget_requested") is False
        assert parsed.get("budget_cap") is None

        # Check budget node skipping
        recon_result = await check_budget_node(parsed)
        assert recon_result.get("budget_reconciliation") is None

        # Check routing skips negotiation loop
        next_node = route_budget_decision({**parsed, **recon_result})
        assert next_node == "render_final_plan"

        # Render final plan
        render_input = {
            **parsed,
            "cached_transport_results": [{"carrier": "Indigo", "price": {"amount": 4000, "currency": "INR"}}],
            "cached_hotel_results": [{"name": "Ginger", "price_per_night": {"amount": 3000, "currency": "INR"}, "total_price": {"amount": 9000, "currency": "INR"}}],
            "cost_line_items": [{"amount": 4000, "currency": "INR"}, {"amount": 9000, "currency": "INR"}]
        }
        rendered = await _render_final_plan_node_impl(render_input)
        plan = rendered["plan"]
        assert plan.get("budget") is None
        assert plan.get("budget_status") is None
        assert plan.get("budget_cap") is None
        assert plan.get("budget_reconciliation") is None
    asyncio.run(_run())


def test_plan_with_budget_shows_banner_and_status():
    """When user sets budget (e.g. 'delhi to mumbai budget 30000 rupees'), budget is recorded and status is populated."""
    async def _run():
        session_id = "test-budget-30000"
        reset_session_trip(session_id)

        # First plan trip with budget
        state = {
            "session_id": session_id,
            "messages": [HumanMessage(content="delhi to mumbai budget 30000 rupees")],
        }
        parsed = await _parse_intent_node_impl(state)
        assert parsed.get("budget_requested") is True
        assert parsed.get("budget_cap") == 30000.0

        render_input = {
            **parsed,
            "cached_transport_results": [{"carrier": "Indigo", "price": {"amount": 5000, "currency": "INR"}}],
            "cached_hotel_results": [{"name": "Taj", "price_per_night": {"amount": 6000, "currency": "INR"}, "total_price": {"amount": 18000, "currency": "INR"}}],
            "cost_line_items": [{"amount": 5000, "currency": "INR"}, {"amount": 18000, "currency": "INR"}]
        }
        rendered = await _render_final_plan_node_impl(render_input)
        plan = rendered["plan"]
        assert plan.get("budget") == 30000.0
        assert plan.get("budget_status") == "Within Budget Target"
    asyncio.run(_run())


def test_cheaper_hotel_without_budget_keeps_banner_hidden():
    """Requesting 'cheaper hotel' when no budget exists keeps budget_requested=False and budget_status=None."""
    async def _run():
        session_id = "test-cheaper-no-budget"
        reset_session_trip(session_id)
        save_session_trip(session_id, {
            "origin": "DEL",
            "destination": "BOM",
            "budget_requested": False,
            "budget_cap": None,
            "budget": None,
            "budget_status": None,
            "awaiting_confirmation": True
        })

        state = {
            "session_id": session_id,
            "messages": [HumanMessage(content="cheaper hotel")],
        }
        parsed = await _parse_intent_node_impl(state)
        assert parsed.get("budget_requested") is False
        assert parsed.get("budget_cap") is None
    asyncio.run(_run())


def test_new_route_resets_budget():
    """A new route resets budget_requested to False unless user explicitly states a new budget."""
    async def _run():
        session_id = "test-new-route-reset"
        reset_session_trip(session_id)
        # Session had established budget of 50000
        save_session_trip(session_id, {
            "origin": "DEL",
            "destination": "BOM",
            "budget_requested": True,
            "budget_cap": 50000.0,
            "budget": 50000.0,
            "budget_status": "Within Budget Target"
        })

        # User starts a new route with NO budget stated
        state = {
            "session_id": session_id,
            "messages": [HumanMessage(content="London to Paris")],
        }
        parsed = await _parse_intent_node_impl(state)
        assert parsed.get("budget_requested") is False
        assert parsed.get("budget_cap") is None
    asyncio.run(_run())


def test_remove_budget_clears_flag():
    """Saying 'remove budget' or 'no budget' sets budget_requested=False and clears budget_cap."""
    async def _run():
        session_id = "test-remove-budget"
        reset_session_trip(session_id)
        save_session_trip(session_id, {
            "origin": "DEL",
            "destination": "BOM",
            "budget_requested": True,
            "budget_cap": 30000.0,
            "awaiting_confirmation": True
        })

        state = {
            "session_id": session_id,
            "messages": [HumanMessage(content="remove budget")],
        }
        parsed = await _parse_intent_node_impl(state)
        assert parsed.get("budget_requested") is False
        assert parsed.get("budget_cap") is None
    asyncio.run(_run())
