"""
shared/weather.py
-----------------
Real-time weather forecast and proactive travel alerts powered by Open-Meteo.
Uses OpenStreetMap Nominatim geocoding from shared.geocoding.
Free, keyless API with honest degradation on network failures and date boundaries.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Optional

import httpx

from shared.geocoding import geocode_destination_coords

logger = logging.getLogger(__name__)

OPEN_METEO_BASE_URL = "https://api.open-meteo.com/v1/forecast"


HAZARD_HEAT_C = 38.0
HAZARD_FREEZING_C = 0.0
HAZARD_HEAVY_RAIN_MM = 20.0
HAZARD_HEAVY_RAIN_PCT = 60
HAZARD_HIGH_WIND_KMH = 50.0


def evaluate_weather_alert(
    max_high: float,
    min_low: float,
    max_precip: float = 0.0,
    rain_prob_pct: int = 0,
    max_wind: float = 0.0
) -> Optional[str]:
    """
    Evaluates weather stats against centralized hazard thresholds.
    Quotes the exact figures matching the summary.
    """
    if max_high >= HAZARD_HEAT_C:
        return f"⚠️ Heat alert: Temperatures reach {round(max_high)}°C during your travel window — stay hydrated and limit midday outdoor exposure."
    if min_low <= HAZARD_FREEZING_C:
        return f"⚠️ Freezing alert: Temperatures drop to {round(min_low)}°C — pack heavy winter layers."
    if rain_prob_pct >= HAZARD_HEAVY_RAIN_PCT or max_precip >= HAZARD_HEAVY_RAIN_MM:
        return f"⚠️ Heavy rain alert: {rain_prob_pct}% chance of rain (up to {round(max_precip, 1)}mm) — carry sturdy rain gear."
    if max_wind >= HAZARD_HIGH_WIND_KMH:
        return f"⚠️ High wind alert: Winds up to {round(max_wind)} km/h — outdoor plans may be affected."
    return None


def generate_weather_tip(
    destination: str,
    max_high: float,
    min_high: float,
    min_low: float,
    max_low: float,
    rain_prob: int = 0,
    max_precip: float = 0.0,
    is_typical_climate: bool = False,
    month_name: Optional[str] = None
) -> str:
    """
    Generates a deterministic single-sentence weather tip (max ~110 chars)
    from the single source of truth weather stats.
    Priority:
    storm/heavy rain (umbrella/raincoat) > extreme heat >= 38°C > heat >= 32°C > cold <= 10°C > rain chance >= 40% > pleasant
    """
    dest_clean = destination.strip()
    if is_typical_climate:
        month_label = month_name or "this month"
        if round(min_low) != round(max_high):
            temp_str = f"{round(min_low)}-{round(max_high)}°C"
        else:
            temp_str = f"{round(max_high)}°C"

        if rain_prob >= 60 or max_precip >= 20.0:
            tip = f"Usually rainy in {month_label} ({temp_str}), carry an umbrella."
        elif max_high >= 38:
            tip = f"Usually extremely hot in {month_label} ({temp_str}), hydrate and avoid midday sun."
        elif max_high >= 32:
            tip = f"Usually hot in {month_label} ({temp_str}), pack light cotton and sunscreen."
        elif min_low <= 10 or max_high <= 10:
            tip = f"Usually chilly in {month_label} ({temp_str}), pack warm layers."
        elif rain_prob >= 40:
            tip = f"Usually rainy in {month_label} ({temp_str}), carry an umbrella."
        else:
            tip = f"Usually pleasant in {month_label} ({temp_str}), great for exploring outdoors."
        return tip[:110]

    # Live forecast
    if round(min_high) != round(max_high):
        temp_str = f"{round(min_high)}-{round(max_high)}°C"
    else:
        temp_str = f"{round(max_high)}°C"

    if rain_prob >= 60 or max_precip >= 20.0:
        tip = f"Heavy rain expected in {dest_clean} ({temp_str}), carry an umbrella."
    elif max_high >= 38:
        tip = f"Extremely hot in {dest_clean} ({temp_str}), hydrate and avoid midday sun."
    elif max_high >= 32:
        tip = f"Hot in {dest_clean} ({temp_str}), pack light cotton and sunscreen."
    elif min_low <= 10 or max_high <= 10:
        tip = f"Chilly in {dest_clean} ({temp_str}), pack warm layers."
    elif rain_prob >= 40:
        tip = f"Passing showers expected in {dest_clean} ({temp_str}), carry an umbrella."
    else:
        tip = f"Pleasant in {dest_clean} ({temp_str}), great for exploring outdoors."

    return tip[:110]


# Monthly typical climatology database for key destinations
# Format: {city_key: {month (1-12): (min_temp, max_temp, rain_chance_pct, tip, alert_if_any)}}
TYPICAL_CLIMATOLOGY: dict[str, dict[int, tuple[int, int, int, str, Optional[str]]]] = {
    "delhi": {
        1: (8, 21, 10, "Pack warm layers for cool mornings and evening fog.", None),
        2: (11, 24, 15, "Pleasant daytime weather, ideal for sightseeing with light layers.", None),
        3: (16, 30, 15, "Warm spring weather; comfortable for walking tours in breathable clothes.", None),
        4: (22, 37, 10, "Hot daytime sun; carry sunblock, sunglasses, and hydrate regularly.", "⚠️ High daytime heat: Temperatures often cross 37°C."),
        5: (26, 40, 15, "Peak summer heat; avoid midday outdoor exposure and drink lots of fluids.", "⚠️ Extreme summer heat: Temperatures frequently exceed 40°C."),
        6: (28, 39, 30, "Very hot with rising humidity and occasional pre-monsoon dust storms.", "⚠️ Severe heat warning: June is one of the hottest months in Delhi."),
        7: (27, 35, 65, "Monsoon season begins; carry an umbrella and expect traffic delays during heavy showers.", "⚠️ Heavy rain risk: Waterlogging and travel delays are common."),
        8: (26, 34, 70, "Humid and rainy; carry rain gear and wear water-resistant footwear.", "⚠️ High humidity and frequent monsoon showers."),
        9: (25, 34, 45, "Warm and humid with receding rains; light cotton clothing recommended.", None),
        10: (19, 33, 10, "Pleasant autumn weather; great for outdoor monuments and markets.", None),
        11: (13, 28, 5, "Cool and comfortable; light jackets recommended for mornings and evenings.", None),
        12: (8, 22, 10, "Chilly winter; pack warm sweaters or jackets for chilly nights.", None),
    },
    "dubai": {
        1: (15, 24, 10, "Mild and pleasant winter weather; perfect for beach and desert activities.", None),
        2: (16, 26, 15, "Comfortable warm days; light jacket useful for desert evenings.", None),
        3: (18, 29, 10, "Warm and sunny; ideal beach weather.", None),
        4: (22, 34, 5, "Getting hot; plan outdoor activities for morning or evening.", None),
        5: (26, 38, 2, "Very hot; stay in air-conditioned spaces during midday.", "⚠️ High heat: Temperatures frequently cross 38°C."),
        6: (28, 40, 0, "Intense summer heat; outdoor sightseeing is not recommended during daytime.", "⚠️ Extreme heat: Midday temperatures exceed 40°C."),
        7: (31, 42, 0, "Extreme heat and humidity; keep outdoor activities strictly indoors or nocturnal.", "⚠️ Extreme heat warning: July temperatures reach 42°C+ with high humidity."),
        8: (32, 42, 0, "Peak summer heat; stick to air-conditioned venues, malls, and indoor attractions.", "⚠️ Extreme heat warning: August in Dubai is intensely hot."),
        9: (28, 39, 0, "Still very warm; coastal breezes pick up in late evening.", "⚠️ High heat: Temperatures remain near 40°C."),
        10: (24, 35, 2, "Pleasant transition into autumn; great for evening dining.", None),
        11: (20, 31, 5, "Warm and sunny; excellent for desert safaris and water sports.", None),
        12: (17, 26, 10, "Delightful winter climate; comfortable lightweight clothing with a light evening layer.", None),
    },
    "pune": {
        1: (12, 30, 2, "Pleasant, clear skies with cool nights; carry a light shawl or sweater.", None),
        2: (14, 33, 2, "Sunny and dry; comfortable daytime sightseeing.", None),
        3: (18, 36, 5, "Warm afternoons; cotton clothes and sun protection advised.", None),
        4: (22, 38, 10, "Hot summer days; stay hydrated and plan indoor afternoon visits.", "⚠️ Hot summer days: Peak afternoon temperatures near 38°C."),
        5: (23, 37, 20, "Warm with occasional pre-monsoon thundershowers in the evening.", None),
        6: (23, 31, 60, "Monsoon arrives; lush green surroundings, carry umbrellas and raincoats.", "⚠️ Monsoon onset: Expect frequent rains."),
        7: (22, 28, 80, "Heavy monsoon showers; ideal for nature treks with waterproof gear.", "⚠️ High rain volume: Pack heavy rain protection."),
        8: (21, 28, 80, "Lush green and wet; beautiful misty weather in nearby hills.", None),
        9: (21, 29, 55, "Pleasant post-monsoon weather; occasional light showers.", None),
        10: (19, 31, 25, "Warm days and cool nights; great for all outdoor travel.", None),
        11: (15, 30, 10, "Crisp, comfortable winter days; light jacket for late evenings.", None),
        12: (12, 29, 2, "Pleasant and cool; wonderful travel weather throughout the day.", None),
    },
    "mumbai": {
        1: (17, 31, 2, "Pleasant winter weather with low humidity; comfortable for coastal strolls.", None),
        2: (18, 32, 2, "Warm and dry; light summer clothes recommended.", None),
        3: (22, 33, 2, "Warm with rising humidity; stay hydrated.", None),
        4: (24, 34, 2, "Hot and humid; breathable light cottons are best.", None),
        5: (27, 34, 10, "Very humid and warm; prepare for sweaty afternoons.", None),
        6: (26, 32, 75, "Monsoon onset; intense torrential downpours and waterlogging possible.", "⚠️ Heavy monsoon: Carry sturdy rain gear and plan for transport delays."),
        7: (25, 30, 90, "Peak monsoon; heavy rains every day. Waterproof shoes are essential.", "⚠️ Severe rainfall alert: Frequent waterlogging and localized disruptions."),
        8: (25, 30, 85, "Continuous rainfall and overcast skies; pack reliable rain gear.", "⚠️ Heavy monsoon rains continue."),
        9: (25, 31, 60, "Intermittent rain and high humidity; muggy afternoons.", None),
        10: (24, 33, 20, "Post-monsoon 'October heat'; warm afternoons with sunshine.", None),
        11: (21, 34, 5, "Pleasant weather returns; great for sightseeing and beach walks.", None),
        12: (18, 32, 2, "Delightful weather with sea breezes; comfortable all day.", None),
    },
    "london": {
        1: (3, 9, 50, "Cold and damp; pack a warm coat, scarf, and umbrella.", None),
        2: (3, 9, 45, "Chilly winter; thermal layers and water-resistant outerwear recommended.", None),
        3: (5, 12, 40, "Brisk spring weather; layer up with a trench coat or jacket.", None),
        4: (7, 15, 35, "Mild spring days; carry a light jacket and compact umbrella.", None),
        5: (10, 18, 35, "Pleasant spring; comfortable for walking tours in parks.", None),
        6: (13, 21, 40, "Long daylight hours and mild temperatures; great for sightseeing.", None),
        7: (15, 23, 35, "Warm summer days; light clothing with a light layer for evenings.", None),
        8: (15, 23, 40, "Comfortable and pleasant; popular festival and event month.", None),
        9: (12, 20, 45, "Mild early autumn; comfortable walking weather.", None),
        10: (9, 16, 55, "Crisp autumn air with falling leaves; carry an umbrella and medium coat.", None),
        11: (6, 12, 60, "Chilly and damp; warm layers and rain jacket needed.", None),
        12: (4, 9, 55, "Cold winter; festive lights and markets, dress warmly.", None),
    },
    "paris": {
        1: (3, 8, 50, "Cold winter; bundle up with a wool coat and warm accessories.", None),
        2: (3, 9, 45, "Chilly; warm coats and comfortable walking boots recommended.", None),
        3: (5, 13, 40, "Crisp spring; layers and a waterproof jacket are ideal.", None),
        4: (8, 16, 40, "Mild spring; blooming gardens, comfortable for city walking.", None),
        5: (11, 20, 45, "Lovely warm weather; cafe terraces and outdoor walking.", None),
        6: (14, 23, 40, "Warm early summer; pleasant evenings along the Seine.", None),
        7: (16, 26, 35, "Warm to hot summer days; light clothing and sunglasses.", None),
        8: (16, 26, 35, "Warm and sunny; stay hydrated during midday walking.", None),
        9: (13, 22, 40, "Pleasant autumn days; stylish light layers are perfect.", None),
        10: (10, 17, 50, "Cool and romantic autumn; light coat and scarf recommended.", None),
        11: (6, 11, 55, "Chilly and overcast; warm coat and umbrella needed.", None),
        12: (3, 8, 55, "Cold festive winter; warm winter clothing required.", None),
    },
    "tokyo": {
        1: (2, 10, 20, "Crisp and sunny winter days; pack a warm coat and thermal layers.", None),
        2: (3, 11, 25, "Chilly but mostly clear; warm coat and gloves advised.", None),
        3: (6, 14, 45, "Early spring with cherry blossom season beginning; light coat needed.", None),
        4: (11, 19, 45, "Pleasant spring; comfortable for walking tours in parks.", None),
        5: (16, 23, 45, "Comfortable warm days; light clothing with a light evening layer.", None),
        6: (19, 26, 65, "Rainy season (Tsuyu); carry a sturdy umbrella and light breathable clothes.", "⚠️ Rainy season: Frequent precipitation and dampness."),
        7: (23, 30, 50, "Hot and humid summer; stay hydrated and seek air-conditioned shade.", None),
        8: (24, 31, 45, "Peak summer heat and humidity; carry a hand fan and water bottle.", None),
        9: (21, 27, 60, "Warm with occasional typhoons; monitor local weather advisories.", "⚠️ Typhoon season: Monitor local weather advisories."),
        10: (15, 22, 45, "Delightful autumn weather; clear skies and colorful foliage.", None),
        11: (10, 17, 30, "Cool and crisp autumn; light jacket or sweater recommended.", None),
        12: (5, 12, 20, "Cold winter; dress warmly for evening strolls and illuminations.", None),
    },
}

MONTH_NAMES = {
    1: "January", 2: "February", 3: "March", 4: "April",
    5: "May", 6: "June", 7: "July", 8: "August",
    9: "September", 10: "October", 11: "November", 12: "December"
}

MONTH_NAME_TO_INT = {v.lower(): k for k, v in MONTH_NAMES.items()}


def get_typical_climate(city: str, month: int) -> tuple[int, int, int, str, Optional[str]]:
    """Returns (min_temp, max_temp, rain_chance_pct, tip, alert) for a given city and month."""
    city_lower = city.lower().strip()
    for key, data in TYPICAL_CLIMATOLOGY.items():
        if key in city_lower or city_lower in key:
            if month in data:
                return data[month]

    # Latitude/general seasonal fallback
    coords = geocode_destination_coords(city)
    lat = coords[0] if coords else 25.0
    is_northern = lat >= 0
    summer_months = {6, 7, 8} if is_northern else {12, 1, 2}
    winter_months = {12, 1, 2} if is_northern else {6, 7, 8}

    if month in summer_months:
        return (22, 32, 30, "Warm summer weather; pack lightweight clothing and sun protection.", None)
    elif month in winter_months:
        return (5, 14, 40, "Cool winter season; pack warm layers and a weather-resistant jacket.", None)
    else:
        return (14, 24, 30, "Mild transitional season; comfortable clothing with light layers recommended.", None)


async def get_weather_forecast(
    destination: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    client: Optional[httpx.AsyncClient] = None
) -> dict[str, Any]:
    """
    Fetches real-time grounded weather forecast for a destination using Open-Meteo.
    Handles date boundaries honestly:
    - If dates are beyond ~16 days or a specific month is queried, provides typical climate.
    - Format is strictly 2-4 lines:
      Line 4: Alert line (only for real hazards)
    """
    if not destination or not destination.strip():
        return {
            "success": False,
            "destination": "",
            "destination_name": "",
            "forecast": [],
            "summary": "Could not find weather or coordinates for destination.",
            "alert": None,
            "weather_tip": None,
            "max_high": 25,
            "min_high": 25,
            "min_low": 15,
            "max_low": 15,
            "rain_prob": 0,
            "is_typical_climate": False,
        }

    coords = geocode_destination_coords(destination)
    resolved_name = coords[2] if coords else destination

    # Extract target month if specified in text or dates
    target_month: Optional[int] = None
    if start_date:
        for m_name, m_int in MONTH_NAME_TO_INT.items():
            if m_name in start_date.lower():
                target_month = m_int
                break
        if not target_month:
            try:
                target_month = datetime.strptime(start_date.split("T")[0], "%Y-%m-%d").date().month
            except Exception:
                pass

    today = date.today()
    target_start: Optional[date] = None
    target_end: Optional[date] = None

    is_now_query = bool(start_date and any(w in start_date.lower() for w in ["now", "today", "right now", "current"]))

    if start_date:
        try:
            target_start = datetime.strptime(start_date.split("T")[0], "%Y-%m-%d").date()
        except Exception:
            pass
    if end_date:
        try:
            target_end = datetime.strptime(end_date.split("T")[0], "%Y-%m-%d").date()
        except Exception:
            pass

    # If user explicitly asked for 'now/today' OR the provided start_date is in the past:
    # use current conditions plus the next 3 days. Never describe a past window as a forecast!
    if is_now_query or (target_start and target_start < today and not target_month):
        target_start = today
        target_end = today + timedelta(days=3)
        is_now_query = True

    # Check if this query is beyond 16 days or explicitly targeting a month
    is_far_future = False
    if target_start:
        days_ahead = (target_start - today).days
        if days_ahead > 16:
            is_far_future = True

    if is_far_future or (not target_start and target_month):
        month_int = target_month or today.month
        month_label = MONTH_NAMES.get(month_int, "that month")
        t_min, t_max, rain_chance, tip, hazard = get_typical_climate(destination, month_int)
        
        lines = [
            f"🌤️ **Typical Climate for {resolved_name} in {month_label}** (Historical Typical, not a live forecast):",
            f"• **Temperature**: Highs around {t_max}°C, Lows around {t_min}°C.",
            f"• **Rain Chance**: Approximately {rain_chance}% chance of precipitation.",
            f"• **Travel Tip**: {tip}"
        ]
        if hazard:
            lines.append(f"• **Hazard Alert**: {hazard}")

        weather_tip = generate_weather_tip(
            destination=resolved_name,
            max_high=t_max,
            min_high=t_max,
            min_low=t_min,
            max_low=t_min,
            rain_prob=rain_chance,
            is_typical_climate=True,
            month_name=month_label
        )
        return {
            "success": True,
            "destination": destination,
            "destination_name": resolved_name,
            "forecast": [],
            "summary": "\n".join(lines),
            "alert": hazard,
            "weather_tip": weather_tip,
            "max_high": t_max,
            "min_high": t_max,
            "min_low": t_min,
            "max_low": t_min,
            "rain_prob": rain_chance,
            "is_typical_climate": True
        }

    if not coords:
        # Fallback to typical climate for current month
        month_int = today.month
        month_label = MONTH_NAMES.get(month_int, "this season")
        t_min, t_max, rain_chance, tip, hazard = get_typical_climate(destination, month_int)
        lines = [
            f"🌤️ **Typical Weather for {destination} in {month_label}**:",
            f"• **Temperature**: Highs around {t_max}°C, Lows around {t_min}°C.",
            f"• **Rain Chance**: Around {rain_chance}% chance of rain.",
            f"• **Travel Tip**: {tip}"
        ]
        weather_tip = generate_weather_tip(
            destination=destination,
            max_high=t_max,
            min_high=t_max,
            min_low=t_min,
            max_low=t_min,
            rain_prob=rain_chance,
            is_typical_climate=True,
            month_name=month_label
        )
        return {
            "success": True,
            "destination": destination,
            "destination_name": destination,
            "forecast": [],
            "summary": "\n".join(lines),
            "alert": hazard,
            "weather_tip": weather_tip,
            "max_high": t_max,
            "min_high": t_max,
            "min_low": t_min,
            "max_low": t_min,
            "rain_prob": rain_chance,
            "is_typical_climate": True
        }

    lat, lon, resolved_name = coords

    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,wind_speed_10m_max",
        "timezone": "auto",
        "forecast_days": 16
    }

    try:
        # Strict timeout <= 8s with 1 retry
        data = None
        for attempt in range(2):
            try:
                if client:
                    resp = await client.get(OPEN_METEO_BASE_URL, params=params, timeout=8.0)
                else:
                    async with httpx.AsyncClient(timeout=8.0) as ac:
                        resp = await ac.get(OPEN_METEO_BASE_URL, params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    break
            except Exception as e:
                if attempt == 1:
                    raise e

        if not data:
            raise RuntimeError("Empty response from Open-Meteo")

        daily = data.get("daily", {})
        times = daily.get("time", [])
        t_maxs = daily.get("temperature_2m_max", [])
        t_mins = daily.get("temperature_2m_min", [])
        precips = daily.get("precipitation_sum", [])
        winds = daily.get("wind_speed_10m_max", [])

        forecast_list: list[dict[str, Any]] = []
        for i, dt_str in enumerate(times):
            if target_start:
                try:
                    d_obj = datetime.strptime(dt_str, "%Y-%m-%d").date()
                    if d_obj < target_start:
                        continue
                    if target_end and d_obj > target_end:
                        continue
                except Exception:
                    pass

            forecast_list.append({
                "date": dt_str,
                "temp_max": t_maxs[i] if i < len(t_maxs) else None,
                "temp_min": t_mins[i] if i < len(t_mins) else None,
                "precipitation": precips[i] if i < len(precips) else 0.0,
                "wind_speed": winds[i] if i < len(winds) else 0.0,
            })

        if not forecast_list and times:
            for i in range(min(5, len(times))):
                forecast_list.append({
                    "date": times[i],
                    "temp_max": t_maxs[i] if i < len(t_maxs) else None,
                    "temp_min": t_mins[i] if i < len(t_mins) else None,
                    "precipitation": precips[i] if i < len(precips) else 0.0,
                    "wind_speed": winds[i] if i < len(winds) else 0.0,
                })

        # Compute summary stats across forecast window
        valid_maxs = [f["temp_max"] for f in forecast_list if f.get("temp_max") is not None]
        valid_mins = [f["temp_min"] for f in forecast_list if f.get("temp_min") is not None]
        valid_precips = [f.get("precipitation", 0.0) for f in forecast_list]
        valid_winds = [f.get("wind_speed", 0.0) for f in forecast_list]

        max_high = round(max(valid_maxs)) if valid_maxs else 25
        min_high = round(min(valid_maxs)) if valid_maxs else 25
        max_low = round(max(valid_mins)) if valid_mins else 15
        min_low = round(min(valid_mins)) if valid_mins else 15
        max_precip = round(max(valid_precips), 1) if valid_precips else 0.0
        max_wind = round(max(valid_winds), 1) if valid_winds else 0.0

        rainy_days = sum(1 for p in valid_precips if p >= 1.0)
        rain_prob = round((rainy_days / len(forecast_list)) * 100) if forecast_list else 10

        # Consistent temperature range string
        if min_high != max_high:
            temp_high_str = f"Highs {min_high}–{max_high}°C"
        else:
            temp_high_str = f"Highs around {max_high}°C"

        if min_low != max_low:
            temp_low_str = f"Lows {min_low}–{max_low}°C"
        else:
            temp_low_str = f"Lows around {min_low}°C"

        # Evaluate hazard alert with the exact same max_high and min_low figures
        alert = evaluate_weather_alert(
            max_high=max_high,
            min_low=min_low,
            max_precip=max_precip,
            rain_prob_pct=rain_prob,
            max_wind=max_wind
        )

        # General travel tip based on temperature
        if max_high >= 38:
            tip = "Expect hot conditions; plan outdoor sightseeing in early mornings or evenings and stay hydrated."
        elif max_high > 32:
            tip = "Dress in lightweight, breathable clothes, carry sunglasses, and stay well hydrated."
        elif min_low < 10:
            tip = "Pack warm layers, sweaters, or a coat for chilly mornings and nights."
        elif rain_prob >= 40:
            tip = "Carry a compact umbrella or light raincoat for passing showers."
        else:
            tip = "Comfortable mild weather; great for outdoor walking tours and sightseeing."

        window_label = "current conditions & upcoming days" if is_now_query else "your travel window"
        lines = [
            f"🌤️ **Weather Forecast for {resolved_name}** (Open-Meteo):",
            f"• **Temperature**: {temp_high_str}, {temp_low_str}.",
            f"• **Rain Chance**: {rain_prob}% chance of rain across {window_label}.",
            f"• **Travel Tip**: {tip}"
        ]
        if alert:
            lines.append(f"• **Hazard Alert**: {alert}")

        weather_tip = generate_weather_tip(
            destination=resolved_name,
            max_high=max_high,
            min_high=min_high,
            min_low=min_low,
            max_low=max_low,
            rain_prob=rain_prob,
            max_precip=max_precip,
            is_typical_climate=False
        )
        return {
            "success": True,
            "destination": destination,
            "destination_name": resolved_name,
            "forecast": forecast_list,
            "summary": "\n".join(lines),
            "alert": alert,
            "weather_tip": weather_tip,
            "max_high": max_high,
            "min_high": min_high,
            "min_low": min_low,
            "max_low": max_low,
            "rain_prob": rain_prob
        }

    except Exception as exc:
        logger.warning(f"Failed to fetch Open-Meteo weather for {resolved_name}: {exc}; falling back to typical climate")
        month_int = (target_start.month if target_start else today.month)
        month_label = MONTH_NAMES.get(month_int, "this season")
        t_min, t_max, rain_chance, tip, hazard = get_typical_climate(destination, month_int)
        lines = [
            f"🌤️ **Typical Weather for {resolved_name} in {month_label}** (Live forecast unavailable):",
            f"• **Temperature**: Highs around {t_max}°C, Lows around {t_min}°C.",
            f"• **Rain Chance**: Around {rain_chance}% chance of precipitation.",
            f"• **Travel Tip**: {tip}"
        ]
        if hazard:
            lines.append(f"• **Hazard Alert**: {hazard}")
        weather_tip = generate_weather_tip(
            destination=resolved_name,
            max_high=t_max,
            min_high=t_max,
            min_low=t_min,
            max_low=t_min,
            rain_prob=rain_chance,
            is_typical_climate=True,
            month_name=month_label
        )
        return {
            "success": True,
            "destination": destination,
            "destination_name": resolved_name,
            "forecast": [],
            "summary": "\n".join(lines),
            "alert": hazard,
            "weather_tip": weather_tip,
            "max_high": t_max,
            "min_high": t_max,
            "min_low": t_min,
            "max_low": t_min,
            "rain_prob": rain_chance,
            "is_typical_climate": True
        }

