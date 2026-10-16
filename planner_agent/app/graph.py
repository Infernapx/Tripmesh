import os
import re
import json
import logging
from datetime import datetime, date, timedelta
from typing import Optional
from langgraph.graph import StateGraph, START, END
from langchain_core.messages import AIMessage
try:
    from .state import PlannerState
    from .session_store import (
        get_session_trip,
        reset_session_trip,
        save_session_trip,
        set_pending_slot,
        set_awaiting_confirmation,
        confirm_session_trip,
        get_confirmed_trip,
        invalidate_confirmation,
        clear_session_trip
    )
    from .intent_classifier import classify_intent, IntentType
    from .structured_classifier import classify_with_llm, classify_with_rules, check_fast_path
    from .a2a_client import (
        call_budget_agent_a2a,
        call_budget_reconciliation_a2a,
        call_transport_agent_a2a,
        call_hotel_agent_a2a,
        call_local_guide_agent_a2a,
        call_single_currency_rate_a2a,
        _extract_plan_details
    )
except ImportError:
    from app.state import PlannerState
    from app.session_store import (
        get_session_trip,
        reset_session_trip,
        save_session_trip,
        set_pending_slot,
        set_awaiting_confirmation,
        confirm_session_trip,
        get_confirmed_trip,
        invalidate_confirmation,
        clear_session_trip
    )
    from app.intent_classifier import classify_intent, IntentType
    from app.structured_classifier import classify_with_llm, classify_with_rules, check_fast_path
    from app.a2a_client import (
        call_budget_agent_a2a,
        call_budget_reconciliation_a2a,
        call_transport_agent_a2a,
        call_hotel_agent_a2a,
        call_local_guide_agent_a2a,
        call_single_currency_rate_a2a,
        _extract_plan_details
    )

logger = logging.getLogger(__name__)

from shared.schemas import TripPlanPayload
from shared.resolvers import (
    resolve_currency_name, resolve_city_to_iata, validate_and_resolve_city,
    extract_route_cities_from_text, infer_default_currency_from_route, currency_for_city,
    get_default_trip_dates, is_date_in_past
)
from shared.memory_store import (
    get_user_memory,
    update_user_memory,
    record_confirmed_trip,
    infer_hotel_tier
)
from shared.observability import trace_agent_call, get_session_invoked_nodes, get_session_traces
from shared.gemini_client import generate_conversational_travel_response

CURRENCY_SYMBOLS = {
    "$": "USD",
    "€": "EUR",
    "£": "GBP",
    "¥": "JPY",
    "₹": "INR"
}

CURRENCY_TO_SYMBOL = {
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
    "JPY": "¥",
    "INR": "₹",
    "CHF": "CHF "
}

# Technical minimum ceiling to prevent passing zero or negative price ceilings to external booking/search APIs during negotiation.
# Purely an internal API-call guard that never appears in user-facing text or reconciliation math.
MIN_NEGOTIATION_PRICE_CEILING = 10.0


def extract_budget_intent(
    text: str,
    prior_budget_cap: Optional[float] = None,
    prior_total_cost: Optional[float] = None,
    prior_currency: Optional[str] = None
) -> tuple[Optional[float], str, str]:
    budget_cap: Optional[float] = None
    explicit_display_currency = resolve_currency_name(text)
    fallback_curr = prior_currency or "USD"
    target_currency = explicit_display_currency or fallback_curr

    # 1. Relative drop: "dropped $500", "dropped by 200 EUR", "cut costs by 300", "budget dropped 500"
    m_rel = re.search(
        r'\b(?:dropped|decreased|cut|reduced|fell)\s+(?:by\s+)?([$€£¥₹])?\s*(\d+(?:,\d+)*(?:\.\d+)?)\s*(USD|EUR|GBP|JPY|CAD|AUD|CHF|INR|AED)?',
        text,
        re.IGNORECASE
    )
    if m_rel:
        sym = m_rel.group(1)
        drop_amt = float(m_rel.group(2).replace(',', ''))
        code = m_rel.group(3)
        if sym:
            target_currency = CURRENCY_SYMBOLS.get(sym, fallback_curr)
        elif code:
            target_currency = code.upper()
        display_currency = explicit_display_currency or target_currency
        
        base_cap = prior_budget_cap if prior_budget_cap is not None else (prior_total_cost if prior_total_cost is not None else 1000.0)
        budget_cap = round(base_cap - drop_amt, 2)
        return budget_cap, target_currency, display_currency

    # 2. Absolute cap with currency symbol: "$500", "under €300", "$0", "-$50"
    m_sym = re.search(r'([$€£¥₹])\s*(-?\d+(?:,\d+)*(?:\.\d+)?)', text)
    if m_sym:
        sym = m_sym.group(1)
        budget_cap = float(m_sym.group(2).replace(',', ''))
        target_currency = CURRENCY_SYMBOLS.get(sym, fallback_curr)
        display_currency = explicit_display_currency or target_currency
        return budget_cap, target_currency, display_currency

    # 3. Absolute cap with currency code: "300 EUR", "500 USD", "0 USD"
    m_code = re.search(r'(-?\d+(?:,\d+)*(?:\.\d+)?)\s*(USD|EUR|GBP|JPY|CAD|AUD|CHF|INR|AED)', text, re.IGNORECASE)
    if m_code:
        budget_cap = float(m_code.group(1).replace(',', ''))
        target_currency = m_code.group(2).upper()
        display_currency = explicit_display_currency or target_currency
        return budget_cap, target_currency, display_currency

    # 4. Absolute number keywords / conversational budget statements:
    # "cap it at 300", "let's cap it at 300", "let's say 300", "say 800 instead", "300 max", "budget 300", "under 400"
    m_num = re.search(
        r'\b(?:(?:let\'?s\s+)?(?:cap\s+(?:it\s+)?at|say|do|try)|make\s+it|how\s+about|set\s+(?:the\s+)?budget\s+(?:to|at)|(?:my\s+)?(?:budget|bugget|max|cap|limit|spending)\s*(?:is\s+now|now\s+is|is|of\s+|now\s+of\s+|to\s+)?|(?:now\s+)?(?:i\s+have\s+)?(?:a\s+)?(?:budget|bugget)\s*(?:of\s+|is\s+)?|(?:under|keep\s+it\s+under|to)\s*(?:of\s+)?)\s*(?:around\s+)?([$€£¥₹])?\s*(-?\d+(?:,\d+)*(?:\.\d+)?)\s*(USD|EUR|GBP|JPY|CAD|AUD|CHF|INR|AED)?(?:\s+(?:instead|max))?\b',
        text,
        re.IGNORECASE
    )
    if not m_num:
        m_num = re.search(r'\b(-?\d+(?:,\d+)*(?:\.\d+)?)\s*(USD|EUR|GBP|JPY|CAD|AUD|CHF|INR|AED)?\s+(?:instead|max|cap|budget|limit)\b', text, re.IGNORECASE)
        if m_num:
            budget_cap = float(m_num.group(1).replace(',', ''))
            code = m_num.group(2)
            curr = code.upper() if code else (explicit_display_currency or fallback_curr)
            return budget_cap, curr, curr
    else:
        sym = m_num.group(1)
        amt_str = m_num.group(2)
        code = m_num.group(3)
        budget_cap = float(amt_str.replace(',', ''))
        target_curr = CURRENCY_SYMBOLS.get(sym, fallback_curr) if sym else (code.upper() if code else (explicit_display_currency or fallback_curr))
        display_curr = explicit_display_currency or target_curr
        return budget_cap, target_curr, display_curr

    if re.search(r'\b(?:under|budget\s+of)\s+a\s+grand\b', text, re.IGNORECASE):
        display_currency = explicit_display_currency or fallback_curr
        return 1000.0, fallback_curr, display_currency

    display_currency = explicit_display_currency or fallback_curr
    return None, display_currency, display_currency


def get_budget_drop_details(
    text: str,
    prior_budget_cap: Optional[float] = None,
    prior_total_cost: Optional[float] = None
) -> Optional[dict]:
    explicit_display_currency = resolve_currency_name(text)
    m_rel = re.search(
        r'\b(?:dropped|decreased|cut|reduced|fell)\s+(?:by\s+)?([$€£¥₹])?\s*(\d+(?:,\d+)*(?:\.\d+)?)\s*(USD|EUR|GBP|JPY|CAD|AUD|CHF|INR|AED)?',
        text,
        re.IGNORECASE
    )
    if m_rel:
        sym = m_rel.group(1)
        drop_amt = float(m_rel.group(2).replace(',', ''))
        code = m_rel.group(3)
        target_curr = CURRENCY_SYMBOLS.get(sym, "USD") if sym else (code.upper() if code else (explicit_display_currency or "USD"))
        base_cap = prior_budget_cap if prior_budget_cap is not None else (prior_total_cost if prior_total_cost is not None else 1000.0)
        remaining = round(base_cap - drop_amt, 2)
        return {
            "is_relative": True,
            "drop_amt": drop_amt,
            "base_cap": base_cap,
            "remaining": remaining,
            "target_currency": target_curr,
            "currency_symbol": sym or CURRENCY_TO_SYMBOL.get(target_curr, "$")
        }
    return None



