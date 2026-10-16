"""
shared/resolvers.py
-------------------
Provides fast-path and dynamic geocoded city-to-IATA resolution and natural language currency resolution.

RESOLUTION PIPELINE:
1. Fast-Path Dictionary: Instant lookup for common international hubs (Delhi, Mumbai, London, Paris, Tokyo, etc.)
2. Direct 3-letter IATA code verification: Recognizes explicit uppercase airport codes (e.g. BOM, DEL, JFK, PNQ)
3. Dynamic Geocoding + OurAirports Lookup: Geocodes any city on Earth via Nominatim and maps to nearest commercial IATA code.
4. Strict Allowlist Gate: Unresolvable candidate strings, verbs, and non-place words return None.
"""

import os
import re
from datetime import datetime, date, timedelta
from typing import Optional

# Comprehensive fast-path canonical City -> (Title, IATA) mapping
CITY_IATA_MAP = {
    # India
    "delhi": {"name": "Delhi", "iata": "DEL"},
    "delih": {"name": "Delhi", "iata": "DEL"},
    "dilli": {"name": "Delhi", "iata": "DEL"},
    "new delhi": {"name": "Delhi", "iata": "DEL"},
    "mumbai": {"name": "Mumbai", "iata": "BOM"},
    "bombay": {"name": "Mumbai", "iata": "BOM"},
    "mumbay": {"name": "Mumbai", "iata": "BOM"},
    "pune": {"name": "Pune", "iata": "PNQ"},
    "poona": {"name": "Pune", "iata": "PNQ"},
    "kolkata": {"name": "Kolkata", "iata": "CCU"},
    "calcutta": {"name": "Kolkata", "iata": "CCU"},
    "bangalore": {"name": "Bengaluru", "iata": "BLR"},
    "banglore": {"name": "Bengaluru", "iata": "BLR"},
    "bengaluru": {"name": "Bengaluru", "iata": "BLR"},
    "bangaluru": {"name": "Bengaluru", "iata": "BLR"},
    "bengalore": {"name": "Bengaluru", "iata": "BLR"},
    "chennai": {"name": "Chennai", "iata": "MAA"},
    "madras": {"name": "Chennai", "iata": "MAA"},
    "hyderabad": {"name": "Hyderabad", "iata": "HYD"},
    "ahmedabad": {"name": "Ahmedabad", "iata": "AMD"},
    "jaipur": {"name": "Jaipur", "iata": "JAI"},
    "goa": {"name": "Goa", "iata": "GOI"},
    "kochi": {"name": "Kochi", "iata": "COK"},

    # Europe
    "london": {"name": "London", "iata": "LHR"},
    "londn": {"name": "London", "iata": "LHR"},
    "paris": {"name": "Paris", "iata": "CDG"},
    "parsi": {"name": "Paris", "iata": "CDG"},
    "lisbon": {"name": "Lisbon", "iata": "LIS"},
    "lisboa": {"name": "Lisbon", "iata": "LIS"},
    "porto": {"name": "Porto", "iata": "OPO"},
    "madrid": {"name": "Madrid", "iata": "MAD"},
    "barcelona": {"name": "Barcelona", "iata": "BCN"},
    "rome": {"name": "Rome", "iata": "FCO"},
    "milan": {"name": "Milan", "iata": "MXP"},
    "berlin": {"name": "Berlin", "iata": "BER"},
    "munich": {"name": "Munich", "iata": "MUC"},
    "frankfurt": {"name": "Frankfurt", "iata": "FRA"},
    "amsterdam": {"name": "Amsterdam", "iata": "AMS"},
    "vienna": {"name": "Vienna", "iata": "VIE"},
    "zurich": {"name": "Zurich", "iata": "ZRH"},
    "prague": {"name": "Prague", "iata": "PRG"},
    "dublin": {"name": "Dublin", "iata": "DUB"},
    "edinburgh": {"name": "Edinburgh", "iata": "EDI"},
    "athens": {"name": "Athens", "iata": "ATH"},
    "istanbul": {"name": "Istanbul", "iata": "IST"},
    "reykjavik": {"name": "Reykjavik", "iata": "KEF"},
    "keflavik": {"name": "Reykjavik", "iata": "KEF"},

    # North America
    "new york": {"name": "New York", "iata": "JFK"},
    "new york city": {"name": "New York", "iata": "JFK"},
    "nyc": {"name": "New York", "iata": "JFK"},
    "los angeles": {"name": "Los Angeles", "iata": "LAX"},
    "san francisco": {"name": "San Francisco", "iata": "SFO"},
    "chicago": {"name": "Chicago", "iata": "ORD"},
    "seattle": {"name": "Seattle", "iata": "SEA"},
    "miami": {"name": "Miami", "iata": "MIA"},
    "toronto": {"name": "Toronto", "iata": "YYZ"},
    "vancouver": {"name": "Vancouver", "iata": "YVR"},
    "mexico city": {"name": "Mexico City", "iata": "MEX"},

    # Asia & Oceania
    "tokyo": {"name": "Tokyo", "iata": "HND"},
    "tokio": {"name": "Tokyo", "iata": "HND"},
    "kyoto": {"name": "Kyoto", "iata": "KIX"},
    "osaka": {"name": "Osaka", "iata": "KIX"},
    "seoul": {"name": "Seoul", "iata": "ICN"},
    "singapore": {"name": "Singapore", "iata": "SIN"},
    "bangkok": {"name": "Bangkok", "iata": "BKK"},
    "phuket": {"name": "Phuket", "iata": "HKT"},
    "bali": {"name": "Bali", "iata": "DPS"},
    "kuala lumpur": {"name": "Kuala Lumpur", "iata": "KUL"},
    "hong kong": {"name": "Hong Kong", "iata": "HKG"},
    "shanghai": {"name": "Shanghai", "iata": "PVG"},
    "beijing": {"name": "Beijing", "iata": "PEK"},
    "sydney": {"name": "Sydney", "iata": "SYD"},
    "melbourne": {"name": "Melbourne", "iata": "MEL"},
    "auckland": {"name": "Auckland", "iata": "AKL"},

    # Middle East & Africa
    "dubai": {"name": "Dubai", "iata": "DXB"},
    "doha": {"name": "Doha", "iata": "DOH"},
    "cairo": {"name": "Cairo", "iata": "CAI"},
    "nairobi": {"name": "Nairobi", "iata": "NBO"},
    "johannesburg": {"name": "Johannesburg", "iata": "JNB"},
    "cape town": {"name": "Cape Town", "iata": "CPT"},
    "sao paulo": {"name": "Sao Paulo", "iata": "GRU"},
    "buenos aires": {"name": "Buenos Aires", "iata": "EZE"}
}

