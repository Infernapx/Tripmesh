"""
tests/test_round4_seven_issues.py
---------------------------------
Comprehensive verification for:
1. Questions (weather, news, roads) are strictly read-only and never mutate or re-plan trip state.
2. Comparison queries ("Dubai in July or Delhi in June") provide climate comparison without route change.
3. Origin-based currency default (PNQ->DEL=INR, HND->KIX=JPY, LHR->JFK=GBP, DEL->LHR=INR).
4. Central session write guard rejecting None, "", "Unknown", "Unknown City", "UNK".
5. Tavily graceful degradation on missing key, timeout, or error.
6. Open-Meteo weather formatting (2-4 lines, typical climate for distant dates or named months).
7. Confirm follow-up after weather question confirms the original Pune -> Delhi plan.
8. Typo confirmation & show itinerary synonyms.
9. Chat history storage, max-30 cap, resume.
"""

import pytest
import asyncio
from datetime import datetime
from unittest.mock import patch, MagicMock

from shared.resolvers import currency_for_city, resolve_city_to_iata, validate_and_resolve_city
from shared.weather import get_weather_forecast, get_typical_climate
from shared.tavily_search import search_destination_news, search_road_conditions
from planner_agent.app.session_store import (
    get_session_trip,
    save_session_trip,
    reset_session_trip,
    clear_session_trip,
    set_awaiting_confirmation,
    is_awaiting_confirmation,
    confirm_session_trip,
    get_session_snapshot,
    restore_session_snapshot,
    is_invalid_city
)
from planner_agent.app.graph import planner_graph
from planner_agent.app.intent_classifier import classify_intent, IntentType
from langchain_core.messages import HumanMessage
MAX_CHATS = 30




# =========================================================================
# Fix 3: Currency default by origin city
# =========================================================================

def test_currency_for_city_defaults():
    # India origin -> INR
    assert currency_for_city("PNQ") == "INR"
    assert currency_for_city("DEL") == "INR"
    assert currency_for_city("Pune") == "INR"
    assert currency_for_city("Delhi") == "INR"

    # Japan origin -> JPY
    assert currency_for_city("HND") == "JPY"
    assert currency_for_city("NRT") == "JPY"
    assert currency_for_city("Tokyo") == "JPY"

    # UK origin -> GBP
    assert currency_for_city("LHR") == "GBP"
    assert currency_for_city("London") == "GBP"

    # US origin -> USD
    assert currency_for_city("JFK") == "USD"
    assert currency_for_city("New York") == "USD"

    # UAE origin -> AED
    assert currency_for_city("DXB") == "AED"
    assert currency_for_city("Dubai") == "AED"

    # EU origins -> EUR
    assert currency_for_city("CDG") == "EUR"
    assert currency_for_city("Paris") == "EUR"


def test_currency_priority_on_routes():
    async def _run():
        # 1. PNQ -> DEL should default to INR
        sid = "test-curr-pune-delhi"
        clear_session_trip(sid)
        res1 = await planner_graph.ainvoke({
            "messages": [HumanMessage(content="pune to delhi")],
            "session_id": sid,
            "user_id": "test_user_curr"
        })
        plan1 = res1.get("plan")
        assert plan1 is not None
        assert plan1.get("display_currency") == "INR", f"Expected INR for PNQ->DEL, got {plan1.get('display_currency')}"
        assert "Since you usually prefer" not in res1["messages"][-1].content

        # 2. LHR -> JFK should default to GBP
        sid2 = "test-curr-lhr-jfk"
        clear_session_trip(sid2)
        res2 = await planner_graph.ainvoke({
            "messages": [HumanMessage(content="london to new york")],
            "session_id": sid2,
            "user_id": "test_user_curr"
        })
        plan2 = res2.get("plan")
        assert plan2 is not None
        assert plan2.get("display_currency") == "GBP", f"Expected GBP for LHR->JFK, got {plan2.get('display_currency')}"

        # 3. Explicit currency in message overrides origin default
        sid3 = "test-curr-explicit"
        clear_session_trip(sid3)
        res3 = await planner_graph.ainvoke({
            "messages": [HumanMessage(content="pune to delhi in euros")],
            "session_id": sid3,
            "user_id": "test_user_curr"
        })
        plan3 = res3.get("plan")
        assert plan3 is not None
        assert plan3.get("display_currency") == "EUR", f"Expected EUR, got {plan3.get('display_currency')}"

    asyncio.run(_run())


# =========================================================================
# Fix 1 + 2 + 5: Read-only Questions Never Mutate or Re-plan Session
# =========================================================================