# Node 1: Intent parsing & slot handling (implementation)
async def _parse_intent_node_impl(state: PlannerState) -> dict:
    user_message = state["messages"][-1].content
    session_id = state.get("session_id", "local")
    user_id = state.get("user_id", "")
    logger.info(f"LangGraph Parse Node: parsing '{user_message}' for session '{session_id}' (user: '{user_id}')")
    
    # Load session memory
    prior_session = get_session_trip(session_id)
    # Load cross-session persistent user memory
    user_memory = get_user_memory(user_id) if user_id else None
    
    # Run intent classification
    classified = classify_intent(user_message, session_context=prior_session)

    # Check for structured input payload from client (e.g. calendar date picker)
    structured_payload = state.get("structured_payload")
    if structured_payload and structured_payload.get("type") == "change_dates":
        dep = structured_payload.get("departure")
        ret = structured_payload.get("return") or dep
        today_date = date.today()
        try:
            dep_obj = datetime.strptime(dep.split("T")[0], "%Y-%m-%d").date()
            ret_obj = datetime.strptime(ret.split("T")[0], "%Y-%m-%d").date() if ret else dep_obj
        except Exception:
            return {
                "messages": [AIMessage(content="Invalid date format provided in date change request.")],
                "invoked_nodes": ["parse_intent"],
                "plan": prior_session.get("confirmed_plan") or prior_session
            }

        if dep_obj < today_date:
            return {
                "messages": [AIMessage(content="Departure date cannot be in the past. Please select a valid future date.")],
                "invoked_nodes": ["parse_intent"],
                "plan": prior_session.get("confirmed_plan") or prior_session
            }
        if ret_obj < dep_obj:
            return {
                "messages": [AIMessage(content="Return date cannot be earlier than departure date. Please select a valid date range.")],
                "invoked_nodes": ["parse_intent"],
                "plan": prior_session.get("confirmed_plan") or prior_session
            }

        classified.intent = IntentType.MODIFY_EXISTING
        classified.departure_date = dep
        classified.check_in = dep
        classified.check_out = ret

    # When NEW_TRIP_ROUTE is detected, it takes unconditional priority.
    # Full reset of session trip state: clear all leftover data from prior trip.
    structured_cls = None
    if classified.intent == IntentType.NEW_TRIP_ROUTE:
        if classified.origin and classified.origin != "UNK" and classified.destination and classified.destination != "UNK":
            reset_session_trip(session_id)
            prior_session = {}
        else:
            logger.warning(f"Invalid route detected (orig={classified.origin}, dest={classified.destination}); preserving prior session")
    elif (prior_session.get("awaiting_confirmation") or prior_session.get("destination")) and not (structured_payload and structured_payload.get("type") == "change_dates"):
        # When plan is active or awaiting confirmation, invoke unified LLM / semantic structured classifier
        structured_cls = await classify_with_llm(user_message, session_context=prior_session)
        logger.info(f"Unified Structured Classification: intent={structured_cls.intent}, currency={structured_cls.currency}, budget={structured_cls.budget_amount}, target={structured_cls.modifier_target}, direction={structured_cls.modifier_direction}")

        if structured_cls:
            if structured_cls.intent == "new_trip_route":
                route_res = extract_route_cities_from_text(user_message)
                if route_res:
                    (orig_n, orig_i), (dest_n, dest_i) = route_res
                    if orig_i != "UNK" and dest_i != "UNK":
                        reset_session_trip(session_id)
                        prior_session = {}
                        classified.intent = IntentType.NEW_TRIP_ROUTE
                        classified.origin = orig_i
                        classified.origin_name = orig_n
                        classified.destination = dest_i
                        classified.destination_name = dest_n
                        classified.display_currency = currency_for_city(orig_i) or "USD"
                        classified.target_currency = classified.display_currency
            elif structured_cls.intent in ["show_itinerary", "show_current_plan"]:
                classified.intent = IntentType.SHOW_CURRENT_PLAN
            elif structured_cls.intent == "confirm":
                classified.intent = IntentType.CONFIRM_PLAN
            elif structured_cls.intent == "cancel":
                classified.intent = IntentType.CANCEL_TRIP
            elif structured_cls.intent == "general_conversation":
                classified.intent = IntentType.GENERAL_CONVERSATION
            elif structured_cls.intent == "ask_about_destination":
                month_regex = r'(?:january|february|march|april|may|june|july|august|september|october|november|december)'
                if re.search(rf'[a-zA-Z\s]+\s+in\s+{month_regex}\b', user_message, re.IGNORECASE):
                    classified.intent = IntentType.ASK_WEATHER
                else:
                    classified.intent = IntentType.ASK_ABOUT_DESTINATION
                classified.activity_query = user_message
            elif structured_cls.intent in ["check_weather", "ask_weather"]:
                classified.intent = IntentType.ASK_WEATHER
                classified.activity_query = user_message
            elif structured_cls.intent in ["check_road_conditions", "ask_road_conditions"]:
                classified.intent = IntentType.ASK_ROAD_CONDITIONS
                classified.activity_query = user_message
            elif structured_cls.intent in ["check_safety", "ask_safety"]:
                classified.intent = IntentType.ASK_SAFETY
                classified.activity_query = user_message
            elif structured_cls.intent in ["check_destination_news", "ask_news"]:
                classified.intent = IntentType.ASK_NEWS
                classified.activity_query = user_message
            elif structured_cls.intent == "modify_currency":
                valid_curr = resolve_currency_name(structured_cls.currency) if structured_cls.currency else None
                if valid_curr:
                    classified.intent = IntentType.MODIFY_EXISTING
                    classified.display_currency = valid_curr
                    classified.target_currency = valid_curr
                else:
                    classified.intent = IntentType.NEED_CURRENCY_CLARIFICATION
            elif structured_cls.intent == "modify_budget":
                if getattr(structured_cls, "is_budget_removal", False):
                    classified.intent = IntentType.MIDTRIP_BUDGET_CHANGE
                    classified.budget_cap = None
                elif getattr(structured_cls, "is_budget_query", False):
                    classified.intent = IntentType.SHOW_CURRENT_PLAN
                    classified.budget_cap = prior_session.get("budget_cap")
                else:
                    classified.intent = IntentType.MIDTRIP_BUDGET_CHANGE
                    if structured_cls.budget_amount is not None:
                        classified.budget_cap = structured_cls.budget_amount
            elif structured_cls.intent == "modify_hotel":
                classified.intent = IntentType.MODIFY_EXISTING
            elif structured_cls.intent == "modify_flight":
                if bool(re.search(r'\b(?:delay|delayed|rescheduled)\b', user_message, re.IGNORECASE)):
                    classified.intent = IntentType.MIDTRIP_FLIGHT_DELAY
                    classified.delay_context = user_message
                else:
                    classified.intent = IntentType.MODIFY_EXISTING
            elif structured_cls.intent == "modify_dates":
                classified.intent = IntentType.MODIFY_EXISTING
            elif structured_cls.intent == "unclear":
                if prior_session.get("awaiting_confirmation"):
                    classified.intent = IntentType.AMBIGUOUS_CONFIRMATION

    logger.info(f"Classified Intent: {classified.intent} (orig={classified.origin}, dest={classified.destination})")

    prior_plan = prior_session.get("confirmed_plan") or prior_session
    prior_cost_items = prior_plan.get("cost_line_items") or prior_session.get("cost_line_items") or []
    prior_total = sum(float(it.get("amount", 0.0)) for it in prior_cost_items) if prior_cost_items else None
    prior_curr = prior_session.get("display_currency") or prior_session.get("target_currency")

    new_budget_cap, new_target_curr, new_display_curr = extract_budget_intent(
        user_message,
        prior_budget_cap=prior_session.get("budget_cap"),
        prior_total_cost=prior_total,
        prior_currency=prior_curr
    )
    if classified.budget_cap is not None:
        new_budget_cap = classified.budget_cap
    if classified.target_currency:
        new_target_curr = classified.target_currency
    if classified.display_currency:
        new_display_curr = classified.display_currency

    # Determine if an explicit currency change was requested in this turn
    explicit_currency = resolve_currency_name(user_message)
    if not explicit_currency and structured_cls and structured_cls.intent == "modify_currency" and structured_cls.currency:
        explicit_currency = resolve_currency_name(structured_cls.currency)
    if not explicit_currency:
        m_curr_sym = re.search(r'([$€£¥₹])\s*-?\d+', user_message)
        if m_curr_sym:
            explicit_currency = CURRENCY_SYMBOLS.get(m_curr_sym.group(1))
        else:
            m_curr_code = re.search(r'-?\d+\s*(USD|EUR|GBP|JPY|CAD|AUD|CHF|INR|AED)\b', user_message, re.IGNORECASE)
            if m_curr_code:
                explicit_currency = m_curr_code.group(1).upper()

    budget_requested = prior_session.get("budget_requested", False)
    if classified.intent == IntentType.NEW_TRIP_ROUTE:
        # A New Journey or new route resets the budget flag
        budget_requested = False
        if classified.budget_cap is not None or new_budget_cap is not None:
            budget_requested = True
        elif structured_cls and (structured_cls.budget_amount is not None or getattr(structured_cls, "is_budget_query", False)):
            budget_requested = True
        budget_cap = new_budget_cap if budget_requested else None
    else:
        if structured_cls and getattr(structured_cls, "is_budget_removal", False):
            budget_requested = False
            new_budget_cap = None
            budget_cap = None
        elif structured_cls and (structured_cls.budget_amount is not None or getattr(structured_cls, "is_budget_query", False) or structured_cls.intent == "modify_budget"):
            budget_requested = True
            budget_cap = new_budget_cap if new_budget_cap is not None else prior_session.get("budget_cap")
        elif classified.budget_cap is not None or new_budget_cap is not None:
            budget_requested = True
            budget_cap = new_budget_cap if new_budget_cap is not None else prior_session.get("budget_cap")
        else:
            budget_cap = prior_session.get("budget_cap") if budget_requested else None

    save_session_trip(session_id, {"budget_requested": budget_requested, "budget_cap": budget_cap})
    
    used_memory_currency = False
    used_memory_origin = False
    used_memory_currency = False

    # Fix 3 Priority order for display_currency:
    # 1. Currency explicitly requested in this message
    # 2. On a new trip route (new origin): origin city's local currency via currency_for_city(origin)
    # 3. Currency already set explicitly earlier in THIS session
    # 4. Fallback to origin currency, then USD.
    if explicit_currency:
        display_currency = explicit_currency
        target_currency = explicit_currency
    elif classified.intent == IntentType.NEW_TRIP_ROUTE:
        orig_cand = classified.origin or classified.origin_name
        orig_curr = currency_for_city(orig_cand) if orig_cand else None
        display_currency = orig_curr or "USD"
        target_currency = display_currency
    elif prior_session.get("display_currency"):
        display_currency = prior_session.get("display_currency")
        target_currency = prior_session.get("target_currency", display_currency)
    elif new_budget_cap is not None and new_display_curr and new_display_curr != "USD":
        display_currency = new_display_curr
        target_currency = new_target_curr
    else:
        orig_cand = classified.origin or prior_session.get("origin") or classified.origin_name or prior_session.get("origin_name")
        orig_curr = currency_for_city(orig_cand) if orig_cand else None
        display_currency = orig_curr or "USD"
        target_currency = display_currency

    # If prior session has a past departure date, clear it so it re-defaults to the future
    if prior_session.get("departure_date") and is_date_in_past(prior_session.get("departure_date")):
        prior_session["departure_date"] = None
        prior_session["check_in"] = None
        prior_session["check_out"] = None

    default_dep, default_checkin, default_checkout = get_default_trip_dates()

    dep_date = classified.departure_date or prior_session.get("departure_date") or default_dep
    check_in_date = classified.check_in or prior_session.get("check_in") or default_checkin
    check_out_date = classified.check_out or prior_session.get("check_out") or default_checkout

    # Currency Clarification request
    if classified.intent == IntentType.NEED_CURRENCY_CLARIFICATION:
        return {
            "is_conversion": False,
            "is_planning": False,
            "is_budget_change": False,
            "is_flight_delay": False,
            "is_replan": False,
            "is_invalid_budget": False,
            "invalid_budget_message": None,
            "is_cancellation": False,
            "delay_context": None,
            "is_guide_query": False,
            "is_conversational": False,
            "needs_destination": False,
            "needs_origin": False,
            "needs_date_clarification": False,
            "needs_currency_clarification": True,
            "is_confirmation": False,
            "is_ambiguous_confirmation": False,
            "activity_query": None,
            "origin": prior_session.get("origin"),
            "origin_name": prior_session.get("origin_name"),
            "destination": prior_session.get("destination"),
            "destination_name": prior_session.get("destination_name"),
            "departure_date": dep_date,
            "check_in": check_in_date,
            "check_out": check_out_date,
            "budget_cap": budget_cap,
            "target_currency": target_currency,
            "display_currency": display_currency,
            "used_memory_currency": used_memory_currency,
            "used_memory_origin": False,
            "user_id": user_id,
            "negotiation_round": 0,
            "max_negotiation_rounds": 2,
            "transport_price_ceiling": None,
            "hotel_price_ceiling": None,
            "cost_line_items": [],
            "cached_transport_results": [],
            "cached_hotel_results": [],
            "cached_local_guide_results": [],
            "transport_agent_response": "",
            "hotel_agent_response": "",
            "local_guide_agent_response": "",
            "invoked_nodes": ["parse_intent"]
        }

    # Date Clarification request
    if classified.intent == IntentType.NEED_DATE_CLARIFICATION:
        set_pending_slot(session_id, "date")
        return {
            "is_conversion": False,
            "is_planning": False,
            "is_budget_change": False,
            "is_flight_delay": False,
            "is_replan": False,
            "is_invalid_budget": False,
            "invalid_budget_message": None,
            "is_cancellation": False,
            "delay_context": None,
            "is_guide_query": False,
            "is_conversational": False,
            "needs_destination": False,
            "needs_origin": False,
            "needs_date_clarification": True,
            "has_past_date": bool(getattr(classified, "has_past_date", False)),
            "is_safety_query": False,
            "needs_currency_clarification": False,
            "is_confirmation": False,
            "is_ambiguous_confirmation": False,
            "activity_query": None,
            "origin": classified.origin or prior_session.get("origin"),
            "origin_name": classified.origin_name or prior_session.get("origin_name"),
            "destination": classified.destination or prior_session.get("destination"),
            "destination_name": classified.destination_name or prior_session.get("destination_name"),
            "departure_date": None,
            "check_in": None,
            "check_out": None,
            "budget_cap": budget_cap,
            "target_currency": target_currency,
            "display_currency": display_currency,
            "used_memory_currency": used_memory_currency,
            "used_memory_origin": used_memory_origin,
            "user_id": user_id,
            "negotiation_round": 0,
            "max_negotiation_rounds": 2,
            "transport_price_ceiling": None,
            "hotel_price_ceiling": None,
            "cost_line_items": [],
            "cached_transport_results": [],
            "cached_hotel_results": [],
            "cached_local_guide_results": [],
            "transport_agent_response": "",
            "hotel_agent_response": "",
            "local_guide_agent_response": "",
            "invoked_nodes": ["parse_intent"]
        }

    # Default frequent origin from user memory if available
    frequent_origin_name = user_memory.frequent_origin if user_memory else None
    if frequent_origin_name:
        _resolved = resolve_city_to_iata(frequent_origin_name)
        frequent_origin_iata = _resolved[1] if isinstance(_resolved, tuple) else _resolved
    else:
        frequent_origin_iata = None

    # Handle slot transitions
    used_memory_origin = False
    if classified.intent == IntentType.SLOT_FILLED_NEED_ORIGIN:
        destination = classified.destination
        destination_name = classified.destination_name
        
        # If user has a frequent origin in memory, default to it and proceed directly to planning
        if frequent_origin_iata and frequent_origin_name and not prior_session.get("origin"):
            origin = frequent_origin_iata
            origin_name = frequent_origin_name
            used_memory_origin = True
            set_pending_slot(session_id, None)
            return {
                "is_conversion": False,
                "is_planning": True,
                "is_budget_change": False,
                "is_flight_delay": False,
                "is_replan": False,
                "delay_context": None,
                "is_guide_query": False,
                "is_conversational": False,
                "needs_destination": False,
                "needs_origin": False,
                "needs_date_clarification": False,
                "is_confirmation": False,
                "is_ambiguous_confirmation": False,
                "activity_query": classified.activity_query,
                "destination": destination,
                "destination_name": destination_name,
                "origin": origin,
                "origin_name": origin_name,
                "departure_date": dep_date,
                "check_in": check_in_date,
                "check_out": check_out_date,
                "display_currency": display_currency,
                "target_currency": target_currency,
                "budget_cap": budget_cap,
                "used_memory_currency": used_memory_currency,
                "used_memory_origin": used_memory_origin,
                "user_id": user_id,
                "negotiation_round": 0,
                "max_negotiation_rounds": 2,
                "transport_price_ceiling": None,
                "hotel_price_ceiling": None,
                "invoked_nodes": ["parse_intent"],
                "cached_transport_results": [],
                "cached_hotel_results": [],
                "cached_local_guide_results": []
            }

        origin = prior_session.get("origin")
        origin_name = prior_session.get("origin_name")
        return {
            "is_conversion": False,
            "is_planning": False,
            "is_budget_change": False,
            "is_flight_delay": False,
            "is_replan": False,
            "delay_context": None,
            "is_guide_query": False,
            "is_conversational": False,
            "needs_destination": False,
            "needs_origin": True,
            "needs_date_clarification": False,
            "is_confirmation": False,
            "is_ambiguous_confirmation": False,
            "destination": destination,
            "destination_name": destination_name,
            "origin": origin,
            "origin_name": origin_name,
            "departure_date": dep_date,
            "check_in": check_in_date,
            "check_out": check_out_date,
            "display_currency": display_currency,
            "target_currency": target_currency,
            "budget_cap": budget_cap,
            "used_memory_currency": used_memory_currency,
            "used_memory_origin": used_memory_origin,
            "user_id": user_id,
            "invoked_nodes": ["parse_intent"]
        }

    # Cancellation request
    if classified.intent == IntentType.CANCEL_TRIP:
        clear_session_trip(session_id)
        set_pending_slot(session_id, None)
        return {
            "is_conversion": False,
            "is_planning": False,
            "is_budget_change": False,
            "is_flight_delay": False,
            "is_replan": False,
            "is_invalid_budget": False,
            "invalid_budget_message": None,
            "is_cancellation": True,
            "delay_context": None,
            "is_guide_query": False,
            "is_conversational": False,
            "needs_destination": False,
            "needs_origin": False,
            "is_confirmation": False,
            "is_ambiguous_confirmation": False,
            "activity_query": None,
            "origin": None,
            "origin_name": None,
            "destination": None,
            "destination_name": None,
            "departure_date": None,
            "check_in": None,
            "check_out": None,
            "budget_cap": None,
            "target_currency": "USD",
            "display_currency": "USD",
            "used_memory_currency": False,
            "used_memory_origin": False,
            "user_id": user_id,
            "negotiation_round": 0,
            "max_negotiation_rounds": 2,
            "transport_price_ceiling": None,
            "hotel_price_ceiling": None,
            "cost_line_items": [],
            "cached_transport_results": [],
            "cached_hotel_results": [],
            "cached_local_guide_results": [],
            "transport_agent_response": "",
            "hotel_agent_response": "",
            "local_guide_agent_response": "",
            "invoked_nodes": ["parse_intent"]
        }

    # Mid-trip Budget Change: Selective re-entry directly to Budget Agent / check_budget
    if classified.intent == IntentType.MIDTRIP_BUDGET_CHANGE:
        invalidate_confirmation(session_id)
        cached_t = prior_plan.get("transport_results") or []
        cached_h = prior_plan.get("hotel_results") or []
        cached_g = prior_plan.get("local_guide_results") or []
        cost_items = prior_plan.get("cost_line_items") or prior_session.get("cost_line_items") or []

        if structured_cls and getattr(structured_cls, "is_budget_removal", False):
            logger.info("Phase 8 Selective Re-entry: MIDTRIP_BUDGET_CHANGE - User requested budget removal")
            set_pending_slot(session_id, None)
            save_session_trip(session_id, {"budget_cap": None, "budget_requested": False, "budget": None, "budget_status": None, "budget_reconciliation": None})
            return {
                "is_conversion": False,
                "is_planning": False,
                "is_budget_change": True,
                "is_flight_delay": False,
                "is_replan": True,
                "is_invalid_budget": False,
                "invalid_budget_message": None,
                "is_cancellation": False,
                "delay_context": None,
                "is_guide_query": False,
                "is_conversational": False,
                "needs_destination": False,
                "needs_origin": False,
                "is_confirmation": False,
                "is_ambiguous_confirmation": False,
                "activity_query": None,
                "origin": prior_session.get("origin") or "LHR",
                "origin_name": prior_session.get("origin_name") or "London",
                "destination": prior_session.get("destination") or "LIS",
                "destination_name": prior_session.get("destination_name") or "Lisbon",
                "departure_date": prior_session.get("departure_date") or default_dep,
                "check_in": prior_session.get("check_in") or default_checkin,
                "check_out": prior_session.get("check_out") or default_checkout,
                "budget_cap": None,
                "budget_requested": False,
                "target_currency": target_currency,
                "display_currency": display_currency,
                "used_memory_currency": used_memory_currency,
                "used_memory_origin": used_memory_origin,
                "user_id": user_id,
                "negotiation_round": 0,
                "max_negotiation_rounds": 2,
                "transport_price_ceiling": None,
                "hotel_price_ceiling": None,
                "cost_line_items": cost_items,
                "cached_transport_results": cached_t,
                "cached_hotel_results": cached_h,
                "cached_local_guide_results": cached_g,
                "transport_agent_response": "",
                "hotel_agent_response": "",
                "local_guide_agent_response": "",
                "invoked_nodes": ["parse_intent"]
            }

        if classified.budget_cap is not None:
            budget_cap = classified.budget_cap
        elif new_budget_cap is not None:
            budget_cap = new_budget_cap
        else:
            if prior_session.get("pending_slot") == "budget_clarification":
                base_val = prior_session.get("budget_cap") or sum(it["amount"] for it in cost_items) or 500.0
                budget_cap = base_val
            else:
                base_val = prior_session.get("budget_cap") or sum(it["amount"] for it in cost_items) or 500.0
                budget_cap = round(base_val * 0.8, 2)

        # Check if resulting budget cap is unusable (<= 0)
        if budget_cap <= 0:
            drop_details = get_budget_drop_details(
                user_message,
                prior_budget_cap=prior_session.get("budget_cap"),
                prior_total_cost=prior_total
            )
            active_curr = target_currency or display_currency or prior_session.get("target_currency") or "USD"
            curr_sym = CURRENCY_TO_SYMBOL.get(active_curr, f"{active_curr} ")
            if drop_details and drop_details.get("is_relative"):
                drop_amt = drop_details["drop_amt"]
                base_cap = drop_details["base_cap"]
                remaining = drop_details["remaining"]
                sym = drop_details.get("currency_symbol") or curr_sym
                rem_str = f"{sym}{remaining:g}" if remaining >= 0 else f"-{sym}{abs(remaining):g}"
                msg = f"A {sym}{drop_amt:g} drop from your {sym}{base_cap:g} budget leaves {rem_str} — that's not enough for any trip. Would you like to set a different budget, or cancel this trip?"
            else:
                sym = curr_sym
                val_str = f"{sym}{budget_cap:g}" if budget_cap >= 0 else f"-{sym}{abs(budget_cap):g}"
                msg = f"A budget of {val_str} is not enough for any trip. Would you like to set a different budget, or cancel this trip?"

            set_pending_slot(session_id, "budget_clarification")
            return {
                "is_conversion": False,
                "is_planning": False,
                "is_budget_change": False,
                "is_flight_delay": False,
                "is_replan": False,
                "is_invalid_budget": True,
                "invalid_budget_message": msg,
                "is_cancellation": False,
                "delay_context": None,
                "is_guide_query": False,
                "is_conversational": False,
                "needs_destination": False,
                "needs_origin": False,
                "is_confirmation": False,
                "is_ambiguous_confirmation": False,
                "activity_query": None,
                "origin": prior_session.get("origin") or "LHR",
                "origin_name": prior_session.get("origin_name") or "London",
                "destination": prior_session.get("destination") or "LIS",
                "destination_name": prior_session.get("destination_name") or "Lisbon",
                "departure_date": prior_session.get("departure_date") or default_dep,
                "check_in": prior_session.get("check_in") or default_checkin,
                "check_out": prior_session.get("check_out") or default_checkout,
                "budget_cap": budget_cap,
                "budget_requested": True,
                "target_currency": target_currency,
                "display_currency": display_currency,
                "used_memory_currency": used_memory_currency,
                "used_memory_origin": used_memory_origin,
                "user_id": user_id,
                "negotiation_round": 0,
                "max_negotiation_rounds": 2,
                "transport_price_ceiling": None,
                "hotel_price_ceiling": None,
                "cost_line_items": cost_items,
                "cached_transport_results": cached_t,
                "cached_hotel_results": cached_h,
                "cached_local_guide_results": cached_g,
                "transport_agent_response": "",
                "hotel_agent_response": "",
                "local_guide_agent_response": "",
                "invoked_nodes": ["parse_intent"]
            }

        set_pending_slot(session_id, None)
        logger.info(f"Phase 8 Selective Re-entry: MIDTRIP_BUDGET_CHANGE with new cap={budget_cap} {target_currency}")

        return {
            "is_conversion": False,
            "is_planning": False,
            "is_budget_change": True,
            "is_flight_delay": False,
            "is_replan": True,
            "is_invalid_budget": False,
            "invalid_budget_message": None,
            "is_cancellation": False,
            "delay_context": None,
            "is_guide_query": False,
            "is_conversational": False,
            "needs_destination": False,
            "needs_origin": False,
            "is_confirmation": False,
            "is_ambiguous_confirmation": False,
            "activity_query": None,
            "origin": prior_session.get("origin") or "LHR",
            "origin_name": prior_session.get("origin_name") or "London",
            "destination": prior_session.get("destination") or "LIS",
            "destination_name": prior_session.get("destination_name") or "Lisbon",
            "departure_date": prior_session.get("departure_date") or default_dep,
            "check_in": prior_session.get("check_in") or default_checkin,
            "check_out": prior_session.get("check_out") or default_checkout,
            "budget_cap": budget_cap,
            "budget_requested": True,
            "target_currency": target_currency,
            "display_currency": display_currency,
            "used_memory_currency": used_memory_currency,
            "used_memory_origin": used_memory_origin,
            "user_id": user_id,
            "negotiation_round": 0,
            "max_negotiation_rounds": 2,
            "transport_price_ceiling": None,
            "hotel_price_ceiling": None,
            "cost_line_items": cost_items,
            "cached_transport_results": cached_t,
            "cached_hotel_results": cached_h,
            "cached_local_guide_results": cached_g,
            "transport_agent_response": "",
            "hotel_agent_response": "",
            "local_guide_agent_response": "",
            "invoked_nodes": ["parse_intent"]
        }

    # Mid-trip Flight Delay: Selective re-entry directly to Transport Agent
    if classified.intent == IntentType.MIDTRIP_FLIGHT_DELAY:
        invalidate_confirmation(session_id)
        cached_h = prior_plan.get("hotel_results") or []
        cached_g = prior_plan.get("local_guide_results") or []
        cached_t = prior_plan.get("transport_results") or []
        cost_items = prior_plan.get("cost_line_items") or prior_session.get("cost_line_items") or []
        delay_text = classified.delay_context or user_message
        logger.info(f"Phase 8 Selective Re-entry: MIDTRIP_FLIGHT_DELAY ('{delay_text}')")

        delay_hours = None
        delay_m = re.search(r'(\d+(?:\.\d+)?)\s*(?:hours?|hrs?)', delay_text, re.IGNORECASE)
        if delay_m:
            delay_hours = float(delay_m.group(1))
        else:
            min_m = re.search(r'(\d+)\s*(?:mins?|minutes?)', delay_text, re.IGNORECASE)
            if min_m:
                delay_hours = round(float(min_m.group(1)) / 60.0, 2)

        min_departure_after = None
        if delay_hours is not None and cached_t:
            first_dep = cached_t[0].get("departure_at")
            if first_dep:
                try:
                    dt = datetime.fromisoformat(first_dep)
                    min_departure_after = (dt + timedelta(hours=delay_hours)).isoformat()
                except Exception:
                    pass

        return {
            "is_conversion": False,
            "is_planning": False,
            "is_budget_change": False,
            "is_flight_delay": True,
            "is_replan": True,
            "is_invalid_budget": False,
            "invalid_budget_message": None,
            "is_cancellation": False,
            "delay_context": delay_text,
            "delay_hours": delay_hours,
            "min_departure_after": min_departure_after,
            "target_agent": "transport",
            "is_guide_query": False,
            "is_conversational": False,
            "needs_destination": False,
            "needs_origin": False,
            "is_confirmation": False,
            "is_ambiguous_confirmation": False,
            "activity_query": None,
            "origin": prior_session.get("origin") or "LHR",
            "origin_name": prior_session.get("origin_name") or "London",
            "destination": prior_session.get("destination") or "LIS",
            "destination_name": prior_session.get("destination_name") or "Lisbon",
            "departure_date": prior_session.get("departure_date") or default_dep,
            "check_in": prior_session.get("check_in") or default_checkin,
            "check_out": prior_session.get("check_out") or default_checkout,
            "budget_cap": budget_cap,
            "target_currency": target_currency,
            "display_currency": display_currency,
            "used_memory_currency": used_memory_currency,
            "used_memory_origin": used_memory_origin,
            "user_id": user_id,
            "negotiation_round": 0,
            "max_negotiation_rounds": 2,
            "transport_price_ceiling": None,
            "hotel_price_ceiling": None,
            "excluded_hotels": [],
            "prefer_cheaper": False,
            "prior_transport_results": cached_t,
            "prior_hotel_results": cached_h,
            "cost_line_items": cost_items,
            "cached_transport_results": cached_t,
            "cached_hotel_results": cached_h,
            "cached_local_guide_results": cached_g,
            "transport_agent_response": "",
            "hotel_agent_response": "",
            "local_guide_agent_response": "",
            "invoked_nodes": ["parse_intent"]
        }

    # Mid-trip Modification: selective re-entry for cheaper hotel, cheaper flight, different hotel, etc.
    if classified.intent == IntentType.MODIFY_EXISTING:
        invalidate_confirmation(session_id)
        cached_h = prior_plan.get("hotel_results") or []
        cached_g = prior_plan.get("local_guide_results") or []
        cached_t = prior_plan.get("transport_results") or []
        cost_items = prior_plan.get("cost_line_items") or prior_session.get("cost_line_items") or []

        extracted = _extract_plan_details(user_message, session_context=prior_session)
        target_entity = extracted.get("target_entity")
        prefer_cheaper = extracted.get("prefer_cheaper", False)
        prefer_different = extracted.get("prefer_different", False)

        if structured_cls:
            if structured_cls.intent == "modify_hotel":
                target_entity = "hotel"
                if structured_cls.modifier_direction == "cheaper":
                    prefer_cheaper = True
                    prefer_different = False
                else:
                    # Documented default: "unspecified" or "different" -> always treat as "different" (exclude current selection)
                    prefer_different = True
                    prefer_cheaper = False
            elif structured_cls.intent == "modify_flight":
                target_entity = "transport"
                if structured_cls.modifier_direction == "cheaper":
                    prefer_cheaper = True
                    prefer_different = False
                else:
                    prefer_different = True
                    prefer_cheaper = False

        hotel_ceiling = None
        transport_ceiling = None
        excluded_hotels = []
        excluded_transports = []

        lower_msg = user_message.lower()
        if not target_entity:
            if "hotel" in lower_msg or "room" in lower_msg or "stay" in lower_msg or "lodging" in lower_msg or "accommodation" in lower_msg:
                target_entity = "hotel"
            elif "flight" in lower_msg or "transport" in lower_msg or "plane" in lower_msg or "ticket" in lower_msg:
                target_entity = "transport"
            elif prefer_cheaper:
                target_entity = "hotel"

        if target_entity == "hotel":
            target_agent = "hotel"
            if prefer_cheaper and cached_h:
                curr_min_h = min(float(h["total_price"]["amount"]) for h in cached_h)
                hotel_ceiling = round(curr_min_h - 1.0, 2)
                logger.info(f"MODIFY_EXISTING: computed hotel_price_ceiling={hotel_ceiling} (curr_min={curr_min_h})")
            if prefer_different and cached_h:
                excluded_hotels = [h["name"] for h in cached_h]
                logger.info(f"MODIFY_EXISTING: excluded_hotels={excluded_hotels}")
        elif target_entity == "transport":
            target_agent = "transport"
            if prefer_cheaper and cached_t:
                curr_min_t = min(float(t["price"]["amount"]) for t in cached_t)
                transport_ceiling = round(curr_min_t - 1.0, 2)
                logger.info(f"MODIFY_EXISTING: computed transport_price_ceiling={transport_ceiling} (curr_min={curr_min_t})")
            if prefer_different and cached_t:
                excluded_transports = [t.get("carrier") for t in cached_t if t.get("carrier")]
                excluded_transports += [t.get("booking_ref") for t in cached_t if t.get("booking_ref")]
                logger.info(f"MODIFY_EXISTING: excluded_transports={excluded_transports}")
        else:
            target_agent = None

        logger.info(f"Phase 8 Selective Re-entry: MODIFY_EXISTING target={target_agent}, cheaper={prefer_cheaper}, diff={prefer_different}")

        return {
            "is_conversion": False,
            "is_planning": True,
            "is_budget_change": False,
            "is_flight_delay": False,
            "is_replan": True,
            "is_invalid_budget": False,
            "invalid_budget_message": None,
            "is_cancellation": False,
            "delay_context": None,
            "delay_hours": None,
            "min_departure_after": None,
            "is_guide_query": False,
            "is_conversational": False,
            "needs_destination": False,
            "needs_origin": False,
            "is_confirmation": False,
            "is_ambiguous_confirmation": False,
            "activity_query": None,
            "origin": prior_session.get("origin") or "LHR",
            "origin_name": prior_session.get("origin_name") or "London",
            "destination": prior_session.get("destination") or "LIS",
            "destination_name": prior_session.get("destination_name") or "Lisbon",
            "departure_date": dep_date,
            "check_in": check_in_date,
            "check_out": check_out_date,
            "budget_cap": budget_cap,
            "budget_requested": budget_requested,
            "target_currency": target_currency,
            "display_currency": display_currency,
            "used_memory_currency": used_memory_currency,
            "used_memory_origin": used_memory_origin,
            "user_id": user_id,
            "negotiation_round": 0,
            "max_negotiation_rounds": 2,
            "target_agent": target_agent,
            "prefer_cheaper": prefer_cheaper,
            "prefer_different": prefer_different,
            "hotel_price_ceiling": hotel_ceiling,
            "transport_price_ceiling": transport_ceiling,
            "excluded_hotels": excluded_hotels,
            "excluded_transports": excluded_transports,
            "prior_transport_results": cached_t,
            "prior_hotel_results": cached_h,
            "cost_line_items": cost_items,
            "cached_transport_results": cached_t,
            "cached_hotel_results": cached_h,
            "cached_local_guide_results": cached_g,
            "transport_agent_response": "",
            "hotel_agent_response": "",
            "local_guide_agent_response": "",
            "invoked_nodes": ["parse_intent"]
        }

    if classified.intent == IntentType.NEW_TRIP_ROUTE:
        origin = classified.origin or prior_session.get("origin") or frequent_origin_iata or "LHR"
        origin_name = classified.origin_name or prior_session.get("origin_name") or frequent_origin_name or "London"
        destination = classified.destination or prior_session.get("destination", "LIS")
        destination_name = classified.destination_name or prior_session.get("destination_name", "Lisbon")
        set_pending_slot(session_id, None)

        if budget_cap is not None and budget_cap <= 0:
            sym = "$"
            val_str = f"{sym}{budget_cap:g}" if budget_cap >= 0 else f"-{sym}{abs(budget_cap):g}"
            msg = f"A budget of {val_str} is not enough for any trip. Would you like to set a different budget, or cancel this trip?"
            set_pending_slot(session_id, "budget_clarification")
            return {
                "is_conversion": False,
                "is_planning": False,
                "is_budget_change": False,
                "is_flight_delay": False,
                "is_replan": False,
                "is_invalid_budget": True,
                "invalid_budget_message": msg,
                "is_cancellation": False,
                "delay_context": None,
                "is_guide_query": False,
                "is_conversational": False,
                "needs_destination": False,
                "needs_origin": False,
                "is_confirmation": False,
                "is_ambiguous_confirmation": False,
                "activity_query": None,
                "origin": origin,
                "origin_name": origin_name,
                "destination": destination,
                "destination_name": destination_name,
                "departure_date": dep_date,
                "check_in": check_in_date,
                "check_out": check_out_date,
                "budget_cap": budget_cap,
                "target_currency": target_currency,
                "display_currency": display_currency,
                "used_memory_currency": used_memory_currency,
                "used_memory_origin": used_memory_origin,
                "user_id": user_id,
                "negotiation_round": 0,
                "max_negotiation_rounds": 2,
                "transport_price_ceiling": None,
                "hotel_price_ceiling": None,
                "cached_transport_results": [],
                "cached_hotel_results": [],
                "cached_local_guide_results": [],
                "transport_agent_response": "",
                "hotel_agent_response": "",
                "local_guide_agent_response": "",
                "invoked_nodes": ["parse_intent"]
            }

    elif classified.intent == IntentType.ASK_ABOUT_DESTINATION:
        origin = prior_session.get("origin") or frequent_origin_iata
        origin_name = prior_session.get("origin_name") or frequent_origin_name
        destination = classified.destination or prior_session.get("destination")
        destination_name = classified.destination_name or prior_session.get("destination_name")
    elif classified.intent in [IntentType.WEATHER, IntentType.ROAD_CONDITIONS, IntentType.DESTINATION_NEWS, IntentType.SAFETY, IntentType.ASK_SAFETY, IntentType.SHOW_ITINERARY]:
        origin = prior_session.get("origin") or classified.origin or frequent_origin_iata
        origin_name = prior_session.get("origin_name") or classified.origin_name or frequent_origin_name
        destination = prior_session.get("destination") or classified.destination
        destination_name = prior_session.get("destination_name") or classified.destination_name
    else:
        origin = prior_session.get("origin") or frequent_origin_iata
        origin_name = prior_session.get("origin_name") or frequent_origin_name
        destination = prior_session.get("destination")
        destination_name = prior_session.get("destination_name")

    return {
        "is_conversion": (classified.intent == IntentType.CURRENCY_CONVERSION),
        "is_planning": (classified.intent in [IntentType.NEW_TRIP_ROUTE, IntentType.MODIFY_EXISTING]),
        "is_budget_change": False,
        "is_flight_delay": False,
        "is_replan": False,
        "is_invalid_budget": False,
        "invalid_budget_message": None,
        "is_cancellation": False,
        "delay_context": None,
        "is_guide_query": (classified.intent == IntentType.ASK_ABOUT_DESTINATION),
        "is_weather_query": (classified.intent == IntentType.WEATHER),
        "is_road_conditions_query": (classified.intent == IntentType.ROAD_CONDITIONS),
        "is_safety_query": (classified.intent in [IntentType.SAFETY, IntentType.ASK_SAFETY]),
        "is_destination_news_query": (classified.intent == IntentType.DESTINATION_NEWS),
        "is_show_itinerary": (classified.intent == IntentType.SHOW_ITINERARY),
        "is_conversational": (classified.intent == IntentType.GENERAL_CONVERSATION),
        "needs_destination": (classified.intent == IntentType.NO_CONTEXT),
        "needs_origin": False,
        "needs_date_clarification": False,
        "has_past_date": bool(getattr(classified, "has_past_date", False)),
        "needs_currency_clarification": (classified.intent == IntentType.NEED_CURRENCY_CLARIFICATION),
        "is_confirmation": (classified.intent == IntentType.CONFIRM_PLAN),
        "is_ambiguous_confirmation": (classified.intent == IntentType.AMBIGUOUS_CONFIRMATION),
        "activity_query": classified.activity_query,
        "origin": origin,
        "origin_name": origin_name,
        "destination": destination,
        "destination_name": destination_name,
        "departure_date": dep_date,
        "check_in": check_in_date,
        "check_out": check_out_date,
        "budget_cap": budget_cap,
        "budget_requested": budget_requested,
        "target_currency": target_currency,
        "display_currency": display_currency,
        "used_memory_currency": used_memory_currency,
        "used_memory_origin": used_memory_origin,
        "user_id": user_id,
        "negotiation_round": 0,
        "max_negotiation_rounds": 2,
        "transport_price_ceiling": None,
        "hotel_price_ceiling": None,
        "invoked_nodes": ["parse_intent"],
        "cached_transport_results": [],
        "cached_hotel_results": [],
        "cached_local_guide_results": []
    }


