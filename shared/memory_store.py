"""
shared/memory_store.py
----------------------
SQLite-backed persistent memory store for TripMesh (Phase 7).
Tracks cross-session user preferences (currency, inferred hotel tier, frequent origin)
and historical confirmed trips across browser sessions and process restarts.
"""

from __future__ import annotations

import os
import json
import sqlite3
import logging
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

# Default SQLite database file path in shared/data directory
DEFAULT_DB_DIR = os.path.join(os.path.dirname(__file__), "data")
DEFAULT_DB_PATH = os.path.join(DEFAULT_DB_DIR, "tripmesh_memory.db")


@dataclass
class PastTrip:
    id: int
    user_id: str
    destination: str
    destination_name: str
    origin: str
    origin_name: str
    departure_date: Optional[str]
    check_in: Optional[str]
    check_out: Optional[str]
    currency: str
    budget_cap: Optional[float]
    confirmed_at: str
    trip_summary: Dict[str, Any] = field(default_factory=dict)


@dataclass
class UserPreference:
    user_id: str
    currency: Optional[str] = None
    hotel_tier: Optional[str] = None  # "budget", "mid", "luxury"
    frequent_origin: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class UserMemory:
    user_id: str
    preference: UserPreference
    past_trips: List[PastTrip] = field(default_factory=list)

    @property
    def currency(self) -> Optional[str]:
        return self.preference.currency

    @property
    def hotel_tier(self) -> Optional[str]:
        return self.preference.hotel_tier

    @property
    def frequent_origin(self) -> Optional[str]:
        return self.preference.frequent_origin


def get_db_path(custom_path: Optional[str] = None) -> str:
    """Resolves the database path from argument, environment variable, or default."""
    if custom_path:
        return custom_path
    env_path = os.getenv("TRIPMESH_DB_PATH")
    if env_path:
        return env_path
    os.makedirs(DEFAULT_DB_DIR, exist_ok=True)
    return DEFAULT_DB_PATH