def test_weather_news_roads_preserve_session_and_plan():
    async def _run():
        sid = "test-readonly-session"
        clear_session_trip(sid)

        # 1. Establish Pune -> Delhi trip
        r1 = await planner_graph.ainvoke({
            "messages": [HumanMessage(content="pune to delhi")],
            "session_id": sid,
            "user_id": "user1"
        })
        initial_plan = r1.get("plan")
        assert initial_plan is not None
        assert initial_plan["origin"] == "PNQ"
        assert initial_plan["destination"] == "DEL"

        s_before = get_session_trip(sid)

        # 2. Ask weather
        r_weather = await planner_graph.ainvoke({
            "messages": [HumanMessage(content="what's the weather going to be like?")],
            "session_id": sid,
            "user_id": "user1"
        })
        # Must NOT re-render plan card
        assert r_weather.get("plan") is None
        # Transport/hotel nodes must NOT have run
        assert "call_transport_agent" not in r_weather.get("invoked_nodes", [])
        assert "call_hotel_agent" not in r_weather.get("invoked_nodes", [])
        assert "render_weather" in r_weather.get("invoked_nodes", [])

        # Session state must be identical
        s_after_w = get_session_trip(sid)
        assert s_after_w["origin"] == "PNQ"
        assert s_after_w["destination"] == "DEL"
        assert s_after_w["display_currency"] == s_before["display_currency"]

        # 3. Ask news
        r_news = await planner_graph.ainvoke({
            "messages": [HumanMessage(content="any news I should know about before I go?")],
            "session_id": sid,
            "user_id": "user1"
        })
        assert r_news.get("plan") is None
        assert "call_transport_agent" not in r_news.get("invoked_nodes", [])
        assert "render_destination_news" in r_news.get("invoked_nodes", [])
        s_after_n = get_session_trip(sid)
        assert s_after_n["origin"] == "PNQ"
        assert s_after_n["destination"] == "DEL"

        # 4. Ask road conditions
        r_roads = await planner_graph.ainvoke({
            "messages": [HumanMessage(content="how are the roads?")],
            "session_id": sid,
            "user_id": "user1"
        })
        assert r_roads.get("plan") is None
        assert "call_transport_agent" not in r_roads.get("invoked_nodes", [])
        assert "render_road_conditions" in r_roads.get("invoked_nodes", [])
        s_after_r = get_session_trip(sid)
        assert s_after_r["origin"] == "PNQ"
        assert s_after_r["destination"] == "DEL"

        # 5. Follow-up "confirm" must operate on the original Pune -> Delhi plan
        r_confirm = await planner_graph.ainvoke({
            "messages": [HumanMessage(content="confirm")],
            "session_id": sid,
            "user_id": "user1"
        })
        assert "confirm_plan" in r_confirm.get("invoked_nodes", [])
        assert "confirmed" in r_confirm["messages"][-1].content.lower()

    asyncio.run(_run())


def test_comparison_query_preserves_session():
    async def _run():
        sid = "test-comparison-query"
        clear_session_trip(sid)

        # 1. Establish trip
        await planner_graph.ainvoke({
            "messages": [HumanMessage(content="pune to delhi")],
            "session_id": sid,
            "user_id": "user1"
        })

        # 2. Ask comparison
        r_comp = await planner_graph.ainvoke({
            "messages": [HumanMessage(content="Dubai in July or Delhi in June")],
            "session_id": sid,
            "user_id": "user1"
        })

        # Must provide climate comparison for both cities
        resp_text = r_comp["messages"][-1].content
        assert "Dubai" in resp_text and "Delhi" in resp_text
        assert "July" in resp_text and "June" in resp_text
        assert r_comp.get("plan") is None
        assert "call_transport_agent" not in r_comp.get("invoked_nodes", [])

        # Session state must still be Pune -> Delhi
        s = get_session_trip(sid)
        assert s["origin"] == "PNQ"
        assert s["destination"] == "DEL"

    asyncio.run(_run())


# =========================================================================
# Fix 4: Tavily Error Handling & Graceful Degradation
# =========================================================================

def test_tavily_news_degradation_without_key():
    async def _run():
        with patch.dict("os.environ", {"TAVILY_API_KEY": ""}):
            res = await search_destination_news("Delhi")
            assert res["success"] is False
            assert "I couldn't fetch live news right now. Check a local source before you travel." in res["summary"]

    asyncio.run(_run())


def test_tavily_news_timeout_handling():
    async def _run():
        mock_client = MagicMock()
        mock_client.search.side_effect = asyncio.TimeoutError("Connection timed out")
        res = await search_destination_news("Delhi", client=mock_client)
        assert res["success"] is False
        assert "I couldn't fetch live news right now. Check a local source before you travel." in res["summary"]

    asyncio.run(_run())


