import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from icalendar import Calendar

from app.main import app
from app.session_store import (
    get_session_trip,
    save_session_trip,
    set_awaiting_confirmation,
    is_awaiting_confirmation,
    confirm_session_trip,
    get_confirmed_trip,
    clear_session_trip
)
from app.intent_classifier import classify_intent, IntentType
from app.export_service import generate_itinerary_pdf, generate_itinerary_ics

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_sessions():
    clear_session_trip("test-p6-session")
    clear_session_trip("unconfirmed-session")
    yield
    clear_session_trip("test-p6-session")
    clear_session_trip("unconfirmed-session")


def test_confirmation_flag_set_after_plan_render():
    session_id = "test-p6-session"
    
    # Send a trip request to generate a full plan
    resp = client.post(
        "/chat",
        json={"message": "plan a trip from London to Lisbon on 2026-09-24", "session_id": session_id, "user_id": "test-user-p6"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "Ready to finalize?" in data["response"]
    assert "Reply **'confirm'**" in data["response"]
    
    # Assert session flag is active
    assert is_awaiting_confirmation(session_id) is True
    session_ctx = get_session_trip(session_id)
    assert session_ctx.get("destination") == "LIS"
    assert session_ctx.get("origin") == "LHR"
    assert session_ctx.get("confirmed_plan") is not None


def test_confirmation_affirmative_finalizes():
    session_id = "test-p6-session"
    
    # 1. Establish trip and set awaiting confirmation
    client.post(
        "/chat",
        json={"message": "plan a trip from London to Lisbon on 2026-09-24", "session_id": session_id, "user_id": "test-user-p6"}
    )
    assert is_awaiting_confirmation(session_id) is True
    
    # 2. Reply with affirmative confirmation
    resp = client.post(
        "/chat",
        json={"message": "confirm", "session_id": session_id, "user_id": "test-user-p6"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "confirmed" in data["response"].lower()
    assert "export.pdf" in data["response"]
    assert "export.ics" in data["response"]
    
    # Assert session state is finalized
    assert is_awaiting_confirmation(session_id) is False
    confirmed = get_confirmed_trip(session_id)
    assert confirmed is not None
    assert get_session_trip(session_id).get("is_confirmed") is True
    assert get_session_trip(session_id).get("confirmed_at") is not None


def test_confirmation_change_request_reroutes():
    session_id = "test-p6-session"
    
    # 1. Generate plan
    client.post(
        "/chat",
        json={"message": "plan a trip from London to Lisbon on 2026-09-24", "session_id": session_id, "user_id": "test-user-p6"}
    )
    assert is_awaiting_confirmation(session_id) is True
    
    # 2. User requests a change instead of confirming
    resp = client.post(
        "/chat",
        json={"message": "can you find a cheaper hotel or make it under $250?", "session_id": session_id, "user_id": "test-user-p6"}
    )
    assert resp.status_code == 200
    data = resp.json()
    
    # Assert it re-rendered the plan with fresh confirmation prompt rather than finalizing
    assert "Trip Plan" in data["response"]
    assert "Ready to finalize" in data["response"]
    assert get_session_trip(session_id).get("is_confirmed") is not True
    assert is_awaiting_confirmation(session_id) is True


def test_confirmation_ambiguous_reasks():
    session_id = "test-p6-session"
    
    # 1. Generate plan
    client.post(
        "/chat",
        json={"message": "plan a trip from London to Lisbon on 2026-09-24", "session_id": session_id, "user_id": "test-user-p6"}
    )
    assert is_awaiting_confirmation(session_id) is True
    
    # 2. User sends unrelated/ambiguous message
    resp = client.post(
        "/chat",
        json={"message": "what is your favorite color?", "session_id": session_id, "user_id": "test-user-p6"}
    )
    assert resp.status_code == 200
    data = resp.json()
    
    # Assert it asks for clarification and does NOT finalize
    assert "I'm not sure if you'd like to confirm this plan or make changes" in data["response"]
    assert get_session_trip(session_id).get("is_confirmed") is not True
    assert is_awaiting_confirmation(session_id) is True


def test_export_pdf_generates_valid_file():
    plan_data = {
        "origin": "LHR",
        "origin_name": "London",
        "destination": "LIS",
        "destination_name": "Lisbon",
        "departure_date": "2026-09-24",
        "check_in": "2026-09-24",
        "check_out": "2026-09-26",
        "display_currency": "EUR",
        "confirmed_at": "2026-08-27 10:00:00 UTC",
        "cost_line_items": [
            {"source_agent": "transport", "description": "Flight: British Airways", "amount": 85.0, "currency": "EUR", "is_estimated": False},
            {"source_agent": "hotel", "description": "Hotel: Lisbon Boutique", "amount": 140.0, "currency": "EUR", "is_estimated": True},
        ],
        "transport_results": [
            {"carrier": "British Airways", "origin": {"iata": "LHR"}, "destination": {"iata": "LIS"}, "departure_at": "2026-09-24T10:00", "arrival_at": "2026-09-24T12:40", "duration_minutes": 160, "price": {"amount": 85.0, "currency": "EUR"}, "is_estimated": False, "booking_ref": "BA-123"}
        ],
        "hotel_results": [
            {"name": "Lisbon Boutique Hotel", "rating": 4.5, "check_in": "2026-09-24", "check_out": "2026-09-26", "total_price": {"amount": 140.0, "currency": "EUR"}, "amenities": ["WiFi", "Pool"]}
        ],
        "local_guide_results": [
            {"name": "Castelo de Sao Jorge", "estimated_duration_hours": 2.0, "estimated_cost": {"amount": 15.0, "currency": "EUR"}, "description": "Historic fortress.", "source_citation": "Wikivoyage"}
        ]
    }
    
    pdf_bytes = generate_itinerary_pdf(plan_data)
    assert isinstance(pdf_bytes, bytes)
    assert len(pdf_bytes) > 500
    assert pdf_bytes.startswith(b"%PDF")


def test_export_ics_valid_calendar():
    plan_data = {
        "origin": "LHR",
        "origin_name": "London",
        "destination": "LIS",
        "destination_name": "Lisbon",
        "departure_date": "2026-09-24",
        "check_in": "2026-09-24",
        "check_out": "2026-09-26",
        "display_currency": "EUR",
        "transport_results": [
            {"carrier": "British Airways", "origin": {"iata": "LHR"}, "destination": {"iata": "LIS"}, "departure_at": "2026-09-24T10:00", "arrival_at": "2026-09-24T12:40", "duration_minutes": 160, "price": {"amount": 85.0, "currency": "EUR"}, "is_estimated": False, "booking_ref": "BA-123"}
        ],
        "hotel_results": [
            {"name": "Lisbon Boutique Hotel", "rating": 4.5, "check_in": "2026-09-24", "check_out": "2026-09-26", "total_price": {"amount": 140.0, "currency": "EUR"}, "amenities": ["WiFi", "Pool"]}
        ],
        "local_guide_results": [
            {"name": "Castelo de Sao Jorge", "estimated_duration_hours": 2.0, "estimated_cost": {"amount": 15.0, "currency": "EUR"}, "description": "Historic fortress.", "source_citation": "Wikivoyage"}
        ]
    }
    
    ics_bytes = generate_itinerary_ics(plan_data)
    assert isinstance(ics_bytes, bytes)
    assert b"BEGIN:VCALENDAR" in ics_bytes
    assert b"END:VCALENDAR" in ics_bytes
    
    # Round-trip parse using icalendar library
    cal = Calendar.from_ical(ics_bytes)
    events = [component for component in cal.walk() if component.name == "VEVENT"]
    assert len(events) >= 2 # Transport + Hotel + Activities


def test_export_requires_confirmation():
    unconfirmed_id = "unconfirmed-session"
    
    # 1. Test unconfirmed session on PDF endpoint -> Expect 400 Bad Request
    resp_pdf = client.get(f"/api/trip/{unconfirmed_id}/export.pdf")
    assert resp_pdf.status_code == 400
    assert "No confirmed trip plan found" in resp_pdf.json()["detail"]
    
    # 2. Test unconfirmed session on ICS endpoint -> Expect 400 Bad Request
    resp_ics = client.get(f"/api/trip/{unconfirmed_id}/export.ics")
    assert resp_ics.status_code == 400
    assert "No confirmed trip plan found" in resp_ics.json()["detail"]