KNOWN_IATA_CODES = {v["iata"]: v["name"] for v in CITY_IATA_MAP.values()}

EUROPEAN_IATA_HUBS = {
    "BER", "MUC", "FRA", "HAM", "CGN", "STR", "DUS", "LEJ", "NUE",
    "AMS", "VIE", "ZRH", "BRU", "PAR", "CDG", "ORY", "LON", "LHR", "LGW", "STN",
    "LIS", "MAD", "BCN", "FCO", "MIL", "MXP", "LIN", "PRG", "WAW", "BUD", "CPH", "ARN", "OSL"
}

NATIONALITY_CURRENCY_MAP = {
    "indian": "INR",
    "india": "INR",
    "japanese": "JPY",
    "japan": "JPY",
    "european": "EUR",
    "europe": "EUR",
    "eu": "EUR",
    "american": "USD",
    "america": "USD",
    "us": "USD",
    "usa": "USD",
    "british": "GBP",
    "britain": "GBP",
    "uk": "GBP",
    "english": "GBP",
    "swiss": "CHF",
    "switzerland": "CHF",
    "australian": "AUD",
    "australia": "AUD",
    "canadian": "CAD",
    "canada": "CAD",
    "singapore": "SGD",
    "singaporean": "SGD",
    "emirati": "AED",
    "uae": "AED",
    "dubai": "AED"
}

CURRENCY_WORDS_MAP = {
    "indian rupee": "INR",
    "indian rupees": "INR",
    "rupee": "INR",
    "rupees": "INR",
    "inr": "INR",
    "rs": "INR",
    "us dollar": "USD",
    "us dollars": "USD",
    "american dollar": "USD",
    "american dollars": "USD",
    "dollar": "USD",
    "dollars": "USD",
    "usd": "USD",
    "euro": "EUR",
    "euros": "EUR",
    "eur": "EUR",
    "british pound": "GBP",
    "british pounds": "GBP",
    "pound": "GBP",
    "pounds": "GBP",
    "gbp": "GBP",
    "japanese yen": "JPY",
    "yen": "JPY",
    "jpy": "JPY",
    "canadian dollar": "CAD",
    "canadian dollars": "CAD",
    "cad": "CAD",
    "australian dollar": "AUD",
    "australian dollars": "AUD",
    "aud": "AUD",
    "swiss franc": "CHF",
    "swiss francs": "CHF",
    "chf": "CHF",
    "uae dirham": "AED",
    "uae dirhams": "AED",
    "dirham": "AED",
    "dirhams": "AED",
    "aed": "AED",
    "singapore dollar": "SGD",
    "singapore dollars": "SGD",
    "sgd": "SGD"
}

