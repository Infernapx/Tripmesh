import os
import logging
from datetime import date, datetime, timedelta
from typing import Optional
import httpx
from shared.schemas import DataQuality, Location, Money, AccommodationOption

logger = logging.getLogger(__name__)

import hashlib
import time
import re

HOTELBEDS_API_KEY = os.getenv("HOTELBEDS_API_KEY")
HOTELBEDS_API_SECRET = os.getenv("HOTELBEDS_API_SECRET")
HOTELBEDS_BASE_URL = os.getenv("HOTELBEDS_BASE_URL", "https://api.test.hotelbeds.com/hotel-api/1.0")
NOMINATIM_BASE_URL = os.getenv("NOMINATIM_BASE_URL", "https://nominatim.openstreetmap.org")

def compute_hotelbeds_signature(api_key: str, api_secret: str, timestamp: int | None = None) -> str:
    """
    Computes Hotelbeds X-Signature header:
    SHA256(HOTELBEDS_API_KEY + HOTELBEDS_API_SECRET + current_utc_unix_timestamp_in_seconds)
    Hex-digest encoded.
    """
    if timestamp is None:
        timestamp = int(time.time())
    raw = f"{api_key}{api_secret}{timestamp}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()

def _parse_coordinates(destination: str) -> tuple[float, float] | None:
    try:
        parts = destination.split(",")
        if len(parts) == 2:
            lat = float(parts[0].strip())
            lon = float(parts[1].strip())
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                return lat, lon
    except Exception:
        pass
    return None

async def _geocode_destination(client: httpx.AsyncClient, destination: str) -> tuple[float, float] | None:
    if not destination or len(destination.strip()) == 0:
        return None
    try:
        url = f"{NOMINATIM_BASE_URL}/search"
        headers = {
            "User-Agent": "TripMeshHotelAgent/1.0 (contact@example.com)"
        }
        params = {
            "q": destination,
            "format": "json",
            "limit": 1
        }
        logger.info(f"Nominatim Geocoding: requesting coordinates for '{destination}'")
        res = await client.get(url, headers=headers, params=params, timeout=10.0)
        res.raise_for_status()
        data = res.json()
        if data and isinstance(data, list) and len(data) > 0:
            lat = float(data[0]["lat"])
            lon = float(data[0]["lon"])
            logger.info(f"Nominatim Geocoding: resolved '{destination}' to ({lat}, {lon})")
            return lat, lon
    except Exception as e:
        logger.error(f"Nominatim Geocoding failed for '{destination}': {e}")
    return None

