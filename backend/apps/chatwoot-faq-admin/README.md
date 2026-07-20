# Proton FAQ Admin — Chatwoot Dashboard App

A self-contained **Chatwoot Dashboard App** (static HTML + vanilla JS, no build step) that lets
the Proton CRM team manage the **real-time FAQ knowledge base** (full CRUD) from inside Chatwoot.
It is the admin counterpart to `apps/chatwoot-agent-app/` (the agent-assist suggestion sidebar):
that app *reads* the FAQ via `/kb/suggest`; this app *writes* it via the `/kb/faq` admin API.

Edits made here are reflected in agent suggestions **in real time** — the backend recomputes the
semantic embedding on every create/update, so `/kb/suggest` matches the new content by meaning
immediately (no re-index step).

> This is an **ADD**, not a replacement. The backend under `apps/backend/` is unchanged.

## What it does

- On load, `GET /kb/faq` and renders every entry in a **table**: question, answer preview,
  keywords, tags, active state, updated timestamp, and per-row actions.
- **Add FAQ** form (question, answer, keywords comma-separated, tags comma-separated) → `POST /kb/faq`.
- Per-row **Edit** (modal that PUTs only the changed fields) → `PUT /kb/faq/{id}`.
- Per-row **Delete** (browser confirm) → `DELETE /kb/faq/{id}`.
- Per-row **active/inactive toggle** → `PUT /kb/faq/{id}` with `{ active }`.
- Success/error **toasts**, and a clear **401** banner when the API key is missing or wrong.

Because it manages the *global* knowledge base rather than a single conversation, it does **not**
depend on the Chatwoot per-conversation `appContext` handshake. It still answers the handshake
harmlessly if Chatwoot loads it as a dashboard-app iframe, so it works either embedded or standalone.

## Files

- **index.html** — self-contained: inline `<style>` + `<script>`, no external dependencies, no
  framework, no build step. There is no `manifest.json` — Chatwoot Dashboard Apps are registered in
  the Chatwoot admin UI by pointing at a hosted iframe URL (see below), not by uploading a package.

## Configuration

Config is read from query-string params on the iframe URL:

| Param            | Purpose                                              | Default                                                        |
|------------------|------------------------------------------------------|----------------------------------------------------------------|
| `apiKey`         | `x-api-key` header for every `/kb/faq*` request       | *(empty — required; requests 401 without it)*                  |
| `backendBaseUrl` | Proton backend origin                                | `https://proton-backend-247165654737.asia-southeast1.run.app` |

Examples:

```
https://your-host/chatwoot-faq-admin/index.html?apiKey=YOUR_FAQ_ADMIN_API_KEY
https://your-host/chatwoot-faq-admin/index.html?apiKey=YOUR_FAQ_ADMIN_API_KEY&backendBaseUrl=http://localhost:8000
```

The API key is held **only in memory / the URL**. It is never written to `localStorage`.

## Backend endpoints used

All are guarded by `x-api-key` compared constant-time against `Settings.faq_admin_api_key`
(env `FAQ_ADMIN_API_KEY`). An empty configured key 401s everything. Source of truth:
`apps/backend/src/chatbot/features/chat/faq_admin_router.py` and the `LiveFaqEntry` dataclass in
`apps/backend/src/chatbot/features/chat/ports.py`.

| Method & path        | Request body                                                        | Response                              |
|----------------------|--------------------------------------------------------------------|---------------------------------------|
| `GET /kb/faq`        | —                                                                  | `{ "entries": [ <entry>, ... ] }`     |
| `POST /kb/faq`       | `{ question, answer, keywords: [str], tags: [str] }` (active→true)  | `{ "id": "...", "status": "ok" }`     |
| `PUT /kb/faq/{id}`   | any of `{ question, answer, keywords, tags, active }`               | `{ "id": "...", "status": "ok" }`     |
| `DELETE /kb/faq/{id}`| —                                                                  | `{ "id": "...", "status": "ok" }`     |

An `<entry>` (embedding omitted from the listing) is:

```json
{
  "id": "faq_abc123",
  "question": "How do I reset my password?",
  "answer": "Click 'Forgot Password' on the login screen ...",
  "keywords": ["password", "reset", "login"],
  "tags": ["account", "security"],
  "active": true,
  "updated_at": "2026-07-16T10:20:30Z"
}
```

## Go-live steps

1. **Host `index.html`** over **HTTPS** (any static host — GCS bucket, Netlify, nginx). The iframe
   URL is whatever serves that file.
2. **Register in Chatwoot**: **Admin (Settings) → Integrations → Dashboard Apps → Add new dashboard app**.
   - **App Name**: `FAQ Admin`
   - **App URL (iframe URL)**: the hosted `index.html` URL **with the key appended**, e.g.
     `https://your-host/chatwoot-faq-admin/index.html?apiKey=<FAQ_ADMIN_API_KEY>`
     (add `&backendBaseUrl=...` only if not using the Cloud Run default).
   - Save. The app appears as a tab in the dashboard panel for agents/admins.
3. **Set the API key**: the `?apiKey=` value must equal the backend's `FAQ_ADMIN_API_KEY`
   (env on the Cloud Run service). Rotate the key by updating both the env var and the iframe URL.
4. **Add the CRM origin to backend CORS**: the browser's `Origin` for these `fetch` calls is the
   iframe's **own** origin (wherever `index.html` is hosted — and/or the Chatwoot origin if it
   serves the app). Add that exact origin to `FRONTEND_ORIGINS` (the `Settings.frontend_origins`
   allowlist applied by `CORSMiddleware` in `apps/backend/src/chatbot/main.py`). The middleware uses
   `allow_credentials=True`, which is **incompatible with a wildcard** — list the exact origin, not `*`.

## Security notes

- The **API key is passed in the URL** (`?apiKey=...`). Host over **HTTPS** in production so the
  URL is not exposed in transit, and treat the iframe URL as a secret (it grants full FAQ CRUD).
- The key lives only in memory / the URL — it is **not** persisted to `localStorage`.
- Restrict who can see the Chatwoot Dashboard App configuration, since it contains the key.
- The **CRM origin must be in the backend CORS allowlist** (`FRONTEND_ORIGINS`) or the browser
  will block the requests.

## Notes

- **No automated tests** — static admin scaffold; manual testing via the Chatwoot UI / a browser.
- **Graceful degradation** — on any error (network, CORS, 401, backend down) the app shows a toast
  or banner and leaves the knowledge base untouched.
- **Real-time effect** — creates/updates recompute the embedding server-side, so changes surface in
  agent `/kb/suggest` results immediately.

---

**Built for Proton by PRO-NET**