# Add [nationality] currency and [nationality] money phrases
for _nat, _code in NATIONALITY_CURRENCY_MAP.items():
    CURRENCY_WORDS_MAP[f"{_nat} currency"] = _code
    CURRENCY_WORDS_MAP[f"{_nat} currencies"] = _code
    CURRENCY_WORDS_MAP[f"{_nat} money"] = _code

CURRENCY_SYMBOLS_MAP = {
    "$": "USD",
    "€": "EUR",
    "£": "GBP",
    "¥": "JPY",
    "₹": "INR"
}

MONTHS_MAP = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sept": 9, "sep": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12
}

ENGLISH_NON_CITY_WORDS = {
    "going", "be", "weather", "like", "news", "know", "before", "road", "roads",
    "condition", "conditions", "traffic", "trip", "trips", "plan", "plans",
    "itinerary", "show", "tell", "see", "visit", "want", "think", "good",
    "great", "fine", "cool", "best", "what", "where", "how", "when", "why",
    "which", "who", "any", "some", "all", "can", "could", "would", "should",
    "will", "is", "are", "was", "were", "do", "does", "did", "have", "has",
    "had", "again", "now", "later", "instead", "please", "confirm", "conferm",
    "done", "book", "cost", "price", "budget", "flight", "flights", "hotel", "hotels",
    "stay", "stays", "day", "days", "night", "nights", "week", "weeks", "month", "months",
    "year", "years", "july", "june", "august", "september", "october",
    "november", "december", "january", "february", "march", "april", "may",
    "about", "around", "near", "there", "here", "with", "from", "for", "out"
}

IATA_COUNTRY_MAP = {
    # India (IN) -> INR
    "DEL": "IN", "BOM": "IN", "PNQ": "IN", "CCU": "IN", "BLR": "IN",
    "MAA": "IN", "HYD": "IN", "AMD": "IN", "JAI": "IN", "GOI": "IN", "COK": "IN",
    # United Kingdom (GB) -> GBP
    "LHR": "GB", "LGW": "GB", "STN": "GB", "MAN": "GB", "EDI": "GB", "GLA": "GB", "BHX": "GB", "LON": "GB",
    # Japan (JP) -> JPY
    "HND": "JP", "NRT": "JP", "KIX": "JP", "ITM": "JP", "CTS": "JP", "FUK": "JP", "TYO": "JP",
    # United States (US) -> USD
    "JFK": "US", "EWR": "US", "LGA": "US", "LAX": "US", "SFO": "US", "ORD": "US",
    "SEA": "US", "MIA": "US", "ATL": "US", "BOS": "US", "DFW": "US",
    # Canada (CA) -> CAD
    "YYZ": "CA", "YVR": "CA", "YUL": "CA", "YYC": "CA",
    # Australia (AU) -> AUD
    "SYD": "AU", "MEL": "AU", "BNE": "AU", "PER": "AU", "ADL": "AU",
    # Switzerland (CH) -> CHF
    "ZRH": "CH", "GVA": "CH", "BSL": "CH",
    # United Arab Emirates (AE) -> AED
    "DXB": "AE", "AUH": "AE", "SHJ": "AE",
    # Singapore (SG) -> SGD
    "SIN": "SG",
    # France (FR) -> EUR
    "CDG": "FR", "ORY": "FR", "NCE": "FR", "LYS": "FR", "PAR": "FR",
    # Germany (DE) -> EUR
    "BER": "DE", "MUC": "DE", "FRA": "DE", "HAM": "DE", "DUS": "DE",
    # Spain (ES) -> EUR
    "MAD": "ES", "BCN": "ES", "VLC": "ES",
    # Italy (IT) -> EUR
    "FCO": "IT", "MXP": "IT", "LIN": "IT", "VCE": "IT", "MIL": "IT",
    # Portugal (PT) -> EUR
    "LIS": "PT", "OPO": "PT", "FAO": "PT",
    # Other Eurozone
    "AMS": "NL", "VIE": "AT", "DUB": "IE", "ATH": "GR", "BRU": "BE", "HEL": "FI",
    # Thailand (TH) -> THB
    "BKK": "TH", "HKT": "TH", "CNX": "TH",
    # Malaysia (MY) -> MYR
    "KUL": "MY", "PEN": "MY",
    # New Zealand (NZ) -> NZD
    "AKL": "NZ", "WLG": "NZ", "CHC": "NZ",
    # South Africa (ZA) -> ZAR
    "JNB": "ZA", "CPT": "ZA",
    # Mexico (MX) -> MXN
    "MEX": "MX",
    # Brazil (BR) -> BRL
    "GRU": "BR", "GIG": "BR",
    # Egypt (EG) -> EGP
    "CAI": "EG",
    # Kenya (KE) -> KES
    "NBO": "KE",
    # South Korea (KR) -> KRW
    "ICN": "KR", "GMP": "KR",
    # China & Hong Kong
    "PEK": "CN", "PVG": "CN", "SHA": "CN", "CAN": "CN",
    "HKG": "HK",
    # Indonesia (ID) -> IDR
    "DPS": "ID", "CGK": "ID"
}

