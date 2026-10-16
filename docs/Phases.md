# Phases.md — Incremental build plan

Each phase should end in something runnable and testable. Do not start a phase
until the previous one's exit criteria are met.

## Phase 0 — Foundations
**Goal**: Repo skeleton, shared contracts, no agent logic yet.
- Create folder structure per Architecture.md.
- Define `shared/schemas.py` (Trip, ItineraryItem, BudgetConstraint, etc.).
- Set up `.env.example`, `docker-compose.yml` skeleton.
- Set up Chroma locally and confirm it starts empty.
- **Exit criteria**: `docker-compose up` starts empty containers without errors.

## Phase 1 — First agent end-to-end (Currency/Budget Agent)
**Goal**: Prove the A2A plumbing with the simplest agent.
- Build Budget Agent using Frankfurter API (free, no key) + mock budget logic.
- Expose it as an A2A server with an `AgentCard`.
- Write `test_budget_agent.py` that calls it directly (no orchestrator yet).
- **Exit criteria**: Can send a currency-conversion request to the Budget Agent
  over A2A and get a correct structured response.

## Phase 2 — Planner Agent + single-agent orchestration
**Goal**: LangGraph orchestrator that calls exactly one specialist agent.
- Build Planner Agent with a minimal graph: parse intent → call Budget Agent → return.
- Add the Astro frontend (index page + a ChatPanel island) wired to the Planner via the gateway.
- **Exit criteria**: User can type "convert $500 to EUR" in the UI and get a
  correct answer routed through Planner → A2A → Budget Agent → back.

## Phase 3 — Transport and Hotel agents
**Goal**: Expand the specialist roster.
- Build Transport Agent (LangGraph) with the flight adapter first
  (Amadeus sandbox or mock) — get one mode working end to end before adding others.
- Build Hotel Agent (CrewAI + Amadeus sandbox or mock) — intentionally a
  different framework to prove framework-agnostic A2A communication.
- Extend Planner's graph to call these in parallel and aggregate results.
- **Exit criteria**: A single "plan me a trip to Lisbon" request returns
  flight options + hotel options aggregated into one response.

## Phase 3a — Add train and bus adapters (conditional on API access)
**Goal**: Prove the multi-modal adapter pattern without disrupting Phase 3.
- Add a train adapter (Deutsche Bahn or National Rail, whichever fits your
  target region) behind the existing `TransportAdapter` interface.
- Add a GTFS-based bus adapter for at least one region with public GTFS data.
- Update the Transport Agent to query all adapters that cover the requested
  route and merge results; clearly label any mock/estimated results.
- **Exit criteria**: For a route with real train/bus coverage, the response
  includes flight, train, and bus options side by side, each correctly
  labeled with its source (real API vs. estimate). No changes required to
  the Planner or any other agent.

## Phase 4 — RAG-powered Local Guide Agent
**Goal**: Add the grounded-recommendation feature (the "wow" feature).
- Collect a small seed dataset of travel guides/reviews (public sources).
- Build `ingest.py` to chunk + embed into Chroma.
- Build the Local Guide Agent to retrieve + cite sources in its answers.
- Wire it into the Planner's graph.
- **Exit criteria**: A request for "things to do in Lisbon" returns
  recommendations with visible source citations, and honestly says "no
  strong match found" when retrieval is weak.

## Phase 5 — Budget reconciliation & negotiation loop
**Goal**: Make the Budget Agent an active negotiator, not just a calculator.
- Planner sends the full aggregated plan to Budget Agent for a check against
  the user's stated cap.
- If over budget, Planner requests cheaper alternatives from the relevant
  agent(s) and re-aggregates (bounded retry, e.g. max 2 rounds).
- **Exit criteria**: Giving a budget below the first proposed plan's cost
  results in an automatically revised, cheaper plan without the user re-asking.

## Phase 6 — Human-in-the-loop confirmation + itinerary output
**Goal**: Make the system feel trustworthy and demo-able end to end.
- Add a LangGraph interrupt before finalizing a plan, presenting a summary and
  requiring explicit user confirmation.
- Build itinerary export (PDF via docx/pdf tooling, and `.ics` calendar file).
- **Exit criteria**: Full flow from chat request → aggregated, budget-checked
  plan → user confirmation → exported itinerary file.

## Phase 7 — Memory & personalization
**Goal**: The system remembers the user across sessions.
- Add a simple per-user memory store (preferences, past trips).
- Planner consults memory when building new plans ("last time you preferred
  boutique hotels — should I bias toward that again?").
- **Exit criteria**: A returning user's stated preference from a prior session
  visibly influences a new plan.

## Phase 8 — Dynamic replanning
**Goal**: React to mid-conversation change without restarting.
- Support messages like "actually my budget just dropped $500" or "my flight
  got delayed 3 hours" mid-session.
- Planner re-enters the graph at only the affected node(s).
- **Exit criteria**: A budget change mid-conversation triggers only the Budget
  Agent (and any agent it needs to re-negotiate with), not a full re-plan from scratch.

## Phase 9 — Observability & polish
**Goal**: Make the internals visible and the UI presentable.
- Add Langfuse tracing across all agent calls.
- Build the AgentTrace island in Astro, showing which agent is doing what in real time (via SSE/polling from the gateway).
- Build out `ItineraryDay.astro` components per `Design.md`'s boarding-pass motif, apply the Tailwind theme (colors/type tokens) across the app.
- **Exit criteria**: A live demo can show, in real time, which agent is
  working, what it retrieved/decided, and why.

## Phase 10 (stretch) — Multi-modal & group mode
- Photo-based destination search (CLIP embeddings + similarity search).
- Group trip consensus mode (multiple user sessions merged by a coordinator).
- Voice interface.
- Only start this phase once Phases 0–9 are stable and demoable.

---
**Rule of thumb**: if a phase's exit criteria can't be demoed in under two
minutes, the phase is too big — split it.
