"""
shared/schemas.py
-----------------
Canonical Pydantic v2 models shared across all TripMesh agents.
Every cross-agent message MUST use these models — no raw dicts.

Phase 0: minimal field set; extend per agent in later phases.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Annotated, Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class TransportMode(str, Enum):
    """Supported modes of transport for the Transport Agent."""

    flight = "flight"
    train = "train"
    bus = "bus"


class DataQuality(str, Enum):
    """Indicates whether data came from a live API or is estimated/mock."""

    live = "live"        # real API response
    cached = "cached"    # recently cached live data
    estimated = "estimated"  # mock / no real adapter coverage — MUST be shown to user


class ItineraryItemType(str, Enum):
    transport = "transport"
    accommodation = "accommodation"
    activity = "activity"
    meal = "meal"
    note = "note"


class BookingStatus(str, Enum):
    draft = "draft"            # not yet confirmed by user
    pending_confirmation = "pending_confirmation"  # awaiting human-in-the-loop
    confirmed = "confirmed"    # user explicitly approved (simulated booking)
    cancelled = "cancelled"


# ---------------------------------------------------------------------------
# Supporting sub-models
# ---------------------------------------------------------------------------


class Location(BaseModel):
    """A named place with optional geographic coordinates."""

    name: str = Field(..., description="Human-readable place name (city, airport, hotel, etc.)")
    iata: Optional[str] = Field(
        None,
        description="IATA airport code when applicable (3-letter uppercase)",
        min_length=3,
        max_length=3,
    )
    country: Optional[str] = Field(None, description="ISO 3166-1 alpha-2 country code")
    lat: Optional[float] = Field(None, ge=-90, le=90)
    lon: Optional[float] = Field(None, ge=-180, le=180)

    @field_validator("iata", mode="before")
    @classmethod
    def uppercase_iata(cls, v: Optional[str]) -> Optional[str]:
        return v.upper() if v else v


class Money(BaseModel):
    """An amount in a specific currency, always stored as a float."""

    amount: float = Field(..., ge=0, description="Monetary amount (non-negative)")
    currency: str = Field(
        ...,
        description="ISO 4217 currency code (e.g. USD, EUR, GBP)",
        min_length=3,
        max_length=3,
    )

    @field_validator("currency", mode="before")
    @classmethod
    def uppercase_currency(cls, v: str) -> str:
        return v.upper()

    def __str__(self) -> str:
        return f"{self.amount:.2f} {self.currency}"


class BudgetConstraint(BaseModel):
    """The user's stated budget for the trip."""

    total: Money = Field(..., description="Hard budget cap for the entire trip")
    per_night_accommodation: Optional[Money] = Field(
        None, description="Optional sub-cap for nightly accommodation cost"
    )
    per_person: bool = Field(
        False, description="True if 'total' is per-person (group trips)"
    )
    flexible: bool = Field(
        False,
        description=(
            "If True, the Budget Agent can propose options slightly over budget "
            "and ask the user to confirm. If False, stay strictly within."
        ),
    )


class TransportOption(BaseModel):
    """
    A single transport result from any adapter (flight / train / bus / mock).
    Mode-agnostic: the adapter pattern in transport_agent ensures all modes
    produce this same schema.
    """

    mode: TransportMode
    origin: Location
    destination: Location
    departure_at: datetime = Field(..., description="Departure datetime (UTC)")
    arrival_at: datetime = Field(..., description="Arrival datetime (UTC)")
    duration_minutes: int = Field(..., ge=0)
    price: Money
    carrier: Optional[str] = Field(None, description="Airline, rail operator, or bus company name")
    booking_ref: Optional[str] = Field(
        None, description="Reference code for mock or sandbox bookings"
    )
    co2_estimate_kg: Optional[float] = Field(
        None,
        ge=0,
        description="Estimated CO₂ per-passenger kg (optional, enables stretch carbon feature)",
    )
    data_quality: DataQuality = Field(
        DataQuality.estimated,
        description="Whether this result comes from a live API or is mock/estimated",
    )
    # Agents MUST flag estimated results. Planner surfaces this to the user.
    is_estimated: bool = Field(
        False,
        description="Must be True when data_quality is 'estimated'. Never hide this from the user.",
    )

    @model_validator(mode="after")
    def flag_estimated_quality(self) -> "TransportOption":
        if self.data_quality == DataQuality.estimated:
            object.__setattr__(self, "is_estimated", True)
        return self

    @model_validator(mode="after")
    def arrival_after_departure(self) -> "TransportOption":
        if self.arrival_at <= self.departure_at:
            raise ValueError("arrival_at must be after departure_at")
        return self


