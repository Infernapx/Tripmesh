import os
import json
import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient

from app.main import app
from app.session_store import clear_session_trip, save_session_trip
from shared.observability import (
    trace_agent_call,
    get_session_traces,
    get_session_invoked_nodes,
    clear_session_traces,
    start_session_turn
)

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_observability_sessions():
    sessions = ["test-p9-full", "test-p9-endpoint", "test-p9-offline", "test-p9-honest", "test-p9-ceiling"]
    for sid in sessions:
        clear_session_trip(sid)
        clear_session_traces(sid)
    yield
    for sid in sessions:
        clear_session_trip(sid)
        clear_session_traces(sid)


# ---------------------------------------------------------------------------
# Test 1: Unification check — trace records agent calls & matches invoked_nodes
# ---------------------------------------------------------------------------
def test_trace_records_agent_calls():
    """
    Assert a full trip-planning request produces trace entries for every agent actually invoked,
    matching what invoked_nodes shows (unification check).
    """
    session_id = "test-p9-full"
    clear_session_traces(session_id)

    mock_transport = json.dumps({
        "results": [
            {
                "carrier": "Eurostar",
                "origin": {"iata": "LON", "name": "London"},
                "destination": {"iata": "PAR", "name": "Paris"},
                "departure_at": "2026-09-10T09:00:00",
                "arrival_at": "2026-09-10T11:20:00",
                "duration_minutes": 140,
                "price": {"amount": 120.0, "currency": "USD"},
                "is_estimated": False,
                "data_quality": "live"
            }
        ]
    })

    mock_hotel = json.dumps({
        "results": [
            {
                "name": "Le Marais Boutique Hotel",
                "rating": 4.6,
                "check_in": "2026-09-10",
                "check_out": "2026-09-12",
                "price_per_night": {"amount": 150.0, "currency": "USD"},
                "total_price": {"amount": 300.0, "currency": "USD"},
                "amenities": ["Wifi", "Breakfast"],
                "is_estimated": False
            }
        ]
    })

    mock_guide = json.dumps({
        "results": [
            {
                "name": "Louvre Museum",
                "estimated_duration_hours": 3.0,
                "estimated_cost": {"amount": 22.0, "currency": "USD"},
                "description": "World-famous art museum.",
                "source_citation": "Wikivoyage",
                "location": {"name": "Paris"},
                "tags": ["Art", "Museum"]
            }
        ]
    })

    mock_recon = json.dumps({
        "total_cost": 442.0,
        "currency": "USD",
        "budget_cap": 600.0,
        "over_budget": False,
        "overage_amount": 0.0,
        "breakdown_by_agent": {
            "transport": 120.0,
            "hotel": 300.0,
            "local_guide": 22.0
        }
    })

    with patch("app.graph.call_transport_agent_a2a", new=AsyncMock(return_value=mock_transport)), \
         patch("app.graph.call_hotel_agent_a2a", new=AsyncMock(return_value=mock_hotel)), \
         patch("app.graph.call_local_guide_agent_a2a", new=AsyncMock(return_value=mock_guide)), \
         patch("app.graph.call_budget_reconciliation_a2a", new=AsyncMock(return_value=mock_recon)):

        resp = client.post(
            "/chat",
            json={
                "message": "Plan a trip from London to Paris with $600 budget",
                "session_id": session_id,
                "user_id": "u-p9-user"
            }
        )
        assert resp.status_code == 200

        # Retrieve traces
        traces = get_session_traces(session_id)
        assert len(traces) >= 5, f"Expected at least 5 traces, got {len(traces)}: {traces}"

        agents_invoked = [t["agent"] for t in traces]
        skills_invoked = [t["skill"] for t in traces]

        # Verify all key agents were traced
        assert "planner_agent" in agents_invoked
        assert "transport_agent" in agents_invoked
        assert "hotel_agent" in agents_invoked
        assert "local_guide_agent" in agents_invoked
        assert "budget_agent" in agents_invoked

        # Verify skills
        assert "parse_intent" in skills_invoked
        assert "call_transport_agent" in skills_invoked
        assert "call_hotel_agent" in skills_invoked
        assert "call_local_guide_agent" in skills_invoked
        assert "aggregate_results" in skills_invoked
        assert "check_budget" in skills_invoked
        assert "render_final_plan" in skills_invoked

        # Unification check: get_session_invoked_nodes matches the skills from the trace store
        derived_nodes = get_session_invoked_nodes(session_id)
        assert "call_transport_agent" in derived_nodes
        assert "call_hotel_agent" in derived_nodes
        assert "check_budget" in derived_nodes
        assert "render_final_plan" in derived_nodes
        assert derived_nodes == [t["skill"] for t in traces]


