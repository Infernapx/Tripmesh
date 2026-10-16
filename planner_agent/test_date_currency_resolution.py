import pytest
import json
from datetime import datetime
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient
from planner_agent.app.main import app
from planner_agent.app.session_store import clear_session_trip, get_session_trip
from shared.resolvers import resolve_dates_from_text, resolve_currency_name

client = TestClient(app)


def test_resolve_dates_dynamic_current_year():
    now_year = datetime.now().year
    parsed, amb = resolve_dates_from_text("on 15 September")
    assert amb is False
    assert parsed is not None
    assert parsed["check_in"] == f"{now_year}-09-15"
    assert parsed["check_out"] == f"{now_year}-09-17"

    # With explicit year override in text
    parsed_override, amb_override = resolve_dates_from_text("on 15 September 2028")
    assert amb_override is False
    assert parsed_override is not None
    assert parsed_override["check_in"] == "2028-09-15"
    assert parsed_override["check_out"] == "2028-09-17"


def test_resolve_dates_various_formats():
    now_year = datetime.now().year
    formats = [
        "15 September",
        "September 15",
        f"15/09/{now_year}",
        f"{now_year}-09-15",
        f"15th September {now_year}",
        "September 15th"
    ]
    for fmt in formats:
        parsed, amb = resolve_dates_from_text(f"trip to Paris on {fmt}")
        assert amb is False, f"Failed on format: {fmt}"
        assert parsed is not None, f"Failed on format: {fmt}"
        assert parsed["check_in"].endswith("-09-15"), f"Expected check_in to end with -09-15 for {fmt}, got {parsed['check_in']}"


def test_resolve_multiword_currency_phrases():
    assert resolve_currency_name("I want in Indian rupees") == "INR"
    assert resolve_currency_name("convert to US dollars please") == "USD"
    assert resolve_currency_name("give prices in British pounds") == "GBP"
    assert resolve_currency_name("show in Japanese yen") == "JPY"
    assert resolve_currency_name("in Euros") == "EUR"


def test_unparseable_ambiguous_date_asks_for_clarification():
    session_id = "test-ambiguous-date-session"
    clear_session_trip(session_id)

    # User gives ambiguous date: "Delhi to Mumbai on someday soon"
    res = client.post(
        "/chat",
        json={"message": "Delhi to Mumbai on someday soon", "session_id": session_id, "user_id": "test-user-date"}
    )
    assert res.status_code == 200
    data = res.json()
    assert "what date" in data["response"].lower() or "clarify" in data["response"].lower()
    
    session = get_session_trip(session_id)
    assert session.get("pending_slot") == "date"


@pytest.mark.anyio
async def test_oneshot_date_and_currency_combined_repro():
    """
    Exact repro:
    'Delhi to Mumbai on 15 September, I want in Indian rupees'
    Must parse departure/check-in date as 15 September of current year,
    and display_currency / target_currency as INR, returning structured TripPlanPayload.
    """
    session_id = "test-oneshot-combined-repro"
    clear_session_trip(session_id)
    now_year = datetime.now().year

    with patch("planner_agent.app.graph.call_transport_agent_a2a", new_callable=AsyncMock) as mock_t, \
         patch("planner_agent.app.graph.call_hotel_agent_a2a", new_callable=AsyncMock) as mock_h, \
         patch("planner_agent.app.graph.call_local_guide_agent_a2a", new_callable=AsyncMock) as mock_g, \
         patch("planner_agent.app.graph.call_single_currency_rate_a2a", new_callable=AsyncMock) as mock_rate:

        mock_t.return_value = json.dumps({
            "success": True,
            "results": [{
                "mode": "flight",
                "origin": {"iata": "DEL"},
                "destination": {"iata": "BOM"},
                "departure_at": f"{now_year}-09-15T08:00:00",
                "arrival_at": f"{now_year}-09-15T10:30:00",
                "duration_minutes": 150,
                "price": {"amount": 5000.0, "currency": "INR"},
                "carrier": "IndiGo",
                "is_estimated": False
            }]
        })
        mock_h.return_value = json.dumps({
            "success": True,
            "results": [{
                "name": "Taj Mahal Palace Mumbai",
                "rating": 4.8,
                "check_in": f"{now_year}-09-15",
                "check_out": f"{now_year}-09-17",
                "price_per_night": {"amount": 12000.0, "currency": "INR"},
                "total_price": {"amount": 24000.0, "currency": "INR"},
                "amenities": ["Pool", "Spa"],
                "is_estimated": False
            }]
        })
        mock_g.return_value = json.dumps({"success": True, "results": []})
        mock_rate.return_value = 1.0

        resp = client.post(
            "/chat",
            json={
                "message": "Delhi to Mumbai on 15 September, I want in Indian rupees",
                "session_id": session_id,
                "user_id": "test-user-repro"
            }
        )
        assert resp.status_code == 200
        data = resp.json()

        # 1. Text checks: currency INR and date 15 September
        assert "INR" in data["response"] or "₹" in data["response"]
        assert "2026-09-10" not in data["response"]  # Not default date
        assert f"{now_year}-09-15" in data["response"] or "15 Sep" in data["response"] or "September 15" in data["response"]

        # 2. Structured plan payload check (Rules.md §1 typing discipline)
        assert data.get("plan") is not None
        plan = data["plan"]
        assert plan["origin"] == "DEL"
        assert plan["destination"] == "BOM"
        assert plan["departure_date"] == f"{now_year}-09-15"
        assert plan["check_in"] == f"{now_year}-09-15"
        assert plan["display_currency"] == "INR"
        assert plan["target_currency"] == "INR"
        assert len(plan["transport_results"]) > 0
        assert len(plan["hotel_results"]) > 0
