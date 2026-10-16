import operator
from typing import TypedDict, Annotated, Sequence, Optional
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

class PlannerState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]
    session_id: str
    user_id: str
    used_memory_currency: bool
    used_memory_origin: bool
    is_conversion: bool
    is_planning: bool
    is_guide_query: bool
    is_weather_query: Optional[bool]
    is_road_conditions_query: Optional[bool]
    is_destination_news_query: Optional[bool]
    is_safety_query: Optional[bool]
    is_show_itinerary: Optional[bool]
    is_conversational: bool
    needs_destination: bool
    needs_origin: bool
    needs_date_clarification: bool
    has_past_date: Optional[bool]
    needs_currency_clarification: bool
    is_confirmation: bool
    is_ambiguous_confirmation: bool
    plan: Optional[dict]
    # Activity specific query
    activity_query: Optional[str]
    # Trip location context
    origin: Optional[str]
    origin_name: Optional[str]
    destination: Optional[str]
    destination_name: Optional[str]
    departure_date: Optional[str]
    check_in: Optional[str]
    check_out: Optional[str]
    # Responses
    budget_agent_response: str
    transport_agent_response: str
    hotel_agent_response: str
    local_guide_agent_response: str
    # Phase 5: negotiation & budget fields
    budget_cap: Optional[float]
    target_currency: str
    display_currency: str
    negotiation_round: int
    max_negotiation_rounds: int
    budget_reconciliation: Optional[dict]
    cost_line_items: list[dict]
    transport_price_ceiling: Optional[float]
    hotel_price_ceiling: Optional[float]
    # Phase 8: Dynamic re-planning & selective node re-entry
    is_budget_change: bool
    is_flight_delay: bool
    delay_context: Optional[str]
    invoked_nodes: Annotated[list[str], operator.add]
    is_replan: bool
    cached_transport_results: Optional[list[dict]]
    cached_hotel_results: Optional[list[dict]]
    cached_local_guide_results: Optional[list[dict]]
    # Phase 8: Honest zero/negative budget handling & cancellation
    is_invalid_budget: Optional[bool]
    invalid_budget_message: Optional[str]
    is_cancellation: Optional[bool]
    # Semantic modifiers & selective routing
    target_agent: Optional[str]
    delay_hours: Optional[float]
    min_departure_after: Optional[str]
    excluded_hotels: Optional[list[str]]
    excluded_transports: Optional[list[str]]
    prefer_cheaper: Optional[bool]
    prefer_different: Optional[bool]
    prior_transport_results: Optional[list[dict]]
    prior_hotel_results: Optional[list[dict]]
    no_later_flight_found: Optional[bool]
    no_cheaper_hotel_found: Optional[bool]
    no_cheaper_transport_found: Optional[bool]
    budget_requested: Optional[bool]
    structured_payload: Optional[dict]

