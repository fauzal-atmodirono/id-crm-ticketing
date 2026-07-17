# CRM Backend Migration to Chatwoot + Zammad — Slice 1 (MVP) Design

**Status:** Draft for review
**Date:** 2026-07-16
**Context:** The Zendesk free trial expired; the ticketing/CRM backend moves to a Chatwoot (CRM/chat) + Zammad (ticketing) instance a colleague deployed on a GCE VM (`crm.<ip>.nip.io`, `tickets.<ip>.nip.io`, from `github.com/fauzal-atmodirono/id-crm-ticketing`). Proton already ships a `ChatwootZammadAdapter` and `crm_provider="chatwoot"`, so this is a re-point + harden, not a rebuild.

## Decisions (approved 2026-07-16)

- **Migration is decomposed into 4 slices**; this spec is **Slice 1 only** (MVP). Later slices: (2) adapter parity + bidirectional sync, (3) instrumentation port (ConversationLog + Zammad metrics sync + CSAT/NPS on Chatwoot), (4) Chatwoot-native live-agent handoff.
- **Topology:** Proton FE + BE **stay on Cloud Run**; Chatwoot + Zammad **stay on the GCE VM as-is**. Proton reaches them over their public URLs; Chatwoot's webhook targets Proton's Cloud Run URL. Proton **replaces the colleague's `agent` service as the chat brain** (webhooks point at Proton, not `agent.<ip>.nip.io`).
- **Multi-tenancy = Option 1 (one Proton deployment per customer).** Proton code stays **single-tenant**; each customer gets its own Cloud Run service with its own Chatwoot Account id + Zammad group + tokens + webhook URL. **This slice targets PROTON e.MAS only**; the user replicates the deployment for other tenants (Wah Chan, Bank Jago) later.
- **Isolation model (informational, not built here):** one **Chatwoot Account per customer** + one **Zammad Group per customer** (Chatwoot Teams and URL paths are NOT isolation boundaries). Per-customer subdomains are optional cosmetic branding. Provisioning these accounts/groups and re-scoping the existing Wah Chan agent (A3) are **instance-admin tasks, out of scope for this Proton slice.**
- **Gemini via Vertex AI + ADC** — no `GEMINI_API_KEY`.

## Prerequisites (provided by user/CTO, not built here)

- A **PROTON e.MAS Chatwoot Account** exists on the instance, with: the numeric `account_id`, an **agent access token** (for Proton to send replies + read), and the account's **inbox** the customer chats into.
- A **Zammad** API token (Zammad ticketing stays group `Users` for slice 1; per-customer group is Slice 2).
- The Cloud Run runtime **service account** can be granted **Vertex AI User**, and `aiplatform.googleapis.com` is enabled in the project.

## Scope of Slice 1

Get a **secure, ADC-based Proton** running on Cloud Run against the e.MAS Chatwoot Account + Zammad, with the core loop working: inbound Chatwoot message → AI turn → Chatwoot reply; AI escalation → Zammad ticket. Everything else is explicitly deferred.

---

## A. Architecture & data flow

```
 Customer ─► Chatwoot (VM, e.MAS Account) ──webhook: message_created──► (HTTPS)
                                                                          │
                                          Proton BE (Cloud Run) /webhooks/chatwoot
                                                  │  handle_turn (Gemini via Vertex/ADC)
                                    ┌─────────────┼───────────────────────┐
                                    ▼             ▼                        ▼
                         Chatwoot API (reply)   Zammad API (escalate→ticket)   [conversation_log = NoOp]
                         http://crm.<ip>.nip.io  http://tickets.<ip>.nip.io
```