def get_db_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Returns a SQLite connection with row factory enabled and tables initialized."""
    path = get_db_path(db_path)
    parent_dir = os.path.dirname(os.path.abspath(path))
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Initializes the database schema if tables do not exist."""
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_preferences (
                user_id TEXT PRIMARY KEY,
                currency TEXT,
                hotel_tier TEXT,
                frequent_origin TEXT,
                updated_at TEXT NOT NULL
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS past_trips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                destination TEXT NOT NULL,
                destination_name TEXT,
                origin TEXT,
                origin_name TEXT,
                departure_date TEXT,
                check_in TEXT,
                check_out TEXT,
                currency TEXT,
                budget_cap REAL,
                confirmed_at TEXT NOT NULL,
                trip_summary_json TEXT,
                FOREIGN KEY (user_id) REFERENCES user_preferences (user_id)
            );
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_past_trips_user_id ON past_trips (user_id);
        """)


def infer_hotel_tier(hotel_results: list[dict]) -> Optional[str]:
    """
    Infers user hotel tier preference ("budget", "mid", "luxury") based on
    star ratings and price per night of confirmed/selected hotels.
    
    Heuristic Rules:
    - If average rating >= 4.5 or avg price/night > 250 USD/equiv -> "luxury"
    - If average rating <= 2.5 or avg price/night < 80 USD/equiv -> "budget"
    - Otherwise -> "mid"
    """
    if not hotel_results:
        return None

    ratings = []
    prices = []
    for h in hotel_results:
        if isinstance(h, dict):
            r = h.get("rating")
            if r is not None:
                try:
                    ratings.append(float(r))
                except (ValueError, TypeError):
                    pass
            p = h.get("price_per_night", {})
            if isinstance(p, dict) and p.get("amount") is not None:
                try:
                    prices.append(float(p["amount"]))
                except (ValueError, TypeError):
                    pass

    avg_rating = sum(ratings) / len(ratings) if ratings else None
    avg_price = sum(prices) / len(prices) if prices else None

    if (avg_rating is not None and avg_rating >= 4.5) or (avg_price is not None and avg_price >= 250.0):
        return "luxury"
    if (avg_rating is not None and avg_rating <= 2.5) or (avg_price is not None and avg_price < 80.0):
        return "budget"
    return "mid"


def sanitize_trip_summary(plan_data: dict) -> dict:
    """
    Creates a clean, bounded trip summary for storage, stripping out bulky raw vendor payloads.
    """
    if not isinstance(plan_data, dict):
        return {}

    sanitized = {
        "origin": plan_data.get("origin"),
        "origin_name": plan_data.get("origin_name"),
        "destination": plan_data.get("destination"),
        "destination_name": plan_data.get("destination_name"),
        "departure_date": plan_data.get("departure_date"),
        "check_in": plan_data.get("check_in"),
        "check_out": plan_data.get("check_out"),
        "budget_cap": plan_data.get("budget_cap"),
        "target_currency": plan_data.get("target_currency"),
        "display_currency": plan_data.get("display_currency"),
        "confirmed_at": plan_data.get("confirmed_at"),
    }

    # Clean transport options
    raw_t = plan_data.get("transport_results", [])
    if isinstance(raw_t, list):
        sanitized["transport_options"] = [
            {
                "carrier": t.get("carrier"),
                "origin": t.get("origin"),
                "destination": t.get("destination"),
                "price": t.get("price"),
                "departure_at": t.get("departure_at"),
                "duration_minutes": t.get("duration_minutes")
            }
            for t in raw_t[:3] if isinstance(t, dict)
        ]

    # Clean hotel options
    raw_h = plan_data.get("hotel_results", [])
    if isinstance(raw_h, list):
        sanitized["hotel_options"] = [
            {
                "name": h.get("name"),
                "rating": h.get("rating"),
                "price_per_night": h.get("price_per_night"),
                "total_price": h.get("total_price")
            }
            for h in raw_h[:3] if isinstance(h, dict)
        ]

    # Clean guide highlights
    raw_g = plan_data.get("local_guide_results", [])
    if isinstance(raw_g, list):
        sanitized["guide_highlights"] = [
            {
                "name": g.get("name"),
                "tags": g.get("tags", []),
                "estimated_cost": g.get("estimated_cost")
            }
            for g in raw_g[:5] if isinstance(g, dict)
        ]

    return sanitized


def compute_frequent_origin(conn: sqlite3.Connection, user_id: str) -> Optional[str]:
    """
    Computes the statistical mode (most frequent) origin city/IATA from confirmed past trips.
    Ties are broken by the most recent confirmation.
    """
    cursor = conn.cursor()
    cursor.execute("""
        SELECT COALESCE(origin_name, origin) AS origin_val, COUNT(*) as cnt, MAX(confirmed_at) as latest
        FROM past_trips
        WHERE user_id = ? AND (origin_name IS NOT NULL OR origin IS NOT NULL)
        GROUP BY origin_val
        ORDER BY cnt DESC, latest DESC
        LIMIT 1;
    """, (user_id,))
    row = cursor.fetchone()
    if row and row["origin_val"]:
        return str(row["origin_val"])
    return None


def get_user_memory(user_id: str, db_path: Optional[str] = None) -> Optional[UserMemory]:
    """
    Retrieves user preferences and confirmed past trips for a given user_id.
    Returns None if user has never interacted before.
    """
    if not user_id:
        return None

    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM user_preferences WHERE user_id = ?", (user_id,))
        pref_row = cursor.fetchone()
        
        cursor.execute(
            "SELECT * FROM past_trips WHERE user_id = ? ORDER BY confirmed_at DESC", 
            (user_id,)
        )
        trip_rows = cursor.fetchall()

        if not pref_row and not trip_rows:
            return None

        pref = UserPreference(
            user_id=user_id,
            currency=pref_row["currency"] if pref_row else None,
            hotel_tier=pref_row["hotel_tier"] if pref_row else None,
            frequent_origin=pref_row["frequent_origin"] if pref_row else None,
            updated_at=pref_row["updated_at"] if pref_row else None,
        )

        past_trips = []
        for r in trip_rows:
            summary = {}
            if r["trip_summary_json"]:
                try:
                    summary = json.loads(r["trip_summary_json"])
                except Exception:
                    pass
            past_trips.append(PastTrip(
                id=r["id"],
                user_id=r["user_id"],
                destination=r["destination"],
                destination_name=r["destination_name"],
                origin=r["origin"],
                origin_name=r["origin_name"],
                departure_date=r["departure_date"],
                check_in=r["check_in"],
                check_out=r["check_out"],
                currency=r["currency"],
                budget_cap=r["budget_cap"],
                confirmed_at=r["confirmed_at"],
                trip_summary=summary
            ))

        return UserMemory(user_id=user_id, preference=pref, past_trips=past_trips)
    finally:
        conn.close()


def update_user_memory(
    user_id: str,
    currency: Optional[str] = None,
    hotel_tier: Optional[str] = None,
    frequent_origin: Optional[str] = None,
    db_path: Optional[str] = None
) -> UserMemory:
    """
    Updates or inserts user preferences in the SQLite store.
    """
    if not user_id:
        raise ValueError("user_id must not be empty.")

    now_iso = datetime.now(timezone.utc).isoformat()
    conn = get_db_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM user_preferences WHERE user_id = ?", (user_id,))
            existing = cursor.fetchone()

            if existing:
                new_currency = currency if currency is not None else existing["currency"]
                new_tier = hotel_tier if hotel_tier is not None else existing["hotel_tier"]
                new_origin = frequent_origin if frequent_origin is not None else existing["frequent_origin"]

                cursor.execute("""
                    UPDATE user_preferences
                    SET currency = ?, hotel_tier = ?, frequent_origin = ?, updated_at = ?
                    WHERE user_id = ?
                """, (new_currency, new_tier, new_origin, now_iso, user_id))
            else:
                cursor.execute("""
                    INSERT INTO user_preferences (user_id, currency, hotel_tier, frequent_origin, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (user_id, currency, hotel_tier, frequent_origin, now_iso))

        # Re-fetch the full updated memory
        mem = get_user_memory(user_id, db_path=db_path)
        return mem or UserMemory(
            user_id=user_id,
            preference=UserPreference(user_id=user_id, currency=currency, hotel_tier=hotel_tier, frequent_origin=frequent_origin, updated_at=now_iso)
        )
    finally:
        conn.close()