async def parse_intent_node(state: PlannerState) -> dict:
    session_id = state.get("session_id", "local")
    with trace_agent_call("planner_agent", "parse_intent", session_id) as span:
        result = await _parse_intent_node_impl(state)
        if result.get("is_flight_delay"):
            span.set_decision(f"Parsed mid-trip flight delay: {result.get('delay_context')} ({result.get('delay_hours')}h)")
        elif result.get("is_budget_change"):
            span.set_decision(f"Parsed mid-trip budget adjustment to {result.get('budget_cap')} {result.get('target_currency')}")
        elif result.get("needs_destination"):
            span.set_decision("Identified trip planning intent; missing destination slot")
        elif result.get("needs_origin"):
            span.set_decision(f"Identified destination {result.get('destination_name')}; prompting for origin")
        elif result.get("is_confirmation"):
            span.set_decision("Identified confirmation intent")
        elif result.get("is_cancellation"):
            span.set_decision("Identified trip cancellation intent")
        elif result.get("prefer_cheaper"):
            tgt = result.get("target_agent", "hotel")
            ceil = result.get(f"{tgt}_price_ceiling")
            curr_sym = result.get("display_currency", "USD")
            ceil_str = f", computed price ceiling {ceil:.2f} {curr_sym} (current cheapest minus 1.0)" if ceil is not None else ""
            span.set_decision(f"Parsed request for cheaper {tgt}{ceil_str}")
        elif result.get("prefer_different"):
            tgt = result.get("target_agent", "hotel")
            span.set_decision(f"Parsed request for different {tgt} (excluding previously recommended options)")
        elif result.get("is_planning"):
            dest = result.get('destination_name') or result.get('destination')
            orig = result.get('origin_name') or result.get('origin')
            cap = result.get('budget_cap')
            curr = result.get('display_currency')
            mem_note = " (applied memory preference)" if (result.get("used_memory_currency") or result.get("used_memory_origin")) else ""
            span.set_decision(f"Trip planning intent: {orig} to {dest}, budget {cap} {curr}{mem_note}")
        elif result.get("is_guide_query"):
            span.set_decision(f"Local guide query for attractions in {result.get('destination_name') or result.get('destination')}")
        else:
            span.set_decision("Parsed user intent and slots")
        return result


