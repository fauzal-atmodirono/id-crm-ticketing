# Package B — Contacts/360 Merge + WhatsApp Video Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the AI understand customer-sent WhatsApp videos, and make Contacts the single customer-lookup surface by folding Customer 360 into it.

**Architecture:** Two independent halves bundled by size. The video half threads a third media slot (`video_base64` / `video_mime_type`) through the existing audio/image path: `agent` orchestrator → `ProtonConfigClient.chat_turn` → backend `/chat/turn` → a Gemini `types.Part`. The Contacts half is a Chatwoot fork patch only — the backend endpoint `GET /admin/customer360/search` is unchanged, so only the UI that consumes it moves.

**Tech Stack:** Python 3.12, FastAPI, pytest with `asyncio_mode=auto`, `respx` for HTTP stubbing, google-genai, Vue 3 (Chatwoot SPA, patched at image-build time).

**Spec:** `docs/superpowers/specs/2026-08-04-pkg-b-contacts-360-merge-and-whatsapp-video-design.md`

## Global Constraints

- Tests never hit postgres, the real Chatwoot API, or Gemini. `agent/tests/conftest.py` sets env and points `AGENT_DATABASE_URL` at throwaway sqlite; HTTP is stubbed with `respx`; Gemini clients are injected.
- Run the agent suite from `agent/`: `pytest`. No flags needed.
- **Background-task invariant:** services in `app/services/` never raise for expected "nothing to do" cases — missing fields, unknown ids, downstream HTTP failures are logged and skipped. Raising out of a background task only produces an unretrieved-exception log.
- Any new env var must be added to `agent/app/config.py`, `deploy/tenants/example.env`, and `agent/tests/conftest.py` if required at import time. Names map case-insensitively and **must match verbatim**.
- Flag off ⇒ behaviour byte-identical to today. Assert this explicitly in tests.
- **The Chatwoot SPA source is not in this checkout** and this sandbox cannot clone upstream. Fork patches that touch native files must be authored where upstream `v4.15.1` source is available. Tasks 5-7 say how to handle this rather than pretending otherwise.
- Work on branch `dev-yuda`. Never merge to `main`.

---

## File Structure

| File | Responsibility |
|---|---|
| `agent/app/services/media.py:29-33` | Modify: add a `video` default mime type |
| `agent/app/config.py:64` | Modify: add `whatsapp_video_max_bytes` |
| `agent/app/services/orchestrator.py:592-614` | Modify: add the video attachment branch and size guard |
| `agent/app/clients/proton.py:332-341` | Modify: `chat_turn` gains `video_base64` / `video_mime_type` |
| `backend/apps/backend/src/chatbot/features/chat/router.py:57-64` | Modify: `ChatTurnRequest` gains the two video fields |
| `backend/apps/backend/src/chatbot/features/chat/service.py:410-483` | Modify: `handle_turn` accepts and appends a video part |
| `agent/tests/test_orchestrator_video.py` | Create: video attachment behaviour tests |
| `deploy/tenants/example.env` | Modify: document the new setting |
| `deploy/chatwoot-fork/patches/0042-contacts-360-merge.patch` | Create: fold 360 into Contacts, drop the standalone menu |

---

### Task 1: Teach the media fetcher about video

