import json
import os
import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient

from app.main import app
from app.session_store import (
    get_session_trip,
    save_session_trip,
    reset_session_trip,
    clear_session_trip,
)
from app.intent_classifier import classify_intent, IntentType
from shared.resolvers import infer_default_currency_from_route

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_test_sessions():
    sessions = ["test-s3-weather", "test-s3-dubai", "test-s3-unknown", "test-s3-news", "test-s3-show"]
    for s in sessions:
        clear_session_trip(s)
    yield
    for s in sessions:
        clear_session_trip(s)


def test_weather_query_does_not_modify_route_state():
    """
    Assert that sending a weather question in an active session does NOT wipe
    origin/destination into Unknown City / UNK.
    """
    session_id = "test-s3-weather"
    save_session_trip(session_id, {
        "origin": "PNQ",
        "origin_name": "Pune",
        "destination": "DEL",
        "destination_name": "Delhi",
        "departure_date": "2026-09-10",
        "check_in": "2026-09-10",
        "check_out": "2026-09-12",
        "target_currency": "INR",
        "display_currency": "INR",
        "confirmed_plan": {
            "origin": "PNQ",
            "origin_name": "Pune",
            "destination": "DEL",
            "destination_name": "Delhi",
            "departure_date": "2026-09-10",
            "check_in": "2026-09-10",
            "check_out": "2026-09-12",
            "target_currency": "INR",
            "display_currency": "INR",
            "transport_results": [],
            "hotel_results": [],
            "local_guide_results": []
        }
    })

    # Intent classifier check
    classified = classify_intent("what's the weather going to be like?", session_context={"destination": "DEL", "origin": "PNQ"})
    assert classified.intent == IntentType.WEATHER
    assert classified.origin is None or classified.origin != "UNK"

    # End-to-end endpoint check
    resp = client.post("/chat", json={
        "message": "what's the weather going to be like?",
        "session_id": session_id,
        "user_id": "user-r3"
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "Unknown City" not in data["response"]

    # Verify session state origin/destination remain unchanged
    after_session = get_session_trip(session_id)
    assert after_session.get("origin") == "PNQ"
    assert after_session.get("destination") == "DEL"
    assert after_session.get("destination_name") == "Delhi"


def test_single_city_month_phrase_does_not_corrupt_session():
    """
    Assert that typing 'Dubai in July' in an active session does not overwrite
    origin/destination with 'Unknown City' or 'UNK'.
    """
    session_id = "test-s3-dubai"
    save_session_trip(session_id, {
        "origin": "PNQ",
        "origin_name": "Pune",
        "destination": "DEL",
        "destination_name": "Delhi",
        "confirmed_plan": {
            "origin": "PNQ",
            "destination": "DEL",
            "destination_name": "Delhi",
            "transport_results": [],
            "hotel_results": []
        }
    })

    classified = classify_intent("Dubai in July", session_context={"destination": "DEL", "origin": "PNQ"})
    assert classified.intent != IntentType.NEW_TRIP_ROUTE

    resp = client.post("/chat", json={
        "message": "Dubai in July",
        "session_id": session_id,
        "user_id": "user-r3"
    })
    assert resp.status_code == 200

    after_session = get_session_trip(session_id)
    assert after_session.get("origin") == "PNQ"
    assert after_session.get("destination") == "DEL"
    assert after_session.get("origin") != "UNK"
    assert after_session.get("destination") != "UNK"


def test_unknown_iata_never_written_to_session():
    """
    General guard: if route extraction produces 'Unknown', 'UNK', null, or invalid IATA,
    it must NOT be written to session state. Prior valid state is retained.
    """
    session_id = "test-s3-unknown"
    save_session_trip(session_id, {
        "origin": "PNQ",
        "origin_name": "Pune",
        "destination": "DEL",
        "destination_name": "Delhi",
    })

    # Attempt to overwrite with UNK / Unknown
    save_session_trip(session_id, {
        "origin": "UNK",
        "origin_name": "Unknown City",
        "destination": "Unknown",
        "destination_name": "Unknown",
    })

    state = get_session_trip(session_id)
    assert state.get("origin") == "PNQ"
    assert state.get("origin_name") == "Pune"
    assert state.get("destination") == "DEL"
    assert state.get("destination_name") == "Delhi"

    # Attempt reset with UNK
    reset_session_trip(session_id, origin="UNK", destination=None)
    state2 = get_session_trip(session_id)
    assert state2.get("origin") == "PNQ"
    assert state2.get("destination") == "DEL"


def test_news_query_degrades_gracefully_without_tavily_key():
    """
    With TAVILY_API_KEY unset or invalid, a news query degrades gracefully
    without throwing a 502 Gateway Error.
    """
    session_id = "test-s3-news"
    save_session_trip(session_id, {
        "origin": "PNQ",
        "destination": "DEL",
        "destination_name": "Delhi",
    })

    with patch.dict(os.environ, {"TAVILY_API_KEY": ""}):
        resp = client.post("/chat", json={
            "message": "any news I should know about before I go?",
            "session_id": session_id,
            "user_id": "user-r3"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "502" not in data["response"]
        assert len(data["response"]) > 0


def test_domestic_route_defaults_to_local_currency():
    """
    Test helper and defaults:
    PNQ→DEL defaults to INR
    LHR→MAN defaults to GBP
    HND→KIX defaults to JPY
    JFK→LAX defaults to USD
    International routes return None so memory/USD preference applies.
    """
    assert infer_default_currency_from_route("PNQ", "DEL") == "INR"
    assert infer_default_currency_from_route("LHR", "MAN") == "GBP"
    assert infer_default_currency_from_route("HND", "KIX") == "JPY"
    assert infer_default_currency_from_route("JFK", "LAX") == "USD"
    assert infer_default_currency_from_route("LHR", "JFK") is None

    # Test via intent classifier / graph: domestic route Pune to Delhi defaults to INR
    classified = classify_intent("pune to delhi")
    assert classified.origin == "PNQ"
    assert classified.destination == "DEL"


def test_confirm_typos_accepted():
    """
    Typos of confirm: conferm, confirn, comfirm, comfirn, confrim, confrm, cofirm
    Synonyms: yep, yup, yeah, ok, okay, sounds good, done, let's do it, go for it, book it
    All map to CONFIRM_PLAN.
    """
    typos = ["conferm", "confirn", "comfirm", "comfirn", "confrim", "confrm", "cofirm"]
    for typo in typos:
        res = classify_intent(typo)
        assert res.intent == IntentType.CONFIRM_PLAN, f"Failed for typo: {typo}"

    synonyms = ["yep", "yup", "yeah", "ok", "okay", "sounds good", "done", "let's do it", "go for it", "book it"]
    for syn in synonyms:
        res = classify_intent(syn)
        assert res.intent == IntentType.CONFIRM_PLAN, f"Failed for synonym: {syn}"


def test_show_itinerary_again_redisplays():
    """
    Queries like 'show me the itinerary again', 'show plan', 'what was the trip again'
    classify as SHOW_ITINERARY and re-render the current plan without nagging.
    """
    for query in ["show me the itinerary again", "show plan", "what was the trip again"]:
        res = classify_intent(query)
        assert res.intent == IntentType.SHOW_ITINERARY, f"Failed for query: {query}"

    session_id = "test-s3-show"
    sample_plan = {
        "origin": "PNQ",
        "origin_name": "Pune",
        "destination": "DEL",
        "destination_name": "Delhi",
        "departure_date": "2026-09-10",
        "check_in": "2026-09-10",
        "check_out": "2026-09-12",
        "budget_cap": 25000.0,
        "target_currency": "INR",
        "display_currency": "INR",
        "transport_results": [{"carrier": "IndiGo", "price": {"amount": 5000, "currency": "INR"}}],
        "hotel_results": [{"name": "The Lalit New Delhi", "rating": 4.5, "total_price": {"amount": 8000, "currency": "INR"}}],
        "local_guide_results": []
    }

    save_session_trip(session_id, {
        **sample_plan,
        "confirmed_plan": sample_plan
    })

    resp = client.post("/chat", json={
        "message": "show me the itinerary again",
        "session_id": session_id,
        "user_id": "user-r3"
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "Are you confirming" not in data["response"]
    assert "itinerary" in data["response"].lower()
    assert data.get("plan") is not None
    assert data["plan"]["origin"] == "PNQ"
    assert data["plan"]["destination"] == "DEL"
