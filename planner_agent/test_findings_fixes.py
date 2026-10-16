import json
import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient

from app.main import app
from app.session_store import (
    get_session_trip,
    save_session_trip,
    confirm_session_trip,
    clear_session_trip,
    is_awaiting_confirmation,
    set_awaiting_confirmation
)
from app.intent_classifier import classify_intent, IntentType

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_sessions():
    sessions = [
        "test-f1-a", "test-f1-b", "test-f1-variants",
        "test-f2-clarif", "test-f4-corrupt", "test-f5-stale",
        "test-f6-currency", "test-f7-delay"
    ]
    for s in sessions:
        clear_session_trip(s)
    yield
    for s in sessions:
        clear_session_trip(s)


# ============================================================================
# Finding 1 Tests: "show me" phrasing & local guide routing
# ============================================================================

def test_finding1_repro_a_show_me_route_and_currency():
    """
    Repro A: 'Delhi to Mumbai on 15 September, show me prices in Indian rupees'
    Asserts:
    - Intent is NEW_TRIP_ROUTE (not ASK_ABOUT_DESTINATION)
    - Origin is DEL (Delhi), Destination is BOM (Mumbai)
    - Target currency is INR
    - Does not route to local guide fallback
    """
    msg = "Delhi to Mumbai on 15 September, show me prices in Indian rupees"
    classified = classify_intent(msg)
    assert classified.intent == IntentType.NEW_TRIP_ROUTE
    assert classified.origin == "DEL"
    assert classified.destination == "BOM"
    assert classified.display_currency == "INR"

    # Also test full endpoint mock execution: asserts plan is returned, NOT local guide fallback
    with patch("app.graph.call_transport_agent_a2a", new_callable=AsyncMock) as mock_t, \
         patch("app.graph.call_hotel_agent_a2a", new_callable=AsyncMock) as mock_h, \
         patch("app.graph.call_local_guide_agent_a2a", new_callable=AsyncMock) as mock_g, \
         patch("app.graph.call_budget_reconciliation_a2a", new_callable=AsyncMock) as mock_b:

        mock_t.return_value = json.dumps({"results": [{"carrier": "Air India", "price": {"amount": 4500, "currency": "INR"}, "departure_at": "2026-09-15T08:00", "arrival_at": "2026-09-15T10:15", "duration_minutes": 135, "origin": {"iata": "DEL"}, "destination": {"iata": "BOM"}}]})
        mock_h.return_value = json.dumps({"results": [{"name": "Mumbai Sea View", "rating": 4.5, "check_in": "2026-09-15", "check_out": "2026-09-17", "price_per_night": {"amount": 3000, "currency": "INR"}, "total_price": {"amount": 6000, "currency": "INR"}, "amenities": ["WiFi"]}]})
        mock_g.return_value = json.dumps({"results": []})
        mock_b.return_value = json.dumps({"total_cost": 10500, "currency": "INR", "budget_cap": 15000, "over_budget": False, "overage_amount": 0, "breakdown_by_agent": {}})

        resp = client.post("/chat", json={"message": msg, "session_id": "test-f1-a", "user_id": "u-f1"})
        assert resp.status_code == 200
        data = resp.json()
        assert "No strong match found" not in data["response"]
        assert "Air India" in data["response"]
        assert data.get("plan") is not None
        assert data["plan"]["origin"] == "DEL"
        assert data["plan"]["destination"] == "BOM"
        assert data["plan"]["display_currency"] == "INR"


def test_finding1_repro_b_show_me_currency_update():
    """
    Repro B: Mid-conversation, 'show me this in Swiss francs'
    Asserts:
    - Intent is classified as MODIFY_EXISTING or MIDTRIP_BUDGET_CHANGE, NOT ASK_ABOUT_DESTINATION
    - Currency is recognized as CHF
    """
    ctx = {
        "origin": "DEL", "origin_name": "Delhi",
        "destination": "BOM", "destination_name": "Mumbai",
        "display_currency": "INR"
    }
    msg = "show me this in Swiss francs"
    classified = classify_intent(msg, session_context=ctx)
    assert classified.intent != IntentType.ASK_ABOUT_DESTINATION
    assert classified.intent in [IntentType.MODIFY_EXISTING, IntentType.MIDTRIP_BUDGET_CHANGE]
    assert classified.display_currency == "CHF"