# Node 2: Currency conversion standalone invocation
async def call_budget_agent_node(state: PlannerState) -> dict:
    session_id = state.get("session_id", "local")
    with trace_agent_call("budget_agent", "call_budget_agent", session_id) as span:
        user_message = state["messages"][-1].content
        logger.info("LangGraph: calling Budget Agent (convert_currency)")
        response_text = await call_budget_agent_a2a(user_message)
        span.set_decision("Executed currency conversion via budget agent")
        return {
            "budget_agent_response": response_text,
            "messages": [AIMessage(content=response_text)],
            "invoked_nodes": ["call_budget_agent"]
        }


# Node: Guide user when destination is needed (sets pending_slot="destination")
async def render_need_destination_node(state: PlannerState) -> dict:
    session_id = state.get("session_id", "local")
    with trace_agent_call("planner_agent", "render_need_destination", session_id) as span:
        set_pending_slot(session_id, "destination")
        disp_curr = state.get("display_currency", "USD")
        used_mem = state.get("used_memory_currency", False)
        if used_mem:
            msg = (
                f"I'd love to help you plan your trip! Since you usually prefer prices in {disp_curr}, I've set that as your display currency. "
                "Where would you like to travel? (e.g., 'Delhi to Mumbai' or 'London to Tokyo')"
            )
        else:
            msg = (
                f"I'd love to help you plan your trip (and show prices in {disp_curr})! "
                "Where would you like to travel? (e.g., 'Delhi to Mumbai' or 'London to Tokyo')"
            )
        span.set_decision("Prompting user for missing destination slot")
        return {
            "messages": [AIMessage(content=msg)],
            "invoked_nodes": ["render_need_destination"]
        }


# Node: Guide user when origin is needed (sets pending_slot="origin")
async def render_need_origin_node(state: PlannerState) -> dict:
    session_id = state.get("session_id", "local")
    with trace_agent_call("planner_agent", "render_need_origin", session_id) as span:
        set_pending_slot(session_id, "origin")
        save_session_trip(session_id, {
            "destination": state.get("destination"),
            "destination_name": state.get("destination_name"),
            "departure_date": state.get("departure_date"),
            "check_in": state.get("check_in"),
            "check_out": state.get("check_out"),
            "display_currency": state.get("display_currency", "USD"),
            "target_currency": state.get("target_currency", "USD"),
            "pending_slot": "origin"
        })
        dest_name = state.get("destination_name") or state.get("destination") or "your destination"
        frequent_orig = state.get("origin_name")
        if frequent_orig:
            msg = f"Got it, heading to {dest_name}! Are you traveling from {frequent_orig} again, or somewhere else?"
        else:
            msg = f"Got it, heading to {dest_name}! Where are you traveling from?"
        span.set_decision(f"Prompting user for missing origin slot (destination: {dest_name})")
        return {
            "messages": [AIMessage(content=msg)],
            "invoked_nodes": ["render_need_origin"]
        }


# Node: Guide user when travel date clarification is needed (sets pending_slot="date")
async def render_need_date_clarification_node(state: PlannerState) -> dict:
    session_id = state.get("session_id", "local")
    with trace_agent_call("planner_agent", "render_need_date_clarification", session_id) as span:
        set_pending_slot(session_id, "date")
        save_session_trip(session_id, {
            "origin": state.get("origin"),
            "origin_name": state.get("origin_name"),
            "destination": state.get("destination"),
            "destination_name": state.get("destination_name"),
            "display_currency": state.get("display_currency", "USD"),
            "target_currency": state.get("target_currency", "USD"),
            "pending_slot": "date"
        })
        dest_display = state.get("destination_name") or state.get("destination")
        if state.get("has_past_date"):
            dest_clause = f" to {dest_display}" if dest_display and dest_display != "UNK" else ""
            msg = (
                f"That date has already passed. Please choose a future travel date for your trip{dest_clause}."
            )
            span.set_decision("Prompting user to clarify travel date because date is in the past")
        else:
            msg = (
                "I noticed you mentioned travel dates, but I couldn't quite understand them. "
                "Could you please clarify what date you'd like to depart? (e.g. '15 November' or a future date)"
            )
            span.set_decision("Prompting user to clarify ambiguous travel dates")
        return {
            "messages": [AIMessage(content=msg)],
            "invoked_nodes": ["render_need_date_clarification"]
        }


# Node: Conversational response preserving session context
async def render_conversational_node(state: PlannerState) -> dict:
    session_id = state.get("session_id", "local")
    with trace_agent_call("planner_agent", "render_conversational", session_id) as span:
        dest_name = state.get("destination_name") or state.get("destination") or "your destination"
        user_message = state["messages"][-1].content if state.get("messages") else ""

        # Honest handling for jokes / non-trip small talk requests
        if re.search(r'\b(?:joke|jokes|funny|riddle)\b', user_message, re.IGNORECASE):
            set_awaiting_confirmation(session_id, False)
            msg = "I can't tell jokes, but happy to help with your trip! Where would you like to travel, or would you like recommendations for things to do?"
            span.set_decision("Rendered honest non-trip small talk response")
            return {
                "messages": [AIMessage(content=msg)],
                "invoked_nodes": ["render_conversational"]
            }

        # Try generating intelligent travel response with Gemini
        gemini_response = await generate_conversational_travel_response(
            user_message,
            session_context={
                "destination": state.get("destination"),
                "destination_name": dest_name,
                "origin": state.get("origin"),
                "origin_name": state.get("origin_name")
            }
        )

        if gemini_response:
            msg = gemini_response
            span.set_decision(f"Rendered Gemini AI conversational response for {dest_name}")
        else:
            msg = f"Got it! We're currently looking at your trip to {dest_name}. Let me know if you'd like to see attractions, change your budget, or search for different dates!"
            span.set_decision("Rendered conversational context response")

        return {
            "messages": [AIMessage(content=msg)],
            "invoked_nodes": ["render_conversational"]
        }


# Node: Real grounded weather lookup via Open-Meteo
async def render_weather_node(state: PlannerState) -> dict:
    session_id = state.get("session_id", "local")
    with trace_agent_call("planner_agent", "render_weather", session_id) as span:
        user_msg = state["messages"][-1].content if state.get("messages") else ""
        month_regex = r'(?:january|february|march|april|may|june|july|august|september|october|november|december)'
        city_month_matches = re.findall(rf'([a-zA-Z\s]+?)\s+in\s+({month_regex})\b', user_msg, re.IGNORECASE)

        valid_pairs = []
        for cand_c, cand_m in city_month_matches:
            c_clean = cand_c.strip().lstrip("or ").lstrip("vs ").lstrip("and ").strip()
            res_c = validate_and_resolve_city(c_clean)
            if res_c:
                valid_pairs.append((res_c[0], cand_m.capitalize()))

        # Check for comparison query (e.g. "Dubai in July or Delhi in June")
        if len(valid_pairs) >= 2:
            from shared.weather import get_weather_forecast
            summaries = []
            for city_n, m_name in valid_pairs:
                try:
                    w_item = await get_weather_forecast(city_n, start_date=f"in {m_name}")
                    summaries.append(f"📍 **{city_n} ({m_name})**:\n{w_item.get('summary')}")
                except Exception as e:
                    summaries.append(f"📍 **{city_n} ({m_name})**: Weather information unavailable.")

            msg = f"🌤️ **Climate Comparison: {' vs '.join(f'{c} in {m}' for c, m in valid_pairs)}**\n\n" + "\n\n".join(summaries)
            span.set_decision(f"Rendered climate comparison for {valid_pairs}")
            return {
                "messages": [AIMessage(content=msg)],
                "invoked_nodes": ["render_weather"]
            }

        dest_name = None
        start_date = state.get("departure_date")
        end_date = state.get("check_out")

        if len(valid_pairs) == 1:
            dest_name = valid_pairs[0][0]
            start_date = f"in {valid_pairs[0][1]}"
        else:
            # Check if city named in message
            found_place = validate_and_resolve_city(user_msg)
            if not found_place:
                try:
                    from .intent_classifier import _find_city_in_text
                except ImportError:
                    from app.intent_classifier import _find_city_in_text
                found_place = _find_city_in_text(user_msg)
            if found_place:
                dest_name = found_place[0]
            elif re.search(r'\b(?:back\s+home|there\s+at\s+origin|at\s+home|at\s+origin)\b', user_msg, re.IGNORECASE):
                dest_name = state.get("origin_name") or state.get("origin")
            else:
                dest_name = state.get("destination_name") or state.get("destination")

        if not dest_name or dest_name in ["your destination", "UNK", "Unknown City", "NONE"]:
            msg = "Which city would you like to check the weather for?"
            span.set_decision("Prompted for target city for weather check")
            return {
                "messages": [AIMessage(content=msg)],
                "invoked_nodes": ["render_weather"]
            }

        try:
            from shared.weather import get_weather_forecast
            w_res = await get_weather_forecast(dest_name, start_date=start_date, end_date=end_date)
            msg = w_res.get("summary") or f"I can't check live weather conditions for {dest_name} right now."
            span.set_decision(f"Rendered weather forecast for {dest_name}")
        except Exception as exc:
            logger.warning(f"Weather node error: {exc}")
            msg = f"I can't check live weather conditions for {dest_name} right now."
            span.set_decision(f"Failed to fetch weather for {dest_name}: {exc}")

        return {
            "messages": [AIMessage(content=msg)],
            "invoked_nodes": ["render_weather"]
        }


# Node: Live road conditions search & synthesis via Tavily
async def render_road_conditions_node(state: PlannerState) -> dict:
    session_id = state.get("session_id", "local")
    with trace_agent_call("planner_agent", "render_road_conditions", session_id) as span:
        orig = state.get("origin_name") or state.get("origin")
        dest = state.get("destination_name") or state.get("destination")
        user_msg = state["messages"][-1].content if state.get("messages") else ""
        route = f"{orig} to {dest}" if (orig and dest) else (dest or user_msg)

        if not orig and not dest and not user_msg.strip():
            msg = "Which route or highway would you like to check road conditions for?"
            return {
                "messages": [AIMessage(content=msg)],
                "invoked_nodes": ["render_road_conditions"]
            }

        try:
            from shared.tavily_search import search_road_conditions
            rc_res = await search_road_conditions(route=route)
            msg = rc_res.get("summary") or f"I couldn't find current road condition info for this route ({route})."
            span.set_decision(f"Rendered road conditions for {route}")
        except Exception as exc:
            logger.warning(f"Road conditions node error: {exc}")
            msg = f"I can't check live road conditions for {route} right now."
            span.set_decision(f"Failed road conditions for {route}: {exc}")

        return {
            "messages": [AIMessage(content=msg)],
            "invoked_nodes": ["render_road_conditions"]
        }