async def search_hotels_api(
    destination: str,
    check_in_str: str,
    check_out_str: str,
    num_travelers: int = 1,
    price_ceiling: Optional[float] = None,
    excluded_hotels: Optional[list[str]] = None,
    prefer_cheaper: bool = False
) -> list[AccommodationOption]:
    """
    Search hotels in a destination using Hotelbeds APItude Booking/Availability API.
    Falls back to mock list if credentials are not configured, geocoding fails, or on API errors.
    Filters by price_ceiling if provided and sorts ascending by price.
    """
    api_key = os.getenv("HOTELBEDS_API_KEY") or HOTELBEDS_API_KEY
    api_secret = os.getenv("HOTELBEDS_API_SECRET") or HOTELBEDS_API_SECRET
    base_url = os.getenv("HOTELBEDS_BASE_URL") or HOTELBEDS_BASE_URL

    if not api_key or not api_secret:
        logger.info("Hotelbeds credentials not set for Hotel search. Using mock hotels.")
        return _get_mock_hotels(destination, check_in_str, check_out_str, num_travelers, price_ceiling=price_ceiling, excluded_hotels=excluded_hotels, prefer_cheaper=prefer_cheaper)
        
    try:
        check_in = date.fromisoformat(check_in_str)
        check_out = date.fromisoformat(check_out_str)
    except Exception:
        today = date.today()
        check_in = today + timedelta(days=14)
        check_out = check_in + timedelta(days=2)
        
    num_nights = (check_out - check_in).days
    if num_nights <= 0:
        num_nights = 1

    is_sandbox = "api.test.hotelbeds.com" in base_url
    dq = DataQuality.estimated if is_sandbox else DataQuality.live
    is_est = True if is_sandbox else False
    
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            # Parse or Geocode coordinates
            coords = _parse_coordinates(destination)
            if coords:
                lat, lon = coords
                logger.info(f"Using direct coordinates: lat={lat}, lon={lon}")
            else:
                lat_lon = await _geocode_destination(client, destination)
                if not lat_lon:
                    logger.warning(f"Could not resolve destination '{destination}' to coordinates. Falling back to mocks.")
                    return _get_mock_hotels(destination, check_in_str, check_out_str, num_travelers, price_ceiling=price_ceiling, excluded_hotels=excluded_hotels, prefer_cheaper=prefer_cheaper)
                lat, lon = lat_lon

            timestamp = int(time.time())
            sig_raw = f"{api_key}{api_secret}{timestamp}"
            signature = hashlib.sha256(sig_raw.encode('utf-8')).hexdigest()

            headers = {
                "Api-key": api_key,
                "X-Signature": signature,
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Accept-Encoding": "gzip"
            }
            
            payload = {
                "stay": {
                    "checkIn": check_in.isoformat(),
                    "checkOut": check_out.isoformat()
                },
                "occupancies": [
                    {
                        "rooms": 1,
                        "adults": num_travelers,
                        "children": 0
                    }
                ],
                "geolocation": {
                    "latitude": lat,
                    "longitude": lon,
                    "radius": 20,
                    "unit": "km"
                }
            }

            url = f"{base_url.rstrip('/')}/hotels"
            logger.info(f"Sending Hotelbeds Availability Request to {url} with coordinates ({lat}, {lon})")
            res = await client.post(url, headers=headers, json=payload)
            
            if res.status_code != 200:
                logger.error(f"Hotelbeds API error: {res.status_code} - {res.text}. Falling back to mocks.")
                return _get_mock_hotels(destination, check_in_str, check_out_str, num_travelers, price_ceiling=price_ceiling, excluded_hotels=excluded_hotels, prefer_cheaper=prefer_cheaper)

            data = res.json()
            hotels_data = data.get("hotels", {}).get("hotels", [])
            
            if not hotels_data:
                logger.info("Hotelbeds search returned zero results. Falling back to mock hotels.")
                return _get_mock_hotels(destination, check_in_str, check_out_str, num_travelers, price_ceiling=price_ceiling, excluded_hotels=excluded_hotels, prefer_cheaper=prefer_cheaper)

            options = []
            for hotel in hotels_data[:5]:
                name = hotel.get("name", "Standard Hotel").title()
                code = hotel.get("code", "HOTEL")
                category_name = hotel.get("categoryName", "4 STARS")
                
                # Parse numeric star rating if available
                rating = 4.0
                if category_name:
                    match = re.search(r'([\d.]+)', category_name)
                    if match:
                        rating = float(match.group(1))

                # Parse coordinates if available
                h_lat = float(hotel.get("latitude", lat))
                h_lon = float(hotel.get("longitude", lon))
                loc_name = hotel.get("destinationName") or hotel.get("zoneName") or destination.upper()

                # Parse currency and minRate
                currency = hotel.get("currency", "EUR")
                min_rate_str = hotel.get("minRate", "120.0")
                try:
                    min_rate = float(min_rate_str)
                except ValueError:
                    min_rate = 120.0
                    
                price_val = round(min_rate / num_nights, 2)

                opt = AccommodationOption(
                    name=name,
                    location=Location(
                        name=loc_name,
                        lat=h_lat,
                        lon=h_lon
                    ),
                    check_in=check_in,
                    check_out=check_out,
                    price_per_night=Money(amount=price_val, currency=currency),
                    total_price=Money(amount=min_rate, currency=currency),
                    rating=rating,
                    amenities=["WiFi", "Air Conditioning", category_name],
                    booking_ref=f"HB-HT-{code}",
                    data_quality=dq,
                    is_estimated=is_est
                )
                options.append(opt)
            if excluded_hotels:
                options = [o for o in options if o.name not in excluded_hotels]
            if price_ceiling is not None:
                options = [o for o in options if o.total_price.amount <= price_ceiling]
            options.sort(key=lambda x: x.total_price.amount)
            return options
        except Exception as e:
            logger.error(f"Hotelbeds hotel search error: {e}. Falling back to mocks.")
            return _get_mock_hotels(destination, check_in_str, check_out_str, num_travelers, price_ceiling=price_ceiling, excluded_hotels=excluded_hotels, prefer_cheaper=prefer_cheaper)