def test_finding1_show_me_variants_generalize():
    """
    Test additional 'show me' variants do not swallow into local guide:
    - 'show me the cheapest option'
    - 'show me flights only'
    """
    ctx = {"origin": "LHR", "destination": "LIS", "destination_name": "Lisbon"}

    cheap_intent = classify_intent("show me the cheapest option", session_context=ctx)
    assert cheap_intent.intent != IntentType.ASK_ABOUT_DESTINATION
    assert cheap_intent.intent == IntentType.MODIFY_EXISTING

    flights_intent = classify_intent("show me flights only", session_context=ctx)
    assert flights_intent.intent != IntentType.ASK_ABOUT_DESTINATION

    # Confirm genuine destination requests STILL route to ASK_ABOUT_DESTINATION
    guide_intent = classify_intent("show me places to visit in Mumbai", session_context=ctx)
    assert guide_intent.intent == IntentType.ASK_ABOUT_DESTINATION
    assert guide_intent.destination == "BOM"


# ============================================================================
# Finding 2 Tests: Budget clarification absolute amount
# ============================================================================

def test_budget_clarification_absolute_amount_applied_correctly():
    """
    Finding 2 Repro:
    1. Session has existing plan ($500 cap).
    2. Budget drops below 0 -> honest rejection triggers and pending_slot='budget_clarification' is set.
    3. User replies 'i mean now i have budget of 10000'.
    4. Assert resulting budget_cap is genuinely 10000.0, NOT 80.0 or any mangled value!
    """
    session_id = "test-f2-clarif"
    plan_payload = {
        "origin": "LHR", "origin_name": "London",
        "destination": "LIS", "destination_name": "Lisbon",
        "departure_date": "2026-09-10", "check_in": "2026-09-10", "check_out": "2026-09-12",
        "budget_cap": 100.0, "target_currency": "CHF", "display_currency": "CHF",
        "transport_results": [{"carrier": "Swiss", "origin": {"iata": "LHR"}, "destination": {"iata": "LIS"}, "price": {"amount": 100.0, "currency": "CHF"}}],
        "hotel_results": [{"name": "Hotel Zurich", "rating": 4.0, "check_in": "2026-09-10", "check_out": "2026-09-12", "price_per_night": {"amount": 100.0, "currency": "CHF"}, "total_price": {"amount": 200.0, "currency": "CHF"}, "amenities": ["WiFi"]}],
        "local_guide_results": [],
        "cost_line_items": [{"source_agent": "transport", "amount": 100.0, "currency": "CHF"}]
    }
    save_session_trip(session_id, {**plan_payload, "confirmed_plan": plan_payload})

    # Step 1: Drop budget by 10000 CHF -> negative remaining
    resp1 = client.post(
        "/chat",
        json={"message": "my budget dropped by 10000 CHF", "session_id": session_id, "user_id": "u-f2"}
    )
    assert resp1.status_code == 200
    text1 = resp1.json()["response"]
    assert "not enough for any trip" in text1
    assert get_session_trip(session_id).get("pending_slot") == "budget_clarification"

    # Step 2: User clarifies with absolute amount: 'i mean now i have budget of 10000'
    with patch("app.graph.call_budget_reconciliation_a2a", new_callable=AsyncMock) as mock_recon:
        mock_recon.return_value = json.dumps({
            "total_cost": 300.0,
            "currency": "CHF",
            "budget_cap": 10000.0,
            "over_budget": False,
            "overage_amount": 0.0,
            "breakdown_by_agent": {}
        })

        resp2 = client.post(
            "/chat",
            json={"message": "i mean now i have budget of 10000", "session_id": session_id, "user_id": "u-f2"}
        )
        assert resp2.status_code == 200
        saved_session = get_session_trip(session_id)
        
        # Core assertion: cap must be genuinely 10000.0, NOT 80.0!
        assert saved_session.get("budget_cap") == 10000.0
        assert resp2.json()["plan"]["budget_cap"] == 10000.0
        assert saved_session.get("pending_slot") is None


