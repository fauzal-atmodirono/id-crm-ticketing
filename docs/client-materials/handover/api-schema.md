# API schema chart

**Audience:** whoever integrates with, operates or audits this platform's HTTP
surface. **Scope:** every endpoint the two first-party services expose. Chatwoot's
own Rails API is upstream and out of scope here.

---

## How this document was produced, and why that matters

Every row below was read out of a **running application**, not out of a plan, a
task report or a router file. The method, so it can be repeated:

1. `chatbot.main.bootstrap_application()` was called twice — once with the
   default environment, once with the dependency-gated features switched on
   (RBAC, RSA, the pgvector KB, presence, taxonomy, alert rules, freshness,
   control items, translation) — and `app.openapi()`'s `paths` read from each.
   The difference between the two runs is the "gating" column.
2. Each route's authorisation was derived by walking its dependency tree and
   reading the permission string out of each `require_permission` closure, so the
   permission column is what the app enforces rather than what a docstring
   claims.

**This is not pedantry.** Several routers in this repository were written,
unit-tested against their own throwaway `FastAPI()` instance, and mounted
nowhere — `build_status_router`, `translate_router.py`, `rules_router.py` and
`recording_router.py`, four separate times. Every one had a green test suite and
returned 404 to every real caller. A chart derived from the router files would
have listed all four as available. So:

| Verified fact | Value |
|---|---|
| Backend endpoints reachable, default configuration | **81 paths** |
| Backend endpoints reachable with the dependency-gated features on | **112 paths** |
| Endpoints gated by a `require_permission` route dependency | **39** |
| Agent-service endpoints | **3** |
| Router files inspected | **37** |
| Routers currently mounted nowhere | **0** |
| Routers found unmounted during this verification, since fixed | **1** (see below) |

### The one router this verification found unmounted

**`features/chat/phone/recording_router.py`** — `GET /calls/{conversation_id}/recording`,
P11 task 1. When this document was first verified, `main.py` included it
nowhere; its only `include_router` calls were inside its own test module, which
builds a throwaway `FastAPI()`. So its tests passed while the endpoint 404ed on
every real deployment and `CALL_RECORDING_RETRIEVAL_ENABLED` had no consumer any
operator could reach.

**It is now mounted** (review fix `1fc11a2`), and `test_p11_wiring.py` proves it
through the real app by asserting the discriminating pair: **401 rather than 404
unauthenticated**, and the flag's own 404 for a caller who *would* be permitted —
which is the only way to tell "flag off" apart from "unmounted". A re-verification
against the current tree confirms `/calls/{conversation_id}/recording` in the live
route table, and that **all 37 router files are now reachable**.

**One limitation survives the mount, and it is the substantive one:** the handler
reads an in-process registry (`_RECORDING_RETENTIONS`) that nothing in production
writes to, so against a real conversation it answers the "no recording" state
regardless of the setting. The Chatwoot custom-attribute read that would populate
it is owed. **A mounted endpoint is not a working feature**, and this row is the
clearest example in the platform.

### One router that only looks unmounted

**`features/metrics/freshness_router.py`** — `GET /metrics/freshness` appears
nowhere in `main.py` and is nevertheless **reachable**, because
`build_metrics_anomaly_router` includes it as a sub-router and `main.py` mounts
that. Confirmed present in the live route table.

Recorded because a grep of `main.py` alone would have reported it as a second
unmounted router, and **a false positive in this document is as damaging as a
false negative** — it would send someone to fix something that works, and it
would undermine the true finding sitting next to it. It is the reason the
verification boots the app instead of reading the source.

### One permission a dependency scan cannot see

**`POST /routing/assign` enforces `routing.reassign` inside the handler, not as
a route dependency**, and only on the branch where the caller names an explicit
`agent_id`. The automatic-selection branch keeps the shared-secret check. This
is deliberate and documented in the router; it is called out here because the
programmatic derivation above reports this endpoint as ungated, and it is not.
It is the only such case found.

---

## Authentication and authorisation model

There are three mechanisms, and which one applies depends on the endpoint **and
on whether the tenant has enabled RBAC**.

### 1. `require_permission` — the admin and agent-action endpoints

`features/authz/deps.py`. Deliberately default-preserving:

| Tenant state | Behaviour |
|---|---|
| `RBAC_ENABLED=false` (the default) | Falls back to a **shared-secret check** on the `x-api-key` header, compared with `hmac.compare_digest` against `FAQ_ADMIN_API_KEY` and `PROTON_BACKEND_KEY`. The permission string is not consulted. |
| `RBAC_ENABLED=true` with `RBAC_DATABASE_URL` set | Resolves the caller's Chatwoot identity from the `x-chatwoot-access-token` / `x-chatwoot-client` / `x-chatwoot-uid` triplet, looks up their permission set, and returns **403** if the permission is absent. |

**Any resolution failure is a 401 deny, never a silent allow** — missing token,
invalid token, network error to Chatwoot, or RBAC on with no repository
configured. That last case is explicitly belt-and-braces in the code, because
falling through would mean an unauthenticated write.

The operational consequence worth stating plainly to the client: **on a tenant
with RBAC off, every one of the 39 permission-gated endpoints is protected by a
single shared secret, and every holder of that secret is effectively an
administrator.** Per-role restriction begins when RBAC is enabled.

### 2. Shared-secret `x-api-key` — the knowledge, metrics and admin-adjacent endpoints

The `/kb/*` authoring endpoints and several others check the same shared secret
directly. Same consequence: one secret, no roles.

### 3. HMAC signature — the webhook receivers

Inbound webhooks are not key-authenticated; they are signature-verified.
`/webhooks/chatwoot` on the agent service verifies `sha256=` over
`f"{timestamp}."+body` within a 300-second skew window, and the two receivers
use **different secrets** (`CHATWOOT_WEBHOOK_SECRET` and `CHATWOOT_BOT_SECRET`).
Twilio and Sunshine receivers verify their own providers' signatures.

### Endpoints that are deliberately unauthenticated

`GET /metrics/dashboard` and `GET /metrics/freshness` are unauthenticated by
design: they return sync timing, configuration and aggregates, with no PII and
no per-conversation detail. The `/metrics/insights` family is not. If the client's
security review objects to the two open endpoints, that is a configuration
conversation and not a code change — but they should know the endpoints exist
rather than discover them.

---

## The permission registry

Twenty-two application permissions are seeded by `features/authz/seed.py`. Six
are granted to the built-in `agent` role; the rest are administrator-only.

| Permission | Grants |
|---|---|
| `alerts.set_own_preferences` | Set your own alert-rule preferences *(agent role)* |
| `cases.view` | View the case record panel *(agent role)* |
| `cases.manage` | Edit the case record panel *(agent role)* |
| `knowledge.edit` | Edit Knowledge Base content *(agent role)* |
| `presence.set_own_status` | Set your own availability status *(agent role)* |
| `translation.use` | Translate a customer message for reading *(agent role)* |
| `alerts.manage` | Manage account-level alert-rule defaults |
| `audit.view` | View the audit log |
| `call_recording.listen` | Listen to / retrieve a call recording |
| `customer360.view` | View the Customer 360 lookup |
| `escalation.manage` | Manage PIC / dealer escalation routing |
| `integration.manage` | Manage DMS/TSP integration settings |
| `kb.ingest` | Trigger KB document ingestion |
| `persona.edit` | Edit assistant persona / instructions |
| `roles.manage` | Manage roles and permission assignments |
| `routing.reassign` | Reassign a conversation to a chosen agent |
| `sla.manage` | Manage SLA policies |
| `taxonomy.manage` | Manage the case taxonomy tree and category mappings |
| `workforce.view` | View the workforce / presence dashboard |
| `workforce.manage` | Edit the status catalogue and set other agents' statuses |

A separate `chatwoot.*` namespace mirrors Chatwoot's own role keys
(`chatwoot.conversation_manage`, `chatwoot.contact_manage`, …). The live tenant
returns 12 such keys where `seed.py` defines 20 — an unmatched key renders under
"Other" in the Roles & Permissions page rather than being dropped.

**Reading the two columns together matters.** An agent-facing action gated on an
administrator permission is a dead feature — that mistake was made once in this
programme and caught in review. `presence.set_own_status` and
`alerts.set_own_preferences` exist as separate agent-role permissions for
exactly that reason, distinct from `workforce.manage` and `alerts.manage`.

---

## Endpoints by capability

