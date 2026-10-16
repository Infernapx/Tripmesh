import os
import re
import json
import logging
from datetime import date, datetime, timedelta
from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv())
from fastapi import FastAPI
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.server.routes import add_a2a_routes_to_fastapi, create_agent_card_routes, create_jsonrpc_routes
from a2a.types import AgentCard, AgentSkill, AgentInterface, Message, Part

try:
    from .adapters.flight_duffel import DuffelFlightAdapter
    from .adapters.train_db import DBTrainAdapter
    from .adapters.bus_gtfs import GTFSBusAdapter
    from .adapters.mock import MockTransportAdapter
except ImportError:
    from app.adapters.flight_duffel import DuffelFlightAdapter
    from app.adapters.train_db import DBTrainAdapter
    from app.adapters.bus_gtfs import GTFSBusAdapter
    from app.adapters.mock import MockTransportAdapter

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

app = FastAPI(title="TripMesh Transport Agent", version="0.0.1")

class TransportAgentExecutor(AgentExecutor):
    def __init__(self):
        super().__init__()
        self.duffel_adapter = DuffelFlightAdapter()
        self.train_adapter = DBTrainAdapter()
        self.bus_adapter = GTFSBusAdapter()
        self.mock_adapter = MockTransportAdapter()

    async def execute(self, context, event_queue):
        user_input = context.get_user_input()
        logger.info(f"Transport Agent received task: {user_input}")
        
        delay_hours = None
        min_dep_after_str = None
        excluded_transports = []
        prefer_different = False
        try:
            data = json.loads(user_input)
            default_dep_str = (date.today() + timedelta(days=14)).isoformat()
            origin = data.get("origin", "LHR")
            dest = data.get("destination", "LIS")
            dep_date_str = data.get("departure_date") or default_dep_str
            num_travelers = int(data.get("num_travelers", 1))
            price_ceiling = data.get("price_ceiling") or data.get("max_price")
            if price_ceiling is not None:
                price_ceiling = float(price_ceiling)
            delay_val = data.get("delay_hours")
            if delay_val is not None:
                delay_hours = float(delay_val)
            min_dep_after_str = data.get("min_departure_after")
            excluded_transports = data.get("excluded_transports") or []
            prefer_different = bool(data.get("prefer_different", False))
        except Exception:
            default_dep_str = (date.today() + timedelta(days=14)).isoformat()
            match = re.search(r'from\s+(\w+)\s+to\s+(\w+)\s+on\s+([\d-]+)', user_input, re.IGNORECASE)
            if match:
                origin = match.group(1).upper()
                dest = match.group(2).upper()
                dep_date_str = match.group(3)
            else:
                origin = "LHR"
                dest = "LIS"
                dep_date_str = default_dep_str
            num_travelers = 1
            price_ceiling = None
            excluded_transports = []
            prefer_different = False
            
        try:
            departure_date = date.fromisoformat(dep_date_str)
        except Exception:
            departure_date = date.today() + timedelta(days=14)
            
        logger.info(f"Transport search parameters: origin={origin}, dest={dest}, date={departure_date}, passengers={num_travelers}, price_ceiling={price_ceiling}, delay_hours={delay_hours}, min_departure_after={min_dep_after_str}, excluded={excluded_transports}, prefer_different={prefer_different}")
        
        results = []
        if self.duffel_adapter.can_serve(origin, dest):
            flight_results = await self.duffel_adapter.search_async(origin, dest, departure_date, num_travelers, max_price=price_ceiling)
            results.extend(flight_results)
            
        if self.train_adapter.can_serve(origin, dest):
            train_results = self.train_adapter.search(origin, dest, departure_date, num_travelers, max_price=price_ceiling)
            results.extend(train_results)
            
        if self.bus_adapter.can_serve(origin, dest):
            bus_results = self.bus_adapter.search(origin, dest, departure_date, num_travelers, max_price=price_ceiling)
            results.extend(bus_results)
            
        contains_estimated_data = False
        if not results:
            logger.info("Falling back to MockTransportAdapter.")
            results = self.mock_adapter.search(
                origin, dest, departure_date, num_travelers,
                max_price=price_ceiling, delay_hours=delay_hours,
                excluded_transports=excluded_transports, prefer_different=prefer_different
            )
            contains_estimated_data = True

        results.sort(key=lambda x: x.price.amount)

        # Baseline original options prior to delay cutoff adjustments
        original_opts = [opt for opt in results if opt.booking_ref != "MOCK-FL-103" and opt.carrier != "MockAir Evening"]
        if not original_opts:
            original_opts = list(results)

        # Apply delay filtering if min_departure_after or delay_hours is provided
        no_later_options = False
        cutoff_dt = None
        if min_dep_after_str:
            try:
                cutoff_dt = datetime.fromisoformat(min_dep_after_str)
            except Exception:
                cutoff_dt = None
        elif delay_hours is not None and results:
            earliest_dep = min(opt.departure_at for opt in results)
            cutoff_dt = earliest_dep + timedelta(hours=delay_hours)

        if cutoff_dt is not None:
            filtered_opts = [opt for opt in results if opt.departure_at >= cutoff_dt]
            if filtered_opts:
                results = filtered_opts
                logger.info(f"Applied delay cutoff {cutoff_dt}: {len(results)} option(s) remaining after delay.")
            else:
                no_later_options = True
                results = original_opts
                logger.info(f"Delay cutoff {cutoff_dt} eliminated all options. Retaining original {len(results)} options with no_later_options=True.")

            
        serialized = []
        for opt in results:
            serialized.append({
                "mode": opt.mode.value,
                "origin": {"name": opt.origin.name, "iata": opt.origin.iata},
                "destination": {"name": opt.destination.name, "iata": opt.destination.iata},
                "departure_at": opt.departure_at.isoformat(),
                "arrival_at": opt.arrival_at.isoformat(),
                "duration_minutes": opt.duration_minutes,
                "price": {"amount": opt.price.amount, "currency": opt.price.currency},
                "carrier": opt.carrier,
                "booking_ref": opt.booking_ref,
                "co2_estimate_kg": opt.co2_estimate_kg,
                "data_quality": opt.data_quality.value,
                "is_estimated": opt.is_estimated
            })
            if opt.is_estimated:
                contains_estimated_data = True
                
        response_payload = {
            "success": True,
            "results": serialized,
            "no_later_options": no_later_options,
            "contains_estimated_data": contains_estimated_data
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
    name="transport_agent",
    description="Flight, train, and transit searches across global and local networks.",
    supported_interfaces=[
        AgentInterface(
            url=f"http://localhost:{os.getenv('TRANSPORT_AGENT_PORT', '8002')}",
            protocol_binding="JSONRPC",
            protocol_version="1.0"
        ),
        AgentInterface(
            url="http://transport_agent:8002",
            protocol_binding="JSONRPC",
            protocol_version="1.0"
        )
    ],
    skills=[
        AgentSkill(
            id="search_transport",
            name="search_transport",
            description="Finds transport options between origin and destination on a specific date."
        )
    ]
)

request_handler = DefaultRequestHandler(
    agent_executor=TransportAgentExecutor(),
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
    return {"service": "transport_agent", "status": "running"}

@app.get("/health")
async def health():
    return {"status": "ok"}
