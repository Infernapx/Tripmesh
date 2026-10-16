import pytest
from datetime import date
from fastapi.testclient import TestClient
from transport_agent.app.main import app
from transport_agent.app.adapters.mock import MockTransportAdapter
from transport_agent.app.adapters.flight_duffel import DuffelFlightAdapter
from transport_agent.app.adapters.train_db import DBTrainAdapter
from transport_agent.app.adapters.bus_gtfs import GTFSBusAdapter

client = TestClient(app)

def test_root():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["service"] == "transport_agent"

def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

def test_mock_adapter():
    adapter = MockTransportAdapter()
    assert adapter.can_serve("LHR", "CDG") is True
    
    results = adapter.search("LHR", "LIS", date(2026, 9, 10), num_travelers=1)
    assert len(results) == 2
    assert results[0].is_estimated is True
    assert results[0].data_quality.value == "estimated"
    assert results[0].price.amount > 0

def test_duffel_adapter_mock_fallback():
    adapter = DuffelFlightAdapter()
    # Without real credentials, it should return empty list or mock behavior
    results = adapter.search("LHR", "LIS", date(2026, 9, 10), num_travelers=1)
    # Check that search completes gracefully
    assert isinstance(results, list)

def test_a2a_send_message_jsonrpc():
    # Construct standard A2A JSON-RPC request for SendMessage
    payload = {
        "jsonrpc": "2.0",
        "method": "SendMessage",
        "params": {
            "message": {
                "message_id": "msg-test",
                "role": "ROLE_USER",
                "parts": [{"text": '{"origin": "LHR", "destination": "LIS", "departure_date": "2026-09-10", "num_travelers": 1}'}]
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
    # Parse returned text inside A2A envelope
    result_text = data["result"]["message"]["parts"][0]["text"]
    import json
    parsed = json.loads(result_text)
    assert parsed["success"] is True
    assert len(parsed["results"]) > 0
    assert parsed["results"][0]["is_estimated"] is True

def test_duffel_adapter_test_token_flagging():
    from unittest.mock import patch, MagicMock
    adapter = DuffelFlightAdapter()
    
    # 1. Test with sandbox token (should flag as estimated)
    adapter.access_token = "duffel_test_mock_token"
    
    mock_response = MagicMock()
    mock_response.status_code = 201
    mock_response.json.return_value = {
        "data": {
            "offers": [
                {
                    "id": "off_mock_1",
                    "total_amount": "250.00",
                    "total_currency": "USD",
                    "owner": {
                        "name": "Duffel Airways",
                        "iata_code": "ZZ"
                    },
                    "slices": [
                        {
                            "id": "sli_mock_1",
                            "segments": [
                                {
                                    "id": "seg_mock_1",
                                    "departing_at": "2026-09-24T10:00:00",
                                    "arriving_at": "2026-09-24T12:00:00",
                                    "marketing_carrier": {
                                        "name": "Duffel Airways",
                                        "iata_code": "ZZ"
                                    }
                                }
                            ]
                        }
                    ]
                }
            ]
        }
    }

    with patch("httpx.AsyncClient.post", return_value=mock_response) as mock_post:
        results = adapter.search("LHR", "JFK", date(2026, 9, 24), num_travelers=1)
        mock_post.assert_called_once()
        assert len(results) == 1
        assert results[0].is_estimated is True
        assert results[0].data_quality.value == "estimated"
        assert results[0].carrier == "Duffel Airways"

    # 2. Test with live token (should flag as live)
    adapter.access_token = "duffel_live_mock_token"
    with patch("httpx.AsyncClient.post", return_value=mock_response):
        results = adapter.search("LHR", "JFK", date(2026, 9, 24), num_travelers=1)
        assert len(results) == 1
        assert results[0].is_estimated is False
        assert results[0].data_quality.value == "live"

def test_db_train_adapter():
    adapter = DBTrainAdapter()
    assert adapter.can_serve("FRA", "MUC") is True
    assert adapter.can_serve("LHR", "JFK") is False
    
    # Missing credentials fallback
    adapter.api_key = None
    results = adapter.search("FRA", "MUC", date(2026, 9, 24), num_travelers=1)
    assert len(results) == 2
    assert results[0].mode.value == "train"
    assert results[0].is_estimated is True
    assert results[0].data_quality.value == "estimated"

def test_gtfs_bus_adapter():
    adapter = GTFSBusAdapter()
    
    # Check coverage mapping
    assert adapter.can_serve("LHR", "LGW") is True
    assert adapter.can_serve("LHR", "JFK") is False # JFK not in GTFS Stops table
    
    # Query LHR -> LGW on valid date in S1 service range
    results = adapter.search("LHR", "LGW", date(2026, 9, 24), num_travelers=1)
    
    assert len(results) == 2
    assert results[0].mode.value == "bus"
    assert results[0].carrier == "Sample Test Bus Co"
    # Ensure it's correctly flagged as estimated since it's a dev fixture
    assert results[0].is_estimated is True
    assert results[0].data_quality.value == "estimated"
    assert results[0].origin.iata == "LHR"
    assert results[0].destination.iata == "LGW"

def test_a2a_multi_modal_lhr_lgw():
    # Construct standard A2A JSON-RPC request for LHR to LGW
    payload = {
        "jsonrpc": "2.0",
        "method": "SendMessage",
        "params": {
            "message": {
                "message_id": "msg-multimodal",
                "role": "ROLE_USER",
                "parts": [{"text": '{"origin": "LHR", "destination": "LGW", "departure_date": "2026-09-24", "num_travelers": 1}'}]
            }
        },
        "id": 2
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
    import json
    parsed = json.loads(result_text)
    assert parsed["success"] is True
    
    # We should have flights, trains, and buses in the output
    results = parsed["results"]
    assert len(results) > 0
    
    modes = {r["mode"] for r in results}
    assert "flight" in modes
    assert "train" in modes
    assert "bus" in modes

def test_mock_adapter_price_ceiling_and_sorting():
    adapter = MockTransportAdapter()
    # Unconstrained
    results_all = adapter.search("LHR", "LIS", date(2026, 9, 10), num_travelers=1)
    assert len(results_all) == 2
    # Ascending sort check
    assert results_all[0].price.amount <= results_all[1].price.amount
    assert results_all[0].price.amount == 85.0
    assert results_all[1].price.amount == 120.0

    # Constrained to 100.0
    results_filtered = adapter.search("LHR", "LIS", date(2026, 9, 10), num_travelers=1, max_price=100.0)
    assert len(results_filtered) == 1
    assert results_filtered[0].price.amount == 85.0

def test_a2a_send_message_with_price_ceiling():
    payload = {
        "jsonrpc": "2.0",
        "method": "SendMessage",
        "params": {
            "message": {
                "message_id": "msg-price-ceil",
                "role": "ROLE_USER",
                "parts": [{"text": '{"origin": "LHR", "destination": "LIS", "departure_date": "2026-09-10", "price_ceiling": 100.0}'}]
            }
        },
        "id": 3
    }
    response = client.post("/", json=payload, headers={"A2A-Version": "1.0"})
    assert response.status_code == 200
    data = response.json()
    result_text = data["result"]["message"]["parts"][0]["text"]
    import json
    parsed = json.loads(result_text)
    assert parsed["success"] is True
    for item in parsed["results"]:
        assert item["price"]["amount"] <= 100.0

def test_db_adapter_scoped_to_plausible_routes():
    adapter = DBTrainAdapter()
    # European routes supported by DB network
    assert adapter.can_serve("BER", "MUC") is True
    assert adapter.can_serve("LHR", "CDG") is True
    assert adapter.can_serve("FRA", "AMS") is True

    # Non-European routes must be rejected
    assert adapter.can_serve("DEL", "BOM") is False
    assert adapter.can_serve("JFK", "LAX") is False
    assert adapter.can_serve("HND", "NRT") is False
    assert adapter.can_serve("CAI", "NBO") is False