COUNTRY_CURRENCY_MAP = {
    "IN": "INR",
    "GB": "GBP",
    "JP": "JPY",
    "US": "USD",
    "CA": "CAD",
    "AU": "AUD",
    "CH": "CHF",
    "AE": "AED",
    "SG": "SGD",
    "FR": "EUR", "DE": "EUR", "ES": "EUR", "IT": "EUR", "PT": "EUR",
    "NL": "EUR", "AT": "EUR", "IE": "EUR", "GR": "EUR", "BE": "EUR", "FI": "EUR",
    "TH": "THB",
    "MY": "MYR",
    "NZ": "NZD",
    "ZA": "ZAR",
    "MX": "MXN",
    "BR": "BRL",
    "EG": "EGP",
    "KE": "KES",
    "KR": "KRW",
    "CN": "CNY",
    "HK": "HKD",
    "ID": "IDR"
}


def currency_for_city(iata_or_city: Optional[str]) -> str:
    """
    Returns the ISO 4217 currency for an origin airport or city
    (e.g., PNQ/Pune -> INR, LHR/London -> GBP, HND/Tokyo -> JPY, JFK/New York -> USD).
    Falls back to 'USD' for unknown countries.
    """
    if not iata_or_city:
        return "USD"

    val = str(iata_or_city).strip().upper()
    # 1. Direct 3-letter IATA match
    if len(val) == 3 and val in IATA_COUNTRY_MAP:
        c_code = IATA_COUNTRY_MAP[val]
        return COUNTRY_CURRENCY_MAP.get(c_code, "USD")

    # 2. Check canonical city map
    lower = str(iata_or_city).strip().lower()
    if lower in CITY_IATA_MAP:
        iata = CITY_IATA_MAP[lower]["iata"]
        c_code = IATA_COUNTRY_MAP.get(iata)
        if c_code:
            return COUNTRY_CURRENCY_MAP.get(c_code, "USD")

    # 3. Dynamic resolution
    resolved = validate_and_resolve_city(str(iata_or_city))
    if resolved:
        _, iata = resolved
        c_code = IATA_COUNTRY_MAP.get(iata.upper())
        if c_code:
            return COUNTRY_CURRENCY_MAP.get(c_code, "USD")

    return "USD"


def infer_default_currency_from_route(origin_iata: Optional[str], dest_iata: Optional[str]) -> Optional[str]:
    """
    Infers default currency based on route:
    - Domestic route (both cities in the same country): defaults to that country's currency.
    - Also provides origin city currency lookup.
    """
    if origin_iata:
        return currency_for_city(origin_iata)
    return None


def validate_and_resolve_city(raw_text: Optional[str]) -> Optional[tuple[str, str]]:
    """
    STRICT ALLOWLIST GATE:
    1. Checks fast-path canonical CITY_IATA_MAP.
    2. Checks known uppercase 3-letter IATA code.
    3. Falls through to dynamic Nominatim geocoding + nearest OurAirports commercial airport.
    4. Returns None if the input is not a genuine, resolvable place.
    """
    if not raw_text:
        return None

    cleaned = raw_text.strip().strip(",.!?\"'").strip()
    if not cleaned or len(cleaned) < 2:
        return None

    # Reject common English conversational words from being treated as cities
    lower_name = cleaned.lower()
    if lower_name in ENGLISH_NON_CITY_WORDS:
        return None

    # Reject long sentences from being treated as city names
    if len(cleaned.split()) > 3:
        return None

    # 1. Exact match against fast-path canonical city dictionary
    if lower_name in CITY_IATA_MAP:
        entry = CITY_IATA_MAP[lower_name]
        return entry["name"], entry["iata"]

    # 2. Exact match against known uppercase 3-letter IATA codes
    upper_name = cleaned.upper()
    if len(upper_name) == 3 and upper_name in KNOWN_IATA_CODES:
        return KNOWN_IATA_CODES[upper_name], upper_name

    # 3. Dynamic geocoding via Nominatim and OurAirports nearest IATA code
    try:
        from shared.geocoding import geocode_city_to_iata
        geocoded = geocode_city_to_iata(cleaned)
        if geocoded:
            return geocoded
    except Exception:
        pass

    return None


