"""
local_guide_agent_rag/app/retriever.py
-------------------------------------
Retrieves activity and POI recommendations from the Chroma vector store
with distance thresholding and authentic Wikivoyage citations.
"""

from __future__ import annotations

import os
import logging
from typing import Optional
from shared.schemas import ActivityRecommendation, DataQuality, Location, Money
from shared.vector_store import get_chroma_client
try:
    from .ingest import ensure_ingested, COLLECTION_NAME
except ImportError:
    from app.ingest import ensure_ingested, COLLECTION_NAME

logger = logging.getLogger(__name__)

# City code mappings
CITY_TO_IATA = {
    "LISBON": "LIS",
    "LISBOA": "LIS",
    "BARCELONA": "BCN",
    "LONDON": "LON",
    "TOKYO": "TYO",
    "PARIS": "PAR"
}

# Distance threshold: distance values higher than this are considered weak matches
MAX_DISTANCE_THRESHOLD = 0.65


def search_activities(
    destination: str,
    query: str = "",
    top_k: int = 4,
    client=None
) -> list[ActivityRecommendation]:
    """
    Queries Chroma for grounded activity recommendations for the given destination.
    Returns empty list if no strong matches meet the relevance threshold.
    """
    if client is None:
        client = get_chroma_client()

    ensure_ingested(client=client)

    collection = client.get_or_create_collection(name=COLLECTION_NAME)
    if collection.count() == 0:
        logger.warning("Local Guide collection is empty.")
        return []

    dest_clean = destination.strip().upper()
    dest_code = CITY_TO_IATA.get(dest_clean, dest_clean)

    search_query = query.strip() if query.strip() else f"Best attractions, sights, and things to do in {destination}"

    # Build where filter if known destination code
    where_filter = None
    if dest_code in ["LIS", "BCN", "LON", "TYO", "PAR"]:
        where_filter = {"destination": dest_code}

    logger.info(f"Local Guide RAG: searching '{search_query}' (dest={dest_code}, filter={where_filter})")

    try:
        if where_filter:
            results = collection.query(
                query_texts=[search_query],
                n_results=top_k,
                where=where_filter
            )
        else:
            # Query across all documents and rely on distance thresholding
            results = collection.query(
                query_texts=[f"{destination} {search_query}"],
                n_results=top_k
            )
    except Exception as e:
        logger.error(f"Error querying Chroma vector store: {e}")
        return []

    metadatas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0] if results.get("distances") else []

    if not metadatas:
        logger.info(f"No vector matches found for destination '{destination}'")
        return []

    recommendations: list[ActivityRecommendation] = []

    for idx, meta in enumerate(metadatas):
        # Distance check if distances are available
        if distances and idx < len(distances):
            dist = distances[idx]
            # When where_filter is not set (unknown/generic destination), enforce strict distance threshold
            if where_filter is None and dist > MAX_DISTANCE_THRESHOLD:
                logger.info(f"Discarding weak match '{meta.get('name')}' with distance {dist:.4f} > {MAX_DISTANCE_THRESHOLD}")
                continue

        name = meta.get("name", "Local Attraction")
        desc = meta.get("description", "")
        city = meta.get("city", destination)
        citation = meta.get("source_citation")
        tags_str = meta.get("tags", "")
        tags = [t.strip() for t in tags_str.split(",") if t.strip()]

        duration = meta.get("duration")
        cost_val = meta.get("cost", 0.0)
        currency = meta.get("currency", "EUR")

        lat = meta.get("lat")
        lon = meta.get("lon")

        rec = ActivityRecommendation(
            name=name,
            description=desc,
            location=Location(
                name=f"{name}, {city}",
                lat=lat,
                lon=lon
            ),
            estimated_duration_hours=duration,
            estimated_cost=Money(amount=cost_val, currency=currency) if cost_val is not None else None,
            source_citation=citation,
            data_quality=DataQuality.live,
            tags=tags
        )
        recommendations.append(rec)

    logger.info(f"Local Guide RAG: returning {len(recommendations)} grounded recommendations for '{destination}'")
    return recommendations
