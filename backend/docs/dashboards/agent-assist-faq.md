# Agent-Assist FAQ — Endpoint & Dashboard Reference

This document covers the two agent-assist FAQ endpoints (`GET /kb/suggest` and `POST /kb/feedback`), the BigQuery `v_faq_quality` quality view, the Zendesk sidebar app, and the relevant backend configuration keys.

---

## Endpoints

### GET /kb/suggest

Real-time knowledge-base suggestion lookup for human agents. Reuses the same `KnowledgePort` that the AI conversation pipeline uses for grounding — no additional data source or index is required.

| Detail | Value |
|---|---|
| Method / Path | `GET /kb/suggest` |
| Auth | None (POC-open — see Deferred section) |
| Tags | `kb` |

**Query parameters**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `q` | string | `""` | Keyword or phrase to search. Empty string returns `{"query": "", "suggestions": []}` immediately. |
| `limit` | integer | `3` | Maximum number of suggestions to return. |

**Response (200)**

```json
{
  "query": "battery drain",
  "suggestions": [
    {
      "title": "Troubleshoot Battery Drain",
      "snippet": "If your device is losing charge faster than expected, start by checking ...",
      "url": "https://help.example.com/articles/42",
      "source_type": "zendesk_help_center"
    }
  ]
}
```

- `snippet` is truncated to 280 characters.
- `source_type` reflects the `KnowledgePort` implementation in use (`in_memory`, `zendesk_help_center`, `vertex_search`, etc.).
- An empty `suggestions` array is a valid response when no articles match the query.

**KnowledgePort reuse**

The router is constructed with `build_kb_suggest_router(knowledge_port)` in `bootstrap_application` and delegates directly to `knowledge_port.search_kb(query, limit)`. Swapping the knowledge provider (e.g., from `in_memory` to `vertex_search`) automatically affects this endpoint with no code change.

---

### POST /kb/feedback

Records an agent's explicit quality rating for a specific FAQ article. Auth-gated via `x-api-key`.

| Detail | Value |
|---|---|
| Method / Path | `POST /kb/feedback` |
| Auth | `x-api-key` header must equal `settings.qa_api_key` (constant-time compare via `hmac.compare_digest`) |
| Tags | `kb` |

**Request body**

```json
{
  "article_id": "Troubleshoot Battery Drain",
  "session_id": "session_abc123",
  "helpful": true,
  "score": 5
}
```

| Field | Type | Constraints | Description |
|---|---|---|---|
| `article_id` | string | required | Identifier for the article (title or ID). |
| `session_id` | string | optional, default `""` | Conversation or page-load session ID for correlation. |
| `helpful` | boolean | required | Whether the article was useful. |
| `score` | integer | 1–5 (inclusive) | Likert-scale quality rating. |

**Responses**

| Status | Meaning |
|---|---|
| 200 | `{"status": "ok"}` — feedback accepted. |
| 401 | Missing or incorrect `x-api-key`. |
| 422 | Malformed request body (score out of range, missing required fields). |

**Auth header**

```
x-api-key: <value of settings.qa_api_key>
```

If `qa_api_key` is not configured (empty string), every request returns 401 and no feedback is stored. Configure the key in your `.env` before using this endpoint in production.

---

## BigQuery Quality View — `v_faq_quality`

Defined in `apps/backend/src/chatbot/features/metrics/faq_schema.py` via `faq_view_ddls(project, dataset)`.

```sql
CREATE OR REPLACE VIEW `<project>.<dataset>.v_faq_quality` AS
SELECT
  article_id,
  COUNT(*)                             AS feedback_count,
  AVG(score)                           AS avg_score,
  SAFE_DIVIDE(COUNTIF(helpful), COUNT(*)) AS helpful_rate
FROM `<project>.<dataset>.faq_feedback`
GROUP BY article_id
```

The underlying `faq_feedback` table schema:

| Column | BQ Type | Notes |
|---|---|---|
| `article_id` | STRING (REQUIRED) | Article title or ID as submitted by the agent. |
| `session_id` | STRING | Optional; correlates with conversation session. |
| `helpful` | BOOL | Agent's binary rating. |
| `score` | INT64 | 1–5. |
| `at` | TIMESTAMP | UTC timestamp of the feedback event. |

---

## Zendesk Sidebar App

A thin Zendesk App Framework (ZAF) v2 sidebar widget that wires the two endpoints above into the agent's ticket view.

Source: `apps/zendesk-agent-app/` — see [README](../../apps/zendesk-agent-app/README.md) for full installation and configuration steps.

**What it does**

1. Reads the ticket subject and latest comment as the search query.
2. Calls `GET {backendBaseUrl}/kb/suggest?q=...&limit=5`.
3. Renders each suggestion (title, snippet, source type) with two actions:
   - **Insert** — appends the article title and URL to the ticket comment via `client.invoke('ticket.comment.appendText', ...)`.
   - **Thumbs up / down** — sends `POST /kb/feedback` with `helpful: true/false` and `score: 5/1`.

**App parameters** (configured in Zendesk Admin Center after upload)

| Parameter | Description |
|---|---|
| `backendBaseUrl` | Base URL of the Proton backend (e.g., `https://api.example.com`). |
| `apiKey` | Value of `qa_api_key`; passed as `x-api-key` header to `/kb/feedback`. Leave blank to skip feedback auth (not recommended in production). |

---

## Backend Configuration

All keys are read from environment variables / `.env` via `apps/backend/src/chatbot/platform/config.py`.

| Key | Default | Description |
|---|---|---|
| `metrics_provider` | `"noop"` | Set to `"bigquery"` to activate the BigQuery FAQ-feedback adapter. When `"noop"`, feedback calls are silently discarded. |
| `bigquery_faq_feedback_table` | `"faq_feedback"` | BigQuery table name (within `bigquery_dataset`) for raw feedback rows. |
| `bigquery_project_id` | `"lv-playground-genai"` | GCP project ID for BigQuery writes. |
| `bigquery_dataset` | `"demo_proton"` | BigQuery dataset containing the feedback table and `v_faq_quality` view. |
| `qa_api_key` | `""` (empty) | Shared secret required in `x-api-key` for `POST /kb/feedback`. Must be non-empty to accept any feedback in production. |

---

## Wiring (main.py)

The routers are registered in `bootstrap_application` via `_wire_agent_assist`:

```python
def _wire_agent_assist(app: FastAPI, knowledge_port: KnowledgePort, settings: Settings) -> None:
    app.include_router(build_kb_suggest_router(knowledge_port))
    faq_port = build_faq_feedback_port(settings)
    app.include_router(build_faq_router(faq_port, settings))
```

`knowledge_port` is the same instance used by the chat orchestrator, so no additional adapter construction is needed.

---

## Deferred Enhancements

- **Derive `/kb/suggest` query from `session_id`**: Currently the caller must supply `?q=` explicitly. A follow-up could accept `?session_id=` and have the backend derive the query from the session's latest transcript, enabling zero-effort suggestions without client-side query construction.
- **Auth on `/kb/suggest`**: The endpoint is POC-open (no auth). If it leaves the internal network, add `x-api-key` parity with `/kb/feedback`.
- **ZAF app UI polish**: The sidebar scaffold is functional but minimal. Production use should add article-count badges, loading skeleton, and error retry.
