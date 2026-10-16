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
from app.graph import extract_budget_intent
from shared.resolvers import resolve_currency_name, is_unresolvable_currency_query

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_round2_sessions():
    sessions = [
        "test-r2-mod", "test-r2-budget", "test-r2-multi",
        "test-r2-delay-changed", "test-r2-delay-unchanged", "test-r2-typos",
        "test-r2-nat-curr", "test-r2-unres-curr"
    ]
    for s in sessions:
        clear_session_trip(s)
    yield
    for s in sessions:
        clear_session_trip(s)


# ============================================================================
# Priority 3: Generic Modifier Phrasing
# ============================================================================

def test_generic_modifier_phrasing_recognized():
    """
    Asserts:
    - 'adjust the hotel', 'change the hotel', 'update my flight' are recognized.
    - If hotel results change, banner indicates update.
    - If hotel results are identical, system NEVER falsely claims 'Updated Trip Plan' over identical data.
    """
    # 1. Test intent classification & modifier extraction
    ctx = {"destination": "KIX", "destination_name": "Osaka", "origin": "HND", "origin_name": "Tokyo"}
    
    ci1 = classify_intent("I'd like to adjust the hotel", session_context=ctx)
    assert ci1.intent == IntentType.MODIFY_EXISTING

    ci2 = classify_intent("change the hotel", session_context=ctx)
    assert ci2.intent == IntentType.MODIFY_EXISTING

    ci3 = classify_intent("update my flight", session_context=ctx)
    assert ci3.intent == IntentType.MODIFY_EXISTING

    session_id = "test-r2-mod"
    prior_hotel = [{"name": "Osaka Central Boutique Inn", "total_price": {"amount": 190.0, "currency": "EUR"}}]
    prior_transport = [{"carrier": "ANA", "price": {"amount": 120.0, "currency": "EUR"}}]
    
    save_session_trip(session_id, {
        "origin": "HND",
        "origin_name": "Tokyo",
        "destination": "KIX",
        "destination_name": "Osaka",
        "display_currency": "USD",
        "target_currency": "USD",
        "hotel_results": prior_hotel,
        "transport_results": prior_transport,
        "awaiting_confirmation": True
    })

    # Test A: Different hotel returned -> updated banner is valid
    diff_hotel = [{"name": "Osaka Eco Traveler Hostel & Suites", "total_price": {"amount": 110.0, "currency": "EUR"}}]
    with patch("app.graph.call_hotel_agent_a2a", new_callable=AsyncMock) as mock_h, \
         patch("app.graph.call_transport_agent_a2a", new_callable=AsyncMock) as mock_t, \
         patch("app.graph.call_single_currency_rate_a2a", new_callable=AsyncMock) as mock_rate:
        mock_rate.return_value = 1.0
        mock_h.return_value = json.dumps({"results": diff_hotel})
        mock_t.return_value = json.dumps({"results": prior_transport})

        resp = client.post("/chat", json={"message": "I'd like to adjust the hotel", "session_id": session_id, "user_id": "test-user"})
        assert resp.status_code == 200
        data = resp.json()
        text = data["response"]
        assert "⚡ Updated Trip Plan" in text
        assert "Osaka Eco Traveler Hostel & Suites" in text

    # Test B: Identical hotel returned -> MUST NOT claim 'Updated Trip Plan'
    with patch("app.graph.call_hotel_agent_a2a", new_callable=AsyncMock) as mock_h, \
         patch("app.graph.call_transport_agent_a2a", new_callable=AsyncMock) as mock_t, \
         patch("app.graph.call_single_currency_rate_a2a", new_callable=AsyncMock) as mock_rate:
        mock_rate.return_value = 1.0
        current_hotel = get_session_trip(session_id).get("hotel_results")
        # Return identical hotel to current session hotel
        mock_h.return_value = json.dumps({"results": current_hotel, "no_cheaper_options": False})
        mock_t.return_value = json.dumps({"results": prior_transport})

        resp = client.post("/chat", json={"message": "I'd like to adjust the hotel", "session_id": session_id, "user_id": "test-user"})
        assert resp.status_code == 200
        data = resp.json()
        text = data["response"]
        # CRITICAL: Never claim 'Updated Trip Plan' over identical data
        assert "⚡ Updated Trip Plan" not in text
        assert "No alternative hotel options were found" in text or "Trip Plan" in text


# ============================================================================
# Priority 2: Budget Change Always Renders Status
# ============================================================================

def test_budget_change_always_renders_status():
    """
    Asserts:
    - 'okay let's say 800 instead' parses budget_cap = 800.0.
    - Budget reconciliation section is ALWAYS rendered when budget_cap is set.
    - Specifically prevents the bug where the banner says 'Updated Trip Plan' but no budget section is rendered.
    """
    # 1. Verify parser extracts 800 from conversational phrasing
    cap, target, disp = extract_budget_intent("okay let's say 800 instead")
    assert cap == 800.0

    session_id = "test-r2-budget"
    save_session_trip(session_id, {
        "origin": "HND",
        "origin_name": "Tokyo",
        "destination": "KIX",
        "destination_name": "Osaka",
        "display_currency": "USD",
        "target_currency": "USD",
        "awaiting_confirmation": True
    })

    with patch("app.graph.call_hotel_agent_a2a", new_callable=AsyncMock) as mock_h, \
         patch("app.graph.call_transport_agent_a2a", new_callable=AsyncMock) as mock_t, \
         patch("app.graph.call_budget_reconciliation_a2a", new_callable=AsyncMock) as mock_b, \
         patch("app.graph.call_single_currency_rate_a2a", new_callable=AsyncMock) as mock_rate:
        mock_rate.return_value = 1.0
        mock_t.return_value = json.dumps({"results": [{"carrier": "ANA", "price": {"amount": 250.0, "currency": "USD"}}]})
        mock_h.return_value = json.dumps({"results": [{"name": "Osaka Inn", "total_price": {"amount": 300.0, "currency": "USD"}}]})
        mock_b.return_value = json.dumps({
            "total_cost": 550.0,
            "currency": "USD",
            "budget_cap": 800.0,
            "over_budget": False,
            "overage_amount": 0.0,
            "breakdown_by_agent": {"transport": 250.0, "hotel": 300.0, "local_guide": 0.0}
        })

        resp = client.post("/chat", json={"message": "okay let's say 800 instead", "session_id": session_id, "user_id": "test-user"})
        assert resp.status_code == 200
        data = resp.json()
        text = data["response"]

        # MUST render budget section with status
        assert "Budget Reconciliation" in text
        assert "Status: Within Budget" in text or "Status: Over Budget" in text
        assert "800.00 USD" in text

        # Saved session must have budget_cap = 800.0
        saved = get_session_trip(session_id)
        assert saved.get("budget_cap") == 800.0