# ---------------------------------------------------------------------------
# Test 2: Endpoint contract test — GET /api/trip/{session_id}/traces
# ---------------------------------------------------------------------------
def test_trace_endpoint_returns_chronological_events():
    """
    Basic endpoint contract test: GET /api/trip/{session_id}/traces returns
    properly structured, chronological trace records.
    """
    session_id = "test-p9-endpoint"
    clear_session_traces(session_id)

    # Manually record two traces to test endpoint contract
    with trace_agent_call("planner_agent", "parse_intent", session_id) as span1:
        span1.set_decision("Classified planning intent")

    with trace_agent_call("transport_agent", "call_transport_agent", session_id) as span2:
        span2.set_decision("Searched flights")

    resp = client.get(f"/api/trip/{session_id}/traces")
    assert resp.status_code == 200
    data = resp.json()

    assert "session_id" in data
    assert data["session_id"] == session_id
    assert "traces" in data
    traces = data["traces"]
    assert len(traces) == 2

    first_trace = traces[0]
    assert first_trace["agent"] == "planner_agent"
    assert first_trace["skill"] == "parse_intent"
    assert first_trace["status"] == "success"
    assert first_trace["decision_reason"] == "Classified planning intent"
    assert "start_time" in first_trace
    assert "end_time" in first_trace
    assert "duration_ms" in first_trace
    assert first_trace["duration_ms"] >= 0.0

    second_trace = traces[1]
    assert second_trace["agent"] == "transport_agent"
    assert second_trace["skill"] == "call_transport_agent"
    assert second_trace["decision_reason"] == "Searched flights"

    # Chronological order check
    assert first_trace["start_time"] <= second_trace["start_time"]


# ---------------------------------------------------------------------------
# Test 3: Zero-config offline mode — works with no Langfuse credentials
# ---------------------------------------------------------------------------
def test_trace_graceful_without_langfuse():
    """
    Confirm zero-config offline mode operates with zero exceptions and records
    full traces when no Langfuse credentials are set.
    """
    session_id = "test-p9-offline"
    clear_session_traces(session_id)

    # Ensure Langfuse environment variables are unset
    old_pub = os.environ.pop("LANGFUSE_PUBLIC_KEY", None)
    old_sec = os.environ.pop("LANGFUSE_SECRET_KEY", None)

    try:
        with trace_agent_call("hotel_agent", "search_hotels", session_id) as span:
            span.set_decision("Offline mode verified successfully")
            span.update(sample_key="sample_val")

        traces = get_session_traces(session_id)
        assert len(traces) == 1
        t = traces[0]
        assert t["agent"] == "hotel_agent"
        assert t["skill"] == "search_hotels"
        assert t["status"] == "success"
        assert t["decision_reason"] == "Offline mode verified successfully"
        assert t["duration_ms"] is not None
        assert t["metadata"].get("sample_key") == "sample_val"
    finally:
        if old_pub:
            os.environ["LANGFUSE_PUBLIC_KEY"] = old_pub
        if old_sec:
            os.environ["LANGFUSE_SECRET_KEY"] = old_sec


# ---------------------------------------------------------------------------
# Test 4: Honest decision reasons captured in trace
# ---------------------------------------------------------------------------
def test_trace_captures_honest_decision_reasons():
    """
    Assert that the trace actually records honest plain-language decision reasons
    for Phase 8's honest-no-change paths (e.g. 'no cheaper hotel found', 'flight delay: no later options available').
    """
    session_id = "test-p9-honest"
    clear_session_traces(session_id)

    # Seed an existing session plan
    prior_hotel = [
        {"name": "Lisbon Standard Hotel", "total_price": {"amount": 250.0, "currency": "USD"}}
    ]
    prior_transport = [
        {"carrier": "TAP", "departure_at": "2026-09-10T10:00", "arrival_at": "2026-09-10T13:00", "price": {"amount": 150.0, "currency": "USD"}}
    ]
    save_session_trip(session_id, {
        "origin": "LHR",
        "origin_name": "London",
        "destination": "LIS",
        "destination_name": "Lisbon",
        "departure_date": "2026-09-10",
        "check_in": "2026-09-10",
        "check_out": "2026-09-12",
        "budget_cap": 500.0,
        "target_currency": "USD",
        "display_currency": "USD",
        "cached_hotel_results": prior_hotel,
        "cached_transport_results": prior_transport,
        "confirmed_plan": {"hotel_results": prior_hotel, "transport_results": prior_transport}
    })

    # Scenario A: Cheaper hotel requested, but Hotel Agent reports no cheaper options available
    mock_hotel_no_cheaper = json.dumps({
        "results": prior_hotel,
        "no_cheaper_options": True
    })
    mock_recon = json.dumps({
        "total_cost": 400.0,
        "currency": "USD",
        "budget_cap": 500.0,
        "over_budget": False,
        "overage_amount": 0.0,
        "breakdown_by_agent": {"hotel": 250.0, "transport": 150.0}
    })

    with patch("app.graph.call_hotel_agent_a2a", new=AsyncMock(return_value=mock_hotel_no_cheaper)), \
         patch("app.graph.call_budget_reconciliation_a2a", new=AsyncMock(return_value=mock_recon)):

        resp = client.post(
            "/chat",
            json={
                "message": "actually the cheaper hotel",
                "session_id": session_id,
                "user_id": "u-p9-user"
            }
        )
        assert resp.status_code == 200

        traces = get_session_traces(session_id)
        # Search for hotel trace or aggregate trace
        reasons = [t["decision_reason"] for t in traces if t.get("decision_reason")]
        full_reason_text = " ".join(reasons).lower()
        assert "no cheaper hotel found" in full_reason_text, f"Expected 'no cheaper hotel found' in reasons: {reasons}"

        # Verify status is flagged appropriately
        hotel_trace = next((t for t in traces if t["skill"] == "call_hotel_agent"), None)
        assert hotel_trace is not None
        assert hotel_trace["status"] == "no_change"

    # Scenario B: Flight delay requested, but Transport Agent reports no later options available
    clear_session_traces(session_id)
    mock_transport_no_later = json.dumps({
        "results": prior_transport,
        "no_later_options": True
    })

    with patch("app.graph.call_transport_agent_a2a", new=AsyncMock(return_value=mock_transport_no_later)), \
         patch("app.graph.call_budget_reconciliation_a2a", new=AsyncMock(return_value=mock_recon)):

        resp = client.post(
            "/chat",
            json={
                "message": "my flight got delayed 3 hours",
                "session_id": session_id,
                "user_id": "u-p9-user"
            }
        )
        assert resp.status_code == 200

        traces = get_session_traces(session_id)
        reasons = [t["decision_reason"] for t in traces if t.get("decision_reason")]
        full_reason_text = " ".join(reasons).lower()
        assert "no later options available" in full_reason_text, f"Expected 'no later options available' in reasons: {reasons}"

        transport_trace = next((t for t in traces if t["skill"] == "call_transport_agent"), None)
        assert transport_trace is not None
        assert transport_trace["status"] == "no_change"


