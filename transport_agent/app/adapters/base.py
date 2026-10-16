"""
transport_agent/app/adapters/base.py
-------------------------------------
TransportAdapter interface — the contract every mode adapter must implement.

Architecture.md §3a: each adapter declares which regions/routes it covers
and implements search(). The Transport Agent queries every adapter that
claims coverage for the requested origin/destination and merges results.

Phase 0: interface only. Concrete adapters are implemented in Phase 3 / 3a.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from shared.schemas import TransportOption


class TransportAdapter(ABC):
    """
    Abstract base class for all transport mode adapters.

    Subclasses must implement:
      - can_serve(origin, destination) → bool
      - search(origin, destination, departure_date, num_travelers) → list[TransportOption]

    Rules (from Rules.md §3 and Architecture.md §3a):
    - If can_serve returns False, the adapter must not be queried.
    - If search raises or returns nothing, the caller falls back to mock.py.
    - Results from adapters without a real API key must set
      data_quality=DataQuality.estimated and is_estimated=True.
    - Never present estimated results as real fares.
    """

    @abstractmethod
    def can_serve(self, origin: str, destination: str) -> bool:
        """
        Return True if this adapter has real coverage for the given
        origin → destination pair (e.g. country code, region, IATA pair).
        Must never claim coverage it cannot actually provide.
        """
        ...

    @abstractmethod
    def search(
        self,
        origin: str,
        destination: str,
        departure_date: date,
        num_travelers: int = 1,
        max_price: Optional[float] = None,
    ) -> list[TransportOption]:
        """
        Return a list of transport options for the given route and date.

        On failure (API error, timeout, etc.):
          - Log the error.
          - Return an empty list (caller will fall back to mock).
          - Do NOT raise — exceptions must not cross the adapter boundary
            into the agent (consistent with Rules.md §4 error handling).
        """
        ...