**Files:**
- Modify: `agent/app/services/media.py:29-33`
- Test: `agent/tests/test_media.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `fetch_attachment_bytes(data_url, file_type_hint="video")` returns `("video/mp4")` as the mime type when the server sends no useful Content-Type. Task 3 relies on this.

- [ ] **Step 1: Write the failing test**

Append to `agent/tests/test_media.py`:

```python
@respx.mock
async def test_fetch_video_falls_back_to_mp4_when_content_type_generic():
    respx.get("http://cw/v.bin").mock(
        return_value=httpx.Response(
            200, content=b"\x00\x00\x00\x18ftypmp42", headers={"content-type": "application/octet-stream"}
        )
    )
    result = await fetch_attachment_bytes("http://cw/v.bin", file_type_hint="video")
    assert result is not None
    data, mime = result
    assert mime == "video/mp4"
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd agent && pytest tests/test_media.py::test_fetch_video_falls_back_to_mp4_when_content_type_generic -v`
Expected: FAIL — `assert 'application/octet-stream' == 'video/mp4'`, because the hint is unknown and `_DEFAULT_MIME_TYPE` is returned.

- [ ] **Step 3: Add the mapping**

In `agent/app/services/media.py`, extend `_FILE_TYPE_MIME_DEFAULTS`:

```python
_FILE_TYPE_MIME_DEFAULTS = {
    "audio": "audio/ogg",
    "image": "image/jpeg",
    # WhatsApp/Twilio deliver customer videos as MP4; used only when the
    # response Content-Type is missing or generic.
    "video": "video/mp4",
}
```

- [ ] **Step 4: Run it and watch it pass**

Run: `cd agent && pytest tests/test_media.py -v`
Expected: PASS, all pre-existing tests in the file still green.

- [ ] **Step 5: Commit**

```bash
git add agent/app/services/media.py agent/tests/test_media.py
git commit -m "feat(media): default video attachments to video/mp4"
```

---

### Task 2: Add the video size cap setting

**Files:**
- Modify: `agent/app/config.py` (next to `whatsapp_media_understanding_enabled`, line 64)
- Modify: `deploy/tenants/example.env`
- Test: `agent/tests/test_video_config.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: `get_settings().whatsapp_video_max_bytes -> int`, default `16777216`. Task 3 reads it.

- [ ] **Step 1: Write the failing test**

Create `agent/tests/test_video_config.py`:

```python
"""The video size cap must have a WhatsApp-sized default so an oversized clip
is skipped rather than sent to Gemini and rejected mid-turn."""

from app.config import get_settings


def test_video_max_bytes_defaults_to_whatsapp_limit():
    assert get_settings().whatsapp_video_max_bytes == 16 * 1024 * 1024
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd agent && pytest tests/test_video_config.py -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'whatsapp_video_max_bytes'`.

- [ ] **Step 3: Add the setting**

In `agent/app/config.py`, immediately after `whatsapp_media_understanding_enabled`:

```python
    # Videos above this are skipped rather than sent to Gemini: WhatsApp caps
    # inbound video at 16 MB and Gemini inline request data at roughly 20 MB,
    # so 16 MB fits inline and no Files API upload is needed.
    whatsapp_video_max_bytes: int = 16 * 1024 * 1024
```

- [ ] **Step 4: Run it and watch it pass**

Run: `cd agent && pytest tests/test_video_config.py -v`
Expected: PASS.

- [ ] **Step 5: Document the variable**

In `deploy/tenants/example.env`, below the media-understanding flag:

```bash
# Max inbound WhatsApp video size handed to Gemini, in bytes. Larger clips are
# skipped and logged; the turn still proceeds on its text. Default 16 MB, which
# is WhatsApp's own inbound cap.
WHATSAPP_VIDEO_MAX_BYTES=16777216
```

- [ ] **Step 6: Commit**

```bash
git add agent/app/config.py agent/tests/test_video_config.py deploy/tenants/example.env
git commit -m "feat(config): add whatsapp_video_max_bytes cap"
```

---

### Task 3: Pull video attachments in the orchestrator

**Files:**
- Modify: `agent/app/services/orchestrator.py:592-620` (inside `_process_via_chat_agent`)
- Test: `agent/tests/test_orchestrator_video.py` (create)

**Interfaces:**
- Consumes: `fetch_attachment_bytes` (Task 1), `whatsapp_video_max_bytes` (Task 2).
- Produces: `_process_via_chat_agent` calls `proton.chat_turn(..., video_base64=..., video_mime_type=...)`. Task 4 must accept those kwargs.

- [ ] **Step 1: Write the failing tests**

Create `agent/tests/test_orchestrator_video.py`. It follows the injection pattern of `test_orchestrator_chat_agent.py` — a real `httpx` client against the respx-mocked backend, patched into the orchestrator module namespace.