from shared.resolvers import resolve_city_to_iata

def _get_mock_hotels(
    destination: str,
    check_in_str: str,
    check_out_str: str,
    num_travelers: int = 1,
    price_ceiling: Optional[float] = None,
    excluded_hotels: Optional[list[str]] = None,
    prefer_cheaper: bool = False
) -> list[AccommodationOption]:
    try:
        check_in = date.fromisoformat(check_in_str)
        check_out = date.fromisoformat(check_out_str)
    except Exception:
        today = date.today()
        check_in = today + timedelta(days=14)
        check_out = check_in + timedelta(days=2)
        
    num_nights = (check_out - check_in).days
    if num_nights <= 0:
        num_nights = 1
        
    city_name, city_code = resolve_city_to_iata(destination)
    
    # Destination-aware Mock hotels across price tiers
    opt1 = AccommodationOption(
        name=f"{city_name} Grand Palace Hotel",
        location=Location(name=f"{city_name} City Center", iata=city_code),
        check_in=check_in,
        check_out=check_out,
        price_per_night=Money(amount=180.0, currency="EUR"),
        total_price=Money(amount=180.0 * num_nights, currency="EUR"),
        rating=4.8,
        amenities=["WiFi", "Spa", "Fitness Center", "Bar"],
        booking_ref=f"MOCK-HT-{city_code}-202",
        data_quality=DataQuality.estimated,
        is_estimated=True
    )
    
    opt2 = AccommodationOption(
        name=f"{city_name} Central Boutique Inn",
        location=Location(name=f"{city_name} Downtown", iata=city_code),
        check_in=check_in,
        check_out=check_out,
        price_per_night=Money(amount=95.0, currency="EUR"),
        total_price=Money(amount=95.0 * num_nights, currency="EUR"),
        rating=4.2,
        amenities=["WiFi", "Free Coffee", "Central Location"],
        booking_ref=f"MOCK-HT-{city_code}-203",
        data_quality=DataQuality.estimated,
        is_estimated=True
    )

    opt3 = AccommodationOption(
        name=f"{city_name} Eco Traveler Hostel & Suites",
        location=Location(name=f"{city_name} Old Town", iata=city_code),
        check_in=check_in,
        check_out=check_out,
        price_per_night=Money(amount=55.0, currency="EUR"),
        total_price=Money(amount=55.0 * num_nights, currency="EUR"),
        rating=4.0,
        amenities=["WiFi", "Shared Kitchen", "Central Location"],
        booking_ref=f"MOCK-HT-{city_code}-204",
        data_quality=DataQuality.estimated,
        is_estimated=True
    )
    
    opts = [opt1, opt2]
    # Include budget tier when price ceiling is below 95/night, when cheaper is requested, or when alternatives are requested
    if (price_ceiling is not None and price_ceiling < (95.0 * num_nights)) or prefer_cheaper or excluded_hotels:
        opts.append(opt3)

    if excluded_hotels:
        opts = [o for o in opts if o.name not in excluded_hotels]

    if price_ceiling is not None:
        opts = [o for o in opts if o.total_price.amount <= price_ceiling]
    opts.sort(key=lambda x: x.total_price.amount)
    return opts
