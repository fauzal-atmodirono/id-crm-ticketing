# Package B — Fold Customer 360 into Contacts + WhatsApp video understanding

**Date:** 2026-08-04
**Covers demo-feedback items:** #1 (merge Customer 360 into Contacts), #9 (customer-sent videos)
**Type:** two small, independent code changes bundled by size, not by subject.
**Effort:** small. Neither depends on anything outside this repo.

---

## Part 1 — Merge Customer 360 into Contacts

### 1.1 Goal

One place to look a customer up. Today `Customer 360` is a separate sidebar
icon with its own search box, next to Chatwoot's native `Contacts` page that
does a similar-but-different thing. Agents shouldn't have to know which to
click. **Decision taken: fold 360 into native Contacts and drop the standalone
menu entry.**

### 1.2 Current state

- Backend: `GET /admin/customer360/search?q=` in
  `backend/apps/backend/src/chatbot/features/chat/customer360_router.py`,
  permission `customer360.view`. Two branches:
  - input looks like a phone number (`_PHONE_RE`) → `chatwoot.search_contacts`,
    pick the exact digits-only match (`_pick_best_contact`), then
    `list_contact_conversations`;
  - otherwise → treat as a vehicle number: substring match against RSA
    `vehicle_no`, plus a best-effort substring match against each
    conversation's `vehicle_model` custom attribute.
- Frontend: `ProtonCustomer360Page.vue` + a sidebar entry + a route, added by
  `deploy/chatwoot-fork/patches/0041-customer360-admin.patch`.

### 1.3 Design

Three changes, all in a **new fork patch (`0042`)** that supersedes the UI half
of `0041`:

1. **Remove** the standalone sidebar entry and route for Customer 360.
   `ProtonCustomer360Page.vue` is deleted; its search/rendering logic moves
   into the components below.
2. **Extend the native Contacts search** so a vehicle-number query returns
   results. The native search box calls Chatwoot's contact search, which knows
   nothing about vehicles. Rather than fight it, the patch adds a small
   client-side branch: if the query doesn't look like a phone number/email/name
   match and returns nothing, call `/admin/customer360/search` and render the
   matched contacts from the vehicle branch. This keeps native behaviour
   untouched for every normal search and only adds a fallback.
3. **Add a "360" panel to the contact detail view**, rendering what the
   standalone page rendered: cross-channel conversation history (channel,
   status, created/resolved, division/concern, assignee) and RSA incidents for
   that customer's vehicles. This is where the per-case detail ask (feedback
   #14) actually gets answered.

The backend endpoint is unchanged — same URL, same permission, same payload.
Only the UI that consumes it moves. That keeps the blast radius to one patch
and means the endpoint's tests
(`features/chat/test_customer360_router.py`) stay valid as-is.

### 1.4 A known accuracy limit to carry forward, not hide

Vehicle lookup is **approximate**: Chatwoot has no vehicle-number field, so the
conversation side matches `vehicle_model`, not a plate. A search for `WXY 1234`
finds RSA incidents for that plate but will not find that customer's WhatsApp
conversation unless the plate happens to appear in `vehicle_model`. Two ways to
close it, both out of scope here:

- store a `vehicle_no` custom attribute on conversations/contacts and populate
  it (cheap, needs an operator habit or a bot question), or
- get plates from a DMS (Package F).

The UI must not imply completeness. Show an explicit note when the vehicle
branch returns conversations only via model matching.

### 1.5 Testing

- Backend: unchanged, existing suite must stay green.
- Frontend: the fork has no test harness; verification is manual —
  (a) phone search returns the contact plus history,
  (b) vehicle search returns RSA incidents,
  (c) an unknown value returns empty, not an error,
  (d) the Customer 360 sidebar icon is gone and nothing 404s,
  (e) a user without `customer360.view` sees Contacts working normally with the
  360 panel absent, not a broken page.

### 1.6 Build constraint (applies to every fork patch)

The Chatwoot SPA source is **not** in this checkout — patches are `git apply`-ed
onto upstream at image-build time, and this sandbox cannot clone upstream. A
patch that edits native files (`ContactsView`, the contacts sidebar panel) must
be authored where upstream `v4.15.1` source is available, or reconstructed from
the diff context in existing patches. Budget for this; it is the single most
common way this kind of task stalls.

---

## Part 2 — WhatsApp video understanding

### 2.1 Goal

A customer sends a video of their car on WhatsApp; the AI understands it the
same way it already understands a voice note or a photo. This also closes the
credibility gap flagged in feedback #26, where the presenter told Proton live
that video works while the engineering docs said it doesn't.

### 2.2 Current state

`agent/app/services/orchestrator.py::_process_via_chat_agent` (around line 585)
already walks the trailing incoming messages and, when
`whatsapp_media_understanding_enabled` is on, pulls **the first audio and the
first image** attachment, fetches the bytes, base64-encodes them, and passes
them to `ProtonConfigClient.chat_turn`. The backend's
`features/chat/service.py` turns those into `types.Part.from_bytes` parts for
Gemini (`service.py:471-479`). There is no video branch anywhere in that chain.

### 2.3 Design

The change is one new branch mirroring the existing two, threaded through three
files:

1. `agent/app/services/orchestrator.py` — add `video_base64` / `video_mime_type`,
   populated from the first `file_type == "video"` attachment, exactly like the
   audio and image branches (first-one-only, same YAGNI rule). Include it in the
   `not text and ... is None` short-circuit so a caption-less video isn't dropped.
2. `agent/app/clients/` — `ProtonConfigClient.chat_turn` gains the two new
   optional kwargs and forwards them.
3. `backend/.../features/chat/router.py` (`TurnRequest`, ~line 61) and
   `features/chat/service.py::handle_turn` (~line 416) — accept the fields and
   append a video `Part`.

### 2.4 Size limits — the one real design decision

- WhatsApp caps inbound video at **16 MB**.
- Gemini inline request data is capped around **20 MB**.

16 < 20, so **inline bytes are sufficient and the Files API is not needed.**
Still, add an explicit guard: if the fetched video exceeds a configured
`whatsapp_video_max_bytes` (default 16 MB), skip the video, log it, and let the
turn proceed on text alone rather than sending an oversized request that fails.

Long videos are the cost risk, not the size risk: a 60-second clip is many
tokens. Ship behind the existing `WHATSAPP_MEDIA_UNDERSTANDING_ENABLED` flag —
no new flag; the operator already opted into media understanding — but log
video turns distinctly so cost is attributable.

### 2.5 Testing

Follows the existing pattern in `agent/tests/` — `respx`-stubbed attachment
fetch, injected Gemini client:

- a video attachment with no caption produces a turn with a video part;
- an oversized video is skipped, logged, and does not break the turn;
- a turn with both a video and an image sends both (they're independent slots);
- flag off → no fetch attempt at all, byte-identical to today;
- a fetch failure degrades to text-only rather than raising (fail-open, per the
  background-task invariant in `CLAUDE.md`).

### 2.6 Out of scope

- Video on the **website/web-widget** channel (feedback #26 mentioned both;
  the widget path is a different upload flow and deserves its own change).
- Video in the phone/IVR channel (meaningless) and in the KB ingest path
  (feedback #2, separate gap).
- Frame extraction, thumbnails, or storing video anywhere. Bytes go to Gemini
  and are not persisted by us.

---

## 2.7 Build status — 2026-08-04

**The video half is code-complete and merged to `dev-yuda`** (commits `7562bfc`
… `d965f22`). Agent suite 270 passed; backend suite 1391 passed, 1 skipped.
Both feature flags default off, so the committed code is inert until enabled.

### What the final whole-feature review caught

Per-task reviews all passed, but the whole-feature review found two Critical
defects living in the seams between tasks. Both were verified by running the
code, and both are fixed:

1. **Inline media was persisted in and replayed from the ADK session.** The
   `Runner` had no artifact service, so a video blob was appended to the
   session and re-sent to Gemini on every later turn. On tenants using the
   Firestore session store — which `deploy/tenants/default.env` sets — the
   whole session is rewritten as one document, so a ~785 KB video breached
   Firestore's 1 MiB limit and the conversation broke permanently. Latent for
   audio and images (a voice note is ~60 KB); video made it certain. Fixed
   with a `BlobFreeSessionService` decorator: the persisted copy carries a text
   placeholder while the live session keeps the real media, so the current turn
   still reaches Gemini intact. Covers all three media types.
2. **The 16 MB cap was arithmetically wrong.** base64 inflates ~1.335×, so
   16 MB became ~21.4 MB against Gemini's ~20 MB inline limit — videos between
   roughly 15 and 16 MB passed the guard and were then rejected by Gemini,
   exactly what the guard existed to prevent. Lowered to 14 MB, with the false
   rationale corrected in `agent/app/config.py` and `deploy/tenants/example.env`
   and a test that pins the arithmetic, not just the number.

Also fixed: the per-turn media budget now spans audio + image + video together
rather than guarding video alone.

### Open follow-ups (not blockers)

- **Voice-channel history loses meaning.** `handle_voice_turn` sends one audio
  part and no text, so from the next turn the model sees a placeholder instead
  of what the caller said. Multi-turn voice coherence degrades. The fix is
  cheap because `_transcribe_audio` already produces the text: put the
  transcription into the stored history, or carry it in the placeholder. Worth
  doing before the phone channel is used in anger.
- **`whatsapp_video_max_bytes` now governs total media**, so its drop log names
  a *video* setting while explaining an *image* drop. Rename to
  `whatsapp_media_max_bytes` with a pydantic alias fallback rather than a hard
  break, which would invalidate deployed tenant env files.

### Not executed — needs a different environment

Tasks 6 and 7 (the Contacts/360 merge fork patch, and the Cloud Build +
tenant deploy) were not run: this sandbox cannot reach github.com to clone
upstream Chatwoot `v4.15.1`, and deploying is an out-of-sandbox operation.
They need a machine with GitHub access and deploy rights.

## 3. Definition of done for Package B

Contacts is the single lookup surface with a working 360 panel and no
standalone menu item; a WhatsApp video gets a substantive answer from the bot
on `proton`; `agent/` tests pass; the coverage doc's items #1/#9/#14/#26 are
re-marked with what was actually verified by hand.