```python
"""Customer-sent WhatsApp video reaches Gemini as a third media slot alongside
audio and image, and an oversized clip is skipped rather than breaking the turn.
"""

from __future__ import annotations

import base64

import httpx
import pytest
import respx

from app.clients.proton import ProtonConfigClient
from app.config import get_settings
from app.services import orchestrator

PROTON = "http://proton-backend:8080"
CDN = "http://cw-assets"


def _make_proton_client() -> ProtonConfigClient:
    inner = httpx.AsyncClient(base_url=PROTON, headers={"x-api-key": "testkey"})
    return ProtonConfigClient(base_url=PROTON, api_key="testkey", client=inner, ttl=0.0)


def _message_with_video(content: str = "tengok video ni") -> list[dict]:
    return [
        {
            "id": 1,
            "content": content,
            "message_type": 0,
            "private": False,
            "attachments": [{"file_type": "video", "data_url": f"{CDN}/clip.mp4"}],
        }
    ]


@pytest.fixture(autouse=True)
def _enable_media(monkeypatch):
    monkeypatch.setattr(get_settings(), "whatsapp_media_understanding_enabled", True)


@respx.mock
async def test_video_attachment_is_sent_to_chat_turn(monkeypatch):
    respx.get(f"{CDN}/clip.mp4").mock(
        return_value=httpx.Response(200, content=b"MP4BYTES", headers={"content-type": "video/mp4"})
    )
    turn = respx.post(f"{PROTON}/chat/turn").mock(
        return_value=httpx.Response(200, json={"reply": "ok", "handoff": None, "products": []})
    )
    client = _make_proton_client()
    monkeypatch.setattr(orchestrator, "get_proton_config_client", lambda: client)

    await orchestrator._process_via_chat_agent(
        conversation_id=42,
        message_list=_message_with_video(),
        effective_mode="auto",
        chatwoot=_FakeChatwoot(),
        handoff_message="one moment",
        inbox_id=7,
    )

    body = turn.calls.last.request.content.decode()
    assert base64.b64encode(b"MP4BYTES").decode() in body
    assert "video/mp4" in body


@respx.mock
async def test_oversized_video_is_skipped_but_turn_proceeds(monkeypatch):
    monkeypatch.setattr(get_settings(), "whatsapp_video_max_bytes", 4)
    respx.get(f"{CDN}/clip.mp4").mock(
        return_value=httpx.Response(200, content=b"MUCHTOOBIG", headers={"content-type": "video/mp4"})
    )
    turn = respx.post(f"{PROTON}/chat/turn").mock(
        return_value=httpx.Response(200, json={"reply": "ok", "handoff": None, "products": []})
    )
    client = _make_proton_client()
    monkeypatch.setattr(orchestrator, "get_proton_config_client", lambda: client)

    await orchestrator._process_via_chat_agent(
        conversation_id=42,
        message_list=_message_with_video(),
        effective_mode="auto",
        chatwoot=_FakeChatwoot(),
        handoff_message="one moment",
        inbox_id=7,
    )

    body = turn.calls.last.request.content.decode()
    assert "video_base64" not in body
    assert "tengok video ni" in body


@respx.mock
async def test_flag_off_does_not_fetch_video(monkeypatch):
    monkeypatch.setattr(get_settings(), "whatsapp_media_understanding_enabled", False)
    fetch = respx.get(f"{CDN}/clip.mp4")
    respx.post(f"{PROTON}/chat/turn").mock(
        return_value=httpx.Response(200, json={"reply": "ok", "handoff": None, "products": []})
    )
    client = _make_proton_client()
    monkeypatch.setattr(orchestrator, "get_proton_config_client", lambda: client)

    await orchestrator._process_via_chat_agent(
        conversation_id=42,
        message_list=_message_with_video(),
        effective_mode="auto",
        chatwoot=_FakeChatwoot(),
        handoff_message="one moment",
        inbox_id=7,
    )

    assert not fetch.called
```

Add the minimal Chatwoot double this file needs, above the tests:

```python
class _FakeChatwoot:
    """Only the methods _process_via_chat_agent touches on the happy path."""

    async def get_inbox(self, inbox_id):
        return {"greeting_enabled": False}

    async def create_message(self, *args, **kwargs):
        return None

    async def toggle_status(self, *args, **kwargs):
        return None
```

If a test fails because `_process_via_chat_agent` calls a Chatwoot method not stubbed here, add that method to `_FakeChatwoot` returning `None` — do not weaken the assertions.

- [ ] **Step 2: Run them and watch them fail**

Run: `cd agent && pytest tests/test_orchestrator_video.py -v`
Expected: the first two FAIL (no video is ever sent); `test_flag_off_does_not_fetch_video` may already pass, which is fine — it is a regression guard.

- [ ] **Step 3: Add the video branch**

In `agent/app/services/orchestrator.py`, extend the media block. Initialise alongside the existing slots:

```python
    audio_base64 = audio_mime_type = None
    image_base64 = image_mime_type = None
    video_base64 = video_mime_type = None
```

