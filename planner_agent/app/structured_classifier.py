"""
planner_agent/app/structured_classifier.py
-----------------------------------------
Consolidated single-step natural language classifier.
Replaces scattered keyword matching across small talk, currency phrasing,
bare-number budgets, and hotel/flight modifiers with a single structured classification call.
Supports Gemini Flash and Ollama, with fast-path for trivial replies and
a deterministic semantic fallback for offline/test environments.
"""

from typing import Optional, Literal
import json
import logging
import re
from pydantic import BaseModel, Field

from shared.resolvers import resolve_currency_name, is_unresolvable_currency_query, extract_route_cities_from_text
from shared.gemini_client import generate_gemini_content, is_gemini_configured

logger = logging.getLogger(__name__)


class StructuredPlanClassification(BaseModel):
    intent: Literal[
        "new_trip_route",
        "confirm",
        "cancel",
        "modify_currency",
        "modify_budget",
        "modify_hotel",
        "modify_flight",
        "modify_dates",
        "ask_about_destination",
        "ask_weather",
        "check_weather",
        "ask_road_conditions",
        "check_road_conditions",
        "ask_news",
        "check_destination_news",
        "ask_safety",
        "check_safety",
        "show_current_plan",
        "show_itinerary",
        "general_conversation",
        "unclear"
    ] = Field(..., description="High-level user intent")
    currency: Optional[str] = Field(None, description="ISO currency code (e.g. USD, EUR, INR) or null")
    budget_amount: Optional[float] = Field(None, description="Extracted numerical budget cap or adjustment")
    budget_is_relative: bool = Field(False, description="True if the user requested a relative drop/cut")
    modifier_target: Optional[Literal["hotel", "flight", "dates"]] = Field(None, description="Target entity")
    modifier_direction: Optional[Literal["cheaper", "different", "unspecified"]] = Field(None, description="Modifier direction")
    is_budget_query: bool = Field(False, description="True if user asked a question about budget (e.g. within budget or total cost)")
    is_budget_removal: bool = Field(False, description="True if user requested to remove/clear budget")


def check_fast_path(text: str) -> Optional[StructuredPlanClassification]:
    """
    Fast-path for trivial single-word/short confirmation or cancellation replies
    to avoid LLM round-trip on the most common messages.
    """
    cleaned = text.strip().rstrip(".!?").strip()
    lower = cleaned.lower()

    confirm_exact = {
        "confirm", "conferm", "confirn", "comfirm", "comfirn", "confrim", "confrm", "cofirm", "confirmation",
        "yes", "yeah", "yep", "yup", "sure", "ok", "okay",
        "looks good", "looks great", "book it", "book this", "book now",
        "let's do it", "lets do it", "go for it", "go ahead", "proceed",
        "approved", "approve", "finalize", "perfect", "sounds good", "done"
    }
    if lower in confirm_exact:
        return StructuredPlanClassification(intent="confirm")

    redisplay_exact = {
        "show me the itinerary again", "show itinerary again", "show the itinerary again",
        "show me the plan again", "show plan again", "show the plan again",
        "show itinerary", "show plan", "show me the itinerary", "show me the plan",
        "what was the trip again", "what was the plan again", "what was the itinerary again",
        "what is the itinerary again", "what's the itinerary again", "what was my itinerary",
        "display itinerary again", "see the plan again", "show the trip again",
        "view itinerary", "view plan"
    }
    if lower in redisplay_exact or re.search(r'^(?:show\s+(?:me\s+)?(?:the\s+)?(?:itinerary|plan)(?:\s+again)?|what\s+was\s+(?:the\s+)?(?:trip|itinerary|plan)(?:\s+again)?)$', lower):
        return StructuredPlanClassification(intent="show_itinerary")

    cancel_exact = {
        "cancel", "stop", "abort", "quit", "nevermind", "never mind",
        "forget it", "don't book", "dont book"
    }
    if lower in cancel_exact:
        return StructuredPlanClassification(intent="cancel")

    return None