# ============================================================================
# Priority 1: Long Conversation Context Survives Many Turns
# ============================================================================

def test_long_conversation_context_survives_many_turns():
    """
    Scripted multi-turn conversation (8+ turns, mixing currency, budget, delay, confirm)
    asserting session context is intact at every step.
    Regression test ensuring session context NEVER resets to 'Where would you like to travel?'.
    """
    session_id = "test-r2-multi"

    with patch("app.graph.call_hotel_agent_a2a", new_callable=AsyncMock) as mock_h, \
         patch("app.graph.call_transport_agent_a2a", new_callable=AsyncMock) as mock_t, \
         patch("app.graph.call_local_guide_agent_a2a", new_callable=AsyncMock) as mock_g, \
         patch("app.graph.call_budget_reconciliation_a2a", new_callable=AsyncMock) as mock_b, \
         patch("app.graph.call_single_currency_rate_a2a", new_callable=AsyncMock) as mock_rate, \
         patch("app.graph.generate_conversational_travel_response", new_callable=AsyncMock) as mock_gemini:
        
        mock_rate.return_value = 1.0
        mock_gemini.return_value = "The weather in Osaka in late December is typically cool with daytime temperatures around 10-12°C."
        mock_t.return_value = json.dumps({"results": [{"carrier": "ANA", "price": {"amount": 200.0, "currency": "AUD"}}]})
        mock_h.return_value = json.dumps({"results": [{"name": "Osaka Inn", "total_price": {"amount": 300.0, "currency": "AUD"}}]})
        mock_g.return_value = json.dumps({"results": []})
        mock_b.return_value = json.dumps({"total_cost": 500.0, "currency": "AUD", "budget_cap": 1000.0, "over_budget": False, "overage_amount": 0.0, "breakdown_by_agent": {}})

        # Turn 1: Initial trip route
        r1 = client.post("/chat", json={"message": "Tokyo to Osaka on 20 December, prices in Australian dollars please", "session_id": session_id, "user_id": "test-user"})
        assert r1.status_code == 200
        s1 = get_session_trip(session_id)
        assert s1.get("origin") == "HND"
        assert s1.get("destination") == "KIX"
        assert s1.get("display_currency") == "AUD"

        # Turn 2: Currency change
        r2 = client.post("/chat", json={"message": "can you show me this trip in Canadian dollars", "session_id": session_id, "user_id": "test-user"})
        assert r2.status_code == 200
        s2 = get_session_trip(session_id)
        assert s2.get("destination") == "KIX"
        assert s2.get("display_currency") == "CAD"

        # Turn 3: Generic modifier
        r3 = client.post("/chat", json={"message": "I'd like to adjust the hotel", "session_id": session_id, "user_id": "test-user"})
        assert r3.status_code == 200
        s3 = get_session_trip(session_id)
        assert s3.get("destination") == "KIX"

        # Turn 4: Typo confirmation ("conferm")
        r4 = client.post("/chat", json={"message": "conferm", "session_id": session_id, "user_id": "test-user"})
        assert r4.status_code == 200
        assert "confirmed" in r4.json()["response"].lower()
        s4 = get_session_trip(session_id)
        assert s4.get("is_confirmed") is True

        # Turn 5: Budget adjustment
        r5 = client.post("/chat", json={"message": "okay let's say 800 instead", "session_id": session_id, "user_id": "test-user"})
        assert r5.status_code == 200
        s5 = get_session_trip(session_id)
        assert s5.get("destination") == "KIX"
        assert s5.get("budget_cap") == 800.0

        # Turn 6: Specific currency budget trim
        r6 = client.post("/chat", json={"message": "gonna need to trim this to about 90 CHF", "session_id": session_id, "user_id": "test-user"})
        assert r6.status_code == 200
        s6 = get_session_trip(session_id)
        assert s6.get("destination") == "KIX"
        assert s6.get("budget_cap") == 90.0
        assert s6.get("display_currency") == "CHF"

        # Turn 7: Flight delay
        mock_t.return_value = json.dumps({"results": [{"carrier": "JAL", "price": {"amount": 50.0, "currency": "CHF"}}]})
        r7 = client.post("/chat", json={"message": "my flight got delayed 10 hours", "session_id": session_id, "user_id": "test-user"})
        assert r7.status_code == 200
        s7 = get_session_trip(session_id)
        assert s7.get("destination") == "KIX"

        # Turn 8: Conversational message
        r8 = client.post("/chat", json={"message": "what's the weather like in Osaka around then", "session_id": session_id, "user_id": "test-user"})
        assert r8.status_code == 200
        resp_text = r8.json()["response"]
        # MUST preserve context, NEVER reset to brand-new prompt
        assert "Where would you like to travel?" not in resp_text
        s8 = get_session_trip(session_id)
        assert s8.get("destination") == "KIX"


