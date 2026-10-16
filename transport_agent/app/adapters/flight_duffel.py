from __future__ import annotations
import os
import logging
from datetime import date, datetime
import httpx
from shared.schemas import DataQuality, Location, Money, TransportMode, TransportOption
from .base import TransportAdapter

logger = logging.getLogger(__name__)

class DuffelFlightAdapter(TransportAdapter):
    """
    Queries the Duffel API for flight offers.
    Falls back to empty list on missing credentials or API issues.
    """

    def __init__(self):
        self.access_token = os.getenv("DUFFEL_ACCESS_TOKEN")
        self.base_url = "https://api.duffel.com/air"

    def can_serve(self, origin: str, destination: str) -> bool:
        # Duffel supports 3-letter IATA airport codes
        return len(origin) == 3 and len(destination) == 3

    async def search_async(
        self,
        origin: str,
        destination: str,
        departure_date: date,
        num_travelers: int = 1,
        max_price: Optional[float] = None,
    ) -> list[TransportOption]:
        if not self.access_token:
            logger.info("Duffel access token not set. Returning empty list to trigger mock fallback.")
            return []

        is_test = self.access_token.startswith("duffel_test_")

        async with httpx.AsyncClient(timeout=5.0) as client:
            try:
                url = f"{self.base_url}/offer_requests?return_offers=true"
                headers = {
                    "Authorization": f"Bearer {self.access_token}",
                    "Duffel-Version": "v2",
                    "Content-Type": "application/json",
                    "Accept-Encoding": "gzip"
                }
                payload = {
                    "data": {
                        "slices": [
                            {
                                "origin": origin.upper(),
                                "destination": destination.upper(),
                                "departure_date": departure_date.strftime("%Y-%m-%d")
                            }
                        ],
                        "passengers": [{"type": "adult"} for _ in range(num_travelers)],
                        "cabin_class": "economy"
                    }
                }

                logger.info(f"Sending Duffel Offer Request to {url}")
                res = await client.post(url, headers=headers, json=payload)
                
                if res.status_code != 201:
                    logger.error(f"Duffel API error: {res.status_code} - {res.text}")
                    return []

                data = res.json()
                offers = data.get("data", {}).get("offers", [])
                
                options = []
                # Limit to top 3 offers
                for offer in offers[:3]:
                    offer_id = offer.get("id")
                    price_val = float(offer.get("total_amount", 0.0))
                    currency = offer.get("total_currency", "USD")
                    
                    owner = offer.get("owner", {})
                    carrier_name = owner.get("name", "Unknown Carrier")
                    carrier_code = owner.get("iata_code", "XX")
                    carrier = carrier_name if carrier_name else carrier_code

                    slices = offer.get("slices", [])
                    if not slices:
                        continue
                    
                    slice_item = slices[0]
                    segments = slice_item.get("segments", [])
                    if not segments:
                        continue
                    
                    first_seg = segments[0]
                    last_seg = segments[-1]
                    
                    dep_str = first_seg.get("departing_at")
                    arr_str = last_seg.get("arriving_at")
                    
                    # Parse timestamps
                    dep_time = datetime.fromisoformat(dep_str.replace("Z", "+00:00"))
                    arr_time = datetime.fromisoformat(arr_str.replace("Z", "+00:00"))
                    duration_minutes = int((arr_time - dep_time).total_seconds() / 60)

                    # Determine quality and estimated flag based on test mode
                    dq = DataQuality.estimated if is_test else DataQuality.live
                    is_est = True if is_test else False

                    opt = TransportOption(
                        mode=TransportMode.flight,
                        origin=Location(name=origin.upper(), iata=origin.upper()),
                        destination=Location(name=destination.upper(), iata=destination.upper()),
                        departure_at=dep_time,
                        arrival_at=arr_time,
                        duration_minutes=duration_minutes,
                        price=Money(amount=price_val, currency=currency),
                        carrier=carrier,
                        booking_ref=offer_id,
                        data_quality=dq,
                        is_estimated=is_est
                    )
                    options.append(opt)
                
                if max_price is not None:
                    options = [o for o in options if o.price.amount <= max_price]
                options.sort(key=lambda x: x.price.amount)
                return options

            except Exception as e:
                logger.error(f"Duffel Flight search failed: {e}")
                return []

    def search(
        self,
        origin: str,
        destination: str,
        departure_date: date,
        num_travelers: int = 1,
        max_price: Optional[float] = None,
    ) -> list[TransportOption]:
        import asyncio
        try:
            return asyncio.run(self.search_async(origin, destination, departure_date, num_travelers, max_price=max_price))
        except Exception as e:
            logger.error(f"Error in async search runner: {e}")
            return []
