# Native Knowledge UI + Documents-from-Vertex — Plan

Two related follow-ups to the Knowledge nav shell (patch 0009).

## A. Native Chatwoot-styled Knowledge UI (replace the iframe)
**Problem:** the Knowledge Manager is a standalone HTML **iframe** with its own CSS, so it
can't inherit Chatwoot's design system and always looks bolted-on. A stopgap recolour
(Chatwoot blue) is applied to `apps/knowledge-manager/index.html`, but the real fix is native.

**Approach:** make `ProtonKnowledgeHost.vue`'s **FAQs** section render a **native Vue list/table**
(Chatwoot components + `n-*` design tokens) that calls `/kb/faq` directly (GET/POST/PUT/DELETE
with `x-api-key` from `useProtonConfig`), instead of iframing the static app. Reuse Chatwoot's
table/button/modal components so it matches settings pages exactly.
- New: `app/javascript/dashboard/components/proton/KnowledgeFaqs.vue` (native list + create/edit modal).
- `ProtonKnowledgeHost.vue`: render `<KnowledgeFaqs>` for `section==='faqs'` (drop the iframe).
- The static `apps/knowledge-manager` app can stay for the dashboard-app entry, or be retired.
- CORS already allows the crm origin for `/kb/*` (Task 3 + `.localhost` origins).

## B. Documents page — show Vertex Search content in the CRM
**Goal:** the **Documents** sub-page lists what's indexed in the tenant's Vertex AI Search data
store (the bulk KB), read-only first, so agents/admins see the grounding corpus.

**Backend (`proton-conversational-ai`):**
- New `GET /kb/documents` in a small router (auth: `proton_backend_key`, CORS like `/kb/*`).
  Lists documents from the Vertex Search data store via Discovery Engine
  `DocumentServiceClient.list_documents(parent=<datastore branch>)` — NOT the search serving
  config. Parent = `projects/{vertex_search_project_id}/locations/{vertex_search_location}/collections/default_collection/dataStores/{vertex_search_data_store_id}/branches/default_branch`.
  Return `[{id, title, uri, snippet}]` (map from `document.derived_struct_data` / `struct_data`;
  fields vary by ingestion — confirm against the live data store shape).
  Never raises; `[]` on failure (mirrors the read-only clients).
- Wire into `main.py` + `run_assist_local.py`; add unit tests (mock the Discovery Engine client).
- Reuses existing settings: `vertex_search_project_id/location/data_store_id`.

**Frontend (patch, or fold into KnowledgeFaqs work):**
- `ProtonKnowledgeHost.vue` `documents` section → native Vue list calling `{backendUrl}/kb/documents`,
  columns Title / URI (link) / snippet. Read-only v1 (upload/delete = later).

**Open:** the Vertex data-store document schema depends on how it was ingested (website crawl vs
structured) — confirm `list_documents` field mapping against the real `proton-kb` data store
before finalizing the response shape.

## Sequencing
A (native FAQs UI) and B (Documents backend + view) are independent; do A first (fixes the
styling complaint end-to-end), then B (new capability). Each is its own focused build with tests
+ image rebuild + browser smoke.