# Node: Travel-relevant destination news & advisories search via Tavily
async def render_destination_news_node(state: PlannerState) -> dict:
    session_id = state.get("session_id", "local")
    with trace_agent_call("planner_agent", "render_destination_news", session_id) as span:
        user_msg = state["messages"][-1].content if state.get("messages") else ""
        dest_name = state.get("destination_name") or state.get("destination")
        
        # Check if city named in message
        found_place = validate_and_resolve_city(user_msg)
        if not found_place:
            try:
                from .intent_classifier import _find_city_in_text
            except ImportError:
                from app.intent_classifier import _find_city_in_text
            found_place = _find_city_in_text(user_msg)
        if found_place:
            dest_name = found_place[0]

        if not dest_name or dest_name in ["your destination", "UNK", "Unknown City", "NONE"]:
            msg = "Which city would you like to check news and travel advisories for?"
            return {
                "messages": [AIMessage(content=msg)],
                "invoked_nodes": ["render_destination_news"]
            }

        start_date = state.get("departure_date")

        try:
            from shared.tavily_search import search_destination_news
            n_res = await search_destination_news(dest_name, travel_dates=start_date)
            msg = n_res.get("summary") or "I couldn't fetch live news right now. Check a local source before you travel."
            span.set_decision(f"Rendered travel news for {dest_name}")
        except Exception as exc:
            logger.warning(f"Destination news node error: {exc}")
            msg = "I couldn't fetch live news right now. Check a local source before you travel."
            span.set_decision(f"Failed news for {dest_name}: {exc}")

        return {
            "messages": [AIMessage(content=msg)],
            "invoked_nodes": ["render_destination_news"]
        }


# Node: Multi-source destination safety & travel advisories synthesis (Fix C)
async def render_safety_node(state: PlannerState) -> dict:
    session_id = state.get("session_id", "local")
    with trace_agent_call("planner_agent", "render_safety", session_id) as span:
        user_msg = state["messages"][-1].content if state.get("messages") else ""
        dest_name = state.get("destination_name") or state.get("destination")
        
        # Check if city named in message
        found_place = validate_and_resolve_city(user_msg)
        if not found_place:
            try:
                from .intent_classifier import _find_city_in_text
            except ImportError:
                from app.intent_classifier import _find_city_in_text
            found_place = _find_city_in_text(user_msg)
        if found_place:
            dest_name = found_place[0]

        if not dest_name or dest_name in ["your destination", "UNK", "Unknown City", "NONE"]:
            msg = "Which city would you like to check safety information and travel advisories for?"
            return {
                "messages": [AIMessage(content=msg)],
                "invoked_nodes": ["render_safety"]
            }

        start_date = state.get("departure_date")

        # Diagnose Tavily API key loading in planner process
        tavily_key = os.getenv("TAVILY_API_KEY")
        if tavily_key:
            logger.info(f"[TAVILY DIAGNOSTIC] TAVILY_API_KEY is loaded in planner process (length: {len(tavily_key)} chars, prefix: {tavily_key[:4]}...)")
        else:
            logger.warning("[TAVILY DIAGNOSTIC] TAVILY_API_KEY is NOT set in planner process environment (os.getenv returned None/empty). Live advisory search will be bypassed.")

        # 1. Live Tavily web search for safety advisories / unrest / protests
        from shared.tavily_search import search_safety_advisories, get_evergreen_safety_guidance
        safety_res = await search_safety_advisories(dest_name, travel_dates=start_date)

        # 2. Open-Meteo current weather hazards
        weather_hazard_text = "No severe weather hazards detected for the upcoming travel window."
        try:
            from shared.weather import get_weather_forecast
            w_res = await get_weather_forecast(dest_name, start_date="now")
            if w_res.get("alert"):
                weather_hazard_text = w_res.get("alert")
            elif w_res.get("summary"):
                weather_hazard_text = f"Current conditions: {w_res.get('summary')}"
        except Exception as w_exc:
            logger.warning(f"Weather hazard check in safety node failed: {w_exc}")
            weather_hazard_text = "Current weather conditions could not be verified."

        # 3. Evergreen city traveler tips
        evergreen_text = get_evergreen_safety_guidance(dest_name)

        # Assemble synthesis without declaring 'safe' or 'unsafe'
        sections = [f"## Safety & Travel Advisories for {dest_name}\n"]

        # Section 1: Live Advisories
        sections.append("### 🛡️ Live Security & Travel Advisories")
        if (safety_res.get("status") == "ok" or safety_res.get("success")) and safety_res.get("items"):
            for item in safety_res["items"][:5]:
                title = item.get("title", "Advisory Item")
                url = item.get("url", "")
                snippet = item.get("snippet") or item.get("content", "").strip()
                date_str = item.get("date") or item.get("published_date") or ""
                date_prefix = f"({date_str}) " if date_str else ""
                sections.append(f"- **{date_prefix}[{title}]({url})**\n  {snippet}")
        else:
            fail_reason = safety_res.get("reason", "Live advisories unavailable")
            logger.warning(f"Live safety advisory search omitted for {dest_name}: {fail_reason}")
            sections.append("Live advisories weren't available; check your government's travel advisory page.")

        # Section 2: Weather Hazards
        sections.append(f"\n### ⛅ Weather Hazard Status\n{weather_hazard_text}")

        # Section 3: Evergreen Guidance
        sections.append(f"\n### 💡 General Traveler Guidance\n{evergreen_text}")

        # Disclaimer
        sections.append("\n*Note: TripMesh compiles objective situational updates and never issues definitive 'safe' or 'unsafe' verdicts. Travelers should always consult official governmental advisories and local emergency channels prior to travel.*")

        msg = "\n".join(sections)
        span.set_decision(f"Rendered safety synthesis for {dest_name}")

        return {
            "messages": [AIMessage(content=msg)],
            "invoked_nodes": ["render_safety"]
        }



# Node: Re-display existing plan without state mutation
async def render_show_itinerary_node(state: PlannerState) -> dict:
    session_id = state.get("session_id", "local")
    with trace_agent_call("planner_agent", "render_show_itinerary", session_id) as span:
        prior_session = get_session_trip(session_id)
        raw_plan = prior_session.get("confirmed_plan") or prior_session
        plan_data = None
        if raw_plan and (raw_plan.get("transport_results") or raw_plan.get("hotel_results")):
            try:
                plan_data = TripPlanPayload(**{k: v for k, v in raw_plan.items() if k in TripPlanPayload.model_fields}).model_dump()
            except Exception as e:
                logger.warning(f"Failed to parse existing plan into TripPlanPayload: {e}")
                plan_data = raw_plan

        orig_name = prior_session.get("origin_name") or prior_session.get("origin") or "Origin"
        dest_name = prior_session.get("destination_name") or prior_session.get("destination") or "Destination"

        if plan_data:
            msg = f"Here is your itinerary for **{orig_name} → {dest_name}**:"
            span.set_decision(f"Re-displayed current itinerary for {orig_name} -> {dest_name}")
            return {
                "messages": [AIMessage(content=msg)],
                "plan": plan_data,
                "invoked_nodes": ["render_show_itinerary"]
            }
        else:
            msg = "You don't have an active trip plan in this session yet. Where would you like to go?"
            span.set_decision("No itinerary found to re-display")
            return {
                "messages": [AIMessage(content=msg)],
                "invoked_nodes": ["render_show_itinerary"]
            }


# Node: Dedicated Local Guide only query handler
async def call_local_guide_only_node(state: PlannerState) -> dict:
    session_id = state.get("session_id", "local")
    with trace_agent_call("local_guide_agent", "call_local_guide_agent", session_id) as span:
        user_message = state["messages"][-1].content
        dest_name = state.get("destination_name") or state.get("destination", "Destination")
        dest_iata = state.get("destination") or "DEL"
        
        session_context = {
            "destination": dest_iata,
            "destination_name": dest_name,
            "origin": state.get("origin"),
            "origin_name": state.get("origin_name")
        }
        
        logger.info(f"LangGraph: Dedicated Local Guide query for '{dest_name}' ({dest_iata})")
        raw_resp = await call_local_guide_agent_a2a(user_message, session_context=session_context)
        
        try:
            data = json.loads(raw_resp)
            results = data.get("results", [])
            g_msg = data.get("message", "")
        except Exception:
            results = []
            g_msg = ""

        lines = [f"### 🏛️ Local Guide & Attractions for {dest_name}", ""]
        if results:
            for idx, a in enumerate(results, 1):
                cost_info = "Free / Public"
                if a.get("estimated_cost") and a['estimated_cost'].get('amount', 0) > 0:
                    cost_info = f"{a['estimated_cost']['amount']} {a['estimated_cost']['currency']}"
                duration_info = f"{a['estimated_duration_hours']} hours" if a.get("estimated_duration_hours") else "Flexible"
                lines.append(
                    f"{idx}. **{a['name']}** ({duration_info} | {cost_info})\n"
                    f"   {a['description']}\n"
                    f"   📍 Location: {a['location']['name']}\n"
                    f"   🏷️ Tags: {', '.join(a.get('tags', []))}\n"
                    f"   📖 *Source: {a['source_citation']}*\n"
                )
            span.set_decision(f"Retrieved {len(results)} attractions for {dest_name}")
        else:
            if g_msg:
                lines.append(f"*{g_msg}*")
            else:
                lines.append(f"*No strong match found in local guide knowledge base for '{dest_name}'.*")
            span.set_decision(f"No attractions matched in knowledge base for '{dest_name}'")

        combined = "\n".join(lines)
        return {
            "local_guide_agent_response": raw_resp,
            "messages": [AIMessage(content=combined)],
            "invoked_nodes": ["call_local_guide_agent"]
        }


# Node: Re-display itinerary without modifying state or nagging (Fix 5)
async def render_show_itinerary_node(state: PlannerState) -> dict:
    session_id = state.get("session_id", "local")
    with trace_agent_call("planner_agent", "render_show_itinerary", session_id) as span:
        prior_session = get_session_trip(session_id)
        plan_data = prior_session.get("confirmed_plan") or prior_session
        dest_name = plan_data.get("destination_name") or plan_data.get("destination") or "your destination"
        orig_name = plan_data.get("origin_name") or plan_data.get("origin") or "your origin"

        if not plan_data.get("destination"):
            msg = "You don't have an active itinerary yet. Tell me where you'd like to travel!"
            span.set_decision("No active itinerary found to re-display")
            return {
                "messages": [AIMessage(content=msg)],
                "invoked_nodes": ["render_show_itinerary"]
            }

        lines = [f"### 📋 Itinerary: {orig_name} → {dest_name}", ""]
        disp_curr = plan_data.get("display_currency", "USD")

        # Transport
        transports = plan_data.get("transport_results", [])
        if transports:
            lines.append("✈️ **Flights & Transit:**")
            for t in transports[:2]:
                carrier = t.get("carrier", "Flight")
                price = t.get("price", {}).get("amount", "")
                curr = t.get("price", {}).get("currency", disp_curr)
                lines.append(f"- **{carrier}**: {price} {curr} ({t.get('departure_at', '')} → {t.get('arrival_at', '')})")
            lines.append("")

        # Hotels
        hotels = plan_data.get("hotel_results", [])
        if hotels:
            lines.append("🏨 **Accommodations:**")
            for h in hotels[:2]:
                h_name = h.get("name", "Hotel")
                tot = h.get("total_price", {}).get("amount", "")
                curr = h.get("total_price", {}).get("currency", disp_curr)
                lines.append(f"- **{h_name}**: {tot} {curr} total (⭐ {h.get('rating', '')})")
            lines.append("")

        # Local Guide
        guides = plan_data.get("local_guide_results", [])
        if guides:
            lines.append("🏛️ **Top Sights & Activities:**")
            for g in guides[:3]:
                desc = (g.get('description') or '')[:120]
                lines.append(f"- **{g.get('name', '')}**: {desc}...")
            lines.append("")

        recon = plan_data.get("budget_reconciliation")
        if recon and recon.get("total_cost"):
            lines.append(f"💰 **Total Estimated Cost**: **{recon.get('total_cost'):.2f} {disp_curr}**\n")

        lines.append("*(Here is your itinerary as currently planned. Let me know if you would like to book or adjust anything!)*")
        msg = "\n".join(lines)
        span.set_decision(f"Re-displayed current itinerary for {dest_name} without state changes")
        return {
            "messages": [AIMessage(content=msg)],
            "plan": plan_data,
            "invoked_nodes": ["render_show_itinerary"]
        }


# Node: Confirm plan node (Phase 6 Finalization & Phase 7 Memory Recording)
async def confirm_plan_node(state: PlannerState) -> dict:
    session_id = state.get("session_id", "local")
    user_id = state.get("user_id", "")
    with trace_agent_call("planner_agent", "confirm_plan", session_id) as span:
        logger.info(f"LangGraph Confirm Plan Node: Finalizing booking for session '{session_id}' (user: '{user_id}')")
        
        prior_session = get_session_trip(session_id)
        plan_data = prior_session.get("confirmed_plan") or prior_session
        
        confirmed_ctx = confirm_session_trip(session_id, plan_data)
        confirmed_at = confirmed_ctx.get("confirmed_at", "Just now")
        dest_name = confirmed_ctx.get("destination_name") or confirmed_ctx.get("destination") or "your destination"
        
        # Phase 7: Record confirmed trip to long-term memory store
        if user_id:
            record_confirmed_trip(user_id, confirmed_ctx)
        
        msg = (
            f"🎉 **Your trip to {dest_name} is confirmed!**\n\n"
            f"🕒 Confirmed at: **{confirmed_at}**\n\n"
            f"You can download your complete itinerary and calendar export below:\n"
            f"- 📄 [Download PDF Itinerary](http://localhost:8000/api/trip/{session_id}/export.pdf)\n"
            f"- 📅 [Download Calendar (.ics)](http://localhost:8000/api/trip/{session_id}/export.ics)\n\n"
            f"*⚠️ Note: This is a simulated booking confirmation generated for demonstration purposes.*"
        )
        span.set_decision(f"Trip confirmed to {dest_name} at {confirmed_at}. Export links generated.")
        return {
            "messages": [AIMessage(content=msg)],
            "invoked_nodes": ["confirm_plan"]
        }


# Node: Ambiguous confirmation re-prompt
async def render_ambiguous_confirmation_node(state: PlannerState) -> dict:
    session_id = state.get("session_id", "local")
    with trace_agent_call("planner_agent", "render_ambiguous_confirmation", session_id) as span:
        msg = (
            "I'm not sure if you'd like to confirm this plan or make changes.\n\n"
            "👉 Reply **'confirm'** to finalize your booking and download your itinerary, "
            "or tell me what you'd like to adjust (e.g. *'cheaper hotel'*, *'different flight'*, *'change dates'*)."
        )
        span.set_decision("Prompted user to clarify ambiguous confirmation or request changes")
        return {
            "messages": [AIMessage(content=msg)],
            "invoked_nodes": ["render_ambiguous_confirmation"]
        }


# Node: Currency clarification re-prompt
async def render_need_currency_clarification_node(state: PlannerState) -> dict:
    session_id = state.get("session_id", "local")
    with trace_agent_call("planner_agent", "render_need_currency_clarification", session_id) as span:
        msg = (
            "I'm not sure which currency you'd like to use.\n\n"
            "I currently support **USD ($)**, **EUR (€)**, **GBP (£)**, **INR (₹)**, **JPY (¥)**, "
            "**CAD**, **AUD**, **CHF**, **AED**, and **SGD**.\n\n"
            "Which currency would you like to see prices in?"
        )
        span.set_decision("Prompted user for supported currency clarification")
        return {
            "messages": [AIMessage(content=msg)],
            "invoked_nodes": ["render_need_currency_clarification"]
        }


