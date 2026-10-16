import pytest
import json
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient
from planner_agent.app.main import app
from planner_agent.app.intent_classifier import classify_intent, IntentType
from planner_agent.app.session_store import clear_session_trip, save_session_trip
from shared.resolvers import validate_and_resolve_city

client = TestClient(app)


def test_strict_allowlist_gate_direct():
    """Confirms that validate_and_resolve_city is a strict allowlist gate."""
    # Arbitrary English words, parts of speech, verbs must return None
    assert validate_and_resolve_city("tell") is None
    assert validate_and_resolve_city("visit") is None
    assert validate_and_resolve_city("tel") is None
    assert validate_and_resolve_city("vis") is None
    assert validate_and_resolve_city("sounds") is None
    assert validate_and_resolve_city("thinking") is None
    assert validate_and_resolve_city("rupees") is None
    assert validate_and_resolve_city("temples") is None
    assert validate_and_resolve_city("somewhere") is None

    # Canonical cities must resolve
    assert validate_and_resolve_city("delhi") == ("Delhi", "DEL")
    assert validate_and_resolve_city("mumbai") == ("Mumbai", "BOM")
    assert validate_and_resolve_city("new york") == ("New York", "JFK")
    assert validate_and_resolve_city("tokyo") == ("Tokyo", "HND")
    assert validate_and_resolve_city("lisbon") == ("Lisbon", "LIS")


def test_intent_classification_adversarial_phrasing_unit():
    """Unit tests the intent classifier on adversarial inputs."""
    prior_session = {"origin": "DEL", "origin_name": "Delhi", "destination": "BOM", "destination_name": "Mumbai"}

    # 1. "tell me some good places to visit in mumbai"
    c1 = classify_intent("tell me some good places to visit in mumbai", session_context=prior_session)
    assert c1.intent == IntentType.ASK_ABOUT_DESTINATION
    assert c1.destination == "BOM"
    assert c1.destination_name == "Mumbai"

    # 2. "i want to see some temples"
    c2 = classify_intent("i want to see some temples", session_context=prior_session)
    assert c2.intent == IntentType.ASK_ABOUT_DESTINATION
    assert c2.destination == "BOM"

    # 3. "what's there to do in the city"
    c3 = classify_intent("what's there to do in the city", session_context=prior_session)
    assert c3.intent == IntentType.ASK_ABOUT_DESTINATION
    assert c3.destination == "BOM"

    # 4. "used to live near tokyo, thinking of going back"
    c4 = classify_intent("used to live near tokyo, thinking of going back", session_context=prior_session)
    assert c4.intent == IntentType.GENERAL_CONVERSATION

    # 5. "from what I've read, mumbai sounds great"
    c5 = classify_intent("from what I've read, mumbai sounds great", session_context=prior_session)
    assert c5.intent == IntentType.GENERAL_CONVERSATION


def test_intent_classification_fresh_session_no_prior_trip():
    """Fresh session with no prior trip: verifies no fabricated destinations."""
    empty_session = {}

    c1 = classify_intent("tell me some good places to visit in mumbai", session_context=empty_session)
    assert c1.intent == IntentType.ASK_ABOUT_DESTINATION
    assert c1.destination == "BOM"

    c2 = classify_intent("i want to see some temples", session_context=empty_session)
    assert c2.intent == IntentType.NO_CONTEXT

    c3 = classify_intent("what's there to do in the city", session_context=empty_session)
    assert c3.intent == IntentType.NO_CONTEXT

    c4 = classify_intent("used to live near tokyo, thinking of going back", session_context=empty_session)
    assert c4.intent == IntentType.NO_CONTEXT

    c5 = classify_intent("from what I've read, mumbai sounds great", session_context=empty_session)
    assert c5.intent == IntentType.NO_CONTEXT