class AccommodationOption(BaseModel):
    """A single hotel / rental / hostel option."""

    name: str
    location: Location
    check_in: date
    check_out: date
    price_per_night: Money
    total_price: Money
    rating: Optional[float] = Field(None, ge=0, le=5)
    amenities: list[str] = Field(default_factory=list)
    booking_ref: Optional[str] = None
    data_quality: DataQuality = DataQuality.estimated
    is_estimated: bool = False

    @model_validator(mode="after")
    def flag_estimated_quality(self) -> "AccommodationOption":
        if self.data_quality == DataQuality.estimated:
            object.__setattr__(self, "is_estimated", True)
        return self

    @model_validator(mode="after")
    def checkout_after_checkin(self) -> "AccommodationOption":
        if self.check_out <= self.check_in:
            raise ValueError("check_out must be after check_in")
        return self


class ActivityRecommendation(BaseModel):
    """
    A local activity / point of interest, returned by the Local Guide Agent.
    Must always carry a source citation when data_quality is 'live'.
    """

    name: str
    description: str
    location: Location
    estimated_duration_hours: Optional[float] = Field(None, ge=0)
    estimated_cost: Optional[Money] = None
    source_citation: Optional[str] = Field(
        None,
        description=(
            "Citation for the retrieved source document. "
            "Required when grounded via RAG. "
            "If retrieval returned nothing relevant, the agent must say so — "
            "never fabricate a citation."
        ),
    )
    data_quality: DataQuality = DataQuality.estimated
    tags: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Phase 5: Budget reconciliation models
# ---------------------------------------------------------------------------


class CostLineItem(BaseModel):
    """
    A single cost item contributed by a specialist agent (transport, hotel, guide).
    """

    source_agent: Literal["transport", "hotel", "local_guide"]
    amount: float = Field(..., ge=0)
    currency: str
    is_estimated: bool = False
    data_quality: DataQuality = DataQuality.live
    description: Optional[str] = None

    @field_validator("currency")
    @classmethod
    def uppercase_currency(cls, v: str) -> str:
        return v.strip().upper()


class BudgetReconciliation(BaseModel):
    """
    Result of multi-currency budget reconciliation by the Budget Agent.
    """

    total_cost: float = Field(..., ge=0)
    currency: str
    budget_cap: Optional[float] = None
    over_budget: bool = False
    overage_amount: float = Field(0.0, ge=0)
    breakdown_by_agent: dict[str, float] = Field(default_factory=dict)
    items: list[CostLineItem] = Field(default_factory=list)
    conversion_failures: list[CostLineItem] = Field(default_factory=list)

    @field_validator("currency")
    @classmethod
    def uppercase_currency(cls, v: str) -> str:
        return v.strip().upper()


# ---------------------------------------------------------------------------
# Core trip / itinerary models
# ---------------------------------------------------------------------------


class TripRequest(BaseModel):
    """
    The user's initial trip request, parsed from natural language by the Planner.
    All fields are optional except destination; the Planner fills gaps by asking.
    """

    destination: str = Field(..., description="Primary destination (city, region, or country)")
    origin: Optional[str] = Field(None, description="Departure city or airport")
    departure_date: Optional[date] = None
    return_date: Optional[date] = None
    num_travelers: int = Field(1, ge=1, le=50)
    budget: Optional[BudgetConstraint] = None
    preferences: dict[str, Any] = Field(
        default_factory=dict,
        description="Free-form preference bag (e.g. {'hotel_stars': 3, 'prefers_train': True})",
    )
    # Populated by Planner after parsing, not by the user directly
    session_id: Optional[str] = Field(None, description="Unique session identifier")

    @model_validator(mode="after")
    def return_after_departure(self) -> "TripRequest":
        if self.departure_date and self.return_date:
            if self.return_date <= self.departure_date:
                raise ValueError("return_date must be after departure_date")
        return self