def classify_with_rules(text: str, session_context: Optional[dict] = None) -> StructuredPlanClassification:
    """
    High-coverage deterministic semantic classifier used as offline/mock fallback.
    Handles bare-number budgets, flexible modifiers, and open small talk without narrow lists.
    """
    ctx = session_context or {}
    lower = text.lower().strip()

    # Fast-path check
    fp = check_fast_path(text)
    if fp:
        return fp

    # Priority 0: Real weather checks (never touch route state)
    if re.search(r'\b(?:what(?:\'s|\s+is)?\s+(?:the\s+)?weather|how(?:\'s|\s+is)?\s+(?:the\s+)?weather|will\s+it\s+rain|is\s+it\s+(?:hot|cold|rainy|sunny|warm|freezing)|forecast|temperature|weather\s+(?:like|in|for)|weather)\b', lower):
        return StructuredPlanClassification(intent="check_weather")

    # Priority 0: Road conditions
    if re.search(r'\b(?:road\s+conditions?|road\s+closures?|driving\s+conditions?|traffic\s+between|how\s+are\s+the\s+roads?|highway\s+conditions?|road\s+status)\b', lower):
        return StructuredPlanClassification(intent="check_road_conditions")

    # Priority 0: Safety & security advisories (never touch route state)
    if re.search(r'\b(?:is\s+(?:it|there)\s+safe\s+to\s+visit|is\s+it\s+safe|is\s+there\s+safe|safe\s+to\s+(?:travel|visit|go)|how\s+safe\s+is|safety\s+in|safety\s+of|safety\s+advisories?|safety|dangerous|danger|unrest|curfew|scams?|advisory|advisories|should\s+i\s+go)\b', lower):
        return StructuredPlanClassification(intent="check_safety")

    # Priority 0: Destination news & advisories
    if re.search(r'\b(?:(?:any\s+)?news\s+(?:about|in|for|before|i\s+should|to\s+know)|(?:any\s+)?travel\s+(?:news|advisories?|warnings?)|news\b.*?\b(?:before|go|trip|travel)|what(?:\'s|\s+is)?\s+happening\s+in)\b', lower):
        return StructuredPlanClassification(intent="check_destination_news")

    # Priority 0: Single city queries with month (e.g. "Dubai in July" or "Dubai in July or Delhi in June") -> weather/climate info
    if re.search(r'[a-zA-Z\s]+\s+in\s+(?:january|february|march|april|may|june|july|august|september|october|november|december)\b', lower):
        return StructuredPlanClassification(intent="ask_weather")

    # 0. Route check: complete "X to Y" route is never a modifier or small talk
    if extract_route_cities_from_text(text):
        return StructuredPlanClassification(intent="new_trip_route")

    # 1. Budget modification & budget queries
    # Remove/clear budget
    if re.search(r'\b(?:no\s+budget|remove\s+(?:the\s+)?budget|clear\s+(?:the\s+)?budget|delete\s+(?:the\s+)?budget|without\s+(?:a\s+)?budget|drop\s+(?:the\s+)?budget)\b', lower):
        return StructuredPlanClassification(
            intent="modify_budget",
            is_budget_removal=True,
            budget_amount=None
        )

    # Budget question / inquiry
    if re.search(r'\b(?:is\s+this\s+within\s+(?:my\s+)?budget|am\s+i\s+within\s+(?:my\s+)?budget|is\s+it\s+within\s+(?:my\s+)?budget|within\s+(?:my\s+)?budget\??|how\s+much\s+will\s+this\s+cost\s+(?:in\s+total)?|what(?:\'s|\s+is)?\s+(?:the\s+)?total\s+cost|total\s+cost\s+of\s+this|what\s+is\s+the\s+budget\s+status)\b', lower):
        return StructuredPlanClassification(
            intent="modify_budget",
            is_budget_query=True
        )

    # Relative drop/cut
    m_rel = re.search(
        r'\b(?:dropped|decreased|cut|reduced|fell)\s+(?:by\s+)?([$€£¥₹])?\s*(\d+(?:,\d+)*(?:\.\d+)?)\b',
        text, re.IGNORECASE
    )
    if m_rel:
        amt = float(m_rel.group(2).replace(',', ''))
        return StructuredPlanClassification(
            intent="modify_budget",
            budget_amount=amt,
            budget_is_relative=True
        )

    # Absolute budget cap:
    # 'cap it at 300', 'let's cap it at 300', 'cap at 300', 'set budget to 300', 'budget 300', '300 max', 'under 300', 'make it 300', 'let's say 300'
    m_budget = re.search(
        r'\b(?:(?:let\'?s\s+)?(?:cap\s+(?:it\s+)?at|say|make\s+it|how\s+about|do)|set\s+(?:the\s+)?budget\s+(?:to|at)|(?:my\s+)?(?:budget|max|cap|limit)\s*(?:is\s+now|now\s+is|is|of\s+|now\s+of\s+|to\s+)?|(?:under|keep\s+it\s+under))\s*(?:around\s+)?([$€£¥₹])?\s*(-?\d+(?:,\d+)*(?:\.\d+)?)\s*(USD|EUR|GBP|JPY|CAD|AUD|CHF|INR|AED|RUPEES|RUPEE)?(?:\s+(?:max|instead))?\b',
        text, re.IGNORECASE
    )
    if m_budget and m_budget.group(2):
        amt = float(m_budget.group(2).replace(',', ''))
        raw_code = m_budget.group(3)
        code = "INR" if raw_code and raw_code.upper() in ["RUPEES", "RUPEE"] else (raw_code.upper() if raw_code else None)
        return StructuredPlanClassification(
            intent="modify_budget",
            budget_amount=amt,
            budget_is_relative=False,
            currency=code
        )

    # Standalone bare number with budget/max/cap suffix: e.g. "300 max", "400 budget", "500 cap", "300 limit"
    m_suffix = re.search(
        r'\b(-?\d+(?:,\d+)*(?:\.\d+)?)\s*(USD|EUR|GBP|JPY|CAD|AUD|CHF|INR|AED|RUPEES|RUPEE)?\s+(?:max|cap|budget|limit|instead)\b',
        text, re.IGNORECASE
    )
    if m_suffix:
        amt = float(m_suffix.group(1).replace(',', ''))
        raw_code = m_suffix.group(2)
        code = "INR" if raw_code and raw_code.upper() in ["RUPEES", "RUPEE"] else (raw_code.upper() if raw_code else None)
        return StructuredPlanClassification(
            intent="modify_budget",
            budget_amount=amt,
            budget_is_relative=False,
            currency=code
        )

    # 2. Currency modification
    curr = resolve_currency_name(text)
    if curr:
        return StructuredPlanClassification(
            intent="modify_currency",
            currency=curr,
            modifier_direction="different"
        )
    if is_unresolvable_currency_query(text):
        return StructuredPlanClassification(
            intent="modify_currency",
            currency=None
        )

    # 3. Hotel modification:
    # "different hotel", "switch hotels", "modify the accommodation", "change the room", "cheaper hotel", "adjust hotel", "another stay"
    is_hotel = bool(re.search(r'\b(?:hotel|hotels|room|rooms|accommodation|stay|stays|lodging)\b', lower))
    # Flight modification:
    # "different flight", "switch flights", "change flight", "another flight", "cheaper flight", "flight got delayed", "flight option"
    is_flight = bool(re.search(r'\b(?:flight|flights|airline|plane|transit|ticket|tickets)\b', lower))

    is_cheaper = bool(re.search(r'\b(?:cheaper|cheapest|lower\s+cost|less\s+expensive|more\s+affordable)\b', lower))
    is_different = bool(re.search(r'\b(?:different|switch|another|alternative|other|modify|change|adjust|update)\b', lower))

    if is_hotel and not is_flight:
        direction = "cheaper" if is_cheaper else ("different" if is_different else "unspecified")
        return StructuredPlanClassification(
            intent="modify_hotel",
            modifier_target="hotel",
            modifier_direction=direction
        )

    if is_flight and not is_hotel:
        direction = "cheaper" if is_cheaper else ("different" if is_different else "unspecified")
        return StructuredPlanClassification(
            intent="modify_flight",
            modifier_target="flight",
            modifier_direction=direction
        )

    if is_hotel and is_flight:
        if "hotel" in lower:
            direction = "cheaper" if is_cheaper else "different"
            return StructuredPlanClassification(intent="modify_hotel", modifier_target="hotel", modifier_direction=direction)

    if is_cheaper:
        return StructuredPlanClassification(
            intent="modify_hotel",
            modifier_target="hotel",
            modifier_direction="cheaper"
        )
    if is_different and ("option" in lower or "selection" in lower):
        return StructuredPlanClassification(
            intent="modify_hotel",
            modifier_target="hotel",
            modifier_direction="different"
        )

    # Flight delay:
    if re.search(r'\b(?:delayed|rescheduled|delay)\b', lower):
        return StructuredPlanClassification(
            intent="modify_flight",
            modifier_target="flight",
            modifier_direction="unspecified"
        )

    # 4. Dates modification:
    if re.search(r'\b(?:dates?|october|november|december|january|february|march|april|may|june|july|august|september)\b', lower):
        return StructuredPlanClassification(
            intent="modify_dates",
            modifier_target="dates",
            modifier_direction="different"
        )

    # 5. Destination questions:
    if re.search(r'\b(?:tell\s+me\s+(?:about|some|places|things)|show\s+me\s+(?:places|things|attractions|sights)|what(?:\'s|\s+is)?\s+there\s+to\s+do|things\s+to\s+do|places\s+to\s+visit|sights|attractions|activities|places\s+to\s+eat|restaurants)\b', lower):
        return StructuredPlanClassification(
            intent="ask_about_destination"
        )

    # 6. Real weather checks:
    if re.search(r'\b(?:what(?:\'s|\s+is)?\s+(?:the\s+)?weather|how(?:\'s|\s+is)?\s+(?:the\s+)?weather|will\s+it\s+rain|is\s+it\s+(?:hot|cold|rainy|sunny|warm|freezing)|forecast|temperature|weather\s+(?:like|in|for)|weather)\b', lower):
        return StructuredPlanClassification(
            intent="check_weather"
        )

    # 7. Road conditions:
    if re.search(r'\b(?:road\s+conditions?|road\s+closures?|driving\s+conditions?|traffic\s+between|how\s+are\s+the\s+roads?|highway\s+conditions?|road\s+status)\b', lower):
        return StructuredPlanClassification(
            intent="check_road_conditions"
        )

    # 8. Destination news:
    if re.search(r'\b(?:(?:any\s+)?news\s+(?:about|in|for)|what(?:\'s|\s+is)?\s+happening\s+in|is\s+it\s+safe\s+to\s+visit|travel\s+advisories?|travel\s+warnings?|safety\s+in)\b', lower):
        return StructuredPlanClassification(
            intent="check_destination_news"
        )

    # 9. General conversation / small talk:
    small_talk_patterns = [
        r'\b(?:joke|jokes|funny|riddle)\b',
        r'\b(?:who\s+are\s+you|who\s+made\s+you|what\s+are\s+you|what\s+can\s+you\s+do|who\s+created\s+you)\b',
        r'\b(?:hello|hi|hey|howdy|greetings|how\s+are\s+you|good\s+morning|good\s+evening)\b',
        r'\b(?:what(?:\'s|\s+is)?\s+(?:your\s+)?favorite\b)',
        r'\b(?:favorite\s+(?:color|food|movie|music|place|city|country))\b',
        r'\b(?:are\s+you\s+(?:a\s+)?(?:human|robot|ai|bot|real))\b',
        r'\b(?:meaning\s+of\s+life|tell\s+me\s+about\s+yourself|interesting\s+about\s+yourself)\b',
    ]
    if any(re.search(pat, lower) for pat in small_talk_patterns):
        return StructuredPlanClassification(
            intent="general_conversation"
        )

    # If it's a general question not about travel booking
    if re.search(r'^(?:what|why|how|who|tell\s+me|do\s+you)\b', lower) and not re.search(r'\b(?:flight|hotel|trip|plan|cost|price|ticket)\b', lower):
        return StructuredPlanClassification(
            intent="general_conversation"
        )

    # 10. Default to unclear
    return StructuredPlanClassification(
        intent="unclear"
    )