def resolve_city_to_iata(raw_text: str) -> tuple[str, str]:
    """
    Backward-compatible resolver. Returns ('Unknown City', 'UNK') if not in allowlist.
    """
    resolved = validate_and_resolve_city(raw_text)
    if resolved:
        return resolved
    return "Unknown City", "UNK"


def resolve_currency_name(text: str) -> Optional[str]:
    """
    Extracts explicit currency preference from natural language expressions.
    Supports single and multi-word currency expressions (e.g. 'Indian rupees', 'US dollars', 'in rupees', 'in ₹', 'in indian currency').
    """
    if not text:
        return None

    # 1. Match symbol pattern: e.g. "in ₹", "in €", "in $"
    m_sym = re.search(r'\b(?:in|into|currency)\s*([$€£¥₹])\b', text)
    if m_sym:
        return CURRENCY_SYMBOLS_MAP.get(m_sym.group(1))

    # Sort phrases by descending length so multi-word phrases match first
    sorted_phrases = sorted(CURRENCY_WORDS_MAP.keys(), key=len, reverse=True)

    # 2. Match natural word patterns: 'want in indian rupees', 'in rupees', 'show in GBP', 'prices in euros', 'pay in euros'
    for phrase in sorted_phrases:
        pattern = rf'\b(?:in|into|currency|get\s+(?:me\s+)?in|show\s+(?:me\s+)?in|prices?\s+in|want\s+in|make\s+it\s+in|convert\s+to|display\s+in|prefer\s+in|pay\s+in|pay\s+with|pay\s+using)\s+{re.escape(phrase)}\b'
        if re.search(pattern, text, re.IGNORECASE):
            return CURRENCY_WORDS_MAP[phrase]

    # 3. Direct desire/verb mention: 'want indian rupees', 'use rupees', 'make it euros', 'pay in euros'
    for phrase in sorted_phrases:
        pattern = rf'\b(?:want|make\s+it|show|display|convert\s+to|use|prefer|pay\s+in|pay\s+with|pay\s+using)\s+{re.escape(phrase)}\b'
        if re.search(pattern, text, re.IGNORECASE):
            return CURRENCY_WORDS_MAP[phrase]

    # 4. Direct nationality currency/money pattern e.g. 'in indian currency', 'swiss money'
    m_nat = re.search(r'\b(?:in\s+|pay\s+in\s+)?([a-zA-Z]+)\s+(?:currency|currencies|money)\b', text, re.IGNORECASE)
    if m_nat:
        nat_word = m_nat.group(1).lower()
        if nat_word in NATIONALITY_CURRENCY_MAP:
            return NATIONALITY_CURRENCY_MAP[nat_word]

    # 5. Standalone mention: e.g. 'Indian rupees', 'INR', 'USD'
    for phrase in sorted_phrases:
        if re.search(rf'\b{re.escape(phrase)}\b', text, re.IGNORECASE):
            return CURRENCY_WORDS_MAP[phrase]

    return None


def is_unresolvable_currency_query(text: str) -> bool:
    """
    Returns True if the text asks or specifies a currency, but resolve_currency_name() returned None.
    e.g. 'in martian currency', 'pay in pesos', 'what currency can I use', 'show in bitcoin'.
    """
    if not text:
        return False
    if resolve_currency_name(text) is not None:
        return False

    patterns = [
        r'\b(?:in|into|show\s+in|prices?\s+in|pay\s+in|pay\s+with|pay\s+using|convert\s+to|switch\s+to|change\s+to)\s+([a-zA-Z]+)\s+(?:currency|currencies|money)\b',
        r'\b([a-zA-Z]+)\s+(?:currency|currencies|money)\b',
        r'\b(?:which|what)\s+currenc(?:y|ies)\b',
        r'\b(?:currency|currencies)\b',
        r'\bpay\s+(?:in|with|using)\b',
        r'\b(?:in|into|show\s+(?:me\s+)?in|prices?\s+in|pay\s+in)\s+(?:pesos?|rubles?|dinars?|baht|shekels?|bitcoins?|crypto|rand|won|francs?|dollars?|pounds?|rupees?)\b',
    ]
    return any(re.search(pat, text, re.IGNORECASE) for pat in patterns)


DEFAULT_TRIP_LEAD_DAYS = int(os.getenv("DEFAULT_TRIP_LEAD_DAYS", "14"))
DEFAULT_TRIP_LENGTH_DAYS = 2


