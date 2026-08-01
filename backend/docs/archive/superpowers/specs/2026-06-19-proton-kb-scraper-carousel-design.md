# Design: Rich Proton KB Scraper + Product Carousel

- **Date:** 2026-06-19
- **Status:** Approved (pre-implementation)
- **Author:** AI pair (brainstormed with repo owner)

## Problem

The assistant deflects detailed product questions with replies like:

> "I apologize, but I don't have detailed descriptions of each Proton model
> readily available in my knowledge base. However, you can find comprehensive
> information ... by downloading their brochures directly from the Proton website."

### Root cause (confirmed in code)

`apps/backend/src/chatbot/features/chat/adapters/vertex_search.py:73-77` documents that
the engine runs on **Standard tier**, which returns **no snippets / extractive
segments** — the agent only ever sees `struct_data`. Today `struct_data.body_excerpt`
is the first **1500 characters** of page text (`scripts/scrape_proton.py:115`), which
for model pages is mostly navigation chrome. Verified against the live X50 record in
`apps/backend/scraped_data/proton-kb.jsonl`: the excerpt opens with
`"Models Back MODELS Starting at RM89,800* ... TEST DRIVE REGISTER INTEREST ..."` and
runs out of budget before any real specs. The detailed specs live in **brochure PDFs**
that are never scraped, and the current scraper is **httpx-only** so it cannot see the
JS-rendered portions of model pages.

So the fix is not "scrape more pages" — it is: put **rich, clean, structured content
into `struct_data`**, render JS pages, and ingest brochure text — then add a product
**carousel** for product queries.

## Reference

The owner's CelcomDigi scraper at `/Users/yudaadipratama/Archive/celcomdigi-chatbot/scraper`
is the architectural template: hybrid httpx+Selenium transport, URL classifier,
per-page-type extractors, full-content indexing into Vertex AI Discovery Engine,
idempotent provisioning, composable `Source` protocol. We port these patterns into the
Proton repo, adapted for a car website (model pages, spec tables, images, brochures).

## Hard constraints

- **Do NOT touch the Zendesk integration.** Off-limits: `adapters/zendesk.py`,
  the ticketing port, and the handoff/escalation paths in `service.py`
  (`_escalate_handoff`, `_open_live_bridge`, the Sunshine bridge, `emit_handoff_tool`).
  All new work is **additive**.
- Stay on **Vertex Standard tier** (no extra GCP cost). Because Standard returns only
  `struct_data`, all content the agent must read — including **brochure PDF text** —
  is extracted locally and packed into `struct_data`.
- Existing data store / engine / GCS bucket are reused:
  project `lv-playground-genai`, data store `proton-kb`, engine `proton-kb-engine`,
  bucket `proton-kb-scraped-lv-playground-genai`.

## Decisions (locked)

| Decision | Choice |
|---|---|
| Effort scope | Rich text/specs **and** photos, end-to-end |
| Scraper architecture | Modular port of CelcomDigi into `apps/backend/src/scraper/` |
| Brochure PDFs | Ingest + **extract text locally** into `struct_data` (Standard tier) |
| Carousel protocol | Bespoke **A2UI-shaped** `product_carousel` block (not full A2UI yet) |
| Carousel rendering | New Vue `ProductCarousel.vue` (no DOMPurify `<img>` change needed) |
| Phasing | Phase 1 (data layer) → Phase 2 (carousel UI) |
| Vertex tier | Standard (unchanged) |

### Why bespoke carousel, not full A2UI