# ============================================================================
# Finding 4 Tests: Failed request leaves clean session state
# ============================================================================

def test_failed_request_does_not_corrupt_session_state():
    """
    Finding 4 Repro:
    1. 'plan a trip to Barcelona' fails due to exception.
    2. Assert session state is NOT partially written with Barcelona.
    3. Next query 'what should I see in Reykjavik?' must be for Reykjavik, not Barcelona.
    """
    session_id = "test-f4-corrupt"
    clear_session_trip(session_id)

    # Simulate failure on 'plan a trip to Barcelona'
    with patch("app.main.planner_graph.ainvoke", side_effect=RuntimeError("Simulated Gateway/Agent failure")):
        resp_fail = client.post(
            "/chat",
            json={"message": "plan a trip to Barcelona", "session_id": session_id, "user_id": "u-f4"}
        )
        assert "Planner LangGraph Execution Error" in resp_fail.json()["response"]

    # Session state must be clean and not contain Barcelona
    corrupt_state = get_session_trip(session_id)
    assert corrupt_state.get("destination") is None
    assert corrupt_state.get("destination_name") is None

    # Next query: 'what should I see in Reykjavik?'
    with patch("app.graph.call_local_guide_agent_a2a", new_callable=AsyncMock) as mock_guide:
        mock_guide.return_value = json.dumps({
            "results": [
                {
                    "name": "Hallgrimskirkja",
                    "description": "Iconic cathedral in Reykjavik.",
                    "estimated_duration_hours": 2,
                    "estimated_cost": {"amount": 0, "currency": "USD"},
                    "source_citation": "Wikivoyage",
                    "location": {"name": "Reykjavik"},
                    "tags": ["church", "architecture"]
                }
            ]
        })
        resp_next = client.post(
            "/chat",
            json={"message": "what should I see in Reykjavik?", "session_id": session_id, "user_id": "u-f4"}
        )
        assert resp_next.status_code == 200
        text = resp_next.json()["response"]
        assert "Barcelona" not in text
        assert "Reykjavik" in text


# ============================================================================
# Finding 5 Tests: Stale confirmation state does not trap small talk
# ============================================================================

def test_stale_confirmation_does_not_catch_unrelated_messages():
    """
    Finding 5 Repro:
    1. Session has awaiting_confirmation = True.
    2. Interleave 2+ queries (e.g. guide questions).
    3. User says 'tell me a joke'.
    4. Assert response is polite/honest small talk ('I can't tell jokes, but happy to help with your trip'),
       NOT the confirmation nag ('Ready to finalize...').
    """
    session_id = "test-f5-stale"
    plan_payload = {
        "origin": "LHR", "origin_name": "London",
        "destination": "LIS", "destination_name": "Lisbon",
        "departure_date": "2026-09-10", "check_in": "2026-09-10", "check_out": "2026-09-12",
        "budget_cap": 500.0, "target_currency": "USD", "display_currency": "USD",
        "transport_results": [], "hotel_results": [], "local_guide_results": []
    }
    save_session_trip(session_id, {**plan_payload, "confirmed_plan": plan_payload})
    set_awaiting_confirmation(session_id, True)
    assert is_awaiting_confirmation(session_id) is True

    # Query 1: Unrelated guide query
    with patch("app.graph.call_local_guide_agent_a2a", new_callable=AsyncMock) as mock_guide:
        mock_guide.return_value = json.dumps({"results": []})
        client.post("/chat", json={"message": "what should I see in Reykjavik?", "session_id": session_id, "user_id": "u-f5"})

    # Query 2: Unrelated guide query
    with patch("app.graph.call_local_guide_agent_a2a", new_callable=AsyncMock) as mock_guide:
        mock_guide.return_value = json.dumps({"results": []})
        client.post("/chat", json={"message": "tell me places to visit in Rome", "session_id": session_id, "user_id": "u-f5"})

    # Query 3: Non-sequitur: 'tell me a joke'
    resp_joke = client.post("/chat", json={"message": "tell me a joke", "session_id": session_id, "user_id": "u-f5"})
    assert resp_joke.status_code == 200
    joke_text = resp_joke.json()["response"]
    assert "Ready to finalize" not in joke_text
    assert "I can't tell jokes, but happy to help with your trip" in joke_text