Then add a third branch inside the attachment loop, after the `image` branch:

```python
                elif file_type == "video" and video_base64 is None:
                    fetched = await fetch_attachment_bytes(data_url, file_type_hint=file_type)
                    if fetched is not None:
                        data, mime = fetched
                        # Skip oversized clips rather than sending a request
                        # Gemini will reject: the turn still proceeds on its
                        # text, which is better than failing the whole turn.
                        if len(data) > settings.whatsapp_video_max_bytes:
                            logger.warning(
                                "orchestrator_video_too_large: conversation %s video %d bytes "
                                "exceeds whatsapp_video_max_bytes %d; skipping video",
                                conversation_id,
                                len(data),
                                settings.whatsapp_video_max_bytes,
                            )
                        else:
                            video_base64 = base64.b64encode(data).decode()
                            video_mime_type = mime
```

Include video in the empty-turn short-circuit so a caption-less video is not silently dropped:

```python
    if not text and audio_base64 is None and image_base64 is None and video_base64 is None:
```

And pass it through:

```python
        await proton.chat_turn(
            f"crm-{conversation_id}",
            text,
            inbox_id,
            audio_base64=audio_base64,
            audio_mime_type=audio_mime_type,
            image_base64=image_base64,
            image_mime_type=image_mime_type,
            video_base64=video_base64,
            video_mime_type=video_mime_type,
        )
```

- [ ] **Step 4: Run the whole agent suite**

