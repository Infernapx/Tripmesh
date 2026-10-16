import os
import re
import json
import logging
from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv())
from fastapi import FastAPI
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.server.routes import add_a2a_routes_to_fastapi, create_agent_card_routes, create_jsonrpc_routes
from a2a.types import AgentCard, AgentSkill, AgentInterface, Message, Part

try:
    from .retriever import search_activities
except ImportError:
    from app.retriever import search_activities

from shared.gemini_client import generate_local_guide_highlight

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

app = FastAPI(title="TripMesh Local Guide Agent", version="0.0.1")


class LocalGuideAgentExecutor(AgentExecutor):
    async def execute(self, context, event_queue):
        user_input = context.get_user_input()
        logger.info(f"Local Guide Agent received task: {user_input}")

        destination = "LIS"
        query = ""

        try:
            data = json.loads(user_input)
            destination = data.get("destination", "LIS")
            destination_name = data.get("destination_name") or destination
            query = data.get("query", "")
        except Exception:
            match = re.search(r'in\s+([A-Za-z]+)', user_input, re.IGNORECASE)
            if match:
                destination = match.group(1).upper()
            else:
                destination = "LIS"
            destination_name = destination
            query = user_input

        logger.info(f"Local Guide search parameters: destination={destination}, destination_name={destination_name}, query='{query}'")

        try:
            recommendations = search_activities(destination=destination, query=query)
        except Exception as exc:
            logger.warning(f"Local Guide search fallback due to vector store error: {exc}")
            recommendations = []

        serialized = []
        for rec in recommendations:
            serialized.append({
                "name": rec.name,
                "description": rec.description,
                "location": {
                    "name": rec.location.name,
                    "lat": rec.location.lat,
                    "lon": rec.location.lon
                },
                "estimated_duration_hours": rec.estimated_duration_hours,
                "estimated_cost": {
                    "amount": rec.estimated_cost.amount,
                    "currency": rec.estimated_cost.currency
                } if rec.estimated_cost else None,
                "source_citation": rec.source_citation,
                "data_quality": rec.data_quality.value,
                "tags": rec.tags
            })

        no_match = len(serialized) == 0
        gemini_summary = await generate_local_guide_highlight(destination_name, serialized) if not no_match else None
        
        web_results = []
        web_summary = ""
        if no_match:
            try:
                from shared.tavily_search import search_local_guide_fallback
                fallback_resp = await search_local_guide_fallback(destination_name)
                if fallback_resp.get("results"):
                    web_results = fallback_resp["results"]
                web_summary = fallback_resp.get("summary", "")
            except Exception as exc:
                logger.warning(f"Local Guide web fallback search error: {exc}")
                web_summary = "Live web search could not be completed right now."

        if not no_match:
            if gemini_summary:
                message_text = f"{gemini_summary} (Found {len(serialized)} grounded recommendations.)"
            else:
                message_text = f"Found {len(serialized)} grounded recommendations."
        else:
            seed_line = f"No strong match found in local guide knowledge base for '{destination_name}'."
            if web_summary:
                message_text = f"{seed_line}\n\n{web_summary}"
            else:
                message_text = seed_line

        response_payload = {
            "success": True,
            "results": serialized,
            "web_results": web_results,
            "no_match": no_match,
            "message": message_text
        }

        await event_queue.enqueue_event(
            Message(
                parts=[Part(text=json.dumps(response_payload))]
            )
        )

    async def cancel(self, context, event_queue):
        pass


# Agent Card definition
agent_card_data = AgentCard(
    name="local_guide_agent",
    description="RAG-grounded local activity and POI recommendations with authentic Wikivoyage citations.",
    supported_interfaces=[
        AgentInterface(
            url=f"http://localhost:{os.getenv('LOCAL_GUIDE_AGENT_PORT', '8004')}",
            protocol_binding="JSONRPC",
            protocol_version="1.0"
        ),
        AgentInterface(
            url="http://local_guide_agent:8004",
            protocol_binding="JSONRPC",
            protocol_version="1.0"
        )
    ],
    skills=[
        AgentSkill(
            id="recommend_activities",
            name="recommend_activities",
            description="Finds grounded local activities, sights, and POIs for a destination."
        )
    ]
)

request_handler = DefaultRequestHandler(
    agent_executor=LocalGuideAgentExecutor(),
    task_store=InMemoryTaskStore(),
    agent_card=agent_card_data
)

add_a2a_routes_to_fastapi(
    app,
    agent_card_routes=create_agent_card_routes(agent_card_data),
    jsonrpc_routes=create_jsonrpc_routes(request_handler, rpc_url='/'),
)


@app.get("/")
async def root():
    return {"service": "local_guide_agent", "status": "running"}


@app.get("/health")
async def health():
    return {"status": "ok"}
