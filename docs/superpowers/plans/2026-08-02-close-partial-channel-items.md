# Close Out Partial Channel UI Items — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the language-matching bug, the FAQ-upload 404, and add a
pending-doc bot fallback; then deploy those plus the already-built
hierarchical-categories and WhatsApp-media-understanding features live to the
`proton` tenant on the production VM.

**Architecture:** Three small, independent code fixes in `agent/` and
`backend/` (Python, pytest/respx), one new Chatwoot fork patch (Vue, applied
at image-build time), then a sequence of VM operations (SSH, env edits,
container redeploys, one live API provisioning script) against the running
`proton` tenant.

**Tech Stack:** Python 3 (FastAPI, pytest, respx, httpx), Vue 3 (Chatwoot
fork patch), Docker Compose, gcloud, Cloud Build.

## Global Constraints

- Every code change must be covered by a new or updated test in the same
  task; don't move to the next task with a red test suite.
- `agent/` tests run via `cd agent && pytest`; `backend/` tests via
  `cd backend/apps/backend && uv run pytest src/`.
- Never build the Chatwoot custom image on the production VM — off-VM
  Docker build or Cloud Build only (per `deploy/chatwoot-fork/README.md`).
- VM: instance `crm-ticketing`, zone `asia-southeast2-a`, project
  `lv-playground-genai`. SSH via `gcloud compute ssh crm-ticketing
  --zone=asia-southeast2-a --project=lv-playground-genai --command="..."`.
- Tenant being changed: `proton`. Never touch `default` or `wahchan` tenant
  config as part of this plan.
- Fail-open pattern: any new backend-call code in `agent/` must return a
  safe default (e.g. `False`/`None`) on error, never raise, matching every
  existing method on `ProtonConfigClient`.

---

### Task 1: Fix the WhatsApp text-bot language override (agent)

**Files:**
- Modify: `agent/app/services/orchestrator.py:64-96`
- Test: `agent/tests/test_orchestrator_persona_prompt.py`

**Interfaces:**
- Produces: `orchestrator.LANGUAGE_MATCH_INSTRUCTION` (str constant) — the
  exact sentence that must always appear in the built prompt regardless of
  persona overrides. No other task consumes this directly.

- [ ] **Step 1: Write the failing tests**

Replace the file's contents with:

```python
# test_orchestrator_persona_prompt.py
from app.services.orchestrator import LANGUAGE_MATCH_INSTRUCTION, SYSTEM_PROMPT, _build_system_prompt


def test_none_persona_returns_verbatim() -> None:
    assert _build_system_prompt(None) == SYSTEM_PROMPT


def test_empty_persona_returns_verbatim() -> None:
    assert _build_system_prompt({"instructions": "", "guardrails": [], "language": ""}) == SYSTEM_PROMPT


def test_instructions_override_base_but_keep_language_match() -> None:
    out = _build_system_prompt({"instructions": "You are Ana.", "guardrails": [], "language": ""})
    assert out.startswith("You are Ana.")
    assert SYSTEM_PROMPT not in out
    assert LANGUAGE_MATCH_INSTRUCTION in out


def test_guardrails_and_language_appended() -> None:
    out = _build_system_prompt({"instructions": "", "guardrails": ["No prices"], "language": "Bahasa Melayu"})
    assert out.startswith(SYSTEM_PROMPT)  # default base kept, already has LANGUAGE_MATCH_INSTRUCTION
    assert "## Guardrails" in out and "- No prices" in out
    assert (
        "Prefer Bahasa Melayu when the customer's language is unclear, but "
        "always match the language the customer writes in." in out
    )


def test_instructions_and_language_both_set_keep_language_match() -> None:
    out = _build_system_prompt(
        {"instructions": "You are Ana.", "guardrails": [], "language": "Bahasa Melayu"}
    )
    assert out.startswith("You are Ana.")
    assert LANGUAGE_MATCH_INSTRUCTION in out
    assert "Prefer Bahasa Melayu when the customer's language is unclear" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent && pytest tests/test_orchestrator_persona_prompt.py -v`
Expected: FAIL — `ImportError: cannot import name 'LANGUAGE_MATCH_INSTRUCTION'`
and/or assertion failures on the new wording.

- [ ] **Step 3: Implement the fix**

In `agent/app/services/orchestrator.py`, replace the `SYSTEM_PROMPT` constant
and `_build_system_prompt` function (currently lines 64-96) with:

