import os
import logging
import httpx
from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv())
from typing import Optional
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from shared.schemas import TripPlanPayload

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="TripMesh Gateway", version="0.0.1")

# Enable CORS for frontend local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ChatRequest(BaseModel):
    message: str
    session_id: str
    user_id: str
    structured_payload: Optional[dict] = None

class ChatResponse(BaseModel):
    response: str
    session_id: str
    plan: Optional[TripPlanPayload] = None

# In docker-compose, this resolved to http://planner_agent:8001
PLANNER_AGENT_URL = os.getenv("PLANNER_AGENT_URL", "http://localhost:8001")


@app.get("/")
async def root():
    return {"service": "gateway", "status": "running"}


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    logger.info(f"Gateway received message: {request.message} (session: {request.session_id}, user: {request.user_id})")
    
    # Forward the request to the Planner Agent
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            planner_endpoint = f"{PLANNER_AGENT_URL}/chat"
            logger.info(f"Forwarding to planner agent at: {planner_endpoint}")
            payload = {
                "message": request.message,
                "session_id": request.session_id,
                "user_id": request.user_id
            }
            if request.structured_payload is not None:
                payload["structured_payload"] = request.structured_payload
            response = await client.post(
                planner_endpoint,
                json=payload
            )
            response.raise_for_status()
            data = response.json()
            return ChatResponse(
                response=data.get("response", "No response from planner."),
                session_id=request.session_id,
                plan=data.get("plan")
            )
        except httpx.HTTPStatusError as exc:
            logger.exception(f"Planner Agent HTTP {exc.response.status_code} error: {exc.response.text}")
            return ChatResponse(
                response=f"I encountered a temporary service issue: {exc.response.text or exc}. Please try again.",
                session_id=request.session_id,
                plan=None
            )
        except Exception as exc:
            logger.exception(f"Exception communicating with Planner Agent: {exc}")
            return ChatResponse(
                response=f"I couldn't process your request right now: {exc}. Please verify services and try again.",
                session_id=request.session_id,
                plan=None
            )



@app.get("/api/trip/{session_id}/export.pdf")
async def export_pdf(session_id: str):
    logger.info(f"Gateway proxying export.pdf for session '{session_id}'")
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            planner_endpoint = f"{PLANNER_AGENT_URL}/api/trip/{session_id}/export.pdf"
            resp = await client.get(planner_endpoint)
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            return Response(
                content=resp.content,
                media_type="application/pdf",
                headers={"Content-Disposition": f"attachment; filename=itinerary_{session_id}.pdf"}
            )
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"Error proxying PDF export: {exc}")
            raise HTTPException(status_code=502, detail=f"Failed to export PDF: {exc}")


@app.get("/api/trip/{session_id}/export.ics")
async def export_ics(session_id: str):
    logger.info(f"Gateway proxying export.ics for session '{session_id}'")
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            planner_endpoint = f"{PLANNER_AGENT_URL}/api/trip/{session_id}/export.ics"
            resp = await client.get(planner_endpoint)
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            return Response(
                content=resp.content,
                media_type="text/calendar",
                headers={"Content-Disposition": f"attachment; filename=trip_{session_id}.ics"}
            )
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"Error proxying ICS export: {exc}")
            raise HTTPException(status_code=502, detail=f"Failed to export calendar: {exc}")


@app.get("/api/trip/{session_id}/traces")
async def get_traces(session_id: str):
    logger.info(f"Gateway proxying traces for session '{session_id}'")
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            planner_endpoint = f"{PLANNER_AGENT_URL}/api/trip/{session_id}/traces"
            resp = await client.get(planner_endpoint)
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            return resp.json()
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"Error proxying traces: {exc}")
            raise HTTPException(status_code=502, detail=f"Failed to fetch traces: {exc}")
