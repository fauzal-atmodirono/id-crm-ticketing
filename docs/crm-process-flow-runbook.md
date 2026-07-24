# CRM Process Flow — Operator Runbook

**What this is.** The "CRM process flow" is the Proton customer-service SOP from
`docs/CRM Process Flow (1).xlsx`, implemented as an automated **conversation
lifecycle** in the first-party **`agent/`** service (with per-channel first-response
SLAs in the vendored **`backend/`**). It is **gated off by default** — this runbook
covers what it does, how to wire it, and how to turn it on per tenant.

Source design: `docs/superpowers/specs/2026-07-23-conversation-lifecycle-autoclose-design.md`
and `docs/superpowers/specs/2026-07-24-sop-completion-categorization-emailack-channelsla-design.md`.

---

## 1. What it does

A per-conversation state machine, layered on top of Chatwoot's own status, driven by
a background **scanner** (`agent/app/services/lifecycle_scanner.py`, ticks every
`LIFECYCLE_SCAN_INTERVAL_SECONDS`) and Chatwoot **webhook** events.

```
conversation_created ─► post AI disclaimer / welcome   (email inbox → SOP auto-ack instead)
        │
     ACTIVE ──idle WARN_MIN──► IDLE_WARNED ──CLOSE_GRACE──► AWAITING_RESOLUTION
        ▲                                                     │  "Is this resolved? YES / NO"
        └──── agent replies / customer sends a message ───────┘
                                                              │ YES  (or CONFIRM_GRACE idle)
                                                      AWAITING_SURVEY
                                                              │  "Rate our AI 1–5"  (AI-perf
                                                              │   or agent-perf variant)
                                                           CLOSED  (+ optional category_* label)
```

- **Disclaimer / welcome** on a new conversation (chat channels). Uses the assistant's
  configured `welcome_message` if set, else the SOP disclaimer text.
- **Email auto-ack** — on an **email** inbox, instead of the AI disclaimer the bot posts
  a once-per-thread acknowledgement (`EMAIL_AUTOACK_TEMPLATE`). Dedup is structural
  (`conversation_created` fires once per conversation).
- **Idle handling** — after `IDLE_WARN_MINUTES` of no activity the bot posts a warning;
  after a further `IDLE_CLOSE_GRACE_MINUTES` it posts the auto-close + resolution prompt.
- **Resolution gate** — customer replies **NO** → conversation reopens to `ACTIVE`;
  **YES** (or silence past `CONFIRM_GRACE_MINUTES`) → moves to the survey.
- **Rating survey** — asks the customer to rate 1–5. Two variants: **AI-performance**
  (bot-handled) and **agent-performance** (a human resolved it — triggered by the
  `conversation_status_changed → resolved` webhook). Ratings are recorded to `ai_actions`
  (and the backend NPS/CSAT store).
- **Auto-categorization** — at the genuine bot-resolution points, applies one
  `category_<slug>` label chosen by Gemini from `LIFECYCLE_CATEGORY_LABELS`.
- **Per-channel first-response SLA** — the `backend/` SLA engine breaches the ack timer
  using a per-channel minutes override (`SLA_ACK_MINUTES_BY_CHANNEL_JSON`) instead of the
  global hours.

**Business hours** are read natively from each Chatwoot inbox's working hours. If an
inbox has working hours **disabled**, it is treated as always in-hours (safe default);
out-of-hours idle uses `LIFECYCLE_IDLE_CLOSE_OUT_OF_HOURS_GRACE_MINUTES`.

---

## 2. Architecture / where it runs

| Piece | Lives in | Trigger |
|---|---|---|
| Disclaimer / email-ack, resolution + survey parsing | `agent/app/services/lifecycle.py` | Chatwoot webhooks (`conversation_created`, `conversation_status_changed`) + customer replies |
| Idle warn → auto-close scanner | `agent/app/services/lifecycle_scanner.py` | background loop, started from `agent/app/main.py` lifespan **when `LIFECYCLE_ENABLED=true`** |
| State store | `conversation_lifecycle` table (agent DB) + `lifecycle_state` Chatwoot custom attribute | — |
| Auto-categorization | `agent/app/services/categorize.py` (Gemini) | bot-resolution points |
| Per-channel ack SLA | `backend/.../features/chat/sla.py` | backend SLA scan |

**The whole flow needs the `agent/` service connected to the tenant's Chatwoot** (API
token + a webhook). Without that link the lifecycle can neither receive events nor post
messages.

---

## 3. Prerequisite — wire the agent to Chatwoot (one-time per tenant)

Some tenants (e.g. proton) were set up with the agent intentionally unwired. Before
enabling the flow:

1. **API token** — put a Chatwoot access token in `deploy/tenants/<tenant>.env`:
   ```
   CHATWOOT_API_TOKEN=<an admin/agent access token>
   CHATWOOT_ACCOUNT_ID=1
   ```
   (Chatwoot UI: avatar → Profile Settings → Access Token. Or reuse the account
   administrator's token.)

2. **Webhook** — register a webhook in the tenant's Chatwoot pointing at the agent, and
   subscribe the events the flow needs:
   - URL: `http://<prefix>agent.<PUBLIC_IP>.nip.io/webhooks/chatwoot`
   - Events: **`conversation_created`**, `conversation_updated`, `conversation_status_changed`
   - Copy the webhook's server-generated `secret` into `CHATWOOT_WEBHOOK_SECRET`.

   > Chatwoot's SSRF filter rejects private/internal hosts for webhook delivery — use the
   > public `nip.io` agent hostname, not the internal `<tenant>-agent:8000`.

---

## 4. Configuration reference (agent service)

All in `deploy/tenants/<tenant>.env`. Defaults shown; the feature is a byte-identical
no-op while `LIFECYCLE_ENABLED=false`.

| Var | Default | Purpose |
|---|---|---|
| `LIFECYCLE_ENABLED` | `false` | **Master switch.** Starts the scanner + webhook handlers. |
| `LIFECYCLE_SCAN_INTERVAL_SECONDS` | `60` | Scanner tick. |
| `LIFECYCLE_IDLE_WARN_MINUTES` | `10` | Idle → warning. |
| `LIFECYCLE_IDLE_CLOSE_GRACE_MINUTES` | `5` | Warning → auto-close + resolution prompt. |
| `LIFECYCLE_IDLE_CLOSE_OUT_OF_HOURS_GRACE_MINUTES` | `0` | Extra grace when the inbox is out of business hours. |
| `LIFECYCLE_CONFIRM_GRACE_MINUTES` | `10` | Wait for YES/NO before defaulting to resolved. |
| `LIFECYCLE_SURVEY_ENABLED` | `true` | Ask the 1–5 rating survey. |
| `LIFECYCLE_DISCLAIMER_ENABLED` | `true` | Post the AI disclaimer/welcome on new chat conversations. |
| `LIFECYCLE_AUTO_CATEGORIZE` | `false` | Apply a `category_*` label at bot-resolution. |
| `LIFECYCLE_CATEGORY_LABELS` | `""` | Comma-separated category slugs the bot may choose from (must exist as Chatwoot labels). Empty → categorize no-ops. |
| `EMAIL_AUTOACK_ENABLED` | `false` | Once-per-thread email acknowledgement. |
| `EMAIL_AUTOACK_TEMPLATE` | *(SOP text)* | Override the ack body. |

**Backend (per-channel ack SLA)** — in the same tenant env (read by `backend`):

| Var | Example | Purpose |
|---|---|---|
| `SLA_ACK_MINUTES_BY_CHANNEL_JSON` | `{"whatsapp":2,"call":0.33,"email":240,"facebook":120,"instagram":120}` | Per-channel first-response minutes; empty → global `SLA_RESPONSE_HOURS`. `Channel::Api→call` is a deployment assumption — adjust per tenant. |

---

## 5. Activation steps

```bash
cd /opt/platform/deploy      # on the VM

# 1. Edit tenants/<tenant>.env: add the CHATWOOT_* wiring (§3) + the LIFECYCLE_* /
#    EMAIL_AUTOACK / SLA_ACK_MINUTES_BY_CHANNEL_JSON vars (§4).

# 2. (Recommended) In Chatwoot: Settings → Inboxes → <inbox> → Business Hours,
#    set hours + an out-of-office reply. Skippable — unset = always in-hours.

# 3. Apply — recreate the agent (and backend if you changed the SLA var):
docker compose -p <tenant> -f docker-compose.tenant.yml \
  --env-file tenants/<tenant>.env up -d agent backend
```

The scanner logs `lifecycle_scanner_started` on boot when enabled.

---

## 6. Verify / smoke test

1. **Agent up:** `curl http://<prefix>agent.<PUBLIC_IP>.nip.io/healthz` → `{"status":"ok"}`.
2. **Webhook registered:** `GET /api/v1/accounts/1/webhooks` lists the agent URL with the
   three subscriptions.
3. **Disclaimer:** start a new conversation in a chat inbox → the AI disclaimer/welcome
   posts within a few seconds. On an email inbox with `EMAIL_AUTOACK_ENABLED=true`, the
   ack template posts instead.
4. **Idle → close:** leave the conversation idle past `WARN + CLOSE_GRACE` minutes (lower
   them temporarily to test) → warning, then auto-close + "resolved? YES/NO".
5. **Resolution / survey:** reply `NO` → reopens; reply `YES` → 1–5 survey → `CLOSED`,
   and (if `LIFECYCLE_AUTO_CATEGORIZE=true`) a `category_*` label is applied.
6. **Agent-resolved survey:** have a human resolve a conversation → the agent-performance
   survey posts.

Watch: `docker logs -f <tenant>-agent` (look for `lifecycle_*` log lines).

---

## 7. Disable / rollback

Set `LIFECYCLE_ENABLED=false` (and `EMAIL_AUTOACK_ENABLED=false`) in the tenant env and
`up -d agent`. The scanner stops and all handlers become no-ops — behavior returns to
exactly what it was before. The `conversation_lifecycle` table and `lifecycle_state`
attributes are harmless if left in place.

---

## 8. Notes & gotchas

- Enabling this on a **live** tenant means the bot starts posting disclaimers/surveys into
  real customer conversations — validate on a non-production tenant first when possible.
- Caddy strips the underscore `api_access_token` header; API clients should also send the
  dash form `Api-Access-Token` (the agent client already does).
- Auto-categorization needs a working `GEMINI_API_KEY` in the agent env; if absent it
  fails open (no label) — the rest of the flow still runs.
- `welcome_message` per-assistant text (if configured via the Knowledge → Assistants UI)
  overrides the default disclaimer.