def record_confirmed_trip(
    user_id: str,
    trip_summary: dict,
    db_path: Optional[str] = None
) -> None:
    """
    Records a confirmed trip into the past_trips table and recalculates frequent origin.
    """
    if not user_id:
        raise ValueError("user_id must not be empty.")

    now_iso = datetime.now(timezone.utc).isoformat()
    sanitized = sanitize_trip_summary(trip_summary)
    summary_json = json.dumps(sanitized)

    destination = str(trip_summary.get("destination") or "UNK")
    destination_name = trip_summary.get("destination_name")
    origin = trip_summary.get("origin")
    origin_name = trip_summary.get("origin_name")
    departure_date = trip_summary.get("departure_date")
    check_in = trip_summary.get("check_in")
    check_out = trip_summary.get("check_out")
    currency = trip_summary.get("display_currency") or trip_summary.get("target_currency") or "USD"
    budget_cap = trip_summary.get("budget_cap")
    if budget_cap is not None:
        try:
            budget_cap = float(budget_cap)
        except (ValueError, TypeError):
            budget_cap = None

    conn = get_db_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO past_trips (
                    user_id, destination, destination_name, origin, origin_name,
                    departure_date, check_in, check_out, currency, budget_cap,
                    confirmed_at, trip_summary_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                user_id, destination, destination_name, origin, origin_name,
                departure_date, check_in, check_out, currency, budget_cap,
                now_iso, summary_json
            ))

            # Recalculate frequent origin using mode of past trips
            frequent_orig = compute_frequent_origin(conn, user_id)

            # Check if user preference already exists
            cursor.execute("SELECT * FROM user_preferences WHERE user_id = ?", (user_id,))
            existing = cursor.fetchone()

            inferred_tier = infer_hotel_tier(trip_summary.get("hotel_results", []))

            if existing:
                new_curr = currency or existing["currency"]
                new_tier = inferred_tier or existing["hotel_tier"]
                cursor.execute("""
                    UPDATE user_preferences
                    SET currency = ?, hotel_tier = ?, frequent_origin = ?, updated_at = ?
                    WHERE user_id = ?
                """, (new_curr, new_tier, frequent_orig, now_iso, user_id))
            else:
                cursor.execute("""
                    INSERT INTO user_preferences (user_id, currency, hotel_tier, frequent_origin, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (user_id, currency, inferred_tier, frequent_orig, now_iso))

        logger.info(f"Recorded confirmed trip for user '{user_id}' to '{destination}' (frequent origin: {frequent_orig})")
    finally:
        conn.close()


class MemoryStore:
    """
    Facade wrapper conforming to the original planned class interface.
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path

    def get_memory(self, user_id: str) -> Optional[UserMemory]:
        return get_user_memory(user_id, db_path=self.db_path)

    def update_preference(
        self,
        user_id: str,
        currency: Optional[str] = None,
        hotel_tier: Optional[str] = None,
        frequent_origin: Optional[str] = None
    ) -> UserMemory:
        return update_user_memory(
            user_id,
            currency=currency,
            hotel_tier=hotel_tier,
            frequent_origin=frequent_origin,
            db_path=self.db_path
        )

    def record_trip(self, user_id: str, trip_summary: dict) -> None:
        record_confirmed_trip(user_id, trip_summary, db_path=self.db_path)
