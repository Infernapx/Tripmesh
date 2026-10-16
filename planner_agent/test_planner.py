import pytest
import json
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage
from planner_agent.app.main import app
from planner_agent.app.graph import extract_budget_intent, planner_graph
from planner_agent.app.a2a_client import _extract_plan_details
from planner_agent.app.session_store import clear_session_trip
from shared.resolvers import validate_and_resolve_city

client = TestClient(app)


def test_root():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["service"] == "planner_agent"


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_extraction_rejects_unresolvable_destination():
    # 1. Non-city words and currency names must be rejected by validator
    assert validate_and_resolve_city("rupees") is None
    assert validate_and_resolve_city("inr") is None
    assert validate_and_resolve_city("dollars") is None
    assert validate_and_resolve_city("somewhere") is None
    assert validate_and_resolve_city("it") is None
    assert validate_and_resolve_city("there") is None
    assert validate_and_resolve_city("next week") is None
    assert validate_and_resolve_city("budget") is None

    # 2. Legitimate cities must be resolved accurately
    assert validate_and_resolve_city("delhi") == ("Delhi", "DEL")
    assert validate_and_resolve_city("mumbai") == ("Mumbai", "BOM")
    assert validate_and_resolve_city("new york") == ("New York", "JFK")
    assert validate_and_resolve_city("los angeles") == ("Los Angeles", "LAX")
    assert validate_and_resolve_city("cairo") == ("Cairo", "CAI")


def test_extract_budget_intent():
    # 1. Dollar symbol
    cap, target_curr, disp_curr = extract_budget_intent("Plan a trip to Lisbon under $800 from 2026-09-10 to 2026-09-12")
    assert cap == 800.0
    assert target_curr == "USD"
    assert disp_curr == "USD"

    # 2. Euro code
    cap, target_curr, disp_curr = extract_budget_intent("Plan a trip to Paris budget 600 EUR")
    assert cap == 600.0
    assert target_curr == "EUR"
    assert disp_curr == "EUR"

    # 3. Pound symbol
    cap, target_curr, disp_curr = extract_budget_intent("London trip under £500")
    assert cap == 500.0
    assert target_curr == "GBP"
    assert disp_curr == "GBP"

    # 4. Yen symbol
    cap, target_curr, disp_curr = extract_budget_intent("Tokyo vacation max ¥50000")
    assert cap == 50000.0
    assert target_curr == "JPY"
    assert disp_curr == "JPY"

    # 5. Colloquial phrasing
    cap, target_curr, disp_curr = extract_budget_intent("Trip to Barcelona under a grand")
    assert cap == 1000.0
    assert target_curr == "USD"
    assert disp_curr == "USD"

    # 6. Direct currency request without budget
    cap, target_curr, disp_curr = extract_budget_intent("delhi to mumbai in rupees")
    assert cap is None
    assert disp_curr == "INR"

    cap, target_curr, disp_curr = extract_budget_intent("i want in rupees")
    assert cap is None
    assert disp_curr == "INR"


def test_route_extraction_multiword_and_unlisted_cities():
    # Multi-word city names
    details_ny_la = _extract_plan_details("Plan a trip from New York to Los Angeles next week")
    assert details_ny_la["origin"] == "JFK"
    assert details_ny_la["origin_name"] == "New York"
    assert details_ny_la["destination"] == "LAX"
    assert details_ny_la["destination_name"] == "Los Angeles"

    # Multi-word international city names
    details_sp_mex = _extract_plan_details("Sao Paulo to Mexico City")
    assert details_sp_mex["origin"] == "GRU"
    assert details_sp_mex["origin_name"] == "Sao Paulo"
    assert details_sp_mex["destination"] == "MEX"
    assert details_sp_mex["destination_name"] == "Mexico City"

    # Unlisted city fallback policy: clean name & pseudo-code, NEVER substitute hardcoded LHR/LIS
    details_cairo_nairobi = _extract_plan_details("Cairo to Nairobi")
    assert details_cairo_nairobi["origin"] == "CAI"
    assert details_cairo_nairobi["origin_name"] == "Cairo"
    assert details_cairo_nairobi["destination"] == "NBO"
    assert details_cairo_nairobi["destination_name"] == "Nairobi"


@pytest.mark.anyio
async def test_planner_followup_no_route_no_prior_context():
    # Fresh session with currency-only query and no prior context
    clear_session_trip("test-fresh-sess")
    res = client.post("/chat", json={"message": "i want in rupees", "session_id": "test-fresh-sess", "user_id": "test-planner-user-fresh"})
    assert res.status_code == 200
    msg = res.json()["response"]
    assert "Where would you like to travel?" in msg
    assert "INR" in msg


@pytest.mark.anyio
async def test_planner_followup_currency_only_preserves_route():
    session_id = "test-multiturn-session"
    clear_session_trip(session_id)

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
                "departure_at": "2026-09-10T08:00:00",
                "arrival_at": "2026-09-10T10:45:00",
                "duration_minutes": 165,
                "price": {"amount": 80.0, "currency": "EUR"},
                "carrier": "Air India",
                "is_estimated": False
            }]
        })
        mock_h.return_value = json.dumps({
            "success": True,
            "results": [{
                "name": "Mumbai Central Inn",
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
        mock_rate.return_value = 90.0

        # Turn 1: "delhi to mumbai"
        res1 = client.post("/chat", json={"message": "delhi to mumbai", "session_id": session_id, "user_id": "test-planner-user-mt"})
        assert res1.status_code == 200
        msg1 = res1.json()["response"]
        assert "DEL to BOM" in msg1
        assert "Mumbai Central Inn" in msg1

        # Turn 2: "i want in rupees" (no city mentioned, should preserve DEL->BOM)
        res2 = client.post("/chat", json={"message": "i want in rupees", "session_id": session_id, "user_id": "test-planner-user-mt"})
        assert res2.status_code == 200
        msg2 = res2.json()["response"]
        assert "DEL to BOM" in msg2
        assert "Mumbai Central Inn" in msg2
        assert "7200.0 INR" in msg2 or "7200 INR" in msg2
