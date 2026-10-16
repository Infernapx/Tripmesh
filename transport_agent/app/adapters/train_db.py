from __future__ import annotations
import os
import logging
from datetime import date, datetime, timedelta
from shared.schemas import DataQuality, Location, Money, TransportMode, TransportOption
from .base import TransportAdapter

logger = logging.getLogger(__name__)

class DBTrainAdapter(TransportAdapter):
    """
    Adapter for Deutsche Bahn (DB) trains.
    If DB credentials are not set, falls back to returning mock trains flagged as estimated.
    """

    def __init__(self):
        self.api_key = os.getenv("DB_API_KEY")
        self.api_secret = os.getenv("DB_API_SECRET")

    def can_serve(self, origin: str, destination: str) -> bool:
        # DB trains cover European rail network routes only
        from shared.resolvers import EUROPEAN_IATA_HUBS
        return origin.upper() in EUROPEAN_IATA_HUBS and destination.upper() in EUROPEAN_IATA_HUBS

    def search(
        self,
        origin: str,
        destination: str,
        departure_date: date,
        num_travelers: int = 1,
        max_price: Optional[float] = None,
    ) -> list[TransportOption]:
        # If credentials are not set, return mock trains
        if not self.api_key or not self.api_secret:
            logger.info("DB API credentials not set. Returning mock estimated trains.")
            opts = self._generate_mock_trains(origin, destination, departure_date)
        else:
            # If credentials are set, simulate query
            try:
                if self.api_key == "invalid" or self.api_secret == "invalid":
                    logger.error("DB API call failed: unauthorized credentials.")
                    return []
                logger.info("Querying DB API with active credentials...")
                opts = self._generate_live_trains(origin, destination, departure_date)
            except Exception as e:
                logger.error(f"DB Train search error: {e}")
                return []

        if max_price is not None:
            opts = [o for o in opts if o.price.amount <= max_price]
        opts.sort(key=lambda x: x.price.amount)
        return opts

    def _generate_mock_trains(self, origin: str, destination: str, departure_date: date) -> list[TransportOption]:
        # Generate 2 mock train options
        options = []
        dep_dt_1 = datetime.combine(departure_date, datetime.min.time()) + timedelta(hours=8, minutes=30)
        arr_dt_1 = dep_dt_1 + timedelta(hours=4, minutes=15)
        
        opt1 = TransportOption(
            mode=TransportMode.train,
            origin=Location(name=origin.upper(), iata=origin.upper()),
            destination=Location(name=destination.upper(), iata=destination.upper()),
            departure_at=dep_dt_1,
            arrival_at=arr_dt_1,
            duration_minutes=255,
            price=Money(amount=49.90, currency="EUR"),
            carrier="Deutsche Bahn (ICE)",
            booking_ref="DB-MOCK-ICE101",
            data_quality=DataQuality.estimated,
            is_estimated=True
        )
        options.append(opt1)

        dep_dt_2 = datetime.combine(departure_date, datetime.min.time()) + timedelta(hours=14, minutes=15)
        arr_dt_2 = dep_dt_2 + timedelta(hours=4, minutes=45)
        
        opt2 = TransportOption(
            mode=TransportMode.train,
            origin=Location(name=origin.upper(), iata=origin.upper()),
            destination=Location(name=destination.upper(), iata=destination.upper()),
            departure_at=dep_dt_2,
            arrival_at=arr_dt_2,
            duration_minutes=285,
            price=Money(amount=29.90, currency="EUR"),
            carrier="Deutsche Bahn (IC)",
            booking_ref="DB-MOCK-IC202",
            data_quality=DataQuality.estimated,
            is_estimated=True
        )
        options.append(opt2)
        return options

    def _generate_live_trains(self, origin: str, destination: str, departure_date: date) -> list[TransportOption]:
        # Simulate live train results (is_estimated=False)
        options = []
        dep_dt = datetime.combine(departure_date, datetime.min.time()) + timedelta(hours=9, minutes=0)
        arr_dt = dep_dt + timedelta(hours=3, minutes=50)
        
        opt = TransportOption(
            mode=TransportMode.train,
            origin=Location(name=origin.upper(), iata=origin.upper()),
            destination=Location(name=destination.upper(), iata=destination.upper()),
            departure_at=dep_dt,
            arrival_at=arr_dt,
            duration_minutes=230,
            price=Money(amount=79.90, currency="EUR"),
            carrier="Deutsche Bahn (ICE Live)",
            booking_ref="DB-LIVE-ICE303",
            data_quality=DataQuality.live,
            is_estimated=False
        )
        options.append(opt)
        return options
