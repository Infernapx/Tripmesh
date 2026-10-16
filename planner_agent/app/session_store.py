"""
planner_agent/app/session_store.py
---------------------------------
In-memory session trip store keyed by session_id.
Tracks established trip slots (origin, destination, dates, budget), conversational
pending slots (e.g. pending_slot="destination" or pending_slot="origin"),
and Human-in-the-Loop confirmation state (awaiting_confirmation, is_confirmed, confirmed_at, confirmed_plan).
"""

from datetime import datetime, timezone
from typing import Optional
import copy
import logging

logger = logging.getLogger(__name__)

_SESSION_TRIP_STORE: dict[str, dict] = {}

INVALID_CITY_VALUES = {"", "NONE", "NULL", "UNK", "UNKNOWN", "UNKNOWN CITY"}


def is_invalid_city(val: Optional[str]) -> bool:
    if val is None:
        return True
    if not isinstance(val, str):
        return False
    return val.strip().upper() in INVALID_CITY_VALUES


def get_session_trip(session_id: str) -> dict:
    """Retrieves existing trip context for a session_id."""
    return _SESSION_TRIP_STORE.get(session_id, {}).copy()


def reset_session_trip(session_id: str, new_trip: Optional[dict] = None, **kwargs) -> None:
    """Completely resets session trip state, wiping all prior trip artifacts."""
    if not session_id:
        return
    prior = _SESSION_TRIP_STORE.get(session_id, {})
    trip = (new_trip or {}).copy()
    if kwargs:
        trip.update(kwargs)

    # Central guard: reject any write where origin/destination/IATA is None, empty, "Unknown", "Unknown City", "UNK".
    for key, name_key in [("origin", "origin_name"), ("destination", "destination_name")]:
        val = trip.get(key)
        if is_invalid_city(val):
            logger.warning(f"SessionStore Guard [reset_session_trip]: rejected invalid {key}={val!r}; keeping previous valid value {prior.get(key)!r}")
            trip.pop(key, None)
            if prior.get(key):
                trip[key] = prior[key]

        nval = trip.get(name_key)
        if is_invalid_city(nval):
            logger.warning(f"SessionStore Guard [reset_session_trip]: rejected invalid {name_key}={nval!r}; keeping previous valid value {prior.get(name_key)!r}")
            trip.pop(name_key, None)
            if prior.get(name_key):
                trip[name_key] = prior[name_key]

    _SESSION_TRIP_STORE[session_id] = trip


def save_session_trip(session_id: str, updates: dict) -> None:
    """Merges updates into the session's active trip context with strict central validation."""
    if not session_id:
        return
    current = _SESSION_TRIP_STORE.get(session_id, {})
    for key, val in updates.items():
        if key in ["origin", "destination"]:
            if is_invalid_city(val):
                logger.warning(f"SessionStore Guard [save_session_trip]: rejected invalid {key}={val!r}; preserving prior {current.get(key)!r}")
                continue
            current[key] = val
        elif key in ["origin_name", "destination_name"]:
            if is_invalid_city(val):
                logger.warning(f"SessionStore Guard [save_session_trip]: rejected invalid {key}={val!r}; preserving prior {current.get(key)!r}")
                continue
            current[key] = val
        elif key in ["confirmed_plan", "plan"] and isinstance(val, dict):
            cp_orig = val.get("origin")
            cp_dest = val.get("destination")
            if is_invalid_city(cp_orig) or is_invalid_city(cp_dest):
                logger.warning(f"SessionStore Guard [save_session_trip]: rejected invalid plan payload with orig={cp_orig!r}, dest={cp_dest!r}; preserving prior plan")
                continue
            current[key] = val
        elif key == "budget_requested":
            current[key] = bool(val)
        elif key == "budget_cap" and val is None:
            current.pop("budget_cap", None)
        elif val is not None and val != "":
            current[key] = val
        elif key in ["pending_slot", "awaiting_confirmation"] and val is None:
            current.pop(key, None)
    _SESSION_TRIP_STORE[session_id] = current



def set_pending_slot(session_id: str, slot: Optional[str]) -> None:
    """Sets or clears pending_slot for a session."""
    if not session_id:
        return
    current = _SESSION_TRIP_STORE.get(session_id, {})
    if slot:
        current["pending_slot"] = slot
    else:
        current.pop("pending_slot", None)
    _SESSION_TRIP_STORE[session_id] = current


def set_awaiting_confirmation(session_id: str, awaiting: bool) -> None:
    """Sets whether the session is awaiting user confirmation."""
    if not session_id:
        return
    current = _SESSION_TRIP_STORE.get(session_id, {})
    current["awaiting_confirmation"] = bool(awaiting)
    _SESSION_TRIP_STORE[session_id] = current


def is_awaiting_confirmation(session_id: str) -> bool:
    """Returns True if the session is currently awaiting confirmation."""
    if not session_id:
        return False
    return bool(_SESSION_TRIP_STORE.get(session_id, {}).get("awaiting_confirmation", False))


