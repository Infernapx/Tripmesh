import os
import re
import json
import logging
from typing import Optional
from datetime import datetime, timedelta
import httpx
from a2a.client import create_client, ClientConfig
from a2a.types import SendMessageRequest, Message, Part

logger = logging.getLogger(__name__)

BUDGET_AGENT_URL = os.getenv("BUDGET_AGENT_URL", "http://localhost:8005")
TRANSPORT_AGENT_URL = os.getenv("TRANSPORT_AGENT_URL", "http://localhost:8002")
HOTEL_AGENT_URL = os.getenv("HOTEL_AGENT_URL", "http://localhost:8003")
LOCAL_GUIDE_AGENT_URL = os.getenv("LOCAL_GUIDE_AGENT_URL", "http://localhost:8004")

from shared.resolvers import validate_and_resolve_city, resolve_city_to_iata, resolve_dates_from_text, extract_route_cities_from_text

def _clean_city_string(text: str) -> str:
    stop_pattern = r'\b(?:next\s+\w+|this\s+\w+|tomorrow|yesterday|for\s+\d+|on\s+[\d-]+|from\s+[\d-]+|in\s+\d+|budget\b|under\b|with\b|in\s+[a-zA-Z]{3}\b|in\s+rupees\b|in\s+dollars\b|in\s+euros\b|in\s+pounds\b|in\s+yen\b|\d{4}-\d{2}-\d{2}|[$€£¥₹])'
    parts = re.split(stop_pattern, text, flags=re.IGNORECASE)
    return parts[0].strip().strip(",.!?\"'").strip()

