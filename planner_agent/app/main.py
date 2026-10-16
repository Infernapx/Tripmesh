import os
import logging
from typing import Optional
from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv())
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel
from langchain_core.messages import HumanMessage
try:
    from .graph import planner_graph
    from .session_store import get_confirmed_trip, get_session_trip, get_session_snapshot, restore_session_snapshot, is_invalid_city
    from .export_service import generate_itinerary_pdf, generate_itinerary_ics
except ImportError:
    from app.graph import planner_graph
    from app.session_store import get_confirmed_trip, get_session_trip, get_session_snapshot, restore_session_snapshot, is_invalid_city
    from app.export_service import generate_itinerary_pdf, generate_itinerary_ics

from shared.observability import start_session_turn, get_session_traces
from shared.schemas import TripPlanPayload

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

app = FastAPI(title="TripMesh Planner Agent", version="0.0.1")

class ChatRequest(BaseModel):
    message: str
    session_id: str
    user_id: str
    structured_payload: Optional[dict] = None

class ChatResponse(BaseModel):
    response: str
    session_id: str
    plan: Optional[TripPlanPayload] = None


@app.get("/")
async def root():
    return {"service": "planner_agent", "status": "running"}


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    logger.info(f"Planner Agent chat request: '{request.message}' (session: {request.session_id}, user: {request.user_id})")
    start_session_turn(request.session_id)
    
    structured_payload = request.structured_payload
    if not structured_payload and request.message.strip().startswith("{"):
        try:
            import json
            parsed = json.loads(request.message)
            if isinstance(parsed, dict) and parsed.get("type"):
                structured_payload = parsed
        except Exception:
            pass

    # Initialize LangGraph state input
    state_input = {
        "messages": [HumanMessage(content=request.message)],
        "session_id": request.session_id,
        "user_id": request.user_id,
        "structured_payload": structured_payload,
        "budget_requested": None,
        "used_memory_currency": False,
        "used_memory_origin": False,
        "is_conversion": False,
        "is_planning": False,
        "is_guide_query": False,
        "is_weather_query": False,
        "is_road_conditions_query": False,
        "is_destination_news_query": False,
        "is_show_itinerary": False,
        "is_conversational": False,
        "needs_destination": False,
        "needs_origin": False,
        "needs_currency_clarification": False,
        "is_confirmation": False,
        "is_ambiguous_confirmation": False,
        "activity_query": None,
        "origin": None,
        "origin_name": None,
        "destination": None,
        "destination_name": None,
        "departure_date": None,
        "check_in": None,
        "check_out": None,
        "budget_agent_response": "",
        "transport_agent_response": "",
        "hotel_agent_response": "",
        "local_guide_agent_response": "",
        "budget_cap": None,
        "target_currency": "USD",
        "display_currency": "USD",
        "negotiation_round": 0,
        "max_negotiation_rounds": 2,
        "budget_reconciliation": None,
        "cost_line_items": [],
        "transport_price_ceiling": None,
        "hotel_price_ceiling": None,
        "is_budget_change": False,
        "is_flight_delay": False,
        "is_replan": False,
        "delay_context": None,
        "invoked_nodes": [],
        "cached_transport_results": [],
        "cached_hotel_results": [],
        "is_invalid_budget": False,
        "invalid_budget_message": None,
        "is_cancellation": False,
        "target_agent": None,
        "delay_hours": None,
        "min_departure_after": None,
        "excluded_hotels": [],
        "prefer_cheaper": False,
        "prefer_different": False,
        "prior_transport_results": [],
        "prior_hotel_results": [],
        "no_later_flight_found": False,
        "no_cheaper_hotel_found": False,
        "no_cheaper_transport_found": False
    }

    
    session_snapshot = get_session_snapshot(request.session_id)
    try:
        # Invoke the LangGraph orchestrator graph
        result = await planner_graph.ainvoke(state_input)
        
        if result.get("is_conversion", False):
            response_text = result.get("budget_agent_response", "Failed to retrieve currency conversion response.")
        else:
            response_text = result["messages"][-1].content
            
        if "Planner LangGraph Execution Error" in response_text or result.get("error"):
            restore_session_snapshot(request.session_id, session_snapshot)

        plan = result.get("plan")
        if not plan:
            prior = get_session_trip(request.session_id)
            raw = prior.get("confirmed_plan") or prior
            if raw and (raw.get("transport_results") or raw.get("hotel_results") or (raw.get("origin") and raw.get("destination"))):
                try:
                    plan = TripPlanPayload(**{k: v for k, v in raw.items() if k in TripPlanPayload.model_fields}).model_dump()
                except Exception:
                    pass

        if plan:
            orig = plan.get("origin")
            dest = plan.get("destination")
            if is_invalid_city(orig) or is_invalid_city(dest):
                logger.error(f"Render path guard: plan generated with invalid cities (orig={orig!r}, dest={dest!r}); rolling back session snapshot")
                restore_session_snapshot(request.session_id, session_snapshot)
                plan = None
                response_text = "I couldn't plan that route. Please specify valid origin and destination cities."
            else:
                # Ensure weather_tip is present if destination is known (3s timeout)
                if not plan.get("weather_tip") and (plan.get("destination_name") or plan.get("destination")):
                    try:
                        import asyncio
                        from shared.weather import get_weather_forecast
                        target_d = plan.get("destination_name") or plan.get("destination")
                        w_tip_res = await asyncio.wait_for(
                            get_weather_forecast(target_d, start_date=plan.get("departure_date"), end_date=plan.get("check_out")),
                            timeout=3.0
                        )
                        plan["weather_tip"] = w_tip_res.get("weather_tip")
                    except Exception as wex:
                        logger.debug(f"Plan weather tip lookup skipped or timed out: {wex}")
                        plan["weather_tip"] = None

                # Enforce budget_requested invariant
                sess = get_session_trip(request.session_id)
                budget_req = sess.get("budget_requested", False)
                if not budget_req:
                    plan["budget"] = None
                    plan["budget_cap"] = None
                    plan["budget_status"] = None
                    plan["budget_reconciliation"] = None
                else:
                    if plan.get("budget") is None:
                        plan["budget"] = plan.get("budget_cap") or sess.get("budget_cap")
                    if not plan.get("budget_status"):
                        recon = plan.get("budget_reconciliation")
                        is_over = recon.get("over_budget") if recon else False
                        plan["budget_status"] = "over_budget" if is_over else "within_budget"

        return ChatResponse(
            response=response_text,
            session_id=request.session_id,
            plan=plan
        )
        
    except Exception as e:
        logger.exception(f"LangGraph execution failed: {e}")
        restore_session_snapshot(request.session_id, session_snapshot)
        return ChatResponse(
            response=f"Planner LangGraph Execution Error: {e}",
            session_id=request.session_id
        )