@pytest.mark.anyio
async def test_adversarial_repro_three_turn_session():
    """
    Concrete repro test:
    Turn 1: "delhi to mumbai" -> establishes DEL->BOM
    Turn 2: "in rupees" -> preserves DEL->BOM, updates display_currency to INR
    Turn 3: "tell me some good places to visit in mumbai" -> returns Mumbai guide, preserves DEL->BOM, never TEL/VIS
    """
    session_id = "test-adversarial-repro-3turn"
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
                "departure_at": "2026-09-10T10:00:00",
                "arrival_at": "2026-09-10T12:00:00",
                "duration_minutes": 120,
                "price": {"amount": 70.0, "currency": "EUR"},
                "is_estimated": False
            }]
        })
        mock_h.return_value = json.dumps({
            "success": True,
            "results": [{
                "name": "BOM Palace Inn",
                "rating": 4.5,
                "check_in": "2026-09-10",
                "check_out": "2026-09-12",
                "price_per_night": {"amount": 50.0, "currency": "EUR"},
                "total_price": {"amount": 100.0, "currency": "EUR"},
                "amenities": ["WiFi"],
                "is_estimated": False
            }]
        })
        mock_g.return_value = json.dumps({
            "success": True,
            "results": [{
                "name": "Gateway of India & Colaba Heritage Walk",
                "description": "Historic landmark and bustling promenade in Mumbai.",
                "location": {"name": "Colaba, Mumbai"},
                "estimated_duration_hours": 3.0,
                "estimated_cost": {"amount": 0.0, "currency": "INR"},
                "tags": ["culture", "sights"],
                "source_citation": "Wikivoyage contributors, 'Mumbai/South', CC BY-SA 4.0"
            }]
        })
        mock_rate.return_value = 90.0

        # Turn 1: "delhi to mumbai"
        r1 = client.post("/chat", json={"message": "delhi to mumbai", "session_id": session_id, "user_id": "test-user-adv"})
        assert r1.status_code == 200
        msg1 = r1.json()["response"]
        assert "DEL to BOM" in msg1
        assert "BOM Palace Inn" in msg1

        # Turn 2: "in rupees"
        r2 = client.post("/chat", json={"message": "in rupees", "session_id": session_id, "user_id": "test-user-adv"})
        assert r2.status_code == 200
        msg2 = r2.json()["response"]
        assert "DEL to BOM" in msg2
        assert "INR" in msg2
        assert "TEL" not in msg2
        assert "VIS" not in msg2

        # Turn 3: "tell me some good places to visit in mumbai"
        r3 = client.post("/chat", json={"message": "tell me some good places to visit in mumbai", "session_id": session_id, "user_id": "test-user-adv"})
        assert r3.status_code == 200
        msg3 = r3.json()["response"]
        assert "Local Guide" in msg3 or "Mumbai" in msg3
        assert "Gateway of India" in msg3
        assert "TEL" not in msg3
        assert "VIS" not in msg3


@pytest.mark.anyio
async def test_adversarial_queries_in_fresh_session():
    """Verifies that in a fresh session with no prior trip, non-route queries gracefully prompt the user."""
    fresh_id = "test-adversarial-fresh"
    clear_session_trip(fresh_id)

    # 1. "what's there to do in the city"
    r1 = client.post("/chat", json={"message": "what's there to do in the city", "session_id": fresh_id, "user_id": "test-user-adv"})
    assert "Where would you like to travel?" in r1.json()["response"]

    # 2. "used to live near tokyo, thinking of going back"
    r2 = client.post("/chat", json={"message": "used to live near tokyo, thinking of going back", "session_id": fresh_id, "user_id": "test-user-adv"})
    assert "Where would you like to travel?" in r2.json()["response"]

    # 3. "from what I've read, mumbai sounds great"
    r3 = client.post("/chat", json={"message": "from what I've read, mumbai sounds great", "session_id": fresh_id, "user_id": "test-user-adv"})
    assert "Where would you like to travel?" in r3.json()["response"]
