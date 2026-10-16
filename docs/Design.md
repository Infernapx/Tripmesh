# Design.md — Visual identity

## 1. Direction
TripMesh should feel like a **cartographer's field journal crossed with a
boarding pass** — not a generic SaaS dashboard, and not the AI-cliché cream
background with a terracotta accent. The visual language draws from flight
paths, ink stamps, and vintage travel-poster typography, kept restrained and
functional so it doesn't fight the chat UI.

**Signature element**: the live agent trace (Phase 9) is rendered like a
flight path being drawn across a map — each active agent lights up like a
waypoint being stamped, not a generic spinner/progress bar.

## 2. Color palette

| Token | Hex | Use |
|---|---|---|
| `--ink-900` | `#12232E` | primary text, dark-mode background |
| `--paper-50` | `#F7F5EF` | light-mode background (aged paper, not stark white) |
| `--horizon-600` | `#2C5F6B` | primary brand color — deep teal, "ocean/sky" |
| `--horizon-300` | `#7FA8AE` | secondary/muted teal, borders, inactive states |
| `--stamp-500` | `#C1662F` | accent — a single warm "ink stamp" burnt-orange, used sparingly (CTAs, active-agent highlight, confirmations) |
| `--brass-400` | `#B9973F` | secondary accent — used for currency/budget-related UI only, evokes old travel-fare stamps |
| `--signal-red` | `#B23A32` | errors, over-budget warnings |
| `--signal-green` | `#3F7554` | success, confirmed states |

Rules:
- `--stamp-500` is the *one* bold color — reserve it for the single most
  important action or state on any given screen (per frontend-design
  guidance: "spend your boldness in one place"). Don't let it bleed into
  decorative use.
- Dark mode: swap `--paper-50` background for `--ink-900`, and lighten text
  to `#EDEAE1`. Keep `--horizon-600` and `--stamp-500` roughly as-is (they
  hold contrast in both modes) — verify contrast ratios ≥ 4.5:1 for body text.

## 3. Typography

| Role | Typeface | Notes |
|---|---|---|
| Display / headings | **Fraunces** (serif, variable) | Used at large sizes only (h1/h2). Gives the vintage travel-poster character without being a full script/decorative face. |
| Body / UI | **Inter** | Clean, highly legible at small sizes for chat bubbles and forms. |
| Data / mono (prices, flight numbers, dates, code) | **JetBrains Mono** | Anything tabular or numeric — fares, times, itinerary line items — uses mono for scan-ability, like a boarding pass. |

Type scale (base 16px):
- h1: 40px / Fraunces / weight 500
- h2: 28px / Fraunces / weight 500
- h3: 20px / Inter / weight 600
- body: 16px / Inter / weight 400, line-height 1.6
- caption/meta: 13px / Inter / weight 400, `--horizon-300` color
- data/mono: 14px / JetBrains Mono / weight 400

Sentence case everywhere — no ALL CAPS headers, no title case buttons.

## 4. Layout principles
- Chat remains the primary surface — the agent trace panel is a secondary,
  collapsible strip (not a competing dashboard), living to the side on desktop
  and below the chat on mobile.
- Itinerary output renders as a **day-by-day "boarding pass stack"**: each day
  is a card with a torn/perforated top edge motif (subtle, CSS only — no
  heavy illustration), using mono type for times and serif for place names.
- Generous whitespace; avoid dashboard-style dense grids. This is a planning
  companion, not an analytics tool.
- Corners: 8px radius on cards/controls, consistent with a soft-edged paper
  feel — not sharp/newspaper, not pill-shaped.

## 5. Iconography
- Line icons only (consistent stroke width, no filled icons), evoking travel
  wayfinding signage: compass, pin, luggage, calendar, currency symbols.
- Icons are functional, not decorative — every icon should replace or
  reinforce a label, never appear purely for visual filler.

## 6. Motion
- Minimal, purposeful motion only:
  - Agent trace: a subtle "path drawing" animation as each agent activates.
  - Confirmation step: a light "stamp" animation on approval (single, quick,
    not repeated/looping).
  - Respect `prefers-reduced-motion` — disable non-essential animation when set.
- No decorative gradients, no parallax, no bouncing elements.

## 7. Voice & tone (UI copy)
- Plain, active verbs: "Confirm plan," not "Submit request."
- Errors state what happened and what to do next, in plain language — no
  apologetic filler, no blame.
- Empty states are an invitation to act: e.g. "No trip yet — tell me where
  you're headed."

## 8. Implementation notes (Astro + Tailwind)
- All color tokens in §2 go into `tailwind.config.mjs` under `theme.extend.colors`
  using the same names (`horizon`, `stamp`, `brass`, `ink`, `paper`, `signal`).
- Fraunces and JetBrains Mono are not system fonts — load them via
  `@fontsource/fraunces` and `@fontsource/jetbrains-mono` (self-hosted, no
  external font CDN call) and register them in `tailwind.config.mjs`
  `theme.extend.fontFamily`.
- Static content (itinerary day cards, hero/empty states) should be plain
  `.astro` components with Tailwind classes — zero JS shipped.
- Only `ChatPanel` and `AgentTrace` need `client:load` (or `client:visible`
  for the trace panel, since it's often below the fold) — every other
  component should ship zero JavaScript.