**Reading the columns.** `Auth` is the enforced permission, or `x-api-key` for
the shared-secret check, or `signature` for a verified webhook, or `open` for a
deliberately unauthenticated endpoint. `Mounted` is `always` when the endpoint
exists in the default configuration, or names the setting or dependency the
mount requires — a `GATED` endpoint returns FastAPI's own 404 with no handler
reachable until its condition is met, which is different from an endpoint that
answers `{"disabled": true}`.

### Conversation and channel intake

| Endpoint | Auth | Mounted |
|---|---|---|
| `POST /webhooks/chatwoot` | signature | always |
| `POST /webhooks/twilio-whatsapp` | signature | always |
| `POST /webhooks/sunshine` | signature | always |
| `POST /webhooks/phone/recording-status` | signature | always |
| `POST /chat/turn` | x-api-key | always |
| `GET /chat/stream/{session_id}` | x-api-key | always |
| `POST /voice/turn` | x-api-key | always |
| `POST /voice/tts` | x-api-key | always |
| `POST /voice/phone/incoming` | signature | always |
| `POST /voice/phone/token` | x-api-key | always |

Six further `POST /webhooks/zendesk*` endpoints
(`zendesk`, `-email`, `-handback`, `-sla-escalation`, `-support`) are mounted
unconditionally and are **legacy**: `CRM_PROVIDER` defaults to `chatwoot`, and on
a Chatwoot tenant nothing calls them. They are listed because an auditor will
find them and ask, and because "mounted but never called" is a different
statement from "not present".

### AI assist (agent-facing)

| Endpoint | Auth | Mounted |
|---|---|---|
| `POST /assist/suggest` | x-api-key | always |
| `POST /assist/summarize` | x-api-key | always |
| `POST /assist/ask` | x-api-key | always |
| `POST /assist/copilot` | x-api-key | always |
| `POST /assist/translate` | `translation.use` | always (returns `{"disabled": true, "reason": …}` when `TRANSLATION_ENABLED` is off) |

`/assist/translate` is mounted unconditionally on purpose: a 404 would leave the
fork's Translate button unable to distinguish "this tenant has not enabled
translation" from "the backend is the wrong version". A disabled response carries
no `translation` field, so there is nothing to mistake for a successful
translation.

### Knowledge base and FAQ

| Endpoint | Auth | Mounted |
|---|---|---|
| `GET /kb/suggest` | x-api-key | always |
| `GET,POST /kb/faq` · `PUT,DELETE /kb/faq/{entry_id}` | x-api-key | always |
| `POST /kb/faq/bulk` | x-api-key | always |
| `POST /kb/feedback` | x-api-key | always |
| `GET /kb/documents` | x-api-key | always |
| `GET,POST /kb/assistants` · `GET,PUT,DELETE /kb/assistants/{assistant_id}` | x-api-key | always |
| `GET,PUT /kb/settings` | x-api-key | always |
| `GET,POST /kb/tools` · `PUT,DELETE /kb/tools/{slug}` · `PUT /kb/tools/builtins/{name}` | x-api-key | always |
| `GET,POST /kb/scenarios` · `PUT,DELETE /kb/scenarios/{scenario_id}` | x-api-key | always |
| `GET /kb/inboxes` · `PUT /kb/inboxes/{inbox_id}` · `GET,PUT /kb/inboxes/{inbox_id}/timing` | x-api-key | always |
| `GET /kb/knowledge` · `GET,DELETE /kb/knowledge/{document_id}` | x-api-key | **GATED**: `KNOWLEDGE_PG_ENABLED` + `KNOWLEDGE_DATABASE_URL` + an available embedder |
| `POST /kb/knowledge/text` · `POST /kb/knowledge/file` | x-api-key | same gate |

The `/kb/knowledge` gate has a third condition worth knowing: with the flag on
but **no embedder available** (no Gemini SDK or credentials), the router is
deliberately *not* mounted, so uploads 404 rather than every document silently
failing to embed.

### Escalation and cases

| Endpoint | Auth | Mounted |
|---|---|---|
| `POST /escalation/notify` | x-api-key | requires the Chatwoot CRM provider |
| `POST /escalation/acknowledge` | x-api-key | same |
| `GET /escalation/contacts` · `GET /escalation/departments` | x-api-key | same |
| `GET,PATCH /cases/{conv_id}/fields` | `cases.view` / `cases.manage` | **GATED**: RBAC + a Chatwoot client |
| `GET /cases/{ticket_id}/audit` | x-api-key | always |
| `GET,POST /rsa/incidents` · `GET,PATCH,DELETE /rsa/incidents/{incident_id}` | x-api-key | **GATED**: `RSA_ENABLED` + `RSA_DATABASE_URL` |
| `GET /rsa/incidents/aggregate` · `GET /rsa/incidents/export` | x-api-key | same |

