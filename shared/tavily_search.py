"""
shared/tavily_search.py
-----------------------
Live web search integration using Tavily for:
1. Road conditions & transit disruptions.
2. Destination news & travel advisories (topic="news", recency filtered).
3. Local Guide web search fallback for uncovered cities.

Strict adherence to citation discipline, paraphrasing, and honest degradation.
Never fabricates ungrounded data.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime
from typing import Any, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Travel-relevant safety, disruption, and notable event keywords for news filtering
TRAVEL_RELEVANCE_KEYWORDS = [
    "advisory", "warning", "strike", "protest", "riot", "safety",
    "emergency", "closure", "closed", "disruption", "delay",
    "cancellation", "transport", "transit", "train", "flight", "airport",
    "earthquake", "storm", "hurricane", "typhoon", "cyclone", "flood",
    "wildfire", "volcano", "festival", "evacuation", "curfew", "unrest"
]


def get_tavily_client() -> Optional[Any]:
    """
    Returns an initialized TavilyClient if TAVILY_API_KEY is configured in environment,
    or None if unset.
    """
    api_key = os.getenv("TAVILY_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        from tavily import TavilyClient
        return TavilyClient(api_key=api_key)
    except Exception as exc:
        logger.warning(f"Failed to initialize TavilyClient: {exc}")
        return None


def extract_site_name(url: str) -> str:
    """Extracts a clean site name from a URL."""
    try:
        parsed = urlparse(url)
        netloc = parsed.netloc or ""
        netloc = re.sub(r'^www\.', '', netloc)
        return netloc if netloc else url
    except Exception:
        return url


# ---------------------------------------------------------------------------
# 1. Road Conditions
# ---------------------------------------------------------------------------

async def search_road_conditions(
    route: str,
    client: Optional[Any] = None
) -> dict[str, Any]:
    """
    Searches current road conditions for a route using Tavily.
    Synthesizes a concise summary citing real sources.
    Degrades honestly when key is missing or search yields no results.
    """
    tavily_client = client or get_tavily_client()
    if not tavily_client:
        return {
            "success": False,
            "summary": "I can't check live road conditions right now (live web search is not configured).",
            "sources": [],
            "error": "missing_api_key"
        }

    now_str = datetime.now().strftime("%B %Y")
    query = f"road conditions {route} {now_str}"

    results = []
    for attempt in range(2):
        try:
            resp = await asyncio.wait_for(
                asyncio.to_thread(
                    tavily_client.search,
                    query=query,
                    topic="general",
                    max_results=3
                ),
                timeout=8.0
            )
            if isinstance(resp, dict):
                results = resp.get("results", [])
                if results:
                    break
        except Exception as e:
            if attempt == 1:
                logger.warning(f"Tavily road conditions search failed after retry for '{route}': {e}")
                return {
                    "success": False,
                    "summary": f"I can't check live road conditions for {route} right now.",
                    "sources": [],
                    "error": str(e)
                }

    if not results:
        return {
            "success": True,
            "summary": f"I couldn't find current road condition info for this route ({route}).",
            "sources": []
        }

    # Synthesize concise answer with real citations
    lines = [f"🚗 **Road & Driving Conditions for {route}**:", ""]
    sources = []
    for r in results:
        title = r.get("title", "").strip()
        url = r.get("url", "").strip()
        content = r.get("content", "").strip()
        site = extract_site_name(url)

        # Clean and truncate snippet
        snippet = re.sub(r'\s+', ' ', content)[:180].strip()
        if snippet:
            if not snippet.endswith('.'):
                snippet += '...'
            lines.append(f"• **{title}**: {snippet}")
        if url:
            sources.append({"site": site, "url": url})

    if sources:
        lines.append("\n**Sources:**")
        seen_urls = set()
        for s in sources:
            if s["url"] not in seen_urls:
                seen_urls.add(s["url"])
                lines.append(f"- [{s['site']}]({s['url']})")

    return {
        "success": True,
        "summary": "\n".join(lines),
        "sources": sources
    }



# ---------------------------------------------------------------------------
# 2. Destination News & Travel Advisories
# ---------------------------------------------------------------------------

def filter_travel_relevant_news(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Filters Tavily news articles for genuine travel relevance (strikes, advisories,
    natural disasters, transport disruptions, large public events).
    Discards routine local headlines that do not affect travel.
    """
    relevant = []
    for art in articles:
        title = art.get("title", "").lower()
        content = art.get("content", "").lower()
        combined = f"{title} {content}"

        if any(kw in combined for kw in TRAVEL_RELEVANCE_KEYWORDS):
            relevant.append(art)
    return relevant


