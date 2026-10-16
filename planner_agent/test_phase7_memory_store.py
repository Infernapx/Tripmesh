import os
import json
import sqlite3
import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient

from app.main import app
from app.session_store import clear_session_trip
from shared.memory_store import (
    get_user_memory,
    update_user_memory,
    record_confirmed_trip,
    infer_hotel_tier,
    get_db_connection,
    init_db
)

client = TestClient(app)


@pytest.fixture
def temp_db_path(tmp_path):
    """Provides an isolated SQLite database path for testing."""
    db_file = str(tmp_path / "test_memory.db")
    os.environ["TRIPMESH_DB_PATH"] = db_file
    yield db_file
    if "TRIPMESH_DB_PATH" in os.environ:
        del os.environ["TRIPMESH_DB_PATH"]


def test_memory_persists_across_process_restart(temp_db_path):
    """
    Write a preference and trip, close the SQLite connection (simulating a restart),
    re-open and confirm data is still intact.
    """
    user_id = "user-restart-test"
    
    # 1. First process / connection: record preference and trip
    update_user_memory(user_id, currency="INR", hotel_tier="luxury", frequent_origin="Delhi", db_path=temp_db_path)
    record_confirmed_trip(
        user_id,
        {
            "origin": "DEL",
            "origin_name": "Delhi",
            "destination": "BOM",
            "destination_name": "Mumbai",
            "display_currency": "INR",
            "budget_cap": 25000.0,
            "hotel_results": [{"rating": 4.8, "price_per_night": {"amount": 280.0, "currency": "USD"}}]
        },
        db_path=temp_db_path
    )

    # 2. Simulate complete restart by opening a brand new direct connection
    conn = sqlite3.connect(temp_db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM user_preferences WHERE user_id = ?", (user_id,))
    pref_row = cursor.fetchone()
    assert pref_row is not None
    assert pref_row["currency"] == "INR"
    assert pref_row["hotel_tier"] == "luxury"
    assert pref_row["frequent_origin"] == "Delhi"

    cursor.execute("SELECT * FROM past_trips WHERE user_id = ?", (user_id,))
    trip_rows = cursor.fetchall()
    assert len(trip_rows) == 1
    assert trip_rows[0]["destination"] == "BOM"
    assert trip_rows[0]["currency"] == "INR"
    conn.close()

    # 3. Test through get_user_memory API
    mem = get_user_memory(user_id, db_path=temp_db_path)
    assert mem is not None
    assert mem.currency == "INR"
    assert mem.frequent_origin == "Delhi"
    assert len(mem.past_trips) == 1


def test_new_user_no_memory_uses_system_default(temp_db_path):
    """A user_id never seen before gets the standard default (USD) with no crash."""
    user_id = "brand-new-user-123"
    session_id = "fresh-session-xyz"
    clear_session_trip(session_id)

    # Missing user memory returns None cleanly
    mem = get_user_memory(user_id, db_path=temp_db_path)
    assert mem is None

    # Chat request for new user without explicit currency defaults to USD
    res = client.post(
        "/chat",
        json={"message": "I want to go somewhere", "session_id": session_id, "user_id": user_id}
    )
    assert res.status_code == 200
    msg = res.json()["response"]
    assert "USD" in msg
    assert "Where would you like to travel?" in msg


def test_missing_user_id_fails_validation():
    """user_id must be required, returning 422 Unprocessable Entity if omitted."""
    res = client.post(
        "/chat",
        json={"message": "hello", "session_id": "test-session"}
    )
    assert res.status_code == 422


@pytest.mark.anyio
async def test_returning_user_currency_default_applied(temp_db_path):
    """
    1. Session 1: User 'user-alpha' plans and confirms a trip in INR.
    2. Session 2: User 'user-alpha' in a new session (different session_id) asks to plan a trip with no currency stated.
       Assert the new plan defaults to INR and includes the visible personalization note.
    """
    user_id = "user-alpha"
    session_1 = "sess-turn-1"
    session_2 = "sess-turn-2"
    clear_session_trip(session_1)
    clear_session_trip(session_2)

    with patch("app.graph.call_transport_agent_a2a", new_callable=AsyncMock) as mock_t, \
         patch("app.graph.call_hotel_agent_a2a", new_callable=AsyncMock) as mock_h, \
         patch("app.graph.call_local_guide_agent_a2a", new_callable=AsyncMock) as mock_g, \
         patch("app.graph.call_single_currency_rate_a2a", new_callable=AsyncMock) as mock_rate:

        mock_t.return_value = json.dumps({
            "success": True,
            "results": [{
                "carrier": "Air India",
                "origin": {"iata": "DEL"},
                "destination": {"iata": "BOM"},
                "departure_at": "2026-09-10T08:00:00",
                "arrival_at": "2026-09-10T10:00:00",
                "duration_minutes": 120,
                "price": {"amount": 50.0, "currency": "EUR"},
                "is_estimated": False
            }]
        })
        mock_h.return_value = json.dumps({
            "success": True,
            "results": [{
                "name": "Taj Mahal Tower",
                "rating": 4.7,
                "check_in": "2026-09-10",
                "check_out": "2026-09-12",
                "price_per_night": {"amount": 260.0, "currency": "USD"},
                "total_price": {"amount": 520.0, "currency": "USD"},
                "amenities": ["Spa", "Pool"],
                "is_estimated": False
            }]
        })
        mock_g.return_value = json.dumps({"success": True, "results": []})
        mock_rate.return_value = 90.0

        # --- Session 1: Request in INR and confirm ---
        r1 = client.post(
            "/chat",
            json={"message": "delhi to mumbai in INR", "session_id": session_1, "user_id": user_id}
        )
        assert r1.status_code == 200
        assert "Trip Plan" in r1.json()["response"]

        # Confirm the plan in Session 1
        r_confirm = client.post(
            "/chat",
            json={"message": "confirm", "session_id": session_1, "user_id": user_id}
        )
        assert r_confirm.status_code == 200
        assert "confirmed" in r_confirm.json()["response"].lower()

        # Check memory was updated
        mem = get_user_memory(user_id, db_path=temp_db_path)
        assert mem is not None
        assert mem.currency == "INR"
        assert mem.hotel_tier == "luxury"

        # --- Session 2: New session, same user_id, no currency mentioned ---
        r2 = client.post(
            "/chat",
            json={"message": "plan a trip from London to Lisbon", "session_id": session_2, "user_id": user_id}
        )
        assert r2.status_code == 200
        msg2 = r2.json()["response"]

        # Assert INR was applied from long-term memory
        assert "INR" in msg2
        # Assert visible personalization notice is present
        assert "Since you usually prefer prices in INR, I've shown this plan in INR" in msg2


def test_past_trips_recorded_on_confirmation(temp_db_path):
    """Confirming a plan writes a row to past_trips correctly linked to user_id."""
    user_id = "user-beta"
    trip_data = {
        "origin": "LHR",
        "origin_name": "London",
        "destination": "NRT",
        "destination_name": "Tokyo",
        "departure_date": "2026-10-01",
        "check_in": "2026-10-01",
        "check_out": "2026-10-05",
        "display_currency": "JPY",
        "budget_cap": 200000.0,
        "hotel_results": [{"rating": 4.0, "price_per_night": {"amount": 150.0, "currency": "USD"}}]
    }

    record_confirmed_trip(user_id, trip_data, db_path=temp_db_path)
    mem = get_user_memory(user_id, db_path=temp_db_path)
    assert mem is not None
    assert len(mem.past_trips) == 1
    t = mem.past_trips[0]
    assert t.user_id == user_id
    assert t.destination == "NRT"
    assert t.destination_name == "Tokyo"
    assert t.origin_name == "London"
    assert t.currency == "JPY"
    assert t.budget_cap == 200000.0


def test_frequent_origin_mode_calculation(temp_db_path):
    """
    Assert frequent_origin computes the statistical mode of past trips for the user.
    """
    user_id = "user-frequent-origin"
    # Trip 1: from London
    record_confirmed_trip(user_id, {"origin": "LHR", "origin_name": "London", "destination": "CDG"}, db_path=temp_db_path)
    # Trip 2: from Delhi
    record_confirmed_trip(user_id, {"origin": "DEL", "origin_name": "Delhi", "destination": "BOM"}, db_path=temp_db_path)
    # Trip 3: from Delhi again
    record_confirmed_trip(user_id, {"origin": "DEL", "origin_name": "Delhi", "destination": "BLR"}, db_path=temp_db_path)

    mem = get_user_memory(user_id, db_path=temp_db_path)
    assert mem is not None
    # Delhi has count 2, London has count 1 -> Mode is Delhi
    assert mem.frequent_origin == "Delhi"


def test_infer_hotel_tier():
    """Verify hotel tier inference heuristics."""
    # Luxury: high rating
    assert infer_hotel_tier([{"rating": 4.8, "price_per_night": {"amount": 200}}]) == "luxury"
    # Luxury: high price
    assert infer_hotel_tier([{"rating": 3.5, "price_per_night": {"amount": 300}}]) == "luxury"
    # Budget: low rating
    assert infer_hotel_tier([{"rating": 2.0, "price_per_night": {"amount": 100}}]) == "budget"
    # Budget: low price
    assert infer_hotel_tier([{"rating": 3.8, "price_per_night": {"amount": 50}}]) == "budget"
    # Mid: average rating & price
    assert infer_hotel_tier([{"rating": 3.9, "price_per_night": {"amount": 150}}]) == "mid"
    # Empty
    assert infer_hotel_tier([]) is None


@pytest.mark.anyio
async def test_returning_user_origin_default_applied(temp_db_path):
    """
    1. Session 1: User 'user-gamma' confirms trips originating from Delhi.
    2. Session 2: User 'user-gamma' in a fresh session asks 'travel to Tokyo' (no origin specified).
       Assert the planner defaults origin to Delhi (DEL) and visibly announces it in the plan.
    """
    user_id = "user-gamma"
    session_1 = "sess-gamma-1"
    session_2 = "sess-gamma-2"
    clear_session_trip(session_1)
    clear_session_trip(session_2)

    # Seed past confirmed trip with origin Delhi
    record_confirmed_trip(
        user_id,
        {
            "origin": "DEL",
            "origin_name": "Delhi",
            "destination": "BOM",
            "destination_name": "Mumbai",
            "display_currency": "USD"
        },
        db_path=temp_db_path
    )

    with patch("app.graph.call_transport_agent_a2a", new_callable=AsyncMock) as mock_t, \
         patch("app.graph.call_hotel_agent_a2a", new_callable=AsyncMock) as mock_h, \
         patch("app.graph.call_local_guide_agent_a2a", new_callable=AsyncMock) as mock_g, \
         patch("app.graph.call_single_currency_rate_a2a", new_callable=AsyncMock) as mock_rate:

        mock_t.return_value = json.dumps({
            "success": True,
            "results": [{
                "carrier": "Air India",
                "origin": {"iata": "DEL"},
                "destination": {"iata": "NRT"},
                "departure_at": "2026-09-10T08:00:00",
                "arrival_at": "2026-09-10T18:00:00",
                "duration_minutes": 600,
                "price": {"amount": 500.0, "currency": "USD"},
                "is_estimated": False
            }]
        })
        mock_h.return_value = json.dumps({
            "success": True,
            "results": [{
                "name": "Tokyo Grand Hotel",
                "rating": 4.5,
                "check_in": "2026-09-10",
                "check_out": "2026-09-12",
                "price_per_night": {"amount": 120.0, "currency": "USD"},
                "total_price": {"amount": 240.0, "currency": "USD"},
                "amenities": ["WiFi"],
                "is_estimated": False
            }]
        })
        mock_g.return_value = json.dumps({"success": True, "results": []})
        mock_rate.return_value = 1.0

        # Fresh session query giving only destination
        r = client.post(
            "/chat",
            json={"message": "travel to Tokyo", "session_id": session_2, "user_id": user_id}
        )
        assert r.status_code == 200
        msg = r.json()["response"]

        # Assert plan was generated starting from Delhi
        assert "Trip Plan" in msg
        assert "DEL to NRT" in msg
        assert "Tokyo Grand Hotel" in msg

        # Assert visible personalization notice is present
        assert "Since you usually travel from Delhi, I've planned this trip starting from Delhi (DEL)" in msg
