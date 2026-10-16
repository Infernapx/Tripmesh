"""
tests/test_all_agents.py
------------------------
Cross-agent integration tests (added progressively by phase).

Phase 0: validates shared schemas parse correctly and Chroma initialises.
Later phases will add end-to-end A2A flow tests.
"""

from __future__ import annotations

import os
import sys
import pytest

# Add tripmesh root to path so 'shared' is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# Phase 0: schema smoke tests
# ---------------------------------------------------------------------------

class TestSchemas:
    def test_money_validates_currency_uppercase(self):
        from shared.schemas import Money
        m = Money(amount=100.0, currency="usd")
        assert m.currency == "USD"

    def test_money_rejects_negative(self):
        from shared.schemas import Money
        with pytest.raises(Exception):
            Money(amount=-1.0, currency="EUR")

    def test_location_iata_uppercase(self):
        from shared.schemas import Location
        loc = Location(name="Heathrow", iata="lhr")
        assert loc.iata == "LHR"

    def test_transport_option_arrival_must_be_after_departure(self):
        from datetime import datetime
        from shared.schemas import (
            DataQuality, Location, Money, TransportMode, TransportOption
        )
        origin = Location(name="London", iata="LHR")
        dest = Location(name="Paris", iata="CDG")
        with pytest.raises(Exception):
            TransportOption(
                mode=TransportMode.flight,
                origin=origin,
                destination=dest,
                departure_at=datetime(2025, 6, 1, 12, 0),
                arrival_at=datetime(2025, 6, 1, 11, 0),  # before departure!
                duration_minutes=60,
                price=Money(amount=150.0, currency="EUR"),
                data_quality=DataQuality.live,
            )

    def test_estimated_transport_is_flagged(self):
        from datetime import datetime
        from shared.schemas import (
            DataQuality, Location, Money, TransportMode, TransportOption
        )
        origin = Location(name="London", iata="LHR")
        dest = Location(name="Paris", iata="CDG")
        option = TransportOption(
            mode=TransportMode.flight,
            origin=origin,
            destination=dest,
            departure_at=datetime(2025, 6, 1, 10, 0),
            arrival_at=datetime(2025, 6, 1, 11, 30),
            duration_minutes=90,
            price=Money(amount=150.0, currency="EUR"),
            data_quality=DataQuality.estimated,
        )
        assert option.is_estimated is True

    def test_trip_request_return_before_departure_rejected(self):
        from datetime import date
        from shared.schemas import TripRequest
        with pytest.raises(Exception):
            TripRequest(
                destination="Lisbon",
                departure_date=date(2025, 6, 10),
                return_date=date(2025, 6, 5),  # before departure!
            )

    def test_a2a_request_default_timeout(self):
        from shared.schemas import A2ARequest
        req = A2ARequest(
            skill="convert_currency",
            session_id="test-session",
            payload={"from": "USD", "to": "EUR", "amount": 100},
        )
        assert req.timeout_seconds == 15

    def test_activity_recommendation_schema(self):
        from shared.schemas import ActivityRecommendation, Location, Money, DataQuality
        act = ActivityRecommendation(
            name="Castelo de São Jorge",
            description="Historic fortress overlooking Lisbon.",
            location=Location(name="Castelo de São Jorge, Lisbon", lat=38.7139, lon=-9.1334),
            estimated_duration_hours=2.5,
            estimated_cost=Money(amount=15.0, currency="EUR"),
            source_citation="Wikivoyage contributors, 'Lisbon', Wikivoyage, CC BY-SA 4.0, https://en.wikivoyage.org/wiki/Lisbon",
            data_quality=DataQuality.live,
            tags=["historic", "castle"]
        )
        assert act.name == "Castelo de São Jorge"
        assert act.source_citation.startswith("Wikivoyage contributors")
        assert act.data_quality == DataQuality.live


class TestChromaInit:
    def test_chroma_in_memory_starts_empty(self):
        """Phase 0 exit criterion: Chroma initialises and is queryable."""
        os.environ["CHROMA_USE_MEMORY"] = "1"
        from shared.vector_store import get_chroma_client

        client = get_chroma_client()
        collections = client.list_collections()
        assert isinstance(collections, list)
        # In a brand-new in-memory client there are no collections
        assert len(collections) == 0

        del os.environ["CHROMA_USE_MEMORY"]

    def test_local_guide_rag_grounding(self):
        """Phase 4 test: Chroma loads seed guides and retrieves grounded recommendations."""
        from local_guide_agent_rag.app.retriever import search_activities
        from shared.vector_store import get_chroma_client

        client = get_chroma_client()
        results = search_activities(destination="Lisbon", client=client)
        assert len(results) > 0
        assert all("Wikivoyage contributors" in r.source_citation for r in results)