async def search_destination_news(
    destination: str,
    travel_dates: Optional[str] = None,
    client: Optional[Any] = None
) -> dict[str, Any]:
    """
    Searches travel-relevant news and advisories for a destination using Tavily news topic.
    Query: {destination} travel advisory OR strikes OR protests OR festival OR weather alert {month/year}
    Returns 3-5 items with title, date and source link, labeled as live web results.
    Degrades gracefully with timeout <= 8s and 1 retry.
    """
    tavily_client = client or get_tavily_client()
    honest_failure_msg = "I couldn't fetch live news right now. Check a local source before you travel."
    if not tavily_client:
        return {
            "success": False,
            "summary": honest_failure_msg,
            "alert": None,
            "articles": [],
            "error": "missing_api_key"
        }

    now_dt = datetime.now()
    month_year = now_dt.strftime("%B %Y")
    if travel_dates:
        for m_name in ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]:
            if m_name.lower() in travel_dates.lower():
                month_year = f"{m_name} {now_dt.year}"
                break

    query = f"{destination} travel advisory OR strikes OR protests OR festival OR weather alert {month_year}"

    raw_results = []
    # Timeout <= 8s with 1 retry
    for attempt in range(2):
        try:
            resp = await asyncio.wait_for(
                asyncio.to_thread(
                    tavily_client.search,
                    query=query,
                    topic="news",
                    days=45,
                    max_results=5
                ),
                timeout=8.0
            )
            if isinstance(resp, dict):
                raw_results = resp.get("results", [])
                if raw_results:
                    break
        except Exception as e:
            if attempt == 1:
                logger.warning(f"Tavily news search failed after retry: {e}")
                return {
                    "success": False,
                    "summary": honest_failure_msg,
                    "alert": None,
                    "articles": [],
                    "error": str(e)
                }

    relevant = filter_travel_relevant_news(raw_results) if raw_results else []
    # If filtered is empty but raw results exist, fall back to top raw results
    display_articles = relevant if relevant else raw_results

    if not display_articles:
        return {
            "success": True,
            "summary": f"No notable travel advisories or disruptions found for {destination}.",
            "alert": None,
            "articles": [],
            "is_notable": False
        }

    lines = [f"🌐 **Live Web News & Advisories for {destination}**:", ""]
    sources = []
    lead_alert_text = None

    for idx, art in enumerate(display_articles[:5], 1):
        title = art.get("title", "").strip()
        url = art.get("url", "").strip()
        content = art.get("content", "").strip()
        pub_date = art.get("published_date") or art.get("date") or ""
        site = extract_site_name(url)

        snippet = re.sub(r'\s+', ' ', content)[:180].strip()
        if snippet and not snippet.endswith('.'):
            snippet += '...'

        date_str = f" ({pub_date.split('T')[0]})" if pub_date else ""
        lines.append(f"{idx}. **{title}**{date_str}\n   {snippet}\n   *Source: [{site}]({url})*")
        sources.append({"site": site, "url": url, "title": title})

        if not lead_alert_text and title and any(kw in (title + content).lower() for kw in ["warning", "advisory", "strike", "closure", "alert"]):
            lead_alert_text = f"⚠️ Travel Advisory for {destination}: {title} (Source: {site})"

    return {
        "success": True,
        "summary": "\n\n".join(lines),
        "alert": lead_alert_text,
        "articles": display_articles,
        "sources": sources,
        "is_notable": bool(lead_alert_text)
    }



# ---------------------------------------------------------------------------
# 3. Local Guide Web Search Fallback for Uncovered Cities
# ---------------------------------------------------------------------------

async def search_local_guide_fallback(
    destination: str,
    client: Optional[Any] = None
) -> dict[str, Any]:
    """
    Provides a live web search escalation for cities outside the Wikivoyage seed set.
    Formats results with distinct '🌐 Live web result' badge and real citations.
    """
    tavily_client = client or get_tavily_client()
    if not tavily_client:
        return {
            "success": False,
            "summary": "Live web search is not configured to check for additional recommendations right now.",
            "results": [],
            "error": "missing_api_key"
        }

    query = f"top sights attractions things to do in {destination}"

    try:
        resp = await asyncio.wait_for(
            asyncio.to_thread(
                tavily_client.search,
                query=query,
                topic="general",
                max_results=4
            ),
            timeout=8.0
        )
        results = resp.get("results", []) if isinstance(resp, dict) else []

        if not results:
            return {
                "success": True,
                "summary": f"No additional attractions found via live web search for '{destination}'.",
                "results": []
            }

        lines = [f"\n🌐 **Live Web Search Recommendations for {destination}**:"]
        formatted_items = []

        for idx, r in enumerate(results, 1):
            title = r.get("title", "").strip()
            url = r.get("url", "").strip()
            content = r.get("content", "").strip()
            site = extract_site_name(url)

            snippet = re.sub(r'\s+', ' ', content)[:200].strip()
            if snippet and not snippet.endswith('.'):
                snippet += '...'

            item_text = (
                f"{idx}. 🌐 Live web result: **{title}**\n"
                f"   {snippet}\n"
                f"   Source: [{site}]({url})"
            )
            lines.append(item_text)
            formatted_items.append({
                "title": title,
                "description": snippet,
                "source": site,
                "url": url,
                "citation": f"Source: {site} ({url})"
            })

        return {
            "success": True,
            "summary": "\n\n".join(lines),
            "results": formatted_items
        }

    except Exception as exc:
        logger.warning(f"Tavily local guide fallback search failed for '{destination}': {exc}")
        return {
            "success": False,
            "summary": f"Live web search for '{destination}' could not be completed right now.",
            "results": [],
            "error": str(exc)
        }