# Node: Handle invalid (zero or negative) budget attempts
async def render_invalid_budget_node(state: PlannerState) -> dict:
    session_id = state.get("session_id", "local")
    with trace_agent_call("planner_agent", "render_invalid_budget", session_id) as span:
        set_pending_slot(session_id, "budget_clarification")
        msg = state.get("invalid_budget_message") or "That budget is not enough for any trip. Would you like to set a different budget, or cancel this trip?"
        span.set_decision("Detected invalid or zero budget; prompted for revision or cancellation")
        span.set_status("no_change")
        return {
            "messages": [AIMessage(content=msg)],
            "invoked_nodes": ["render_invalid_budget"]
        }


# Node: Handle trip cancellation
async def render_trip_cancelled_node(state: PlannerState) -> dict:
    session_id = state.get("session_id", "local")
    with trace_agent_call("planner_agent", "render_trip_cancelled", session_id) as span:
        clear_session_trip(session_id)
        set_pending_slot(session_id, None)
        msg = "Your trip has been cancelled. Let me know whenever you'd like to plan a new trip!"
        span.set_decision("Cancelled trip and cleared active session")
        return {
            "messages": [AIMessage(content=msg)],
            "invoked_nodes": ["render_trip_cancelled"]
        }


# Node 3: Transport Agent invocation
async def call_transport_agent_node(state: PlannerState) -> dict:
    session_id = state.get("session_id", "local")
    with trace_agent_call("transport_agent", "call_transport_agent", session_id) as span:
        user_message = state["messages"][-1].content
        ceiling = state.get("transport_price_ceiling")
        def_dep, def_cin, def_cout = get_default_trip_dates()
        session_context = {
            "origin": state.get("origin"),
            "origin_name": state.get("origin_name"),
            "destination": state.get("destination"),
            "destination_name": state.get("destination_name"),
            "departure_date": state.get("departure_date") or def_dep,
            "check_in": state.get("check_in") or def_cin,
            "check_out": state.get("check_out") or def_cout,
            "delay_context": state.get("delay_context"),
            "delay_hours": state.get("delay_hours"),
            "min_departure_after": state.get("min_departure_after"),
            "prior_transport_results": state.get("prior_transport_results") or state.get("cached_transport_results"),
            "target_entity": state.get("target_entity"),
            "prefer_cheaper": state.get("prefer_cheaper"),
            "prefer_different": state.get("prefer_different"),
            "excluded_transports": state.get("excluded_transports")
        }
        logger.info(f"LangGraph: calling Transport Agent ({session_context['origin']}->{session_context['destination']}, delay_hours={state.get('delay_hours')}, ceiling={ceiling})")
        response_text = await call_transport_agent_a2a(user_message, price_ceiling=ceiling, session_context=session_context)
        
        try:
            t_data = json.loads(response_text)
            t_results = t_data.get("results", [])
            t_no_later = bool(t_data.get("no_later_options", False))
        except Exception:
            t_results = []
            t_no_later = False

        if state.get("is_flight_delay"):
            if t_no_later or not t_results or (session_context.get("prior_transport_results") and t_results == session_context["prior_transport_results"]):
                span.set_decision(f"flight delay: no later options available (delayed by {state.get('delay_hours', 3)} hours)")
                span.set_status("no_change")
            else:
                span.set_decision(f"flight delay: updated {len(t_results)} flight options matching {state.get('delay_hours', 3)}h delay")
        elif ceiling is not None:
            span.set_decision(f"Transport search under ceiling {ceiling:.2f} {state.get('display_currency', 'USD')}: found {len(t_results)} options")
        else:
            span.set_decision(f"Found {len(t_results)} transport options from {session_context['origin']} to {session_context['destination']}")

        return {
            "transport_agent_response": response_text,
            "invoked_nodes": ["call_transport_agent"]
        }

# Node 4: Hotel Agent invocation
async def call_hotel_agent_node(state: PlannerState) -> dict:
    session_id = state.get("session_id", "local")
    with trace_agent_call("hotel_agent", "call_hotel_agent", session_id) as span:
        user_message = state["messages"][-1].content
        ceiling = state.get("hotel_price_ceiling")
        def_dep, def_cin, def_cout = get_default_trip_dates()
        session_context = {
            "origin": state.get("origin"),
            "origin_name": state.get("origin_name"),
            "destination": state.get("destination"),
            "destination_name": state.get("destination_name"),
            "departure_date": state.get("departure_date") or def_dep,
            "check_in": state.get("check_in") or def_cin,
            "check_out": state.get("check_out") or def_cout,
            "excluded_hotels": state.get("excluded_hotels"),
            "target_entity": state.get("target_entity"),
            "prefer_cheaper": state.get("prefer_cheaper"),
            "prefer_different": state.get("prefer_different")
        }
        logger.info(f"LangGraph: calling Hotel Agent ({session_context['destination']}, ceiling={ceiling}, excluded={state.get('excluded_hotels')})")
        response_text = await call_hotel_agent_a2a(user_message, price_ceiling=ceiling, session_context=session_context)
        
        try:
            h_data = json.loads(response_text)
            h_results = h_data.get("results", [])
            h_no_cheaper = bool(h_data.get("no_cheaper_options", False))
        except Exception:
            h_results = []
            h_no_cheaper = False

        if state.get("prefer_cheaper") and state.get("target_agent") == "hotel":
            if h_no_cheaper or not h_results or (state.get("cached_hotel_results") and h_results == state.get("cached_hotel_results")):
                ceiling_str = f" under ceiling {ceiling:.2f} {state.get('display_currency', 'USD')}" if ceiling is not None else ""
                span.set_decision(f"no cheaper hotel found{ceiling_str}")
                span.set_status("no_change")
            else:
                span.set_decision(f"Found {len(h_results)} cheaper hotel options under ceiling" if ceiling is not None else f"Found {len(h_results)} cheaper hotel options")
        elif ceiling is not None:
            span.set_decision(f"Hotel search under ceiling {ceiling:.2f} {state.get('display_currency', 'USD')}: found {len(h_results)} options")
        else:
            span.set_decision(f"Found {len(h_results)} hotel options in {session_context['destination']}")

        return {
            "hotel_agent_response": response_text,
            "invoked_nodes": ["call_hotel_agent"]
        }

# Node 5: Local Guide Agent invocation
async def call_local_guide_agent_node(state: PlannerState) -> dict:
    session_id = state.get("session_id", "local")
    with trace_agent_call("local_guide_agent", "call_local_guide_agent", session_id) as span:
        user_message = state["messages"][-1].content
        def_dep, def_cin, def_cout = get_default_trip_dates()
        session_context = {
            "origin": state.get("origin"),
            "origin_name": state.get("origin_name"),
            "destination": state.get("destination"),
            "destination_name": state.get("destination_name"),
            "departure_date": state.get("departure_date") or def_dep,
            "check_in": state.get("check_in") or def_cin,
            "check_out": state.get("check_out") or def_cout,
        }
        logger.info(f"LangGraph: calling Local Guide Agent ({session_context['destination']})")
        response_text = await call_local_guide_agent_a2a(user_message, session_context=session_context)
        try:
            g_data = json.loads(response_text)
            g_results = g_data.get("results", [])
            span.set_decision(f"Retrieved {len(g_results)} local guide recommendations for {session_context['destination_name'] or session_context['destination']}")
        except Exception:
            span.set_decision(f"Local guide search completed for {session_context['destination_name'] or session_context['destination']}")

        return {
            "local_guide_agent_response": response_text,
            "invoked_nodes": ["call_local_guide_agent"]
        }

# Node 6: Aggregating & extracting cost line items
async def aggregate_results_node(state: PlannerState) -> dict:
    session_id = state.get("session_id", "local")
    with trace_agent_call("planner_agent", "aggregate_results", session_id) as span:
        logger.info("LangGraph Aggregate Node: collecting options and cost line items")
        
        transport_raw = state.get("transport_agent_response", "")
        hotel_raw = state.get("hotel_agent_response", "")
        guide_raw = state.get("local_guide_agent_response", "")
        
        cached_t = state.get("cached_transport_results") or []
        cached_h = state.get("cached_hotel_results") or []
        cached_g = state.get("cached_local_guide_results") or []
        prior_t = state.get("prior_transport_results") or cached_t
        prior_h = state.get("prior_hotel_results") or cached_h

        no_later_flight_found = False
        no_cheaper_hotel_found = False
        no_cheaper_transport_found = False

        t_results = []
        t_no_later = False
        if transport_raw and transport_raw != "{}":
            try:
                t_data = json.loads(transport_raw)
                t_results = t_data.get("results", [])
                t_no_later = bool(t_data.get("no_later_options", False))
            except Exception:
                t_results = []

        def _are_hotels_identical(list_a, list_b):
            if not list_a or not list_b:
                return False
            names_a = [h.get("name") for h in list_a if isinstance(h, dict)]
            names_b = [h.get("name") for h in list_b if isinstance(h, dict)]
            return bool(names_a and names_b and names_a == names_b)

        def _are_transports_identical(list_a, list_b):
            if not list_a or not list_b:
                return False
            sig_a = [(t.get("carrier"), t.get("departure_at")) for t in list_a if isinstance(t, dict)]
            sig_b = [(t.get("carrier"), t.get("departure_at")) for t in list_b if isinstance(t, dict)]
            return bool(sig_a and sig_b and sig_a == sig_b)

        if state.get("is_flight_delay"):
            if t_no_later:
                no_later_flight_found = True
                if not t_results and prior_t:
                    t_results = prior_t
                else:
                    t_results = [t for t in t_results if t.get("booking_ref") != "MOCK-FL-103" and t.get("carrier") != "MockAir Evening"]
            elif not t_results or (prior_t and _are_transports_identical(t_results, prior_t)):
                no_later_flight_found = True
                if not t_results and prior_t:
                    t_results = prior_t
                else:
                    t_results = [t for t in t_results if t.get("booking_ref") != "MOCK-FL-103" and t.get("carrier") != "MockAir Evening"]
            else:
                no_later_flight_found = False
        elif not t_results and cached_t:
            t_results = cached_t
            
        h_results = []
        h_no_cheaper = False
        if hotel_raw and hotel_raw != "{}":
            try:
                h_data = json.loads(hotel_raw)
                h_results = h_data.get("results", [])
                h_no_cheaper = bool(h_data.get("no_cheaper_options", False))
            except Exception:
                h_results = []

        if (state.get("prefer_cheaper") or state.get("prefer_different")) and state.get("target_agent") == "hotel":
            hotels_same = _are_hotels_identical(h_results, prior_h)
            if h_no_cheaper or not h_results or hotels_same:
                no_cheaper_hotel_found = True
            if (not h_results or h_no_cheaper or hotels_same) and prior_h:
                h_results = prior_h
        elif not h_results and cached_h:
            h_results = cached_h

        if (state.get("prefer_cheaper") or state.get("prefer_different")) and state.get("target_agent") == "transport":
            trans_same = _are_transports_identical(t_results, prior_t)
            if not t_results or trans_same:
                no_cheaper_transport_found = True
            if (not t_results or trans_same) and prior_t:
                t_results = prior_t

        g_results = []
        if guide_raw and guide_raw != "{}":
            try:
                g_data = json.loads(guide_raw)
                g_results = g_data.get("results", [])
            except Exception:
                g_results = []
        if not g_results and cached_g:
            g_results = cached_g

        cost_items = []
        if t_results:
            best_t = t_results[0]
            cost_items.append({
                "source_agent": "transport",
                "amount": float(best_t["price"]["amount"]),
                "currency": best_t["price"]["currency"],
                "is_estimated": best_t.get("is_estimated", False),
                "data_quality": best_t.get("data_quality", "live"),
                "description": f"Flight/Transit: {best_t.get('carrier', 'Flight')}"
            })
            
        if h_results:
            best_h = h_results[0]
            cost_items.append({
                "source_agent": "hotel",
                "amount": float(best_h["total_price"]["amount"]),
                "currency": best_h["total_price"]["currency"],
                "is_estimated": best_h.get("is_estimated", False),
                "data_quality": best_h.get("data_quality", "live"),
                "description": f"Hotel: {best_h.get('name', 'Hotel')}"
            })

        for act in g_results:
            if act.get("estimated_cost") and act["estimated_cost"].get("amount", 0) > 0:
                cost_items.append({
                    "source_agent": "local_guide",
                    "amount": float(act["estimated_cost"]["amount"]),
                    "currency": act["estimated_cost"]["currency"],
                    "is_estimated": False,
                    "data_quality": act.get("data_quality", "live"),
                    "description": f"Activity: {act.get('name', 'Attraction')}"
                })

        decision_reasons = []
        if no_later_flight_found:
            decision_reasons.append("flight delay: no later options available")
        if no_cheaper_hotel_found:
            decision_reasons.append("no cheaper hotel found")
        if no_cheaper_transport_found:
            decision_reasons.append("no cheaper transport found")
        if decision_reasons:
            span.set_decision("; ".join(decision_reasons))
            span.set_status("no_change")
        else:
            span.set_decision(f"Aggregated {len(cost_items)} cost items across transport, lodging, and activities")

        return {
            "cost_line_items": cost_items,
            "cached_transport_results": t_results,
            "cached_hotel_results": h_results,
            "cached_local_guide_results": g_results,
            "no_later_flight_found": no_later_flight_found,
            "no_cheaper_hotel_found": no_cheaper_hotel_found,
            "no_cheaper_transport_found": no_cheaper_transport_found,
            "invoked_nodes": ["aggregate_results"]
        }

# Node 7: Budget check & reconciliation
async def check_budget_node(state: PlannerState) -> dict:
    session_id = state.get("session_id", "local")
    with trace_agent_call("budget_agent", "check_budget", session_id) as span:
        budget_requested = state.get("budget_requested", False)
        budget_cap = state.get("budget_cap")
        target_currency = state.get("target_currency", "USD")
        cost_items = state.get("cost_line_items", [])

        if not budget_requested or budget_cap is None or not cost_items:
            logger.info("No budget cap specified, budget not requested, or no cost items found; skipping reconciliation.")
            span.set_decision("No budget cap specified, budget not requested, or no cost items found; skipping reconciliation")
            return {
                "budget_reconciliation": None,
                "invoked_nodes": ["check_budget"]
            }

        logger.info(f"Reconciling budget cap {budget_cap} {target_currency} with {len(cost_items)} cost line items")
        recon_raw = await call_budget_reconciliation_a2a(cost_items, target_currency, budget_cap)
        try:
            recon_data = json.loads(recon_raw)
        except Exception as e:
            logger.error(f"Error parsing budget reconciliation: {e}")
            recon_data = {
                "total_cost": sum(item["amount"] for item in cost_items),
                "currency": target_currency,
                "budget_cap": budget_cap,
                "over_budget": False,
                "overage_amount": 0.0,
                "breakdown_by_agent": {}
            }

        total_val = float(recon_data.get("total_cost") or 0.0)
        overage_val = float(recon_data.get("overage_amount") or 0.0)
        cap_val = float(budget_cap) if budget_cap is not None else total_val

        if recon_data.get("over_budget"):
            span.set_decision(f"Over budget: total {total_val:.2f} {target_currency} exceeds cap {cap_val:.2f} {target_currency} by {overage_val:.2f} {target_currency}; initiating negotiation")
        else:
            span.set_decision(f"Within budget: total {total_val:.2f} {target_currency} under cap {cap_val:.2f} {target_currency}")

        return {
            "budget_reconciliation": recon_data,
            "invoked_nodes": ["check_budget"]
        }

