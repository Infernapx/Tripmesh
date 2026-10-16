import os
import re
import json
import logging
from datetime import date, timedelta
from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv())
from fastapi import FastAPI
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.server.routes import add_a2a_routes_to_fastapi, create_agent_card_routes, create_jsonrpc_routes
from a2a.types import AgentCard, AgentSkill, AgentInterface, Message, Part

try:
    from .agent import hotel_crew
except ImportError:
    from app.agent import hotel_crew

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

app = FastAPI(title="TripMesh Hotel Agent", version="0.0.1")

class HotelAgentExecutor(AgentExecutor):
    async def execute(self, context, event_queue):
        user_input = context.get_user_input()
        logger.info(f"Hotel Agent received task: {user_input}")
        
        default_in = (date.today() + timedelta(days=14)).isoformat()
        default_out = (date.today() + timedelta(days=16)).isoformat()
        try:
            data = json.loads(user_input)
            destination = data.get("destination", "LIS")
            check_in = data.get("check_in") or default_in
            check_out = data.get("check_out") or default_out
            num_travelers = int(data.get("num_travelers", 1))
            price_ceiling = data.get("price_ceiling") or data.get("max_price")
            if price_ceiling is not None:
                price_ceiling = float(price_ceiling)
            excluded_hotels = data.get("excluded_hotels") or []
            prefer_cheaper = bool(data.get("prefer_cheaper", False))
        except Exception:
            match = re.search(r'in\s+(\w+)\s+from\s+([\d-]+)\s+to\s+([\d-]+)', user_input, re.IGNORECASE)
            if match:
                destination = match.group(1).upper()
                check_in = match.group(2)
                check_out = match.group(3)
            else:
                destination = "LIS"
                check_in = default_in
                check_out = default_out
            num_travelers = 1
            price_ceiling = None
            excluded_hotels = []
            prefer_cheaper = False

        logger.info(f"Hotel search parameters: destination={destination}, check_in={check_in}, check_out={check_out}, travelers={num_travelers}, price_ceiling={price_ceiling}, excluded={excluded_hotels}, prefer_cheaper={prefer_cheaper}")
        
        results = hotel_crew.kickoff({
            "destination": destination,
            "check_in": check_in,
            "check_out": check_out,
            "num_travelers": num_travelers,
            "price_ceiling": price_ceiling,
            "excluded_hotels": excluded_hotels,
            "prefer_cheaper": prefer_cheaper
        })
        
        contains_estimated_data = False
        serialized = []
        for opt in results:
            serialized.append({
                "name": opt.name,
                "location": {"name": opt.location.name, "iata": opt.location.iata},
                "check_in": opt.check_in.isoformat(),
                "check_out": opt.check_out.isoformat(),
                "price_per_night": {"amount": opt.price_per_night.amount, "currency": opt.price_per_night.currency},
                "total_price": {"amount": opt.total_price.amount, "currency": opt.total_price.currency},
                "rating": opt.rating,
                "amenities": opt.amenities,
                "booking_ref": opt.booking_ref,
                "data_quality": opt.data_quality.value,
                "is_estimated": opt.is_estimated
            })
            if opt.is_estimated:
                contains_estimated_data = True
                
        no_cheaper_options = False
        if price_ceiling is not None and len(serialized) == 0:
            no_cheaper_options = True

        response_payload = {
            "success": True,
            "results": serialized,
            "contains_estimated_data": contains_estimated_data,
            "no_cheaper_options": no_cheaper_options
        }
        
        await event_queue.enqueue_event(
            Message(
                parts=[Part(text=json.dumps(response_payload))]
            )
        )

    async def cancel(self, context, event_queue):
        pass

# Agent card definition
agent_card_data = AgentCard(
    name="hotel_agent",
    description="Hotel accommodations and lodging services search.",
    supported_interfaces=[
        AgentInterface(
            url=f"http://localhost:{os.getenv('HOTEL_AGENT_PORT', '8003')}",
            protocol_binding="JSONRPC",
            protocol_version="1.0"
        ),
        AgentInterface(
            url="http://hotel_agent:8003",
            protocol_binding="JSONRPC",
            protocol_version="1.0"
        )
    ],
    skills=[
        AgentSkill(
            id="search_hotels",
            name="search_hotels",
            description="Finds hotel and accommodation options at destination for a date range."
        )
    ]
)

request_handler = DefaultRequestHandler(
    agent_executor=HotelAgentExecutor(),
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
    return {"service": "hotel_agent", "status": "running"}

@app.get("/health")
async def health():
    return {"status": "ok"}