@app.get("/api/trip/{session_id}/export.pdf")
async def export_pdf(session_id: str):
    logger.info(f"Export PDF requested for session '{session_id}'")
    confirmed_plan = get_confirmed_trip(session_id)
    if not confirmed_plan:
        raise HTTPException(
            status_code=400,
            detail=f"No confirmed trip plan found for session '{session_id}'. Please finalize and confirm your trip before exporting."
        )
    
    try:
        pdf_bytes = generate_itinerary_pdf(confirmed_plan)
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename=itinerary_{session_id}.pdf"}
        )
    except Exception as e:
        logger.error(f"Failed to generate PDF for session '{session_id}': {e}")
        raise HTTPException(status_code=500, detail=f"PDF generation failed: {e}")


@app.get("/api/trip/{session_id}/export.ics")
async def export_ics(session_id: str):
    logger.info(f"Export ICS requested for session '{session_id}'")
    confirmed_plan = get_confirmed_trip(session_id)
    if not confirmed_plan:
        raise HTTPException(
            status_code=400,
            detail=f"No confirmed trip plan found for session '{session_id}'. Please finalize and confirm your trip before exporting."
        )
    
    try:
        ics_bytes = generate_itinerary_ics(confirmed_plan)
        return Response(
            content=ics_bytes,
            media_type="text/calendar",
            headers={"Content-Disposition": f"attachment; filename=trip_{session_id}.ics"}
        )
    except Exception as e:
        logger.error(f"Failed to generate ICS for session '{session_id}': {e}")
        raise HTTPException(status_code=500, detail=f"Calendar export failed: {e}")


@app.get("/api/trip/{session_id}/traces")
async def get_traces(session_id: str):
    logger.info(f"Retrieving traces for session '{session_id}'")
    traces = get_session_traces(session_id)
    return {
        "session_id": session_id,
        "traces": traces
    }
