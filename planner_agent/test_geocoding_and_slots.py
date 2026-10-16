import pytest
import json
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient
from planner_agent.app.main import app
from planner_agent.app.session_store import clear_session_trip
from shared.resolvers import validate_and_resolve_city
from shared.geocoding import geocode_city_to_iata

client = TestClient(app)


def test_resolve_city_not_in_static_map():
    # Test geocoding and OurAirports lookup for cities
    res_pune = validate_and_resolve_city("Pune")
    assert res_pune is not None
    assert res_pune[1] == "PNQ"

    res_kolkata = validate_and_resolve_city("Kolkata")
    assert res_kolkata is not None
    assert res_kolkata[1] == "CCU"

    res_seattle = validate_and_resolve_city("Seattle")
    assert res_seattle is not None
    assert res_seattle[1] == "SEA"


def test_resolve_nonsense_still_rejected():
    # Assert nonsense words and non-places return None
    assert validate_and_resolve_city("tell") is None
    assert validate_and_resolve_city("visit") is None
    assert validate_and_resolve_city("rupees") is None
    assert validate_and_resolve_city("weather") is None
    assert validate_and_resolve_city("sounds") is None


@pytest.mark.anyio
async def test_slot_fill_destination_and_origin_flow():
    """
    Test step-by-step intake:
    Turn 1: "I want to see some temples" -> bot asks destination (pending_slot="destination")
    Turn 2: "pune" -> destination set to Pune, bot asks origin (pending_slot="origin")
    Turn 3: "mumbai" -> origin set to Mumbai, plan rendered for BOM -> PNQ
    """
    session_id = "test-slot-filling-session"
    clear_session_trip(session_id)

    with patch("planner_agent.app.graph.call_transport_agent_a2a", new_callable=AsyncMock) as mock_t, \
         patch("planner_agent.app.graph.call_hotel_agent_a2a", new_callable=AsyncMock) as mock_h, \
         patch("planner_agent.app.graph.call_local_guide_agent_a2a", new_callable=AsyncMock) as mock_g, \
         patch("planner_agent.app.graph.call_single_currency_rate_a2a", new_callable=AsyncMock) as mock_rate:

        mock_t.return_value = json.dumps({
            "success": True,
            "results": [{
                "carrier": "Air India",
                "origin": {"iata": "BOM"},
                "destination": {"iata": "PNQ"},
                "departure_at": "2026-09-10T08:00:00",
                "arrival_at": "2026-09-10T09:00:00",
                "duration_minutes": 60,
                "price": {"amount": 40.0, "currency": "EUR"},
                "is_estimated": False
            }]
        })
        mock_h.return_value = json.dumps({
            "success": True,
            "results": [{
                "name": "Pune Grand Heritage Hotel",
                "rating": 4.5,
                "check_in": "2026-09-10",
                "check_out": "2026-09-12",
                "price_per_night": {"amount": 45.0, "currency": "EUR"},
                "total_price": {"amount": 90.0, "currency": "EUR"},
                "amenities": ["WiFi", "Pool"],
                "is_estimated": False
            }]
        })
        mock_g.return_value = json.dumps({"success": True, "results": []})
        mock_rate.return_value = 1.0

        # Turn 1: "I want to see some temples" (no prior context)
        r1 = client.post("/chat", json={"message": "I want to see some temples", "session_id": session_id, "user_id": "test-geo-user"})
        assert r1.status_code == 200
        msg1 = r1.json()["response"]
        assert "Where would you like to travel?" in msg1

        # Turn 2: "pune" (fills destination slot)
        r2 = client.post("/chat", json={"message": "pune", "session_id": session_id, "user_id": "test-geo-user"})
        assert r2.status_code == 200
        msg2 = r2.json()["response"]
        assert "heading to Pune" in msg2
        assert "Where are you traveling from?" in msg2

        # Turn 3: "mumbai" (fills origin slot, completes trip plan)
        r3 = client.post("/chat", json={"message": "mumbai", "session_id": session_id, "user_id": "test-geo-user"})
        assert r3.status_code == 200
        msg3 = r3.json()["response"]
        assert "Trip Plan" in msg3
        assert "BOM to PNQ" in msg3
        assert "Pune Grand Heritage Hotel" in msg3


@pytest.mark.anyio
async def test_slot_fill_ignored_for_new_topic():
    """If user asks something unrelated while pending_slot is set, fall through to normal classification."""
    session_id = "test-slot-ignore-session"
    clear_session_trip(session_id)

    # Prompt user for destination
    r1 = client.post("/chat", json={"message": "plan a trip", "session_id": session_id, "user_id": "test-geo-user"})
    assert "Where would you like to travel?" in r1.json()["response"]

    # User asks something unrelated instead of giving a city
    r2 = client.post("/chat", json={"message": "what's the weather like", "session_id": session_id, "user_id": "test-geo-user"})
    assert r2.status_code == 200
    msg2 = r2.json()["response"]
    assert "Where would you like to travel?" in msg2


@pytest.mark.anyio
async def test_full_route_still_works_without_slot_filling():
    """One-shot route queries work immediately in a single turn."""
    session_id = "test-oneshot-session"
    clear_session_trip(session_id)

    with patch("planner_agent.app.graph.call_transport_agent_a2a", new_callable=AsyncMock) as mock_t, \
         patch("planner_agent.app.graph.call_hotel_agent_a2a", new_callable=AsyncMock) as mock_h, \
         patch("planner_agent.app.graph.call_local_guide_agent_a2a", new_callable=AsyncMock) as mock_g, \
         patch("planner_agent.app.graph.call_single_currency_rate_a2a", new_callable=AsyncMock) as mock_rate:

        mock_t.return_value = json.dumps({
            "success": True,
            "results": [{
                "carrier": "Air India",
                "origin": {"iata": "DEL"},
                "destination": {"iata": "BOM"},
                "departure_at": "2026-09-10T08:00:00",
                "arrival_at": "2026-09-10T10:00:00",
                "duration_minutes": 120,
                "price": {"amount": 60.0, "currency": "EUR"},
                "is_estimated": False
            }]
        })
        mock_h.return_value = json.dumps({
            "success": True,
            "results": [{
                "name": "Mumbai Inn",
                "rating": 4.2,
                "check_in": "2026-09-10",
                "check_out": "2026-09-12",
                "price_per_night": {"amount": 50.0, "currency": "EUR"},
                "total_price": {"amount": 100.0, "currency": "EUR"},
                "amenities": ["WiFi"],
                "is_estimated": False
            }]
        })
        mock_g.return_value = json.dumps({"success": True, "results": []})
        mock_rate.return_value = 1.0

        res = client.post("/chat", json={"message": "delhi to mumbai", "session_id": session_id, "user_id": "test-geo-user"})
        assert res.status_code == 200
        msg = res.json()["response"]
        assert "DEL to BOM" in msg
        assert "Mumbai Inn" in msg
