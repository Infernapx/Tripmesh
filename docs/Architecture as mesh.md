# Architecture.md

## 1. High-level flow

1. User sends a message via the chat UI (Astro frontend).
2. The **Planner Agent** (LangGraph, orchestrator) parses intent and builds a plan graph.
3. Planner calls specialist agents over **A2A protocol**, each running as its own
   independent server with its own `AgentCard`:
   - Transport Agent (flight, train, bus — mode chosen per availability/preference)
   - Hotel Agent
   - Local Guide Agent (RAG-backed)
   - Budget/Currency Agent
4. Specialist agents call external tools/APIs (or mocks) and, where relevant,
   query the shared **vector store** (RAG) and **memory store** (per-user preferences).
5. Planner aggregates responses into a candidate itinerary.
6. Budget Agent reconciles the plan against the stated budget; if over, Planner
   asks the relevant agent(s) for cheaper alternatives (negotiation loop).
7. Planner presents the itinerary to the user and **pauses for human confirmation**
   before marking anything "booked" (simulated).
8. On confirmation, itinerary is stored (for future memory/personalization) and
   can be exported (PDF/.ics).
9. If the user changes a constraint mid-conversation, Planner re-enters the graph
   at the affected node(s) only (dynamic replanning), not from scratch.

## 2. Why A2A + LangGraph together
- **LangGraph** handles the orchestration logic *inside* the Planner: state,
  branching, cycles, interrupts (human-in-the-loop).
- **A2A protocol** handles communication *between* independently deployable
  agents, each potentially built with a different framework (CrewAI, LangGraph,
  plain SDK) — this is what makes agents swappable/scalable independently and
  is the core learning goal of the project.

## 3. Tech stack

| Layer | Choice | Notes |
|---|---|---|
| Agent orchestration | LangGraph | Planner agent, stateful graph, interrupts |
| Inter-agent protocol | `a2a-sdk` (Python) | AgentCard per agent, JSON-RPC over HTTP |
| Specialist agent frameworks | CrewAI (Hotel), LangGraph (Transport, Budget), plain LangChain (Local Guide + RAG) | mirrors the reference repo's "mixed framework" pattern |
| RAG | LlamaIndex or LangChain retriever + Chroma | Chroma runs embedded, no server needed for dev |
| Embeddings | `sentence-transformers` (bge-small-en or e5-small) | free, local, no API key |
| LLM | Anthropic/OpenAI API for quality; Ollama (llama3.1/qwen2.5) for local dev | keep a provider-agnostic wrapper |
| Memory | Simple vector-store-backed user profile (or `mem0`) | per-user preferences, past trips |
| External data (real or mocked) | Open-Meteo (weather), Frankfurter (currency), Amadeus sandbox (flights/hotels), OSM Nominatim (places), `Faker` for anything unavailable | see Rules.md for API policy |
| Transport providers | Amadeus sandbox (flights), Deutsche Bahn API / National Rail (train, region-limited), GTFS static/realtime feeds (train + bus, patchy coverage), mock data elsewhere | see §3a below for the adapter pattern |
| Backend/API | FastAPI (one per agent + a gateway) | |
| Frontend | Astro, with React islands for interactive parts (chat box, live agent trace) | static-first — itinerary/day cards render as plain Astro components, only the chat and trace panel hydrate as islands |
| Frontend styling | Tailwind CSS (`@astrojs/tailwind`) | matches Design.md tokens as a Tailwind theme config |
| Frontend ↔ backend | Astro API routes (SSR mode, `@astrojs/node` adapter) proxy to the FastAPI gateway, or the gateway is called directly from islands via `fetch`/SSE | decide per Phase 2 — see Rules.md §1 for the decision constraint |
| Observability | Langfuse (self-hosted, free) | trace every agent call |
| Containerization | Docker Compose, one service per agent | |
| Export | `reportlab`/docx skill for PDF, `ics` library for calendar | |

### 3a. Transport mode adapter pattern
Flight, train, and bus are all "get from A to B" — one **Transport Agent**
exposes a single A2A skill (`search_transport`) and internally dispatches to
mode-specific adapters rather than being three separate agents:

```
transport_agent/app/adapters/
├── base.py           # TransportAdapter interface: search(origin, dest, date) -> list[TransportOption]
├── flight_amadeus.py
├── train_db.py        # Deutsche Bahn — enabled only if DB_API_KEY is set
├── train_national_rail.py
├── bus_gtfs.py         # generic GTFS-feed adapter, configurable per region
└── mock.py             # always available, used when no real adapter matches the route
```
- Each adapter declares which regions/routes it can serve; the agent queries
  every adapter that claims coverage for the requested origin/destination and
  merges results.