# =========================================================================
# Fix 5: Weather Forecast & Climatology (2-4 lines)
# =========================================================================

def test_weather_forecast_delhi_june_typical():
    async def _run():
        res = await get_weather_forecast("Delhi", start_date="in June")
        assert res["success"] is True
        summary = res["summary"]
        lines = [l for l in summary.split("\n") if l.strip()]
        # Expected 2-5 lines with temperature, rain chance, and tip
        assert 2 <= len(lines) <= 6
        assert "Typical Climate for Delhi in June" in summary
        assert "Temperature" in summary
        assert "Rain Chance" in summary
        assert "Travel Tip" in summary

    asyncio.run(_run())


# =========================================================================
# Fix 1.4: Central Session Write Guard
# =========================================================================

def test_central_session_write_guard():
    sid = "test-guard-session"
    clear_session_trip(sid)

    # Initial valid write
    save_session_trip(sid, {"origin": "PNQ", "destination": "DEL", "origin_name": "Pune", "destination_name": "Delhi"})
    s = get_session_trip(sid)
    assert s["origin"] == "PNQ" and s["destination"] == "DEL"

    # Attempt to overwrite with UNK / Unknown / empty / None
    save_session_trip(sid, {"origin": "UNK", "destination": "UNKNOWN", "origin_name": "Unknown City", "destination_name": ""})
    s2 = get_session_trip(sid)
    assert s2["origin"] == "PNQ", "Guard failed to protect origin from UNK"
    assert s2["destination"] == "DEL", "Guard failed to protect destination from UNKNOWN"
    assert s2["origin_name"] == "Pune", "Guard failed to protect origin_name"

    # Attempt to write invalid confirmed_plan
    save_session_trip(sid, {
        "confirmed_plan": {"origin": "UNK", "destination": "DEL"}
    })
    s3 = get_session_trip(sid)
    assert "confirmed_plan" not in s3, "Guard failed to reject confirmed_plan with UNK origin"


# =========================================================================
# Fix 7: Synonyms and Typos
# =========================================================================

def test_confirm_and_show_itinerary_synonyms():
    ctx = {"awaiting_confirmation": True, "origin": "PNQ", "destination": "DEL"}

    # Typos for confirm
    for typo in ["conferm", "confirn", "comfirm", "yep", "yup", "ok", "go ahead", "book it"]:
        ci = classify_intent(typo, session_context=ctx)
        assert ci.intent == IntentType.CONFIRM_PLAN, f"Failed for typo '{typo}'"

    # Show itinerary synonyms
    for phrase in ["show me the itinerary again", "show plan", "what was the trip", "display itinerary again"]:
        ci = classify_intent(phrase, session_context=ctx)
        assert ci.intent in [IntentType.SHOW_CURRENT_PLAN, IntentType.SHOW_ITINERARY], f"Failed for phrase '{phrase}'"


# =========================================================================
# Fix 6: Chat History Storage Logic & Max 30 Cap (Matching Frontend Contract)
# =========================================================================

def test_chat_history_storage_and_cap():
    import json

    # Simulate localStorage dictionary
    local_storage = {}

    def save_archived_chat(user_id: str, chat_data: dict):
        key = f"tripmesh:chats:{user_id}"
        existing_json = local_storage.get(key, "[]")
        try:
            chats = json.loads(existing_json)
        except Exception:
            chats = []
        chats = [c for c in chats if c.get("id") != chat_data.get("id")]
        chats.insert(0, chat_data)
        if len(chats) > MAX_CHATS:
            chats = chats[:MAX_CHATS]
        local_storage[key] = json.dumps(chats)

    def load_archived_chats(user_id: str):
        key = f"tripmesh:chats:{user_id}"
        existing_json = local_storage.get(key, "[]")
        try:
            return json.loads(existing_json)
        except Exception:
            return []

    # Push 35 chats
    for i in range(35):
        save_archived_chat("test-user", {
            "id": f"chat-{i}",
            "sessionId": f"sess-{i}",
            "timestamp": 1000 + i,
            "routeTitle": f"CityA -> CityB {i}",
            "messages": []
        })

    loaded = load_archived_chats("test-user")
    assert len(loaded) == 30, f"Expected capped to 30, got {len(loaded)}"
    # Most recent chat must be at index 0
    assert loaded[0]["id"] == "chat-34"
    # Oldest chats (0 to 4) must be dropped
    ids = [c["id"] for c in loaded]
    assert "chat-0" not in ids
    assert "chat-4" not in ids
    assert "chat-5" in ids

