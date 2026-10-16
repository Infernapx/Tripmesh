"""
planner_agent/app/intent_classifier.py
--------------------------------------
Structured Intent Classifier & Slot Extractor with Conversational Slot-Filling and Human-in-the-Loop Confirmation.
- Checks awaiting_confirmation when a trip plan has been rendered.
- Checks pending_slot ("destination" or "origin") with direct place validation.
- Falls through to normal classification if the message modifies the plan or introduces a new route.
- Dispatches into confirm_plan, ambiguous_confirmation, new_trip_route, ask_about_destination, modify_existing, etc.
"""

import re
from enum import Enum
from typing import Optional
from pydantic import BaseModel
from shared.resolvers import validate_and_resolve_city, resolve_currency_name, resolve_dates_from_text, is_unresolvable_currency_query, CITY_IATA_MAP, extract_route_cities_from_text

try:
    from .structured_classifier import classify_with_rules, check_fast_path
except ImportError:
    from app.structured_classifier import classify_with_rules, check_fast_path


class IntentType(str, Enum):
    NEW_TRIP_ROUTE = "new_trip_route"
    ASK_ABOUT_DESTINATION = "ask_about_destination"
    MODIFY_EXISTING = "modify_existing"
    MIDTRIP_BUDGET_CHANGE = "midtrip_budget_change"
    MIDTRIP_FLIGHT_DELAY = "midtrip_flight_delay"
    CURRENCY_CONVERSION = "currency_conversion"
    GENERAL_CONVERSATION = "general_conversation"
    SLOT_FILLED_NEED_ORIGIN = "slot_filled_need_origin"
    NO_CONTEXT = "no_context"
    CONFIRM_PLAN = "confirm_plan"
    AMBIGUOUS_CONFIRMATION = "ambiguous_confirmation"
    CANCEL_TRIP = "cancel_trip"
    NEED_DATE_CLARIFICATION = "need_date_clarification"
    NEED_CURRENCY_CLARIFICATION = "need_currency_clarification"
    WEATHER = "weather"
    ASK_WEATHER = "weather"
    ROAD_CONDITIONS = "road_conditions"
    ASK_ROAD_CONDITIONS = "road_conditions"
    DESTINATION_NEWS = "destination_news"
    ASK_NEWS = "destination_news"
    SAFETY = "safety"
    ASK_SAFETY = "safety"
    SHOW_ITINERARY = "show_itinerary"
    SHOW_CURRENT_PLAN = "show_itinerary"


class ClassifiedIntent(BaseModel):
    intent: IntentType
    origin: Optional[str] = None
    origin_name: Optional[str] = None
    destination: Optional[str] = None
    destination_name: Optional[str] = None
    departure_date: Optional[str] = None
    check_in: Optional[str] = None
    check_out: Optional[str] = None
    budget_cap: Optional[float] = None
    target_currency: Optional[str] = None
    display_currency: Optional[str] = None
    activity_query: Optional[str] = None
    cleared_slot: Optional[str] = None
    delay_context: Optional[str] = None
    has_ambiguous_date: bool = False
    has_past_date: bool = False


def _find_city_in_text(text: str) -> Optional[tuple[str, str]]:
    """Finds any canonical or geocoded place mentioned in text."""
    lower = text.lower()
    for city_key, entry in CITY_IATA_MAP.items():
        if re.search(rf'\b{re.escape(city_key)}\b', lower):
            return entry["name"], entry["iata"]
            
    # Skip dynamic geocoding for conversational/confirmation words
    if re.search(r'\b(?:confirm|yes|ok|great|good|fine|cool|thanks|hello|hi|please|book|itinerary|trip|plan|delay|delayed|budget|drop|dropped)\b', lower):
        return None

    # Check for prepositional patterns e.g. "in Reykjavik", "around Paris", "visit Rome"
    m_prep = re.search(r'\b(?:in|around|near|visit|see|explore)\s+([A-Za-z\s]+?)(?:\?|$|\.|\,)', text, re.IGNORECASE)
    if m_prep:
        candidate = m_prep.group(1).strip()
        if candidate and len(candidate.split()) <= 3:
            direct = validate_and_resolve_city(candidate)
            if direct:
                return direct

    # Try dynamic geocoding only for short clean phrases
    if len(text.split()) <= 3:
        direct = validate_and_resolve_city(text)
        if direct:
            return direct

    # Skip dynamic geocoding for conversational/confirmation words
    if re.search(r'\b(?:confirm|conferm|cofirm|comfirm|confrm|yes|ok|great|good|fine|cool|thanks|hello|hi|please|book|itinerary|trip|plan|delay|delayed|budget|drop|dropped)\b', lower):
        return None

    # Common travel queries
    if re.search(r'\b(?:flights?|trains?|hotels?|bus|buses|ferry|car|activities|places|things to do)\b', lower):
        return None

    return None


def _resolve_target_city_for_question(text: str, ctx: dict) -> tuple[Optional[str], Optional[str]]:
    """
    Handler contract:
    Resolve target city: city named in message > session destination > session origin (if user says 'back home'/'at origin').
    Never re-parse origin/destination for the trip itself.
    """
    lower = text.lower()
    if re.search(r'\b(?:back\s+home|there\s+at\s+origin|at\s+home|at\s+origin|my\s+city)\b', lower):
        orig_i = ctx.get("origin")
        orig_n = ctx.get("origin_name") or orig_i
        if orig_i and orig_i != "UNK":
            return orig_n, orig_i

    # Explicit city in text
    found = _find_city_in_text(text)
    if found:
        return found

    # Session destination
    dest_i = ctx.get("destination")
    dest_n = ctx.get("destination_name") or dest_i
    if dest_i and dest_i != "UNK":
        return dest_n, dest_i

    return None, None


