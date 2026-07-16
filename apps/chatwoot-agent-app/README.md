# Proton Agent-Assist FAQ — Chatwoot Dashboard App

A thin **Chatwoot Dashboard App** (static HTML + vanilla JS, no build step) that surfaces
knowledge-base FAQ suggestions for the current conversation. It is the Chatwoot-native
counterpart to `apps/zendesk-agent-app/` (the ZAF sidebar). Both consume the same backend
FAQ endpoints (`GET /kb/suggest`, `POST /kb/feedback`).

> This is an **ADD**, not a replacement — the Zendesk app remains intact for Zendesk mode.

## What it does

1. Performs the Chatwoot Dashboard App postMessage handshake to read the current conversation.
2. Extracts the **latest incoming (customer) message** as the search query.
3. Calls `GET {backendBaseUrl}/kb/suggest?q=...&limit=5`.
4. Renders each suggestion (title, snippet, source link).
5. Lets the agent:
   - **Copy** the suggestion to the clipboard (see the one-click-insert limitation below).
   - **👍 / 👎** to rate usefulness → `POST /kb/feedback`.

## Files

- **index.html** — self-contained: inline `<style>` + `<script>`, no external dependencies,
  no ZAF/Chatwoot SDK required (the dashboard-app handshake is plain `postMessage`).

There is no `manifest.json` — unlike Zendesk, Chatwoot Dashboard Apps are registered in the
Chatwoot admin UI by pointing at a hosted iframe URL (see below), not by uploading a package.

## Configuration

The backend base URL and optional API key are read from query-string params on the iframe URL:

| Param            | Purpose                                        | Default                                                              |
|------------------|------------------------------------------------|---------------------------------------------------------------------|
| `backendBaseUrl` | Proton backend origin                          | `https://proton-backend-247165654737.asia-southeast1.run.app`       |
| `apiKey`         | `x-api-key` header for `POST /kb/feedback`     | *(empty)*                                                            |

Examples:

```
https://your-host/chatwoot-agent-app/index.html
https://your-host/chatwoot-agent-app/index.html?backendBaseUrl=http://localhost:8000
https://your-host/chatwoot-agent-app/index.html?apiKey=YOUR_QA_API_KEY
```

You can also edit the `DEFAULT_BACKEND` const at the top of the `<script>` block in
`index.html` if you prefer a hard-coded default.

## How to register it in Chatwoot

1. Host `index.html` somewhere reachable over HTTPS (any static host — GCS bucket, Netlify,
   nginx, etc.). The iframe URL is whatever serves that file.
2. In Chatwoot: **Admin (Settings) → Integrations → Dashboard Apps → "Configure" / "Add new
   dashboard app"**.
3. Fill in:
   - **App Name**: `Proton FAQ Assist`
   - **App URL (iframe URL)**: the hosted `index.html` URL (append `?backendBaseUrl=...` /
     `?apiKey=...` if you need non-default values).
4. Save. The app now appears as a tab in the **conversation dashboard panel** (right side) for
   agents. When an agent opens a conversation, Chatwoot renders this iframe and the handshake runs.

## The postMessage handshake

Chatwoot Dashboard Apps communicate with the embedded iframe via `window.postMessage`:

1. On load, the iframe posts a **string** message to its parent to request context:
   ```js
   window.parent.postMessage('chatwoot-dashboard-app:fetch-info', '*');
   ```
2. Chatwoot responds with a message whose payload is:
   ```js
   { event: 'appContext', data: { conversation, contact, currentAgent, ... } }
   ```
   (Chatwoot may deliver `event.data` as either an object or a JSON string; this app parses both.)
3. The app reads `data.conversation.messages`, walks them newest-first, and takes the latest
   **incoming** message (`message_type === 0`) `content` as the query. It falls back to the
   newest non-empty message, then to `conversation.last_non_activity_message.content`.
4. Whenever a fresh `appContext` arrives (e.g. the agent switches conversations, or clicks
   **Refresh**), suggestions are re-fetched.

## One-click-insert limitation (vs the old ZAF app)

The Zendesk app can inject text straight into the agent's reply box via
`client.invoke('ticket.comment.appendText', ...)`. **Chatwoot has no equivalent** — dashboard
apps run in a **cross-origin sandboxed iframe** and Chatwoot currently exposes **no
postMessage API for writing into the agent's reply editor**. Cross-origin DOM access to the
parent's composer is blocked by the browser same-origin policy.

Therefore this app provides **Copy to clipboard** instead of one-click insert: the agent
clicks **Copy** and pastes into their reply. This is a documented, intentional degradation.

## Backend TODOs (required for a live run — NOT done here)

> The backend under `apps/backend/` was intentionally **not modified**. The following changes
> are required on the backend for this app to work in production, and must be applied
> separately.

1. **CORS — add the Chatwoot origin to the allowlist.**
   The backend's CORS allowlist is `Settings.frontend_origins`
   (`apps/backend/src/chatbot/platform/config.py`), applied by `CORSMiddleware` in
   `apps/backend/src/chatbot/main.py` (`allow_origins=settings.frontend_origins`).
   The Chatwoot origin — `http://crm.34-50-103-151.nip.io` — must be added, either by:
   - setting the `FRONTEND_ORIGINS` env var (comma-separated) on the Cloud Run service to
     include `http://crm.34-50-103-151.nip.io`, **or**
   - appending it to the `frontend_origins` default list in `config.py`.

   ⚠️ Note: the current `main.py` middleware uses `allow_credentials=True`, which is
   incompatible with a wildcard origin — so the exact Chatwoot origin (not `*`) must be listed.
   Also add the origin of wherever `index.html` is **hosted** if that differs from the Chatwoot
   origin, since the browser's Origin header for the `fetch` is the iframe's own origin.

2. **API key for feedback (`POST /kb/feedback`).**
   The feedback endpoint (`apps/backend/src/chatbot/features/metrics/faq_router.py`) requires an
   `x-api-key` header that must match `Settings.qa_api_key` (env `QA_API_KEY`) via
   `hmac.compare_digest`. If `qa_api_key` is unset the endpoint returns **401** for all requests.
   To use feedback live:
   - ensure `QA_API_KEY` is set on the backend, and
   - pass the same value to this app via the `?apiKey=...` query param on the iframe URL.

   `GET /kb/suggest` is **unauthenticated** — suggestions work without an API key; only the
   feedback POST needs it.

## Backend endpoints targeted (read-only, unchanged)

### `GET /kb/suggest`

```
GET {backendBaseUrl}/kb/suggest?q=<query>&limit=5
```

Response (200):
```json
{
  "query": "password reset",
  "suggestions": [
    {
      "title": "How to Reset Your Password",
      "snippet": "To reset your password, click the 'Forgot Password' link...",
      "url": "https://help.example.com/articles/1234",
      "source_type": "zendesk_help_center"
    }
  ]
}
```

### `POST /kb/feedback`

```
POST {backendBaseUrl}/kb/feedback
Content-Type: application/json
x-api-key: <QA_API_KEY>

{
  "article_id": "How to Reset Your Password",
  "session_id": "session_abc123",
  "helpful": true,
  "score": 5
}
```

Response (200): `{ "status": "ok" }` · Response (401) if the key is missing/mismatched.

## Notes

- **No automated tests** — static scaffold; manual testing via the Chatwoot UI.
- **Session ID** is random per page load, not tied to agent identity.
- **Graceful degradation** — if the backend is unreachable or CORS-blocked, the app shows an
  error and the conversation workflow is unaffected.

---

**Built for Proton by PRO-NET**