class ItineraryItem(BaseModel):
    """A single line in the day-by-day itinerary."""

    day: int = Field(..., ge=1, description="1-indexed day number of the trip")
    time: Optional[str] = Field(
        None, description="HH:MM string (local time at destination)"
    )
    item_type: ItineraryItemType
    title: str
    description: Optional[str] = None
    location: Optional[Location] = None
    transport: Optional[TransportOption] = None
    accommodation: Optional[AccommodationOption] = None
    activity: Optional[ActivityRecommendation] = None
    estimated_cost: Optional[Money] = None
    notes: Optional[str] = None


class Itinerary(BaseModel):
    """
    The full trip plan, built incrementally by the Planner as agents respond.
    This is the top-level object that flows through the orchestration graph.
    """

    trip_request: TripRequest
    items: list[ItineraryItem] = Field(default_factory=list)
    total_cost: Optional[Money] = None
    within_budget: Optional[bool] = None  # set by Budget Agent
    budget_notes: Optional[str] = None    # Budget Agent's explanation
    status: BookingStatus = BookingStatus.draft
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    citations: list[str] = Field(
        default_factory=list,
        description="All RAG source citations referenced in this itinerary",
    )

class TripPlanPayload(BaseModel):
    """
    Structured cross-boundary trip plan payload delivered to the client UI.
    Contains flight, hotel, activity options, dates, and budget breakdown.
    """
    origin: Optional[str] = Field(None, description="Departure IATA code")
    origin_name: Optional[str] = Field(None, description="Departure city name")
    destination: Optional[str] = Field(None, description="Destination IATA code")
    destination_name: Optional[str] = Field(None, description="Destination city name")
    departure_date: Optional[str] = Field(None, description="Departure date (YYYY-MM-DD)")
    check_in: Optional[str] = Field(None, description="Accommodation check-in date (YYYY-MM-DD)")
    check_out: Optional[str] = Field(None, description="Accommodation check-out date (YYYY-MM-DD)")
    budget_cap: Optional[float] = Field(None, description="Budget cap in target currency")
    budget: Optional[float] = Field(None, description="Active budget cap if requested")
    budget_status: Optional[str] = Field(None, description="Budget status (e.g. within_budget / over_budget) if requested")
    target_currency: Optional[str] = Field("USD", description="Target currency code")
    display_currency: Optional[str] = Field("USD", description="Display currency code")
    transport_results: list[dict[str, Any]] = Field(default_factory=list, description="Transport / flight options")
    hotel_results: list[dict[str, Any]] = Field(default_factory=list, description="Hotel / accommodation options")
    local_guide_results: list[dict[str, Any]] = Field(default_factory=list, description="Local guide recommendations")
    budget_reconciliation: Optional[dict[str, Any]] = Field(None, description="Reconciliation summary")
    cost_line_items: list[dict[str, Any]] = Field(default_factory=list, description="Detailed cost line items")
    no_later_flight_found: bool = Field(False, description="True if a flight delay replan could not find later flights")
    lead_banner: Optional[str] = Field(None, description="Honest explanation or status note for the itinerary card")
    weather_alert: Optional[str] = Field(None, description="Proactive weather warning banner for extreme conditions")
    weather_tip: Optional[str] = Field(None, description="One-line deterministic weather tip for destination travel window")
    news_alert: Optional[str] = Field(None, description="Proactive travel advisory or disruption banner")


# ---------------------------------------------------------------------------
# A2A message envelope
# ---------------------------------------------------------------------------


class A2ARequest(BaseModel):
    """
    Minimal envelope for cross-agent A2A calls.
    Wraps the payload with routing metadata.
    """

    skill: str = Field(..., description="The A2A skill name to invoke (e.g. 'search_transport')")
    session_id: str = Field(..., description="Shared session ID for tracing across agents")
    payload: dict[str, Any] = Field(
        ..., description="Skill-specific parameters (will be validated by the receiving agent)"
    )
    timeout_seconds: int = Field(15, ge=1, le=120, description="A2A call timeout per Rules.md")


class A2AResponse(BaseModel):
    """
    Minimal envelope for cross-agent A2A responses.
    Errors are always structured — no raw stack traces cross agent boundaries.
    """

    skill: str
    session_id: str
    success: bool
    data: Optional[dict[str, Any]] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    # True when any data in this response is mock/estimated
    contains_estimated_data: bool = False
