# pgvector Knowledge Base + No-Code Ingestion — Design

**Date:** 2026-07-26
**Status:** Approved design (pre-implementation)
**Scope:** Subsystems "A + B" of the no-code configuration effort
(see `2026-07-26-no-code-config-roadmap.md` for the full backlog).

> **REVISION (2026-07-26, discovered mid-implementation):** The product ALREADY
> has (a) a backend `GET /kb/documents` serving the read-only Vertex corpus
> listing, and (b) a native Knowledge section in the Chatwoot SPA fork
> (`KnowledgeDocuments.vue` read-only listing + `KnowledgeFaqs.vue` CRUD +
> Assistants, patches 0011/0012). The new pgvector store must COEXIST with these,
> not replace them. Two decisions updated accordingly:
> - **Route/file:** the new operator-authored documents use `/kb/knowledge`
>   (`kb_knowledge_router.py`), leaving the Vertex `/kb/documents` listing
>   untouched. The merged knowledge layer still merges pgvector + Live FAQ +
>   Vertex at query time.
> - **UI:** delivered as a NEW native SPA view in the existing Knowledge section
>   (matching `KnowledgeFaqs.vue`, via the fork patch workflow), NOT a standalone
>   Chatwoot dashboard app.
> Everything else in this design (store, embedder, ingestion, data model,
> retrieval, rollout) is unchanged. Sections below that say `/kb/documents` or
> "dashboard app" are superseded by this banner.

## Problem

The end users of this platform are non-technical business operators. Today,
grounding the chatbot in product knowledge requires either editing short Q/A
pairs in the Firestore-backed Live FAQ store, or running a GCP-console-heavy
Vertex AI Search setup (create a datastore, an engine, a GCS bucket, run a
scraper CLI). Neither lets an operator simply add a document and have the bot
use it, and both depend on Google infrastructure.

## Goal

Let a non-technical operator create and connect knowledge to the chatbot from
within the product — paste an article or upload a file — with retrieval served
from the platform's own Postgres via `pgvector`. No GCP console, no CLI.

## Non-goals (deferred to the roadmap)

- Provider picker / choosing the knowledge backend from the UI (subsystem C).
- Bring-your-own-GCP Service Account credential entry (subsystem C).
- Self-hosted embedding model on the VM (subsystem D).
- URL / website-crawl ingestion.
- A knowledge-source selector setting (enable/disable/precedence per source).
- Migrating the existing Live FAQ store off Firestore.

## Decisions (from brainstorming)

| Decision | Choice |
|----------|--------|
| Store | `pgvector` in the backend's per-tenant Postgres |
| Relationship to Live FAQ | **Coexist** — merged at query time; nothing migrated |
| Embedder | Vertex `text-embedding-004` (existing platform SA; operator touches no GCP) |
| Ingestion modes | Paste text + file upload (PDF/DOCX/MD/TXT); no crawling |
| Which service owns pgvector | **Backend** (where `KnowledgePort`/embedder/copilot already live) |
| UI | New Chatwoot dashboard app extending the `chatwoot-faq-admin` pattern |
| Rollout | Ships in the **default tenant template**; enabled on the **VM for proton only** (default-off flag) |

## Architecture

The backend gains an async-SQLAlchemy Postgres connection to a per-tenant DB
with the `pgvector` extension, mirroring the agent service's `db/` setup
(`_to_async_url`, `Base.metadata.create_all`, no Alembic — matching repo
convention). All knowledge concerns stay in the backend.

```
Operator (in Chatwoot) ──> chatwoot-knowledge dashboard app
                                │ x-api-key (faq_admin_api_key)
                                ▼
      Backend  POST/GET/DELETE /kb/documents ──> ingestion (background task)
                                │                    extract → chunk → embed → insert
                                ▼
            Postgres (backend_<tenant>, pgvector): kb_documents, kb_chunks
                                ▲
      copilot search_knowledge_base ──> MergedKnowledgeAdapter
                                           ├─ PgVectorKnowledgeAdapter   (NEW)
                                           ├─ LiveFaqAdapter             (existing, kept)
                                           └─ Vertex Search              (untouched, optional)
```

### Components

- **`PgVectorKnowledgeAdapter(KnowledgePort)`** — new. Implements
  `search_kb(query, limit)`: embed the query once, run a cosine-distance search,
  return `KbArticle` results. The one unit responsible for vector retrieval.
- **Ingestion service** — new. Extract → chunk → embed → persist, run as a
  FastAPI background task. Owns text extraction and chunking.
- **`MergedKnowledgeAdapter`** — existing; extended to include the pgvector
  adapter alongside Live FAQ (and Vertex Search when configured).
- **Backend `db/` module** — new. Async engine/session + models, patterned on
  `agent/app/db/`.
- **`chatwoot-knowledge` dashboard app** — new static-HTML+JS app, patterned on
  `backend/apps/chatwoot-faq-admin/`.

## Data model (backend, per-tenant Postgres)

No `tenant_id` column — the database is already per-tenant.

**`kb_documents`**
- `id` (uuid, pk)
- `title` (text)
- `source_type` (`text` | `file`)
- `original_filename` (text, nullable)
- `mime_type` (text, nullable)
- `char_count` (int)
- `status` (`pending` | `indexed` | `failed`)
- `error` (text, nullable)
- `created_at`, `updated_at` (timestamptz)