### Routing, presence and workforce

| Endpoint | Auth | Mounted |
|---|---|---|
| `GET /routing/agents` | x-api-key | always |
| `POST /routing/assign` | x-api-key, **plus `routing.reassign` in-handler on the explicit-`agent_id` branch** | always |
| `GET,POST /routing/priorities` · `PUT,DELETE /routing/priorities/{agent_id}` | x-api-key | always |
| `GET,POST /routing/presence/status` | `presence.set_own_status` | **GATED**: `PRESENCE_CUSTOM_STATUSES_ENABLED` |
| `GET /routing/presence/statuses` | `presence.set_own_status` | same |
| `PUT /routing/presence/statuses/{key}` | `workforce.manage` | same |
| `GET /admin/workforce` | `workforce.view` | **GATED**: `PRESENCE_TRACKING_ENABLED` |

`ROUTING_ENABLED` does **not** gate any endpoint's existence — it governs
automatic agent *selection* inside the handler. The configuration endpoints are
mounted regardless, because they back the routing-admin UI.

The presence endpoints being gated rather than mounted-and-disabled is
deliberate: a status *write* answering 200 `{"disabled": true}` is a shape a UI
could read as a change that worked.

### Reporting and metrics

All unauthenticated or shared-secret; none is permission-gated.

| Endpoint | Auth | Mounted |
|---|---|---|
| `GET /metrics/dashboard` | open | always |
| `GET /metrics/freshness` | open | always — 404s while `DASHBOARD_FRESHNESS_ENABLED` is off |
| `GET /metrics/callcenter` · `/metrics/lifecycle` · `/metrics/by-tag` · `/metrics/after-hours` | x-api-key | always |
| `GET /metrics/departments` · `/metrics/case-aging` · `/metrics/sla-buckets` · `/metrics/volume-by-type` · `/metrics/dealer-escalation` | x-api-key | always |
| `GET /metrics/{departments,case-aging,sla-buckets,volume-by-type,dealer-escalation}/export` | x-api-key | always |
| `GET /metrics/export` | x-api-key | always |
| `GET /metrics/anomalies` | x-api-key | always |
| `GET /metrics/anomalies/hourly` | x-api-key | always — answers `{"status": "unavailable", "hours": []}` without a warehouse |
| `GET /metrics/ai-cost` | x-api-key | always — answers `read_status: "unavailable"` until the `token_usage` views exist |
| `GET /metrics/control-items` | x-api-key | always — 5 of 14 rows report `no_data` with a reason |
| `POST /qa/label` | x-api-key | always |

**Three of these endpoints deliberately return an "unavailable" status rather
than a number**, and that is a contract, not a defect: with no warehouse there
is no evidence of zero spend, an empty anomaly list would read as "no anomalies",
and a zero on a control item would assert a performance figure the platform
cannot measure. Anyone building a dashboard on these must render the unavailable
state rather than coercing it to `0`.

### Administration