def get_default_trip_dates(today: Optional[date] = None, lead_days: Optional[int] = None, length_days: Optional[int] = None) -> tuple[str, str, str]:
    """
    Returns (departure_date, check_in, check_out) ISO strings.
    Departure = today + DEFAULT_TRIP_LEAD_DAYS (default 14 days)
    Return/Checkout = departure + length_days (default 2 nights)
    """
    base_date = today or date.today()
    lead = lead_days if lead_days is not None else DEFAULT_TRIP_LEAD_DAYS
    length = length_days if length_days is not None else DEFAULT_TRIP_LENGTH_DAYS
    dep = base_date + timedelta(days=lead)
    ret = dep + timedelta(days=length)
    return dep.isoformat(), dep.isoformat(), ret.isoformat()


def is_date_in_past(date_val: Optional[str | date], today: Optional[date] = None) -> bool:
    """
    Returns True if date_val is strictly before today's date.
    """
    if not date_val:
        return False
    ref = today or date.today()
    try:
        if isinstance(date_val, date):
            return date_val < ref
        d_str = str(date_val).split("T")[0].strip()
        if re.match(r'^\d{4}-\d{2}-\d{2}$', d_str):
            return date.fromisoformat(d_str) < ref
        return False
    except Exception:
        return False


def _make_date_dict(d1: date, d2: Optional[date] = None, is_past: bool = False) -> dict:
    if not d2:
        d2 = d1 + timedelta(days=2)
    return {
        "departure_date": d1.isoformat(),
        "check_in": d1.isoformat(),
        "check_out": d2.isoformat(),
        "is_past": is_past
    }