**`kb_chunks`**
- `id` (uuid, pk)
- `document_id` (uuid, fk → `kb_documents`, `ON DELETE CASCADE`)
- `chunk_index` (int)
- `content` (text)
- `embedding` (`vector(768)` — text-embedding-004 dimensionality)
- `char_count` (int)
- `created_at` (timestamptz)
- Index: HNSW on `embedding` using `vector_cosine_ops`.

## Ingestion flow (no-code)

1. Operator, in the Knowledge dashboard app, either **pastes text** (title +
   body) or **uploads a file** (PDF/DOCX/MD/TXT).
2. `POST /kb/documents` — JSON for text, multipart for file; guarded by
   `faq_admin_api_key` (constant-time compare, reusing existing auth). Inserts a
   `kb_documents` row with `status=pending` and returns its id **immediately**
   (fast-return, like the webhook receivers).
3. **Background task**: extract text (pypdf for PDF, python-docx for DOCX, plain
   read for MD/TXT) → normalize whitespace → chunk (~800 tokens, ~100 overlap,
   sentence-aware) → embed each chunk via the existing `VertexEmbedder` (batched)
   → insert `kb_chunks` → set `status=indexed`. On any failure: `status=failed`,
   store `error`, log, and **do not raise** (matches the background-task
   invariant).
4. The UI polls `GET /kb/documents` and shows a status badge until `indexed`.

**Endpoints**
- `POST /kb/documents` — create from text or file (returns id, `pending`).
- `GET /kb/documents` — list with status + chunk counts.
- `GET /kb/documents/{id}` — detail.
- `DELETE /kb/documents/{id}` — delete document + chunks (cascade).

## Retrieval flow

The copilot's `search_knowledge_base` tool calls
`MergedKnowledgeAdapter.search_kb(query, limit)`:

1. Embed the query once.
2. pgvector: `SELECT ... ORDER BY embedding <=> :q LIMIT k`, returning chunk
   text + parent document title.
3. Live FAQ queried as today (unchanged).
4. Merge candidates by cosine score, apply a score floor (start at the Live FAQ
   floor of 0.55, tune during implementation), dedupe, return top-N as the
   existing `KbArticle` shape (`title` = document title, `snippet` = chunk text,
   `link` = none for now).

**Fail-open:** if the pgvector query errors, log and return nothing *from that
source*; Live FAQ results still flow and the copilot's existing fail-open covers
the rest. Knowledge retrieval never breaks a reply.

## UI — `chatwoot-knowledge` dashboard app

Extends the `chatwoot-faq-admin` static-HTML+JS pattern; auth via `x-api-key`
= `faq_admin_api_key`; registered under Chatwoot → Settings → Integrations →
Dashboard Apps.

- **Content view** (this build): list documents (title, type, **status badge**,
  chunk count, date); "Add text" form (title + body); "Upload file" (drag-drop
  PDF/DOCX/MD/TXT); delete. Auto-refresh while any document is `pending`.
- **Config view** (the "settings sub-menu"): **deferred** — future home of the
  source-selector / provider picker (roadmap).

## Configuration & deployment

**New backend settings** (`platform/config.py` + `deploy/tenants/example.env`):
- `knowledge_pg_enabled` (bool, default `false`) — feature flag; wires the
  pgvector adapter into the merged knowledge layer when true.
- `knowledge_database_url` — the backend's Postgres connection.
- `kb_chunk_size_tokens` (default ~800), `kb_chunk_overlap_tokens` (default ~100)
  — env-tunable, not surfaced in the UI.
- Reuse existing `embedding_model` (`text-embedding-004`) and `faq_admin_api_key`.

**Infrastructure**
- Swap the shared Postgres image to a pgvector-enabled build (e.g.
  `pgvector/pgvector:pg16`) in the infra compose.
- `add-tenant.sh` gains: create `backend_<tenant>` DB, run
  `CREATE EXTENSION IF NOT EXISTS vector`, and set `KNOWLEDGE_DATABASE_URL` +
  `KNOWLEDGE_PG_ENABLED` in the tenant env.

**Rollout**
- The feature ships in the **default tenant template** so any tenant can enable
  it, but is **default-off** (`knowledge_pg_enabled=false`).
- Initial VM rollout is **proton only**: enable the flag and provision the
  backend DB + extension for the proton tenant; other tenants (incl. `default`)
  keep it off until explicitly enabled.

## Error handling

Fail-open throughout, matching the codebase's ethos:
- Ingestion failures are per-document (`failed` status, visible and deletable),
  never crash the service.
- Unsupported file type or empty extraction → `failed` with a clear message.
- Retrieval degrades gracefully to the other knowledge sources.
- Embedding-API failure: during ingest → mark `failed`; during query → fall back
  to other sources.

## Testing

- **Unit (existing sqlite/respx style):** chunking logic; per-format text
  extraction (fixtures for PDF/DOCX/MD/TXT); merge/ranking with a fake embedder
  and fake store; ingestion endpoint behavior with respx-stubbed embeddings.
- **Integration (new):** vector search is Postgres-specific (sqlite has no
  `vector` type), so add **one** Postgres-backed integration test for the
  similarity query (CI Postgres service or testcontainers). Everything else
  stays on the existing sqlite suite.

## Open implementation details (decide during planning)

- Exact chunker (token counting via the embedding model's tokenizer vs. a simple
  heuristic).
- HNSW vs. IVFFlat index parameters and when to build the index (small corpora
  may not need an ANN index at all).
- Whether to batch-embed chunks in one API call or stream them.
