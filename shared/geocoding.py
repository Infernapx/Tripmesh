"""
shared/geocoding.py
-------------------
Provides live Nominatim geocoding and OurAirports-based nearest commercial IATA lookup.
Includes an in-memory cache and strict validation to reject nonsense words.
"""

import os
import csv
import math
import logging
import httpx
from typing import Optional

logger = logging.getLogger(__name__)

# In-memory geocode cache: {normalized_query: (city_name, iata_code)}
_GEOCODE_CACHE: dict[str, Optional[tuple[str, str]]] = {}

# In-memory coordinates cache: {normalized_query: (lat, lon, place_name)}
_COORDS_CACHE: dict[str, Optional[tuple[float, float, str]]] = {
    "tokyo": (35.6762, 139.6503, "Tokyo"),
    "kyoto": (35.0116, 135.7681, "Kyoto"),
    "lisbon": (38.7223, -9.1393, "Lisbon"),
    "paris": (48.8566, 2.3522, "Paris"),
    "london": (51.5074, -0.1278, "London"),
    "new york": (40.7128, -74.0060, "New York"),
    "mumbai": (19.0760, 72.8777, "Mumbai"),
    "delhi": (28.6139, 77.2090, "Delhi"),
    "santorini": (36.3932, 25.4615, "Santorini"),
    "rome": (41.9028, 12.4964, "Rome"),
    "bali": (-8.3405, 115.0920, "Bali"),
    "reykjavik": (64.1466, -21.9426, "Reykjavik"),
    "phoenix": (33.4484, -112.0740, "Phoenix"),
    "cairo": (30.0444, 31.2357, "Cairo"),
}

# Loaded airport dataset: list of dicts
_AIRPORTS_DATA: list[dict] = []

VALID_CLASSES = {"place", "boundary"}
VALID_TYPES = {"city", "town", "village", "administrative", "municipality", "state", "country", "island", "region"}
BLOCKLIST_WORDS = {
    "tell", "visit", "tel", "vis", "rupees", "rupee", "inr", "dollars", "dollar", "usd", "euros", "euro", "eur",
    "pounds", "pound", "gbp", "yen", "jpy", "temples", "temple", "what", "where", "how", "when",
    "why", "who", "which", "sounds", "somewhere", "anywhere", "here", "there", "it", "this", "that",
    "flights", "flight", "hotels", "hotel", "trips", "trip", "vacation", "holiday", "travel",
    "next", "week", "month", "today", "tomorrow", "weather", "like"
}