- If zero real adapters cover a route, the agent falls back to `mock.py` and
  **must flag the result as estimated**, never presenting mock data as a real fare.
- Adding a new transport provider later = adding one adapter file, no changes
  to the Planner or any other agent (this is the whole point of the pattern).
- Output schema (`TransportOption` in `shared/schemas.py`) is mode-agnostic:
  `mode: Literal["flight","train","bus"]`, `duration`, `price`, `co2_estimate_kg`
  (optional, enables the carbon-footprint stretch feature for free).

## 4. Folder structure

```
tripmesh/
├── PRD.md
├── Architecture.md
├── Rules.md
├── Phases.md
├── Design.md
├── docker-compose.yml
├── .env.example
├── gateway/                       # thin FastAPI gateway in front of the agents
│   ├── app.py
│   └── requirements.txt
├── frontend/                      # Astro app
│   ├── astro.config.mjs
│   ├── tailwind.config.mjs         # theme = Design.md color/type tokens
│   ├── src/
│   │   ├── layouts/
│   │   │   └── BaseLayout.astro
│   │   ├── pages/
│   │   │   ├── index.astro          # main planner/chat screen
│   │   │   └── trip/[id].astro      # itinerary view (day-by-day cards)
│   │   ├── components/
│   │   │   ├── ItineraryDay.astro   # static "boarding pass" day card
│   │   │   ├── ChatPanel.tsx        # island — the chat interface
│   │   │   └── AgentTrace.tsx       # island — live agent trace panel
│   │   └── styles/
│   │       └── global.css
│   └── package.json
├── planner_agent/                 # LangGraph orchestrator
│   ├── app/
│   │   ├── graph.py                # LangGraph state graph definition
│   │   ├── agent_card.py
│   │   ├── a2a_client.py           # calls specialist agents over A2A
│   │   └── state.py
│   ├── requirements.txt
│   └── test_planner.py
├── transport_agent/                # LangGraph + pluggable mode adapters
│   ├── app/
│   │   ├── agent.py
│   │   ├── agent_executor.py       # A2A executor
│   │   └── adapters/
│   │       ├── base.py
│   │       ├── flight_amadeus.py
│   │       ├── train_db.py
│   │       ├── train_national_rail.py
│   │       ├── bus_gtfs.py
│   │       └── mock.py
│   ├── requirements.txt
│   └── test_transport_agent.py
├── hotel_agent_crewai/            # CrewAI + Amadeus (or mock)
│   ├── app/
│   │   ├── agent.py
│   │   ├── agent_executor.py
│   │   └── tools/hotel_api.py
│   ├── requirements.txt
│   └── test_hotel_agent.py
├── local_guide_agent_rag/         # LangChain + Chroma (RAG)
│   ├── app/
│   │   ├── agent.py
│   │   ├── agent_executor.py
│   │   ├── ingest.py                # builds the vector store from source docs
│   │   └── retriever.py
│   ├── data/                       # raw guides/reviews to ingest
│   ├── requirements.txt
│   └── test_local_guide_agent.py
├── budget_agent/                  # LangGraph + Frankfurter
│   ├── app/
│   │   ├── agent.py
│   │   ├── agent_executor.py
│   │   └── tools/currency_api.py
│   ├── requirements.txt
│   └── test_budget_agent.py
├── shared/                        # cross-agent shared code
│   ├── memory_store.py
│   ├── vector_store.py
│   ├── schemas.py                  # shared pydantic models (Itinerary, Trip, etc.)
│   └── observability.py
└── tests/
    └── test_all_agents.py
```

## 5. Agent communication contract (A2A)
- Every agent exposes an `AgentCard` describing its skills, input/output schema, and endpoint.
- Planner discovers agents via a static registry (`shared/agent_registry.json`) in v1
  (dynamic discovery is a stretch goal).
- Messages between agents use a shared `Trip`/`Itinerary` pydantic schema
  (`shared/schemas.py`) so every agent speaks the same data language regardless
  of its internal framework.

## 6. Data flow for RAG (Local Guide Agent)
1. `ingest.py` chunks source documents (guides, blog posts, review exports) and
   embeds them into Chroma.
2. At query time, the agent retrieves top-k chunks, passes them to the LLM with
   an instruction to cite sources, and returns both the answer and the source list.
3. The Planner surfaces those citations to the user.

## 7. Deployment model (dev)
- `docker-compose up` starts the gateway + all five agent services + the Astro frontend, each on their own ports.
- Each agent is independently restartable/testable — this is the point of A2A:
  agents are opaque services, not local function calls.