def classify_intent(message: str, session_context: Optional[dict] = None) -> ClassifiedIntent:
    text = message.strip()
    ctx = session_context or {}
    awaiting_confirmation = bool(ctx.get("awaiting_confirmation"))
    pending_slot = ctx.get("pending_slot")
    has_prior_destination = bool(ctx.get("destination") and ctx.get("destination") != "UNK")
    has_prior_origin = bool(ctx.get("origin") and ctx.get("origin") != "UNK")

    # Unified date and currency extraction
    parsed_dates, has_ambiguous_date = resolve_dates_from_text(text)
    explicit_curr = resolve_currency_name(text)

    # Fast-path check: confirmations, redisplay, cancellations
    fp = check_fast_path(text)
    if fp:
        if fp.intent == "confirm":
            return ClassifiedIntent(
                intent=IntentType.CONFIRM_PLAN,
                origin=ctx.get("origin"),
                origin_name=ctx.get("origin_name"),
                destination=ctx.get("destination"),
                destination_name=ctx.get("destination_name"),
                display_currency=ctx.get("display_currency", "USD")
            )
        elif fp.intent in ["show_itinerary", "show_current_plan"]:
            return ClassifiedIntent(
                intent=IntentType.SHOW_CURRENT_PLAN,
                origin=ctx.get("origin"),
                origin_name=ctx.get("origin_name"),
                destination=ctx.get("destination"),
                destination_name=ctx.get("destination_name"),
                display_currency=ctx.get("display_currency", "USD")
            )
        elif fp.intent == "cancel":
            return ClassifiedIntent(
                intent=IntentType.CANCEL_TRIP,
                origin=ctx.get("origin"),
                origin_name=ctx.get("origin_name"),
                destination=ctx.get("destination"),
                destination_name=ctx.get("destination_name"),
                display_currency=ctx.get("display_currency", "USD")
            )

    # Priority Check 1: Weather queries (NEVER touch route state)
    is_weather_pattern = bool(re.search(r'\b(?:what(?:\'s|\s+is)?\s+(?:the\s+)?weather|how(?:\'s|\s+is)?\s+(?:the\s+)?weather|will\s+it\s+rain|is\s+it\s+(?:hot|cold|rainy|sunny|warm|freezing)|forecast|temperature|weather\s+(?:like|in|for)|weather)\b', text, re.IGNORECASE))
    if is_weather_pattern:
        dest_n, dest_i = _resolve_target_city_for_question(text, ctx)
        if not dest_i and not dest_n:
            return ClassifiedIntent(intent=IntentType.NO_CONTEXT, activity_query=text)

        # Time resolution: month/date in message > session travel dates
        dep_date = parsed_dates["departure_date"] if parsed_dates else ctx.get("departure_date")
        m_month = re.search(r'\b(?:in\s+)?(january|february|march|april|may|june|july|august|september|october|november|december)\b', text, re.IGNORECASE)
        if m_month:
            dep_date = f"in {m_month.group(1).capitalize()}"

        return ClassifiedIntent(
            intent=IntentType.ASK_WEATHER,
            origin=ctx.get("origin"),
            origin_name=ctx.get("origin_name"),
            destination=ctx.get("destination") or dest_i,
            destination_name=ctx.get("destination_name") or dest_n,
            departure_date=dep_date,
            check_in=parsed_dates["check_in"] if parsed_dates else ctx.get("check_in"),
            check_out=parsed_dates["check_out"] if parsed_dates else ctx.get("check_out"),
            display_currency=ctx.get("display_currency", "USD"),
            activity_query=text
        )

    # Priority Check 2: Road conditions queries (NEVER touch route state)
    if re.search(r'\b(?:road\s+conditions?|road\s+closures?|driving\s+conditions?|traffic\s+between|how\s+are\s+the\s+roads?|highway\s+conditions?|road\s+status)\b', text, re.IGNORECASE):
        dest_n, dest_i = _resolve_target_city_for_question(text, ctx)
        return ClassifiedIntent(
            intent=IntentType.ASK_ROAD_CONDITIONS,
            origin=ctx.get("origin"),
            origin_name=ctx.get("origin_name"),
            destination=ctx.get("destination") or dest_i,
            destination_name=ctx.get("destination_name") or dest_n,
            display_currency=ctx.get("display_currency", "USD"),
            activity_query=text
        )

    # Priority Check 3: Safety / "is it safe to visit X now" queries (NEVER touch route state)
    is_safety_pattern = bool(re.search(
        r'\b(?:is\s+(?:it|there)\s+safe\s+to\s+visit|is\s+it\s+safe|is\s+there\s+safe|safe\s+to\s+(?:travel|visit|go)|how\s+safe\s+is|safety\s+in|safety\s+of|safety\s+advisories?|safety|dangerous|danger|unrest|curfew|scams?|advisory|advisories|should\s+i\s+go)\b',
        text,
        re.IGNORECASE
    ))
    if is_safety_pattern:
        dest_n, dest_i = _resolve_target_city_for_question(text, ctx)
        return ClassifiedIntent(
            intent=IntentType.ASK_SAFETY,
            origin=ctx.get("origin"),
            origin_name=ctx.get("origin_name"),
            destination=ctx.get("destination") or dest_i,
            destination_name=ctx.get("destination_name") or dest_n,
            display_currency=ctx.get("display_currency", "USD"),
            activity_query=text
        )

    # Priority Check 4: Destination news queries (NEVER touch route state)
    if re.search(r'\b(?:(?:any\s+)?news\s+(?:about|in|for|before|i\s+should|to\s+know)|(?:any\s+)?travel\s+news|news\b.*?\b(?:before|go|trip|travel)|what(?:\'s|\s+is)?\s+happening\s+in)\b', text, re.IGNORECASE):
        dest_n, dest_i = _resolve_target_city_for_question(text, ctx)
        return ClassifiedIntent(
            intent=IntentType.ASK_NEWS,
            origin=ctx.get("origin"),
            origin_name=ctx.get("origin_name"),
            destination=ctx.get("destination") or dest_i,
            destination_name=ctx.get("destination_name") or dest_n,
            display_currency=ctx.get("display_currency", "USD"),
            activity_query=text
        )

    # Priority Check 4: Single city + month phrase (e.g. "Dubai in July") OR comparison ("Dubai in July or Delhi in June")
    # MUST NOT overwrite origin/destination, must not trigger Mid-Trip Adjustment!
    month_regex = r'(?:january|february|march|april|may|june|july|august|september|october|november|december)'
    city_month_matches = re.findall(rf'([a-zA-Z\s]+?)\s+in\s+({month_regex})\b', text, re.IGNORECASE)
    if city_month_matches:
        valid_pairs = []
        for cand_c, cand_m in city_month_matches:
            c_clean = cand_c.strip().lstrip("or ").lstrip("vs ").lstrip("and ").strip()
            res_c = validate_and_resolve_city(c_clean)
            if res_c:
                valid_pairs.append((res_c[0], res_c[1], cand_m.capitalize()))

        if valid_pairs:
            # If user has an active session, it's a read-only destination/weather inquiry
            if has_prior_destination:
                return ClassifiedIntent(
                    intent=IntentType.ASK_WEATHER,
                    origin=ctx.get("origin"),
                    origin_name=ctx.get("origin_name"),
                    destination=ctx.get("destination"),
                    destination_name=ctx.get("destination_name"),
                    departure_date=f"in {valid_pairs[0][2]}",
                    activity_query=text,
                    display_currency=ctx.get("display_currency", "USD")
                )
            else:
                # Without prior destination, if single city, guide towards planning
                if len(valid_pairs) == 1:
                    c_name, c_iata, c_m = valid_pairs[0]
                    return ClassifiedIntent(
                        intent=IntentType.SLOT_FILLED_NEED_ORIGIN,
                        destination=c_iata,
                        destination_name=c_name,
                        departure_date=parsed_dates["departure_date"] if parsed_dates else None,
                        display_currency="USD",
                        target_currency="USD"
                    )
                else:
                    return ClassifiedIntent(
                        intent=IntentType.ASK_WEATHER,
                        activity_query=text,
                        display_currency="USD"
                    )


    # 0. UNCONDITIONAL PRIORITY: Complete "X to Y" route statement
    # A complete route statement is NEVER ambiguous with modifiers or awaiting confirmation.
    # It must unconditionally classify as NEW_TRIP_ROUTE and clear prior session baggage.
    route_res = extract_route_cities_from_text(text)
    if route_res:
        (orig_name, orig_iata), (dest_name, dest_iata) = route_res
        if has_ambiguous_date or (parsed_dates and parsed_dates.get("is_past")):
            return ClassifiedIntent(
                intent=IntentType.NEED_DATE_CLARIFICATION,
                origin=orig_iata,
                origin_name=orig_name,
                destination=dest_iata,
                destination_name=dest_name,
                has_ambiguous_date=has_ambiguous_date,
                has_past_date=bool(parsed_dates and parsed_dates.get("is_past")),
                display_currency=explicit_curr or "USD",
                target_currency=explicit_curr or "USD",
                cleared_slot=pending_slot
            )
        return ClassifiedIntent(
            intent=IntentType.NEW_TRIP_ROUTE,
            origin=orig_iata,
            origin_name=orig_name,
            destination=dest_iata,
            destination_name=dest_name,
            departure_date=parsed_dates["departure_date"] if parsed_dates else None,
            check_in=parsed_dates["check_in"] if parsed_dates else None,
            check_out=parsed_dates["check_out"] if parsed_dates else None,
            display_currency=explicit_curr or "USD",
            target_currency=explicit_curr or "USD",
            cleared_slot=pending_slot
        )

    # Mid-trip change patterns (Phase 8)
    flight_delay_pattern = re.search(
        r'\b(?:flight|plane|flight\s+to\s+[a-zA-Z\s]+)\s+(?:got\s+|was\s+|is\s+)?(?:delayed|pushed|rescheduled|moved)(?:\s+(?:by|to|around)\s+[\w\s]+)?\b|\b(?:delayed|rescheduled|pushed)\s+(?:by\s+)?\d+\s*(?:hours?|hrs?|mins?|minutes?)\b|\bflight\s+delay\b',
        text,
        re.IGNORECASE
    )
    budget_drop_pattern = re.search(
        r'\b(?:budget\s+(?:just\s+)?(?:dropped|decreased|cut|reduced|fell)|cut\s+(?:our\s+|my\s+|the\s+)?(?:costs?|budget)|lower\s+(?:our\s+|my\s+|the\s+)?budget|reduce\s+(?:our\s+|my\s+|the\s+)?budget|need\s+to\s+cut\s+costs|keep\s+it\s+under\s+(\d+|\$|€|£|¥|₹)|(?:my\s+)?budget\s+(?:is\s+now|now\s+is|is)\s+(\d+|\$|€|£|¥|₹)|(?:let\'?s\s+say|say|how\s+about|make\s+it)\s+([$€£¥₹])?\s*(\d+(?:,\d+)*(?:\.\d+)?)\s*(?:instead)?|(\d+(?:,\d+)*(?:\.\d+)?)\s*(?:USD|EUR|GBP|JPY|CAD|AUD|CHF|INR|AED)?\s+instead)\b',
        text,
        re.IGNORECASE
    )

    # 1. Human-in-the-Loop: Check awaiting_confirmation first
    if awaiting_confirmation:
        if is_unresolvable_currency_query(text):
            return ClassifiedIntent(
                intent=IntentType.NEED_CURRENCY_CLARIFICATION,
                origin=ctx.get("origin"),
                origin_name=ctx.get("origin_name"),
                destination=ctx.get("destination"),
                destination_name=ctx.get("destination_name"),
                display_currency=ctx.get("display_currency", "USD"),
                target_currency=ctx.get("target_currency", "USD")
            )

        structured = classify_with_rules(text, session_context=ctx)

        if structured.intent == "confirm":
            return ClassifiedIntent(
                intent=IntentType.CONFIRM_PLAN,
                origin=ctx.get("origin"),
                origin_name=ctx.get("origin_name"),
                destination=ctx.get("destination"),
                destination_name=ctx.get("destination_name"),
                display_currency=ctx.get("display_currency", "USD")
            )
        elif structured.intent == "show_itinerary":
            return ClassifiedIntent(
                intent=IntentType.SHOW_ITINERARY,
                origin=ctx.get("origin"),
                origin_name=ctx.get("origin_name"),
                destination=ctx.get("destination"),
                destination_name=ctx.get("destination_name"),
                display_currency=ctx.get("display_currency", "USD")
            )
        elif structured.intent == "check_weather":
            return ClassifiedIntent(
                intent=IntentType.WEATHER,
                origin=ctx.get("origin"),
                origin_name=ctx.get("origin_name"),
                destination=ctx.get("destination"),
                destination_name=ctx.get("destination_name"),
                display_currency=ctx.get("display_currency", "USD"),
                activity_query=text
            )
        elif structured.intent == "check_road_conditions":
            return ClassifiedIntent(
                intent=IntentType.ROAD_CONDITIONS,
                origin=ctx.get("origin"),
                origin_name=ctx.get("origin_name"),
                destination=ctx.get("destination"),
                destination_name=ctx.get("destination_name"),
                display_currency=ctx.get("display_currency", "USD"),
                activity_query=text
            )
        elif structured.intent in ["check_safety", "ask_safety"]:
            return ClassifiedIntent(
                intent=IntentType.ASK_SAFETY,
                origin=ctx.get("origin"),
                origin_name=ctx.get("origin_name"),
                destination=ctx.get("destination"),
                destination_name=ctx.get("destination_name"),
                display_currency=ctx.get("display_currency", "USD"),
                activity_query=text
            )
        elif structured.intent in ["check_destination_news", "ask_news"]:
            return ClassifiedIntent(
                intent=IntentType.DESTINATION_NEWS,
                origin=ctx.get("origin"),
                origin_name=ctx.get("origin_name"),
                destination=ctx.get("destination"),
                destination_name=ctx.get("destination_name"),
                display_currency=ctx.get("display_currency", "USD"),
                activity_query=text
            )
        elif structured.intent == "cancel":
            return ClassifiedIntent(
                intent=IntentType.CANCEL_TRIP,
                origin=ctx.get("origin"),
                origin_name=ctx.get("origin_name"),
                destination=ctx.get("destination"),
                destination_name=ctx.get("destination_name"),
                display_currency=ctx.get("display_currency", "USD")
            )
        elif structured.intent == "modify_currency":
            if structured.currency:
                return ClassifiedIntent(
                    intent=IntentType.MODIFY_EXISTING,
                    origin=ctx.get("origin"),
                    origin_name=ctx.get("origin_name"),
                    destination=ctx.get("destination"),
                    destination_name=ctx.get("destination_name"),
                    display_currency=structured.currency,
                    target_currency=structured.currency
                )
            else:
                return ClassifiedIntent(
                    intent=IntentType.NEED_CURRENCY_CLARIFICATION,
                    origin=ctx.get("origin"),
                    origin_name=ctx.get("origin_name"),
                    destination=ctx.get("destination"),
                    destination_name=ctx.get("destination_name"),
                    display_currency=ctx.get("display_currency", "USD"),
                    target_currency=ctx.get("target_currency", "USD")
                )
        elif structured.intent == "modify_budget":
            return ClassifiedIntent(
                intent=IntentType.MIDTRIP_BUDGET_CHANGE,
                origin=ctx.get("origin"),
                origin_name=ctx.get("origin_name"),
                destination=ctx.get("destination"),
                destination_name=ctx.get("destination_name"),
                budget_cap=structured.budget_amount,
                display_currency=structured.currency or ctx.get("display_currency"),
                target_currency=structured.currency or ctx.get("target_currency")
            )
        elif structured.intent in ("modify_hotel", "modify_flight", "modify_dates"):
            if structured.intent == "modify_flight" and bool(re.search(r'\b(?:delay|delayed|rescheduled)\b', text, re.IGNORECASE)):
                return ClassifiedIntent(
                    intent=IntentType.MIDTRIP_FLIGHT_DELAY,
                    origin=ctx.get("origin"),
                    origin_name=ctx.get("origin_name"),
                    destination=ctx.get("destination"),
                    destination_name=ctx.get("destination_name"),
                    delay_context=text,
                    display_currency=ctx.get("display_currency"),
                    target_currency=ctx.get("target_currency")
                )
            return ClassifiedIntent(
                intent=IntentType.MODIFY_EXISTING,
                origin=ctx.get("origin"),
                origin_name=ctx.get("origin_name"),
                destination=ctx.get("destination"),
                destination_name=ctx.get("destination_name"),
                display_currency=ctx.get("display_currency"),
                target_currency=ctx.get("target_currency")
            )
        elif structured.intent == "ask_about_destination":
            return ClassifiedIntent(
                intent=IntentType.ASK_ABOUT_DESTINATION,
                origin=ctx.get("origin"),
                origin_name=ctx.get("origin_name"),
                destination=ctx.get("destination"),
                destination_name=ctx.get("destination_name"),
                activity_query=text,
                display_currency=ctx.get("display_currency", "USD")
            )
        elif structured.intent == "general_conversation":
            return ClassifiedIntent(
                intent=IntentType.GENERAL_CONVERSATION,
                origin=ctx.get("origin"),
                origin_name=ctx.get("origin_name"),
                destination=ctx.get("destination"),
                destination_name=ctx.get("destination_name"),
                display_currency=ctx.get("display_currency", "USD")
            )
        elif structured.intent == "unclear":
            return ClassifiedIntent(
                intent=IntentType.AMBIGUOUS_CONFIRMATION,
                origin=ctx.get("origin"),
                origin_name=ctx.get("origin_name"),
                destination=ctx.get("destination"),
                destination_name=ctx.get("destination_name"),
                display_currency=ctx.get("display_currency", "USD")
            )

    # 2. Conversational Slot-Filling: Check pending_slot
    if pending_slot == "destination":
        res_place = validate_and_resolve_city(text)
        if res_place:
            dest_name, dest_iata = res_place
            if has_prior_origin:
                if has_ambiguous_date or (parsed_dates and parsed_dates.get("is_past")):
                    return ClassifiedIntent(
                        intent=IntentType.NEED_DATE_CLARIFICATION,
                        origin=ctx.get("origin"),
                        origin_name=ctx.get("origin_name"),
                        destination=dest_iata,
                        destination_name=dest_name,
                        has_ambiguous_date=has_ambiguous_date,
                        has_past_date=bool(parsed_dates and parsed_dates.get("is_past")),
                        display_currency=explicit_curr or ctx.get("display_currency", "USD"),
                        target_currency=explicit_curr or ctx.get("target_currency", "USD"),
                        cleared_slot="destination"
                    )
                return ClassifiedIntent(
                    intent=IntentType.NEW_TRIP_ROUTE,
                    origin=ctx.get("origin"),
                    origin_name=ctx.get("origin_name"),
                    destination=dest_iata,
                    destination_name=dest_name,
                    departure_date=parsed_dates["departure_date"] if parsed_dates else ctx.get("departure_date"),
                    check_in=parsed_dates["check_in"] if parsed_dates else ctx.get("check_in"),
                    check_out=parsed_dates["check_out"] if parsed_dates else ctx.get("check_out"),
                    display_currency=explicit_curr or ctx.get("display_currency", "USD"),
                    target_currency=explicit_curr or ctx.get("target_currency", "USD"),
                    cleared_slot="destination"
                )
            else:
                return ClassifiedIntent(
                    intent=IntentType.SLOT_FILLED_NEED_ORIGIN,
                    destination=dest_iata,
                    destination_name=dest_name,
                    departure_date=parsed_dates["departure_date"] if parsed_dates else ctx.get("departure_date"),
                    check_in=parsed_dates["check_in"] if parsed_dates else ctx.get("check_in"),
                    check_out=parsed_dates["check_out"] if parsed_dates else ctx.get("check_out"),
                    display_currency=explicit_curr or ctx.get("display_currency", "USD"),
                    target_currency=explicit_curr or ctx.get("target_currency", "USD"),
                    cleared_slot="destination"
                )

    if pending_slot == "origin":
        res_place = validate_and_resolve_city(text)
        if res_place:
            orig_name, orig_iata = res_place
            dest_iata = ctx.get("destination")
            dest_name = ctx.get("destination_name")
            if not dest_iata or dest_iata in ["UNK", "Unknown City", "NONE"]:
                return ClassifiedIntent(intent=IntentType.NO_CONTEXT)
            if has_ambiguous_date or (parsed_dates and parsed_dates.get("is_past")):
                return ClassifiedIntent(
                    intent=IntentType.NEED_DATE_CLARIFICATION,
                    origin=orig_iata,
                    origin_name=orig_name,
                    destination=dest_iata,
                    destination_name=dest_name,
                    has_ambiguous_date=has_ambiguous_date,
                    has_past_date=bool(parsed_dates and parsed_dates.get("is_past")),
                    display_currency=explicit_curr or ctx.get("display_currency", "USD"),
                    target_currency=explicit_curr or ctx.get("target_currency", "USD"),
                    cleared_slot="origin"
                )
            return ClassifiedIntent(
                intent=IntentType.NEW_TRIP_ROUTE,
                origin=orig_iata,
                origin_name=orig_name,
                destination=dest_iata,
                destination_name=dest_name,
                departure_date=parsed_dates["departure_date"] if parsed_dates else ctx.get("departure_date"),
                check_in=parsed_dates["check_in"] if parsed_dates else ctx.get("check_in"),
                check_out=parsed_dates["check_out"] if parsed_dates else ctx.get("check_out"),
                display_currency=explicit_curr or ctx.get("display_currency", "USD"),
                target_currency=explicit_curr or ctx.get("target_currency", "USD"),
                cleared_slot="origin"
            )

    if pending_slot == "date":
        if parsed_dates and not has_ambiguous_date:
            if parsed_dates.get("is_past"):
                return ClassifiedIntent(
                    intent=IntentType.NEED_DATE_CLARIFICATION,
                    origin=ctx.get("origin"),
                    origin_name=ctx.get("origin_name"),
                    destination=ctx.get("destination"),
                    destination_name=ctx.get("destination_name"),
                    has_past_date=True,
                    display_currency=explicit_curr or ctx.get("display_currency", "USD"),
                    target_currency=explicit_curr or ctx.get("target_currency", "USD")
                )
            dest_iata = ctx.get("destination")
            dest_name = ctx.get("destination_name")
            orig_iata = ctx.get("origin")
            orig_name = ctx.get("origin_name")
            if not dest_iata or not orig_iata or dest_iata in ["UNK", "Unknown City"] or orig_iata in ["UNK", "Unknown City"]:
                return ClassifiedIntent(intent=IntentType.NO_CONTEXT)
            return ClassifiedIntent(
                intent=IntentType.NEW_TRIP_ROUTE,
                origin=orig_iata,
                origin_name=orig_name,
                destination=dest_iata,
                destination_name=dest_name,
                departure_date=parsed_dates["departure_date"],
                check_in=parsed_dates["check_in"],
                check_out=parsed_dates["check_out"],
                display_currency=explicit_curr or ctx.get("display_currency", "USD"),
                target_currency=explicit_curr or ctx.get("target_currency", "USD"),
                cleared_slot="date"
            )
        else:
            return ClassifiedIntent(
                intent=IntentType.NEED_DATE_CLARIFICATION,
                origin=ctx.get("origin"),
                origin_name=ctx.get("origin_name"),
                destination=ctx.get("destination"),
                destination_name=ctx.get("destination_name"),
                has_ambiguous_date=True,
                display_currency=explicit_curr or ctx.get("display_currency", "USD"),
                target_currency=explicit_curr or ctx.get("target_currency", "USD")
            )

    if pending_slot == "budget_clarification":
        # Cancellation check
        if re.search(r'\b(?:cancel|stop|abort|quit|nevermind|forget\s+it|don\'t\s+book)\b', text, re.IGNORECASE):
            return ClassifiedIntent(
                intent=IntentType.CANCEL_TRIP,
                origin=ctx.get("origin"),
                origin_name=ctx.get("origin_name"),
                destination=ctx.get("destination"),
                destination_name=ctx.get("destination_name"),
                cleared_slot="budget_clarification"
            )
        # New budget amount check
        curr_map = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY", "₹": "INR"}
        m_num = re.search(r'([$€£¥₹])?\s*(-?\d+(?:,\d+)*(?:\.\d+)?)\s*(USD|EUR|GBP|JPY|CAD|AUD|CHF|INR|AED)?', text, re.IGNORECASE)
        if m_num and m_num.group(2):
            explicit_curr = resolve_currency_name(text) or (curr_map.get(m_num.group(1)) if m_num.group(1) else (m_num.group(3).upper() if m_num.group(3) else None))
            amt = float(m_num.group(2).replace(',', ''))
            return ClassifiedIntent(
                intent=IntentType.MIDTRIP_BUDGET_CHANGE,
                origin=ctx.get("origin"),
                origin_name=ctx.get("origin_name"),
                destination=ctx.get("destination"),
                destination_name=ctx.get("destination_name"),
                budget_cap=amt,
                display_currency=explicit_curr or ctx.get("display_currency", "USD"),
                target_currency=explicit_curr or ctx.get("target_currency", "USD"),
                cleared_slot="budget_clarification"
            )

    # 3. Check for Mid-Trip Dynamic Changes (Phase 8)
    if flight_delay_pattern:
        if has_prior_destination:
            return ClassifiedIntent(
                intent=IntentType.MIDTRIP_FLIGHT_DELAY,
                origin=ctx.get("origin"),
                origin_name=ctx.get("origin_name"),
                destination=ctx.get("destination"),
                destination_name=ctx.get("destination_name"),
                display_currency=ctx.get("display_currency", "USD"),
                delay_context=text
            )
        else:
            return ClassifiedIntent(intent=IntentType.NO_CONTEXT)

    if budget_drop_pattern:
        if has_prior_destination:
            explicit_curr = resolve_currency_name(text)
            return ClassifiedIntent(
                intent=IntentType.MIDTRIP_BUDGET_CHANGE,
                origin=ctx.get("origin"),
                origin_name=ctx.get("origin_name"),
                destination=ctx.get("destination"),
                destination_name=ctx.get("destination_name"),
                display_currency=explicit_curr or ctx.get("display_currency", "USD")
            )
        else:
            return ClassifiedIntent(intent=IntentType.NO_CONTEXT)

    # 4. Check for standalone currency conversion (e.g. "convert 500 USD to EUR")
    if re.search(r'\bconvert\s+\d+', text, re.IGNORECASE) or re.search(r'\b(USD|EUR|GBP|JPY|INR)\s+to\s+(USD|EUR|GBP|JPY|INR)\b', text, re.IGNORECASE):
        return ClassifiedIntent(intent=IntentType.CURRENCY_CONVERSION)

    # 5. Check for New Trip Route Intent (Strict origin -> destination validation checked early)
    match_route = re.search(
        r'(?:plan\s+(?:a\s+)?trip\s+)?(?:from\s+)?([A-Za-z\s]+?)\s+to\s+([A-Za-z\s]+?)(?:\s+(?:on|from|in|budget|under|with|for|show\s+me|\d{4}|\d+\s+days?|\$|€|£|¥|₹)|$)',
        text,
        re.IGNORECASE
    )
    if match_route:
        raw_orig = match_route.group(1).strip()
        raw_dest = match_route.group(2).strip()
        clean_orig = re.sub(r'^(?:plan\s+(?:a\s+)?trip\s+(?:from\s+)?|trip\s+(?:from\s+)?|flights?\s+(?:from\s+)?)', '', raw_orig, flags=re.IGNORECASE).strip()
        res_orig = validate_and_resolve_city(clean_orig)
        res_dest = validate_and_resolve_city(raw_dest)

        if res_orig and res_dest:
            orig_name, orig_iata = res_orig
            dest_name, dest_iata = res_dest
            if has_ambiguous_date:
                return ClassifiedIntent(
                    intent=IntentType.NEED_DATE_CLARIFICATION,
                    origin=orig_iata,
                    origin_name=orig_name,
                    destination=dest_iata,
                    destination_name=dest_name,
                    has_ambiguous_date=True,
                    display_currency=explicit_curr or "USD",
                    target_currency=explicit_curr or "USD"
                )

            return ClassifiedIntent(
                intent=IntentType.NEW_TRIP_ROUTE,
                origin=orig_iata,
                origin_name=orig_name,
                destination=dest_iata,
                destination_name=dest_name,
                departure_date=parsed_dates["departure_date"] if parsed_dates else ctx.get("departure_date"),
                check_in=parsed_dates["check_in"] if parsed_dates else ctx.get("check_in"),
                check_out=parsed_dates["check_out"] if parsed_dates else ctx.get("check_out"),
                display_currency=explicit_curr or "USD",
                target_currency=explicit_curr or "USD"
            )

    # 6. Check for Destination/Activity Questions (ask_about_destination)
    # Narrowed so currency, price, and transport options do not get swallowed into local guide
    is_price_or_curr_query = bool(re.search(
        r'\b(?:prices?|cost|costs?|cheapest|flights?\s+only|trains?\s+only|in\s+(?:indian\s+)?rupees?|in\s+(?:swiss\s+)?francs?|in\s+usd|in\s+eur|in\s+chf|in\s+inr|in\s+gbp|in\s+jpy)\b',
        text,
        re.IGNORECASE
    ))
    dest_question_pattern = re.search(
        r'\b(?:tell\s+me\s+(?:about|some|places|things)|show\s+me\s+(?:places|things\s+to\s+do|attractions|sights|activities|spots|recommendations|around)|what(?:\'s|\s+is)?\s+there\s+to\s+do|things\s+to\s+do|places\s+to\s+visit|good\s+places|want\s+to\s+see|recommend(?:\s+activities|\s+places|\s+sights|\s+things)?|sights|attractions|activities|what\s+should\s+i\s+see|places\s+to\s+eat|restaurants)\b',
        text,
        re.IGNORECASE
    )
    if dest_question_pattern and not is_price_or_curr_query:
        city_found = _find_city_in_text(text)
        if city_found:
            dest_name, dest_iata = city_found
        elif has_prior_destination:
            dest_name = ctx.get("destination_name") or ctx.get("destination")
            dest_iata = ctx.get("destination")
            if not dest_iata or dest_iata in ["UNK", "Unknown City", "NONE"]:
                return ClassifiedIntent(intent=IntentType.NO_CONTEXT)
        else:
            return ClassifiedIntent(intent=IntentType.NO_CONTEXT)

        act_query = text
        for prefix in ["tell me some good places to visit in", "tell me good places to visit in", "tell me places to visit in", "things to do in", "what's there to do in"]:
            act_query = re.sub(rf'^{prefix}\s+[a-zA-Z\s]+', '', act_query, flags=re.IGNORECASE)

        return ClassifiedIntent(
            intent=IntentType.ASK_ABOUT_DESTINATION,
            destination=dest_iata,
            destination_name=dest_name,
            activity_query=act_query.strip() or text
        )

    # 6a. Check for Weather
    if re.search(r'\b(?:what(?:\'s|\s+is)?\s+(?:the\s+)?weather|how(?:\'s|\s+is)?\s+(?:the\s+)?weather|will\s+it\s+rain|is\s+it\s+(?:hot|cold|rainy|sunny|warm|freezing)|forecast|temperature|weather\s+(?:like|in|for)|weather)\b', text, re.IGNORECASE):
        found_place = _find_city_in_text(text)
        dest_n = found_place[0] if found_place else (ctx.get("destination_name") or ctx.get("destination"))
        dest_i = found_place[1] if found_place else ctx.get("destination")
        if not dest_i and not dest_n:
            return ClassifiedIntent(intent=IntentType.NO_CONTEXT)
        return ClassifiedIntent(
            intent=IntentType.WEATHER,
            origin=ctx.get("origin"),
            origin_name=ctx.get("origin_name"),
            destination=dest_i,
            destination_name=dest_n,
            departure_date=parsed_dates["departure_date"] if parsed_dates else ctx.get("departure_date"),
            check_in=parsed_dates["check_in"] if parsed_dates else ctx.get("check_in"),
            check_out=parsed_dates["check_out"] if parsed_dates else ctx.get("check_out"),
            display_currency=ctx.get("display_currency", "USD"),
            activity_query=text
        )

    # 6b. Check for Road Conditions
    if re.search(r'\b(?:road\s+conditions?|road\s+closures?|driving\s+conditions?|traffic\s+between|how\s+are\s+the\s+roads?|highway\s+conditions?|road\s+status)\b', text, re.IGNORECASE):
        return ClassifiedIntent(
            intent=IntentType.ROAD_CONDITIONS,
            origin=ctx.get("origin"),
            origin_name=ctx.get("origin_name"),
            destination=ctx.get("destination"),
            destination_name=ctx.get("destination_name"),
            display_currency=ctx.get("display_currency", "USD"),
            activity_query=text
        )

    # 6c. Check for Safety Advisories
    if re.search(r'\b(?:is\s+(?:it|there)\s+safe\s+to\s+visit|is\s+it\s+safe|is\s+there\s+safe|safe\s+to\s+(?:travel|visit|go)|how\s+safe\s+is|safety\s+in|safety\s+of|safety\s+advisories?|safety|dangerous|danger|unrest|curfew|scams?|advisory|advisories|should\s+i\s+go)\b', text, re.IGNORECASE):
        found_place = _find_city_in_text(text)
        dest_n = found_place[0] if found_place else (ctx.get("destination_name") or ctx.get("destination"))
        dest_i = found_place[1] if found_place else ctx.get("destination")
        return ClassifiedIntent(
            intent=IntentType.ASK_SAFETY,
            origin=ctx.get("origin"),
            origin_name=ctx.get("origin_name"),
            destination=dest_i,
            destination_name=dest_n,
            display_currency=ctx.get("display_currency", "USD"),
            activity_query=text
        )

    # 6d. Check for Destination News
    if re.search(r'\b(?:(?:any\s+)?news\s+(?:about|in|for)|what(?:\'s|\s+is)?\s+happening\s+in)\b', text, re.IGNORECASE):
        found_place = _find_city_in_text(text)
        dest_n = found_place[0] if found_place else (ctx.get("destination_name") or ctx.get("destination"))
        dest_i = found_place[1] if found_place else ctx.get("destination")
        return ClassifiedIntent(
            intent=IntentType.DESTINATION_NEWS,
            origin=ctx.get("origin"),
            origin_name=ctx.get("origin_name"),
            destination=dest_i,
            destination_name=dest_n,
            display_currency=ctx.get("display_currency", "USD"),
            activity_query=text
        )

    # 7. Check for Conversational / Commentary non-route statements
    commentary_pattern = re.search(
        r'\b(?:used\s+to|thinking\s+of|sounds\s+great|what\s+i(?:(?:\')?ve)?\s+read|read\s+about|heard\s+about|just\s+curious|wondering)\b',
        text,
        re.IGNORECASE
    )
    if commentary_pattern:
        if has_prior_destination:
            return ClassifiedIntent(
                intent=IntentType.GENERAL_CONVERSATION,
                origin=ctx.get("origin"),
                origin_name=ctx.get("origin_name"),
                destination=ctx.get("destination"),
                destination_name=ctx.get("destination_name")
            )
        else:
            return ClassifiedIntent(intent=IntentType.NO_CONTEXT)

    # 8. Check for Single Destination Route (e.g. "travel to Paris", "fly to London", "trip to Pune")
    match_single_dest = re.search(
        r'\b(?:travel\s+to|fly\s+to|trip\s+to|going\s+to|visit)\s+([A-Za-z\s]+?)(?:\s+(?:on|from|in|budget|under|with|for|show\s+me|\d{4}|\d+\s+days?|\$|€|£|¥|₹)|$)',
        text,
        re.IGNORECASE
    )
    if match_single_dest:
        res_dest = validate_and_resolve_city(match_single_dest.group(1).strip())
        if res_dest:
            dest_name, dest_iata = res_dest
            orig_iata = ctx.get("origin")
            orig_name = ctx.get("origin_name")
            if orig_iata:
                if has_ambiguous_date or (parsed_dates and parsed_dates.get("is_past")):
                    return ClassifiedIntent(
                        intent=IntentType.NEED_DATE_CLARIFICATION,
                        origin=orig_iata,
                        origin_name=orig_name,
                        destination=dest_iata,
                        destination_name=dest_name,
                        has_ambiguous_date=has_ambiguous_date,
                        has_past_date=bool(parsed_dates and parsed_dates.get("is_past")),
                        display_currency=explicit_curr or ctx.get("display_currency", "USD"),
                        target_currency=explicit_curr or ctx.get("target_currency", "USD")
                    )
                return ClassifiedIntent(
                    intent=IntentType.NEW_TRIP_ROUTE,
                    origin=orig_iata,
                    origin_name=orig_name,
                    destination=dest_iata,
                    destination_name=dest_name,
                    departure_date=parsed_dates["departure_date"] if parsed_dates else ctx.get("departure_date"),
                    check_in=parsed_dates["check_in"] if parsed_dates else ctx.get("check_in"),
                    check_out=parsed_dates["check_out"] if parsed_dates else ctx.get("check_out"),
                    display_currency=explicit_curr or ctx.get("display_currency", "USD"),
                    target_currency=explicit_curr or ctx.get("target_currency", "USD")
                )
            else:
                return ClassifiedIntent(
                    intent=IntentType.SLOT_FILLED_NEED_ORIGIN,
                    destination=dest_iata,
                    destination_name=dest_name,
                    departure_date=parsed_dates["departure_date"] if parsed_dates else ctx.get("departure_date"),
                    check_in=parsed_dates["check_in"] if parsed_dates else ctx.get("check_in"),
                    check_out=parsed_dates["check_out"] if parsed_dates else ctx.get("check_out"),
                    display_currency=explicit_curr or ctx.get("display_currency", "USD"),
                    target_currency=explicit_curr or ctx.get("target_currency", "USD")
                )

    # 9. Check for Modify Existing Trip (General Currency, Hotel, Flight, Date change)
    if is_unresolvable_currency_query(text):
        return ClassifiedIntent(
            intent=IntentType.NEED_CURRENCY_CLARIFICATION,
            origin=ctx.get("origin"),
            origin_name=ctx.get("origin_name"),
            destination=ctx.get("destination"),
            destination_name=ctx.get("destination_name"),
            display_currency=ctx.get("display_currency", "USD"),
            target_currency=ctx.get("target_currency", "USD")
        )

    budget_pattern = re.search(r'\b(?:under|budget|max|cap|spend)\s+(?:is\s+)?(\d+|\$|€|£|¥|₹)', text, re.IGNORECASE)
    mod_pattern = re.search(r'\b(?:cheaper|cheapest|more\s+expensive|different|instead|change|switch|modify|adjust|update|hotel|hotels|stay|room|rooms|flight|flights|airline|plane|train|trains|bus|transport|dates?)\b', text, re.IGNORECASE)

    if (has_ambiguous_date or (parsed_dates and parsed_dates.get("is_past"))) and has_prior_destination:
        return ClassifiedIntent(
            intent=IntentType.NEED_DATE_CLARIFICATION,
            origin=ctx.get("origin"),
            origin_name=ctx.get("origin_name"),
            destination=ctx.get("destination"),
            destination_name=ctx.get("destination_name"),
            has_ambiguous_date=has_ambiguous_date,
            has_past_date=bool(parsed_dates and parsed_dates.get("is_past")),
            display_currency=explicit_curr or ctx.get("display_currency", "USD"),
            target_currency=explicit_curr or ctx.get("target_currency", "USD")
        )

    if explicit_curr or budget_pattern or mod_pattern or parsed_dates:
        if has_prior_destination:
            return ClassifiedIntent(
                intent=IntentType.MODIFY_EXISTING,
                origin=ctx.get("origin"),
                origin_name=ctx.get("origin_name"),
                destination=ctx.get("destination"),
                destination_name=ctx.get("destination_name"),
                departure_date=parsed_dates["departure_date"] if parsed_dates else ctx.get("departure_date"),
                check_in=parsed_dates["check_in"] if parsed_dates else ctx.get("check_in"),
                check_out=parsed_dates["check_out"] if parsed_dates else ctx.get("check_out"),
                display_currency=explicit_curr or ctx.get("display_currency", "USD"),
                target_currency=explicit_curr or ctx.get("target_currency", "USD")
            )
        else:
            return ClassifiedIntent(
                intent=IntentType.NO_CONTEXT,
                departure_date=parsed_dates["departure_date"] if parsed_dates else None,
                check_in=parsed_dates["check_in"] if parsed_dates else None,
                check_out=parsed_dates["check_out"] if parsed_dates else None,
                display_currency=explicit_curr or "USD",
                target_currency=explicit_curr or "USD"
            )


    # 10. Fallback: if prior trip exists, keep conversation; otherwise prompt for destination
    if has_prior_destination:
        return ClassifiedIntent(
            intent=IntentType.GENERAL_CONVERSATION,
            origin=ctx.get("origin"),
            origin_name=ctx.get("origin_name"),
            destination=ctx.get("destination"),
            destination_name=ctx.get("destination_name"),
            display_currency=ctx.get("display_currency"),
            target_currency=ctx.get("target_currency")
        )
    return ClassifiedIntent(intent=IntentType.NO_CONTEXT)

