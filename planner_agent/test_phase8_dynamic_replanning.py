import json
import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient

from app.main import app
from app.session_store import (
    get_session_trip,
    save_session_trip,
    confirm_session_trip,
    get_confirmed_trip,
    clear_session_trip,
    is_awaiting_confirmation
)
from app.intent_classifier import classify_intent, IntentType

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_sessions():
    clear_session_trip("test-p8-session")
    clear_session_trip("test-p8-empty")
    clear_session_trip("test-p8-export")
    yield
    clear_session_trip("test-p8-session")
    clear_session_trip("test-p8-empty")
    clear_session_trip("test-p8-export")


def test_midtrip_change_with_no_prior_plan_falls_through():
    """
    A budget-change or delay-shaped message with no existing plan in session
    doesn't crash or fabricate anything, falling through to normal handling (e.g. asking for destination).
    """
    session_id = "test-p8-empty"
    
    # 1. Budget drop message in an empty session
    resp = client.post(
        "/chat",
        json={"message": "my budget just dropped $500", "session_id": session_id, "user_id": "u-p8-empty"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "Where would you like to travel?" in data["response"]
    assert get_session_trip(session_id).get("pending_slot") == "destination"

    # 2. Flight delay message in an empty session
    clear_session_trip(session_id)
    resp_delay = client.post(
        "/chat",
        json={"message": "my flight got delayed 3 hours", "session_id": session_id, "user_id": "u-p8-empty"}
    )
    assert resp_delay.status_code == 200
    data_delay = resp_delay.json()
    assert "Where would you like to travel?" in data_delay["response"]
    assert get_session_trip(session_id).get("pending_slot") == "destination"


def test_budget_change_only_invokes_budget_and_dominant_agent():
    """
    Assert via mock assertions that a budget-drop message on an existing plan
    does NOT call Local Guide or the non-dominant agent, and DOES call Budget reconciliation
    and only the dominant agent (Hotel or Transport) if over budget.
    """
    session_id = "test-p8-session"

    # Pre-populate session with an existing plan:
    # Transport: $100, Hotel: $400 (dominant), Local Guide: $50 -> Total $550
    existing_transport = [
        {"carrier": "British Airways", "origin": {"iata": "LHR"}, "destination": {"iata": "LIS"}, "departure_at": "2026-09-24T10:00", "arrival_at": "2026-09-24T12:40", "duration_minutes": 160, "price": {"amount": 100.0, "currency": "USD"}, "is_estimated": False}
    ]
    existing_hotel = [
        {"name": "Luxury Lisbon Resort", "rating": 4.8, "check_in": "2026-09-24", "check_out": "2026-09-26", "price_per_night": {"amount": 200.0, "currency": "USD"}, "total_price": {"amount": 400.0, "currency": "USD"}, "amenities": ["Spa", "Pool"], "is_estimated": False}
    ]
    existing_guide = [
        {"name": "Castelo de Sao Jorge", "estimated_duration_hours": 2.0, "estimated_cost": {"amount": 50.0, "currency": "USD"}, "description": "Historic fortress.", "source_citation": "Wikivoyage", "location": {"name": "Lisbon"}}
    ]
    cost_items = [
        {"source_agent": "transport", "amount": 100.0, "currency": "USD", "description": "Flight: BA"},
        {"source_agent": "hotel", "amount": 400.0, "currency": "USD", "description": "Hotel: Luxury Resort"},
        {"source_agent": "local_guide", "amount": 50.0, "currency": "USD", "description": "Activity: Castle"}
    ]

    plan_payload = {
        "origin": "LHR",
        "origin_name": "London",
        "destination": "LIS",
        "destination_name": "Lisbon",
        "departure_date": "2026-09-24",
        "check_in": "2026-09-24",
        "check_out": "2026-09-26",
        "budget_cap": 600.0,
        "target_currency": "USD",
        "display_currency": "USD",
        "transport_results": existing_transport,
        "hotel_results": existing_hotel,
        "local_guide_results": existing_guide,
        "cost_line_items": cost_items
    }
    save_session_trip(session_id, {
        **plan_payload,
        "confirmed_plan": plan_payload
    })
    confirm_session_trip(session_id, plan_payload)

    # Mock all A2A client functions to track invocations
    with patch("app.graph.call_budget_reconciliation_a2a", new_callable=AsyncMock) as mock_budget_recon, \
         patch("app.graph.call_hotel_agent_a2a", new_callable=AsyncMock) as mock_hotel, \
         patch("app.graph.call_transport_agent_a2a", new_callable=AsyncMock) as mock_transport, \
         patch("app.graph.call_local_guide_agent_a2a", new_callable=AsyncMock) as mock_guide:

        # First reconciliation returns over_budget with Hotel as dominant ($400 vs $100)
        # Second reconciliation (after hotel reduction) returns within budget
        mock_budget_recon.side_effect = [
            json.dumps({
                "total_cost": 550.0,
                "currency": "USD",
                "budget_cap": 300.0,
                "over_budget": True,
                "overage_amount": 250.0,
                "breakdown_by_agent": {"transport": 100.0, "hotel": 400.0, "local_guide": 50.0}
            }),
            json.dumps({
                "total_cost": 300.0,
                "currency": "USD",
                "budget_cap": 300.0,
                "over_budget": False,
                "overage_amount": 0.0,
                "breakdown_by_agent": {"transport": 100.0, "hotel": 150.0, "local_guide": 50.0}
            })
        ]
        # Revised cheaper hotel option returned by hotel agent
        mock_hotel.return_value = json.dumps({
            "results": [
                {"name": "Budget Lisbon Inn", "rating": 4.1, "check_in": "2026-09-24", "check_out": "2026-09-26", "price_per_night": {"amount": 75.0, "currency": "USD"}, "total_price": {"amount": 150.0, "currency": "USD"}, "amenities": ["WiFi"], "is_estimated": False}
            ]
        })


        # Send budget drop message: "my budget just dropped $300"
        resp = client.post(
            "/chat",
            json={"message": "my budget just dropped $300", "session_id": session_id, "user_id": "u-p8"}
        )
        assert resp.status_code == 200
        data = resp.json()

        # Assert invocations:
        # 1. Budget reconciliation MUST be called
        assert mock_budget_recon.call_count >= 1

        # 2. Hotel Agent MUST be called (it was the dominant contributor)
        assert mock_hotel.call_count == 1

        # 3. Transport Agent MUST NOT be called (not dominant)
        assert mock_transport.call_count == 0

        # 4. Local Guide Agent MUST NOT be called (never negotiated)
        assert mock_guide.call_count == 0

        # 5. Local guide recommendations in response must be preserved
        assert "Castelo de Sao Jorge" in data["response"]
        assert "British Airways" in data["response"]


def test_flight_delay_only_invokes_transport():
    """
    Assert Hotel and Local Guide are NOT re-called at all when a flight delay is reported.
    Their results in the re-rendered plan must be preserved identically.
    """
    session_id = "test-p8-session"

    existing_hotel = [
        {"name": "Lisbon Sunshine Hotel", "rating": 4.4, "check_in": "2026-09-24", "check_out": "2026-09-26", "price_per_night": {"amount": 110.0, "currency": "USD"}, "total_price": {"amount": 220.0, "currency": "USD"}, "amenities": ["WiFi", "Breakfast"], "is_estimated": False}
    ]
    existing_guide = [
        {"name": "Belem Tower Exploration", "estimated_duration_hours": 3.0, "estimated_cost": {"amount": 20.0, "currency": "USD"}, "description": "UNESCO World Heritage fortress.", "source_citation": "Wikivoyage", "location": {"name": "Belem"}}
    ]
    cost_items = [
        {"source_agent": "transport", "amount": 120.0, "currency": "USD", "description": "Flight: Original BA"},
        {"source_agent": "hotel", "amount": 220.0, "currency": "USD", "description": "Hotel: Lisbon Sunshine"},
        {"source_agent": "local_guide", "amount": 20.0, "currency": "USD", "description": "Activity: Belem"}
    ]

    plan_payload = {
        "origin": "LHR",
        "origin_name": "London",
        "destination": "LIS",
        "destination_name": "Lisbon",
        "departure_date": "2026-09-24",
        "check_in": "2026-09-24",
        "check_out": "2026-09-26",
        "budget_cap": 500.0,
        "target_currency": "USD",
        "display_currency": "USD",
        "transport_results": [
            {"carrier": "Original BA Flight", "origin": {"iata": "LHR"}, "destination": {"iata": "LIS"}, "departure_at": "2026-09-24T10:00", "arrival_at": "2026-09-24T12:40", "duration_minutes": 160, "price": {"amount": 120.0, "currency": "USD"}, "is_estimated": False}
        ],
        "hotel_results": existing_hotel,
        "local_guide_results": existing_guide,
        "cost_line_items": cost_items
    }
    save_session_trip(session_id, {
        **plan_payload,
        "confirmed_plan": plan_payload
    })
    confirm_session_trip(session_id, plan_payload)

    with patch("app.graph.call_transport_agent_a2a", new_callable=AsyncMock) as mock_transport, \
         patch("app.graph.call_hotel_agent_a2a", new_callable=AsyncMock) as mock_hotel, \
         patch("app.graph.call_local_guide_agent_a2a", new_callable=AsyncMock) as mock_guide, \
         patch("app.graph.call_budget_reconciliation_a2a", new_callable=AsyncMock) as mock_budget_recon:

        mock_transport.return_value = json.dumps({
            "results": [
                {"carrier": "Rescheduled TAP Air Portugal", "origin": {"iata": "LHR"}, "destination": {"iata": "LIS"}, "departure_at": "2026-09-24T14:30", "arrival_at": "2026-09-24T17:15", "duration_minutes": 165, "price": {"amount": 135.0, "currency": "USD"}, "is_estimated": False}
            ]
        })
        mock_budget_recon.return_value = json.dumps({
            "total_cost": 375.0,
            "currency": "USD",
            "budget_cap": 500.0,
            "over_budget": False,
            "overage_amount": 0.0,
            "breakdown_by_agent": {"transport": 135.0, "hotel": 220.0, "local_guide": 20.0}
        })

        # Send flight delay message: "my flight got delayed 3 hours"
        resp = client.post(
            "/chat",
            json={"message": "my flight got delayed 3 hours", "session_id": session_id, "user_id": "u-p8"}
        )
        assert resp.status_code == 200
        data = resp.json()

        # Assert:
        # 1. Transport Agent MUST be called
        assert mock_transport.call_count == 1

        # 2. Hotel Agent MUST NOT be called
        assert mock_hotel.call_count == 0

        # 3. Local Guide Agent MUST NOT be called
        assert mock_guide.call_count == 0

        # 4. Updated transport is displayed
        assert "Rescheduled TAP Air Portugal" in data["response"]

        # 5. Hotel and Guide sections are preserved byte-identically
        assert "Lisbon Sunshine Hotel" in data["response"]
        assert "Belem Tower Exploration" in data["response"]


def test_midtrip_change_after_confirmation_requires_reconfirmation():
    """
    Confirm a plan, send a budget-change or delay message, assert is_confirmed reverts to False
    and awaiting_confirmation is set again.
    """
    session_id = "test-p8-session"

    # 1. Establish and confirm a plan
    client.post(
        "/chat",
        json={"message": "plan a trip from London to Lisbon on 2026-09-24 with budget $500", "session_id": session_id, "user_id": "u-p8"}
    )
    confirm_resp = client.post(
        "/chat",
        json={"message": "confirm", "session_id": session_id, "user_id": "u-p8"}
    )
    assert confirm_resp.status_code == 200
    assert get_session_trip(session_id).get("is_confirmed") is True
    assert is_awaiting_confirmation(session_id) is False

    # 2. Send mid-trip change: budget drop
    change_resp = client.post(
        "/chat",
        json={"message": "my budget just dropped $200", "session_id": session_id, "user_id": "u-p8"}
    )
    assert change_resp.status_code == 200
    
    # 3. Assert confirmation is invalidated
    session_state = get_session_trip(session_id)
    assert session_state.get("is_confirmed") is False
    assert session_state.get("awaiting_confirmation") is True
    assert is_awaiting_confirmation(session_id) is True
    assert "Ready to finalize" in change_resp.json()["response"]


def test_export_blocked_after_uncommitted_change():
    """
    After a mid-trip change reopens confirmation, exporting must fail (HTTP 400 Bad Request)
    until the user explicitly re-confirms.
    """
    session_id = "test-p8-export"

    # 1. Establish and confirm trip
    client.post(
        "/chat",
        json={"message": "plan a trip from London to Lisbon on 2026-09-24 with budget $500", "session_id": session_id, "user_id": "u-p8"}
    )
    client.post(
        "/chat",
        json={"message": "confirm", "session_id": session_id, "user_id": "u-p8"}
    )
    assert get_session_trip(session_id).get("is_confirmed") is True

    # PDF export should succeed
    pdf_resp = client.get(f"/api/trip/{session_id}/export.pdf")
    assert pdf_resp.status_code == 200

    # 2. User modifies budget mid-trip
    client.post(
        "/chat",
        json={"message": "actually keep it under 300 EUR now", "session_id": session_id, "user_id": "u-p8"}
    )
    assert get_session_trip(session_id).get("is_confirmed") is False

    # 3. Export MUST now be blocked with 400 Bad Request
    blocked_pdf = client.get(f"/api/trip/{session_id}/export.pdf")
    assert blocked_pdf.status_code == 400
    assert "No confirmed trip plan found" in blocked_pdf.json()["detail"]

    blocked_ics = client.get(f"/api/trip/{session_id}/export.ics")
    assert blocked_ics.status_code == 400
    assert "No confirmed trip plan found" in blocked_ics.json()["detail"]

    # 4. Re-confirm trip
    reconfirm_resp = client.post(
        "/chat",
        json={"message": "confirm", "session_id": session_id, "user_id": "u-p8"}
    )
    assert reconfirm_resp.status_code == 200
    assert get_session_trip(session_id).get("is_confirmed") is True

    # 5. Export succeeds again
    re_pdf = client.get(f"/api/trip/{session_id}/export.pdf")
    assert re_pdf.status_code == 200
    assert re_pdf.content.startswith(b"%PDF")


def test_budget_drop_equal_to_cap_triggers_honest_zero_response():
    """
    Exact repro: $500 cap, 'my budget just dropped $500'.
    Asserts response states budget is now $0/unusable and does NOT run negotiation
    or present a fabricated cap of any kind.
    """
    session_id = "test-p8-zero-drop"
    clear_session_trip(session_id)

    existing_transport = [{"carrier": "BA", "origin": {"iata": "LHR"}, "destination": {"iata": "LIS"}, "departure_at": "2026-09-24T10:00", "arrival_at": "2026-09-24T12:40", "duration_minutes": 160, "price": {"amount": 100.0, "currency": "USD"}, "is_estimated": False}]
    existing_hotel = [{"name": "Lisbon Hotel", "rating": 4.5, "check_in": "2026-09-24", "check_out": "2026-09-26", "price_per_night": {"amount": 100.0, "currency": "USD"}, "total_price": {"amount": 200.0, "currency": "USD"}, "amenities": ["WiFi"], "is_estimated": False}]
    plan_payload = {
        "origin": "LHR", "origin_name": "London", "destination": "LIS", "destination_name": "Lisbon",
        "departure_date": "2026-09-24", "check_in": "2026-09-24", "check_out": "2026-09-26",
        "budget_cap": 500.0, "target_currency": "USD", "display_currency": "USD",
        "transport_results": existing_transport, "hotel_results": existing_hotel, "local_guide_results": [],
        "cost_line_items": [{"source_agent": "hotel", "amount": 200.0, "currency": "USD"}]
    }
    save_session_trip(session_id, {**plan_payload, "confirmed_plan": plan_payload})
    confirm_session_trip(session_id, plan_payload)

    with patch("app.graph.call_budget_reconciliation_a2a", new_callable=AsyncMock) as mock_recon, \
         patch("app.graph.call_hotel_agent_a2a", new_callable=AsyncMock) as mock_hotel:
        resp = client.post(
            "/chat",
            json={"message": "my budget just dropped $500", "session_id": session_id, "user_id": "u-zero"}
        )
        assert resp.status_code == 200
        text = resp.json()["response"]

        # 1. Honestly states $0 remaining
        assert "A $500 drop from your $500 budget leaves $0" in text
        assert "that's not enough for any trip" in text
        assert "Would you like to set a different budget, or cancel this trip?" in text

        # 2. Does NOT present a fabricated 20.0 cap
        assert "20.0" not in text
        assert "20.00" not in text

        # 3. Does NOT run negotiation or reconciliation
        assert mock_recon.call_count == 0
        assert mock_hotel.call_count == 0

        # 4. Sets pending_slot to budget_clarification
        session_trip = get_session_trip(session_id)
        assert session_trip.get("pending_slot") == "budget_clarification"


def test_budget_drop_exceeding_cap_handled_honestly():
    """
    A drop larger than current cap (e.g. dropped $600 on $500 budget).
    Asserts caught as negative remainder, not silently floored.
    """
    session_id = "test-p8-negative-drop"
    clear_session_trip(session_id)

    plan_payload = {
        "origin": "LHR", "origin_name": "London", "destination": "LIS", "destination_name": "Lisbon",
        "departure_date": "2026-09-24", "check_in": "2026-09-24", "check_out": "2026-09-26",
        "budget_cap": 500.0, "target_currency": "USD", "display_currency": "USD",
        "cost_line_items": [{"source_agent": "hotel", "amount": 200.0, "currency": "USD"}]
    }
    save_session_trip(session_id, {**plan_payload, "confirmed_plan": plan_payload})
    confirm_session_trip(session_id, plan_payload)

    with patch("app.graph.call_budget_reconciliation_a2a", new_callable=AsyncMock) as mock_recon, \
         patch("app.graph.call_hotel_agent_a2a", new_callable=AsyncMock) as mock_hotel:
        resp = client.post(
            "/chat",
            json={"message": "my budget just dropped $600", "session_id": session_id, "user_id": "u-neg"}
        )
        assert resp.status_code == 200
        text = resp.json()["response"]

        # Plain arithmetic with -$100
        assert "A $600 drop from your $500 budget leaves -$100" in text
        assert "that's not enough for any trip" in text
        assert mock_recon.call_count == 0
        assert mock_hotel.call_count == 0
        assert get_session_trip(session_id).get("pending_slot") == "budget_clarification"


def test_budget_drop_leaving_small_valid_amount_still_works():
    """
    A drop that leaves a small but positive real amount ($500 -> drop $480 -> $20)
    should proceed normally through reconciliation and negotiation.
    """
    session_id = "test-p8-small-valid"
    clear_session_trip(session_id)

    existing_transport = [{"carrier": "BA", "origin": {"iata": "LHR"}, "destination": {"iata": "LIS"}, "departure_at": "2026-09-24T10:00", "arrival_at": "2026-09-24T12:40", "duration_minutes": 160, "price": {"amount": 5.0, "currency": "USD"}, "is_estimated": False}]
    existing_hotel = [{"name": "Lisbon Hostel", "rating": 4.0, "check_in": "2026-09-24", "check_out": "2026-09-26", "price_per_night": {"amount": 10.0, "currency": "USD"}, "total_price": {"amount": 20.0, "currency": "USD"}, "amenities": ["WiFi"], "is_estimated": False}]
    cost_items = [
        {"source_agent": "transport", "amount": 5.0, "currency": "USD"},
        {"source_agent": "hotel", "amount": 20.0, "currency": "USD"}
    ]
    plan_payload = {
        "origin": "LHR", "origin_name": "London", "destination": "LIS", "destination_name": "Lisbon",
        "departure_date": "2026-09-24", "check_in": "2026-09-24", "check_out": "2026-09-26",
        "budget_cap": 500.0, "target_currency": "USD", "display_currency": "USD",
        "transport_results": existing_transport, "hotel_results": existing_hotel, "local_guide_results": [],
        "cost_line_items": cost_items
    }
    save_session_trip(session_id, {**plan_payload, "confirmed_plan": plan_payload})
    confirm_session_trip(session_id, plan_payload)

    with patch("app.graph.call_budget_reconciliation_a2a", new_callable=AsyncMock) as mock_recon, \
         patch("app.graph.call_hotel_agent_a2a", new_callable=AsyncMock) as mock_hotel:
        mock_recon.return_value = json.dumps({
            "total_cost": 25.0, "currency": "USD", "budget_cap": 20.0,
            "over_budget": False, "overage_amount": 0.0,
            "breakdown_by_agent": {"transport": 5.0, "hotel": 20.0}
        })
        resp = client.post(
            "/chat",
            json={"message": "my budget just dropped $480", "session_id": session_id, "user_id": "u-small"}
        )
        assert resp.status_code == 200
        text = resp.json()["response"]

        # Reconciles against the genuine $20 cap
        assert mock_recon.call_count >= 1
        call_cap = mock_recon.call_args[0][2]
        assert call_cap == 20.0
        assert "20.00 USD" in text or "20.0 USD" in text


def test_no_hardcoded_floor_remains():
    """
    Code-level assertion confirming no bare numeric floor remains in extract_budget_intent or graph.py.
    """
    import inspect
    from app import graph
    source = inspect.getsource(graph)

    # 1. No max(20 in graph.py
    assert 'max(20' not in source
    assert 'max( 20' not in source

    # 2. extract_budget_intent source inspection
    extract_source = inspect.getsource(graph.extract_budget_intent)
    assert 'max(' not in extract_source, "extract_budget_intent must not clamp the resulting budget with max()"


def test_budget_clarification_continuation_and_cancellation():
    """
    Asserts that after receiving a zero-budget prompt, subsequent message:
    1. with '$400' provides the new budget and re-runs re-entry.
    2. with 'cancel' cancels the trip and clears the session.
    """
    session_id = "test-p8-clarify"
    clear_session_trip(session_id)

    plan_payload = {
        "origin": "LHR", "origin_name": "London", "destination": "LIS", "destination_name": "Lisbon",
        "departure_date": "2026-09-24", "check_in": "2026-09-24", "check_out": "2026-09-26",
        "budget_cap": 500.0, "target_currency": "USD", "display_currency": "USD",
        "cost_line_items": [{"source_agent": "hotel", "amount": 200.0, "currency": "USD"}]
    }
    save_session_trip(session_id, {**plan_payload, "confirmed_plan": plan_payload})
    confirm_session_trip(session_id, plan_payload)

    # Trigger zero budget
    resp1 = client.post("/chat", json={"message": "my budget just dropped $500", "session_id": session_id, "user_id": "u1"})
    assert "leaves $0" in resp1.json()["response"]
    assert get_session_trip(session_id).get("pending_slot") == "budget_clarification"

    # User provides new budget: "$400"
    with patch("app.graph.call_budget_reconciliation_a2a", new_callable=AsyncMock) as mock_recon:
        mock_recon.return_value = json.dumps({
            "total_cost": 200.0, "currency": "USD", "budget_cap": 400.0,
            "over_budget": False, "overage_amount": 0.0,
            "breakdown_by_agent": {"hotel": 200.0}
        })
        resp2 = client.post("/chat", json={"message": "$400", "session_id": session_id, "user_id": "u1"})
        assert resp2.status_code == 200
        assert mock_recon.call_count >= 1
        assert mock_recon.call_args[0][2] == 400.0
        assert get_session_trip(session_id).get("pending_slot") is None

    # Again trigger zero budget: $500 drop from the now $400 budget leaves -$100
    resp3 = client.post("/chat", json={"message": "my budget just dropped $500", "session_id": session_id, "user_id": "u1"})
    assert "leaves -$100" in resp3.json()["response"]
    assert get_session_trip(session_id).get("pending_slot") == "budget_clarification"

    # User says "cancel"
    resp4 = client.post("/chat", json={"message": "cancel this trip", "session_id": session_id, "user_id": "u1"})
    assert resp4.status_code == 200
    assert "cancelled" in resp4.json()["response"].lower()
    assert get_session_trip(session_id) == {}


def test_flight_delay_actually_filters_results():
    """
    Mock the Transport Agent's adapter data such that a later option genuinely exists;
    send a delay report; assert the returned options differ from the pre-delay set
    and reflect the later departure constraint.
    """
    session_id = "test-p8-delay-filter"
    clear_session_trip(session_id)

    original_flight = {
        "mode": "flight", "carrier": "MockAir Early",
        "origin": {"name": "London", "iata": "LHR"}, "destination": {"name": "Lisbon", "iata": "LIS"},
        "departure_at": "2026-09-10T08:00:00", "arrival_at": "2026-09-10T10:45:00",
        "duration_minutes": 165, "price": {"amount": 100.0, "currency": "EUR"},
        "booking_ref": "MOCK-1", "is_estimated": True
    }
    later_flight = {
        "mode": "flight", "carrier": "MockAir Evening",
        "origin": {"name": "London", "iata": "LHR"}, "destination": {"name": "Lisbon", "iata": "LIS"},
        "departure_at": "2026-09-10T18:00:00", "arrival_at": "2026-09-10T20:45:00",
        "duration_minutes": 165, "price": {"amount": 120.0, "currency": "EUR"},
        "booking_ref": "MOCK-2", "is_estimated": True
    }

    plan_payload = {
        "origin": "LHR", "origin_name": "London", "destination": "LIS", "destination_name": "Lisbon",
        "departure_date": "2026-09-10", "check_in": "2026-09-10", "check_out": "2026-09-12",
        "budget_cap": 500.0, "target_currency": "EUR", "display_currency": "EUR",
        "transport_results": [original_flight],
        "hotel_results": [{"name": "Lisbon Hotel", "total_price": {"amount": 200.0, "currency": "EUR"}, "price_per_night": {"amount": 100.0, "currency": "EUR"}, "rating": 4.5, "amenities": ["WiFi"], "check_in": "2026-09-10", "check_out": "2026-09-12"}],
        "local_guide_results": []
    }
    save_session_trip(session_id, {**plan_payload, "confirmed_plan": plan_payload})
    confirm_session_trip(session_id, plan_payload)

    with patch("app.graph.call_transport_agent_a2a", new_callable=AsyncMock) as mock_t:
        mock_t.return_value = json.dumps({
            "success": True,
            "results": [later_flight],
            "no_later_options": False
        })
        resp = client.post("/chat", json={"message": "my flight got delayed 3 hours", "session_id": session_id, "user_id": "u-delay"})
        assert resp.status_code == 200
        text = resp.json()["response"]

        assert mock_t.call_count == 1
        assert "MockAir Evening" in text
        assert "MockAir Early" not in text
        assert "Updated Trip Plan (Mid-Trip Adjustment)" in text
        assert "Your flight options have been updated to reflect the schedule change" in text


def test_flight_delay_no_better_options_says_so_honestly():
    """
    Mock adapter data with nothing later available; assert the response explicitly states
    no better options were found, and does NOT render the 'updated' banner claiming a change that didn't happen.
    """
    session_id = "test-p8-delay-none"
    clear_session_trip(session_id)

    original_flight = {
        "mode": "flight", "carrier": "MockAir OnlyOption",
        "origin": {"name": "London", "iata": "LHR"}, "destination": {"name": "Lisbon", "iata": "LIS"},
        "departure_at": "2026-09-10T14:30:00", "arrival_at": "2026-09-10T17:15:00",
        "duration_minutes": 165, "price": {"amount": 100.0, "currency": "EUR"},
        "booking_ref": "MOCK-1", "is_estimated": True
    }

    plan_payload = {
        "origin": "LHR", "origin_name": "London", "destination": "LIS", "destination_name": "Lisbon",
        "departure_date": "2026-09-10", "check_in": "2026-09-10", "check_out": "2026-09-12",
        "budget_cap": 500.0, "target_currency": "EUR", "display_currency": "EUR",
        "transport_results": [original_flight],
        "hotel_results": [{"name": "Lisbon Hotel", "total_price": {"amount": 200.0, "currency": "EUR"}, "price_per_night": {"amount": 100.0, "currency": "EUR"}, "rating": 4.5, "amenities": ["WiFi"], "check_in": "2026-09-10", "check_out": "2026-09-12"}],
        "local_guide_results": []
    }
    save_session_trip(session_id, {**plan_payload, "confirmed_plan": plan_payload})
    confirm_session_trip(session_id, plan_payload)

    with patch("app.graph.call_transport_agent_a2a", new_callable=AsyncMock) as mock_t:
        mock_t.return_value = json.dumps({
            "success": True,
            "results": [original_flight],
            "no_later_options": True
        })
        resp = client.post("/chat", json={"message": "my flight got delayed 3 hours", "session_id": session_id, "user_id": "u-delay2"})
        assert resp.status_code == 200
        text = resp.json()["response"]

        assert "No later flight options were found given the delay — here are your original options" in text
        assert "Updated Trip Plan (Mid-Trip Adjustment)" not in text
        assert "Your flight options have been updated" not in text
        assert "MockAir OnlyOption" in text


def test_cheaper_hotel_returns_genuinely_cheaper_option():
    """
    Mock Hotel Agent data with a cheaper option available; assert 'cheaper hotel'
    returns it, with is_replan=True and the correct banner.
    """
    session_id = "test-p8-cheaper-hotel"
    clear_session_trip(session_id)

    initial_hotel = {
        "name": "Lisbon Luxury Palace", "rating": 4.8,
        "check_in": "2026-09-10", "check_out": "2026-09-12",
        "price_per_night": {"amount": 180.0, "currency": "EUR"},
        "total_price": {"amount": 360.0, "currency": "EUR"},
        "amenities": ["Spa", "Pool"], "booking_ref": "MOCK-H1", "is_estimated": True
    }
    cheaper_hotel = {
        "name": "Lisbon Eco Traveler Hostel", "rating": 4.0,
        "check_in": "2026-09-10", "check_out": "2026-09-12",
        "price_per_night": {"amount": 55.0, "currency": "EUR"},
        "total_price": {"amount": 110.0, "currency": "EUR"},
        "amenities": ["WiFi", "Shared Kitchen"], "booking_ref": "MOCK-H3", "is_estimated": True
    }

    plan_payload = {
        "origin": "LHR", "origin_name": "London", "destination": "LIS", "destination_name": "Lisbon",
        "departure_date": "2026-09-10", "check_in": "2026-09-10", "check_out": "2026-09-12",
        "budget_cap": 800.0, "target_currency": "EUR", "display_currency": "EUR",
        "transport_results": [{"carrier": "MockAir", "price": {"amount": 85.0, "currency": "EUR"}, "departure_at": "2026-09-10T14:30:00", "arrival_at": "2026-09-10T17:15:00", "duration_minutes": 165, "origin": {"iata": "LHR"}, "destination": {"iata": "LIS"}}],
        "hotel_results": [initial_hotel],
        "local_guide_results": []
    }
    save_session_trip(session_id, {**plan_payload, "confirmed_plan": plan_payload})
    confirm_session_trip(session_id, plan_payload)

    with patch("app.graph.call_hotel_agent_a2a", new_callable=AsyncMock) as mock_h:
        mock_h.return_value = json.dumps({
            "success": True,
            "results": [cheaper_hotel],
            "no_cheaper_options": False
        })
        resp = client.post("/chat", json={"message": "actually the cheaper hotel", "session_id": session_id, "user_id": "u-cheap"})
        assert resp.status_code == 200
        text = resp.json()["response"]

        assert mock_h.call_count == 1
        passed_ceiling = mock_h.call_args[1].get("price_ceiling")
        assert passed_ceiling is not None
        assert passed_ceiling <= 359.0

        assert "Lisbon Eco Traveler Hostel" in text
        assert "Updated Trip Plan (Mid-Trip Adjustment)" in text
        assert "Your hotel options have been updated with a cheaper accommodation" in text


def test_cheaper_hotel_already_cheapest_says_so_honestly():
    """
    Mock data where the current selection is already the cheapest available;
    assert an honest 'already the cheapest' message, not a silent no-op dressed as success.
    """
    session_id = "test-p8-cheapest-already"
    clear_session_trip(session_id)

    cheapest_hotel = {
        "name": "Lisbon Eco Traveler Hostel", "rating": 4.0,
        "check_in": "2026-09-10", "check_out": "2026-09-12",
        "price_per_night": {"amount": 55.0, "currency": "EUR"},
        "total_price": {"amount": 110.0, "currency": "EUR"},
        "amenities": ["WiFi"], "booking_ref": "MOCK-H3", "is_estimated": True
    }

    plan_payload = {
        "origin": "LHR", "origin_name": "London", "destination": "LIS", "destination_name": "Lisbon",
        "departure_date": "2026-09-10", "check_in": "2026-09-10", "check_out": "2026-09-12",
        "budget_cap": 800.0, "target_currency": "EUR", "display_currency": "EUR",
        "transport_results": [{"carrier": "MockAir", "price": {"amount": 85.0, "currency": "EUR"}, "departure_at": "2026-09-10T14:30:00", "arrival_at": "2026-09-10T17:15:00", "duration_minutes": 165, "origin": {"iata": "LHR"}, "destination": {"iata": "LIS"}}],
        "hotel_results": [cheapest_hotel],
        "local_guide_results": []
    }
    save_session_trip(session_id, {**plan_payload, "confirmed_plan": plan_payload})
    confirm_session_trip(session_id, plan_payload)

    with patch("app.graph.call_hotel_agent_a2a", new_callable=AsyncMock) as mock_h:
        mock_h.return_value = json.dumps({
            "success": True,
            "results": [],
            "no_cheaper_options": True
        })
        resp = client.post("/chat", json={"message": "can I get a cheaper hotel?", "session_id": session_id, "user_id": "u-cheapest"})
        assert resp.status_code == 200
        text = resp.json()["response"]

        assert mock_h.call_count == 1
        assert "This is already the most affordable option available for your dates" in text
        assert "Updated Trip Plan (Mid-Trip Adjustment)" not in text
        assert "Lisbon Eco Traveler Hostel" in text


def test_modify_existing_extracts_semantic_modifier():
    """
    Unit test _extract_plan_details directly — assert 'cheaper hotel', 'cheaper flight',
    and 'different hotel' each produce distinct, correct structured output, not the same
    generic payload as a fresh query.
    """
    from app.a2a_client import _extract_plan_details

    # 1. Fresh generic query
    fresh = _extract_plan_details("trip from London to Lisbon on 2026-09-10")
    assert fresh.get("modifier") is None
    assert fresh.get("prefer_cheaper") is not True
    assert fresh.get("prefer_different") is not True
    assert fresh.get("target_entity") is None

    # 2. Cheaper hotel
    mod_cheap_hotel = _extract_plan_details("actually the cheaper hotel")
    assert mod_cheap_hotel.get("modifier") == "cheaper_hotel"
    assert mod_cheap_hotel.get("target_entity") == "hotel"
    assert mod_cheap_hotel.get("prefer_cheaper") is True

    # 3. Cheaper flight
    mod_cheap_flight = _extract_plan_details("find me a cheaper flight")
    assert mod_cheap_flight.get("modifier") == "cheaper_flight"
    assert mod_cheap_flight.get("target_entity") == "transport"
    assert mod_cheap_flight.get("prefer_cheaper") is True

    # 4. Different hotel
    mod_diff_hotel = _extract_plan_details("show me a different hotel")
    assert mod_diff_hotel.get("modifier") == "different_hotel"
    assert mod_diff_hotel.get("target_entity") == "hotel"
    assert mod_diff_hotel.get("prefer_different") is True

    # Ensure all 3 distinct outputs differ from each other and from fresh
    assert mod_cheap_hotel != fresh
    assert mod_cheap_flight != fresh
    assert mod_diff_hotel != fresh
    assert mod_cheap_hotel != mod_cheap_flight
    assert mod_cheap_hotel != mod_diff_hotel
    assert mod_cheap_flight != mod_diff_hotel
