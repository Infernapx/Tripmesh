"""
shared/vector_store.py
----------------------
Thin wrapper around Chroma (embedded, no server) for Phase 0.
Provides a single get_chroma_client() factory used by the Local Guide Agent
and any other component that needs vector search.

Usage:
    from shared.vector_store import get_chroma_client
    client = get_chroma_client()
    # Returns an empty chromadb.Client, ready to create/load collections.
"""

from __future__ import annotations

import os
import logging

logger = logging.getLogger(__name__)


def get_chroma_client():
    """
    Return a persistent (or in-memory for testing) Chroma client.

    Behaviour:
    - Reads CHROMA_PERSIST_DIR from the environment (falls back to
      './local_guide_agent_rag/data/chroma_db').
    - If the environment variable CHROMA_USE_MEMORY=1 is set (e.g. in CI),
      returns a pure in-memory client with no disk writes.

    Raises:
        ImportError: if the `chromadb` package is not installed.
        RuntimeError: if Chroma fails to initialise.
    """
    try:
        import chromadb  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            "chromadb is not installed. Add it to shared/requirements.txt "
            "and run `pip install chromadb`."
        ) from exc

    use_memory = os.getenv("CHROMA_USE_MEMORY", "0").strip() == "1"

    if use_memory:
        logger.info("Chroma: using ephemeral in-memory client (CHROMA_USE_MEMORY=1)")
        return chromadb.EphemeralClient()

    persist_dir = os.getenv(
        "CHROMA_PERSIST_DIR",
        "./local_guide_agent_rag/data/chroma_db",
    )
    logger.info("Chroma: persistent client at %s", persist_dir)
    try:
        client = chromadb.PersistentClient(path=persist_dir)
        return client
    except Exception as exc:
        raise RuntimeError(
            f"Failed to initialise Chroma at '{persist_dir}': {exc}"
        ) from exc