```python
LANGUAGE_MATCH_INSTRUCTION = "Always reply in the same language the customer is using."

SYSTEM_PROMPT = (
    "You are a support agent for the company, handling a live customer "
    "conversation. Decide exactly one action by calling a function: "
    "send_reply to answer the customer directly, escalate_to_ticket if this "
    "needs a human specialist or can't be resolved from the conversation "
    "alone, or handoff_to_human for anything else you're unsure about. Keep "
    "replies short, friendly, and strictly grounded in what the conversation "
    "actually says — never invent facts, prices, policies, or commitments "
    "you can't verify from it. " + LANGUAGE_MATCH_INSTRUCTION
)


def _build_system_prompt(persona: dict | None) -> str:
    """Compose the agent-bot decision prompt from an assistant persona.

    None or all-empty persona -> the module SYSTEM_PROMPT verbatim (byte-identical
    default). Otherwise: base = instructions if set else SYSTEM_PROMPT; then append
    a Guardrails section and a language-preference line when present.

    LANGUAGE_MATCH_INSTRUCTION is always present in the output, even when custom
    instructions replace SYSTEM_PROMPT — operators routinely forget to restate it,
    and dropping it caused the bot to answer in the wrong language (WA-2/IVR-4).
    """
    if not persona:
        return SYSTEM_PROMPT
    instructions = (persona.get("instructions") or "").strip()
    guardrails = [g for g in (persona.get("guardrails") or []) if str(g).strip()]
    language = (persona.get("language") or "").strip()
    if not instructions and not guardrails and not language:
        return SYSTEM_PROMPT
    parts = [instructions or SYSTEM_PROMPT]
    if instructions:
        parts.append(LANGUAGE_MATCH_INSTRUCTION)
    if guardrails:
        parts.append("## Guardrails\n" + "\n".join(f"- {g}" for g in guardrails))
    if language:
        parts.append(
            f"Prefer {language} when the customer's language is unclear, but "
            "always match the language the customer writes in."
        )
    return "\n\n".join(parts)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent && pytest tests/test_orchestrator_persona_prompt.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Run the full agent suite to check for regressions**

Run: `cd agent && pytest -q`
Expected: PASS, no new failures. (If any other test asserted the old
`"Always reply in {language}."` wording, update it to match the new
`"Prefer {language} when the customer's language is unclear..."` text.)

- [ ] **Step 6: Commit**

```bash
git add agent/app/services/orchestrator.py agent/tests/test_orchestrator_persona_prompt.py
git commit -m "fix(agent): never drop language-match instruction from bot persona prompt"
```

---

### Task 2: Fix the backend chat-agent language wording (backend)

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/chat/chat_persona.py:12-30`
- Test: `backend/apps/backend/src/chatbot/features/chat/test_chat_persona.py`

**Interfaces:**
- Consumes: nothing from Task 1 (separate process/repo, same fix pattern).
- Produces: no new symbols; only changes the appended wording in
  `compose_chat_agent_instruction`'s output.

- [ ] **Step 1: Write the failing test**

Update `test_guardrails_and_language_appended` in
`backend/apps/backend/src/chatbot/features/chat/test_chat_persona.py`:

```python
def test_guardrails_and_language_appended():
    out = compose_chat_agent_instruction(
        BASE, _a(guardrails=["No prices", "No promises"], language="Bahasa Melayu")
    )
    assert out.startswith(BASE)
    assert "## Guardrails" in out and "- No prices" in out and "- No promises" in out
    assert "## Language" in out
    assert (
        "Prefer Bahasa Melayu when the customer's language is unclear, but "
        "always match the language the customer writes in." in out
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/chat/test_chat_persona.py -v`
Expected: FAIL — assertion on the new wording not found (still has
"Always respond in Bahasa Melayu.").

- [ ] **Step 3: Implement the fix**

In `chat_persona.py`, change the `language` branch (currently line 28-29):

```python
    if language:
        parts.append(
            f"## Language\nPrefer {language} when the customer's language is "
            "unclear, but always match the language the customer writes in."
        )
```