- **Inbound:** a Chatwoot **account webhook** subscribed to `message_created` posts to Proton's Cloud Run `/webhooks/chatwoot`. Proton filters to `event == "message_created"` AND `message_type == "incoming"` (ignores its own outgoing replies → no loop). Session id = `chatwoot-conv-{conversation.id}`.
- **Reply:** `ChatwootZammadAdapter.send_message` → `POST /api/v1/accounts/{account_id}/conversations/{id}/messages` with the agent token (`message_type: outgoing`).
- **Escalate:** existing handoff path → `TicketingPort.create_ticket` → Zammad `POST /api/v1/tickets` (group `Users` for slice 1).
- **Gemini:** ADK Runner + genai Client run against **Vertex AI** because `google_genai_use_vertexai=true`; auth is **ADC** (the Cloud Run SA). No API key.

**Webhook mechanism note (risk to confirm live):** Proton uses the Chatwoot **account webhook** (`message_created`). If this Chatwoot version does not HMAC-sign account webhooks, we fall back to a Chatwoot **agent bot** pointed at the same endpoint (agent bots carry an HMAC secret and emit `message_created`). Decide by the live smoke test (§E). Either way the handler + verification below is unchanged except which secret/header is configured.

---

## B. Configuration (env on the Cloud Run service)

Existing settings (already in `config.py`), set for e.MAS:

```
CRM_PROVIDER=chatwoot
CHATWOOT_API_URL=http://crm.<ip>.nip.io
CHATWOOT_API_TOKEN=<e.MAS agent access token>
CHATWOOT_ACCOUNT_ID=<e.MAS numeric account id>
CHATWOOT_ENABLED=true
ZAMMAD_API_URL=http://tickets.<ip>.nip.io
ZAMMAD_API_TOKEN=<zammad token>
ZAMMAD_ENABLED=true
GOOGLE_GENAI_USE_VERTEXAI=true
VERTEX_PROJECT_ID=<gcp project>
VERTEX_LOCATION=us-central1
```

**New setting (the only config addition):**
```
CHATWOOT_WEBHOOK_SECRET=<secret>   # HMAC secret for /webhooks/chatwoot; unset ⇒ verification skipped (dev)
```

**ADC:** grant the Cloud Run runtime SA `roles/aiplatform.user`; ensure `aiplatform.googleapis.com` is enabled. No `GEMINI_API_KEY` needed while `GOOGLE_GENAI_USE_VERTEXAI=true`.

---

## C. Code change — Chatwoot webhook HMAC verification (the only real code)

Today `/webhooks/chatwoot` (`router.py`, handler `chatwoot_webhook`, registered `router.py:273`) performs **no signature verification** — unacceptable on a public URL. Add it, mirroring the existing Twilio pattern (which reads the raw body for signature) and the colleague's proven scheme against this same Chatwoot version.

**New module** `src/chatbot/features/chat/chatwoot_signature.py` (pure, unit-tested), mirroring `twilio_signature.py`:

```python
def verify_chatwoot_signature(secret: str, timestamp: str, raw_body: bytes, signature: str, *, now: float, max_skew_s: int = 300) -> bool:
    """True if `signature` == 'sha256=' + HMAC_SHA256(secret, f"{timestamp}." + raw_body),
    and |now - int(timestamp)| <= max_skew_s. hmac.compare_digest for the compare."""
```

- Scheme: header `X-Chatwoot-Signature: sha256=<hex>` where `<hex> = HMAC_SHA256(secret, f"{timestamp}.".encode() + raw_body)`; `timestamp` from header `X-Chatwoot-Timestamp` (unix seconds); reject skew > 300 s. (Matches `id-crm-ticketing/agent/app/security.py`.)

**Handler change** (`chatwoot_webhook`): accept `Request` (not the pre-parsed pydantic model), read `raw = await request.body()`, and if `settings.chatwoot_webhook_secret` is set, verify `verify_chatwoot_signature(...)` using the two headers → `HTTPException(401)` on failure (mirrors `zendesk_support_webhook` / `twilio_whatsapp_webhook` gating: unset secret ⇒ skip). Then `ChatwootWebhookPayload.model_validate_json(raw)` and proceed exactly as today (filter `message_created` + `incoming`, run turn, reply).

**Config:** add `chatwoot_webhook_secret: str = ""` to `Settings` (near the other webhook secrets) + `.env.example` line.

