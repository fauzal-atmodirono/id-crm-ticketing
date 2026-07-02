# Wah Chan Reuse Analysis — Proton as Baseline

**Date:** 2026-06-30
**Decision:** Use `proton-conversational-ai` as the baseline for the Wah Chan
customer (WhatsApp channel + Zendesk CRM + Vertex AI Search knowledge base),
and integrate the Wah Chan knowledge base.

---

## TL;DR

Proton is the right baseline. It already ships **all three pillars** this
customer wants, live-tested:

| Customer need | Proton status |
|---|---|
| **Zendesk CRM** | ✅ Live-tested adapter (`zendesk.py` — ticketing + email + CSAT) |
| **WhatsApp** | ✅ Twilio + Sunshine Conversations adapters present |
| **Vertex AI Search KB** | ✅ First-class `knowledge_provider` |

Bonus already-present capabilities that the Wah Chan PRD required as hard
constraints:

- **Multilingual EN / Bahasa Melayu / Chinese** — `Language = Literal["en","ms","zh"]`,
  even encourages natural Manglish code-switching.
- **Graceful handoff** — `emit_handoff_tool`, `help_request`, detection gate.

Choosing proton over the `wah-chan-chatbot` repo trades wah-chan's nicer
dual-provider WhatsApp for **a working Zendesk integration + multilingual +
handoff you'd otherwise have to build/port.** For a "WhatsApp + Zendesk + KB"
customer, that's the correct trade.

---

## The two repos compared

### `wah-chan-chatbot` (the other repo — NOT chosen)

A separate, more built-out POC for Wah Chan (Devoteam engagement). Same
`.agents/` framework, feature-sliced architecture.

- **Stack:** FastAPI + WebSockets, Vue 3 + Pinia, Gemini 3.5 Flash on Vertex AI,
  Vertex AI Search, async SQLite, `uv`, Google ADK.
- **Backend slices:** `orchestrator` (lang detect, sentiment, FAQ, handoff,
  summarizer, vision), `knowledge` (Vertex AI Search + PDF loader + embeddings +
  CLI reindex), `whatsapp` (**Twilio AND Meta Cloud** — webhooks, media,
  templates, 24h-window policy), `chatwoot` (CRM bridge), `agent_console`
  (escalation inbox over WebSocket), `supervisor` (analytics + audit reports),
  plus `ingest`/`sessions`/`audit`/`outlets`/`scraper`.
- **CRM:** **Chatwoot**, not Zendesk. Handoff otherwise goes to an in-app Agent
  Console over WebSocket.
- **Verdict:** Better WhatsApp (dual provider) but **no Zendesk** — would need
  the Zendesk slice built from scratch.

### `proton-conversational-ai` (CHOSEN baseline)

- **Stack:** FastAPI (Google ADK over Gemini) + Vue 3 (Vite + Pinia + TS).
- **Ports/adapters (hexagonal):** `ChatPort`, `TicketingPort`, `KnowledgePort`,
  `TextToSpeechPort` in `ports.py`; concrete adapter chosen by env config + DI
  in `main.py`.
- **Adapters present:** `zendesk.py` (15 KB, live-tested), `chatwoot_zammad.py`,
  `sunshine_conversations.py`, `twilio_channel.py`, `vertex_search.py`,
  `gcp_voice.py`, `mock.py`, BigQuery metrics, Firestore sessions.
- **Channels:** web chat, browser voice, phone (Twilio Media Streams), email
  (Zendesk), WhatsApp (Twilio + Sunshine) — each independently env-gated.

---

## How proton maps to the Wah Chan stack

| Customer need | Status in proton | Work required |
|---|---|---|
| **Vertex AI Search KB** | ✅ `knowledge_provider=vertex_search` (`knowledge/vertex_search.py`) | Point IDs at the Wah Chan data store + verify GCP access. **No code.** |
| **WhatsApp** | ✅ Twilio + Sunshine adapters; gated by env (empty creds → inert) | Fill `.env` creds, provision number. **No code.** |
| **Zendesk CRM** | ✅ Live-tested `zendesk.py` | Point env at the Wah Chan Zendesk account/instance. **No code** (but see separation question below). |
| **EN / BM / ZH** | ✅ Already supported | — |
| **Handoff** | ✅ `emit_handoff_tool` + detection gate | Tune triggers/categories for retail. |

---

## "Integrate the Wah Chan KB" — what it actually involves

### The easy 20% — config, not code

The KB is already in Vertex AI Search, so the swap is a handful of env vars:

```bash
KNOWLEDGE_PROVIDER=vertex_search
VERTEX_SEARCH_PROJECT_ID=<wah-chan-gcp-project>
VERTEX_SEARCH_DATA_STORE_ID=<wah-chan-data-store>
VERTEX_SEARCH_ENGINE_ID=<wah-chan-engine>
VERTEX_SEARCH_LOCATION=<global | asia-southeast1 | ...>
```

The `VertexAISearchAdapter` builds its serving path purely from settings, so a
new data store = a new KB. (Config keys: `config.py` lines 30–33.)

### The harder 80% — the domain layer is hardcoded to Proton

The KB is only the *retrieval source*. The bot's **identity and tools are
Proton-specific** (a car dealer). This is the real work:

1. **Agent persona / prompt** — `features/chat/prompts.py` (`AGENT_INSTRUCTION`)
   is written for Proton vehicles. Rewrite for Wah Chan (Malaysian jewelry /
   retail), including the grounding rule and the out-of-domain apology line.
2. **Domain tools** — `agents.py` has Proton-specific tools: `book_test_drive`,
   the **car-model carousel**, `classify_ticket` with vehicle categories
   (Saga/Persona/X50…). Wah Chan doesn't need these — remove or replace with
   retail-appropriate tools (or none).
3. **Detection / handoff tuning** — proton's ticket-open gate and categories
   assume the car domain; review against Wah Chan's triggers (negative
   sentiment / "human"/"manager" / 2 retries).
4. **Frontend persona** — any Proton branding/copy in the Vue app.

---

## Recommendation

Treat proton as **"keep the engine, swap the domain layer":**

- **Config:** point Vertex Search + Zendesk + WhatsApp env at Wah Chan's accounts.
- **Rewrite:** `prompts.py` persona + grounding line, and the Proton-specific
  tools in `agents.py`.
- **Turn off** out-of-scope channels (voice/phone/email webhooks — just don't
  wire them; metrics optional).

### Reuse model: lean **template fork**

Earlier the lean was config-only (one shared codebase, flags). For a *different
company with a different persona*, prefer a **template fork** (clean per-customer
repo): the prompt and tools genuinely diverge, and you don't want Wah Chan's
jewelry persona living next to Proton's car persona behind a flag.

---

## Open decisions

1. **Reuse model:** template fork (recommended) vs. config-only shared codebase.
2. **Zendesk separation:** Proton and Wah Chan must NOT share one Zendesk
   instance/account — see the separate Zendesk-separation note.
3. **Handoff target:** Zendesk ticket vs. in-app console (or both).
4. **Build order:** KB/config first (boot against Wah Chan KB), then domain-layer
   rewrite (persona + tools).
