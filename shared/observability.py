"""
shared/observability.py
-----------------------
Zero-config structured observability and tracing for TripMesh.
Records span timing, agent name, skill name, status, and plain-language decision reasons.
Falls back cleanly to an in-memory structured trace store when Langfuse credentials
aren't set (zero-config offline mode), while supporting Langfuse when configured.
Provides the single source of truth for execution tracking (unifying with invoked_nodes).
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Generator, Optional, List, Dict

logger = logging.getLogger(__name__)


@dataclass
class TraceEvent:
    id: str
    session_id: str
    agent: str
    skill: str
    status: str  # "running", "success", "error", "no_change"
    decision_reason: Optional[str] = None
    start_time: str = ""
    end_time: Optional[str] = None
    duration_ms: Optional[float] = None
    turn_index: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class InMemoryTraceStore:
    """Thread-safe in-memory store for session trace events."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._traces: dict[str, list[TraceEvent]] = {}
        self._current_turn: dict[str, int] = {}

    def start_turn(self, session_id: str) -> int:
        with self._lock:
            turn = self._current_turn.get(session_id, 0) + 1
            self._current_turn[session_id] = turn
            return turn

    def get_current_turn(self, session_id: str) -> int:
        with self._lock:
            return self._current_turn.get(session_id, 1)

    def record_start(
        self,
        session_id: str,
        agent: str,
        skill: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> TraceEvent:
        with self._lock:
            turn = self._current_turn.get(session_id, 1)
            event = TraceEvent(
                id=str(uuid.uuid4()),
                session_id=session_id,
                agent=agent,
                skill=skill,
                status="running",
                decision_reason=None,
                start_time=datetime.now(timezone.utc).isoformat(),
                turn_index=turn,
                metadata=dict(metadata or {}),
            )
            if session_id not in self._traces:
                self._traces[session_id] = []
            self._traces[session_id].append(event)
            return event

    def record_end(
        self,
        event_id: str,
        session_id: str,
        status: str,
        decision_reason: Optional[str] = None,
        duration_ms: Optional[float] = None,
        metadata_update: Optional[dict[str, Any]] = None,
    ) -> Optional[TraceEvent]:
        with self._lock:
            session_list = self._traces.get(session_id, [])
            for event in session_list:
                if event.id == event_id:
                    event.status = status
                    if decision_reason is not None:
                        event.decision_reason = decision_reason
                    event.end_time = datetime.now(timezone.utc).isoformat()
                    if duration_ms is not None:
                        event.duration_ms = duration_ms
                    if metadata_update:
                        event.metadata.update(metadata_update)
                    return event
        return None

    def get_session_traces(self, session_id: str) -> list[TraceEvent]:
        with self._lock:
            return list(self._traces.get(session_id, []))

    def get_current_turn_traces(self, session_id: str) -> list[TraceEvent]:
        with self._lock:
            turn = self._current_turn.get(session_id, 1)
            events = self._traces.get(session_id, [])
            return [e for e in events if e.turn_index == turn]

    def get_current_turn_skills(self, session_id: str) -> list[str]:
        """Derives the ordered list of invoked skills/nodes for current turn."""
        with self._lock:
            turn = self._current_turn.get(session_id, 1)
            events = self._traces.get(session_id, [])
            return [e.skill for e in events if e.turn_index == turn]

    def get_session_skills(self, session_id: str) -> list[str]:
        """Derives all invoked skills/nodes for the whole session."""
        with self._lock:
            events = self._traces.get(session_id, [])
            return [e.skill for e in events]

    def clear_session(self, session_id: str) -> None:
        with self._lock:
            self._traces.pop(session_id, None)
            self._current_turn.pop(session_id, None)

    def clear_all(self) -> None:
        with self._lock:
            self._traces.clear()
            self._current_turn.clear()


# Global singleton trace store
_GLOBAL_TRACE_STORE = InMemoryTraceStore()


def get_global_trace_store() -> InMemoryTraceStore:
    return _GLOBAL_TRACE_STORE


def start_session_turn(session_id: str) -> int:
    return _GLOBAL_TRACE_STORE.start_turn(session_id)


def get_session_traces(session_id: str) -> list[dict[str, Any]]:
    """Return all traces for a session as JSON-serializable dictionaries."""
    return [e.to_dict() for e in _GLOBAL_TRACE_STORE.get_session_traces(session_id)]


def get_session_invoked_nodes(session_id: str) -> list[str]:
    """Single source of truth derivation for invoked_nodes from trace store (current turn)."""
    return _GLOBAL_TRACE_STORE.get_current_turn_skills(session_id)


def clear_session_traces(session_id: str) -> None:
    _GLOBAL_TRACE_STORE.clear_session(session_id)


class TraceSpan:
    """Active span yielded by trace_agent_call context manager."""

    def __init__(
        self,
        event: TraceEvent,
        store: InMemoryTraceStore,
        langfuse_span: Any = None,
    ) -> None:
        self._event = event
        self._store = store
        self._langfuse_span = langfuse_span
        self.decision_reason: Optional[str] = None
        self.status: str = "success"
        self._metadata_updates: dict[str, Any] = {}

    @property
    def event_id(self) -> str:
        return self._event.id

    @property
    def skill(self) -> str:
        return self._event.skill

    @property
    def agent(self) -> str:
        return self._event.agent

    def set_decision(self, reason: str) -> None:
        self.decision_reason = reason

    def set_status(self, status: str) -> None:
        self.status = status

    def update(self, **kwargs: Any) -> None:
        self._metadata_updates.update(kwargs)
        if "decision_reason" in kwargs:
            self.decision_reason = str(kwargs["decision_reason"])
        if "status" in kwargs:
            self.status = str(kwargs["status"])

    def end(self) -> None:
        pass


@contextmanager
def trace_agent_call(
    agent: str,
    skill: str,
    session_id: str,
    decision_reason: Optional[str] = None,
    **metadata: Any,
) -> Generator[TraceSpan, None, None]:
    """
    Context manager to trace a single agent call or graph node execution.

    Operates in zero-config offline mode using an in-memory structured trace store.
    Automatically connects to Langfuse when LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY
    are present in the environment.

    Usage:
        with trace_agent_call("hotel_agent", "call_hotel_agent", session_id) as span:
            hotels = await fetch_hotels(...)
            span.set_decision(f"Found {len(hotels)} hotels in {city}")
    """
    start_monotonic = time.perf_counter()
    event = _GLOBAL_TRACE_STORE.record_start(
        session_id=session_id,
        agent=agent,
        skill=skill,
        metadata=metadata,
    )
    if decision_reason:
        event.decision_reason = decision_reason

    # Optional Langfuse client integration
    langfuse_client = None
    langfuse_span = None
    if os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"):
        try:
            from langfuse import Langfuse
            langfuse_client = Langfuse(
                public_key=os.getenv("LANGFUSE_PUBLIC_KEY"),
                secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
                host=os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"),
            )
            langfuse_span = langfuse_client.span(
                name=f"{agent}:{skill}",
                metadata={"session_id": session_id, **metadata},
            )
        except Exception as lf_err:
            logger.debug("Langfuse span creation bypassed: %s", lf_err)

    span = TraceSpan(event=event, store=_GLOBAL_TRACE_STORE, langfuse_span=langfuse_span)
    if decision_reason:
        span.set_decision(decision_reason)

    try:
        yield span
    except Exception as exc:
        span.set_status("error")
        if not span.decision_reason:
            span.set_decision(f"Error: {type(exc).__name__}: {exc}")
        span.update(error=str(exc))
        raise
    finally:
        duration_ms = round((time.perf_counter() - start_monotonic) * 1000.0, 2)
        _GLOBAL_TRACE_STORE.record_end(
            event_id=event.id,
            session_id=session_id,
            status=span.status,
            decision_reason=span.decision_reason,
            duration_ms=duration_ms,
            metadata_update=span._metadata_updates,
        )

        if langfuse_span is not None:
            try:
                langfuse_span.end(
                    status_message=span.decision_reason or span.status,
                    level="ERROR" if span.status == "error" else "DEFAULT",
                )
            except Exception:
                pass

        logger.debug(
            "[trace] agent=%s skill=%s session=%s status=%s duration=%.2fms reason=%s",
            agent,
            skill,
            session_id,
            span.status,
            duration_ms,
            span.decision_reason,
        )
