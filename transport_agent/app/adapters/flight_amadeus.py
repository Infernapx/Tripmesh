from __future__ import annotations
import os
import logging
from datetime import date, datetime
import httpx
from shared.schemas import DataQuality, Location, Money, TransportMode, TransportOption
from .base import TransportAdapter

logger = logging.getLogger(__name__)

class AmadeusFlightAdapter(TransportAdapter):
    """
    Queries the Amadeus self-service API for flight offers.
    Falls back to empty list on missing credentials or API issues.
    """

    def __init__(self):
        self.api_key = os.getenv("AMADEUS_API_KEY")
        self.api_secret = os.getenv("AMADEUS_API_SECRET")
        self.base_url = "https://test.api.amadeus.com"

    def can_serve(self, origin: str, destination: str) -> bool:
        # Amadeus supports 3-letter IATA airport codes
        return len(origin) == 3 and len(destination) == 3

    async def _get_access_token(self, client: httpx.AsyncClient) -> str:
        url = f"{self.base_url}/v1/security/oauth2/token"
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        data = {
            "grant_type": "client_credentials",
            "client_id": self.api_key,
            "client_secret": self.api_secret
        }
        res = await client.post(url, headers=headers, data=data, timeout=10.0)
        res.raise_for_status()
        return res.json()["access_token"]

    async def search_async(
        self,
        origin: str,
        destination: str,
        departure_date: date,
        num_travelers: int = 1,
    ) -> list[TransportOption]:
        if not self.api_key or not self.api_secret:
            logger.info("Amadeus credentials not set. Returning empty list to trigger mock fallback.")
            return []

        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                # 1. Fetch token
                token = await self._get_access_token(client)
                
                # 2. Query Flight Offers Search
                url = f"{self.base_url}/v2/shopping/flight-offers"
                headers = {"Authorization": f"Bearer {token}"}
                params = {
                    "originLocationCode": origin.upper(),
                    "destinationLocationCode": destination.upper(),
                    "departureDate": departure_date.strftime("%Y-%m-%d"),
                    "adults": str(num_travelers),
                    "max": "3"
                }
                res = await client.get(url, headers=headers, params=params)
                res.raise_for_status()
                data = res.json()
                
                options = []
                for offer in data.get("data", []):
                    # Parse carrier and duration from first itinerary
                    itinerary = offer["itineraries"][0]
                    segment = itinerary["segments"][0]
                    
                    carrier = segment.get("carrierCode", "Unknown Carrier")
                    dep_time = datetime.fromisoformat(segment["departure"]["at"].replace("Z", "+00:00"))
                    arr_time = datetime.fromisoformat(segment["arrival"]["at"].replace("Z", "+00:00"))
                    
                    # Duration from ISO-8601 (e.g. PT2H45M)
                    duration_minutes = int((arr_time - dep_time).total_seconds() / 60)
                    
                    price_val = float(offer["price"]["total"])
                    currency = offer["price"]["currency"]
                    
                    opt = TransportOption(
                        mode=TransportMode.flight,
                        origin=Location(name=origin.upper(), iata=origin.upper()),
                        destination=Location(name=destination.upper(), iata=destination.upper()),
                        departure_at=dep_time,
                        arrival_at=arr_time,
                        duration_minutes=duration_minutes,
                        price=Money(amount=price_val, currency=currency),
                        carrier=carrier,
                        booking_ref=offer.get("id"),
                        data_quality=DataQuality.live,
                        is_estimated=False
                    )
                    options.append(opt)
                return options
            except Exception as e:
                logger.error(f"Amadeus Flight search failed: {e}")
                return []

    def search(
        self,
        origin: str,
        destination: str,
        departure_date: date,
        num_travelers: int = 1,
    ) -> list[TransportOption]:
        # Helper to run async search synchronously
        import asyncio
        try:
            return asyncio.run(self.search_async(origin, destination, departure_date, num_travelers))
        except Exception as e:
            logger.error(f"Error in async search runner: {e}")
            return []