# ============================================================================
# Finding 6 Tests: Currency symbol in honest budget messages
# ============================================================================

def test_honest_budget_message_uses_chf_currency_symbol():
    """
    Finding 6: When session currency is CHF, honest zero/negative-budget message
    must use CHF symbol ('CHF '), not hardcoded '$'.
    """
    session_id = "test-f6-currency"
    plan_payload = {
        "origin": "ZRH", "origin_name": "Zurich",
        "destination": "LIS", "destination_name": "Lisbon",
        "budget_cap": 500.0, "target_currency": "CHF", "display_currency": "CHF",
        "cost_line_items": [{"source_agent": "hotel", "amount": 400.0, "currency": "CHF"}]
    }
    save_session_trip(session_id, {**plan_payload, "confirmed_plan": plan_payload})

    resp = client.post(
        "/chat",
        json={"message": "my budget dropped by 600 CHF", "session_id": session_id, "user_id": "u-f6"}
    )
    assert resp.status_code == 200
    text = resp.json()["response"]
    assert "CHF" in text
    assert "$" not in text
    assert "leaves -CHF 100" in text or "leaves -CHF100" in text or "-CHF 100" in text


# ============================================================================
# Finding 7 Tests: Flight delay honest notice in card UI payload & banner
# ============================================================================

def test_flight_delay_honest_message_in_card_payload():
    """
    Finding 7: When transport agent reports no later flights available,
    the honest notice must be included in the text response, marked in
    no_later_flight_found = True, and provided in lead_banner for the card UI.
    """
    session_id = "test-f7-delay"
    plan_payload = {
        "origin": "LHR", "origin_name": "London",
        "destination": "LIS", "destination_name": "Lisbon",
        "departure_date": "2026-09-10", "check_in": "2026-09-10", "check_out": "2026-09-12",
        "budget_cap": 600.0, "target_currency": "USD", "display_currency": "USD",
        "transport_results": [{"carrier": "BA", "price": {"amount": 100.0, "currency": "USD"}, "departure_at": "2026-09-10T10:00", "arrival_at": "2026-09-10T12:40", "duration_minutes": 160, "origin": {"iata": "LHR"}, "destination": {"iata": "LIS"}}],
        "hotel_results": [{"name": "Lisbon Inn", "rating": 4.2, "check_in": "2026-09-10", "check_out": "2026-09-12", "price_per_night": {"amount": 100.0, "currency": "USD"}, "total_price": {"amount": 200.0, "currency": "USD"}, "amenities": ["WiFi"]}],
        "local_guide_results": [],
        "cost_line_items": [{"source_agent": "transport", "amount": 100.0, "currency": "USD"}]
    }
    save_session_trip(session_id, {**plan_payload, "confirmed_plan": plan_payload})
    confirm_session_trip(session_id, plan_payload)

    with patch("app.graph.call_transport_agent_a2a", new_callable=AsyncMock) as mock_t:
        mock_t.return_value = json.dumps({
            "success": True,
            "results": [],
            "no_later_flight": True
        })

        resp = client.post(
            "/chat",
            json={"message": "my flight got delayed 4 hours", "session_id": session_id, "user_id": "u-f7"}
        )
        assert resp.status_code == 200
        data = resp.json()
        text = data["response"]
        plan = data.get("plan")

        assert "No later flight options were found given the delay" in text
        assert plan is not None
        assert plan.get("no_later_flight_found") is True
        assert "No later flight options were found given the delay" in plan.get("lead_banner", "")
