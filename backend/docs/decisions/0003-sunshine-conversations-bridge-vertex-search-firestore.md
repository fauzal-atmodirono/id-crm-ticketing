# 0003. Sunshine Conversations bridge, Vertex AI Search KB, Firestore handoff store

**Date:** 2026-06-19
**Status:** Accepted
**Builds on:** [0002](0002-monorepo-vue-frontend-gemini-audio.md) — keeps the Vue + FastAPI shape, replaces the Zendesk Web Widget with a server-side relay.

## Context

After ADR-0002 the agent ran end-to-end through Gemini for both text and voice, but three real gaps surfaced as we wired the demo to a live Zendesk instance:

1. **The customer experience was fragmented at handoff.** The Vue UI talked to our FastAPI for the AI turn; the Zendesk Messenger Web Widget was embedded as a separate `<script>` panel for the human turn. On escalation the customer saw two chat surfaces simultaneously: the (now-paused) AI panel on the left, a fresh "Hi there, what's your name?" Messenger panel on the right. The Zendesk-side ticket carried the AI transcript; the Messenger conversation didn't. Two unlinked records, two histories, no continuity.
2. **The Knowledge Base was a stub.** `InMemoryKnowledgeAdapter` had three placeholder articles and no plan for grounding. The agent's prompt instructed it to call `search_kb_tool` but the tool always returned the same hard-coded sentences. There was nothing actually Proton-specific in the agent's answers.
3. **Active handoffs evaporated on backend restart.** The `HandoffBridge` map of `session_id → conversation_id` lived in a Python dict. Every uvicorn reload — or a real deploy roll — wiped every in-flight live chat. The next customer message would be routed back to Gemini, which had already been paused for that session.

Each gap is small individually; collectively they're the difference between a polished demo and an architecture that scales to a real support team.

## Decision

Three coupled changes, landed together because they share the bootstrap wiring and `.env`:

### 1. Server-side Sunshine Conversations bridge — replace the embedded Zendesk widget

Zendesk Messaging is built on Sunshine Conversations. The same `ZENDESK_APP_ID` / `ZENDESK_KEY_ID` / `ZENDESK_SECRET_KEY` that the widget uses can drive the SC REST API directly. We:

- Add `SunshineConversationsAdapter` (httpx, v2 REST) and a new `HumanAgentBridgePort` protocol. The adapter `open_handoff` creates the SC conversation, posts the AI summary + recent transcript as a `business`-authored message (visible to the agent in their dashboard), and returns the conversation id. `forward_customer_message` posts subsequent customer turns as `user`-authored messages with the session's external id.
- Add `HandoffBridge` — a small coordinator that maps `session_id ↔ conversation_id` and fans out agent messages to per-session `asyncio.Queue` subscribers.
- Add `GET /chat/stream/{session_id}` — a Server-Sent Events endpoint that streams `agent_message` events from the bridge with a 30 s heartbeat.
- Add `POST /webhooks/sunshine` — HMAC-SHA256-verified webhook ingest. We parse `conversation:message` events, skip customer echoes (we already showed those in our UI when the user hit Send), and publish business-authored messages to the matching session's queue.
- Frontend extends `ChatMessage.role` with `'agent'`, opens the EventSource on handoff (`live_chat_available: true`), and renders incoming agent messages inline in the same chat log as a purple-tinted card. The `<script id="ze-snippet">` and `plugins/zendesk.ts` helper are deleted; the `HandoffStatus.vue` full-panel swap is replaced by a thin "Connected to a human agent" banner above the log.

Net effect: one chat surface, three speakers (AI / customer / human agent), one continuous transcript visible to both sides.

### 2. Vertex AI Search KB grounding

We pick Vertex AI Search Discovery Engine (`proton-kb-engine` over `proton-kb` data store under `lv-playground-genai`) as the KB backend. Pipeline:

1. `scripts/scrape_proton.py` walks `https://www.proton.com/sitemap.xml`, downloads each page, and extracts a structured Document per URL:
   ```json
   {"id": "...", "struct_data": {"title": "...", "link": "...", "language": "en", "body_excerpt": "...", "sections": [...]}, "content": {"uri": "gs://...", "mime_type": "text/html"}}
   ```
   Output is JSONL (one Document per line), uploaded to GCS alongside the raw HTML.