# ---------------------------------------------------------------------------
# 4. Travel Safety & Advisories
# ---------------------------------------------------------------------------

EVERGREEN_SAFETY_TIPS: dict[str, str] = {
    "mumbai": "Keep belongings secure in crowded commuter trains and bustling markets (Colaba Causeway, Crawford Market). Use metered auto-rickshaws or app-based rides (Uber/Ola). Stay hydrated in humid weather and stick to bottled or filtered water.",
    "delhi": "Exercise alertness in crowded bazaars (Chandni Chowk, Sarojini Nagar) against pickpocketing. Prefer the Delhi Metro or verified ride apps for transit. Avoid poorly lit streets late at night and use prepaid taxis at airports/stations.",
    "pune": "Generally a peaceful education and tech hub. Use verified ride apps (Uber/Ola/Rapido) for local transit. Exercise routine caution on busy thoroughfares and while crossing streets.",
    "london": "Exercise vigilance around major tourist hubs (Westminster, Oxford Street, Camden) against pickpocketing and phone snatching. Use contactless payment on TfL buses and the Underground. Avoid unlicensed minicabs.",
    "paris": "Watch for pickpocketing around the Eiffel Tower, Louvre, Montmartre, and on Metro Line 1. Beware of common street petition and bracelet distraction scams. Use official taxis or Uber.",
    "new york": "Remain aware of your surroundings, especially on late-night subway lines. Stick to well-lit streets in busy areas and keep bags securely zipped in crowded spots like Times Square.",
    "dubai": "Very low violent crime rate with strict public safety enforcement. Respect local cultural norms regarding alcohol consumption in licensed venues and modest attire in administrative buildings. Use RTA official taxis or Careem/Uber.",
    "barcelona": "Be vigilant against bag and phone snatching in crowded tourist zones (Las Ramblas, Gothic Quarter, beach promenades). Never leave phones or bags unattended on outdoor dining tables.",
    "lisbon": "Safe city with low violent crime. Watch for pickpockets on historic Tram 28 and in crowded parts of Baixa and Bairro Alto. Ignore street sellers offering bogus souvenir items.",
    "tokyo": "Exceptionally safe international metropolis with very low street crime. Keep identification with you as required by local law. Transit is reliable, well-lit, and thoroughly signed in English."
}

def get_evergreen_safety_guidance(city: str) -> str:
    key = city.strip().lower()
    for k, tips in EVERGREEN_SAFETY_TIPS.items():
        if k in key or key in k:
            return tips
    return "Stay alert in crowded areas, markets, and transit hubs. Keep valuables secured and avoid displaying large amounts of cash. Use official licensed transport or verified ride-hailing apps, and keep emergency contact details accessible."


async def search_safety_advisories(
    destination: str,
    travel_dates: Optional[str] = None,
    client: Optional[Any] = None
) -> dict[str, Any]:
    """
    Live web search for:
    {city} travel advisory OR protests OR strikes OR unrest OR security {current month year}
    Returns 3-5 items with title, date, snippet, and source link.
    Gracefully degrades with timeout <= 8s and 1 retry.
    """
    tavily_client = client or get_tavily_client()
    if not tavily_client:
        logger.warning(f"search_safety_advisories: TAVILY_API_KEY is unset; live web search skipped for '{destination}'")
        return {
            "success": False,
            "items": [],
            "error": "missing_api_key",
            "reason": "TAVILY_API_KEY is not configured in environment"
        }

    now_dt = datetime.now()
    month_year = now_dt.strftime("%B %Y")
    query = f"{destination} travel advisory OR protests OR strikes OR unrest OR security {month_year}"

    raw_results = []
    last_err = None
    for attempt in range(2):
        try:
            resp = await asyncio.wait_for(
                asyncio.to_thread(
                    tavily_client.search,
                    query=query,
                    topic="news",
                    days=45,
                    max_results=5
                ),
                timeout=8.0
            )
            if isinstance(resp, dict):
                raw_results = resp.get("results", [])
                if raw_results:
                    break
        except Exception as e:
            last_err = e
            if attempt == 1:
                logger.warning(f"Tavily safety advisory search failed after retry for '{destination}': {e}")
                return {
                    "success": False,
                    "items": [],
                    "error": str(e),
                    "reason": f"External search request failed: {e}"
                }

    items = []
    for art in raw_results[:5]:
        title = art.get("title", "").strip()
        url = art.get("url", "").strip()
        content = art.get("content", "").strip()
        pub_date = art.get("published_date") or art.get("date") or ""
        site = extract_site_name(url)
        snippet = re.sub(r'\s+', ' ', content)[:180].strip()
        if snippet and not snippet.endswith('.'):
            snippet += '...'
        date_str = pub_date.split('T')[0] if pub_date else month_year
        items.append({
            "title": title,
            "url": url,
            "site": site,
            "date": date_str,
            "snippet": snippet
        })

    return {
        "status": "ok",
        "success": True,
        "items": items,
        "query": query
    }
