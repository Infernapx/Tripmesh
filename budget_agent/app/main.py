import os
import logging
import re
import json
from datetime import date
from typing import Optional
import httpx
from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv())
from fastapi import FastAPI
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.server.routes import add_a2a_routes_to_fastapi, create_agent_card_routes, create_jsonrpc_routes
from a2a.types import AgentCard, AgentSkill, AgentInterface, Message, Part
from shared.schemas import CostLineItem, BudgetReconciliation

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

# Same-day TTL exchange rate cache: (from_currency, to_currency) -> (rate, cache_date)
_exchange_rate_cache: dict[tuple[str, str], tuple[float, date]] = {}


async def get_exchange_rate(from_curr: str, to_curr: str, client: Optional[httpx.AsyncClient] = None) -> float:
    """
    Retrieves exchange rate from Frankfurter API with same-day in-memory caching.
    """
    from_c = from_curr.strip().upper()
    to_c = to_curr.strip().upper()

    if from_c == to_c:
        return 1.0

    cache_key = (from_c, to_c)
    today = date.today()
    if cache_key in _exchange_rate_cache:
        cached_rate, cached_date = _exchange_rate_cache[cache_key]
        if cached_date == today:
            logger.info(f"Exchange rate cache hit for {from_c}->{to_c}: {cached_rate}")
            return cached_rate

    url = f"https://api.frankfurter.app/latest?from={from_c}&to={to_c}"
    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=10.0, follow_redirects=True)
        close_client = True

    try:
        res = await client.get(url)
        if res.status_code == 200:
            data = res.json()
            rate = float(data["rates"][to_c])
            _exchange_rate_cache[cache_key] = (rate, today)
            logger.info(f"Fetched and cached exchange rate for {from_c}->{to_c}: {rate}")
            return rate
        else:
            raise RuntimeError(f"Frankfurter API returned HTTP {res.status_code}")
    finally:
        if close_client:
            await client.aclose()


async def reconcile_budget_items(
    items: list[CostLineItem],
    target_currency: str,
    budget_cap: Optional[float] = None,
    client: Optional[httpx.AsyncClient] = None
) -> BudgetReconciliation:
    """
    Normalizes all line items to target_currency, computes totals and overages,
    and returns structured BudgetReconciliation.
    """
    target_curr = target_currency.strip().upper()
    breakdown = {"transport": 0.0, "hotel": 0.0, "local_guide": 0.0}
    total_cost = 0.0
    conversion_failures: list[CostLineItem] = []

    for item in items:
        try:
            rate = await get_exchange_rate(item.currency, target_curr, client=client)
            converted_amount = round(item.amount * rate, 2)
            total_cost += converted_amount
            agent_key = item.source_agent
            breakdown[agent_key] = round(breakdown.get(agent_key, 0.0) + converted_amount, 2)
        except Exception as exc:
            logger.error(f"Currency conversion failed for item ({item}): {exc}")
            conversion_failures.append(item)

    total_cost = round(total_cost, 2)
    over_budget = False
    overage_amount = 0.0

    if budget_cap is not None and budget_cap > 0:
        if total_cost > budget_cap:
            over_budget = True
            overage_amount = round(total_cost - budget_cap, 2)

    return BudgetReconciliation(
        total_cost=total_cost,
        currency=target_curr,
        budget_cap=budget_cap,
        over_budget=over_budget,
        overage_amount=overage_amount,
        breakdown_by_agent=breakdown,
        items=items,
        conversion_failures=conversion_failures
    )


class BudgetAgentExecutor(AgentExecutor):
    async def execute(self, context, event_queue):
        user_input = context.get_user_input()
        logger.info(f"Budget Agent received input: {user_input}")

        # Check if input is a structured JSON request for reconcile_budget
        try:
            data = json.loads(user_input)
            if isinstance(data, dict) and ("items" in data or data.get("skill") == "reconcile_budget"):
                raw_items = data.get("items", [])
                items = [CostLineItem(**it) for it in raw_items]
                target_currency = data.get("target_currency", "EUR")
                budget_cap = data.get("budget_cap")
                if budget_cap is not None:
                    budget_cap = float(budget_cap)

                reconciliation = await reconcile_budget_items(items, target_currency, budget_cap)
                result_json = json.dumps(reconciliation.model_dump())
                await event_queue.enqueue_event(Message(parts=[Part(text=result_json)]))
                return
        except Exception as e:
            logger.debug(f"Input is not structured JSON reconciliation: {e}")

        # Handle currency conversion (natural language or parameters)
        amount = 1.0
        from_curr = "USD"
        to_curr = "EUR"

        match = re.search(r'(?:convert\s+)?\$?(\d+(?:\.\d+)?)\s*([A-Za-z]{3})?\s+to\s+([A-Za-z]{3})', user_input, re.IGNORECASE)
        if match:
            amount = float(match.group(1))
            if match.group(2):
                from_curr = match.group(2).upper()
            else:
                from_curr = "USD"
            to_curr = match.group(3).upper()
        else:
            match2 = re.search(r'(\d+(?:\.\d+)?)\s*([A-Za-z]{3})\s+([A-Za-z]{3})', user_input, re.IGNORECASE)
            if match2:
                amount = float(match2.group(1))
                from_curr = match2.group(2).upper()
                to_curr = match2.group(3).upper()

        logger.info(f"Parsed conversion: {amount} {from_curr} to {to_curr}")

        try:
            rate = await get_exchange_rate(from_curr, to_curr)
            converted_val = round(amount * rate, 2)
            result_text = f"{amount:.2f} {from_curr} is equivalent to {converted_val:.2f} {to_curr}."
        except Exception as e:
            logger.error(f"Frankfurter API error: {e}")
            rate = 0.9 if from_curr == "USD" and to_curr == "EUR" else 1.1
            converted_val = round(amount * rate, 2)
            result_text = f"[Estimated] {amount:.2f} {from_curr} is equivalent to {converted_val:.2f} {to_curr} (Frankfurter fallback)."

        await event_queue.enqueue_event(Message(parts=[Part(text=result_text)]))

    async def cancel(self, context, event_queue):
        pass


app = FastAPI(title="TripMesh Budget Agent", version="0.0.1")

agent_card = AgentCard(
    name="budget_agent",
    description="Currency conversion (Frankfurter) and budget reconciliation / negotiation.",
    supported_interfaces=[
        AgentInterface(
            url=f"http://localhost:{os.getenv('BUDGET_AGENT_PORT', '8005')}",
            protocol_binding="JSONRPC",
            protocol_version="1.0"
        ),
        AgentInterface(
            url="http://budget_agent:8005",
            protocol_binding="JSONRPC",
            protocol_version="1.0"
        )
    ],
    skills=[
        AgentSkill(
            id="convert_currency",
            name="convert_currency",
            description="Converts currency using Frankfurter API",
        ),
        AgentSkill(
            id="reconcile_budget",
            name="reconcile_budget",
            description="Reconciles multi-currency cost line items against a target budget cap with same-day exchange rates."
        )
    ]
)

request_handler = DefaultRequestHandler(
    agent_executor=BudgetAgentExecutor(),
    task_store=InMemoryTaskStore(),
    agent_card=agent_card
)

# A2A endpoints
add_a2a_routes_to_fastapi(
    app,
    agent_card_routes=create_agent_card_routes(agent_card),
    jsonrpc_routes=create_jsonrpc_routes(request_handler, rpc_url='/'),
)


@app.get("/")
async def root():
    return {"service": "budget_agent", "status": "running"}


@app.get("/health")
async def health():
    return {"status": "ok"}