def _extract_plan_details(
    text: str,
    price_ceiling: Optional[float] = None,
    session_context: Optional[dict] = None
) -> dict:
    ctx = session_context or {}
    origin_name = ctx.get("origin_name")
    origin_iata = ctx.get("origin")
    dest_name = ctx.get("destination_name")
    dest_iata = ctx.get("destination")
    
    # If origin and destination are already in session_context, treat as single source of truth
    if not (origin_iata and dest_iata):
        route_res = extract_route_cities_from_text(text)
        if route_res:
            (r_orig_name, r_orig_iata), (r_dest_name, r_dest_iata) = route_res
            if not origin_iata:
                origin_name, origin_iata = r_orig_name, r_orig_iata
            if not dest_iata:
                dest_name, dest_iata = r_dest_name, r_dest_iata
        else:
            # 2. Match single destination: e.g. "trip to Paris", "visit Tokyo", "fly to London"
            match_dest = re.search(
                r'\b(?:to|visit|fly\s+to|travel\s+to)\s+([A-Za-z\s]+?)(?:\s+(?:on|from|in|budget|under|with|\d{4}|\$|€|£|¥|₹)|$)',
                text,
                re.IGNORECASE
            )
            if match_dest:
                cleaned_dest = _clean_city_string(match_dest.group(1).strip())
                res_dest = validate_and_resolve_city(cleaned_dest)
                if res_dest:
                    dest_name, dest_iata = res_dest
            
    current_year = datetime.now().year
    parsed_dates, _ = resolve_dates_from_text(text)
    if parsed_dates:
        check_in = parsed_dates["check_in"]
        check_out = parsed_dates["check_out"]
    else:
        check_in = ctx.get("check_in", f"{current_year}-09-10")
        check_out = ctx.get("check_out", f"{current_year}-09-12")
            
    payload = {
        "origin": origin_iata,
        "origin_name": origin_name,
        "destination": dest_iata,
        "destination_name": dest_name,
        "departure_date": check_in,
        "check_in": check_in,
        "check_out": check_out,
        "num_travelers": 1
    }

    # 1. Price ceiling from argument or session context
    ceiling = price_ceiling if price_ceiling is not None else ctx.get("price_ceiling")
    if ceiling is not None:
        payload["price_ceiling"] = float(ceiling)

    # 2. Semantic modifiers (cheaper hotel, cheaper flight, different/adjust hotel, different/update flight, etc.)
    modifier = None
    target_entity = ctx.get("target_entity")
    prefer_cheaper = ctx.get("prefer_cheaper", False)
    prefer_different = ctx.get("prefer_different", False)

    lower_text = text.lower()
    GENERIC_CHANGE_WORDS = r'(?:adjust|change|update|modify|switch|different|another|alternative)'
    if re.search(r'\bcheaper\s+(?:hotel|room|accommodation|stay|lodging)\b', lower_text) or ("cheaper" in lower_text and "hotel" in lower_text):
        modifier = "cheaper_hotel"
        prefer_cheaper = True
        target_entity = "hotel"
    elif re.search(r'\bcheaper\s+(?:flight|ticket|transport|plane|train)\b', lower_text) or ("cheaper" in lower_text and ("flight" in lower_text or "transport" in lower_text)):
        modifier = "cheaper_flight"
        prefer_cheaper = True
        target_entity = "transport"
    elif re.search(rf'\b{GENERIC_CHANGE_WORDS}\s+(?:the\s+|my\s+)?(?:hotel|room|accommodation|stay|lodging)\b', lower_text) or (re.search(rf'\b{GENERIC_CHANGE_WORDS}\b', lower_text) and "hotel" in lower_text):
        modifier = "different_hotel"
        prefer_different = True
        target_entity = "hotel"
    elif re.search(rf'\b{GENERIC_CHANGE_WORDS}\s+(?:the\s+|my\s+)?(?:flight|ticket|transport|plane|transit|train)\b', lower_text) or (re.search(rf'\b{GENERIC_CHANGE_WORDS}\b', lower_text) and ("flight" in lower_text or "transport" in lower_text or "plane" in lower_text)):
        modifier = "different_flight"
        prefer_different = True
        target_entity = "transport"
    elif re.search(r'\bcheaper\b', lower_text):
        modifier = "cheaper"
        prefer_cheaper = True
    elif re.search(rf'\b{GENERIC_CHANGE_WORDS}\b', lower_text):
        modifier = "different"
        prefer_different = True

    if modifier:
        payload["modifier"] = modifier
    if target_entity:
        payload["target_entity"] = target_entity
    if prefer_cheaper:
        payload["prefer_cheaper"] = True
    if prefer_different:
        payload["prefer_different"] = True

    # 3. Flight delay parsing
    delay_hours = None
    delay_source = text
    if not re.search(r'\bdelay', delay_source, re.IGNORECASE) and ctx.get("delay_context"):
        delay_source = str(ctx["delay_context"])
    
    delay_m = re.search(r'(\d+(?:\.\d+)?)\s*(?:hours?|hrs?)', delay_source, re.IGNORECASE)
    if delay_m:
        delay_hours = float(delay_m.group(1))
    else:
        min_m = re.search(r'(\d+)\s*(?:mins?|minutes?)', delay_source, re.IGNORECASE)
        if min_m:
            delay_hours = round(float(min_m.group(1)) / 60.0, 2)
        elif ctx.get("delay_hours") is not None:
            delay_hours = float(ctx["delay_hours"])

    if delay_hours is not None:
        payload["delay_hours"] = delay_hours

    min_dep_after = ctx.get("min_departure_after")
    if not min_dep_after and delay_hours is not None:
        prior_t = ctx.get("prior_transport_results")
        if prior_t and isinstance(prior_t, list) and len(prior_t) > 0:
            first_dep = prior_t[0].get("departure_at")
            if first_dep:
                try:
                    dt = datetime.fromisoformat(first_dep)
                    min_dep_after = (dt + timedelta(hours=delay_hours)).isoformat()
                except Exception:
                    pass

    if min_dep_after:
        payload["min_departure_after"] = min_dep_after

    # 4. Exclusion lists for 'different' modifier
    if ctx.get("excluded_hotels"):
        payload["excluded_hotels"] = ctx["excluded_hotels"]
    if ctx.get("excluded_transports"):
        payload["excluded_transports"] = ctx["excluded_transports"]
    if ctx.get("excluded_options"):
        payload["excluded_options"] = ctx["excluded_options"]

    return payload