# ============================================================================
# Priority 4: Flight Delay Banner Matches Actual Change
# ============================================================================

def test_flight_delay_banner_matches_actual_change():
    """
    Asserts:
    - If transport results genuinely differ, banner is '⚡ Updated Trip Plan'.
    - If no later flight is found or results are identical, banner is plain 'Trip Plan' with honest message.
    """
    # Scenario A: Flight changed
    sess_a = "test-r2-delay-changed"
    prior_t = [{"carrier": "Duffel Airways", "price": {"amount": 100.0, "currency": "USD"}}]
    save_session_trip(sess_a, {
        "origin": "LHR",
        "origin_name": "London",
        "destination": "LIS",
        "destination_name": "Lisbon",
        "transport_results": prior_t,
        "display_currency": "USD",
        "target_currency": "USD"
    })

    new_t = [{"carrier": "British Airways", "price": {"amount": 130.0, "currency": "USD"}}]
    with patch("app.graph.call_transport_agent_a2a", new_callable=AsyncMock) as mock_t, \
         patch("app.graph.call_single_currency_rate_a2a", new_callable=AsyncMock) as mock_rate:
        mock_rate.return_value = 1.0
        mock_t.return_value = json.dumps({"results": new_t, "no_later_options": False})

        resp = client.post("/chat", json={"message": "my flight got delayed 3 hours", "session_id": sess_a, "user_id": "test-user"})
        assert resp.status_code == 200
        text = resp.json()["response"]
        assert "⚡ Updated Trip Plan" in text
        assert "British Airways" in text

    # Scenario B: No later flight found (delay exceeds available flights)
    sess_b = "test-r2-delay-unchanged"
    save_session_trip(sess_b, {
        "origin": "LHR",
        "origin_name": "London",
        "destination": "LIS",
        "destination_name": "Lisbon",
        "transport_results": prior_t,
        "display_currency": "USD",
        "target_currency": "USD"
    })

    with patch("app.graph.call_transport_agent_a2a", new_callable=AsyncMock) as mock_t, \
         patch("app.graph.call_single_currency_rate_a2a", new_callable=AsyncMock) as mock_rate:
        mock_rate.return_value = 1.0
        # transport agent signals no later options
        mock_t.return_value = json.dumps({"results": prior_t, "no_later_options": True})

        resp = client.post("/chat", json={"message": "my flight got delayed 24 hours", "session_id": sess_b, "user_id": "test-user"})
        assert resp.status_code == 200
        text = resp.json()["response"]
        assert "⚡ Updated Trip Plan" not in text
        assert "No later flight options were found given the delay" in text


# ============================================================================
# Priority 6: Confirm Typo Tolerance
# ============================================================================

def test_confirm_typo_tolerance():
    """
    Asserts:
    - 'conferm', 'cofirm', 'comfirm', 'confrm' all recognized as CONFIRM_PLAN when awaiting_confirmation is True.
    """
    ctx = {"destination": "LIS", "origin": "LHR", "awaiting_confirmation": True}
    
    typos = ["conferm", "cofirm", "comfirm", "confrm", "CONFIRM", "Conferm"]
    for typo in typos:
        classified = classify_intent(typo, session_context=ctx)
        assert classified.intent == IntentType.CONFIRM_PLAN, f"Failed for typo: {typo}"


# ============================================================================
# Priority: Nationality Currency Phrasing & Currency Clarification
# ============================================================================

def test_nationality_currency_phrasing_resolves():
    """
    Table of '[nationality] currency' phrases mapped to expected currency codes,
    including the exact repro 'in indian currency' -> INR.
    Asserts:
    - resolve_currency_name correctly maps each phrasing to its ISO currency code.
    - classify_intent recognizes them as MODIFY_EXISTING with the right target currency.
    - Live chat endpoint re-renders the plan in INR without confirmation-ambiguous fallback.
    """
    phrases = {
        "in indian currency": "INR",
        "in japanese yen": "JPY",
        "pay in euros": "EUR",
        "show me in american dollars": "USD",
        "in swiss currency": "CHF",
        "australian money": "AUD",
        "canadian currency": "CAD",
        "pay in british currency": "GBP",
        "in eu currency": "EUR",
        "in us currency": "USD",
        "singapore currency": "SGD",
        "uae currency": "AED"
    }

    ctx = {
        "destination": "DEL",
        "destination_name": "Delhi",
        "origin": "BOM",
        "origin_name": "Mumbai",
        "display_currency": "USD",
        "awaiting_confirmation": True
    }

    for phrase, expected_code in phrases.items():
        resolved = resolve_currency_name(phrase)
        assert resolved == expected_code, f"resolve_currency_name failed for '{phrase}': got {resolved}, expected {expected_code}"

        ci = classify_intent(phrase, session_context=ctx)
        assert ci.intent == IntentType.MODIFY_EXISTING, f"classify_intent failed for '{phrase}': got {ci.intent}"
        assert ci.display_currency == expected_code, f"display_currency failed for '{phrase}': got {ci.display_currency}, expected {expected_code}"

    # End-to-end repro test: 'in indian currency' switches plan to INR
    session_id = "test-r2-nat-curr"
    prior_hotel = [{"name": "Delhi Palace", "total_price": {"amount": 100.0, "currency": "INR"}}]
    prior_transport = [{"carrier": "Air India", "price": {"amount": 100.0, "currency": "INR"}}]
    save_session_trip(session_id, {
        "origin": "BOM",
        "origin_name": "Mumbai",
        "destination": "DEL",
        "destination_name": "Delhi",
        "display_currency": "USD",
        "target_currency": "USD",
        "hotel_results": prior_hotel,
        "transport_results": prior_transport,
        "awaiting_confirmation": True
    })

    with patch("app.graph.call_hotel_agent_a2a", new_callable=AsyncMock) as mock_h, \
         patch("app.graph.call_transport_agent_a2a", new_callable=AsyncMock) as mock_t, \
         patch("app.graph.call_single_currency_rate_a2a", new_callable=AsyncMock) as mock_rate:
        mock_rate.return_value = 83.0
        mock_h.return_value = json.dumps({"results": prior_hotel})
        mock_t.return_value = json.dumps({"results": prior_transport})

        resp = client.post("/chat", json={"message": "in indian currency", "session_id": session_id, "user_id": "test-user"})
        assert resp.status_code == 200
        text = resp.json()["response"]
        # Ensure it does NOT fall back to ambiguous confirmation
        assert "I'm not sure if you'd like to confirm this plan or make changes" not in text
        # Ensure it re-renders the plan in INR
        assert "INR" in text or "₹" in text or "Trip Plan" in text
        saved = get_session_trip(session_id)
        assert saved.get("display_currency") == "INR"