async def classify_with_llm(
    text: str,
    session_context: Optional[dict] = None
) -> StructuredPlanClassification:
    """
    One-call structured LLM classification per message.
    Tries Gemini (or Ollama if available), with fast-path and robust rule fallback.
    """
    # Fast path for trivial single-word responses
    fp = check_fast_path(text)
    if fp:
        return fp

    # Unconditional route check: a complete route statement is always a new trip route
    if extract_route_cities_from_text(text):
        return StructuredPlanClassification(intent="new_trip_route")

    ctx = session_context or {}

    # Fast-path deterministic check for budget removal and budget queries
    rule_check = classify_with_rules(text, session_context=ctx)
    if getattr(rule_check, "is_budget_removal", False) or getattr(rule_check, "is_budget_query", False):
        return rule_check
    dest = ctx.get("destination_name") or ctx.get("destination") or "Unknown"
    orig = ctx.get("origin_name") or ctx.get("origin") or "Unknown"
    curr = ctx.get("display_currency") or "USD"
    cap = ctx.get("budget_cap")

    prompt = f"""You are the TripMesh Travel Intent Classifier.
Analyze the user's message in the context of their currently active trip plan.

CURRENT PLAN CONTEXT:
- Origin: {orig}
- Destination: {dest}
- Current Currency: {curr}
- Current Budget Cap: {cap}
- Status: Awaiting confirmation or reviewing plan

USER MESSAGE:
"{text}"

Classify the user message into EXACTLY ONE of the following intents:
- "new_trip_route": Completely new trip route or flight from city X to city Y (e.g. "mumbai to delhi", "new trip from NYC to Paris", "fly from Chicago to Miami").
- "confirm": Approves/confirms booking (e.g. "confirm", "conferm", "looks good", "book it", "let's do it", "sounds good", "yep", "ok").
- "cancel": Cancels/aborts trip (e.g. "cancel", "stop", "nevermind", "abort").
- "modify_currency": Requests currency change (e.g. "in indian currency", "show in euros", "in yen", "pay in USD").
- "modify_budget": Specifies new budget cap or budget cut, including bare numbers (e.g. "cap it at 300", "let's say 300", "300 max", "cut budget by 200").
- "modify_hotel": Requests hotel adjustment, change, or alternatives (e.g. "can we switch hotels", "different hotel", "modify the accommodation", "cheaper hotel", "change room").
- "modify_flight": Requests flight adjustment or reports delay (e.g. "I want a different flight option", "cheaper flight", "switch flights", "flight delayed 3 hours").
- "modify_dates": Requests different travel dates (e.g. "change dates to October", "next week instead").
- "ask_about_destination": Inquires about destination sights, attractions, food, or activities (e.g. "what to do in {dest}", "best sights in Paris", "tell me about local culture").
- "ask_weather": Asks about destination weather, climate, forecasts, or city-month comparisons (e.g. "what's the weather going to be like?", "what's the weather in delhi in june?", "Dubai in July or Delhi in June", "is it hot in Dubai in July?", "will it rain?").
- "ask_road_conditions": Asks about road or driving conditions, traffic, road closures (e.g. "how are the roads?", "driving conditions", "traffic between Pune and Mumbai").
- "ask_safety": Asks about safety, danger, unrest, curfews, scams, security advisories, or "is it safe to visit / should I go" (e.g. "is it safe to visit mumbai now", "is there safe to visit mumbai now", "safety in Paris", "curfew in London", "any scams in Rome", "should I go to Delhi right now?").
- "ask_news": Asks about destination news, headlines, or local happenings (e.g. "any news I should know about before I go?", "what's happening in Delhi").
- "show_current_plan": Requests to re-display or view the current itinerary without modifying anything (e.g. "show me the itinerary again", "show plan", "what was the trip again").
- "general_conversation": Small talk, casual banter, bot persona questions, or comments (e.g. "what's your favorite color", "who made you", "tell me something interesting about yourself").
- "unclear": Truly unintelligible or ambiguous.

Respond strictly with valid JSON conforming to:
{{
  "intent": "new_trip_route" | "confirm" | "cancel" | "modify_currency" | "modify_budget" | "modify_hotel" | "modify_flight" | "modify_dates" | "ask_about_destination" | "ask_weather" | "ask_road_conditions" | "ask_safety" | "ask_news" | "show_current_plan" | "general_conversation" | "unclear",
  "currency": string or null,
  "budget_amount": number or null,
  "budget_is_relative": boolean,
  "modifier_target": "hotel" | "flight" | "dates" | null,
  "modifier_direction": "cheaper" | "different" | "unspecified" | null
}}
"""
    # 1. Try Gemini
    if is_gemini_configured():
        try:
            raw_json = await generate_gemini_content(prompt, temperature=0.0, max_tokens=256)
            if raw_json:
                clean = re.sub(r'^```(?:json)?\s*', '', raw_json.strip())
                clean = re.sub(r'\s*```$', '', clean).strip()
                parsed = json.loads(clean)
                raw_intent = parsed.get("intent")
                # Normalize synonyms to canonical names
                norm_map = {
                    "check_weather": "ask_weather",
                    "check_safety": "ask_safety",
                    "check_destination_news": "ask_news",
                    "check_road_conditions": "ask_road_conditions",
                    "show_itinerary": "show_current_plan"
                }
                if raw_intent in norm_map:
                    parsed["intent"] = norm_map[raw_intent]
                return StructuredPlanClassification(**parsed)
        except Exception as exc:
            logger.warning(f"Structured LLM classification error: {exc}. Using semantic rule engine.")

    # 2. Fallback to comprehensive semantic rule engine
    return classify_with_rules(text, session_context=ctx)
