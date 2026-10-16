# PRD.md — Product Requirements Document

## 1. Project name
**TripMesh** — a multi-agent AI travel concierge (working name, change freely).

## 2. Problem statement
Planning a trip means juggling flights, hotels, local activities, budget, currency,
and visa/weather constraints across a dozen tabs. Most "AI travel planners" today
are a single chatbot with a few tool calls bolted on — they don't reason across
constraints, don't remember you, and don't explain *why* they recommend something.

## 3. Vision
A network of specialized AI agents that negotiate with each other (over the A2A
protocol) to produce a grounded, personalized, explainable travel plan — and can
adapt that plan live when something changes (a delayed flight, a price spike, a
new preference).

## 4. Target users
| Persona | Need |
|---|---|
| Solo budget traveler | Fast, optimized itinerary within a hard budget cap |
| Group of friends planning together | A plan that satisfies everyone's differing preferences |
| Developer/learner (you) | A working, portfolio-grade reference implementation of agentic AI, A2A, and RAG patterns |

## 5. Goals
- Ship a working multi-agent system, not a single-prompt chatbot.
- Every recommendation should be explainable and, where possible, cited (RAG).
- The system should be able to react to change without the user re-prompting from scratch.
- The codebase should be a genuine demonstration of current agentic AI patterns:
  LangGraph orchestration, A2A inter-agent protocol, RAG, tool calling, memory,
  and human-in-the-loop confirmation.

## 6. Non-goals (v1)
- Real payment processing / actually booking flights or hotels.
- Mobile native apps (web only for v1).
- Supporting every travel provider API — a small curated set is enough to prove the pattern.
- Enterprise-scale multi-tenancy, billing, or auth beyond a simple user session.

## 7. Core features (MVP — must have)
1. **Conversational trip intake** — user describes destination, dates, budget, group size, preferences in natural language.
2. **Multi-agent planning** — Planner agent decomposes the request and delegates to Flight, Hotel, Local Guide, and Budget agents over A2A.
3. **RAG-grounded local recommendations** — Local Guide agent retrieves from a vector store of travel guides/reviews and cites sources.
4. **Budget reconciliation** — Budget agent checks the combined plan against the user's stated budget and flags/adjusts if over.
5. **Itinerary output** — day-by-day plan the user can view in-chat and export (PDF/.ics).
6. **Human-in-the-loop confirmation** — nothing is "booked" (simulated) without explicit user approval.

## 8. Exciting / differentiating features (v1.5 — should have)
1. **Live agent trace** — user can watch which agent is working on what, in real time.
2. **Dynamic replanning** — if a constraint changes (flight delay, price spike), the orchestrator re-triggers only the affected agents.
3. **Budget-as-negotiation** — Budget agent asks other agents for alternative (cheaper/pricier) options rather than just filtering a static list.
4. **Long-term memory** — the system remembers past trips and stated preferences across sessions.
5. **Multi-modal transport** — the Transport Agent (formerly "Flight Agent") compares flight, train, and bus options where a provider API/feed is available, and recommends the best mode by price, time, or carbon footprint — not flight-only. Coverage depends on API availability per region (see Architecture.md §3); the agent must clearly indicate when a mode is unavailable rather than silently omitting it.

## 9. Stretch features (v2 — nice to have)
1. Multi-modal destination search (upload a photo → find similar places).
2. Group trip consensus mode (multiple users, one merged itinerary).
3. Voice interface (speech in, speech out).
4. Carbon footprint estimate per itinerary (made easier once train/bus data exists alongside flights).
5. Full regional GTFS ingestion for broader train/bus coverage beyond the initial provider set.

## 10. Success criteria
- A user can go from "plan me a trip" to a complete, exportable itinerary without leaving the chat.
- At least one agent (Local Guide) demonstrably grounds its answers in retrieved documents with citations, not hallucination.
- The system survives a mid-conversation change (e.g., "actually my budget is $500 less") by replanning rather than restarting.
- All agents run as independent A2A services and can be demoed running/communicating independently (proves the "many frameworks talking to each other" pattern).

## 11. Assumptions & constraints
- Real booking APIs are mocked or use free/sandbox tiers (see Architecture.md).
- Single developer (or small team) building incrementally — see Phases.md.
- LLM costs should be kept low during development by defaulting to a local model (Ollama) where practical, with cloud models reserved for final quality passes.