def test_unresolvable_currency_asks_not_ambiguous_fallback():
    """
    Asserts:
    - Unsupported/unclear currency phrases (e.g. 'in martian currency', 'pay in pesos')
      trigger currency clarification specifically, not the generic confirm-or-adjust message.
    """
    unresolvable_phrases = [
        "in martian currency",
        "pay in pesos",
        "what currency can I use",
        "prices in bitcoin"
    ]

    ctx = {
        "destination": "DEL",
        "destination_name": "Delhi",
        "origin": "BOM",
        "origin_name": "Mumbai",
        "display_currency": "USD",
        "awaiting_confirmation": True
    }

    for phrase in unresolvable_phrases:
        ci = classify_intent(phrase, session_context=ctx)
        assert ci.intent == IntentType.NEED_CURRENCY_CLARIFICATION, f"Expected NEED_CURRENCY_CLARIFICATION for '{phrase}', got {ci.intent}"

    session_id = "test-r2-unres-curr"
    save_session_trip(session_id, {
        "origin": "BOM",
        "origin_name": "Mumbai",
        "destination": "DEL",
        "destination_name": "Delhi",
        "display_currency": "USD",
        "target_currency": "USD",
        "awaiting_confirmation": True
    })

    resp = client.post("/chat", json={"message": "in martian currency", "session_id": session_id, "user_id": "test-user"})
    assert resp.status_code == 200
    text = resp.json()["response"]

    # Must NOT ask generic ambiguous confirmation message
    assert "I'm not sure if you'd like to confirm this plan or make changes" not in text
    # Must specifically ask for currency clarification
    assert "Which currency" in text or "currency you'd like" in text
    assert "USD" in text and "INR" in text


def test_bare_number_budget_recognized():
    """
    Test that bare-number budgets without currency words:
    'cap it at 300', 'let's say 300', '300 max' all correctly set a 300 budget cap.
    """
    ctx = {
        "destination": "LIS",
        "destination_name": "Lisbon",
        "origin": "LHR",
        "origin_name": "London",
        "budget_cap": 800.0,
        "display_currency": "USD",
        "target_currency": "USD",
        "awaiting_confirmation": True
    }

    test_phrases = ["cap it at 300", "let's say 300", "300 max"]
    for phrase in test_phrases:
        ci = classify_intent(phrase, session_context=ctx)
        assert ci.intent == IntentType.MIDTRIP_BUDGET_CHANGE, f"Expected MIDTRIP_BUDGET_CHANGE for '{phrase}', got {ci.intent}"
        assert ci.budget_cap == 300.0, f"Expected budget_cap 300.0 for '{phrase}', got {ci.budget_cap}"

    # Verify end-to-end /chat response for bare number budget
    session_id = "test-bare-budget-session"
    save_session_trip(session_id, {
        "origin": "LHR",
        "origin_name": "London",
        "destination": "LIS",
        "destination_name": "Lisbon",
        "budget_cap": 800.0,
        "display_currency": "USD",
        "target_currency": "USD",
        "awaiting_confirmation": True,
        "confirmed_plan": {
            "origin": "LHR",
            "destination": "LIS",
            "cost_line_items": [{"amount": 250.0, "currency": "USD"}]
        }
    })

    resp = client.post("/chat", json={"message": "let's cap it at 300", "session_id": session_id, "user_id": "test-user"})
    assert resp.status_code == 200
    plan = resp.json().get("plan")
    # Budget cap in plan must be updated to 300
    assert plan is not None, "Plan should be returned on budget change"
    assert plan.get("budget_cap") == 300.0, f"Expected budget cap 300.0, got {plan.get('budget_cap')}"
    # Must NOT produce confirmation nagging
    assert "I'm not sure if you'd like to confirm this plan or make changes" not in resp.json()["response"]