def resolve_dates_from_text(
    text: str,
    reference_date: Optional[date] = None,
    reference_year: Optional[int] = None
) -> tuple[Optional[dict], bool]:
    """
    Extracts departure and return/checkout dates from natural language text.
    - Dates without a year resolve to the next occurrence (this year, or next year if passed).
    - Explicit past dates are flagged with 'is_past': True in the result dict.
    Returns:
        tuple (parsed_dict, is_ambiguous):
        - (dict, False): parsed successfully with {"departure_date", "check_in", "check_out", "is_past"}.
        - (None, True): text contains an unparseable/ambiguous date attempt.
        - (None, False): no date mentioned at all.
    """
    ref_today = reference_date or date.today()
    ref_year = reference_year or ref_today.year

    # 1. ISO date patterns YYYY-MM-DD
    iso_matches = re.findall(r'\b(\d{4})-(\d{1,2})-(\d{1,2})\b', text)
    if iso_matches:
        try:
            dates = [date(int(y), int(m), int(d)) for y, m, d in iso_matches]
            is_past = dates[0] < ref_today
            if len(dates) >= 2:
                return _make_date_dict(dates[0], dates[1], is_past=is_past), False
            return _make_date_dict(dates[0], is_past=is_past), False
        except ValueError:
            return None, True

    # 2. DD/MM/YYYY or DD-MM-YYYY numeric formats
    slash_matches = re.findall(r'\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b', text)
    if slash_matches:
        try:
            dates = [date(int(y), int(m), int(d)) for d, m, y in slash_matches]
            is_past = dates[0] < ref_today
            if len(dates) >= 2:
                return _make_date_dict(dates[0], dates[1], is_past=is_past), False
            return _make_date_dict(dates[0], is_past=is_past), False
        except ValueError:
            return None, True

    month_names_pattern = '|'.join(sorted(MONTHS_MAP.keys(), key=len, reverse=True))

    # 3. Date Ranges: '15 to 18 September', '15-18 September'
    m_range = re.search(
        rf'\b(\d{{1,2}})(?:st|nd|rd|th)?\s*(?:to|-)\s*(\d{{1,2}})(?:st|nd|rd|th)?\s+({month_names_pattern})(?:\s+(\d{{4}}))?\b',
        text,
        re.IGNORECASE
    )
    if m_range:
        d1_str, d2_str, m_str, y_str = m_range.groups()
        m_val = MONTHS_MAP[m_str.lower()]
        try:
            if y_str:
                y_val = int(y_str)
                d1 = date(y_val, m_val, int(d1_str))
                d2 = date(y_val, m_val, int(d2_str))
                is_past = d1 < ref_today
            else:
                y_val = ref_today.year
                d1 = date(y_val, m_val, int(d1_str))
                d2 = date(y_val, m_val, int(d2_str))
                if d1 < ref_today:
                    y_val += 1
                    d1 = date(y_val, m_val, int(d1_str))
                    d2 = date(y_val, m_val, int(d2_str))
                is_past = False
            return _make_date_dict(d1, d2, is_past=is_past), False
        except ValueError:
            return None, True

    # 4. Explicit Range: '15 September to 18 September', 'from 15 September to 18 September'
    m_full_range = re.search(
        rf'\b(?:from\s+)?(\d{{1,2}})(?:st|nd|rd|th)?\s+({month_names_pattern})(?:\s+(\d{{4}}))?\s+(?:to|-)\s+(\d{{1,2}})(?:st|nd|rd|th)?\s+({month_names_pattern})(?:\s+(\d{{4}}))?\b',
        text,
        re.IGNORECASE
    )
    if m_full_range:
        d1_str, m1_str, y1_str, d2_str, m2_str, y2_str = m_full_range.groups()
        try:
            m1_val = MONTHS_MAP[m1_str.lower()]
            m2_val = MONTHS_MAP[m2_str.lower()]
            if y1_str:
                y1_val = int(y1_str)
                y2_val = int(y2_str) if y2_str else y1_val
                d1 = date(y1_val, m1_val, int(d1_str))
                d2 = date(y2_val, m2_val, int(d2_str))
                is_past = d1 < ref_today
            else:
                y1_val = ref_today.year
                d1 = date(y1_val, m1_val, int(d1_str))
                if d1 < ref_today:
                    y1_val += 1
                    d1 = date(y1_val, m1_val, int(d1_str))
                y2_val = y1_val
                d2 = date(y2_val, m2_val, int(d2_str))
                is_past = False
            return _make_date_dict(d1, d2, is_past=is_past), False
        except ValueError:
            return None, True

    # 5. Day Month: '15 September', '15th September', '15th of September 2026'
    m_day_month = re.search(
        rf'\b(\d{{1,2}})(?:st|nd|rd|th)?(?:\s+of)?\s+({month_names_pattern})(?:,?\s+(\d{{4}}))?\b',
        text,
        re.IGNORECASE
    )
    if m_day_month:
        d_str, m_str, y_str = m_day_month.groups()
        m_val = MONTHS_MAP[m_str.lower()]
        try:
            if y_str:
                y_val = int(y_str)
                d1 = date(y_val, m_val, int(d_str))
                is_past = d1 < ref_today
            else:
                y_val = ref_today.year
                d1 = date(y_val, m_val, int(d_str))
                if d1 < ref_today:
                    y_val += 1
                    d1 = date(y_val, m_val, int(d_str))
                is_past = False
            return _make_date_dict(d1, is_past=is_past), False
        except ValueError:
            return None, True

    # 6. Month Day: 'September 15', 'September 15th, 2026'
    m_month_day = re.search(
        rf'\b({month_names_pattern})\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(\d{{4}}))?\b',
        text,
        re.IGNORECASE
    )
    if m_month_day:
        m_str, d_str, y_str = m_month_day.groups()
        m_val = MONTHS_MAP[m_str.lower()]
        try:
            if y_str:
                y_val = int(y_str)
                d1 = date(y_val, m_val, int(d_str))
                is_past = d1 < ref_today
            else:
                y_val = ref_today.year
                d1 = date(y_val, m_val, int(d_str))
                if d1 < ref_today:
                    y_val += 1
                    d1 = date(y_val, m_val, int(d_str))
                is_past = False
            return _make_date_dict(d1, is_past=is_past), False
        except ValueError:
            return None, True

    # 7. Check for ambiguous/unparseable date expressions
    # Looks for date-specific prepositions ('on <expr>', 'departing <expr>', 'dates: <expr>')
    ambiguous_triggers = [
        r'\bon\s+([a-zA-Z0-9\s]+?)(?:[,.]|$|\s+(?:and|with|under|i\s+want|in\s+rupees|budget)\b)',
        r'\b(?:departing|leaving|departure\s+date|travel\s+date|dates?)\s+(?:is\s+|on\s+)?([a-zA-Z0-9\s]+?)(?:[,.]|$|\s+(?:and|with|under|i\s+want|in\s+rupees|budget)\b)'
    ]
    for trig in ambiguous_triggers:
        m_trig = re.search(trig, text, re.IGNORECASE)
        if m_trig:
            captured = m_trig.group(1).strip().lower()
            non_dates = {'the', 'a', 'an', 'my', 'our'}
            if captured not in non_dates and len(captured) >= 3:
                return None, True

    return None, False