| Endpoint | Auth | Mounted |
|---|---|---|
| `GET /authz/check` · `GET /authz/permissions` | x-api-key | **GATED**: `RBAC_ENABLED` + `RBAC_DATABASE_URL` |
| `GET /authz/permission-registry` | `roles.manage` | same |
| `GET,POST /authz/roles` | `roles.manage` | same |
| `GET,POST /authz/roles/{role_id}/permissions` · `DELETE /authz/roles/{role_id}/permissions/{permission_key}` | `roles.manage` | same |
| `POST,DELETE /authz/roles/{role_id}/assign` · `GET /authz/roles/{role_id}/users` | `roles.manage` | same |
| `GET /admin/audit` | `audit.view` | same |
| `GET,PUT /admin/sla-policy/default` · `GET,PUT /admin/sla-policy/inbox/{inbox_id}` | `sla.manage` | same |
| `GET /admin/escalation/pics` · `PUT,DELETE /admin/escalation/pics/{department}` | `escalation.manage` | same |
| `GET /admin/escalation/dealers` · `PUT,DELETE /admin/escalation/dealers/{dealer}` | `escalation.manage` | same |
| `GET,PUT /admin/integrations/dms` · `POST /admin/integrations/dms/test` | `integration.manage` | same |
| `GET /admin/customer360/search` | `customer360.view` | **GATED**: RBAC + a Chatwoot client **+ `rsa_repo`** |
| `GET /admin/taxonomy/tree` | x-api-key | always |
| `GET /admin/taxonomy/coverage` | x-api-key | always — but answers only when **both** `TAXONOMY_ADMIN_ENABLED` **and** `CATEGORY_DEPARTMENT_MAPPING_ENABLED` are on |
| `POST /admin/taxonomy/node` · `POST /admin/taxonomy/node/{key}/retire` | `taxonomy.manage` | always |
| `GET /alerts/rules/defaults` · `GET /alerts/rules/mine` · `PUT,DELETE /alerts/rules/mine/{event}` | `alerts.set_own_preferences` | always |
| `PUT /alerts/rules/defaults/{event}` | `alerts.manage` | always |
| `GET /calls/{conversation_id}/recording` | `call_recording.listen` | see the unmounted-router note above |
| `GET /tasks/mine` | x-api-key | always |
| `GET /` | open | always — liveness only; returns provider names and the model id |

**`/admin/customer360/search` has a third mount condition that is easy to miss:**
it requires `rsa_repo`, so a tenant with RBAC on and RSA logging off does not get
Customer 360 at all. `main.py` logs `customer360_prerequisites_missing` at boot
in that case rather than failing silently, but the endpoint 404s.

**The taxonomy read endpoints are not permission-gated while the write endpoints
are.** That is intentional (the tree is not privileged information) and is the
same boundary `/alerts/rules/defaults` and the status catalogue read draw.

**`/admin/taxonomy/coverage` needs two flags, and until recently needed only
one.** `CATEGORY_DEPARTMENT_MAPPING_ENABLED` had **no consumer anywhere** —
`example.env` documented it as the switch that mounts this endpoint, nothing read
it, and the report answered on `TAXONOMY_ADMIN_ENABLED` alone, so an operator
flipping the documented flag saw no change in either direction. Fixed in
`1fc11a2` with a real-app test on each state. Recorded here because it is the
third instance in this programme of a documented switch that did nothing, and the
first two were `FAQ_SUGGESTION_POPUP_ENABLED` and `INBOUND_ALERTS_ENABLED`.

---

## Agent service

Three endpoints, and the shape is deliberate: verify → dedupe → return 200 →
dispatch to a background task. The slow Chatwoot and Gemini calls never run in
the request path.

| Endpoint | Auth | Notes |
|---|---|---|
| `GET /healthz` | open | liveness |
| `POST /webhooks/chatwoot` | HMAC over `CHATWOOT_WEBHOOK_SECRET` | Contact, conversation-status and `conversation_updated` events |
| `POST /webhooks/chatwoot/bot` | HMAC over `CHATWOOT_BOT_SECRET` | The agent-bot decision path |

Plus a static mount at `/apps/` serving the in-Chatwoot dashboard apps, present
only when the `APPS_DIR` directory exists.

**The two receivers use different secrets.** Configuring both webhooks in
Chatwoot with the same secret is a working misconfiguration — one of the two will
401 every delivery, and the symptom is a feature that silently never fires.

---

## Regenerating this document

It is **not** generated, unlike `configuration.md`, and that is a known
weakness: it will drift. The verification is cheap to repeat and should be
repeated after any change to `main.py`:

```bash
cd backend/apps/backend
GOOGLE_API_KEY=test-key uv run python - <<'EOF'
from chatbot.main import bootstrap_application
app = bootstrap_application()
for path in sorted(app.openapi()["paths"]):
    print(path)
EOF
```

Run it once with the default environment and once with the gated features on,
and diff both against the tables above. The live OpenAPI document is also served
at `GET /openapi.json`, with Swagger UI at `/docs` and ReDoc at `/redoc` — those
are the authoritative request and response schemas; this document is the map.

**What no version of this document can tell you:** whether an endpoint does what
its name says against real infrastructure. Nothing here has been exercised
against real BigQuery, Gemini, Twilio, Postgres or Firestore. See
`../governance/qa-plan.md` for what the test suites do and do not prove, and
`../governance/risk-register.md` for the consequences.