A2UI (Google's Agent-to-UI protocol) is the right long-term direction and is ADK-native
on the backend, but as of June 2026 it is **v1.0 release-candidate / early-stage public
preview** with **no Vue renderer** (official renderers: Lit/web-components, Flutter,
Angular; React on roadmap). Betting a production support chat on a preview protocol with
no native Vue support is too risky now. We implement a tiny bespoke `product_carousel`
JSON block, shaped to mirror A2UI concepts (component + data model), so a later migration
to real A2UI is a contained change.

---

## Phase 1 — Data layer (removes the apology on its own)

### New package: `apps/backend/src/scraper/`

Mirrors CelcomDigi's structure. Replaces the single-file `scripts/scrape_proton.py`
(kept until parity is proven, then removed).

| Module | Responsibility |
|---|---|
| `config.py` | Settings via `pydantic-settings` (`SCRAPER_*`): project, data store, engine, bucket, sitemap URL, delay, headless, allow/deny prefixes. |
| `models.py` | `ScrapedDoc` dataclass — the canonical record (see schema below). |
| `transport.py` | Hybrid fetch: httpx first; **Selenium (headless Chrome) fallback** when `<main>`/primary content is empty (Proton model pages are JS-heavy). Single `asyncio.Lock` serializes the shared Selenium driver. Politeness delay + retry. |
| `sitemap.py` | Fetch `proton.com/sitemap.xml`; keep `/models/*`, `/after-sales/*`, offers; drop noise (legal, thank-you, duplicate locales beyond `en`). |
| `classifier.py` | URL → page type: `model`, `after_sales`, `offer`, `generic`. |
| `extractors/model.py` | Per-model: clean description, `price`, variant/spec table (serialized), **image URLs**, **brochure PDF link**, section headings. |
| `extractors/generic.py` | Clean main-content text for non-model pages (selector-based chrome stripping). |
| `pdf.py` | Download brochure PDFs; **extract text** (e.g. `pypdf`) into a string for `struct_data`. |
| `indexer.py` | Build Discovery Engine documents; upsert (try create, catch `AlreadyExists`, update); UTF-8-safe truncation at the struct_data size cap. |
| `provisioner.py` | Idempotent data store/engine creation (reuse existing). |
| `main.py` | CLI: `scrape` / `provision` / `index` (Click or argparse). |
| `tests/` | pytest with saved HTML/PDF **fixtures** per page type; extractors are pure functions over fixtures — no network. |

### Chrome stripping (the core quality fix)

Selector-based main-content extraction (find `<main>` / primary content container,
`decompose()` nav/header/footer/script/style), **not** "first 1500 chars". Full cleaned
text goes into `struct_data` (not a tiny excerpt).

### Enriched `struct_data` schema (what the agent finally sees)

```json
{
  "title": "PROTON All-New X50",
  "link": "https://www.proton.com/models/all-new-x50",
  "source_type": "model",
  "language": "en",
  "body_excerpt": "<full cleaned description + spec text; large, struct_data-cap bound>",
  "price": "RM 89,800",
  "image_urls": ["https://.../x50-front.jpg", "https://.../x50-interior.jpg"],
  "brochure_url": "https://.../x50-brochure.pdf",
  "sections": ["Stylish Design", "Powerful Performance", "Outstanding Safety"]
}
```

- `body_excerpt` keeps its name for backward compatibility with `vertex_search.py`, but
  now carries the full cleaned text (bounded by Vertex's struct_data size limit; truncate
  UTF-8-safely).
- `source_type` enables Phase 2 to filter product/model docs.
- Brochure docs are separate records with `source_type: "brochure"` whose `body_excerpt`
  holds the **locally extracted PDF text** (Standard tier cannot read PDF content itself).

### Brochure PDF flow

Model extractor discovers PDF links → `pdf.py` downloads + extracts text →
indexed as separate `source_type: "brochure"` documents with extracted text in
`struct_data.body_excerpt` and `link` = the public PDF URL (so the agent can still cite
the downloadable brochure).

### Vertex search adapter — minimal change

`vertex_search.py` already prefers `struct_data` and reads `body_excerpt`. It needs only
to **pass through the new fields** (`price`, `image_urls`, `brochure_url`, `source_type`)
on the returned object so Phase 2 can use them. To stay additive and avoid touching the
`KbArticle` contract used elsewhere, add the extra fields as **optional** on `KbArticle`
(all defaulting to `None`/empty) so existing callers are unaffected.

### Phase 1 acceptance

- Asking "tell me about the X50" yields a substantive, grounded description with specs
  and a citation — **no apology**.
- Brochure-only specs (e.g. boot capacity) are answerable.
- All existing backend tests still pass; Zendesk paths untouched.

---

## Phase 2 — Product carousel (additive)

### Backend (follows the existing `emit_handoff_tool` state pattern exactly)

- **New agent tool** `show_models_tool(query)` in `agents.py`: queries Vertex filtered to
  `source_type == "model"`, maps each hit's `struct_data` → a product card, stores the
  list in `tool_context.state["product_carousel"]`, and returns a short internal
  confirmation string (same shape as `emit_handoff_tool`).
- **`models.py`**: new `ProductCard{title, image_url, description, price, url}`; add
  `products: list[ProductCard] = field(default_factory=list)` to `TurnResult`.
  (`MediaAttachment` exists but is too thin; `ProductCard` is purpose-built.)
- **`service.py`**: after the runner loop, read `session_state.get("product_carousel")`
  in the **same block** that already reads `handoff_triggered`, and populate
  `TurnResult.products`. No changes to handoff/Zendesk logic.
- **`router.py`**: `ChatTurnResponse` gains `products: list[dict[str, Any]] = []`; map
  from `TurnResult.products` in `chat_turn`.
- **`prompts.py`**: add an instruction — when the user asks to see/compare models or asks
  which car to buy, call `show_models_tool` and write a brief intro line; the cards render
  below. Leaves all escalation rules intact.

### Frontend (matches existing scoped-CSS + design-token patterns)

- **`features/chat/types/index.ts`**: `ChatMessage` gains optional
  `products?: ProductCard[]`; add a `ProductCard` interface mirroring the backend.
- **`features/chat/api/chat.api.ts`** / response type: include `products`.
- **`features/chat/store/chat.store.ts`**: read `result.products`; attach to the pushed
  assistant message.
- **New `features/chat/components/ProductCarousel.vue`**: horizontal-scroll row of cards
  (image, title, price, "Learn more" link to `url`). Uses `var(--surface)`,
  `var(--radius-md)`, `var(--space-*)`, scoped CSS. Lazy-load images; graceful fallback
  when an image fails.
- **`features/chat/components/ChatLog.vue`**: render `<ProductCarousel :items="msg.products">`
  below the assistant text when `msg.products?.length`. Text path (marked + DOMPurify)
  unchanged; images live inside the carousel component, so **no DOMPurify `<img>` change
  is required**.

### `product_carousel` block shape (A2UI-shaped, bespoke)

```json
{
  "type": "product_carousel",
  "items": [
    {
      "title": "PROTON All-New X50",
      "image_url": "https://.../x50-front.jpg",
      "description": "Compact SUV ... ",
      "price": "RM 89,800",
      "url": "https://www.proton.com/models/all-new-x50"
    }
  ]
}
```

Transported as the `products` array on the turn response. The `type` discriminator and
component+data separation keep it migratable to real A2UI later.

### Phase 2 acceptance

- "Show me your SUVs" / "which Proton should I buy?" returns a short intro line plus an
  inline carousel of model cards (photo + price + link).
- Non-product questions are unchanged (no carousel, plain grounded text).
- `vue-tsc` passes; `ProductCarousel.vue` has a render test; Zendesk paths untouched.

---

## Testing strategy

- **Scraper:** pytest over saved HTML/PDF fixtures; extractors and classifier are pure
  functions (no network). Transport's Selenium fallback decision unit-tested via a fake
  "empty main" page.
- **Backend:** unit-test `show_models_tool` mapping and the `service.py` carousel wiring
  (mirror `test_vertex_search.py`); assert handoff/Zendesk behavior is unaffected.
- **Frontend:** `vue-tsc` strict + a component render test for `ProductCarousel.vue`.

## Out of scope / deferred

- Full A2UI adoption (revisit when a Vue renderer exists and the protocol stabilizes).
- Enterprise-tier Vertex (snippets/extractive answers).
- Any change to voice flow beyond what it inherits for free (carousel is chat-only;
  `handle_voice_turn` is not modified).
- Image hosting/CDN — we reference Proton's own image URLs, not re-host.

## Risks

- **Selenium in CI/headless env**: needs Chrome + driver; gate behind the transport
  fallback and document local setup.
- **struct_data size cap**: full text may exceed limits; truncate UTF-8-safely and prefer
  description+specs over boilerplate.
- **Proton markup drift**: extractors are selector-based; fixtures make breakage visible
  and fixable in isolation.
- **PDF text quality**: scanned/image PDFs extract poorly; fall back to citing the
  brochure link.