(No change needed to the "drop base" concern here — this function always
keeps `base` per line 23's `parts = [base]`, and `prompts.py`'s
`AGENT_INSTRUCTION` already tells the model to reply in the customer's
language in its own "Tone" section — only the override wording was wrong.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/chat/test_chat_persona.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full backend suite to check for regressions**

Run: `cd backend/apps/backend && uv run pytest src/ -k chat_persona or -k persona`
Then: `cd backend/apps/backend && uv run pytest src/`
Expected: PASS, no new failures.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/backend/src/chatbot/features/chat/chat_persona.py backend/apps/backend/src/chatbot/features/chat/test_chat_persona.py
git commit -m "fix(backend): reword persona language line as a preference, not an override"
```

---

### Task 3: WA-4 pending-doc fallback (agent)

**Files:**
- Modify: `agent/app/clients/proton.py` (add `has_pending_kb_documents`)
- Modify: `agent/app/services/orchestrator.py:417-432` (wire in the fallback)
- Test: `agent/tests/test_proton_client.py`
- Test: `agent/tests/test_orchestrator_proton.py`

**Interfaces:**
- Produces: `ProtonConfigClient.has_pending_kb_documents(self) -> bool` —
  fail-open, returns `False` on any error/bad shape/unreachable backend.
- Produces: `orchestrator.PENDING_KB_FALLBACK_MESSAGE` (str constant).
- Consumes: `ProtonConfigClient._fetch_cached` (existing, already returns
  `None` on any failure — see `agent/app/clients/proton.py:64-77`).

- [ ] **Step 1: Write the failing client test**

Add to `agent/tests/test_proton_client.py` (same file, use existing
`_make_client()` helper and `PROTON_BASE` constant already at the top):

```python
# ---------------------------------------------------------------------------
# has_pending_kb_documents
# ---------------------------------------------------------------------------


@respx.mock
async def test_has_pending_kb_documents_true_when_any_pending():
    respx.get(f"{PROTON_BASE}/kb/knowledge").mock(
        return_value=httpx.Response(200, json={"documents": [
            {"id": "1", "title": "A", "status": "indexed"},
            {"id": "2", "title": "B", "status": "pending"},
        ]})
    )
    client = _make_client()
    assert await client.has_pending_kb_documents() is True
    await client.aclose()


@respx.mock
async def test_has_pending_kb_documents_false_when_all_indexed():
    respx.get(f"{PROTON_BASE}/kb/knowledge").mock(
        return_value=httpx.Response(200, json={"documents": [
            {"id": "1", "title": "A", "status": "indexed"},
        ]})
    )
    client = _make_client()
    assert await client.has_pending_kb_documents() is False
    await client.aclose()


@respx.mock
async def test_has_pending_kb_documents_false_on_non_2xx():
    respx.get(f"{PROTON_BASE}/kb/knowledge").mock(return_value=httpx.Response(503))
    client = _make_client()
    assert await client.has_pending_kb_documents() is False
    await client.aclose()


@respx.mock
async def test_has_pending_kb_documents_false_on_connection_error():
    respx.get(f"{PROTON_BASE}/kb/knowledge").mock(side_effect=httpx.ConnectError("boom"))
    client = _make_client()
    assert await client.has_pending_kb_documents() is False
    await client.aclose()


@respx.mock
async def test_has_pending_kb_documents_false_on_bad_shape():
    respx.get(f"{PROTON_BASE}/kb/knowledge").mock(return_value=httpx.Response(200, json={"oops": []}))
    client = _make_client()
    assert await client.has_pending_kb_documents() is False
    await client.aclose()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent && pytest tests/test_proton_client.py -k has_pending_kb_documents -v`
Expected: FAIL — `AttributeError: 'ProtonConfigClient' object has no attribute 'has_pending_kb_documents'`

- [ ] **Step 3: Implement the client method**

Add to `agent/app/clients/proton.py`, as a new method on `ProtonConfigClient`
(place it near `copilot_answer`, e.g. right after it):

```python
    async def has_pending_kb_documents(self) -> bool:
        """True if GET /kb/knowledge lists at least one document with
        status == "pending". Fail-open: any error, non-2xx, or bad shape
        (already handled inside _fetch_cached) returns False, so the caller
        just falls back to today's behavior instead of blocking on this."""
        data = await self._fetch_cached("/kb/knowledge")
        if not isinstance(data, dict):
            return False
        documents = data.get("documents")
        if not isinstance(documents, list):
            return False
        return any(
            isinstance(doc, dict) and doc.get("status") == "pending"
            for doc in documents
        )
```

- [ ] **Step 4: Run client tests to verify they pass**

Run: `cd agent && pytest tests/test_proton_client.py -k has_pending_kb_documents -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Write the failing orchestrator test**

Add to `agent/tests/test_orchestrator_proton.py` (reuse the file's existing
`CHATWOOT`/`PROTON` constants, `_payload()`, `_stub_decide()`,
`_make_proton_client()` helpers already defined at the top of the file):

```python
@respx.mock
async def test_send_reply_falls_back_to_pending_message_when_kb_pending(monkeypatch):
    from app.config import get_settings
    settings = get_settings()
    monkeypatch.setattr(settings, "kb_grounded_replies", True)
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/42").mock(
        return_value=httpx.Response(200, json=CONVERSATION_RESPONSE)
    )
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages").mock(
        return_value=httpx.Response(200, json={"payload": [
            {"message_type": 0, "content": "ada promo apa", "private": False,
             "sender": {"name": "Cust", "email": "c@x.my"}},
        ]})
    )
    respx.get(f"{PROTON}/kb/inboxes").mock(return_value=httpx.Response(200, json=INBOXES_WITH_MODE["auto"]))
    respx.post(f"{PROTON}/assist/copilot").mock(return_value=httpx.Response(200, json={"answer": ""}))
    respx.get(f"{PROTON}/kb/knowledge").mock(
        return_value=httpx.Response(200, json={"documents": [{"id": "1", "title": "A", "status": "pending"}]})
    )
    create_message = respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    monkeypatch.setattr(gemini, "decide", _stub_decide(
        gemini.Decision("send_reply", {"text": "LOCAL DRAFT"}, None, 5)
    ))
    client = _make_proton_client()
    monkeypatch.setattr(orchestrator, "get_proton_config_client", lambda: client)

    task = await orchestrator.handle_bot_event(_payload())
    await task

    import json as _json
    posted = _json.loads(create_message.calls.last.request.content)
    assert posted["content"] == orchestrator.PENDING_KB_FALLBACK_MESSAGE
    await client.aclose()


@respx.mock
async def test_send_reply_uses_local_draft_when_no_answer_and_no_pending(monkeypatch):
    from app.config import get_settings
    settings = get_settings()
    monkeypatch.setattr(settings, "kb_grounded_replies", True)
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/42").mock(
        return_value=httpx.Response(200, json=CONVERSATION_RESPONSE)
    )
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages").mock(
        return_value=httpx.Response(200, json={"payload": [
            {"message_type": 0, "content": "hi", "private": False, "sender": {"name": "C"}},
        ]})
    )
    respx.get(f"{PROTON}/kb/inboxes").mock(return_value=httpx.Response(200, json=INBOXES_WITH_MODE["auto"]))
    respx.post(f"{PROTON}/assist/copilot").mock(return_value=httpx.Response(200, json={"answer": ""}))
    respx.get(f"{PROTON}/kb/knowledge").mock(
        return_value=httpx.Response(200, json={"documents": [{"id": "1", "title": "A", "status": "indexed"}]})
    )
    create_message = respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    monkeypatch.setattr(gemini, "decide", _stub_decide(
        gemini.Decision("send_reply", {"text": "LOCAL DRAFT"}, None, 5)
    ))
    client = _make_proton_client()
    monkeypatch.setattr(orchestrator, "get_proton_config_client", lambda: client)

    task = await orchestrator.handle_bot_event(_payload())
    await task

    import json as _json
    posted = _json.loads(create_message.calls.last.request.content)
    assert posted["content"] == "LOCAL DRAFT"
    await client.aclose()
```

- [ ] **Step 6: Run test to verify it fails**

Run: `cd agent && pytest tests/test_orchestrator_proton.py -k pending -v`
Expected: FAIL — `AttributeError: module 'app.services.orchestrator' has no
attribute 'PENDING_KB_FALLBACK_MESSAGE'`

- [ ] **Step 7: Implement the orchestrator wiring**

In `agent/app/services/orchestrator.py`, add the constant near
`SYSTEM_PROMPT` (top of file):

```python
PENDING_KB_FALLBACK_MESSAGE = (
    "I'm still processing some reference material — please try again in a "
    "few minutes, or I can connect you with an agent."
)
```

Then change the KB-grounded-reply block (currently lines 417-432) to:

```python
    # KB-grounded reply: for a plain answer, source the text from the backend
    # copilot (same KB + assistant as the website) instead of the local draft.
    # Router (send_reply vs escalate/handoff) is unchanged; fail-open to draft.
    # If nothing is grounded but a KB document is still indexing, say so
    # instead of falling through to Gemini's own ungrounded guess (WA-4).
    if (
        settings.kb_grounded_replies
        and decision.action == "send_reply"
        and proton is not None
        and inbox_id is not None
    ):
        thread = _build_thread(message_list)
        if thread:
            answer = await proton.copilot_answer(
                f"chatwoot-conv-{conversation_id}", thread, inbox_id
            )
            if answer:
                decision.args["text"] = answer
            elif await proton.has_pending_kb_documents():
                decision.args["text"] = PENDING_KB_FALLBACK_MESSAGE
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `cd agent && pytest tests/test_orchestrator_proton.py -v`
Expected: PASS (all tests in the file, including the two new ones and the
pre-existing `test_send_reply_falls_back_to_local_when_copilot_none` — note
that test's `respx.post(f"{PROTON}/assist/copilot")` returns a 500, and it
does not mock `/kb/knowledge`, so `has_pending_kb_documents` will hit an
unmocked route; respx raises `AllMockedAssertionError` in that case only if
strict mode is on for the file — check by running it standalone first; if it
fails for that reason, add `respx.get(f"{PROTON}/kb/knowledge").mock(return_value=httpx.Response(200, json={"documents": []}))`
to that existing test's mocks.)

- [ ] **Step 9: Run the full agent suite**

Run: `cd agent && pytest -q`
Expected: PASS, no new failures.

- [ ] **Step 10: Commit**

```bash
git add agent/app/clients/proton.py agent/app/services/orchestrator.py agent/tests/test_proton_client.py agent/tests/test_orchestrator_proton.py
git commit -m "feat(agent): fall back to a pending-doc message when KB grounding finds nothing (WA-4)"
```

---

### Task 4: Fix the FAQ-Uploads 404 handling (new Chatwoot fork patch)

**Files:**
- Create: `deploy/chatwoot-fork/patches/0033-faq-uploads-404-handling.patch`
- Reference (not modified directly — edited in a throwaway clone, see steps):
  `app/javascript/dashboard/components/proton/KnowledgeUploads.vue`

**Interfaces:**
- Produces: a `.patch` file that `git apply --whitespace=fix`es cleanly on
  top of upstream `v4.15.1` with patches `0001`-`0032` already applied, and
  that the Dockerfile's `patches/*.patch` glob will pick up automatically
  (no Dockerfile change needed).

- [ ] **Step 1: Clone upstream and apply all existing patches**

```bash
VERSION=$(cat deploy/chatwoot-fork/UPSTREAM_VERSION)
rm -rf /tmp/proton-chatwoot-dev
git clone --depth 1 --branch "$VERSION" https://github.com/chatwoot/chatwoot.git /tmp/proton-chatwoot-dev
cd /tmp/proton-chatwoot-dev
for p in /Users/yudaadipratama/Archive/id-crm-ticketing/deploy/chatwoot-fork/patches/[0-9]*.patch; do
  echo "applying $p"
  git apply --whitespace=fix "$p" || { echo "FAILED: $p"; exit 1; }
done
```

Expected: every patch applies with no `FAILED` line printed. If one fails,
stop — that's a pre-existing conflict unrelated to this task; do not
proceed until it's investigated.

- [ ] **Step 2: Confirm the target file and current buggy handlers**

```bash
grep -n "err.status === 404\|const load = async\|const remove = async" \
  /tmp/proton-chatwoot-dev/app/javascript/dashboard/components/proton/KnowledgeUploads.vue
```

Expected output: exactly one `err.status === 404` match (inside
`onFileChange`), and `load`/`remove` functions present without a matching
404 branch in their own `catch` blocks.

- [ ] **Step 3: Edit `load()` and `remove()` to add the 404 branch**

In `/tmp/proton-chatwoot-dev/app/javascript/dashboard/components/proton/KnowledgeUploads.vue`,
change the `load` function's catch block from:

```js
  } catch (err) {
    errored.value = true;
    if (err.status === 401) {
      useAlert('Unauthorized (401) — check the backend key configuration.');
    } else if (err.status === 503) {
      useAlert('Knowledge store unavailable (503) on the backend.');
    } else {
      useAlert(`Failed to load uploads: ${err.message}`);
    }
  } finally {
```

to:

```js
  } catch (err) {
    errored.value = true;
    if (err.status === 401) {
      useAlert('Unauthorized (401) — check the backend key configuration.');
    } else if (err.status === 404) {
      useAlert(
        "Document upload isn't enabled for this workspace yet. Contact your administrator."
      );
    } else if (err.status === 503) {
      useAlert('Knowledge store unavailable (503) on the backend.');
    } else {
      useAlert(`Failed to load uploads: ${err.message}`);
    }
  } finally {
```

And change the `remove` function's catch block from:

```js
  } catch (err) {
    if (err.status === 401) {
      useAlert('Unauthorized (401) — check the backend key configuration.');
    } else if (err.status === 503) {
      useAlert('Knowledge store unavailable (503).');
    } else {
      useAlert(`Delete failed: ${err.message}`);
    }
  }
```

to:

```js
  } catch (err) {
    if (err.status === 401) {
      useAlert('Unauthorized (401) — check the backend key configuration.');
    } else if (err.status === 404) {
      useAlert(
        "Document upload isn't enabled for this workspace yet. Contact your administrator."
      );
    } else if (err.status === 503) {
      useAlert('Knowledge store unavailable (503).');
    } else {
      useAlert(`Delete failed: ${err.message}`);
    }
  }
```

- [ ] **Step 4: Regenerate the patch file**

```bash
cd /tmp/proton-chatwoot-dev
git diff -- app/javascript/dashboard/components/proton/KnowledgeUploads.vue \
  > /Users/yudaadipratama/Archive/id-crm-ticketing/deploy/chatwoot-fork/patches/0033-faq-uploads-404-handling.patch
cat /Users/yudaadipratama/Archive/id-crm-ticketing/deploy/chatwoot-fork/patches/0033-faq-uploads-404-handling.patch
```

Expected: a non-empty diff touching only the two catch blocks above.

- [ ] **Step 5: Verify the new patch applies cleanly from scratch**

```bash
rm -rf /tmp/proton-chatwoot-verify
git clone --depth 1 --branch "$VERSION" https://github.com/chatwoot/chatwoot.git /tmp/proton-chatwoot-verify
cd /tmp/proton-chatwoot-verify
for p in /Users/yudaadipratama/Archive/id-crm-ticketing/deploy/chatwoot-fork/patches/[0-9]*.patch; do
  git apply --whitespace=fix "$p" || { echo "FAILED: $p"; exit 1; }
done
echo "ALL PATCHES APPLIED CLEANLY INCLUDING 0033"
```

Expected: `ALL PATCHES APPLIED CLEANLY INCLUDING 0033` printed, no `FAILED`
lines.

- [ ] **Step 6: Clean up the throwaway clones**

```bash
rm -rf /tmp/proton-chatwoot-dev /tmp/proton-chatwoot-verify
```

- [ ] **Step 7: Commit the new patch**

```bash
git add deploy/chatwoot-fork/patches/0033-faq-uploads-404-handling.patch
git commit -m "fix(chatwoot-fork): friendly message on 404 when loading/deleting KB uploads"
```

---

### Task 5: Build and push the updated Chatwoot custom image

**Files:** none (build-only task, no repo changes).

**Interfaces:**
- Consumes: `deploy/chatwoot-fork/patches/0033-faq-uploads-404-handling.patch`
  (Task 4) via the Dockerfile's existing `patches/*.patch` glob.
- Produces: a pushed image tag, e.g.
  `asia-southeast1-docker.pkg.dev/lv-playground-genai/proton-images/proton-chatwoot:v4.15.1-custom`,
  consumed by Task 9.

- [ ] **Step 1: Submit the Cloud Build**

```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing
gcloud builds submit deploy/chatwoot-fork/ \
  --config deploy/chatwoot-fork/cloudbuild.yaml \
  --project lv-playground-genai \
  --substitutions _REGISTRY=asia-southeast1-docker.pkg.dev/lv-playground-genai/proton-images
```

Expected: build succeeds (~10-15 min), ending with the image pushed to
Artifact Registry. Note the exact tag printed at the end (should be
`<UPSTREAM_VERSION>-custom`, e.g. `v4.15.1-custom` — read
`deploy/chatwoot-fork/UPSTREAM_VERSION` if unsure).

- [ ] **Step 2: Confirm the image landed in Artifact Registry**

```bash
gcloud artifacts docker images list \
  asia-southeast1-docker.pkg.dev/lv-playground-genai/proton-images \
  --project lv-playground-genai \
  --include-tags --filter="package:proton-chatwoot"
```

Expected: the new tag appears with a recent creation timestamp.

---

### Task 6: VM recon — confirm proton tenant filename and current flag values

**Files:** none (read-only recon).

**Interfaces:**
- Produces: the exact tenant env filename and current values of
  `GEMINI_LIVE_LANGUAGE`, `KNOWLEDGE_PG_ENABLED`, `KNOWLEDGE_DATABASE_URL`,
  `CASE_TAXONOMY_JSON`, `WHATSAPP_MEDIA_UNDERSTANDING_ENABLED` — needed
  as-is by Task 7.

- [ ] **Step 1: List tenant env files on the VM**

```bash
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --project=lv-playground-genai \
  --command="ls -la /opt/platform/deploy/tenants/*.env 2>/dev/null || ls -la /opt/platform/tenants/*.env"
```

Expected: a listing including a file whose name matches the proton tenant
(e.g. `proton.env`). Note the exact path for use below.

- [ ] **Step 2: Read the current relevant values (do not print secrets beyond what's needed)**

```bash
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --project=lv-playground-genai \
  --command="grep -E '^(GEMINI_LIVE_LANGUAGE|KNOWLEDGE_PG_ENABLED|KNOWLEDGE_DATABASE_URL|CASE_TAXONOMY_JSON|WHATSAPP_MEDIA_UNDERSTANDING_ENABLED)=' <PATH_FROM_STEP_1>"
```

Expected output lines for each var (any may be absent/empty — note which).
This confirms: (a) whether `GEMINI_LIVE_LANGUAGE` is pinned to a fixed
locale (the likely IVR-4 root cause), (b) whether `KNOWLEDGE_DATABASE_URL`
is already populated (meaning only the `KNOWLEDGE_PG_ENABLED` flag needs
flipping), (c) current `CASE_TAXONOMY_JSON` and media-flag state.

**Decision point:** if `KNOWLEDGE_DATABASE_URL` is empty/absent, stop before
Task 7's `KNOWLEDGE_PG_ENABLED` change and flag it back — that means a
pgvector DB needs provisioning first (out of scope for this plan, per the
design spec).

---

### Task 7: Update the proton tenant env and redeploy agent/backend

**Files:** none in this repo (edits a file on the VM only).

**Interfaces:**
- Consumes: the tenant env path and current values from Task 6.
- Produces: a running `agent`/`backend` container pair on proton with the
  new env values live, consumed by Task 10's smoke tests.

- [ ] **Step 1: Back up the current tenant env file on the VM**

```bash
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --project=lv-playground-genai \
  --command="cp <PATH_FROM_TASK_6> <PATH_FROM_TASK_6>.bak-$(date +%Y%m%d)"
```

- [ ] **Step 2: Set `WHATSAPP_MEDIA_UNDERSTANDING_ENABLED=true`**

```bash
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --project=lv-playground-genai \
  --command="sed -i 's/^WHATSAPP_MEDIA_UNDERSTANDING_ENABLED=.*/WHATSAPP_MEDIA_UNDERSTANDING_ENABLED=true/' <PATH_FROM_TASK_6>"
```

- [ ] **Step 3: If `GEMINI_LIVE_LANGUAGE` was pinned to a fixed locale (Task 6), unset it**

Only run this if Task 6 found a non-empty value:

```bash
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --project=lv-playground-genai \
  --command="sed -i 's/^GEMINI_LIVE_LANGUAGE=.*/GEMINI_LIVE_LANGUAGE=/' <PATH_FROM_TASK_6>"
```

- [ ] **Step 4: Set `CASE_TAXONOMY_JSON` (single-line JSON, matching `config.py`'s default)**

```bash
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --project=lv-playground-genai \
  --command='sed -i "s|^CASE_TAXONOMY_JSON=.*|CASE_TAXONOMY_JSON={\"sales\":{\"label\":\"Sales\",\"subcategories\":[\"Test Drive Booking\",\"Pricing Inquiry\",\"Vehicle Availability\",\"Trade-In\",\"Financing\"]},\"aftersales\":{\"label\":\"Aftersales\",\"subcategories\":[\"Service Booking\",\"Warranty Claim\",\"Spare Parts\",\"Recall\"]},\"apps\":{\"label\":\"Apps\",\"subcategories\":[\"Login Issue\",\"App Crash\",\"Feature Request\",\"Account Sync\"]},\"charging\":{\"label\":\"Charging\",\"subcategories\":[\"Charger Fault\",\"Charging Station Locator\",\"Billing\"]},\"roadside_assistance\":{\"label\":\"Roadside Assistance\",\"subcategories\":[\"Breakdown\",\"Accident\",\"Towing\"]},\"general_enquiry\":{\"label\":\"General Enquiry\",\"subcategories\":[\"Product Info\",\"Dealer Locator\",\"Other\"]},\"complaint\":{\"label\":\"Complaint\",\"subcategories\":[\"Service Quality\",\"Product Defect\",\"Staff Conduct\",\"Other\"]}}|" <PATH_FROM_TASK_6>'
```

- [ ] **Step 5: If `KNOWLEDGE_DATABASE_URL` was already populated (Task 6), flip `KNOWLEDGE_PG_ENABLED=true`**

Only run this if Task 6 confirmed a non-empty `KNOWLEDGE_DATABASE_URL`:

```bash
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --project=lv-playground-genai \
  --command="sed -i 's/^KNOWLEDGE_PG_ENABLED=.*/KNOWLEDGE_PG_ENABLED=true/' <PATH_FROM_TASK_6>"
```

- [ ] **Step 6: Sync the updated `agent`/`backend` source and redeploy**

Per `CLAUDE.md`'s deploy notes, `agent`/`backend` images are built on the VM
from synced source (not pulled from a registry):

```bash
# From the Mac, sync this repo's agent/ and backend/ to the VM (adjust path
# to wherever /opt/platform's agent/backend source actually lives — confirm
# with the recon in Task 6 if unsure):
rsync -az --delete agent/ crm-ticketing:/opt/platform/agent/ \
  --exclude '__pycache__' --exclude '.pytest_cache'
rsync -az --delete backend/ crm-ticketing:/opt/platform/backend/ \
  --exclude '__pycache__' --exclude '.pytest_cache' --exclude '.venv'

gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --project=lv-playground-genai \
  --command="cd /opt/platform/deploy && docker compose -p proton -f docker-compose.tenant.yml --env-file <PATH_FROM_TASK_6> up -d --build backend agent"
```

- [ ] **Step 7: Check both containers came up healthy**

```bash
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --project=lv-playground-genai \
  --command="docker compose -p proton -f /opt/platform/deploy/docker-compose.tenant.yml --env-file <PATH_FROM_TASK_6> ps"
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --project=lv-playground-genai \
  --command="docker compose -p proton -f /opt/platform/deploy/docker-compose.tenant.yml --env-file <PATH_FROM_TASK_6> logs --tail=50 agent backend"
```

Expected: both `agent` and `backend` show `Up` / healthy, no tracebacks in
the last 50 log lines.

---

### Task 8: Provision the case taxonomy against live Chatwoot

**Files:** none (runs an existing script against the live API).

**Interfaces:**
- Consumes: `CASE_TAXONOMY_JSON` set in Task 7, and the proton tenant's
  Chatwoot account id + an admin API token.

- [ ] **Step 1: Get the proton account id and an admin API token**

```bash
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --project=lv-playground-genai \
  --command="grep -E '^CHATWOOT_ACCOUNT_ID=' <PATH_FROM_TASK_6>"
```

If no `CHATWOOT_ACCOUNT_ID` var exists in the env file, get the account id
from the Chatwoot UI (Settings → Account Settings → shows the numeric id in
the URL) and a Super Admin API access token from an existing admin's Chatwoot
profile settings (Profile → Access Token) instead of inventing one.

- [ ] **Step 2: Dry-run the provisioning script first**

```bash
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --project=lv-playground-genai \
  --command='CASE_TAXONOMY_JSON="$(grep ^CASE_TAXONOMY_JSON= <PATH_FROM_TASK_6> | cut -d= -f2-)" python3 /opt/platform/chatwoot-config/provision_case_taxonomy.py --chatwoot-url https://<proton-chatwoot-url> --account-id <ACCOUNT_ID> --api-token <TOKEN> --dry-run'
```

Expected: prints the two custom-attribute definitions (`case_category`,
`case_subcategory`) it would create/update, with the full flattened
subcategory list, and exits 0 with no actual API writes.

- [ ] **Step 3: Run it for real**

```bash
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --project=lv-playground-genai \
  --command='CASE_TAXONOMY_JSON="$(grep ^CASE_TAXONOMY_JSON= <PATH_FROM_TASK_6> | cut -d= -f2-)" python3 /opt/platform/chatwoot-config/provision_case_taxonomy.py --chatwoot-url https://<proton-chatwoot-url> --account-id <ACCOUNT_ID> --api-token <TOKEN>'
```

Expected: exit 0, confirms the two custom attribute definitions were
created or updated.

- [ ] **Step 4: Verify in the Chatwoot UI**

Open any conversation in the proton Chatwoot instance → right sidebar →
Conversation Information → Custom Attributes. Confirm `case_category` and
`case_subcategory` appear as pickable fields with the expected values (e.g.
`Sales`, `Sales: Trade-In`).

---

### Task 9: Deploy the new Chatwoot image to the proton tenant

**Files:** none (VM redeploy only).

**Interfaces:**
- Consumes: the image tag pushed in Task 5.

- [ ] **Step 1: Update `CHATWOOT_IMAGE` in the proton tenant env**

```bash
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --project=lv-playground-genai \
  --command="sed -i 's|^CHATWOOT_IMAGE=.*|CHATWOOT_IMAGE=asia-southeast1-docker.pkg.dev/lv-playground-genai/proton-images/proton-chatwoot:v4.15.1-custom|' <PATH_FROM_TASK_6>"
```

(Confirm the exact version string matches what Task 5 actually pushed —
use `v4.15.1-custom` only if that's what `UPSTREAM_VERSION` said; otherwise
substitute the real tag.)

- [ ] **Step 2: Pull and restart only the Chatwoot services**

```bash
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --project=lv-playground-genai \
  --command="cd /opt/platform/deploy && docker compose -p proton -f docker-compose.tenant.yml --env-file <PATH_FROM_TASK_6> pull chatwoot-rails chatwoot-sidekiq"
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --project=lv-playground-genai \
  --command="cd /opt/platform/deploy && docker compose -p proton -f docker-compose.tenant.yml --env-file <PATH_FROM_TASK_6> up -d --force-recreate chatwoot-rails chatwoot-sidekiq"
```

- [ ] **Step 3: Verify Chatwoot comes back healthy**

```bash
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --project=lv-playground-genai \
  --command="docker compose -p proton -f /opt/platform/deploy/docker-compose.tenant.yml --env-file <PATH_FROM_TASK_6> logs --tail=80 chatwoot-rails chatwoot-sidekiq"
```

Expected: no crash loop, Rails boot completes, Sidekiq starts processing.
Then open the proton Chatwoot URL in a browser and confirm the dashboard
loads and Settings → Knowledge → Uploads opens without the raw 404 alert.

---

### Task 10: Live smoke test — all 5 items on proton

**Files:** none (manual/live verification, matching
`docs/analysis/crm-channel-ui-testing-guide.md`'s existing walkthroughs).

- [ ] **Step 1: WA-2 language fix** — send a WhatsApp message to the proton
  test number in Bahasa; confirm the bot replies in Bahasa (per WA-2's
  existing UI walkthrough in the testing guide).
- [ ] **Step 2: IVR-4 language fix** — place a test call and speak Bahasa
  throughout; confirm the AI responds in Bahasa (per IVR-4's walkthrough).
- [ ] **Step 3: FAQ upload** — open Settings → Knowledge → Uploads; confirm
  it loads without an error alert (or, if `KNOWLEDGE_PG_ENABLED` is still
  off for some reason, confirms the friendly "not enabled" message instead
  of a raw 404); upload a small text doc and confirm it reaches `indexed`.
- [ ] **Step 4: WA-4 pending fallback** — immediately after uploading in
  Step 3 (while still `pending`), ask the WhatsApp bot a question that
  isn't covered by any existing indexed content; confirm it responds with
  the "still processing" message rather than a generic/invented answer.
- [ ] **Step 5: WA-8 categories** — resolve a bot conversation and confirm
  a `case_category`/`case_subcategory` custom attribute gets set (per
  WA-8's existing walkthrough), or set one manually and confirm the
  dropdown shows the full flattened list.
- [ ] **Step 6: WA-12 media understanding** — from the WhatsApp test number,
  send a real voice note, then a photo; confirm the bot responds based on
  the content of each (transcription/description), not silence.
- [ ] **Step 7: Record results** — note pass/fail for each of the 6 checks
  above; for anything that fails, do not roll further changes forward
  silently — report back with the specific failure before considering this
  plan complete.

---

## Self-review notes

- Spec coverage: item 1 (language) → Tasks 1-2; item 2 (FAQ 404) → Tasks
  4-5, 9; item 3 (WA-4 fallback) → Task 3; item 4 (categories) → Tasks 7-8;
  item 5 (media flag) → Task 7; rollout/VM plan → Tasks 6-10. All five
  spec items and the rollout section are covered.
- The IVR voice-path "fix" is genuinely just a config check (Task 6/7) per
  the spec — no code task exists for it because none is warranted.
- Tasks 6-9 use `<PATH_FROM_TASK_6>` as a literal placeholder for the real
  path discovered at execution time — this is a runtime value that cannot
  be known before Task 6 runs, not a missing design decision; every command
  that needs it is explicit about which prior step's output to substitute.