Run: `cd agent && pytest`
Expected: all PASS. The two new video tests go green; nothing else regresses. (Step 3's `chat_turn` kwargs do not exist yet — if `chat_turn` raises `TypeError`, do Task 4 first and return here.)

- [ ] **Step 5: Commit**

```bash
git add agent/app/services/orchestrator.py agent/tests/test_orchestrator_video.py
git commit -m "feat(orchestrator): pull WhatsApp video attachments for AI understanding"
```

---

### Task 4: Forward video through the Proton client

**Files:**
- Modify: `agent/app/clients/proton.py:332-372`
- Test: `agent/tests/test_orchestrator_video.py` (already covers this end to end)

**Interfaces:**
- Consumes: called by Task 3.
- Produces: `chat_turn(..., video_base64: str | None = None, video_mime_type: str | None = None)` posts `video_base64` / `video_mime_type` in the JSON body. Task 5 consumes those keys.

- [ ] **Step 1: Extend the signature**

In `agent/app/clients/proton.py`, add two parameters after the image pair:

```python
        image_mime_type: str | None = None,
        video_base64: str | None = None,
        video_mime_type: str | None = None,
    ) -> dict | None:
```

- [ ] **Step 2: Add them to the payload**

Following the existing conditional style, after the image keys:

```python
            if video_base64 is not None:
                payload["video_base64"] = video_base64
            if video_mime_type is not None:
                payload["video_mime_type"] = video_mime_type
```

Keys are omitted when `None`, so an unchanged backend keeps working — this is what makes the agent deployable ahead of the backend.

- [ ] **Step 3: Run the tests**

Run: `cd agent && pytest tests/test_orchestrator_video.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add agent/app/clients/proton.py
git commit -m "feat(proton-client): forward video media on chat_turn"
```

---

### Task 5: Accept and use video in the backend

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/chat/router.py:57-64` and the `/chat/turn` handler around line 1070
- Modify: `backend/apps/backend/src/chatbot/features/chat/service.py:410-483`
- Test: `backend/apps/backend/src/chatbot/features/chat/test_chat_turn_video.py` (create)

**Interfaces:**
- Consumes: the JSON keys produced by Task 4.
- Produces: a Gemini `types.Part` carrying the video bytes.

- [ ] **Step 1: Write the failing test**

Create `backend/apps/backend/src/chatbot/features/chat/test_chat_turn_video.py`:

```python
"""A video part reaches Gemini alongside the text, and a corrupt payload is
skipped rather than failing the turn."""

from __future__ import annotations

import base64


async def test_video_base64_becomes_a_gemini_part(orchestrator_service, captured_contents):
    await orchestrator_service.handle_turn(
        session_id="s1",
        text="what is wrong with my car",
        video_base64=base64.b64encode(b"MP4BYTES").decode(),
        video_mime_type="video/mp4",
    )
    parts = captured_contents[-1].parts
    assert any(getattr(p, "inline_data", None) and p.inline_data.mime_type == "video/mp4" for p in parts)


async def test_undecodable_video_is_skipped_and_turn_still_runs(orchestrator_service, captured_contents):
    await orchestrator_service.handle_turn(
        session_id="s2",
        text="what is wrong with my car",
        video_base64="!!!not-base64!!!",
        video_mime_type="video/mp4",
    )
    parts = captured_contents[-1].parts
    assert any(getattr(p, "text", None) == "what is wrong with my car" for p in parts)
    assert not any(getattr(p, "inline_data", None) for p in parts)
```

Reuse the fixtures the existing co-located suites use for `OrchestratorService` and content capture; if no `captured_contents` fixture exists, add one in this file that records the `types.Content` passed to the injected Gemini double, mirroring how `test_phase2_smoke.py` injects its client.

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend/apps/backend && .venv/bin/pytest src/chatbot/features/chat/test_chat_turn_video.py -v`
Expected: FAIL — `handle_turn() got an unexpected keyword argument 'video_base64'`.

- [ ] **Step 3: Extend the request model**

In `router.py`, add to `ChatTurnRequest`:

```python
    video_base64: str | None = None
    video_mime_type: str | None = None
```

and pass them in the `/chat/turn` handler alongside the image pair:

```python
            video_base64=req.video_base64,
            video_mime_type=req.video_mime_type,
```

- [ ] **Step 4: Append the part**

In `service.py`, add the parameters to `handle_turn` after `image_mime_type`, then append a third block matching the existing two exactly:

```python
        if video_base64 and video_mime_type:
            try:
                parts.append(
                    types.Part.from_bytes(data=base64.b64decode(video_base64), mime_type=video_mime_type)
                )
            except Exception:
                _log.warning("handle_turn_video_decode_failed", session_id=session_id)
```

Note the `try/except` is what makes the second test pass — a corrupt payload must degrade to text, never raise.

- [ ] **Step 5: Run the backend suite**

Run: `cd backend/apps/backend && .venv/bin/pytest src/ -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/backend/src/chatbot/features/chat/router.py backend/apps/backend/src/chatbot/features/chat/service.py backend/apps/backend/src/chatbot/features/chat/test_chat_turn_video.py
git commit -m "feat(chat): accept video media on /chat/turn"
```

---

### Task 6: Author the Contacts/360 merge fork patch

**Files:**
- Create: `deploy/chatwoot-fork/patches/0042-contacts-360-merge.patch`
- Reference: `deploy/chatwoot-fork/patches/0041-customer360-admin.patch` (the patch this supersedes the UI half of)

**Interfaces:**
- Consumes: `GET /admin/customer360/search?q=` — **unchanged**, same URL, same `customer360.view` permission, same `{contact, conversations, rsa_incidents}` payload.
- Produces: no new backend surface.

**Before starting:** the Chatwoot SPA source is not in this checkout. Author this patch on a machine that can clone `chatwoot/chatwoot` at tag `v4.15.1` (the value in `deploy/chatwoot-fork/UPSTREAM_VERSION`). Do not hand-write a diff against files you have not read — the hunk context will not apply and the image build will fail.

- [ ] **Step 1: Obtain upstream and apply the existing patch stack**

```bash
git clone --depth 1 --branch v4.15.1 https://github.com/chatwoot/chatwoot.git /tmp/cw
cd /tmp/cw && for p in <repo>/deploy/chatwoot-fork/patches/00*.patch; do git apply "$p" || echo "FAILED $p"; done
```

Expected: every patch applies cleanly. A failure here means the stack has drifted and must be fixed before adding to it.

- [ ] **Step 2: Remove the standalone Customer 360 surface**

Delete `app/javascript/dashboard/views/ProtonCustomer360Page.vue`, and remove its route from `dashboard.routes.js` and its entry from `components-next/sidebar/Sidebar.vue` — both were added by patch `0041`.

- [ ] **Step 3: Add the 360 panel to the contact detail view**

In the native contact detail sidebar, add a section that calls `protonAdmin.js`'s customer360 search with the contact's phone number and renders two blocks: cross-channel conversations (channel, status, created/resolved, division/concern, assignee) and RSA incidents. Reuse the rendering that `ProtonCustomer360Page.vue` used before deletion — move it into a component rather than rewriting it.

- [ ] **Step 4: Add the vehicle-number search fallback**

In the native Contacts search, when a query returns no native results and does not look like an email or phone number, call `/admin/customer360/search` and render the matched contacts from its vehicle branch. Normal searches must be untouched — this is a fallback, not a replacement.

- [ ] **Step 5: Render the accuracy caveat**

When results came from the vehicle branch, show an inline note: conversation matching is by `vehicle_model` custom attribute, not a true plate, so results may be incomplete. Do not let the UI imply completeness — the limitation is real until Package E's `vehicle_no` field or Package F's DMS lands.

- [ ] **Step 6: Generate the patch**

```bash
cd /tmp/cw && git diff > <repo>/deploy/chatwoot-fork/patches/0042-contacts-360-merge.patch
```

- [ ] **Step 7: Verify it applies from clean**

```bash
rm -rf /tmp/cw2 && git clone --depth 1 --branch v4.15.1 https://github.com/chatwoot/chatwoot.git /tmp/cw2
cd /tmp/cw2 && for p in <repo>/deploy/chatwoot-fork/patches/*.patch; do git apply "$p" || echo "FAILED $p"; done
```

Expected: no `FAILED` lines. This is the only automated check this half has — the fork has no test harness.

- [ ] **Step 8: Commit**

```bash
git add deploy/chatwoot-fork/patches/0042-contacts-360-merge.patch
git commit -m "feat(chatwoot-fork): fold Customer 360 into Contacts and drop the standalone menu"
```

---

### Task 7: Build, deploy and verify both halves on proton

**Files:** none — deployment and manual verification.

**Interfaces:**
- Consumes: everything above.
- Produces: verified behaviour on the live tenant.

- [ ] **Step 1: Build the Chatwoot image off-VM for amd64**

```bash
gcloud builds submit deploy/chatwoot-fork/ --config deploy/chatwoot-fork/cloudbuild.yaml --substitutions _REGISTRY=<AR repo>
```

Never build this image on the VM, and never build it locally on an arm64 Mac — the VM's amd64 pull will fail with "no matching manifest".

- [ ] **Step 2: Deploy agent and backend**

```bash
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --command='cd /opt/platform/deploy && sudo docker compose -p proton -f docker-compose.tenant.yml --env-file tenants/proton.env up -d --build backend agent'
```

- [ ] **Step 3: Pull and recreate Chatwoot**

```bash
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --command='cd /opt/platform/deploy && sudo docker compose -p proton -f docker-compose.tenant.yml --env-file tenants/proton.env pull chatwoot-rails chatwoot-sidekiq && sudo docker compose -p proton -f docker-compose.tenant.yml --env-file tenants/proton.env up -d --force-recreate chatwoot-rails chatwoot-sidekiq'
```

- [ ] **Step 4: Verify the video path end to end**

Send a short video of a car to the proton WhatsApp number. Expected: the bot answers about the video's content. Confirm the media actually reached the backend:

```bash
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --command='sudo docker logs proton-agent --since 5m 2>&1 | grep -iE "video" | tail -10'
```

A `orchestrator_video_too_large` line means the clip exceeded the cap — retry with a shorter one, which is correct behaviour, not a bug.

- [ ] **Step 5: Verify the Contacts merge**

In the UI: the **Customer 360** sidebar icon is gone; Contacts search by phone returns the contact with the 360 panel showing conversations and RSA incidents; a vehicle-number search returns results with the accuracy caveat visible; an unknown value returns an empty result rather than an error; and a user **without** `customer360.view` sees Contacts working normally with the panel simply absent — not a broken page.

- [ ] **Step 6: Update the coverage document**

In `docs/analysis/proton-demo-feedback-coverage-2026-07-28.md`, move items **#1** (Contacts merge), **#9** (video), **#14** (per-case detail) and **#26** (the video discrepancy) from 🧪 to ✅, citing what was verified by hand. Item #26 in particular should note that the presenter's live claim is now true rather than quietly dropping the discrepancy.

- [ ] **Step 7: Commit**

```bash
git add docs/analysis/proton-demo-feedback-coverage-2026-07-28.md
git commit -m "docs: mark Contacts merge and WhatsApp video verified on proton"
```

---

## Out of scope

Video on the website widget (a different upload flow), video in KB ingest (feedback #2), frame extraction or thumbnails, and persisting video anywhere — bytes go to Gemini and are not stored by us.