def test_hotel_flight_modifier_consistent_regardless_of_wording():
    """
    Test that 'different hotel', 'switch hotels', 'modify the accommodation', 'change the room'
    all produce the SAME behavior (exclude current, find genuine alternative) — assert they don't diverge.
    """
    ctx = {
        "destination": "OSA",
        "destination_name": "Osaka",
        "origin": "TYO",
        "origin_name": "Tokyo",
        "display_currency": "USD",
        "awaiting_confirmation": True,
        "confirmed_plan": {
            "hotel_results": [
                {"name": "Osaka Grand Palace Hotel", "total_price": {"amount": 360.0, "currency": "EUR"}},
                {"name": "Osaka Central Boutique Inn", "total_price": {"amount": 190.0, "currency": "EUR"}}
            ],
            "transport_results": [
                {"carrier": "MockAir Direct", "booking_ref": "MOCK-FL-101", "price": {"amount": 120.0, "currency": "EUR"}},
                {"carrier": "MockAir Connecting", "booking_ref": "MOCK-FL-102", "price": {"amount": 85.0, "currency": "EUR"}}
            ]
        }
    }

    hotel_phrases = [
        "different hotel",
        "switch hotels",
        "modify the accommodation",
        "change the room"
    ]

    alternative_hotels = [
        {"name": "Osaka Eco Traveler Hostel & Suites", "total_price": {"amount": 110.0, "currency": "EUR"}, "price_per_night": {"amount": 55.0, "currency": "EUR"}},
        {"name": "Dotonbori Riverside Capsule", "total_price": {"amount": 95.0, "currency": "EUR"}, "price_per_night": {"amount": 47.5, "currency": "EUR"}}
    ]

    results = []
    with patch("app.graph.call_hotel_agent_a2a", new_callable=AsyncMock) as mock_h, \
         patch("app.graph.call_single_currency_rate_a2a", new_callable=AsyncMock) as mock_r:
        mock_r.return_value = 1.0
        mock_h.return_value = json.dumps({"results": alternative_hotels})

        for idx, phrase in enumerate(hotel_phrases):
            sess_id = f"test-hotel-mod-{idx}"
            save_session_trip(sess_id, {
                "origin": "TYO",
                "origin_name": "Tokyo",
                "destination": "OSA",
                "destination_name": "Osaka",
                "display_currency": "USD",
                "target_currency": "USD",
                "awaiting_confirmation": True,
                "confirmed_plan": ctx["confirmed_plan"],
                "cached_hotel_results": ctx["confirmed_plan"]["hotel_results"],
                "cached_transport_results": ctx["confirmed_plan"]["transport_results"]
            })

            resp = client.post("/chat", json={"message": phrase, "session_id": sess_id, "user_id": "test-user"})
            assert resp.status_code == 200
            plan = resp.json().get("plan")
            assert plan is not None, f"Expected plan for phrase '{phrase}'"
            hotels = plan.get("hotel_results", [])
            assert len(hotels) > 0, f"Expected alternative hotels for phrase '{phrase}'"
            hotel_names = [h["name"] for h in hotels]
            results.append(hotel_names)

    # Assert that all wordings produce consistent results without diverging
    first_result = results[0]
    for idx, res in enumerate(results[1:], 1):
        assert res == first_result, f"Hotel modifier result for '{hotel_phrases[idx]}' diverged from '{hotel_phrases[0]}': {res} vs {first_result}"

    # Verify that the alternative hotel excludes previous choices
    assert "Osaka Eco Traveler Hostel & Suites" in first_result or any("Hostel" in n for n in first_result)


def test_small_talk_generalizes_beyond_keyword_list():
    """
    Test that at least 3 small-talk phrases not in any hardcoded list get a sensible general response,
    not confirmation nagging.
    """
    unseen_small_talk = [
        "what's your favorite color",
        "do you dream of electric sheep",
        "tell me something interesting about yourself"
    ]

    for idx, phrase in enumerate(unseen_small_talk):
        sess_id = f"test-small-talk-{idx}"
        save_session_trip(sess_id, {
            "origin": "LHR",
            "origin_name": "London",
            "destination": "LIS",
            "destination_name": "Lisbon",
            "display_currency": "USD",
            "awaiting_confirmation": True
        })

        resp = client.post("/chat", json={"message": phrase, "session_id": sess_id, "user_id": "test-user"})
        assert resp.status_code == 200
        text = resp.json()["response"]

        # MUST NOT fall through to confirmation nagging
        assert "I'm not sure if you'd like to confirm this plan or make changes" not in text, (
            f"Phrase '{phrase}' fell through to confirmation nagging!"
        )
        assert len(text) > 10, f"Response too short for '{phrase}'"


def test_delay_message_matches_rendered_flight_list():
    """
    Assert the flight count/content in the response matches what the honest message text claims —
    never a mismatch between claimed and shown options.
    """
    sess_id = "test-delay-consistency"
    original_flights = [
        {"carrier": "MockAir Direct", "booking_ref": "MOCK-FL-101", "price": {"amount": 120.0, "currency": "EUR"}, "departure_at": "2026-09-10T08:15:00"},
        {"carrier": "MockAir Connecting", "booking_ref": "MOCK-FL-102", "price": {"amount": 85.0, "currency": "EUR"}, "departure_at": "2026-09-10T14:30:00"}
    ]
    save_session_trip(sess_id, {
        "origin": "FCO",
        "origin_name": "Rome",
        "destination": "ATH",
        "destination_name": "Athens",
        "departure_date": "2026-09-10",
        "display_currency": "EUR",
        "target_currency": "EUR",
        "awaiting_confirmation": True,
        "confirmed_plan": {
            "transport_results": original_flights,
            "hotel_results": [{"name": "Athens Central Boutique Inn", "total_price": {"amount": 190.0, "currency": "EUR"}}]
        },
        "cached_transport_results": original_flights,
        "prior_transport_results": original_flights
    })

    # Trigger flight delay where no later flights satisfy cutoff (delay 15 hours past 8:15 departure)
    resp = client.post("/chat", json={"message": "my flight got delayed 15 hours", "session_id": sess_id, "user_id": "test-user"})
    assert resp.status_code == 200
    data = resp.json()
    text = data["response"]
    plan = data.get("plan")

    assert "No later flight options were found given the delay" in text or "here are your original options" in text
    assert plan is not None
    rendered_flights = plan.get("transport_results", [])

    # The text claimed to be showing original options (2 flights)
    # The rendered flight list MUST have exactly 2 flights — never 3!
    assert len(rendered_flights) == 2, f"Expected 2 original flights matching message text, got {len(rendered_flights)}: {rendered_flights}"
    carriers = [f["carrier"] for f in rendered_flights]
    assert "MockAir Evening" not in carriers, "Delay flight MockAir Evening should not appear when no later flight cutoff eliminated it!"
    assert "MockAir Direct" in carriers
    assert "MockAir Connecting" in carriers