No other Proton code changes in slice 1. `conversation_log_port` remains `NoOpConversationLog` on the Chatwoot path (accepted — see §D).

---

## D. Explicitly deferred (stubbed in slice 1)

- **conversation_log_port = NoOp** → no per-conversation ticket mirroring, **no metrics capture, no CSAT/NPS surveys.** (Slice 3.)
- **Zammad ticket quality:** customer stays hardcoded (`customer@example.com`), group `Users`, classification tags/priority don't flow into Zammad. (Slice 2.)
- **Bidirectional sync** (contact/conversation links, ticket-state back-sync to Chatwoot). (Slice 2.)
- **Live-agent handoff** via Chatwoot (reopen-for-human), replacing Sunshine/SSE. (Slice 4.)
- **Multi-tenant Proton** (single service serving many accounts). Not needed — Option 1 replicates the deployment.

Slice 1 must not regress the existing Zendesk path (still selectable via `CRM_PROVIDER=zendesk`).

---

## E. Testing

**Unit (pure, TDD):** `verify_chatwoot_signature` — valid signature passes; wrong signature fails; expired/future timestamp (> 300 s skew) fails; malformed header fails. `hmac.compare_digest` used (no timing leak).

**Handler (FastAPI TestClient, mirror `test_router.py` harness):** POST `/webhooks/chatwoot` with a correctly-signed `message_created`/`incoming` body + `CHATWOOT_WEBHOOK_SECRET` set → 200 and the orchestrator turn runs (mock orchestrator asserts a Chatwoot reply send); wrong signature → 401; secret unset → verification skipped (200).

**Config test:** `chatwoot_webhook_secret` default `""`.

**Backend baselines unchanged:** full suite green (currently 419), zero new mypy/ruff over the existing baseline (mypy 3 / ruff 1 pre-existing).

**Live smoke-test runbook (manual, documented in §F):**
1. Boot check: Cloud Run logs show Vertex/ADC mode (no API-key path), `GET /` healthy.
2. In the e.MAS Chatwoot account, send a customer message in the inbox → Proton replies in the conversation (AI answer).
3. Trigger an escalation (KB-miss / "talk to human") → a Zammad ticket appears.
4. Confirm the webhook signature validates (403/401 count is zero in logs) — **this is where we confirm the account-webhook-vs-agent-bot fork from §A.**

---

## F. Deploy runbook (doc: `docs/runbooks/chatwoot-zammad-migration.md`)

- `gcloud run deploy` with the §B env vars (via `--set-env-vars` / Secret Manager for tokens + `CHATWOOT_WEBHOOK_SECRET`).
- Grant the Cloud Run SA `roles/aiplatform.user`; enable `aiplatform.googleapis.com`.
- Configure the Chatwoot **account webhook** (Settings → Integrations → Webhooks): URL = Proton Cloud Run `/webhooks/chatwoot`, event `message_created`; retrieve the auto-generated signing secret (`GET /api/v1/accounts/{id}/webhooks`) → set as `CHATWOOT_WEBHOOK_SECRET`. (Fallback: register an agent bot pointed at the same URL if account webhooks aren't signed.)
- Run the §E smoke test.
- **Per-customer replication (Option 1):** repeat `gcloud run deploy` as a new service with that customer's `CHATWOOT_ACCOUNT_ID` + tokens + webhook. (User does this for Wah Chan/Bank Jago.)

---

## Open questions — resolved

- **Deploy target:** Cloud Run for Proton (not in-VM compose) — confirmed.
- **Multi-tenancy:** Option 1 (deploy-per-customer); e.MAS first — confirmed.
- **Webhook signing scheme:** mirror the colleague's `security.py` (account webhook, sha256 + timestamp); confirm live, fall back to agent-bot if unsigned — documented as a smoke-test decision.

## Out of scope (later slices / instance-admin)
- Slices 2–4 (parity, instrumentation, handoff). Instance multi-tenancy provisioning (A1/A2) + Wah Chan agent re-scope (A3).
