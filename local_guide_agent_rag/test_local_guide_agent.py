import os
import json
import pytest
from fastapi.testclient import TestClient
from local_guide_agent_rag.app.main import app
from local_guide_agent_rag.app.retriever import search_activities
from local_guide_agent_rag.app.ingest import ingest_seed_data
from shared.vector_store import get_chroma_client

client = TestClient(app)


def test_root():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["service"] == "local_guide_agent"


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ingest_and_query_activities():
    # Test Chroma vector store ingestion & retrieval
    chroma_client = get_chroma_client()
    count = ingest_seed_data(client=chroma_client)
    assert count >= 10

    # Query Lisbon activities
    results = search_activities(destination="Lisbon", query="historic castle and viewpoints", client=chroma_client)
    assert len(results) > 0

    # Spot check: verify result contains genuine Wikivoyage attribution
    for r in results:
        assert "Wikivoyage contributors" in r.source_citation
        assert "CC BY-SA 4.0" in r.source_citation
        assert "https://en.wikivoyage.org/wiki/" in r.source_citation
        assert r.location.name != ""
        assert len(r.tags) > 0
        assert r.data_quality.value == "live"


def test_unknown_destination_honest_no_match():
    # Test that querying an unindexed city honestly returns empty results rather than fabricating citations
    chroma_client = get_chroma_client()
    results = search_activities(destination="UnknownNowhereCity12345", query="random sightseeing", client=chroma_client)
    assert len(results) == 0


def test_a2a_local_guide_send_message_jsonrpc():
    # Test JSON-RPC SendMessage over A2A
    payload = {
        "jsonrpc": "2.0",
        "method": "SendMessage",
        "params": {
            "message": {
                "message_id": "msg-guide-test",
                "role": "ROLE_USER",
                "parts": [{"text": '{"destination": "LIS", "query": "cultural landmarks"}'}]
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
    assert parsed["no_match"] is False
    assert "Wikivoyage contributors" in parsed["results"][0]["source_citation"]