# ============================================================================
# Round 5: Currency Preservation Across Bare-Number Budget & All Modifiers
# ============================================================================

def test_bare_number_budget_preserves_existing_currency():
    """
    Exact repro:
    - Initial plan set up in AUD with budget 800.
    - Bare-number budget update sent: "let's cap it at 300".
    - Asserts budget is updated to 300.0.
    - Asserts display_currency and target_currency remain AUD in session, plan payload, and response text.
    """
    sess_id = "test-aud-bare-budget"
    clear_session_trip(sess_id)
    save_session_trip(sess_id, {
        "origin": "HND",
        "origin_name": "Tokyo",
        "destination": "KIX",
        "destination_name": "Osaka",
        "departure_date": "2026-12-20",
        "check_in": "2026-12-20",
        "check_out": "2026-12-22",
        "budget_cap": 800.0,
        "display_currency": "AUD",
        "target_currency": "AUD",
        "awaiting_confirmation": True,
        "cost_line_items": [
            {"source_agent": "transport", "amount": 72.11, "currency": "AUD", "description": "Flight"},
            {"source_agent": "hotel", "amount": 190.0, "currency": "AUD", "description": "Hotel"}
        ],
        "cached_transport_results": [
            {"carrier": "ANA", "booking_ref": "ANA-101", "price": {"amount": 72.11, "currency": "AUD"}}
        ],
        "cached_hotel_results": [
            {"name": "Osaka Central Inn", "total_price": {"amount": 190.0, "currency": "AUD"}}
        ]
    })

    with patch("app.graph.call_budget_reconciliation_a2a", new_callable=AsyncMock) as mock_b, \
         patch("app.graph.call_single_currency_rate_a2a", new_callable=AsyncMock) as mock_rate:
        mock_rate.return_value = 1.0
        mock_b.return_value = json.dumps({
            "total_cost": 262.11,
            "currency": "AUD",
            "budget_cap": 300.0,
            "over_budget": False,
            "overage_amount": 0.0,
            "breakdown_by_agent": {"transport": 72.11, "hotel": 190.0}
        })

        resp = client.post("/chat", json={"message": "let's cap it at 300", "session_id": sess_id, "user_id": "test-user"})
        assert resp.status_code == 200
        data = resp.json()
        text = data["response"]
        plan = data.get("plan")

        assert plan is not None
        assert plan.get("target_currency") == "AUD"
        assert plan.get("display_currency") == "AUD"
        assert plan.get("budget_cap") == 300.0

        # Assert AUD in reconciliation or prices, never USD
        assert "300.00 AUD" in text or "300 AUD" in text
        assert "USD" not in text

        # Verify session state preserved AUD
        saved = get_session_trip(sess_id)
        assert saved.get("display_currency") == "AUD"
        assert saved.get("target_currency") == "AUD"
        assert saved.get("budget_cap") == 300.0


@pytest.mark.parametrize("modifier_type,modifier_message", [
    ("budget", "let's cap it at 300"),
    ("hotel", "can we switch hotels"),
    ("flight", "I want a different flight option"),
    ("delay", "my flight was delayed 2 hours"),
    ("dates", "can we change dates to 2026-12-22")
])
def test_all_modifier_paths_preserve_currency(modifier_type, modifier_message):
    """
    Parameterized test across all modifier types (budget, hotel, flight, delay, dates)
    ensuring that established non-USD currency (AUD) is strictly preserved, and NEVER silently
    reverts to USD.
    """
    sess_id = f"test-preserve-curr-{modifier_type}"
    clear_session_trip(sess_id)
    initial_flights = [
        {"carrier": "Qantas", "booking_ref": "QF-101", "price": {"amount": 100.0, "currency": "AUD"}, "departure_at": "2026-12-20T10:00:00"}
    ]
    initial_hotels = [
        {"name": "Sydney Harbour Hotel", "total_price": {"amount": 200.0, "currency": "AUD"}}
    ]
    save_session_trip(sess_id, {
        "origin": "SYD",
        "origin_name": "Sydney",
        "destination": "MEL",
        "destination_name": "Melbourne",
        "departure_date": "2026-12-20",
        "check_in": "2026-12-20",
        "check_out": "2026-12-22",
        "budget_cap": 800.0,
        "display_currency": "AUD",
        "target_currency": "AUD",
        "awaiting_confirmation": True,
        "cost_line_items": [
            {"source_agent": "transport", "amount": 100.0, "currency": "AUD", "description": "Flight"},
            {"source_agent": "hotel", "amount": 200.0, "currency": "AUD", "description": "Hotel"}
        ],
        "cached_transport_results": initial_flights,
        "prior_transport_results": initial_flights,
        "cached_hotel_results": initial_hotels,
        "prior_hotel_results": initial_hotels,
        "confirmed_plan": {
            "transport_results": initial_flights,
            "hotel_results": initial_hotels
        }
    })

    with patch("app.graph.call_hotel_agent_a2a", new_callable=AsyncMock) as mock_h, \
         patch("app.graph.call_transport_agent_a2a", new_callable=AsyncMock) as mock_t, \
         patch("app.graph.call_budget_reconciliation_a2a", new_callable=AsyncMock) as mock_b, \
         patch("app.graph.call_single_currency_rate_a2a", new_callable=AsyncMock) as mock_rate:
        mock_rate.return_value = 1.0
        mock_t.return_value = json.dumps({"results": [
            {"carrier": "Virgin Australia", "booking_ref": "VA-202", "price": {"amount": 110.0, "currency": "AUD"}, "departure_at": "2026-12-20T12:00:00"}
        ]})
        mock_h.return_value = json.dumps({"results": [
            {"name": "Melbourne Grand Boutique", "total_price": {"amount": 190.0, "currency": "AUD"}}
        ]})
        mock_b.return_value = json.dumps({
            "total_cost": 300.0,
            "currency": "AUD",
            "budget_cap": 300.0 if modifier_type == "budget" else 800.0,
            "over_budget": False,
            "overage_amount": 0.0,
            "breakdown_by_agent": {"transport": 110.0, "hotel": 190.0}
        })

        resp = client.post("/chat", json={"message": modifier_message, "session_id": sess_id, "user_id": "test-user"})
        assert resp.status_code == 200
        data = resp.json()
        plan = data.get("plan")
        saved = get_session_trip(sess_id)

        assert saved.get("display_currency") == "AUD", f"Session currency reverted for {modifier_type}: {saved.get('display_currency')}"
        assert saved.get("target_currency") == "AUD", f"Session target_currency reverted for {modifier_type}: {saved.get('target_currency')}"
        if plan:
            assert plan.get("display_currency") == "AUD", f"Plan display_currency reverted for {modifier_type}: {plan.get('display_currency')}"
            assert plan.get("target_currency") == "AUD", f"Plan target_currency reverted for {modifier_type}: {plan.get('target_currency')}"
        assert "USD" not in data["response"], f"Response unexpectedly contained USD for {modifier_type} modifier!"