def extract_route_cities_from_text(text: str) -> Optional[tuple[tuple[str, str], tuple[str, str]]]:
    """
    Extracts origin and destination from a complete route statement in text.
    Handles 'mumbai to delhi', 'from London to Lisbon', 'switch to mumbai to delhi',
    'different flight from Paris to Tokyo', 'mumbai to delhi instead', etc.
    Returns ((origin_name, origin_iata), (dest_name, dest_iata)) or None.
    """
    if not text:
        return None

    # Never extract route from questions about weather, news, safety, road conditions, or itinerary re-display
    lower_t = text.lower()
    if re.search(r'\b(?:weather|forecast|temperature|rain|rainy|hot|cold|snow|news|safe|safety|dangerous|danger|unrest|curfew|scams?|advisories?|warnings?|road\s+conditions?|road\s+closures?|traffic|itinerary\s+again|show\s+(?:me\s+)?(?:the\s+)?(?:itinerary|plan)|what\s+was\s+(?:the\s+)?(?:trip|itinerary|plan))\b', lower_t):
        return None

    # 1. First check standard route regex
    match_route = re.search(
        r'(?:plan\s+(?:a\s+)?trip\s+)?(?:from\s+)?([A-Za-z\s]+?)\s+to\s+([A-Za-z\s]+?)(?:\s+(?:on|from|in|budget|under|with|for|show\s+me|\d{4}|\d+\s+days?|\$|€|£|¥|₹|instead)|$)',
        text,
        re.IGNORECASE
    )
    if match_route:
        raw_orig = match_route.group(1).strip()
        raw_dest = match_route.group(2).strip()
        clean_orig = re.sub(
            r'^(?:(?:can\s+we\s+)?(?:change|switch|adjust|update|modify)(?:\s+(?:the|our|my))?(?:\s+(?:trip|flight|route))?(?:\s+to)?\s+|plan\s+(?:a\s+)?trip\s+(?:from\s+)?|trip\s+(?:from\s+)?|flights?\s+(?:from\s+)?|travel\s+(?:from\s+)?|fly\s+(?:from\s+)?|different\s+(?:trip|flight)?\s*(?:from\s+)?|what\s+about\s+(?:from\s+)?|how\s+about\s+(?:from\s+)?|from\s+)',
            '', raw_orig, flags=re.IGNORECASE
        ).strip()
        clean_dest = re.sub(r'\s+(?:instead|please)$', '', raw_dest, flags=re.IGNORECASE).strip()
        res_o = validate_and_resolve_city(clean_orig)
        res_d = validate_and_resolve_city(clean_dest)
        if res_o and res_d and res_o[1] != res_d[1] and res_o[1] != "UNK" and res_d[1] != "UNK":
            return res_o, res_d

    # 2. General candidate search across ' to ' boundaries
    stop_pattern = r'\b(?:next\s+\w+|this\s+\w+|tomorrow|yesterday|for\s+\d+|on\s+[\d-]+|from\s+[\d-]+|in\s+\d+|budget\b|under\b|with\b|in\s+[a-zA-Z]{3}\b|in\s+rupees\b|in\s+dollars\b|in\s+euros\b|in\s+pounds\b|in\s+yen\b|\d{4}-\d{2}-\d{2}|[$€£¥₹]|instead|please)\b'
    cleaned_full = re.split(stop_pattern, text, flags=re.IGNORECASE)[0].strip()

    for m in re.finditer(r'\bto\b', cleaned_full, re.IGNORECASE):
        before = cleaned_full[:m.start()].strip()
        after = cleaned_full[m.end():].strip()

        before_clean = re.sub(
            r'^(?:(?:can\s+we\s+)?(?:change|switch|adjust|update|modify)(?:\s+(?:the|our|my))?(?:\s+(?:trip|flight|route))?(?:\s+to)?\s+|plan\s+(?:a\s+)?trip\s+(?:from\s+)?|trip\s+(?:from\s+)?|flights?\s+(?:from\s+)?|travel\s+(?:from\s+)?|fly\s+(?:from\s+)?|different\s+(?:trip|flight)?\s*(?:from\s+)?|what\s+about\s+(?:from\s+)?|how\s+about\s+(?:from\s+)?|from\s+)',
            '', before, flags=re.IGNORECASE
        ).strip()
        
        words_before = before_clean.split()
        cand_origs = [' '.join(words_before[-n:]) for n in range(min(3, len(words_before)), 0, -1)]

        words_after = after.split()
        cand_dests = [' '.join(words_after[:n]) for n in range(min(3, len(words_after)), 0, -1)]

        for co in cand_origs:
            res_o = validate_and_resolve_city(co)
            if res_o and res_o[1] != "UNK":
                for cd in cand_dests:
                    res_d = validate_and_resolve_city(cd)
                    if res_d and res_d[1] != "UNK" and res_o[1] != res_d[1]:
                        return res_o, res_d
    return None