async def call_single_currency_rate_a2a(from_curr: str, to_curr: str) -> float:
    """Lightweight single exchange rate fetch over A2A directly from Budget Agent."""
    if from_curr.upper() == to_curr.upper():
        return 1.0
    try:
        httpx_client = httpx.AsyncClient(timeout=10.0)
        client_config = ClientConfig(httpx_client=httpx_client)
        client = await create_client(BUDGET_AGENT_URL, client_config=client_config)
        req = SendMessageRequest(
            message=Message(
                message_id="msg-convert-rate",
                role="ROLE_USER",
                parts=[Part(text=f"convert 1 {from_curr.upper()} to {to_curr.upper()}")]
            )
        )
        response_text = ""
        async for response in client.send_message(req):
            if response.HasField("message"):
                response_text += response.message.parts[0].text
        await httpx_client.aclose()
        # Parse rate: "1.00 USD is equivalent to 85.50 INR"
        m = re.search(r'is\s+equivalent\s+to\s+([\d\.]+)\s+' + re.escape(to_curr.upper()), response_text, re.IGNORECASE)
        if m:
            return float(m.group(1))
    except Exception as e:
        logger.warning(f"Failed to fetch exchange rate {from_curr}->{to_curr}: {e}")
    return 1.0

async def call_budget_agent_a2a(message_text: str) -> str:
    logger.info(f"A2A Client: calling Budget Agent at {BUDGET_AGENT_URL} with: {message_text}")
    try:
        httpx_client = httpx.AsyncClient(timeout=15.0)
        client_config = ClientConfig(httpx_client=httpx_client)
        client = await create_client(BUDGET_AGENT_URL, client_config=client_config)
        req = SendMessageRequest(
            message=Message(
                message_id="msg-planner-to-budget",
                role="ROLE_USER",
                parts=[Part(text=message_text)]
            )
        )
        response_text = ""
        async for response in client.send_message(req):
            if response.HasField("message"):
                response_text += response.message.parts[0].text
        await httpx_client.aclose()
        return response_text
    except httpx.TimeoutException:
        logger.error("A2A call to Budget Agent timed out.")
        return "[Estimated] Conversion timed out. 1 USD is equivalent to approximately 0.90 EUR (Timeout fallback)."
    except Exception as e:
        logger.error(f"Error calling Budget Agent: {e}")
        return f"[Estimated] Error calling Budget Agent over A2A: {e}. Falling back to 1:1 rate."

async def call_budget_reconciliation_a2a(items: list[dict], target_currency: str, budget_cap: Optional[float] = None) -> str:
    logger.info(f"A2A Client: calling Budget Reconciliation at {BUDGET_AGENT_URL} (cap={budget_cap} {target_currency})")
    try:
        httpx_client = httpx.AsyncClient(timeout=15.0)
        client_config = ClientConfig(httpx_client=httpx_client)
        client = await create_client(BUDGET_AGENT_URL, client_config=client_config)
        payload = {
            "skill": "reconcile_budget",
            "items": items,
            "target_currency": target_currency,
            "budget_cap": budget_cap
        }
        req = SendMessageRequest(
            message=Message(
                message_id="msg-planner-to-budget-reconcile",
                role="ROLE_USER",
                parts=[Part(text=json.dumps(payload))]
            )
        )
        response_text = ""
        async for response in client.send_message(req):
            if response.HasField("message"):
                response_text += response.message.parts[0].text
        await httpx_client.aclose()
        return response_text
    except httpx.TimeoutException:
        logger.error("A2A call to Budget Reconciliation timed out.")
        return json.dumps({"total_cost": 0.0, "currency": target_currency, "over_budget": False, "breakdown_by_agent": {}})
    except Exception as e:
        logger.error(f"Error calling Budget Reconciliation: {e}")
        return json.dumps({"total_cost": 0.0, "currency": target_currency, "over_budget": False, "breakdown_by_agent": {}})