# ============================================================================
# Route Data Integrity & Single Source of Truth Tests
# ============================================================================

def test_new_route_statement_always_resets_full_state():
    """
    Establish a Rome->Athens trip in GBP in session, then send a complete, different
    'X to Y' statement ('mumbai to delhi').
    Assert:
    - Fully fresh plan: correct banner (not 'Updated Trip Plan').
    - Route header/plan says 'Mumbai -> Delhi' (BOM -> DEL).
    - Default currency (USD, not GBP).
    - Flight search returned BOM -> DEL.
    - Zero Rome, Athens, or GBP leakage from the prior trip.
    """
    sess_id = "test-route-full-reset"
    clear_session_trip(sess_id)

    # Establish prior Rome -> Athens trip in GBP
    save_session_trip(sess_id, {
        "origin": "FCO",
        "origin_name": "Rome",
        "destination": "ATH",
        "destination_name": "Athens",
        "departure_date": "2026-10-01",
        "check_in": "2026-10-01",
        "check_out": "2026-10-05",
        "budget_cap": 900.0,
        "display_currency": "GBP",
        "target_currency": "GBP",
        "awaiting_confirmation": True,
        "cost_line_items": [
            {"source_agent": "transport", "amount": 250.0, "currency": "GBP", "description": "Rome to Athens Flight"},
            {"source_agent": "hotel", "amount": 400.0, "currency": "GBP", "description": "Athens Boutique Hotel"}
        ],
        "cached_transport_results": [
            {"carrier": "Aegean Airlines", "booking_ref": "A3-651", "price": {"amount": 250.0, "currency": "GBP"}, "origin": "FCO", "destination": "ATH"}
        ],
        "cached_hotel_results": [
            {"name": "Athens Grand Hotel", "total_price": {"amount": 400.0, "currency": "GBP"}}
        ],
        "confirmed_plan": {
            "origin": "FCO",
            "destination": "ATH",
            "display_currency": "GBP"
        }
    })

    # Now send fresh route statement "mumbai to delhi"
    with patch("app.graph.call_transport_agent_a2a", new_callable=AsyncMock) as mock_t, \
         patch("app.graph.call_hotel_agent_a2a", new_callable=AsyncMock) as mock_h, \
         patch("app.graph.call_local_guide_agent_a2a", new_callable=AsyncMock) as mock_g, \
         patch("app.graph.call_budget_reconciliation_a2a", new_callable=AsyncMock) as mock_b, \
         patch("app.graph.call_single_currency_rate_a2a", new_callable=AsyncMock) as mock_r, \
         patch("app.graph.classify_with_llm", new_callable=AsyncMock) as mock_clm, \
         patch("shared.gemini_client.generate_gemini_content", new_callable=AsyncMock) as mock_gem:
        
        mock_r.return_value = 1.0
        mock_t.return_value = json.dumps({"results": [
            {"carrier": "Air India", "booking_ref": "AI-101", "price": {"amount": 80.0, "currency": "USD"},
             "origin": "BOM", "destination": "DEL", "departure_at": "2026-09-10T10:00:00", "arrival_at": "2026-09-10T12:00:00", "duration_minutes": 120}
        ]})
        mock_h.return_value = json.dumps({"results": [
            {"name": "The Imperial New Delhi", "total_price": {"amount": 150.0, "currency": "USD"}, "price_per_night": {"amount": 75.0, "currency": "USD"}, "rating": 4.8}
        ]})
        mock_g.return_value = json.dumps({"results": [
            {"name": "Qutub Minar", "estimated_cost": {"amount": 10.0, "currency": "USD"}, "estimated_duration_hours": 2, "location": {"name": "Delhi"}, "tags": ["history"], "source_citation": "Guide", "description": "Historic minaret"}
        ]})
        mock_b.return_value = json.dumps({
            "total_cost": 240.0, "currency": "USD", "budget_cap": None, "over_budget": False, "overage_amount": 0.0, "breakdown_by_agent": {}
        })

        resp = client.post("/chat", json={"message": "mumbai to delhi", "session_id": sess_id, "user_id": "test-fresh-route-user"})
        assert resp.status_code == 200
        data = resp.json()
        resp_text = data["response"]
        plan = data.get("plan")

        # 1. Fresh banner check: Must NOT say Updated Trip Plan (Mid-Trip Adjustment)
        assert "### Trip Plan" in resp_text
        assert "Updated Trip Plan" not in resp_text
        assert "Mid-Trip Adjustment" not in resp_text

        # 2. Route header & plan data check: BOM -> DEL
        assert plan is not None
        assert plan.get("origin") == "BOM"
        assert plan.get("destination") == "DEL"
        assert plan.get("origin_name") == "Mumbai"
        assert plan.get("destination_name") == "Delhi"

        # 3. Currency check: Must default to USD, zero GBP leakage
        assert plan.get("display_currency") == "USD"
        assert plan.get("target_currency") == "USD"
        assert "GBP" not in resp_text

        # 4. Zero Rome or Athens leakage
        assert "Rome" not in resp_text
        assert "Athens" not in resp_text
        assert "FCO" not in resp_text
        assert "ATH" not in resp_text

        # 5. Session state must be fully fresh
        saved = get_session_trip(sess_id)
        assert saved.get("origin") == "BOM"
        assert saved.get("destination") == "DEL"
        assert saved.get("display_currency") == "USD"
        assert saved.get("budget_cap") is None  # Prior 900 GBP cap must be cleared


