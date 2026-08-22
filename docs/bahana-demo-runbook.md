# Runbook — Bahana Sekuritas WhatsApp demo

**Demo date:** Monday 2026-08-24
**Owner:** Yuda
**Spec:** `docs/superpowers/specs/2026-08-22-bahana-personalization-design.md` (§5 = Phase 0 scope, §7 = production security/compliance — cite this live during the demo, see §10 below)
**Plan:** `docs/superpowers/plans/2026-08-22-bahana-demo-phase0.md`

This is the console/credential work the code in this repo can't do for you.
Everything with a code fix already landed on `dev-yuda`; this runbook is what's
left — provisioning verification, Chatwoot/Twilio wiring, seeding, and the
demo itself.

> **Read this first.** §1.1 and §1.2 are external actions that sit in a queue
> once started (Meta review, Twilio balance) — start those before anything
> else in this document, even before reading the rest of it.

---

## 0. Where things stand — DONE, do not re-run

Verified 2026-08-22, same day this runbook was written.

| Piece | State |
|---|---|
| Tenant `bahana` | **live** at `https://bahana.crm.34-50-103-151.nip.io` — `302` to `/installation/onboarding`, valid Let's Encrypt cert (issued 2026-08-22, expires 2026-11-20) |
| Containers | all six healthy: `bahana-chatwoot-rails`, `bahana-chatwoot-sidekiq`, `bahana-agent`, `bahana-backend`, `bahana-redis`, `bahana-memcached` |
| VM disk | 35% used, 37 G free (was 83% before a cache prune — headroom is not a concern) |
| `proton` / `aeon360` | re-verified HTTP 200 after the shared Caddy reload this provisioning required — no collateral damage |
| RBAC bootstrap bug | found + worked around (§2.1) |
| AI credentials bug | found + worked around (§2.2) |
| HTTP-only Caddy vhost bug | found + worked around (§2.3) |
| Missing `PROTON_BACKEND_URL`/`PROTON_BACKEND_KEY` bug | found + worked around (§2.4) |
| `purge`'s RSA sweep is unconditional | expected traceback, documented — not fixable without enabling RSA (§2.5) |
| Task 5 code change (`agent`'s customer-context prompt) | merged to `dev-yuda` (commit `7d2b9aa`), **not yet deployed to the VM** — §5 below |

VM access: `gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --project=lv-playground-genai`. GCE instance `crm-ticketing`, zone `asia-southeast2-a`, project `lv-playground-genai`, public IP `34.50.103.151`.

None of the above needs to be redone. Everything from here on is either a
long-lead external action or console/credential work with no code path.

---

## 1. Pre-flight — start now, external and time-boxed

Start §1.1 and §1.2 immediately, regardless of what else is in progress —
both sit in someone else's queue once submitted. §1.3 is a one-minute
double-check to do before §3.3/§3.4, not a queue item.

### 1.1 WhatsApp business display name (Meta review — start ASAP)

`+16292843510` is a registered WhatsApp sender on WABA `1095367113150862`,
status **Online**, 80 MPS, in the **PT Devoteam Cloud Services** Twilio
account. Its business display name currently reads **"Demo Main Account"** —
this is what the prospect sees in WhatsApp during the call.

Changing it goes through **Meta review**, not an instant Twilio setting.
Start the change today. If it hasn't cleared by Monday, it's survivable:
narrate it as a demo sender when the WhatsApp thread opens, don't let it be
a surprise.

### 1.2 Twilio balance top-up

Balance was **$6.90** at last check — ample for a demo's worth of messages,
but a balance that hits zero mid-meeting stops every send with no local
symptom to debug. Top it up before Monday, not during the rehearsal.

### 1.3 Confirm you're touching the right number

Before configuring any webhook: `+16292843510` (the demo number) is in the
**PT Devoteam Cloud Services** Twilio account. AEON360's live production
number, `+16823993949`, is **not** in that account's WhatsApp sender list —
it lives in a different account or subaccount entirely.

Re-check this in the Twilio console before touching any webhook config. The
failure mode being guarded against is repointing a live customer's inbound
webhook by picking the wrong number off a list — do this deliberately, not
from memory.

---

## 2. Provisioning bugs already found and worked around

Informational — **do not re-run provisioning for these.** bahana is already
patched around all three. Documented here so (a) you don't waste time
re-diagnosing a symptom that's already explained, and (b) the next tenant
provisioned hits the same three bugs, because none of them is fixed in the
repo yet.

### 2.1 `RBAC_BOOTSTRAP_ADMIN_USER_ID` ships empty — repo bug, will hit the next tenant too

`deploy/tenants/example.env` line 234 ships `RBAC_BOOTSTRAP_ADMIN_USER_ID=`
(empty). The backend's pydantic `Settings.rbac_bootstrap_admin_user_id` is
typed `int | None = None`, and an env var that's present-but-empty fails
`int` parsing rather than falling through to the `None` default — so a
freshly provisioned tenant's backend **crash-loops** on
`int_parsing ... input_value=''`.

Both `proton` and `aeon360` already run with `=1`. `bahana` was set the same
way — **`RBAC_BOOTSTRAP_ADMIN_USER_ID=1`** in `tenants/bahana.env` (backup:
`tenants/bahana.env.bak-rbac`). `1` is correct because it matches Chatwoot's
first-created account user (see §3.1 — the Chatwoot admin you're about to
create becomes user id 1).

**Not fixed in `example.env`.** The next tenant provisioned with the
unmodified template will crash-loop on this exact error. Fix belongs in a
separate change to `deploy/tenants/example.env` (default it to a sane value
or document the required override inline); out of scope here.

### 2.2 A fresh tenant has no AI credentials wired

`bahana` shipped with `GOOGLE_GENAI_USE_VERTEXAI=false` and `GEMINI_API_KEY`,
`VERTEX_PROJECT_ID`, `GCP_ADC_PATH` all empty — the only valid combination
`example.env`'s template comments describe for `USE_VERTEXAI=false` is a
Gemini API key, and none was set, so the backend failed with
`ValueError: No API key was provided`.

Fixed by matching `proton`'s working configuration in `tenants/bahana.env`:

```
GOOGLE_GENAI_USE_VERTEXAI=true
VERTEX_PROJECT_ID=lv-playground-genai
GCP_ADC_PATH=/opt/platform/deploy/secrets/proton-backend-sa.json
```

**Consequence, not just a fix:** `bahana` now shares `proton`'s GCP service
account. Its Vertex/Firestore usage is attributed to `proton`'s SA. Fine for
a demo tenant with synthetic data; `bahana` should get its own SA before it
is anything more than a demo (a real customer's usage billed/audited under
another tenant's identity is not a shape to carry into production).

### 2.3 `add-tenant.sh` renders an HTTP-only Caddy vhost

There's no global `tls`/`acme` directive in `caddy/Caddyfile`, and the script
only ever renders `http://<prefix>crm.<ip>.nip.io { ... }`. Twilio will not
deliver WhatsApp webhooks to a plain-HTTP endpoint — this is the single
highest-risk gap in the whole provisioning path, because it's silent until
the moment a message from a real phone doesn't arrive.

Fixed by hand: an `https://bahana.crm.34-50-103-151.nip.io { ... }` block,
modelled on `aeon360.caddy`'s existing hand-added block, was appended to
`caddy/tenants/bahana.caddy` (backup: `bahana.caddy.bak-tls-20260822`),
validated, then Caddy reloaded. Verified live (§0): valid cert, `302` on `/`.

The `http://` block was left in place alongside it (any already-registered
plain-HTTP webhook keeps working); the `https://` block is the one that
matters for Twilio.

**Note:** `https://bahana.agent.34-50-103-151.nip.io` (the agent service's own
host) was **not** given the same treatment and is still HTTP-only — verified:
`https://` connection to it fails, `http://…/healthz` returns `200`. This is
fine as-is: nothing external ever calls the agent host directly. Only
Chatwoot (same Docker network / same Caddy) and the operator's own
`register_bot.py` invocation reach it, and `AGENT_PUBLIC_URL` is deliberately
`http://` in `docker-compose.tenant.yml` for that reason. Don't "fix" this —
there's nothing to fix.

**Same repo bug as §2.1**: `add-tenant.sh` itself is unpatched. The next
tenant needs the same by-hand `https://` block, from the same aeon360-shaped
template, before Twilio (or any other webhook sender) can reach it.

### 2.4 `PROTON_BACKEND_URL`/`PROTON_BACKEND_KEY` ship empty — repo bug, will hit the next tenant too

`tenants/bahana.env` shipped both **`PROTON_BACKEND_URL=`** and
**`PROTON_BACKEND_KEY=`** empty — they ship empty in `example.env`, and
`add-tenant.sh` does not populate them for a new tenant. `agent/app/clients/
deps.py`'s `get_proton_config_client()` returns `None` when either is
falsy, and every caller of it is fail-open by design — so nothing errors
and nothing logs. The practical effect: the agent never fetches the §7
persona at all. §7's persona step would have been a silent no-op — no
error, no log entry, just an English-ish default prompt with none of the
Bahana framing, none of the configured language, and none of the buy/sell
guardrail, indistinguishable on screen from a persona that "just isn't
showing yet."

Fixed by hand: set **`PROTON_BACKEND_URL=http://bahana-backend:8080`** and
generated a fresh 64-hex-char **`PROTON_BACKEND_KEY`** in `tenants/bahana.env`
(backup: `tenants/bahana.env.bak-backendkey`) — both `agent` and `backend`
read the same value from the same env file, so setting it once wires both
sides. Recreated `agent` and `backend`; both healthy.

**Not fixed in `example.env`.** The next tenant provisioned with the
unmodified template will silently lose its persona the same way, with
nothing in any log to point at why. Fix belongs in the same follow-up as
§2.1's (default/document the required override in `example.env` and/or
`add-tenant.sh`); out of scope here.

### 2.5 `purge`'s RSA sweep is unconditional — repo bug, will hit any tenant with `RSA_ENABLED=false`

`purge()` in `deploy/scripts/seed_demo_data/client.py` deletes the batch's
Chatwoot contacts and conversations first, then unconditionally does `GET
/rsa/incidents` on the backend and `raise_for_status()`s the response —
confirmed by reading the function: the contact/conversation delete loop
runs to completion, then `rsa_response = await
_backend.get("/rsa/incidents")` followed by `rsa_response.raise_for_status()`,
with no branch that skips it. There's no flag to opt out either — `purge`'s
own `--help` lists only `--tenant`, `--batch`, `--dry-run`, and the
Chatwoot/backend connection flags.

On `bahana`, `RSA_ENABLED=false` in `tenants/bahana.env` — it's
`example.env`'s own default; `proton` is the outlier with `RSA_ENABLED=true`
— so the backend never mounts the RSA router (`main.py`: `if
settings.rsa_enabled and settings.rsa_database_url`). `GET /rsa/incidents`
then 404s regardless of URL or credentials, and every `purge` run on this
tenant ends in a traceback *after* the Chatwoot deletions have already
succeeded. See §6.2/§6.3 for what this looks like in practice and how to
confirm the deletion went through anyway.

**Not a bahana bug — a repo bug.** `purge()` treats the RSA sweep as
mandatory for every tenant, but RSA is an opt-in feature that's *off* by
default (`example.env`'s `RSA_ENABLED=false`). Any tenant that doesn't
enable RSA hits this same traceback on every purge. Fix belongs in
`client.py` (skip the sweep when the backend reports RSA isn't mounted, or
make it conditional on a flag); out of scope here — and do not enable RSA
on bahana to route around it, it's an automotive roadside-assistance
feature with no place on a securities demo tenant.

---

## 3. Wire the channel

Nothing here has been done — the tenant is at the Chatwoot onboarding wizard,
untouched. Needs a browser, the Chatwoot admin session, and Twilio console
access for the PT Devoteam Cloud Services account.

> **Shell commands below** (here and in §5-§9) assume you're already inside
> an SSH session on the VM, `cd`'d to `/opt/platform/deploy` — that's how
> they're shown. Where a command needs to run from your own machine instead
> (only §5's sync step does), it's wrapped in `gcloud compute ssh ...
> --command='...'` explicitly, or labelled "from your machine".

### 3.1 Create the Chatwoot admin user

Visit `https://bahana.crm.34-50-103-151.nip.io/` — it redirects to
`/installation/onboarding`. Create the admin account there.

This becomes Chatwoot user id 1 — the exact id `RBAC_BOOTSTRAP_ADMIN_USER_ID=1`
(§2.1) already assumes. Nothing further to configure for that; it's already
correct in `tenants/bahana.env`.

### 3.2 Get the two Chatwoot API tokens the agent needs

- **`CHATWOOT_API_TOKEN`**: log in → avatar (bottom-left) → **Profile
  Settings** → **Access Token** tab → copy.
- **`CHATWOOT_PLATFORM_TOKEN`**: **Super Admin console** (`/super_admin`) →
  **Platform Apps** → create/copy the **Platform Token**.

Write both into `tenants/bahana.env` on the VM (`CHATWOOT_ACCOUNT_ID=1` is
already set correctly — Chatwoot's first account). These are needed for
§3.5 (agent bot registration) and the seeder (§6).

### 3.3 Add the Twilio WhatsApp inbox — in Chatwoot, not the backend

This demo uses **Chatwoot's own native Twilio channel** — Chatwoot holds its
own Account SID / Auth Token / `whatsapp:+16292843510`, not the backend.

> Don't touch the commented-out `TWILIO_ACCOUNT_SID` /`TWILIO_AUTH_TOKEN`
> block in `example.env` (around line 1146). Those belong to the backend's
> separate voice/IVR subsystem — a different integration this demo doesn't
> use. Setting them does nothing for WhatsApp here and risks confusion with
> a subsystem that's never been run against a real Twilio call.

In Chatwoot: **Settings → Inboxes → Add Inbox → Twilio** (channel type). Enter
the Account SID and Auth Token from the Twilio console for the **PT Devoteam
Cloud Services** account, and the number `+16292843510` marked as WhatsApp.
Save — Chatwoot shows this inbox's own webhook/callback URL after creation.

**Note the inbox id** shown in its URL/settings — you need it for §3.5 (agent
bot registration) and again for §6 (seeding: `seed-nasabah` targets this same
inbox id, deliberately, see §6.1).

### 3.4 Point Twilio's inbound webhook at that inbox

In the Twilio console, set the number's inbound webhook to:

```
https://bahana.crm.34-50-103-151.nip.io/twilio/callback
```

Must be the **https** host from §2.3 — Twilio will not deliver to `http://`.

Sanity check already done (§0): `https://bahana.crm.34-50-103-151.nip.io/twilio/callback`
currently returns `404`. That's correct and expected — the route only exists
once the Chatwoot Twilio inbox above is created; the point of checking it now
is confirming TLS terminates and Chatwoot answers (404, not a connection
failure or a Caddy error page).

### 3.5 Register the agent bot and assign it to the WhatsApp inbox

```bash
cd /opt/platform/deploy
docker compose -p bahana -f docker-compose.tenant.yml \
  --env-file tenants/bahana.env exec agent \
  python -m scripts.register_bot --inbox-id <whatsapp-inbox-id-from-3.3>
```

Prints `CHATWOOT_BOT_TOKEN=...` and `CHATWOOT_BOT_SECRET=...` (the latter
only if `CHATWOOT_API_TOKEN` belongs to an account administrator — it does,
since it's the user created in §3.1). Copy both into `tenants/bahana.env`,
then restart the agent to pick them up:

```bash
docker compose -p bahana -f docker-compose.tenant.yml \
  --env-file tenants/bahana.env up -d agent
```

(This restart is superseded by §5's `--build agent`, if you do §5 right
after — no need to restart twice if they're back to back.)

The script points the bot at `http://bahana.agent.34-50-103-151.nip.io/webhooks/chatwoot/bot`
automatically (from `AGENT_PUBLIC_URL`, already correct — see §2.3's note).

---

## 4. Contact custom attribute definitions

**Chatwoot admin → Settings → Custom Attributes → Add Custom Attribute**,
once per key, **type: Text**, applied to Contact:

```
demo_seed
risk_profile
aum_band
rdn_balance
holdings
days_since_last_transaction
product_gaps
next_best_offer
offer_rationale
```

**This list has nine keys, not eight** — see §4.1 for why, and don't skip
`demo_seed`.

> **A key mismatch here does not error — it silently empties the sidebar.**
> Chatwoot's contact `custom_attributes` API accepts any key as free-form
> JSON regardless of whether an admin-defined attribute exists for it; an
> *undefined* key is written and retrievable via the API, but Chatwoot's
> sidebar only renders keys that have a matching attribute definition. Get
> any one of these nine keys wrong (typo, wrong case, extra underscore) and
> the AI still sees the data (it reads the contact API directly) but the
> human agent sees nothing in the sidebar during the demo — the most
> visible failure mode possible, with no error anywhere to point at.

### 4.1 Verification against the code — the code wins

Task 6's brief (and this repo's SDD plan) both list **eight** keys:
`risk_profile`, `aum_band`, `rdn_balance`, `holdings`,
`days_since_last_transaction`, `product_gaps`, `next_best_offer`,
`offer_rationale`. Verified directly against `build_nasabah_custom_attributes`:

```
$ cd deploy/scripts/seed_demo_data && python3 -c "from nasabah import generate_nasabah; from client import build_nasabah_custom_attributes; print(sorted(build_nasabah_custom_attributes(generate_nasabah(1, batch_id='x')[0], 'x')))"
['aum_band', 'days_since_last_transaction', 'demo_seed', 'holdings', 'next_best_offer', 'offer_rationale', 'product_gaps', 'rdn_balance', 'risk_profile']
```

Nine keys — the eight above plus **`demo_seed`**, the seeder's own batch
marker (used by `purge --batch <id>` to find everything a run created, not a
profile field the AI reads). The code wins per this task's instructions: the
eight-key list in the brief/plan was an oversight, and this runbook's list in
the box above (§4) is corrected to match the code exactly.

`demo_seed` doesn't strictly *need* a Chatwoot attribute definition to
function — `purge` reads it via the contacts API directly, not through the
sidebar — but defining it costs nothing and means the sidebar shows which
batch a contact belongs to, which is useful while you're seeding and
re-seeding today.

---

## 5. Deploy the agent with this branch's changes

**The Task 5 change (customer-profile context folded into the bot's decision
prompt) is merged to `dev-yuda` but is NOT on the VM yet.** Verified directly
on the VM: `/opt/platform` is not a git checkout (synced source only, per
`CLAUDE.md`), its `orchestrator.py` still has the single-argument
`_build_system_prompt(persona)` signature, and it has no `customer_context.py`
at all — it's a real gap, not a formality.

> **Never copy a single file wholesale to `/opt/platform`.** A lone file
> imports its whole future import graph — this has crash-looped production
> before (see project memory `feedback_vm-source-lags-dev-yuda`). Sync the
> full `agent/` (and `backend/` if it also changed) directory tree.

Sync source, from your machine:

```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing
tar czf /tmp/bahana-agent-sync.tgz -C agent app scripts pyproject.toml uv.lock
gcloud compute scp /tmp/bahana-agent-sync.tgz \
  crm-ticketing:/tmp/bahana-agent-sync.tgz \
  --zone=asia-southeast2-a --project=lv-playground-genai
```

Then, on the VM (extract over the existing tree, keep a rollback):

```bash
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --project=lv-playground-genai --command='
  cd /opt/platform &&
  tar czf /tmp/bahana-agent-backup-$(date +%s).tgz agent/app agent/scripts agent/pyproject.toml agent/uv.lock &&
  tar xzf /tmp/bahana-agent-sync.tgz -C agent
'
```

Rebuild and restart just the agent container:

```bash
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --project=lv-playground-genai --command='
  cd /opt/platform/deploy &&
  docker compose -p bahana -f docker-compose.tenant.yml \
    --env-file tenants/bahana.env up -d --build agent
'
```

Verify the new code actually landed before moving on:

```bash
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --project=lv-playground-genai --command='
  docker exec bahana-agent grep -n "_build_system_prompt(persona: dict | None, customer_context" app/services/orchestrator.py &&
  docker exec bahana-agent test -f app/services/customer_context.py && echo "customer_context.py present"
'
```

If either check comes back empty, the sync didn't take — do not proceed to
seeding/rehearsal on a stale agent, the whole personalization beat depends on
this.

**Also confirm `CHAT_AGENT_ENABLED` and `KB_GROUNDED_REPLIES` are both
`false`** in `tenants/bahana.env`. Both default false, so bahana is almost
certainly fine, but each one silently discards the customer-context prompt
on a different code path — `orchestrator.py` bypasses `system_prompt`
entirely for the WhatsApp `/chat/turn` agent when `CHAT_AGENT_ENABLED` is
set, and `KB_GROUNDED_REPLIES` overwrites the reply text with a backend
answer that is not grounded in the nasabah profile — and neither failure
raises an error. It's the last way the demo's headline personalization beat
can vanish silently.

```bash
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --project=lv-playground-genai --command='
  grep -E "^(CHAT_AGENT_ENABLED|KB_GROUNDED_REPLIES)=" /opt/platform/deploy/tenants/bahana.env
'
```

Both lines should be absent or `=false`. If either is `=true`, set it back
to `false` and recreate `agent`/`backend` before proceeding.

---

## 6. Seed demo data

Small batch first, watch it land in Chatwoot, then the real batch.

### 6.1 `--inbox-id` here is the WhatsApp inbox itself — not a dedicated API inbox

This is the one place this repo's own two seeders behave differently, and
it's easy to get backwards if you've seen the older `seed` (automotive
cases) subcommand before. `seed`'s inbox-safety check
(`assert_inbox_is_safe_to_seed` in `deploy/scripts/seed_demo_data/client.py`)
refuses to run against any inbox that has an agent bot attached or isn't
`Channel::Api` — because that command creates full conversations with
inbound messages, and running it against a bot-enabled inbox would fire a
real AI reply per seeded contact into a tenant a client can see.

`seed-nasabah` **deliberately skips that check** (see the comment above
`_run_nasabah_seed` in `__main__.py`, and commit `6ab1a79`). It only creates
*contacts* — no conversation, no message, nothing that could land in
`pending` and wake the orchestrator. The safety hazard the guard exists for
isn't reachable through this path, so the guard would only get in the way:
it would refuse the exact inbox — a `Channel::TwilioSms` inbox with the
agent bot attached — that this command exists to seed.

**So: pass the WhatsApp inbox id from §3.3 directly as `--inbox-id`.** Do
not create a separate API-channel inbox for this — there's nothing to
isolate it from.

### 6.2 Verify the CLI flags before running anything

Confirmed directly against the installed CLI, 2026-08-22:

```
$ cd deploy/scripts && python3 -m seed_demo_data seed-nasabah --help
```
```
usage: python3 -m seed_demo_data seed-nasabah [-h] --tenant TENANT
                                              [--count COUNT]
                                              [--pinned-phone PINNED_PHONE]
                                              [--pinned-name PINNED_NAME]
                                              [--rng-seed RNG_SEED]
                                              [--batch-id BATCH_ID]
                                              [--chatwoot-url CHATWOOT_URL]
                                              [--chatwoot-token CHATWOOT_TOKEN]
                                              [--account-id ACCOUNT_ID]
                                              --inbox-id INBOX_ID
                                              [--backend-url BACKEND_URL]
                                              [--backend-key BACKEND_KEY]
```

`--inbox-id` is required with no env fallback and no default — per §6.1,
pass the WhatsApp inbox id from §3.3.

> **If you run `--help` yourself, ignore what it says about `--inbox-id`.**
> The usage line above is real, but the full `--help` output's description
> for `--inbox-id` is shared code (`_add_chatwoot_flags`'s `inbox_help` in `__main__.py`,
> lines 142-148) written for the `seed` subcommand, and it reads: *"Pick
> (or create) a dedicated inbox for demo data and pass its id explicitly
> every time."* That is correct advice for `seed` and the opposite of
> correct for `seed-nasabah` — §6.1's guidance wins: pass the WhatsApp
> inbox id, not a dedicated one. The reason the shared text is safe to
> ignore here is the same reason §6.1 gives — `seed-nasabah` never creates
> a conversation, so the hazard a "dedicated inbox" protects against
> (bot-enabled inbox + a live conversation = a real AI reply per seeded
> contact) can't happen through this command.

`purge`'s flag is `--batch`, **not** `--batch-id` — confirmed the same way:

```
$ python3 -m seed_demo_data purge --help
```
```
usage: python3 -m seed_demo_data purge [-h] --tenant TENANT --batch BATCH
                                       [--dry-run] ...
```

**`--tenant` is a label, not a config loader.** It's used for the
confirmation prompt and the manifest, but it does **not** read
`tenants/bahana.env` for you — `--chatwoot-url`/`--chatwoot-token`/
`--account-id` (or their `$CHATWOOT_URL`/`$CHATWOOT_API_TOKEN`/
`$CHATWOOT_ACCOUNT_ID` env-var fallbacks) are separately required, every
time. The commands below pass them explicitly so they're copy-pasteable
without an extra step. `--chatwoot-token` is the `CHATWOOT_API_TOKEN` from
§3.2.

`--backend-url`/`--backend-key` are also required — `TenantConfig` needs
*a* value even though `seed-nasabah` never calls the backend (it creates
contacts only; RSA-incident creation is `seed`'s path, not this one). Any
non-empty placeholder satisfies this for `seed-nasabah`; the values in its
commands below are inert.

**`purge` is different — its `--backend-url`/`--backend-key` are NOT
inert.** `purge()` in `client.py` unconditionally sweeps `GET
/rsa/incidents` on the backend and `raise_for_status()`s the response,
*after* it has already deleted the batch's Chatwoot contacts and
conversations, with no flag to skip that sweep — see §2.5. Point it at a
placeholder URL/key (as the smoke/real-batch purge commands below used to)
and the sweep fails for the wrong reason (a junk key or unreachable host)
instead of the real one below, which makes the failure harder to read, not
easier.

**Use bahana's public HTTPS origin for `--backend-url`, not the
docker-compose alias.** `http://bahana-backend:8080` is a Docker-network
hostname — it only resolves for containers on the tenant's own compose
network, not from the bare VM shell these commands run in (§3's banner
above). `https://bahana.crm.34-50-103-151.nip.io` *is* reachable from that
shell, and `add-tenant.sh`'s Caddy rule already proxies its `/rsa/*` prefix
to `bahana-backend:8080` for you (see the `@proton_backend` block in
`deploy/scripts/add-tenant.sh`) — same backend, reachable from the right
place. Pair it with the real `PROTON_BACKEND_KEY` value — get it from
`tenants/bahana.env` on the VM (`grep ^PROTON_BACKEND_KEY=
tenants/bahana.env`, read directly off the VM's own terminal; don't paste
the value into this document, a chat, or a commit).

**Even with the right URL and key, expect a traceback anyway — that's
expected, not a failure.** `bahana` runs with `RSA_ENABLED=false` (§2.5),
so the backend never mounts the RSA router and `/rsa/incidents` 404s no
matter what you pass. Every `purge` command below will delete the batch's
Chatwoot contacts and conversations first — that part succeeds — and then
raise on the RSA sweep. **The traceback is expected and does not mean the
purge failed.** To confirm the deletion actually happened: check the
Chatwoot contacts list for the batch's `[DEMO]`-prefixed contacts (they
should be gone), or just re-run the same `purge` command — a second run
against an already-purged batch finds nothing left to delete and only the
(still-expected) RSA-sweep traceback remains.

### 6.3 Smoke batch — run this first, watch it

```bash
cd /opt/platform/deploy/scripts
python3 -m seed_demo_data seed-nasabah \
  --tenant bahana \
  --chatwoot-url https://bahana.crm.34-50-103-151.nip.io \
  --chatwoot-token <CHATWOOT_API_TOKEN from §3.2> \
  --account-id 1 \
  --inbox-id <whatsapp-inbox-id-from-3.3> \
  --backend-url https://bahana.crm.34-50-103-151.nip.io \
  --backend-key unused-seed-nasabah-does-not-call-the-backend \
  --count 3 --batch-id smoke
```

Open the Chatwoot contacts list, confirm 3 `[DEMO]`-prefixed contacts exist
with the nine attributes from §4 populated in the sidebar. If they don't
render, stop — that's the §4 mismatch failure mode, fix the attribute
definitions before seeding the real batch.

Purge it once confirmed good, or once you're about to re-run:

```bash
python3 -m seed_demo_data purge \
  --tenant bahana \
  --chatwoot-url https://bahana.crm.34-50-103-151.nip.io \
  --chatwoot-token <CHATWOOT_API_TOKEN from §3.2> \
  --account-id 1 \
  --backend-url https://bahana.crm.34-50-103-151.nip.io \
  --backend-key <PROTON_BACKEND_KEY from tenants/bahana.env on the VM> \
  --batch smoke
```

**Expect this to end in a traceback on the RSA sweep — that's normal on
this tenant, see §6.2/§2.5.** The contact/conversation deletion above the
traceback already succeeded; confirm it by checking the Chatwoot contacts
list for the `smoke` batch's `[DEMO]`-prefixed contacts (they should be
gone) or by re-running this same command and seeing nothing left to delete.

### 6.4 Real batch — pin the demo handset

```bash
python3 -m seed_demo_data seed-nasabah \
  --tenant bahana \
  --chatwoot-url https://bahana.crm.34-50-103-151.nip.io \
  --chatwoot-token <CHATWOOT_API_TOKEN from §3.2> \
  --account-id 1 \
  --inbox-id <whatsapp-inbox-id-from-3.3> \
  --backend-url https://bahana.crm.34-50-103-151.nip.io \
  --backend-key unused-seed-nasabah-does-not-call-the-backend \
  --count 25 --batch-id demo1 \
  --pinned-phone <demo handset E.164, e.g. +62812xxxxxxx> \
  --pinned-name "Budi Santoso"
```

**What `--pinned-phone` is for:** Chatwoot matches an inbound WhatsApp
message to a contact by phone number. Every other seeded number is a `+999`
country code — the ITU-T reserved-for-testing prefix, permanently unassigned,
so it can never route to a real subscriber. `--pinned-phone` is the **one**
routable number the seeder ever writes, and it must be the exact E.164 number
of the handset the demo will be performed from, or the bot will not recognize
the sender and the whole personalization beat (§10, step 3) silently degrades
to generic (§11 covers this as a fallback, but it shouldn't be a surprise on
the day — get this number right and confirm it before the meeting).

Keep the purge command for this batch handy in case a re-seed is needed
before Monday (same flags as §6.3's purge, with `--batch demo1`):

```bash
python3 -m seed_demo_data purge \
  --tenant bahana \
  --chatwoot-url https://bahana.crm.34-50-103-151.nip.io \
  --chatwoot-token <CHATWOOT_API_TOKEN from §3.2> \
  --account-id 1 \
  --backend-url https://bahana.crm.34-50-103-151.nip.io \
  --backend-key <PROTON_BACKEND_KEY from tenants/bahana.env on the VM> \
  --batch demo1
```

Same as §6.3: this will delete `demo1`'s contacts/conversations
successfully and then traceback on the RSA sweep — expected, see §6.2/§2.5.

---

## 7. Set the persona

**Before touching the UI, verify `PROTON_BACKEND_KEY` is non-empty in
`tenants/bahana.env`** (§2.4 — it ships empty and `add-tenant.sh` doesn't
populate it):

```bash
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --project=lv-playground-genai --command='
  grep -c "^PROTON_BACKEND_KEY=." /opt/platform/deploy/tenants/bahana.env
'
```

This should print `1` (one non-empty match). If it prints `0`, everything
below in this section will still complete without any error — the symptom
is the persona being **silently absent**: the bot answers with a generic
default prompt, none of the Bahana framing/language/guardrail below takes
effect, and nothing in any log says why. Fix per §2.4 before continuing.

**Chatwoot admin → Knowledge (left nav) → Assistants tab** → create/edit an
assistant with:

- **`instructions`**: the Bahana relationship-assistant framing — a
  WhatsApp assistant for Bahana Sekuritas nasabah, warm and professional,
  speaks to the profile in the sidebar (risk profile, holdings, staged
  offer) without being asked, hands off to a human for anything it isn't
  confident about.
- **`language`**: Bahasa Indonesia.
- **`guardrails`**: add one forbidding **specific buy/sell recommendations**
  — the assistant may mention the staged next-best-offer by name and
  rationale (that's the personalization beat), but must never tell the
  nasabah to buy or sell a specific instrument or give a price target. This
  is the guardrail that keeps the demo inside what §7.4 of the design spec
  calls out as a hard line (suitability must be a filter on the offer
  catalog, never a prompt instruction the model could talk itself out of).
- Leave `temperature`/`response_guidelines`/the three lifecycle messages
  (`welcome_message`/`handoff_message`/`resolution_message`) at whatever
  reads naturally — none of them are load-bearing for the demo script.

Then **Knowledge → Inboxes tab**: assign this assistant to the WhatsApp
inbox from §3.3. (This assignment is what the agent-bot flow reads via
`ProtonConfigClient.get_assistant_persona`, per `CLAUDE.md`'s "Operator-
configurable persona & knowledge" section — no code involved, this is exactly
the point of that surface.)

---

## 8. Demo hygiene — trim `PROTON_FEATURES`, do not build a custom role

**Not done yet — do this before the rehearsal in §9, not just before the
meeting.** Design spec §5.6 mandates it and it's easy to skip because the
tenant otherwise looks ready: containers healthy, Chatwoot wired, data
seeded. This step is about what the *admin sees on screen*, not whether
anything works.

### 8.1 A fresh tenant does not open empty

The platform feature switchboard is still design-only, so `bahana`'s admin
account does **not** see a blank product. Most surfaces (Cases, Taxonomy,
RSA incidents, the DMS card, the report pages) are gated on **Chatwoot
permissions**, not on `PROTON_FEATURES` — and the `administrator` role
(what §3.1's admin user holds) has every permission. Left alone, all of
that is one click away in the left nav during the demo — several of those
surfaces are automotive-flavored (vehicle/dealer language), and the report
pages render blank because this tenant has no warehouse (per
`CLAUDE.md`/recent `fix(metrics)` work: no warehouse → empty reports, not
another tenant's numbers, but empty is still the wrong thing to have on
screen in front of a securities firm).

**Trimming `PROTON_FEATURES` does not hide any of that.** It only gates
`ai_assist`/`copilot`/`knowledge` (verified against the fork's own
`hasFeature(...)` call sites — Cases/Taxonomy/RSA/DMS/Reports don't check
it at all). So this step is two separate, both-required halves, not one:

1. **Trim `PROTON_FEATURES`** to what the demo actually uses.
2. **Simply don't navigate to the rest** — nothing hides Cases/Taxonomy/
   RSA/DMS/Reports, so staying out of them is the only mitigation for those
   specific surfaces.

### 8.2 Trim `PROTON_FEATURES`

The demo script (§10) only ever touches the Knowledge nav (§7's persona
editor) — never Ask Copilot, never the ✨ inline-assist button. Set:

```
PROTON_FEATURES=knowledge
```

in `tenants/bahana.env` (currently blank, which defaults via
`docker-compose.tenant.yml` to the full `ai_assist,nav_menu,copilot,knowledge`
set). `nav_menu` isn't checked by any `hasFeature(...)` call in the current
fork — it's effectively inert either way, so dropping it changes nothing
observable; `knowledge` is the one load-bearing name here, since §7's
Assistants/Inboxes tabs live behind it.

Recreate `chatwoot-rails` to pick up the env change (Compose detects the
value changed and recreates automatically — no `--build` needed, the image
is unchanged):

```bash
cd /opt/platform/deploy
docker compose -p bahana -f docker-compose.tenant.yml \
  --env-file tenants/bahana.env up -d chatwoot-rails chatwoot-sidekiq
```

### 8.3 Do NOT create a custom Chatwoot role to hide the rest

It would be tempting to reach for a custom role scoped to just the demo
surfaces instead of "just don't click there." **Don't.** A Chatwoot custom
role **replaces** `administrator` outright rather than subtracting
permissions from it — it is not a narrower administrator, it is a
different, smaller role. Assign one to the account you're about to demo
from and you lose every permission `administrator` had that the custom role
didn't explicitly re-grant, **including the ability to fix the role
itself** — a real, previously-hit lockout hazard recorded in this repo's
own project memory (`rbac-mirror-demotes-admins`: "a Chatwoot custom role
REPLACES `administrator`; never tick 'Chatwoot access' on a role whose
members are admins"). Doing this under Monday-morning time pressure, an
hour before the meeting, with no second admin account to fall back on, is
exactly how the demo tenant becomes unreachable right when it needs to be
reachable most.

The safe combination is §8.2's env trim plus discipline, not a role. If the
trim alone feels insufficient, the fallback is narrower still: log in,
confirm the left nav, and simply stay off Cases/Taxonomy/RSA/DMS/Reports
for the whole meeting — no config change needed for that half at all.

---

## 9. Rehearse end to end from the pinned handset

Do this before Monday, not for the first time in front of the prospect.
Verify each of these, **in order** — each one only makes sense if the
previous one worked:

1. **The contact is matched, not newly created.** Message the WhatsApp
   number from the exact pinned handset (§6.4). In Chatwoot, the resulting
   conversation should attach to the existing `[DEMO] Budi Santoso` contact,
   not spawn a new unnamed one. If it creates a new contact, the pinned
   phone number in Chatwoot doesn't match the sending number's format
   (check E.164 formatting, `+` prefix, country code) — fix and re-seed
   before anything downstream is worth testing.
2. **The sidebar shows the profile.** Risk profile, AUM band, holdings,
   staged offer all visible next to the conversation.
3. **The bot answers the question.** Ask something mundane (a fee
   question) from the handset; confirm a reply arrives.
4. **The offer appears in context.** The reply should reference the staged
   `next_best_offer`/`offer_rationale`, not just answer the literal
   question — that's the personalization beat, not a generic FAQ answer.
5. **An agent reply silences the bot.** Reply as the human agent from
   Chatwoot; confirm the bot goes quiet for the rest of that conversation
   (per `CLAUDE.md`: only acts on a `pending` conversation).
6. **The `ai_actions` row exists.** Every AI decision is logged before
   execution — this is the demo's audit-trail beat (§10, step 5). Query it
   directly (no admin UI page for this table):

   ```bash
   gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --project=lv-playground-genai --command='
     docker exec platform-infra-postgres-1 psql -U postgres -d agent_bahana \
       -c "SELECT id, conversation_ref, decision, model, output, created_at FROM ai_actions ORDER BY created_at DESC LIMIT 5;"
   '
   ```

   `output` is the column worth pointing at on screen — it is the actual
   reply text the row's `decision` produced, next to its `model` and
   `created_at`. See §10 step 5 for what this table does and does not
   claim.

If step 1 fails on the day itself (contact not matched, e.g. because someone
had to borrow a different handset), you're not stuck — see §11's second
fallback. Everything through step 6 should be exercised once, calmly, before
the actual meeting.

---

## 10. Demo script — the beats

1. **Show the CRM contact list.** Portfolio attributes visible in the
   sidebar for a seeded nasabah. **Say out loud that the data is
   synthetic.** Don't let it be mistaken for real Bahana customer data.
2. **From the pinned handset, message `+16292843510`** with something
   mundane — a fee question works well; it's the kind of question a real
   nasabah would actually send.
3. **The AI answers**, and because it can see the profile, frames the
   answer to this nasabah specifically and weaves in the staged offer in
   context — not a generic FAQ answer.
4. **Interrupt from the CRM.** An agent takes over mid-conversation from
   Chatwoot; the bot goes silent; the human continues with the full
   profile still visible beside them.
5. **Show `ai_actions`.** Every AI decision the bot makes is logged before
   it executes — the model that produced it, its token cost, and the reply
   text itself, keyed to the conversation. That's the audit-trail beat: a
   complete, timestamped record of what the AI decided and said, for every
   conversation, queryable directly (§9 step 6). **Say what this doesn't
   yet cover, don't imply it**: the prompt/persona/customer-context inputs
   that produced each decision aren't captured in this table today — full
   reproducibility (design spec §7.5) is later-phase work, not a Phase 0
   claim.
6. **Close on the production picture.** Point at design spec §6 (the
   roadmap: real data feed, RM suggestion queue, governance/scale) and §7
   (security/compliance) — and **say, explicitly, that authentication is
   not implemented in this demo.** The AI is showing account figures to an
   unverified WhatsApp sender; production requires two tiers (unverified:
   generic, no balances; verified: specific figures after OTP or an
   authenticated deep link — spec §7.1). Naming this instead of hiding it
   is deliberate: a demo that quietly pretends the problem doesn't exist is
   a worse pitch than one that names it and shows the plan.

---

## 11. Fallbacks

- **WhatsApp fails on the day** (Twilio outage, balance issue despite §1.2,
  number/webhook misconfiguration): Chatwoot's **website widget** gives the
  identical personalization story — same contact, same sidebar, same
  agent-bot flow — on a less impressive channel. It's already wired to the
  same Chatwoot account and needs no separate provisioning; just message
  through the widget instead of WhatsApp and narrate the substitution.
- **The pinned contact isn't matched** (wrong handset, phone-format
  mismatch, re-seed didn't take): the bot degrades to generic per-message
  behavior — per `CLAUDE.md`'s fail-open design, no contact/no match/no
  attributes yields today's behavior byte-identical, so it still answers,
  just without the personalization beat. Still a working demo of the base
  AI-assist story; just skip claiming beat 3 in §10 and move to beat 4.

---

## Appendix: command quick-reference

```bash
# SSH to the VM
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --project=lv-playground-genai

# Register + assign the agent bot (§3.5)
cd /opt/platform/deploy
docker compose -p bahana -f docker-compose.tenant.yml --env-file tenants/bahana.env \
  exec agent python -m scripts.register_bot --inbox-id <whatsapp-inbox-id>

# Deploy agent code (§5)
docker compose -p bahana -f docker-compose.tenant.yml --env-file tenants/bahana.env \
  up -d --build agent

# Demo hygiene: trim PROTON_FEATURES (§8) -- edit tenants/bahana.env to
# PROTON_FEATURES=knowledge, then:
docker compose -p bahana -f docker-compose.tenant.yml --env-file tenants/bahana.env \
  up -d chatwoot-rails chatwoot-sidekiq

# Seed (§6) — from deploy/scripts. Full flag set (see §6.2 for why --tenant
# alone isn't enough) is in §6.3/§6.4; abbreviated here for reference:
python3 -m seed_demo_data seed-nasabah --tenant bahana \
  --chatwoot-url https://bahana.crm.34-50-103-151.nip.io --chatwoot-token <token> \
  --account-id 1 --inbox-id <whatsapp-inbox-id> \
  --backend-url https://bahana.crm.34-50-103-151.nip.io --backend-key unused \
  --count 3 --batch-id smoke
# purge ends in a traceback on the RSA sweep -- expected on this tenant
# (RSA_ENABLED=false), the deletions above it already succeeded. §6.2/§2.5.
python3 -m seed_demo_data purge --tenant bahana \
  --chatwoot-url https://bahana.crm.34-50-103-151.nip.io --chatwoot-token <token> \
  --account-id 1 --backend-url https://bahana.crm.34-50-103-151.nip.io \
  --backend-key <PROTON_BACKEND_KEY from tenants/bahana.env on the VM> \
  --batch smoke
# ...then the real batch: --count 25 --batch-id demo1 --pinned-phone <E.164>
#    --pinned-name "Budi Santoso" — see §6.4 for the full command.

# ai_actions audit row (§9, step 6)
docker exec platform-infra-postgres-1 psql -U postgres -d agent_bahana \
  -c "SELECT id, conversation_ref, decision, model, output, created_at FROM ai_actions ORDER BY created_at DESC LIMIT 5;"
```
