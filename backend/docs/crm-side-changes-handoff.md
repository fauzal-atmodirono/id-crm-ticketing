# CRM-side changes — handoff for the `id-crm-ticketing` repo / CRM window

**Context:** these are changes made to the **CRM side** (Chatwoot/Zammad, the VM,
Caddy, Chatwoot admin config) while integrating the Proton conversational-AI
backend. They live OUTSIDE the `proton-conversational-ai` repo and should be
reconciled / version-controlled on the CRM side. Date: 2026-07-17.

**Tenant:** dedicated **Proton** tenant on the shared VM.
- VM: `crm-ticketing`, external IP `34.50.103.151`, zone `asia-southeast2-a`,
  project `lv-playground-genai`. Three tenants (`default`, `proton`, `wahchan`)
  behind one `platform-infra-caddy-1`.
- Chatwoot: `http://proton.crm.34-50-103-151.nip.io` — account **1 "Proton Demo"**,
  inbox **1 "Proton API"** (Channel::Api, the handoff-console inbox).
- Zammad: `http://proton.tickets.34-50-103-151.nip.io` — group **"Users"**.
- Agent service: `http://proton.agent.34-50-103-151.nip.io` — **NOT used** (see §4).
- Our backend (Cloud Run): `https://proton-backend-247165654737.asia-southeast1.run.app`

---

## 1. Chatwoot (dedicated) admin config we created

**Webhooks** (account 1):
- id **1** → `…/webhooks/chatwoot?token=<CHATWOOT_WEBHOOK_SECRET>`, subscriptions
  `[message_created, conversation_status_changed]`. Points at our backend.
- NOTE: the **agent-service webhook is intentionally NOT registered** on this
  instance (we do not use the agent-service→Zammad sync — see §4).

**Dashboard Apps** (account 1) — these add menu entries in Chatwoot:
- id **1 "FAQ Admin"** → `http://proton.crm.34-50-103-151.nip.io/apps/faq-admin/?apiKey=<FAQ_ADMIN_API_KEY>`
- id **2 "FAQ Assist"** → `http://proton.crm.34-50-103-151.nip.io/apps/agent-assist/?apiKey=<QA_API_KEY>`

**Labels contract** (what our backend writes on escalated conversations):
- Always: `ai-escalation` (marker) + dimension labels `category_*`, `subcat_*`,
  `division_*`, `dept_*`, `sla_<int>` (from AI classification; read by our metrics).
- `sla_minutes` also set as a conversation custom attribute.
- **`escalate` label is NOT applied** (we ticket Zammad directly — see §4). Do not
  wire anything to auto-ticket on `ai-escalation`, or you'll double-ticket.

---

## 2. VM / Caddy changes (belongs in the CRM deploy repo)

On the VM, to host the two dashboard apps under `proton.crm`:
- **Files added:** `/opt/platform/deploy/caddy/tenants/apps/faq-admin/index.html`
  and `/opt/platform/deploy/caddy/tenants/apps/agent-assist/index.html`
  (source of these files: `apps/chatwoot-agent-app/` and `apps/chatwoot-faq-admin/`
  in the `proton-conversational-ai` repo — decide who owns them long-term).
- **Caddy config edited:** `/opt/platform/deploy/caddy/tenants/proton.caddy` — the
  `proton.crm` site block now has two `handle_path` static routes before the
  Chatwoot `handle{}` fallback:
  ```caddy
  handle_path /apps/faq-admin/*    { root * /etc/caddy/tenants/apps/faq-admin ; file_server }
  handle_path /apps/agent-assist/* { root * /etc/caddy/tenants/apps/agent-assist ; file_server }
  handle { reverse_proxy proton-chatwoot-rails:3000 }
  ```
  Backups exist as `proton.caddy.bak.<timestamp>`. Applied via
  `caddy validate` + `caddy reload` on `platform-infra-caddy-1`.
- Leftover temp files in the VM home dir (safe to delete): `~/proton.caddy.new`,
  `~/faq-admin.html`, `~/agent-assist.html`, `~/index.html`.
- **This Caddy change is not version-controlled on the CRM side yet** — please
  fold it into the `id-crm-ticketing` Caddy tenant template.

---

## 3. Zammad (dedicated)

- No admin changes beyond using the default **"Users"** group.
- Our backend creates tickets directly via `POST /api/v1/tickets` with
  `customer_id: "guess:<email>"` (auto-creates the customer). Priority mapped
  from urgency. A private note linking the ticket is posted back to the Chatwoot
  conversation.

---

## 4. Integration contract (important for the CRM agent)

- **Our backend is the brain.** It owns: the AI conversation, the live agent
  bridge (agent replies in Chatwoot stream back to the customer via our SSE),
  and **direct Zammad ticket creation** on complaints.
- **The CTO agent service (`proton.agent…`) is NOT used for Proton.** Do not wire
  it to escalate Proton conversations / turn labels into Zammad tickets — our
  backend already does that. (This avoids double tickets.)
- Live chat happens in **Chatwoot**; the Zammad ticket is a back-office record,
  created only for **complaints** (negative sentiment / high urgency). Plain
  "talk to a human" stays Chatwoot-only.

---

## 5. Cleanup items (CRM side)

- **Old SHARED instance** (`crm.34-50-103-151.nip.io`, pre-cutover): our webhook
  **id 3** still points at our backend — should be **deleted** now that Proton is
  on the dedicated tenant. Also test conversations there (conv 273/274/275/277/278)
  and Zammad tickets (#61384/61385/61386/61388) are test artifacts.
- **Dedicated instance test artifacts:** Chatwoot conversations from test handoffs
  (inbox 1) and Zammad tickets **#21002 (DIAG), #21003** + auto-created test
  customers. Safe to delete.
- One seed live-FAQ entry (e.MAS battery warranty) — that's in OUR Firestore, not
  the CRM; delete via the FAQ Admin app if unwanted.

---

## 6. Secrets / keys (values NOT in this doc)

- `CHATWOOT_WEBHOOK_SECRET` — in the Chatwoot webhook id 1 URL + our Cloud Run env.
- `FAQ_ADMIN_API_KEY`, `QA_API_KEY` — in the Dashboard App URLs + our Cloud Run env.
- Chatwoot user access token + Zammad API token — in our Cloud Run Secret Manager
  (`chatwoot-api-token` v2, `zammad-api-token` v2) + local `.env`.
