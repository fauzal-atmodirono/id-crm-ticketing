# Proton Agent-Assist FAQ — Zendesk Sidebar App

A thin Zendesk App Framework (ZAF) sidebar widget that consumes the backend FAQ endpoints (`GET /kb/suggest`, `POST /kb/feedback`) to surface knowledge base suggestions for the current ticket.

## Overview

This is a **static HTML + vanilla JS scaffold** (no build step, no automated tests) that:
1. Reads the ticket subject and latest comment as the search query
2. Calls `GET {backendBaseUrl}/kb/suggest?q=...&limit=5`
3. Renders each suggestion with title, snippet, and action buttons
4. Lets agents:
   - **Insert** the suggestion text into the ticket comment
   - **👍 / 👎** to rate the suggestion usefulness → `POST /kb/feedback`

## Architecture

- **manifest.json** — ZAF v2 metadata. Declares the sidebar location, framework version, and two app parameters:
  - `backendBaseUrl` (text, default: `http://localhost:8000`)
  - `apiKey` (text, secure, optional) — passed as `x-api-key` header to feedback endpoint

- **assets/iframe.html** — Self-contained HTML file:
  - Loads ZAF SDK v2
  - Fetches ticket data via `client.get(['ticket.subject', 'ticket.description', 'ticket.comments'])`
  - Calls backend `GET /kb/suggest` with combined query
  - Renders suggestions; "Insert" button invokes `client.invoke('ticket.comment.appendText', ...)`
  - 👍/👎 buttons POST feedback with session ID + helpful flag

## Installation

### 1. Build a ZIP package

From the repo root:
```bash
cd apps/zendesk-agent-app
zip -r proton-agent-assist-faq.zip manifest.json assets/
```

### 2. Upload as a Private App

1. Go to **Zendesk Admin Center** → **Apps** → **Manage apps**
2. Click **Upload private app** (or **+ Add app** → **Custom**; must have `app:write` permission)
3. Select the ZIP file
4. Click **Upload**

Zendesk will validate the manifest and register the app.

### 3. Configure the App

After upload, go to **Admin Center** → **Apps** → find "Proton Agent-Assist FAQ" → **Settings**:
- **backendBaseUrl**: Set to your Proton backend URL (e.g., `http://localhost:8000`, `https://api.example.com`)
- **apiKey** (optional): If your backend's `/kb/feedback` endpoint requires authentication, paste the API key here

### 4. Enable on Ticket Views

Once installed:
- Go to **Admin Center** → **Ticket settings** → **Ticket workspace**
- Under **Sidebar apps**, enable "Proton Agent-Assist FAQ"
- The widget will appear in agents' ticket sidebars

## Backend Configuration (CORS)

For the app to fetch suggestions from your backend, the Zendesk iframe origin must be whitelisted in the backend's CORS middleware.

Edit `apps/backend/src/chatbot/platform/config.py`:

```python
CORS_ORIGINS = [
    "http://localhost:3000",
    "https://<your-zendesk-subdomain>.zendesk.com",
    "https://<your-zendesk-subdomain>.zdusercontent.com"
]
```

Then update the FastAPI CORS middleware in `apps/backend/src/chatbot/main.py`:

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

### Why Two Origins?

- **zendesk.com** — Traditional Zendesk iframe origin (some deployments)
- **zdusercontent.com** — Newer sandboxed iframe origin (recommended)

Test with your Zendesk subdomain's actual origin in browser DevTools (Network tab, check iframe's `src` and `Location` header).

## App Behavior

### Initialization
1. App loads in ticket sidebar
2. ZAF client initializes and reads app settings (`backendBaseUrl`, `apiKey`)
3. Automatically fetches suggestions for the current ticket's subject + latest comment

### Suggestion Display
- Shows up to 5 FAQ articles (title + snippet + source type)
- If no suggestions, displays "No relevant FAQ articles found"
- If fetch fails, displays error message with HTTP status

### Insert Action
- Clicking **Insert** appends the article title and URL to the ticket comment
- Uses `client.invoke('ticket.comment.appendText', ...)` — agent must still submit the comment manually

### Feedback
- 👍 or 👎 sends a `POST /kb/feedback` request with:
  - `article_id`: the suggestion title
  - `session_id`: a random session ID (generated per page load)
  - `helpful`: true/false
  - `score`: 5 (helpful) or 1 (unhelpful)
- Header: `x-api-key: {apiKey}` (if configured)
- Shows a confirmation message ("✓ Marked helpful/unhelpful") below the suggestion

## Development & Testing

### Local Testing
1. Start backend at `http://localhost:8000`:
   ```bash
   cd apps/backend
   .venv/bin/uvicorn chatbot.main:app --reload
   ```

2. Verify `GET /kb/suggest?q=test&limit=5` works:
   ```bash
   curl http://localhost:8000/kb/suggest?q=test&limit=5
   ```

3. To test the app locally without uploading to Zendesk, you can:
   - Serve `assets/iframe.html` locally and mock the ZAF client (for manual testing)
   - Or use Zendesk's **Zendesk App Tools (ZAT)** CLI to run a local dev environment

### Debugging
- Open browser DevTools on the Zendesk ticket page
- Check the sidebar iframe's Network and Console tabs
- Look for fetch errors or JSON parse issues
- Verify `backendBaseUrl` is correct in app settings

## Notes

- **No automated tests** — this is a scaffold. Manual testing via Zendesk UI is required.
- **Session ID**: Generated randomly per page load; not tied to user identity.
- **API Key**: Sent as `x-api-key` header; configure in backend's authentication middleware if required.
- **Graceful degradation**: If backend is unreachable, app shows error message; ticket workflow is unaffected.
- **Single-file HTML**: No external dependencies except ZAF SDK. Inline `<style>` and `<script>` for simplicity.

## API Endpoints (Backend)

### GET /kb/suggest

```bash
curl 'http://localhost:8000/kb/suggest?q=password%20reset&limit=5'
```

**Response** (200):
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

### POST /kb/feedback

```bash
curl -X POST 'http://localhost:8000/kb/feedback' \
  -H 'Content-Type: application/json' \
  -H 'x-api-key: your-api-key' \
  -d '{
    "article_id": "How to Reset Your Password",
    "session_id": "session_abc123",
    "helpful": true,
    "score": 5
  }'
```

**Response** (200):
```json
{
  "status": "ok",
  "message": "Feedback recorded"
}
```

---

**Built for Proton by PRO-NET**
