"""
shared/gemini_client.py
-----------------------
Async-first Google Gemini client for TripMesh.
Uses direct REST endpoint (v1beta) via httpx for zero-dependency, high-performance LLM generation.
"""

import os
import logging
from typing import Optional
import httpx
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv())

logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
DEFAULT_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.7-flash")
BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
FALLBACK_MODELS = ["gemini-3.7-flash", "gemini-3.8-flash", "gemini-3.1-flash-lite"]


def is_gemini_configured() -> bool:
    """Returns True if a valid Gemini API key is configured."""
    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    return bool(key and len(key.strip()) > 10)


async def generate_gemini_content(
    prompt: str,
    system_instruction: Optional[str] = None,
    model: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 1024,
) -> Optional[str]:
    """
    Sends an async generateContent request to the Google Gemini API.
    Returns the string text or None on failure/unconfigured.
    Includes graceful model failover if the primary model experiences a 429 quota or 503 spike.
    """
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        logger.debug("Gemini API key not set; skipping LLM generation.")
        return None

    primary_model = model or DEFAULT_MODEL
    models_to_try = [primary_model] + [m for m in FALLBACK_MODELS if m != primary_model]

    payload = {
        "contents": [
            {
                "parts": [{"text": prompt}]
            }
        ],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        }
    }

    if system_instruction:
        payload["systemInstruction"] = {
            "parts": [{"text": system_instruction}]
        }

    for model_name in models_to_try:
        url = f"{BASE_URL}/{model_name}:generateContent?key={api_key.strip()}"
        try:
            async with httpx.AsyncClient(timeout=7.0) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    candidates = data.get("candidates", [])
                    if candidates and "content" in candidates[0]:
                        parts = candidates[0]["content"].get("parts", [])
                        if parts and "text" in parts[0]:
                            return parts[0]["text"].strip()
                elif resp.status_code in (429, 503):
                    logger.info(f"Gemini model {model_name} returned {resp.status_code}; trying next fallback model.")
                    continue
                else:
                    logger.warning(f"Gemini API returned {resp.status_code} for {model_name}: {resp.text[:200]}")
                    return None
        except Exception as exc:
            logger.warning(f"Gemini API invocation error ({model_name}): {exc}")
            continue

    return None


async def generate_conversational_travel_response(
    user_message: str,
    session_context: Optional[dict] = None
) -> Optional[str]:
    """
    Generates a natural, travel-concierge conversational response using Gemini.
    """
    ctx = session_context or {}
    dest = ctx.get("destination_name") or ctx.get("destination") or "your destination"
    orig = ctx.get("origin_name") or ctx.get("origin") or "your departure point"

    system_prompt = (
        "You are TripMesh Concierge, a helpful, charming, and knowledgeable travel planning assistant. "
        "You provide concise, friendly, and practical travel advice (packing tips, weather insight, "
        "best areas to visit, cultural etiquette, etc.). Keep answers under 3-4 sentences."
    )

    user_prompt = (
        f"Context: Trip from {orig} to {dest}.\n"
        f"User question: {user_message}\n"
        "Provide a direct, helpful, concise answer."
    )

    return await generate_gemini_content(
        prompt=user_prompt,
        system_instruction=system_prompt,
        temperature=0.7,
        max_tokens=1024
    )


async def generate_local_guide_highlight(
    destination: str,
    activities: list[dict]
) -> Optional[str]:
    """
    Synthesizes retrieved RAG activities into a compelling insider summary.
    """
    if not activities:
        return None

    act_summary = "; ".join(
        [f"{a.get('name')}: {a.get('description', '')}" for a in activities[:4]]
    )

    system_prompt = (
        "You are a passionate local travel guide and cultural insider. "
        "Synthesize the provided attractions into a cohesive, 2-sentence destination highlight."
    )

    prompt = (
        f"Destination: {destination}\n"
        f"Highlights: {act_summary}\n"
        "Write a 2-sentence engaging local summary highlighting the vibe of these places."
    )

    return await generate_gemini_content(
        prompt=prompt,
        system_instruction=system_prompt,
        temperature=0.6,
        max_tokens=512
    )