# ---------------------------------------------------------------------------
# Test 5: Negotiation price ceiling concrete detail in trace
# ---------------------------------------------------------------------------
def test_trace_captures_concrete_price_ceiling_detail():
    """
    Confirm request_cheaper_alternatives trace records the actual calculated price ceiling
    (concrete numbers) rather than a generic 'negotiating' label.
    """
    session_id = "test-p9-ceiling"
    clear_session_traces(session_id)

    # Initial trip over budget by 100 EUR, hotel is dominant ($400)
    mock_transport = json.dumps({"results": [{"carrier": "BA", "price": {"amount": 100.0, "currency": "USD"}}]})
    mock_hotel_first = json.dumps({"results": [{"name": "Grand Hotel", "total_price": {"amount": 400.0, "currency": "USD"}}]})
    mock_hotel_second = json.dumps({"results": [{"name": "Grand Hotel Cheaper Room", "total_price": {"amount": 250.0, "currency": "USD"}}]})
    mock_guide = json.dumps({"results": []})

    # Recon 1: over budget
    mock_recon_over = json.dumps({
        "total_cost": 500.0,
        "currency": "USD",
        "budget_cap": 400.0,
        "over_budget": True,
        "overage_amount": 100.0,
        "breakdown_by_agent": {"hotel": 400.0, "transport": 100.0}
    })
    # Recon 2: reconciled
    mock_recon_ok = json.dumps({
        "total_cost": 350.0,
        "currency": "USD",
        "budget_cap": 400.0,
        "over_budget": False,
        "overage_amount": 0.0,
        "breakdown_by_agent": {"hotel": 250.0, "transport": 100.0}
    })

    hotel_call_count = 0
    async def mock_hotel_call(*args, **kwargs):
        nonlocal hotel_call_count
        hotel_call_count += 1
        if hotel_call_count == 1:
            return mock_hotel_first
        return mock_hotel_second

    recon_call_count = 0
    async def mock_recon_call(*args, **kwargs):
        nonlocal recon_call_count
        recon_call_count += 1
        if recon_call_count == 1:
            return mock_recon_over
        return mock_recon_ok

    with patch("app.graph.call_transport_agent_a2a", new=AsyncMock(return_value=mock_transport)), \
         patch("app.graph.call_hotel_agent_a2a", side_effect=mock_hotel_call), \
         patch("app.graph.call_local_guide_agent_a2a", new=AsyncMock(return_value=mock_guide)), \
         patch("app.graph.call_budget_reconciliation_a2a", side_effect=mock_recon_call):

        resp = client.post(
            "/chat",
            json={
                "message": "Plan a trip from London to Paris with $400 budget",
                "session_id": session_id,
                "user_id": "u-p9-user"
            }
        )
        assert resp.status_code == 200

        traces = get_session_traces(session_id)
        nego_trace = next((t for t in traces if "request_cheaper_alternatives" in t["skill"]), None)
        assert nego_trace is not None, f"Expected negotiation trace, found traces: {[t['skill'] for t in traces]}"
        
        reason = nego_trace["decision_reason"]
        assert reason is not None
        # Check that concrete price ceiling and overage numbers are recorded
        assert "price ceiling" in reason.lower()
        assert "400.00" in reason or "290.00" in reason or "reduced by overage" in reason