async def call_transport_agent_a2a(message_text: str, price_ceiling: Optional[float] = None, session_context: Optional[dict] = None) -> str:
    logger.info(f"A2A Client: calling Transport Agent at {TRANSPORT_AGENT_URL} (price_ceiling={price_ceiling})")
    try:
        httpx_client = httpx.AsyncClient(timeout=15.0)
        client_config = ClientConfig(httpx_client=httpx_client)
        client = await create_client(TRANSPORT_AGENT_URL, client_config=client_config)
        
        details = _extract_plan_details(message_text, price_ceiling=price_ceiling, session_context=session_context)
        req = SendMessageRequest(
            message=Message(
                message_id="msg-planner-to-transport",
                role="ROLE_USER",
                parts=[Part(text=json.dumps(details))]
            )
        )
        response_text = ""
        async for response in client.send_message(req):
            if response.HasField("message"):
                response_text += response.message.parts[0].text
        await httpx_client.aclose()
        return response_text
    except httpx.TimeoutException:
        logger.error("A2A call to Transport Agent timed out.")
        return json.dumps({"success": False, "error": "Transport Agent call timed out (15s limit)."})
    except Exception as e:
        logger.error(f"Error calling Transport Agent: {e}")
        return json.dumps({"success": False, "error": str(e)})

async def call_hotel_agent_a2a(message_text: str, price_ceiling: Optional[float] = None, session_context: Optional[dict] = None) -> str:
    logger.info(f"A2A Client: calling Hotel Agent at {HOTEL_AGENT_URL} (price_ceiling={price_ceiling})")
    try:
        httpx_client = httpx.AsyncClient(timeout=15.0)
        client_config = ClientConfig(httpx_client=httpx_client)
        client = await create_client(HOTEL_AGENT_URL, client_config=client_config)
        
        details = _extract_plan_details(message_text, price_ceiling=price_ceiling, session_context=session_context)
        req = SendMessageRequest(
            message=Message(
                message_id="msg-planner-to-hotel",
                role="ROLE_USER",
                parts=[Part(text=json.dumps(details))]
            )
        )
        response_text = ""
        async for response in client.send_message(req):
            if response.HasField("message"):
                response_text += response.message.parts[0].text
        await httpx_client.aclose()
        return response_text
    except httpx.TimeoutException:
        logger.error("A2A call to Hotel Agent timed out.")
        return json.dumps({"success": False, "error": "Hotel Agent call timed out (15s limit)."})
    except Exception as e:
        logger.error(f"Error calling Hotel Agent: {e}")
        return json.dumps({"success": False, "error": str(e)})

async def call_local_guide_agent_a2a(message_text: str, session_context: Optional[dict] = None) -> str:
    logger.info(f"A2A Client: calling Local Guide Agent at {LOCAL_GUIDE_AGENT_URL}")
    try:
        httpx_client = httpx.AsyncClient(timeout=15.0)
        client_config = ClientConfig(httpx_client=httpx_client)
        client = await create_client(LOCAL_GUIDE_AGENT_URL, client_config=client_config)
        
        details = _extract_plan_details(message_text, session_context=session_context)
        req = SendMessageRequest(
            message=Message(
                message_id="msg-planner-to-guide",
                role="ROLE_USER",
                parts=[Part(text=json.dumps(details))]
            )
        )
        response_text = ""
        async for response in client.send_message(req):
            if response.HasField("message"):
                response_text += response.message.parts[0].text
        await httpx_client.aclose()
        return response_text
    except httpx.TimeoutException:
        logger.error("A2A call to Local Guide Agent timed out.")
        return json.dumps({"success": False, "error": "Local Guide Agent call timed out (15s limit)."})
    except Exception as e:
        logger.error(f"Error calling Local Guide Agent: {e}")
        return json.dumps({"success": False, "error": str(e)})
