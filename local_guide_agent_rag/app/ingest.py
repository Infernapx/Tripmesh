"""
local_guide_agent_rag/app/ingest.py
----------------------------------
Loads travel guide documents from seed_guides.json and embeds them into Chroma.
"""

from __future__ import annotations

import os
import json
import logging
from pathlib import Path
from shared.vector_store import get_chroma_client

logger = logging.getLogger(__name__)

COLLECTION_NAME = os.getenv("CHROMA_COLLECTION_NAME", "tripmesh_local_guide")
SEED_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "seed_guides.json"


def load_seed_data() -> list[dict]:
    if not SEED_DATA_PATH.exists():
        logger.warning(f"Seed data file not found at {SEED_DATA_PATH}")
        return []
    with open(SEED_DATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def ingest_seed_data(client=None) -> int:
    """
    Ingests all POI items from seed_guides.json into the Chroma collection.
    Returns the number of documents upserted.
    """
    if client is None:
        client = get_chroma_client()

    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"description": "TripMesh Local Guide RAG knowledge base"}
    )

    items = load_seed_data()
    if not items:
        logger.warning("No items to ingest into Chroma.")
        return 0

    ids = []
    documents = []
    metadatas = []

    for item in items:
        doc_id = item["id"]
        doc_text = (
            f"City: {item['city']} ({item['destination']})\n"
            f"Activity: {item['name']}\n"
            f"Description: {item['description']}\n"
            f"Category Tags: {', '.join(item['tags'])}\n"
            f"Estimated Duration: {item['estimated_duration_hours']} hours\n"
            f"Estimated Cost: {item['estimated_cost_amount']} {item['estimated_cost_currency']}"
        )
        metadata = {
            "id": item["id"],
            "name": item["name"],
            "destination": item["destination"].upper(),
            "city": item["city"],
            "description": item["description"],
            "tags": ", ".join(item["tags"]),
            "duration": float(item["estimated_duration_hours"]),
            "cost": float(item["estimated_cost_amount"]),
            "currency": item["estimated_cost_currency"],
            "lat": float(item["lat"]),
            "lon": float(item["lon"]),
            "source_citation": item["source_citation"]
        }

        ids.append(doc_id)
        documents.append(doc_text)
        metadatas.append(metadata)

    collection.upsert(
        ids=ids,
        documents=documents,
        metadatas=metadatas
    )

    logger.info(f"Ingested {len(ids)} Wikivoyage guide entries into collection '{COLLECTION_NAME}'")
    return len(ids)


def ensure_ingested(client=None) -> int:
    """
    Checks if collection is empty, and automatically triggers ingestion if needed.
    """
    if client is None:
        client = get_chroma_client()

    collection = client.get_or_create_collection(name=COLLECTION_NAME)
    count = collection.count()
    if count == 0:
        logger.info(f"Collection '{COLLECTION_NAME}' is empty. Running initial ingestion...")
        return ingest_seed_data(client=client)
    logger.info(f"Collection '{COLLECTION_NAME}' already contains {count} items.")
    return count


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    count = ingest_seed_data()
    print(f"Ingested {count} documents.")