# Node 8: Request cheaper alternatives from dominant contributor
async def request_cheaper_alternatives_node(state: PlannerState) -> dict:
    session_id = state.get("session_id", "local")
    round_num = state.get("negotiation_round", 0) + 1
    recon = state.get("budget_reconciliation", {}) or {}
    breakdown = recon.get("breakdown_by_agent", {})
    overage = recon.get("overage_amount", 50.0)
    user_message = state["messages"][0].content
    def_dep, def_cin, def_cout = get_default_trip_dates()
    session_context = {
        "origin": state.get("origin"),
        "origin_name": state.get("origin_name"),
        "destination": state.get("destination"),
        "destination_name": state.get("destination_name"),
        "departure_date": state.get("departure_date") or def_dep,
        "check_in": state.get("check_in") or def_cin,
        "check_out": state.get("check_out") or def_cout,
    }

    logger.info(f"Negotiation Round {round_num}: over budget by {overage}. Breakdown: {breakdown}")

    negotiable_agents = {k: v for k, v in breakdown.items() if k in ["hotel", "transport"] and v > 0}

    if not negotiable_agents:
        logger.warning("No negotiable agents with room to reduce. Terminating negotiation loop.")
        return {"negotiation_round": round_num}

    dominant_agent = max(negotiable_agents, key=lambda k: negotiable_agents[k])
    curr_cost = negotiable_agents[dominant_agent]
    target_ceiling = max(MIN_NEGOTIATION_PRICE_CEILING, round(curr_cost - (overage * 1.1), 2))

    logger.info(f"Negotiating with dominant agent '{dominant_agent}': reducing from {curr_cost} to ceiling {target_ceiling:.2f}")

    with trace_agent_call("budget_agent", f"request_cheaper_alternatives:{dominant_agent}", session_id) as span:
        disp_curr = state.get("display_currency", "USD")
        span.set_decision(f"Targeting cheaper {dominant_agent}: calculated price ceiling {target_ceiling:.2f} {disp_curr} (current cost {curr_cost:.2f} reduced by overage {overage:.2f})")
        span.update(dominant_agent=dominant_agent, target_ceiling=target_ceiling, overage=overage)

        if dominant_agent == "hotel":
            new_resp = await call_hotel_agent_a2a(user_message, price_ceiling=target_ceiling, session_context=session_context)
            return {
                "negotiation_round": round_num,
                "hotel_agent_response": new_resp,
                "hotel_price_ceiling": target_ceiling,
                "invoked_nodes": [f"request_cheaper_alternatives:{dominant_agent}"]
            }
        else:
            new_resp = await call_transport_agent_a2a(user_message, price_ceiling=target_ceiling, session_context=session_context)
            return {
                "negotiation_round": round_num,
                "transport_agent_response": new_resp,
                "transport_price_ceiling": target_ceiling,
                "invoked_nodes": [f"request_cheaper_alternatives:{dominant_agent}"]
            }


# Node 9: Final plan rendering with Human-in-the-Loop Confirmation Prompt (implementation)
async def _render_final_plan_node_impl(state: PlannerState) -> dict:
    logger.info("LangGraph: Rendering final consolidated trip plan")

    transport_raw = state.get("transport_agent_response", "")
    hotel_raw = state.get("hotel_agent_response", "")
    guide_raw = state.get("local_guide_agent_response", "")
    recon = state.get("budget_reconciliation")
    budget_cap = state.get("budget_cap")
    target_currency = state.get("target_currency", "USD")
    display_currency = state.get("display_currency") or target_currency

    # Blanket Currency Safeguard:
    # Ensure current display_currency preserves session's established currency unless
    # an explicit currency was requested in this turn.
    session_id = state.get("session_id", "local")
    prior_session = get_session_trip(session_id)
    stored_curr = prior_session.get("display_currency")
    user_message = state["messages"][-1].content if state.get("messages") else ""
    explicit_curr_in_msg = resolve_currency_name(user_message)
    if not explicit_curr_in_msg:
        m_curr_sym = re.search(r'([$€£¥₹])\s*-?\d+', user_message)
        if m_curr_sym:
            explicit_curr_in_msg = CURRENCY_SYMBOLS.get(m_curr_sym.group(1))
        else:
            m_curr_code = re.search(r'-?\d+\s*(USD|EUR|GBP|JPY|CAD|AUD|CHF|INR|AED)\b', user_message, re.IGNORECASE)
            if m_curr_code:
                explicit_curr_in_msg = m_curr_code.group(1).upper()

    rounds = state.get("negotiation_round", 0)
    is_replan = state.get("is_replan", False)
    is_flight_delay = state.get("is_flight_delay", False)
    is_budget_change = state.get("is_budget_change", False)

    if is_replan and stored_curr and not explicit_curr_in_msg:
        if display_currency != stored_curr:
            logger.warning(
                f"Blanket Currency Safeguard: display_currency ({display_currency}) differed from stored session "
                f"currency ({stored_curr}) without explicit change request in '{user_message}'. Preserving {stored_curr}."
            )
            display_currency = stored_curr
            target_currency = prior_session.get("target_currency", stored_curr)

    t_results = []
    if transport_raw and transport_raw != "{}":
        try:
            t_data = json.loads(transport_raw)
            t_results = t_data.get("results", [])
        except Exception:
            t_results = []
    if not t_results:
        prior_t = state.get("prior_transport_results") or state.get("cached_transport_results")
        if prior_t:
            t_results = prior_t
    if not t_results:
        t_results = state.get("cached_transport_results") or []
    if state.get("no_later_flight_found") and t_results:
        t_results = [t for t in t_results if t.get("booking_ref") != "MOCK-FL-103" and t.get("carrier") != "MockAir Evening"]
        
    h_results = []
    if state.get("no_cheaper_hotel_found"):
        prior_h = state.get("prior_hotel_results") or state.get("cached_hotel_results")
        if prior_h:
            h_results = prior_h
    if not h_results and hotel_raw and hotel_raw != "{}":
        try:
            h_data = json.loads(hotel_raw)
            h_results = h_data.get("results", [])
        except Exception:
            h_results = []
    if not h_results:
        h_results = state.get("cached_hotel_results") or []

    g_results = []
    g_message = ""
    if guide_raw and guide_raw != "{}":
        try:
            g_data = json.loads(guide_raw)
            g_results = g_data.get("results", [])
            g_message = g_data.get("message", "")
        except Exception:
            g_results = []
            g_message = ""
    if not g_results:
        g_results = state.get("cached_local_guide_results") or []

    session_id = state.get("session_id", "local")
    traced_nodes = get_session_invoked_nodes(session_id)
    if traced_nodes:
        nodes = list(traced_nodes)
        if "render_final_plan" not in nodes:
            nodes.append("render_final_plan")
    else:
        nodes = list(state.get("invoked_nodes", []))
        if "render_final_plan" not in nodes:
            nodes.append("render_final_plan")
    
    # Phase 8 & 9: Observability hook (unified single source of truth)
    logger.info(f"MIDTRIP_INVOKED_NODES: {nodes}")

    no_later_flight = state.get("no_later_flight_found", False)
    no_cheaper_hotel = state.get("no_cheaper_hotel_found", False)
    no_cheaper_transport = state.get("no_cheaper_transport_found", False)
    target_agent = state.get("target_agent")
    prefer_different = state.get("prefer_different", False)
    has_honesty_fallback = (no_later_flight or no_cheaper_hotel or no_cheaper_transport)

    lines = []
    if is_replan and not has_honesty_fallback:
        lines.append("### ⚡ Updated Trip Plan (Mid-Trip Adjustment)")
        if is_flight_delay:
            lines.append("✈️ *Your flight options have been updated to reflect the schedule change. Hotel and activity options remain preserved.*")
        elif is_budget_change:
            cap_banner = f"{budget_cap:.2f} {display_currency}" if budget_cap is not None else display_currency
            lines.append(f"💰 *Your trip plan has been reconciled against the revised budget cap ({cap_banner}). Unaffected sections remain preserved.*")
        elif target_agent == "hotel":
            if prefer_different:
                lines.append("🏨 *Your hotel selection has been updated with alternative accommodation options.*")
            else:
                lines.append("🏨 *Your hotel options have been updated with a cheaper accommodation.*")
        elif target_agent == "transport":
            if prefer_different:
                lines.append("✈️ *Your flight options have been updated with alternative transit options.*")
            else:
                lines.append("✈️ *Your flight options have been updated with cheaper transit.*")
        else:
            lines.append("⚡ *Your itinerary has been updated with your requested adjustments.*")
    else:
        lines.append("### Trip Plan")
        if no_later_flight:
            lines.append("✈️ *No later flight options were found given the delay — here are your original options.*")
        elif no_cheaper_hotel:
            if prefer_different:
                lines.append("🏨 *No alternative hotel options were found — here are your original options.*")
            else:
                lines.append("🏨 *This is already the most affordable option available for your dates.*")
        elif no_cheaper_transport:
            if prefer_different:
                lines.append("✈️ *No alternative flight options were found — here are your original options.*")
            else:
                lines.append("✈️ *This is already the most affordable flight option available for your dates.*")

    if state.get("used_memory_origin", False) and state.get("origin_name"):
        lines.append(f"💡 *Since you usually travel from {state.get('origin_name')}, I've planned this trip starting from {state.get('origin_name')} ({state.get('origin')}).*")
    budget_req = state.get("budget_requested")
    if budget_req is None:
        budget_req = bool(prior_session.get("budget_requested"))

    # Budget Summary Section (guaranteed whenever budget_cap is set or is_budget_change and budget was requested)
    if budget_req and budget_cap is not None:
        total = recon.get("total_cost", 0.0) if recon else sum(float(it.get("amount", 0.0)) for it in state.get("cost_line_items", []))
        curr = (recon.get("currency") if recon else None) or target_currency or display_currency
        over_budget = (total > budget_cap) if not recon else recon.get("over_budget", False)
        overage = max(0.0, total - budget_cap) if not recon else recon.get("overage_amount", 0.0)
        breakdown = recon.get("breakdown_by_agent", {}) if recon else {}

        lines.append("#### 💰 Budget Reconciliation")
        if over_budget:
            lines.append(
                f"⚠️ **Status: Over Budget by {overage:.2f} {curr}** (Total: **{total:.2f} {curr}** / Cap: **{budget_cap:.2f} {curr}**) "
                f"after {rounds} negotiation round(s)."
            )
        else:
            if rounds > 0:
                lines.append(
                    f"✅ **Status: Within Budget** (Total: **{total:.2f} {curr}** / Cap: **{budget_cap:.2f} {curr}**) "
                    f"successfully revised after {rounds} negotiation round(s)!"
                )
            else:
                lines.append(
                    f"✅ **Status: Within Budget** (Total: **{total:.2f} {curr}** / Cap: **{budget_cap:.2f} {curr}**)"
                )

        lines.append(
            f"- ✈️ Transport: **{breakdown.get('transport', 0.0):.2f} {curr}**\n"
            f"- 🏨 Accommodation: **{breakdown.get('hotel', 0.0):.2f} {curr}**\n"
            f"- 🏛️ Local Activities: **{breakdown.get('local_guide', 0.0):.2f} {curr}**"
        )
        if recon and recon.get("conversion_failures"):
            lines.append(f"*(Note: {len(recon['conversion_failures'])} line item(s) could not be converted live and were estimated.)*")
        lines.append("")

    # Flight / Transport Options
    lines.append("#### ✈️ Flight Options")
    if t_results:
        for idx, f in enumerate(t_results, 1):
            quality = "Estimated" if f.get("is_estimated", False) else "Live"
            raw_amt = float(f['price']['amount'])
            raw_curr = f['price']['currency']
            if raw_curr.upper() != display_currency.upper():
                rate = await call_single_currency_rate_a2a(raw_curr, display_currency)
                conv_amt = round(raw_amt * rate, 2)
                price_str = f"**{conv_amt} {display_currency}** ({raw_amt} {raw_curr})"
            else:
                price_str = f"**{raw_amt} {raw_curr}**"

            orig_code = f.get('origin', {}).get('iata') if isinstance(f.get('origin'), dict) else (f.get('origin') or state.get('origin', 'UNK'))
            dest_code = f.get('destination', {}).get('iata') if isinstance(f.get('destination'), dict) else (f.get('destination') or state.get('destination', 'UNK'))
            lines.append(
                f"{idx}. **{f.get('carrier', 'Flight')}** — {orig_code} to {dest_code}\n"
                f"   Departs: {f.get('departure_at', 'TBD')} | Arrives: {f.get('arrival_at', 'TBD')} ({f.get('duration_minutes', 0)} min)\n"
                f"   Price: {price_str} ({quality} fare)"
            )
    else:
        lines.append("No transport options found within the requested constraints.")
        
    lines.append("")

    # Hotel Options
    lines.append("#### 🏨 Hotel Options")
    if h_results:
        for idx, h in enumerate(h_results, 1):
            quality = "Estimated" if h.get("is_estimated", False) else "Live"
            price_pn = h.get('price_per_night') or h.get('total_price') or {'amount': 0.0, 'currency': display_currency}
            price_tot = h.get('total_price') or h.get('price_per_night') or {'amount': 0.0, 'currency': display_currency}
            raw_night = float(price_pn.get('amount', 0.0))
            raw_total = float(price_tot.get('amount', 0.0))
            raw_curr = price_pn.get('currency', display_currency)

            if raw_curr.upper() != display_currency.upper():
                rate = await call_single_currency_rate_a2a(raw_curr, display_currency)
                conv_night = round(raw_night * rate, 2)
                conv_total = round(raw_total * rate, 2)
                price_str = f"**{conv_night} {display_currency} / night** (Total: {conv_total} {display_currency})"
            else:
                price_str = f"**{raw_night} {raw_curr} / night** (Total: {raw_total} {raw_curr})"

            h_name = h.get('name', 'Hotel')
            h_rating = h.get('rating', 4.5)
            h_cin = h.get('check_in', state.get('check_in', ''))
            h_cout = h.get('check_out', state.get('check_out', ''))
            h_amen = ', '.join(h.get('amenities', ['WiFi', 'Air Conditioning']))
            lines.append(
                f"{idx}. **{h_name}** — Rating: {h_rating}⭐\n"
                f"   Check-in: {h_cin} | Check-out: {h_cout}\n"
                f"   Price: {price_str} ({quality} rate)\n"
                f"   Amenities: {h_amen}"
            )
    else:
        lines.append("No hotel options found within the requested constraints.")

    lines.append("")

    # Local Activities
    lines.append("#### 🏛️ Local Activities & Guide Recommendations")
    if g_results:
        for idx, a in enumerate(g_results, 1):
            if a.get("estimated_cost") and a['estimated_cost'].get('amount', 0) > 0:
                raw_act = float(a['estimated_cost']['amount'])
                raw_curr = a['estimated_cost']['currency']
                if raw_curr.upper() != display_currency.upper():
                    rate = await call_single_currency_rate_a2a(raw_curr, display_currency)
                    conv_act = round(raw_act * rate, 2)
                    cost_info = f"{conv_act} {display_currency}"
                else:
                    cost_info = f"{raw_act} {raw_curr}"
            else:
                cost_info = "Free / Public"

            duration_info = f"{a['estimated_duration_hours']} hours" if a.get("estimated_duration_hours") else "Flexible"
            lines.append(
                f"{idx}. **{a['name']}** ({duration_info} | {cost_info})\n"
                f"   {a['description']}\n"
                f"   📍 Location: {a['location']['name']}\n"
                f"   🏷️ Tags: {', '.join(a.get('tags', []))}\n"
                f"   📖 *Source: {a['source_citation']}*"
            )
    else:
        if g_message:
            lines.append(f"*{g_message}*")
        else:
            lines.append("No local activities found for this destination.")
    
    # Human-in-the-Loop Confirmation Prompt
    lines.append("")
    lines.append("---")
    if is_replan and not has_honesty_fallback:
        lines.append(
            "💬 **Ready to finalize your updated itinerary?** Reply **'confirm'** to book these updated details, or tell me what else you'd like to adjust."
        )
    else:
        lines.append(
            "💬 **Ready to finalize?** Reply **'confirm'** to book this itinerary, or tell me what you'd like to adjust (e.g. *'cheaper hotel'*, *'change dates'*)."
        )
    
    combined_response = "\n".join(lines)
    
    # Prepare normalized payloads in display_currency for client UI
    payload_transport_results = []
    if t_results:
        for f in t_results:
            f_item = dict(f)
            raw_amt = float(f.get("price", {}).get("amount", 0.0))
            raw_curr = f.get("price", {}).get("currency", display_currency)
            if raw_curr.upper() != display_currency.upper():
                rate = await call_single_currency_rate_a2a(raw_curr, display_currency)
                conv_amt = round(raw_amt * rate, 2)
                f_item["price"] = {"amount": conv_amt, "currency": display_currency}
            payload_transport_results.append(f_item)

    payload_hotel_results = []
    if h_results:
        for h in h_results:
            h_item = dict(h)
            raw_night = float(h.get("price_per_night", {}).get("amount", 0.0))
            raw_total = float(h.get("total_price", {}).get("amount", 0.0))
            raw_curr = h.get("price_per_night", {}).get("currency", display_currency)
            if raw_curr.upper() != display_currency.upper():
                rate = await call_single_currency_rate_a2a(raw_curr, display_currency)
                conv_night = round(raw_night * rate, 2)
                conv_total = round(raw_total * rate, 2)
                h_item["price_per_night"] = {"amount": conv_night, "currency": display_currency}
                h_item["total_price"] = {"amount": conv_total, "currency": display_currency}
            payload_hotel_results.append(h_item)

    payload_cost_items = []
    total_cost_in_display = 0.0
    for it in state.get("cost_line_items", []):
        it_item = dict(it)
        raw_amt = float(it.get("amount", 0.0))
        raw_curr = it.get("currency", display_currency)
        if raw_curr.upper() != display_currency.upper():
            rate = await call_single_currency_rate_a2a(raw_curr, display_currency)
            conv_amt = round(raw_amt * rate, 2)
            it_item["amount"] = conv_amt
            it_item["currency"] = display_currency
            total_cost_in_display += conv_amt
        else:
            total_cost_in_display += raw_amt
        payload_cost_items.append(it_item)

    payload_recon = recon
    if not payload_recon and payload_cost_items:
        payload_recon = {
            "total_cost": round(total_cost_in_display, 2),
            "currency": display_currency,
            "budget_cap": state.get("budget_cap"),
            "over_budget": False,
            "overage_amount": 0.0,
            "breakdown_by_agent": {}
        }
    elif payload_recon and payload_recon.get("currency", "").upper() != display_currency.upper():
        rate = await call_single_currency_rate_a2a(payload_recon["currency"], display_currency)
        payload_recon = {
            **payload_recon,
            "total_cost": round(float(payload_recon.get("total_cost", 0.0)) * rate, 2),
            "currency": display_currency,
            "budget_cap": round(float(payload_recon["budget_cap"]) * rate, 2) if payload_recon.get("budget_cap") is not None else None,
            "overage_amount": round(float(payload_recon.get("overage_amount", 0.0)) * rate, 2),
        }

    # Save structured confirmed_plan payload in session
    session_id = state.get("session_id", "local")
    current_year = datetime.now().year
    lead_banner = None
    if no_later_flight:
        lead_banner = "✈️ No later flight options were found given the delay — here are your original options."
    elif no_cheaper_hotel:
        lead_banner = "🏨 No alternative hotel options were found — here are your original options." if prefer_different else "🏨 This is already the most affordable option available for your dates."
    elif no_cheaper_transport:
        lead_banner = "✈️ No alternative flight options were found — here are your original options." if prefer_different else "✈️ This is already the most affordable flight option available for your dates."

    # Proactive Real Weather & Destination News Alerts
    weather_alert = None
    weather_tip = None
    news_alert = None
    def_dep, def_cin, def_cout = get_default_trip_dates()
    dest_name_val = state.get("destination_name") or state.get("destination")
    dep_date_val = state.get("departure_date") or def_dep
    ret_date_val = state.get("check_out") or def_cout

    if dest_name_val:
        try:
            import asyncio
            from shared.weather import get_weather_forecast
            w_res = await asyncio.wait_for(
                get_weather_forecast(dest_name_val, start_date=dep_date_val, end_date=ret_date_val),
                timeout=3.0
            )
            weather_alert = w_res.get("alert")
            weather_tip = w_res.get("weather_tip")
        except Exception as exc:
            logger.warning(f"Proactive weather check failed: {exc}")

        try:
            from shared.tavily_search import search_destination_news
            n_res = await search_destination_news(dest_name_val, travel_dates=dep_date_val)
            news_alert = n_res.get("alert")
        except Exception as exc:
            logger.warning(f"Proactive destination news check failed: {exc}")

    if weather_alert:
        combined_response += f"\n\n{weather_alert}"
    if news_alert:
        combined_response += f"\n\n{news_alert}"

    # Single Source of Truth Invariant:
    # Synchronize rendered plan origin/destination with underlying flight search data
    plan_orig = state.get("origin")
    plan_orig_name = state.get("origin_name")
    plan_dest = state.get("destination")
    plan_dest_name = state.get("destination_name")
    if t_results:
        first_t = t_results[0]
        t_orig = first_t.get("origin", {}).get("iata") if isinstance(first_t.get("origin"), dict) else first_t.get("origin")
        t_dest = first_t.get("destination", {}).get("iata") if isinstance(first_t.get("destination"), dict) else first_t.get("destination")
        t_orig_name = first_t.get("origin", {}).get("name") if isinstance(first_t.get("origin"), dict) else None
        t_dest_name = first_t.get("destination", {}).get("name") if isinstance(first_t.get("destination"), dict) else None
        if t_orig and (not plan_orig or plan_orig != t_orig):
            logger.warning(f"Single source of truth invariant: aligning plan origin {plan_orig} to flight search origin {t_orig}")
            plan_orig = t_orig
            if t_orig_name:
                plan_orig_name = t_orig_name
        if t_dest and (not plan_dest or plan_dest != t_dest):
            logger.warning(f"Single source of truth invariant: aligning plan destination {plan_dest} to flight search destination {t_dest}")
            plan_dest = t_dest
            if t_dest_name:
                plan_dest_name = t_dest_name

    budget_req = state.get("budget_requested")
    if budget_req is None:
        budget_req = bool(prior_session.get("budget_requested"))

    plan_budget = None
    plan_budget_status = None
    if budget_req and budget_cap is not None:
        plan_budget = float(budget_cap)
        if payload_recon:
            if payload_recon.get("over_budget"):
                overage = payload_recon.get("overage_amount", 0.0)
                plan_budget_status = f"Over budget by {overage:.2f} {display_currency}"
            else:
                plan_budget_status = "Within Budget Target"
        else:
            plan_budget_status = "Within Budget Target"

    plan_obj = TripPlanPayload(
        origin=plan_orig,
        origin_name=plan_orig_name,
        destination=plan_dest,
        destination_name=plan_dest_name,
        departure_date=state.get("departure_date") or def_dep,
        check_in=state.get("check_in") or def_cin,
        check_out=state.get("check_out") or def_cout,
        budget_cap=state.get("budget_cap") if budget_req else None,
        budget=plan_budget,
        budget_status=plan_budget_status,
        weather_tip=weather_tip,
        target_currency=target_currency,
        display_currency=display_currency,
        transport_results=payload_transport_results,
        hotel_results=payload_hotel_results,
        local_guide_results=g_results,
        budget_reconciliation=payload_recon if budget_req else None,
        cost_line_items=payload_cost_items,
        no_later_flight_found=bool(no_later_flight),
        lead_banner=lead_banner,
        weather_alert=weather_alert,
        news_alert=news_alert,
    )
    plan_payload = plan_obj.model_dump()
    
    save_session_trip(session_id, {
        **plan_payload,
        "budget_requested": budget_req,
        "confirmed_plan": plan_payload,
        "pending_slot": None,
        "is_confirmed": False,
        "awaiting_confirmation": True
    })
    set_awaiting_confirmation(session_id, True)

    return {
        "messages": [AIMessage(content=combined_response)],
        "plan": plan_payload,
        "invoked_nodes": nodes,
        "cached_transport_results": t_results,
        "cached_hotel_results": h_results,
        "cached_local_guide_results": g_results
    }


