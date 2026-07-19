# Our Own Captain — Knowledge Base + Authoring + Citations — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let agents/admins author knowledge from inside Chatwoot, and show agents the source article(s) that grounded a Suggest/Copilot answer — reusing the existing live-FAQ + Vertex Search backend.

**Architecture:** Backend changes are small and additive in `proton-conversational-ai` (accept the frontend's existing key on the `/kb/faq` CRUD; return `sources` from `/assist/copilot`; broaden `/kb/*` CORS). Frontend adds (a) a **Knowledge Manager** static Dashboard-App (`apps/knowledge-manager/index.html`, served by the agent service, registered per tenant, config passed via URL query params — mirrors the Taxonomy Manager) and (b) **patch 0006** surfacing "Sources" in the ✨ Suggest result and the Ask Copilot panel.

**Tech Stack:** FastAPI + Pydantic v2, `google-genai`, pytest-asyncio + respx, vanilla HTML/JS Dashboard App, Chatwoot v4.15.1 patch-set (Docker `git apply` glob), `httpx`.

## Global Constraints

- Backend `pytest` root `proton-conversational-ai/apps/backend/`; `asyncio_mode = "auto"`; hermetic tests (conftest disables `.env`); `.venv/bin/python -m pytest`. No new runtime deps.
- **D1:** the Knowledge Manager is available to **admins AND agents** (gated by the `knowledge` feature flag / dashboard-app registration, not by role).
- **D2:** `/kb/faq` routes accept **`proton_backend_key`** (the key the frontend already carries) in addition to `faq_admin_api_key` — no new secret.
- **D3:** citations show by **title**; a live-FAQ entry has no public URL in v1 (cite by title only); Vertex Search hits already carry `url` and link out. `source_url` on live-FAQ entries is **out of scope** for v1.
- Fork patches are exported `git diff` files under `deploy/chatwoot-fork/patches/`, applied by the Dockerfile glob, imports via the `dashboard/composables` alias; build off-VM.
- Reuse existing infra verbatim: `faq_admin_router` CRUD, `live_faq` store, `/assist/suggest` `sources`, `ProtonCopilotPanel.vue`, the Taxonomy Manager app + `register_taxonomy_app.py` pattern.
- Gate the panel/app behind the `knowledge` value in `PROTON_FEATURES`.

---

## File Structure

### Repo: `proton-conversational-ai/apps/backend`
| Path | Action | Responsibility |
|---|---|---|
| `src/chatbot/features/chat/faq_admin_router.py` | Modify | `_authorize` also accepts `settings.proton_backend_key` |
| `src/chatbot/features/chat/test_faq_admin_auth.py` | Create | tests: proton key accepted, faq key still accepted, bad key 401 |
| `src/chatbot/features/assist/copilot_router.py` | Modify | aggregate + return `sources[]` from KB tool results |
| `src/chatbot/features/assist/test_copilot_router.py` | Modify | assert `sources` returned when KB tool ran |
| `src/chatbot/main.py` + `run_assist_local.py` | Modify | broaden the assist CORS middleware to allow the extra origin + GET/PUT/DELETE for `/kb/*` |

### Repo: `id-crm-ticketing`
| Path | Action | Responsibility |
|---|---|---|
| `apps/knowledge-manager/index.html` | Create | static CRUD UI over `/kb/faq`; reads `backend`+`key` from URL query |
| `chatwoot-config/register_knowledge_app.py` | Create | register the app as a Chatwoot Dashboard App per tenant (mirrors `register_taxonomy_app.py`) |
| `chatwoot-config/tests/test_register_knowledge_app.py`, `test_knowledge_manager_html.py` | Create | registration + HTML smoke tests |
| `deploy/chatwoot-fork/patches/0006-kb-sources.patch` | Create | "Sources" in Suggest result + Copilot panel |

---

## Tasks

### Task 1: Backend — `/kb/faq` accepts the frontend key (D2)

**Files:** Modify `src/chatbot/features/chat/faq_admin_router.py`; Create `src/chatbot/features/chat/test_faq_admin_auth.py`

**Interfaces:** Produces: `build_faq_admin_router` unchanged signature; `_authorize` now accepts a key equal to EITHER `settings.faq_admin_api_key` OR `settings.proton_backend_key` (both constant-time compared; empty keys never match).

- [ ] **Step 1: Write the failing tests**

Create `src/chatbot/features/chat/test_faq_admin_auth.py`:

```python
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.chat.faq_admin_router import build_faq_admin_router
from chatbot.platform.config import Settings


class _Store:
    async def list_all(self):
        return []


def _client(faq_key: str, proton_key: str) -> TestClient:
    s = Settings(faq_admin_api_key=faq_key, proton_backend_key=proton_key)
    app = FastAPI()
    app.include_router(build_faq_admin_router(_Store(), s))
    return TestClient(app, raise_server_exceptions=False)


def test_proton_key_accepted() -> None:
    c = _client(faq_key="fk", proton_key="pk")
    assert c.get("/kb/faq", headers={"x-api-key": "pk"}).status_code == 200


def test_faq_key_still_accepted() -> None:
    c = _client(faq_key="fk", proton_key="pk")
    assert c.get("/kb/faq", headers={"x-api-key": "fk"}).status_code == 200


def test_wrong_key_rejected() -> None:
    c = _client(faq_key="fk", proton_key="pk")
    assert c.get("/kb/faq", headers={"x-api-key": "nope"}).status_code == 401


def test_empty_keys_reject_everything() -> None:
    c = _client(faq_key="", proton_key="")
    assert c.get("/kb/faq", headers={"x-api-key": ""}).status_code == 401
```

- [ ] **Step 2: Run to verify fail** — `.venv/bin/python -m pytest src/chatbot/features/chat/test_faq_admin_auth.py -v` → `test_proton_key_accepted` FAILS 401.

- [ ] **Step 3: Implement** — in `faq_admin_router.py`, replace the body of `_authorize`:

```python
    def _authorize(x_api_key: str | None) -> None:
        if x_api_key is None:
            raise HTTPException(status_code=401, detail="Unauthorized")
        candidates = [settings.faq_admin_api_key, settings.proton_backend_key]
        supplied = x_api_key.encode("utf-8")
        for key in candidates:
            if key and hmac.compare_digest(supplied, key.encode("utf-8")):
                return
        raise HTTPException(status_code=401, detail="Unauthorized")
```

- [ ] **Step 4: Run to verify pass** — same command, 4 passed. Then the chat package: `.venv/bin/python -m pytest src/chatbot/features/chat -q` (no regressions).

- [ ] **Step 5: Commit**

```bash
git -C /Users/yudaadipratama/Archive/proton-conversational-ai commit -am \
  "feat(kb): /kb/faq accepts proton_backend_key (agents/admins author from CRM)"
```

---

### Task 2: Backend — `/assist/copilot` returns `sources[]`

**Files:** Modify `src/chatbot/features/assist/copilot_router.py` + `test_copilot_router.py`

**Interfaces:** Consumes: the existing loop + `ToolExecutor`. Produces: response is now `{answer, tool_calls, sources}` where `sources: [{title, snippet, url}]` is aggregated (deduped by title+url) from every `search_knowledge_base` tool result run this turn; `[]` if none ran.

- [ ] **Step 1: Write the failing test** — add to `test_copilot_router.py`:

```python
def test_copilot_returns_sources_from_kb_tool() -> None:
    # First response calls the KB tool; second returns text.
    from types import SimpleNamespace
    c = _client([
        _tool_call_response("search_knowledge_base", {"query": "warranty"}),
        _text_response("12 months per the KB."),
    ])
    r = c.post(
        "/assist/copilot",
        json={"conversation_id": "1", "thread": [{"role": "user", "content": "warranty?"}]},
        headers={"x-api-key": "k"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["sources"] and body["sources"][0]["title"] == "Warranty"
```

(The `_FakeKb` in that file already returns `KbArticle(title="Warranty", content="12 months", url="http://faq/1")`.)

- [ ] **Step 2: Run to verify fail** — `KeyError: 'sources'`.

- [ ] **Step 3: Implement** — in `copilot_router.py`: initialize `sources: list[dict] = []` before the loop; where each tool result comes back, if the tool is `search_knowledge_base`, extend `sources` from `result.get("articles", [])` mapping `{title, snippet: content, url}` and dedup by `(title, url)`; include `"sources": sources` in BOTH return statements (the text-answer return and the cap fallback).

```python
        seen_src: set[tuple] = set()
        # inside the tool loop, after `result = await executor.run(...)`:
        if c.name == "search_knowledge_base":
            for a in result.get("articles", []):
                key = (a.get("title"), a.get("url"))
                if key in seen_src:
                    continue
                seen_src.add(key)
                sources.append({"title": a.get("title"), "snippet": a.get("content", ""), "url": a.get("url")})
```

Return `{"answer": ..., "tool_calls": tool_calls, "sources": sources}` in both places.

- [ ] **Step 4: Run to verify pass** — `.venv/bin/python -m pytest src/chatbot/features/assist/test_copilot_router.py -v` (7 passed) and `src/chatbot/features/assist -q`.

- [ ] **Step 5: Commit** — `feat(copilot): return grounding sources[] alongside the answer`.

---

### Task 3: Backend — CORS for `/kb/*` (list/create/edit/delete cross-origin)

**Files:** Modify `src/chatbot/main.py` (`_wire_assist` CORS block) and `run_assist_local.py`.

**Interfaces:** Produces: the assist CORS middleware allows methods `GET, POST, PUT, DELETE, OPTIONS` (was `POST, OPTIONS`) so the Knowledge Manager (a cross-origin Dashboard App) can call `/kb/faq`. Origins still come from `assist_cors_origins` (+ the local runner's `ASSIST_LOCAL_ORIGINS`).

- [ ] **Step 1:** In `main.py` `_wire_assist`, change the second `CORSMiddleware`'s `allow_methods=["POST", "OPTIONS"]` to `allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"]`.
- [ ] **Step 2:** In `run_assist_local.py`, change the CORS `allow_methods` likewise.
- [ ] **Step 3: Verify boot** — `PROTON_BACKEND_KEY=k .venv/bin/python -c "from chatbot.main import app"` (no error).
- [ ] **Step 4: Commit** — `fix(cors): allow GET/PUT/DELETE on /kb/* for the Knowledge Manager app`.

> No test: CORS method config is not unit-testable meaningfully here; it is verified in the Task 7 local smoke.

---

### Task 4: Frontend — Knowledge Manager Dashboard App (static)

**Files:** Create `apps/knowledge-manager/index.html`

**Interfaces:** Consumes: `?backend=<PROTON_BACKEND_URL>&key=<PROTON_BACKEND_KEY>` from its own URL query (baked in at registration). Produces: a self-contained CRUD page calling `GET/POST/PUT/DELETE {backend}/kb/faq` with header `x-api-key: {key}`.

**What to build (mirror `apps/taxonomy-manager/index.html` — a single self-contained HTML file, vanilla JS, no build step):**
- On load, parse `backend` and `key` from `location.search`; if missing, show a config error.
- **List:** `GET {backend}/kb/faq` → render a table of entries (`question`, `answer` truncated, `keywords`, `tags`, `active`) with Edit/Delete buttons.
- **Create/Edit form:** fields `question` (textarea), `answer` (textarea), `keywords` (comma-separated → array), `tags` (comma-separated → array), `active` (checkbox). Submit → `POST /kb/faq` or `PUT /kb/faq/{id}`; on success re-list.
- **Delete:** confirm → `DELETE /kb/faq/{id}` → re-list.
- All requests send `headers: { 'Content-Type': 'application/json', 'x-api-key': key }`.
- Match the Taxonomy Manager's minimal inline-CSS style.

- [ ] **Step 1:** Author `apps/knowledge-manager/index.html` per the above (read `apps/taxonomy-manager/index.html` first and follow its structure/style).
- [ ] **Step 2: Local check** — open the file with `?backend=http://localhost:8000&key=local-test-key` against the running local runner; confirm the list loads and a create round-trips (this is exercised end-to-end in Task 7; a manual browser check here is enough).
- [ ] **Step 3: Commit** — `feat(knowledge): Knowledge Manager dashboard app (CRUD over /kb/faq)`.

---

### Task 5: Frontend — register the Knowledge Manager per tenant

**Files:** Create `chatwoot-config/register_knowledge_app.py`; Create `chatwoot-config/tests/test_register_knowledge_app.py`, `chatwoot-config/tests/test_knowledge_manager_html.py`

**Interfaces:** Consumes: the same env-file parser + `ChatwootClient` from `provision_labels` that `register_taxonomy_app.py` uses. Produces: a CLI that POSTs a Dashboard App titled "Knowledge" with `content=[{url: <app-url>?backend=..&key=.., type: "iframe"}]`, idempotent (skip if the title already exists).

- [ ] **Step 1: Write failing tests** — mirror `chatwoot-config/tests/test_register_taxonomy_app.py`: assert (a) idempotent skip when a "Knowledge" app exists, (b) a create POST with the right title + iframe url including `backend=` and `key=` query params (mock the httpx client). And `test_knowledge_manager_html.py`: assert `apps/knowledge-manager/index.html` exists, contains a `/kb/faq` fetch and reads `backend`/`key` from the query string.
- [ ] **Step 2: Run to verify fail** — `cd chatwoot-config && python -m pytest tests/test_register_knowledge_app.py tests/test_knowledge_manager_html.py -v`.
- [ ] **Step 3: Implement `register_knowledge_app.py`** — copy `register_taxonomy_app.py`, change: title → "Knowledge"; default `--app-url` → `http://agent.<PUBLIC_IP>.nip.io/apps/knowledge-manager`; append `?backend=<PROTON_BACKEND_URL>&key=<PROTON_BACKEND_KEY>` read from the tenant env; keep the idempotent create/skip logic.
- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** — `feat(knowledge): register_knowledge_app.py (per-tenant dashboard app)`.

---

### Task 6: Frontend — patch 0006, surface "Sources"

**Files:** Create `deploy/chatwoot-fork/patches/0006-kb-sources.patch` (authored in a clone with 0001–0005 applied, exported via `git diff`).

**What it adds (IP-safe description):**
1. **Copilot panel** (`ProtonCopilotPanel.vue`): the `askCopilot` response now includes `sources`; store them on the assistant message (`{ ..., sources }`) and render a compact **"Sources: title₁, title₂"** line under the assistant bubble (title links to `url` in a new tab when `url` is present, else plain text). Dedup by title+url.
2. **Suggest** (`ReplyTopPanel.vue` handler, from patch 0002): the `reply_suggestion` path already receives `data.sources` from `/assist/suggest`. Emit them alongside the text (extend the existing `protonAssistResult` emit to `{ text, mode, sources }`) and in `ReplyBox.vue` render a small "Sources" list under the reply editor when present. (Keep the existing insert behavior.)

- [ ] **Step 1:** Author the two edits in a clone with 0001–0005 applied; `git diff` the touched files → `0006-kb-sources.patch`.
- [ ] **Step 2: Verify apply** — `git apply --check` for 0001–0006 in sequence against a pristine v4.15.1 tree (all CLEAN when applied cumulatively).
- [ ] **Step 3: Commit** — `feat(patch-0006): show grounding sources in Suggest + Ask Copilot`.

---

### Task 7: Build, register, and smoke-test end-to-end

**Files:** Read: patches, `deploy/tenants/default.env`.

- [ ] **Step 1:** Rebuild the image (`deploy/chatwoot-fork`, glob applies 0001–0006); expect `applying 0006-kb-sources.patch` + a clean `vite build`.
- [ ] **Step 2:** Set `PROTON_FEATURES=ai_assist,nav_menu,copilot,knowledge` in `default.env`; `up -d chatwoot-rails chatwoot-sidekiq`. Restart the grounded local runner (`./run_copilot_local.sh`).
- [ ] **Step 3:** Register the app: `cd chatwoot-config && python register_knowledge_app.py --tenant default` (app-url pointing at the local agent host; backend/key from `default.env`).
- [ ] **Step 4: Browser smoke** (Incognito): open a conversation → the **Knowledge** dashboard app tab lists entries; add one ("How long is the Proton X50 battery warranty?" / "24 months.") → it saves. Then ✨ **Suggest** and **Ask Copilot** about the warranty → the answer shows a **"Sources"** line citing the new entry.
- [ ] **Step 5: Commit** any `default.env`/doc note (env is git-ignored; commit only tracked docs).

---

## Self-Review

**Spec coverage:** C1 (authoring API) → Tasks 1,3 (+reuse existing CRUD); C2 (Knowledge Manager nav-app, admins+agents) → Tasks 4,5; C3 (source citations in Suggest + Copilot) → Tasks 2,6. D1 (admins+agents via dashboard app, not role-gated) → Task 5. D2 (accept proton key) → Task 1. D3 (cite by title, no `source_url` in v1) → Tasks 2,6 (title + optional url from Vertex hits only). All covered.

**Placeholder scan:** backend tasks carry full test + impl code and exact `.venv/bin/python -m pytest` commands; the static app + patch use the proven house style (author → export → `git apply --check`); no TODO/TBD.

**Type consistency:** copilot response `{answer, tool_calls, sources}` is used identically in Task 2 (backend) and Task 6 (panel). `sources` item shape `{title, snippet, url}` matches `/assist/suggest` and the KB tool's `{title, content, url}` (mapped `content`→`snippet`). `/kb/faq` request/response shapes match the existing `faq_admin_router` (unchanged). Dashboard-app registration mirrors `register_taxonomy_app.py` verbatim in structure.

---

## Execution Handoff

Plan saved to `docs/superpowers/plans/2026-07-19-own-captain-knowledge-plan.md`. Backend Tasks 1–3 are clean TDD (subagent-friendly); frontend Tasks 4–7 are controller-authored with a browser smoke (human-in-loop), same split as the Copilot build.
