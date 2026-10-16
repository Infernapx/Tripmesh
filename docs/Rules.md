# Rules.md — Boundaries for AI-assisted development

These rules apply to any AI assistant (Claude, Copilot, etc.) working on this
codebase, and to human contributors following the same conventions.

## 1. Libraries — use
- **Orchestration**: LangGraph only. Do not introduce a second orchestration
  framework (e.g. AutoGen, raw ReAct loops) without updating Architecture.md first.
- **Inter-agent protocol**: `a2a-sdk` (official Python SDK) only. Do not hand-roll
  a custom HTTP protocol between agents.
- **RAG**: LangChain or LlamaIndex retrievers + Chroma for the vector store.
  Do not swap in a hosted vector DB (Pinecone, Weaviate Cloud) unless a free
  tier is confirmed and documented in `.env.example`.
- **Embeddings**: `sentence-transformers` local models by default. Only use a
  paid embeddings API if explicitly requested.
- **HTTP/API layer**: FastAPI for all agent servers.
- **Data validation**: Pydantic v2 models for every cross-agent message
  (defined in `shared/schemas.py`) — no passing raw dicts between agents.

## 2. Libraries — avoid
- No deprecated LangChain `Chain`-style APIs (`LLMChain`, etc.) — use LCEL /
  LangGraph patterns only.
- No scraping of sites that disallow it (respect `robots.txt`) — use official
  APIs or clearly-marked mock data instead.
- No heavyweight ORMs for what is, in v1, a small local/SQLite or file-backed store.
- No frontend framework beyond Astro — don't introduce a second UI stack
  mid-project. Interactive elements (chat, live agent trace) are React islands
  within Astro, not a separate SPA. Keep hydration minimal — if a component
  doesn't need interactivity, it stays a static `.astro` component.

## 3. API keys & external calls
- Every external API call must have a **mock fallback** so the system runs
  fully offline/keyless for local development (see Architecture.md's mock/free list).
- Transport providers (train/bus especially) vary by region and most are not
  self-serve. New transport adapters must declare which regions/routes they
  cover (`base.py` interface in Architecture.md §3a) and must never silently
  claim coverage they don't have. If a route has no real adapter, the response
  must be clearly labeled as an estimate, not presented as a live fare.
- Never hardcode API keys. All secrets go in `.env`, loaded via `.env.example`
  as a template. `.env` must be in `.gitignore`.
- Respect published rate limits. Cache responses where sensible (e.g. currency
  rates for a day) rather than re-calling on every message.

## 4. Error handling
- Every agent executor must catch exceptions at the A2A boundary and return a
  structured error response (never let a raw stack trace cross an agent boundary).
- Specialist agents must degrade gracefully: if an external API fails, fall back
  to cached/mock data and clearly flag the itinerary item as "estimated" rather
  than silently failing or hallucinating a number.
- The Planner must never present a "confirmed" booking action without an
  explicit human-in-the-loop approval step.
- Timeouts: every A2A call must have an explicit timeout (default 15s) and a
  defined fallback behavior on timeout.

## 5. RAG / grounding rules
- The Local Guide agent must not present a recommendation as fact without a
  retrieved source; if retrieval returns nothing relevant, it must say so
  rather than fall back to unsourced LLM knowledge.
- Do not reproduce large verbatim excerpts from ingested sources in output —
  paraphrase and cite, consistent with standard copyright practice.

## 6. What the AI assistant should do
- Build incrementally, one phase at a time, per `Phases.md`. Do not jump ahead
  to later phases' features "for convenience."
- Write a test script for each agent (`test_<agent>.py`) before considering
  that agent's phase complete.
- Keep each agent independently runnable and testable (true to the A2A
  "opaque service" philosophy) — no importing one agent's internals directly
  into another.
- Ask before introducing a new external dependency not listed in Architecture.md.
- Flag any place where a security-sensitive shortcut is being taken (e.g.
  storing secrets in code, disabling TLS verification) rather than silently doing it.

## 7. What the AI assistant should not do
- Should not implement real payment or real booking execution — this project
  simulates booking confirmation only.
- Should not silently swap a specified library for another "similar" one.
- Should not remove or weaken the human-in-the-loop confirmation step, even
  if it would make the demo flow "smoother."
- Should not commit `.env`, API keys, or any personal data used in testing.
- Should not fabricate citations in the RAG agent's output.

## 8. Code style
- Python: `black` + `ruff`, type hints on all public functions.
- Commit messages: `<agent>: <what changed>` (e.g. `hotel_agent: add mock fallback for Amadeus timeout`).
- Every new agent ships with a `README.md` explaining how to run and test it standalone.