async def render_final_plan_node(state: PlannerState) -> dict:
    session_id = state.get("session_id", "local")
    with trace_agent_call("planner_agent", "render_final_plan", session_id) as span:
        result = await _render_final_plan_node_impl(state)
        dest = state.get("destination_name") or state.get("destination") or "your destination"
        if state.get("no_later_flight_found"):
            span.set_decision(f"Rendered updated plan with honest flight delay notice (no later flight) for {dest}")
        elif state.get("no_cheaper_hotel_found"):
            span.set_decision(f"Rendered updated plan with honest hotel ceiling notice (no cheaper hotel) for {dest}")
        elif state.get("is_replan"):
            span.set_decision(f"Rendered updated plan reflecting mid-trip adjustments for {dest}")
        else:
            span.set_decision(f"Rendered consolidated trip plan for {dest} awaiting user confirmation")
        return result


# Routing conditions
def route_intent(state: PlannerState) -> list[str] | str:
    if state.get("is_invalid_budget", False):
        return "render_invalid_budget"
    if state.get("is_cancellation", False):
        return "render_trip_cancelled"
    if state.get("is_confirmation", False):
        return "confirm_plan"
    if state.get("is_ambiguous_confirmation", False):
        return "render_ambiguous_confirmation"
    if state.get("is_show_itinerary", False):
        return "render_show_itinerary"
    if state.get("is_conversion", False):
        return "call_budget_agent"
    if state.get("is_guide_query", False):
        return "call_local_guide_only"
    if state.get("is_weather_query", False):
        return "render_weather"
    if state.get("is_road_conditions_query", False):
        return "render_road_conditions"
    if state.get("is_safety_query", False):
        return "render_safety"
    if state.get("is_destination_news_query", False):
        return "render_destination_news"
    if state.get("is_conversational", False):
        return "render_conversational"
    if state.get("needs_origin", False):
        return "render_need_origin"
    if state.get("needs_destination", False):
        return "render_need_destination"
    if state.get("needs_date_clarification", False):
        return "render_need_date_clarification"
    if state.get("needs_currency_clarification", False):
        return "render_need_currency_clarification"
    if state.get("is_flight_delay", False):
        return "call_transport_agent"
    if state.get("is_budget_change", False):
        return "check_budget"
    if state.get("is_planning", False):
        if state.get("target_agent") == "hotel":
            return "call_hotel_agent"
        if state.get("target_agent") == "transport":
            return "call_transport_agent"
        return ["call_transport_agent", "call_hotel_agent", "call_local_guide_agent"]
    return END


def route_budget_decision(state: PlannerState) -> str:
    if not state.get("budget_requested", False) or state.get("budget_cap") is None:
        return "render_final_plan"

    recon = state.get("budget_reconciliation")
    if not recon:
        return "render_final_plan"

    is_over = recon.get("over_budget", False)
    round_num = state.get("negotiation_round", 0)
    max_rounds = state.get("max_negotiation_rounds", 2)

    if is_over and round_num < max_rounds:
        return "request_cheaper_alternatives"
    return "render_final_plan"


# Construct graph
workflow = StateGraph(PlannerState)

# Add nodes
workflow.add_node("parse_intent", parse_intent_node)
workflow.add_node("call_budget_agent", call_budget_agent_node)
workflow.add_node("call_local_guide_only", call_local_guide_only_node)
workflow.add_node("render_weather", render_weather_node)
workflow.add_node("render_road_conditions", render_road_conditions_node)
workflow.add_node("render_destination_news", render_destination_news_node)
workflow.add_node("render_safety", render_safety_node)
workflow.add_node("render_show_itinerary", render_show_itinerary_node)
workflow.add_node("render_conversational", render_conversational_node)
workflow.add_node("render_need_destination", render_need_destination_node)
workflow.add_node("render_need_origin", render_need_origin_node)
workflow.add_node("render_need_date_clarification", render_need_date_clarification_node)
workflow.add_node("render_need_currency_clarification", render_need_currency_clarification_node)
workflow.add_node("confirm_plan", confirm_plan_node)
workflow.add_node("render_ambiguous_confirmation", render_ambiguous_confirmation_node)
workflow.add_node("render_invalid_budget", render_invalid_budget_node)
workflow.add_node("render_trip_cancelled", render_trip_cancelled_node)
workflow.add_node("call_transport_agent", call_transport_agent_node)
workflow.add_node("call_hotel_agent", call_hotel_agent_node)
workflow.add_node("call_local_guide_agent", call_local_guide_agent_node)
workflow.add_node("aggregate_results", aggregate_results_node)
workflow.add_node("check_budget", check_budget_node)
workflow.add_node("request_cheaper_alternatives", request_cheaper_alternatives_node)
workflow.add_node("render_final_plan", render_final_plan_node)

# Set entry
workflow.add_edge(START, "parse_intent")

# Add conditional edges
workflow.add_conditional_edges(
    "parse_intent",
    route_intent,
    {
        "render_invalid_budget": "render_invalid_budget",
        "render_trip_cancelled": "render_trip_cancelled",
        "confirm_plan": "confirm_plan",
        "render_ambiguous_confirmation": "render_ambiguous_confirmation",
        "render_show_itinerary": "render_show_itinerary",
        "render_need_currency_clarification": "render_need_currency_clarification",
        "call_budget_agent": "call_budget_agent",
        "call_local_guide_only": "call_local_guide_only",
        "render_weather": "render_weather",
        "render_road_conditions": "render_road_conditions",
        "render_destination_news": "render_destination_news",
        "render_safety": "render_safety",
        "render_conversational": "render_conversational",
        "render_need_destination": "render_need_destination",
        "render_need_origin": "render_need_origin",
        "render_need_date_clarification": "render_need_date_clarification",
        "call_transport_agent": "call_transport_agent",
        "call_hotel_agent": "call_hotel_agent",
        "call_local_guide_agent": "call_local_guide_agent",
        "check_budget": "check_budget",
        END: END
    }
)

# Parallel fan-out edges to aggregate_results
workflow.add_edge("call_transport_agent", "aggregate_results")
workflow.add_edge("call_hotel_agent", "aggregate_results")
workflow.add_edge("call_local_guide_agent", "aggregate_results")

# Aggregate results flows into check_budget
workflow.add_edge("aggregate_results", "check_budget")

# check_budget conditionally routes to negotiation or final rendering
workflow.add_conditional_edges(
    "check_budget",
    route_budget_decision,
    {
        "request_cheaper_alternatives": "request_cheaper_alternatives",
        "render_final_plan": "render_final_plan"
    }
)

# Loop back from negotiation node to aggregate_results
workflow.add_edge("request_cheaper_alternatives", "aggregate_results")

workflow.add_edge("confirm_plan", END)
workflow.add_edge("render_ambiguous_confirmation", END)
workflow.add_edge("render_show_itinerary", END)
workflow.add_edge("render_need_currency_clarification", END)
workflow.add_edge("render_invalid_budget", END)
workflow.add_edge("render_trip_cancelled", END)
workflow.add_edge("call_budget_agent", END)
workflow.add_edge("call_local_guide_only", END)
workflow.add_edge("render_weather", END)
workflow.add_edge("render_road_conditions", END)
workflow.add_edge("render_destination_news", END)
workflow.add_edge("render_safety", END)
workflow.add_edge("render_conversational", END)
workflow.add_edge("render_need_destination", END)
workflow.add_edge("render_need_origin", END)
workflow.add_edge("render_need_date_clarification", END)
workflow.add_edge("render_final_plan", END)

planner_graph = workflow.compile()

