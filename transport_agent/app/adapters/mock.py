from __future__ import annotations
from datetime import date, datetime, time
from shared.schemas import DataQuality, Location, Money, TransportMode, TransportOption
from .base import TransportAdapter

class MockTransportAdapter(TransportAdapter):
    """
    Fallback adapter. Always claims it can serve any route.
    Always marks results as estimated.
    """

    def can_serve(self, origin: str, destination: str) -> bool:
        return True  # always available

    def search(
        self,
        origin: str,
        destination: str,
        departure_date: date,
        num_travelers: int = 1,
        max_price: Optional[float] = None,
        delay_hours: Optional[float] = None,
        excluded_transports: Optional[list[str]] = None,
        prefer_different: bool = False,
    ) -> list[TransportOption]:
        # Formulate departure datetimes on the requested date
        dep1 = datetime.combine(departure_date, time(8, 15))
        arr1 = datetime.combine(departure_date, time(11, 0)) # 2h 45m duration (165 min)
        
        dep2 = datetime.combine(departure_date, time(14, 30))
        arr2 = datetime.combine(departure_date, time(19, 15)) # 4h 45m duration (285 min)
        
        from shared.resolvers import resolve_city_to_iata
        
        origin_name, origin_iata = resolve_city_to_iata(origin)
        dest_name, dest_iata = resolve_city_to_iata(destination)
        
        # Origin and Destination Locations
        origin_loc = Location(name=origin_name, iata=origin_iata)
        dest_loc = Location(name=dest_name, iata=dest_iata)

        opt1 = TransportOption(
            mode=TransportMode.flight,
            origin=origin_loc,
            destination=dest_loc,
            departure_at=dep1,
            arrival_at=arr1,
            duration_minutes=165,
            price=Money(amount=120.0 * num_travelers, currency="EUR"),
            carrier="MockAir Direct",
            booking_ref="MOCK-FL-101",
            co2_estimate_kg=145.0,
            data_quality=DataQuality.estimated,
            is_estimated=True
        )

        opt2 = TransportOption(
            mode=TransportMode.flight,
            origin=origin_loc,
            destination=dest_loc,
            departure_at=dep2,
            arrival_at=arr2,
            duration_minutes=285,
            price=Money(amount=85.0 * num_travelers, currency="EUR"),
            carrier="MockAir Connecting",
            booking_ref="MOCK-FL-102",
            co2_estimate_kg=190.0,
            data_quality=DataQuality.estimated,
            is_estimated=True
        )

        dep3 = datetime.combine(departure_date, time(19, 0))
        arr3 = datetime.combine(departure_date, time(21, 45))
        opt3 = TransportOption(
            mode=TransportMode.flight,
            origin=origin_loc,
            destination=dest_loc,
            departure_at=dep3,
            arrival_at=arr3,
            duration_minutes=165,
            price=Money(amount=135.0 * num_travelers, currency="EUR"),
            carrier="MockAir Evening",
            booking_ref="MOCK-FL-103",
            co2_estimate_kg=150.0,
            data_quality=DataQuality.estimated,
            is_estimated=True
        )

        dep4 = datetime.combine(departure_date, time(16, 20))
        arr4 = datetime.combine(departure_date, time(18, 55))
        opt4 = TransportOption(
            mode=TransportMode.flight,
            origin=origin_loc,
            destination=dest_loc,
            departure_at=dep4,
            arrival_at=arr4,
            duration_minutes=155,
            price=Money(amount=110.0 * num_travelers, currency="EUR"),
            carrier="MockAir Afternoon Express",
            booking_ref="MOCK-FL-104",
            co2_estimate_kg=140.0,
            data_quality=DataQuality.estimated,
            is_estimated=True
        )

        opts = [opt1, opt2]
        if delay_hours is not None:
            opts.append(opt3)
        if prefer_different or excluded_transports:
            opts.append(opt4)
        if excluded_transports:
            filtered = [o for o in opts if o.carrier not in excluded_transports and o.booking_ref not in excluded_transports]
            if filtered:
                opts = filtered
            elif opt4 not in opts:
                opts = [opt4]
        if max_price is not None:
            opts = [o for o in opts if o.price.amount <= max_price]
        opts.sort(key=lambda x: x.price.amount)
        return opts