def confirm_session_trip(session_id: str, plan_data: Optional[dict] = None) -> dict:
    """
    Marks the session trip as confirmed with UTC timestamp,
    clears awaiting_confirmation, and attaches confirmed_plan.
    """
    if not session_id:
        return {}
    current = _SESSION_TRIP_STORE.get(session_id, {})
    current["awaiting_confirmation"] = False
    current["is_confirmed"] = True
    current["confirmed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    if plan_data:
        current["confirmed_plan"] = plan_data
    _SESSION_TRIP_STORE[session_id] = current
    return current.copy()


def get_confirmed_trip(session_id: str) -> Optional[dict]:
    """Retrieves confirmed plan data if the trip is confirmed, else None."""
    session = _SESSION_TRIP_STORE.get(session_id, {})
    if session.get("is_confirmed"):
        return session.get("confirmed_plan") or session
    return None


def invalidate_confirmation(session_id: str) -> None:
    """
    Invalidates confirmation state when a plan is modified mid-trip.
    Reverts is_confirmed to False, preserves previous confirmation history,
    and sets awaiting_confirmation to True.
    """
    if not session_id:
        return
    current = _SESSION_TRIP_STORE.get(session_id, {})
    if current.get("is_confirmed"):
        current["previous_confirmed_at"] = current.get("confirmed_at")
        current["previous_confirmed_plan"] = current.get("confirmed_plan")
    current["is_confirmed"] = False
    current["awaiting_confirmation"] = True
    _SESSION_TRIP_STORE[session_id] = current


import copy


def clear_session_trip(session_id: str) -> None:
    """Clears all trip context for a session."""
    _SESSION_TRIP_STORE.pop(session_id, None)


def get_session_snapshot(session_id: str) -> dict:
    """Returns a deep snapshot of the current session state."""
    return copy.deepcopy(_SESSION_TRIP_STORE.get(session_id, {}))


def restore_session_snapshot(session_id: str, snapshot: dict) -> None:
    """Restores session state to a previously captured snapshot."""
    if not session_id:
        return
    if snapshot:
        _SESSION_TRIP_STORE[session_id] = copy.deepcopy(snapshot)
    # If snapshot is empty, do not destroy active session data unless explicitly cleared via clear_session_trip


if __name__ == "__main__":
    print("==================================================================")
    print("         SESSION TRIP STORE SELF-VALIDATION CHECK                ")
    print("==================================================================")

    test_sid = "test-store-self-check"
    clear_session_trip(test_sid)
    assert get_session_trip(test_sid) == {}, "Clear failed"
    print("[1] clear_session_trip: PASS")

    # Save
    save_session_trip(test_sid, {
        "origin": "LHR",
        "destination": "LIS",
        "budget_cap": 500.0,
        "display_currency": "USD",
        "pending_slot": "dates"
    })
    snap1 = get_session_trip(test_sid)
    assert snap1["origin"] == "LHR" and snap1["destination"] == "LIS", "Save failed"
    print("[2] save_session_trip & get_session_trip: PASS")

    # Pending slot
    set_pending_slot(test_sid, "budget")
    assert get_session_trip(test_sid)["pending_slot"] == "budget", "Set pending failed"
    set_pending_slot(test_sid, None)
    assert "pending_slot" not in get_session_trip(test_sid), "Clear pending failed"
    print("[3] set_pending_slot: PASS")

    # Awaiting confirmation & Confirm
    set_awaiting_confirmation(test_sid, True)
    assert is_awaiting_confirmation(test_sid) is True, "Awaiting confirmation failed"
    conf = confirm_session_trip(test_sid, {"plan_name": "Lisbon Adventure"})
    assert conf["is_confirmed"] is True and is_awaiting_confirmation(test_sid) is False, "Confirm failed"
    assert get_confirmed_trip(test_sid)["plan_name"] == "Lisbon Adventure", "Get confirmed trip failed"
    print("[4] confirm_session_trip & get_confirmed_trip: PASS")

    # Invalidate
    invalidate_confirmation(test_sid)
    assert get_confirmed_trip(test_sid) is None, "Invalidation failed"
    assert is_awaiting_confirmation(test_sid) is True, "Invalidation awaiting failed"
    print("[5] invalidate_confirmation: PASS")

    # Reset
    reset_session_trip(test_sid, {"origin": "BOM", "destination": "DEL"})
    snap2 = get_session_trip(test_sid)
    assert snap2.get("origin") == "BOM" and snap2.get("destination") == "DEL" and "confirmed_plan" not in snap2, "Reset failed"
    print("[6] reset_session_trip (wipe prior artifacts): PASS")

    # Snapshot & Restore
    saved_snapshot = get_session_snapshot(test_sid)
    save_session_trip(test_sid, {"origin": "JFK", "destination": "NRT"})
    restore_session_snapshot(test_sid, saved_snapshot)
    assert get_session_trip(test_sid)["origin"] == "BOM", "Restore snapshot failed"
    print("[7] snapshot & restore: PASS")

    clear_session_trip(test_sid)
    print("==================================================================")
    print("         ALL SESSION STORE CHECKS PASSED SUCCESSFULLY!            ")
    print("==================================================================")