2. Import is `data_schema="document"` so Vertex indexes our structured fields directly. (In-flight LROs that we accidentally started before this commit are stuck on the branch lock; the script falls back to per-document `CreateDocument` calls until they clear.)
3. `VertexAISearchAdapter` queries the engine and reads `struct_data` (our authoritative fields) ahead of `derived_struct_data` (where Vertex's auto-extracted `link` is the GCS URI, useless to the customer). Standard tier doesn't return snippets, so we surface the pre-extracted `body_excerpt` as the article content.
4. The agent prompt is tightened (`prompts.py`): three-step playbook (search → answer → optionally escalate), explicit `NO_MATCHES` branching, Markdown-link citations from `Source URL`, replies in the customer's language. `classify_ticket_tool` is dropped from the registered tool set because its state was never read downstream and Gemini was treating the classify call as the turn's terminal action — silently skipping the customer-facing text reply.

After this the agent answers concrete Proton questions ("Proton X70 starts at RM89,800, 5-year unlimited-mileage warranty, …") with inline citations to the source pages, and politely refuses out-of-scope questions without escalating to a human.

### 3. Firestore-backed handoff state

`HandoffBridge` gains a `HandoffStorePort` boundary. Implementations:

- `InMemoryHandoffStore` — dict-backed; default for tests and dev.
- `FirestoreHandoffStore` — one document per active handoff in `proton-db/handoff_sessions/<session_id>` with `{conversation_id, created_at}`. Reverse lookup is a `where("conversation_id", "==", …)` indexed query. The Firestore SDK is synchronous, so calls are dispatched via `asyncio.to_thread`.

The bridge keeps an in-process cache; `register` writes through to the store, lookups check cache first and fall back to the store on miss. SSE subscriber queues stay in memory — they can't be persisted, and clients reconnect after a restart anyway, refilling the cache from Firestore on first delivery.

## Consequences

### Positive

- **One UI per customer.** No more dual-panel fragmentation; agent replies arrive in the same chat log as AI replies, with distinct styling.
- **Grounded, cited answers.** The agent quotes real Proton content with Markdown links to the source pages. Hallucinations are bounded by the literal `NO_MATCHES` escalation gate.
- **Restart-safe handoffs.** A uvicorn reload or deploy roll preserves the `session_id ↔ conversation_id` mapping; the next customer message still routes to the agent's conversation.
- **The same env drives both layers.** Gemini, Vertex Search, Firestore, Sunshine Conversations all auth via ADC + the existing Zendesk credentials — no new secrets beyond the optional `SUNSHINE_WEBHOOK_SECRET`.
- **CRM ticket attribution fixed.** As a side fix, Zendesk Support tickets now declare a session-scoped `requester` instead of defaulting to the API token owner. Customer escalation emails stop arriving in the admin's inbox.

### Negative / trade-offs

- **Stuck Discovery Engine LROs.** Two pre-existing ImportDocuments operations on `branches/0` are wedged in "submitted but never progressed" state (`failureCount: 78, totalCount: 78` on one, no progress on the other). They block fresh `ImportDocuments` calls indefinitely; we fall back to per-document `CreateDocument`. The proper fix is to wait for Google to garbage-collect them, recreate the data store, or wait for the LROs to time out server-side.
- **Standard-tier search**. Without Enterprise edition we get no snippets / extractive segments. We mitigate by pre-extracting a ~1.5 kB body excerpt at ingest time and indexing it as a struct field — the agent has real content to ground replies in, but answer quality is bounded by our scraper's main-content extraction. The pages have a lot of navigation chrome that we don't perfectly strip.
- **In-memory subscribers.** SSE queues don't survive a restart. Open browsers reconnect their EventSources automatically, but a message delivered during the restart window is lost. Production fix: a Redis pub/sub or Pub/Sub backplane.
- **Sunshine webhook setup is manual.** The webhook secret + target URL must be registered in the SC dashboard one time. Without it, agent replies never reach the customer. There's no preflight check that warns when the bridge is misconfigured.
- **Customer identification is anonymous.** `identify` without a JWT is an unverified claim; Zendesk shows our `Proton AI Customer (sim-XXXX)` name to the agent but the Messaging widget's own pre-chat form (which we no longer render, but which would re-appear if we ever re-embedded the widget) would still ask for the name. Real customer attribution wants `loginUser` with a signed JWT.

### Rejected alternatives

- **Keep both UIs.** Smaller change, but the dual-record split persists — agents working the Support ticket can't see the Messaging conversation and vice versa.
- **Crawl the Proton site live from each tool call.** Cheap to build, expensive at runtime, fragile against site changes, and gives Gemini fresh HTML noise to digest on every request.
- **Index raw HTML via `data_schema="content"`.** Was our first attempt — required Enterprise tier to get useful snippets, lost control of titles / URLs, and the import LROs we kicked off this way are the ones now stuck.
- **Skip Firestore, persist the bridge state in Redis.** Redis would be marginally faster but adds an extra deploy-time dependency. Firestore is already first-class in our ADC story (same project, same auth, no extra config beyond a database id).
- **In-memory only with sticky sessions.** Works for a single backend instance but breaks the moment we scale horizontally or do a rolling deploy. Firestore was cheaper to add than the load-balancer sticky-session config.

## References

- `apps/backend/src/chatbot/features/chat/adapters/sunshine_conversations.py`
- `apps/backend/src/chatbot/features/chat/adapters/vertex_search.py`
- `apps/backend/src/chatbot/features/chat/adapters/handoff_store.py`
- `apps/backend/src/chatbot/features/chat/handoff_bridge.py`
- `apps/backend/src/chatbot/features/chat/router.py` — `chat_stream`, `sunshine_webhook`
- `apps/backend/scripts/scrape_proton.py`
- `apps/frontend/src/features/chat/store/chat.store.ts` — EventSource subscription
- `apps/frontend/src/features/chat/components/ChatLog.vue` — agent-role styling
