import pytest
import json
from datetime import date
from unittest.mock import patch
from fastapi.testclient import TestClient
from budget_agent.app.main import app, get_exchange_rate, reconcile_budget_items, _exchange_rate_cache
from shared.schemas import CostLineItem, DataQuality

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_convert_currency_a2a():
    payload = {
        "jsonrpc": "2.0",
        "method": "SendMessage",
        "params": {
            "message": {
                "message_id": "msg-123",
                "role": "ROLE_USER",
                "parts": [{"text": "convert 500 USD to EUR"}]
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
    assert "500.00 USD is equivalent to" in result_text
    assert "EUR" in result_text


@pytest.mark.anyio
async def test_reconcile_budget_multi_currency():
    items = [
        CostLineItem(source_agent="transport", amount=120.0, currency="EUR", data_quality=DataQuality.live),
        CostLineItem(source_agent="hotel", amount=190.0, currency="EUR", data_quality=DataQuality.live),
        CostLineItem(source_agent="local_guide", amount=30.0, currency="GBP", data_quality=DataQuality.live)
    ]
    # Reconcile against target currency EUR with budget cap of 400.0
    recon = await reconcile_budget_items(items, target_currency="EUR", budget_cap=400.0)
    assert recon.currency == "EUR"
    assert recon.budget_cap == 400.0
    assert recon.total_cost > 300.0
    assert recon.over_budget is False
    assert recon.overage_amount == 0.0
    assert "transport" in recon.breakdown_by_agent
    assert "hotel" in recon.breakdown_by_agent
    assert "local_guide" in recon.breakdown_by_agent


@pytest.mark.anyio
async def test_reconcile_budget_overage_detection():
    items = [
        CostLineItem(source_agent="transport", amount=250.0, currency="USD"),
        CostLineItem(source_agent="hotel", amount=350.0, currency="USD")
    ]
    # Cap of 500 USD -> total 600 USD -> over budget by 100 USD
    recon = await reconcile_budget_items(items, target_currency="USD", budget_cap=500.0)
    assert recon.total_cost == 600.0
    assert recon.over_budget is True
    assert recon.overage_amount == 100.0
    assert recon.breakdown_by_agent["transport"] == 250.0
    assert recon.breakdown_by_agent["hotel"] == 350.0


@pytest.mark.anyio
async def test_exchange_rate_cache_ttl():
    _exchange_rate_cache.clear()
    # Cache a fixed rate with today's date
    _exchange_rate_cache[("USD", "EUR")] = (0.85, date.today())
    rate = await get_exchange_rate("USD", "EUR")
    assert rate == 0.85

    # Expired date should trigger re-fetch/update
    _exchange_rate_cache[("USD", "EUR")] = (0.50, date(2020, 1, 1))
    rate_fresh = await get_exchange_rate("USD", "EUR")
    assert rate_fresh != 0.50
    assert _exchange_rate_cache[("USD", "EUR")][1] == date.today()


def test_reconcile_budget_a2a_jsonrpc():
    payload = {
        "jsonrpc": "2.0",
        "method": "SendMessage",
        "params": {
            "message": {
                "message_id": "msg-reconcile",
                "role": "ROLE_USER",
                "parts": [{
                    "text": json.dumps({
                        "skill": "reconcile_budget",
                        "items": [
                            {"source_agent": "transport", "amount": 100.0, "currency": "USD"},
                            {"source_agent": "hotel", "amount": 200.0, "currency": "USD"}
                        ],
                        "target_currency": "USD",
                        "budget_cap": 250.0
                    })
                }]
            }
        },
        "id": 2
    }
    response = client.post("/", json=payload, headers={"A2A-Version": "1.0"})
    assert response.status_code == 200
    data = response.json()
    assert "result" in data
    result_text = data["result"]["message"]["parts"][0]["text"]
    parsed = json.loads(result_text)
    assert parsed["total_cost"] == 300.0
    assert parsed["over_budget"] is True
    assert parsed["overage_amount"] == 50.0