def _load_airports_dataset():
    global _AIRPORTS_DATA
    if _AIRPORTS_DATA:
        return

    csv_path = os.path.join(os.path.dirname(__file__), "data", "airports.csv")
    if not os.path.exists(csv_path):
        logger.warning(f"airports.csv not found at {csv_path}")
        return

    try:
        with open(csv_path, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                iata = r.get("iata_code", "").strip().upper()
                if iata and len(iata) == 3:
                    try:
                        lat = float(r.get("latitude_deg", 0))
                        lon = float(r.get("longitude_deg", 0))
                        _AIRPORTS_DATA.append({
                            "iata": iata,
                            "name": r.get("name", "").strip(),
                            "type": r.get("type", "").strip(),
                            "lat": lat,
                            "lon": lon,
                            "municipality": r.get("municipality", "").strip(),
                            "country": r.get("iso_country", "").strip()
                        })
                    except ValueError:
                        continue
        logger.info(f"Loaded {len(_AIRPORTS_DATA)} commercial IATA airports from {csv_path}")
    except Exception as e:
        logger.error(f"Failed to load airports.csv: {e}")


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def find_nearest_commercial_iata(lat: float, lon: float) -> Optional[tuple[str, str]]:
    """
    Finds the nearest commercial IATA airport code for the given coordinates.
    Prioritizes large_airport and medium_airport within 200km before falling back to small_airport.
    Returns (iata_code, airport_name).
    """
    _load_airports_dataset()
    if not _AIRPORTS_DATA:
        return None

    candidates = []
    for a in _AIRPORTS_DATA:
        dist = haversine_km(lat, lon, a["lat"], a["lon"])
        candidates.append((dist, a))

    candidates.sort(key=lambda x: x[0])

    # Priority 1: Large or Medium commercial airport within 200km
    for dist, a in candidates[:25]:
        if a["type"] in ["large_airport", "medium_airport"] and dist <= 200:
            return a["iata"], a["name"]

    # Priority 2: Small commercial airport within 100km
    for dist, a in candidates[:15]:
        if a["type"] == "small_airport" and dist <= 100:
            return a["iata"], a["name"]

    # Priority 3: Nearest airport within 400km
    if candidates and candidates[0][0] <= 400:
        return candidates[0][1]["iata"], candidates[0][1]["name"]

    return None


def geocode_city_to_iata(city_name: str) -> Optional[tuple[str, str]]:
    """
    Geocodes city_name using OpenStreetMap Nominatim and resolves to nearest commercial IATA code.
    Enforces strict place-type validation and rejects non-place words.
    Returns (city_title, iata_code) or None.
    """
    if not city_name:
        return None

    cleaned = city_name.strip().strip(",.!?\"'").strip()
    if not cleaned or len(cleaned) < 2:
        return None

    lower_query = cleaned.lower()
    if lower_query in BLOCKLIST_WORDS:
        return None

    # Check cache
    if lower_query in _GEOCODE_CACHE:
        return _GEOCODE_CACHE[lower_query]

    headers = {"User-Agent": "TripMesh-Travel-Planner/1.0 (contact: support@tripmesh.local)"}
    try:
        with httpx.Client(timeout=8.0, headers=headers) as client:
            resp = client.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": cleaned, "format": "json", "limit": 1, "addressdetails": 1}
            )
            if resp.status_code != 200:
                _GEOCODE_CACHE[lower_query] = None
                return None

            data = resp.json()
            if not data:
                _GEOCODE_CACHE[lower_query] = None
                return None

            first = data[0]
            cat = first.get("class", "")
            tp = first.get("type", "")

            # Strict place type validation
            if cat not in VALID_CLASSES or tp not in VALID_TYPES:
                _GEOCODE_CACHE[lower_query] = None
                return None

            # Place name alignment
            place_name = first.get("name", "").lower()
            disp_name = first.get("display_name", "").lower()
            first_part = disp_name.split(",")[0].strip() if disp_name else ""

            if lower_query != place_name and lower_query != first_part and lower_query not in place_name:
                _GEOCODE_CACHE[lower_query] = None
                return None

            lat = float(first["lat"])
            lon = float(first["lon"])

            airport_match = find_nearest_commercial_iata(lat, lon)
            if not airport_match:
                _GEOCODE_CACHE[lower_query] = None
                return None

            iata_code, _ = airport_match
            resolved_title = first.get("name", "").title() or cleaned.title()
            result = (resolved_title, iata_code)
            _GEOCODE_CACHE[lower_query] = result
            logger.info(f"Geocoded '{cleaned}' -> {result[0]} ({result[1]}) via Nominatim")
            return result

    except Exception as e:
        logger.warning(f"Geocoding error for '{cleaned}': {e}")

    return None


def geocode_destination_coords(place_name: str) -> Optional[tuple[float, float, str]]:
    """
    Resolves a place name or IATA code to (lat, lon, resolved_name).
    Uses in-memory cache, IATA airport dataset, and OpenStreetMap Nominatim.
    """
    if not place_name:
        return None

    cleaned = place_name.strip().strip(",.!?\"'").strip()
    if not cleaned or len(cleaned) < 2:
        return None

    lower_query = cleaned.lower()
    if lower_query in _COORDS_CACHE:
        return _COORDS_CACHE[lower_query]

    # Check if place_name is a 3-letter IATA code
    if len(cleaned) == 3 and cleaned.isalpha():
        _load_airports_dataset()
        for a in _AIRPORTS_DATA:
            if a["iata"] == cleaned.upper():
                resolved = (a["lat"], a["lon"], a["municipality"] or a["name"])
                _COORDS_CACHE[lower_query] = resolved
                return resolved

    headers = {"User-Agent": "TripMesh-Travel-Planner/1.0 (contact: support@tripmesh.local)"}
    try:
        with httpx.Client(timeout=8.0, headers=headers) as client:
            resp = client.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": cleaned, "format": "json", "limit": 1, "addressdetails": 1}
            )
            if resp.status_code == 200:
                data = resp.json()
                if data:
                    first = data[0]
                    lat = float(first["lat"])
                    lon = float(first["lon"])
                    resolved_title = first.get("name", "").title() or cleaned.title()
                    res = (lat, lon, resolved_title)
                    _COORDS_CACHE[lower_query] = res
                    return res
    except Exception as e:
        logger.warning(f"Geocoding coords error for '{cleaned}': {e}")

    _COORDS_CACHE[lower_query] = None
    return None