def test_header_and_search_data_never_disagree():
    """
    Invariant check: For any rendered plan, the displayed route header / plan
    origin/destination must strictly match the actual origin/destination used in the
    underlying flight search.
    """
    sess_id = "test-invariant-header-search"
    clear_session_trip(sess_id)

    test_routes = [
        ("mumbai to delhi", "BOM", "DEL", "Mumbai", "Delhi"),
        ("tokyo to osaka", "HND", "ITM", "Tokyo", "Osaka"),
        ("paris to london", "CDG", "LHR", "Paris", "London"),
    ]

    for user_msg, exp_orig, exp_dest, exp_orig_name, exp_dest_name in test_routes:
        with patch("app.graph.call_transport_agent_a2a", new_callable=AsyncMock) as mock_t, \
             patch("app.graph.call_hotel_agent_a2a", new_callable=AsyncMock) as mock_h, \
             patch("app.graph.call_local_guide_agent_a2a", new_callable=AsyncMock) as mock_g, \
             patch("app.graph.call_budget_reconciliation_a2a", new_callable=AsyncMock) as mock_b, \
             patch("app.graph.call_single_currency_rate_a2a", new_callable=AsyncMock) as mock_r, \
             patch("shared.gemini_client.generate_gemini_content", new_callable=AsyncMock) as mock_gem:
            
            mock_r.return_value = 1.0
            mock_t.return_value = json.dumps({"results": [
                {"carrier": "Airline", "booking_ref": "REF-1", "price": {"amount": 100.0, "currency": "USD"},
                 "origin": exp_orig, "destination": exp_dest, "departure_at": "2026-09-10T10:00:00", "arrival_at": "2026-09-10T12:00:00", "duration_minutes": 120}
            ]})
            mock_h.return_value = json.dumps({"results": [
                {"name": "Hotel City", "total_price": {"amount": 120.0, "currency": "USD"}, "price_per_night": {"amount": 60.0, "currency": "USD"}, "rating": 4.5}
            ]})
            mock_g.return_value = json.dumps({"results": []})
            mock_b.return_value = json.dumps({
                "total_cost": 220.0, "currency": "USD", "budget_cap": None, "over_budget": False, "overage_amount": 0.0, "breakdown_by_agent": {}
            })

            resp = client.post("/chat", json={"message": user_msg, "session_id": sess_id, "user_id": "test-invariant-user"})
            assert resp.status_code == 200
            data = resp.json()
            plan = data.get("plan")
            assert plan is not None

            # Assert header/plan matches underlying flight search origin/destination
            assert plan.get("origin") == exp_orig
            assert plan.get("destination") == exp_dest
            assert plan.get("origin_name") == exp_orig_name
            assert plan.get("destination_name") == exp_dest_name

            # Invariant: Flight transport results must match plan origin/dest
            flights = plan.get("transport_results", [])
            if flights:
                flight_orig = flights[0].get("origin", {}).get("iata") if isinstance(flights[0].get("origin"), dict) else flights[0].get("origin")
                flight_dest = flights[0].get("destination", {}).get("iata") if isinstance(flights[0].get("destination"), dict) else flights[0].get("destination")
                assert flight_orig == plan.get("origin")
                assert flight_dest == plan.get("destination")


def test_new_route_statement_priority_over_modifier_keywords():
    """
    Constructs route statements that contain words also used in modifier detection
    (e.g. 'switch', 'different', 'adjust', 'change', 'instead') and asserts they
    always classify as NEW_TRIP_ROUTE rather than getting intercepted by modifier matching.
    """
    ctx = {
        "origin": "FCO",
        "origin_name": "Rome",
        "destination": "ATH",
        "destination_name": "Athens",
        "awaiting_confirmation": True,
        "display_currency": "GBP"
    }

    test_cases = [
        "switch to mumbai to delhi",
        "different flight from mumbai to delhi",
        "adjust trip from mumbai to delhi",
        "change trip from tokyo to osaka",
        "mumbai to delhi instead",
        "update itinerary from london to paris"
    ]

    for phrase in test_cases:
        ci = classify_intent(phrase, session_context=ctx)
        assert ci.intent == IntentType.NEW_TRIP_ROUTE, f"Failed for phrase '{phrase}': got {ci.intent}"
        assert ci.origin is not None, f"Missing origin for '{phrase}'"
        assert ci.destination is not None, f"Missing destination for '{phrase}'"


