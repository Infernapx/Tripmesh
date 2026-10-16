import pytest
import os
import json
import hashlib
from unittest.mock import patch
import httpx
from fastapi.testclient import TestClient
from hotel_agent_crewai.app.main import app
from hotel_agent_crewai.app.tools.hotel_api import search_hotels_api, compute_hotelbeds_signature

client = TestClient(app)

def test_root():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["service"] == "hotel_agent"

def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

def test_hotelbeds_signature_computation():
    # Test isolated signature calculation with known fixed inputs
    api_key = "test_api_key_123"
    api_secret = "test_api_secret_456"
    timestamp = 1700000000
    
    sig = compute_hotelbeds_signature(api_key, api_secret, timestamp)
    expected_raw = f"{api_key}{api_secret}{timestamp}".encode("utf-8")
    expected_sig = hashlib.sha256(expected_raw).hexdigest()
    
    assert sig == expected_sig
    assert len(sig) == 64

@pytest.mark.anyio
async def test_hotelbeds_missing_credentials_fallback(monkeypatch):
    # Ensure missing credentials trigger mock fallback
    monkeypatch.delenv("HOTELBEDS_API_KEY", raising=False)
    monkeypatch.delenv("HOTELBEDS_API_SECRET", raising=False)
    monkeypatch.setattr("app.tools.hotel_api.HOTELBEDS_API_KEY", None)
    monkeypatch.setattr("app.tools.hotel_api.HOTELBEDS_API_SECRET", None)
    
    results = await search_hotels_api("LIS", "2026-09-10", "2026-09-12")
    assert len(results) == 2
    assert results[0].name != ""
    assert results[0].is_estimated is True
    assert results[0].booking_ref.startswith("MOCK-HT-")

@pytest.mark.anyio
async def test_hotelbeds_api_error_fallback(monkeypatch):
    # Ensure 401 or 500 API errors trigger graceful fallback to mock without raising unhandled exceptions
    monkeypatch.setenv("HOTELBEDS_API_KEY", "dummy_key")
    monkeypatch.setenv("HOTELBEDS_API_SECRET", "dummy_secret")
    monkeypatch.setattr("app.tools.hotel_api.HOTELBEDS_API_KEY", "dummy_key")
    monkeypatch.setattr("app.tools.hotel_api.HOTELBEDS_API_SECRET", "dummy_secret")
    
    # Mock httpx post to simulate a 401 unauthorized or 500 error from Hotelbeds
    async def mock_post(*args, **kwargs):
        req = httpx.Request("POST", "https://api.test.hotelbeds.com/hotel-api/1.0/hotels")
        return httpx.Response(status_code=401, text="Unauthorized: Invalid signature or key", request=req)
        
    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)
    
    # Mock geocoding to return fixed coordinates
    async def mock_geocode(*args, **kwargs):
        return (38.7223, -9.1393)
        
    monkeypatch.setattr("app.tools.hotel_api._geocode_destination", mock_geocode)
    
    results = await search_hotels_api("LIS", "2026-09-10", "2026-09-12")
    assert len(results) == 2
    assert results[0].is_estimated is True
    assert results[0].booking_ref.startswith("MOCK-HT-")

@pytest.mark.anyio
async def test_hotelbeds_sandbox_live():
    # Test real Hotelbeds sandbox availability call when credentials are configured
    key = os.getenv("HOTELBEDS_API_KEY")
    secret = os.getenv("HOTELBEDS_API_SECRET")
    if not key or not secret:
        pytest.skip("HOTELBEDS_API_KEY / HOTELBEDS_API_SECRET not set in environment. Skipping live sandbox test.")
        
    # Search for Barcelona
    results = await search_hotels_api("Barcelona", "2026-09-10", "2026-09-12")
    assert len(results) > 0
    
    # If the account returns an auth failure and falls back to mocks:
    if results[0].booking_ref.startswith("MOCK-HT-"):
        pytest.skip("Hotelbeds API returned an error (account not activated or rate limited). Skipped live assertions.")
        
    # When live sandbox responds:
    assert results[0].booking_ref.startswith("HB-HT-")
    assert results[0].price_per_night.amount > 0
    assert results[0].is_estimated is True  # Sandbox data is flagged as estimated

def test_a2a_hotel_send_message_jsonrpc():
    payload = {
        "jsonrpc": "2.0",
        "method": "SendMessage",
        "params": {
            "message": {
                "message_id": "msg-hotel-test",
                "role": "ROLE_USER",
                "parts": [{"text": '{"destination": "LIS", "check_in": "2026-09-10", "check_out": "2026-09-12"}'}]
            }
        },
        "id": 1
    }
    
    response = client.post(
        "/",
        json=payload,
        headers={"A2A-Version": "1.0"}
    )
    assert response.status_code == 200
    data = response.json()
    assert "result" in data
    
    result_text = data["result"]["message"]["parts"][0]["text"]
    parsed = json.loads(result_text)
    assert parsed["success"] is True
    assert len(parsed["results"]) > 0
    assert parsed["results"][0]["is_estimated"] is True

@pytest.mark.anyio
async def test_hotel_price_ceiling_and_sorting():
    from hotel_agent_crewai.app.tools.hotel_api import _get_mock_hotels
    # Unconstrained mock hotels (2 nights: opt1 is 360 EUR, opt2 is 190 EUR)
    results_all = _get_mock_hotels("LIS", "2026-09-10", "2026-09-12")
    assert len(results_all) == 2
    # Ascending sort check
    assert results_all[0].total_price.amount <= results_all[1].total_price.amount
    assert results_all[0].total_price.amount == 190.0
    assert results_all[1].total_price.amount == 360.0

    # Constrained to 200 EUR
    results_filtered = _get_mock_hotels("LIS", "2026-09-10", "2026-09-12", price_ceiling=200.0)
    assert len(results_filtered) == 1
    assert results_filtered[0].total_price.amount == 190.0

def test_a2a_hotel_with_price_ceiling():
    payload = {
        "jsonrpc": "2.0",
        "method": "SendMessage",
        "params": {
            "message": {
                "message_id": "msg-hotel-ceil",
                "role": "ROLE_USER",
                "parts": [{"text": '{"destination": "LIS", "check_in": "2026-09-10", "check_out": "2026-09-12", "price_ceiling": 200.0}'}]
            }
        },
        "id": 2
    }
    response = client.post("/", json=payload, headers={"A2A-Version": "1.0"})
    assert response.status_code == 200
    data = response.json()
    result_text = data["result"]["message"]["parts"][0]["text"]
    parsed = json.loads(result_text)
    assert parsed["success"] is True
    for item in parsed["results"]:
        assert item["total_price"]["amount"] <= 200.0
