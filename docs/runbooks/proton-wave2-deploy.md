# Proton Wave 2 deploy steps (NOT part of the Wave 1 window)

Wave 2 ships after 17 Nov 2026. Nothing here belongs in the 10 Nov Wave 1
window (see `proton-deploy-2026-11-10.md`). This file is the operator's full
guide: do the steps in section 1 in order, then switch features on one at a
time from section 2 after the decisions in section 3. Every flag is default
off; deploying the code alone changes only what section 4 lists.

The same rules as Wave 1 apply: `/opt/platform` lags `dev-yuda`, so source goes
to the VM as a diff applied with `patch --dry-run` first, never by copying files.
Tenant env edits go through scripts under `scripts/ops/` that the operator runs.
Values are never printed.

## 1. Order

1. **VM compose diff first (`x-chatwoot-env`).** Apply
   `git diff 7d816f9f..<final HEAD> -- deploy/docker-compose.tenant.yml` to
   `/opt/platform/deploy/docker-compose.tenant.yml` with `patch --dry-run`
   first. It forwards to Rails `SESSION_IDLE_TIMEOUT_MINUTES`,
   `SESSION_TOKEN_LIFESPAN_HOURS`, `PASSWORD_MAX_AGE_DAYS`, `PII_MASKING_ENABLED`
   and the three `ACTIVE_RECORD_ENCRYPTION_*` MFA keys. Without it those keys
   reach the backend but never Rails (the stale-compose trap: flags work in the
   backend and do nothing in the CRM). All forwarded values default to
   off/blank, so this step changes nothing on its own.
2. **Chatwoot image `rc17`, rebuilt from the final HEAD** (Cloud Build only:
   `gcloud builds submit deploy/chatwoot-fork/ --config
   deploy/chatwoot-fork/cloudbuild.yaml --substitutions _REGISTRY=<proton AR repo>`,
   tag `v4.15.1-custom-rc17`), then `pull` and `up -d --force-recreate
   chatwoot-rails chatwoot-sidekiq`. It carries fork patches up to 0090,
   including 0086 (the "last 7 days" filter 500, now actually copied into the
   runtime image: commit `c745b293`), 0088 (PII masking follows
   `PII_MASKING_ENABLED`), 0089 (weekly report download sends the session; AI
   performance and data-scope cards hide on an older backend) and 0090.
   Check `/app/.git_sha` starts with the final HEAD's short SHA.
3. **Wave 2 backend patch, generated like Wave 1's.** On the Mac:

   ```bash
   git diff 085f4f25..<final HEAD> -- backend/apps/backend/src ':!**/test_*.py' \
     > scripts/ops/deploy/backend-wave2.patch
   ```

   (085f4f25 is the backend source the Wave 1 patch left on the VM; the Wave 1
   patch file `backend-2026-11-10.patch` is not changed or reused.) Copy it to
   `/tmp` on the VM, then `cd /opt/platform && patch -p1 -F0 --dry-run <
   /tmp/backend-wave2.patch`; apply only with no failed hunks. Tag the running
   backend image first (`docker tag $(docker inspect -f '{{.Image}}'
   proton-backend) platform-backend:pre-wave2`), then `up -d --build backend`.
   No dependency file changed since Wave 1 (`pyproject.toml`/`uv.lock`).
   `platform-backend:latest` and `/opt/platform` are shared by every tenant:
   see Wave 1 runbook section 7 before recreating aeon360 or bahana.
   After the recreate run the `ensure_views` step (section 5) for the AI
   performance reports.
4. **Agent rebuild, last** (section 8 prerequisites first): restore
   `ESCALATION_POLICY_*` to `tenants/proton.env` (Wave 1 `--agent` does this),
   and review the Bahana changes in `/opt/platform/agent` before applying the
   agent diff. Only then `up -d --build agent`.

## 2. Keys per task (all default off or a safe default)

Set through a `scripts/ops/` env script, never by hand-editing
`tenants/proton.env`. Backend keys need a backend recreate; compose-forwarded
keys need a Rails + Sidekiq recreate; agent keys need the agent rebuild.

| Task / FIX | Key | Default | Where | Prerequisite |
|---|---|---|---|---|
| 17 FIX-14 weekly report e-mail | `WEEKLY_REPORT_EMAIL_ENABLED` | `false` | backend | SMTP settings; recipients set |
| | `WEEKLY_REPORT_EMAIL_RECIPIENTS` | blank | backend | Devoteam test addresses until Proton confirms |
| | `WEEKLY_REPORT_EMAIL_DAY` / `_HOUR` | `mon` / `8` | backend | in `REPORTING_TIMEZONE` |
| | `WEEKLY_REPORT_SMTP_FROM` | blank | backend | |
| 18 FIX-16 abandoned calls | `CALL_ABANDON_ENABLED` | `false` | backend | needs `CALL_ABANDON_DATABASE_URL` too |
| | `CALL_ABANDON_DATABASE_URL` | blank | backend | |
| 20 FIX-18 idle timeout | `SESSION_IDLE_TIMEOUT_MINUTES` | `0` (off) | compose -> Rails | step 1, rc17 |
| | `SESSION_TOKEN_LIFESPAN_HOURS` | blank (upstream 2 months) | compose -> Rails | step 1; applies from the next login |
| 21 FIX-15 hold/transfer | `PHONE_HOLD_TRANSFER_ENABLED` | `false` | backend | `PHONE_AGENT_SOFTPHONE_ENABLED=true` (boot refuses otherwise); see section 3 with the IVR |
| | `PHONE_HOLD_MEDIA_URL` | Twilio sample track | backend | Proton's own recording when supplied |
| | `PHONE_HOLD_MAX_SECONDS` | `1200` | backend | |
| 22 FIX-23 FAQ rating | `FAQ_RATING_COLLECTION` | `live_faq_ratings` | backend | none (read at boot) |
| 25 FIX-44 password expiry | `PASSWORD_MAX_AGE_DAYS` | `0` (off) | compose -> Rails | step 1, rc17; see section 3 |
| 26 FIX-46 duplicate detection | `DUPLICATE_DETECTION_ENABLED` | `false` | agent | label `possible_duplicate` created; `contact_updated` webhook subscribed (section 3) |
| | `DUPLICATE_DETECTION_INBOX_IDS` | blank (all) | agent | |
| | `DUPLICATE_DETECTION_AI_JUDGE_ENABLED` | `false` | agent | |
| 28 FIX-48 data scopes | `DATA_SCOPED_RBAC_ENABLED` | `false` | backend | `RBAC_ENABLED=true`; rc17; Proton sign-off (section 7) |
| 29 FIX-49 PII masking | `PII_MASKING_ENABLED` | `false` | backend **and** compose -> Rails (one line in `proton.env` reaches both) | `RBAC_ENABLED=true`; step 1 + rc17 |
| 31 FIX-31 SOP IVR | `PHONE_DTMF_MENU_ENABLED` | `false` | backend | `TWILIO_WEBHOOK_BASE_URL` + `TWILIO_AUTH_TOKEN`; section 3 |
| | `PHONE_IVR_NON_RSA_ROUTE` | `ai` | backend | `agent` needs softphones online or `PHONE_HANDOFF_TARGET_NUMBER` |
| | `PHONE_IVR_RSA_NUMBER` | blank (AI bridge takes RSA 24/7) | backend | a real E.164 line; a placeholder refuses boot |
| | `PHONE_IVR_QUEUE_BUSY_AFTER_SECONDS` / `_AGENT_ATTEND_SECONDS` | `10` / `20` | backend | SOP values |
| | `PHONE_IVR_GATHER_TIMEOUT_SECONDS` / `_VOICEMAIL_MAX_SECONDS` / `_CASE_OPEN_TIMEOUT_SECONDS` | `5` / `120` / `3.0` | backend | |
| | `PHONE_IVR_TIMEZONE` / `_WEEKDAY_HOURS` / `_WEEKEND_HOURS` / `_PUBLIC_HOLIDAYS` | KL, `08:30-17:30`, `09:00-17:00`, built-in list | backend | holiday list for the current year (a warning is logged when it is missing) |
| | `PHONE_TTS_VOICE_EN` / `_MS` | female Google voices | backend | |
| 32 FIX-57/59 agent timeouts | `PROTON_BACKEND_TIMEOUT_SECONDS` | `10` | agent | also covers `/escalation/notify` (7.9 s observed): suggest `20` on proton |
| | `PROTON_BACKEND_SLOW_TIMEOUT_SECONDS` | `60` | agent | `/chat/turn` |
| | `PROTON_ASSIST_TIMEOUT_SECONDS` | `30` | agent | `/assist/suggest` (escalation-reply AI draft) |

The AI activity monitor (FIX-43) has no env key: see the "Wave 2 note" at the
end of the Wave 1 runbook (custom feature `ai_activity` + permission
`ai_activity.view`).

## 3. Decisions for the user before switching on

- **`PHONE_DTMF_MENU_ENABLED` turns after-hours voicemail on for non-RSA
  callers.** Wave 1 already set `PHONE_AFTER_HOURS_ENABLED=true` and
  `PHONE_VOICEMAIL_INGEST_ENABLED=true`; both only take effect through the IVR
  menu. So the moment the menu is on, an out-of-hours non-RSA caller hears the
  after-hours message and is offered voicemail (RSA still reaches help 24/7).
  Decide that this is wanted before the demo.
- **Hold/transfer together with the IVR.** On IVR agent calls, pressing Hold
  used to drop the caller and After-Call-Work never started (the IVR's ring
  handler hung up on an answered dial). **Fixed in `ae0fc2e5`** (ring() now runs
  the same hold/transfer and ACW hooks as `/webhooks/phone/dial-status`). Do not
  enable `PHONE_HOLD_TRANSFER_ENABLED` together with `PHONE_DTMF_MENU_ENABLED`
  on a backend older than that commit.
- **PII masking needs RBAC** (`RBAC_ENABLED=true`, boot refuses otherwise), and
  every user without `pii.view` (only administrator has it by seed) then sees
  masked phone/e-mail/IC in Customer 360, RSA, case fields, AI activity and the
  contact sidebar/list. Grant `pii.view` to the roles that need full values first.
- **Password expiry prompts every account older than the limit at once.**
  The clock starts from each user's creation (or last change), so enabling
  `PASSWORD_MAX_AGE_DAYS=90` routes every account more than 90 days old to the
  password page on its next load. Do not enable it near the demo.
- **Duplicate detection**: create the Chatwoot label `possible_duplicate` first
  (the agent adds it, it does not create it), and check the Chatwoot webhook to
  the agent subscribes to `contact_updated` (vehicle/contact edits re-check for
  duplicates). It never merges anything and never messages the customer.
- **`ESCALATION_POLICY_*` are backend settings.** The backend reads them from
  `tenants/proton.env`; the running `proton-agent` container only holds the last
  copy that the env file lost. Restore them to the env file (Wave 1 `--agent`)
  before any agent recreate, or that copy is gone.
- **`PROTON_BACKEND_TIMEOUT_SECONDS` also covers `/escalation/notify`**, which
  took 7.9 s on proton (the Wave 2 notifier adds a Chatwoot GET and an audit
  read, at most 2 s). At the default 10 s it is close to the limit; `20` is the
  suggested proton value.

## 4. Behaviour changes with no flag (live as soon as the code is)

Backend (step 3):

- **FAQ bulk import is all-or-nothing by default** (`POST /kb/faq/bulk`): one
  invalid row and nothing is imported. `?partial=true` keeps the old behaviour.
  The rc16 upload button sends no `partial`, so with rc16 still running a CSV
  with a bad row imports nothing (rc17's patch 0087 says "nothing imported").
- **`/kb/feedback` auth changes**: it now accepts the shared CRM backend key as
  well as the QA key. A per-agent rating (the CRM strip's thumbs) with
  `RBAC_ENABLED=true` needs the Chatwoot session and records the session user,
  ignoring any `agent_id` in the body; with RBAC off the body `agent_id` is used.
  Legacy QA-key callers that send no agent/session stay feedback-only.
- **`GET /kb/faq` gains `avg_score` and `rating_count`** per entry, and a
  Firestore rating client (`FAQ_RATING_COLLECTION`) is built at boot (fail-open:
  no ratings if Firestore is unreachable).
- **`/escalation/notify` does one extra Chatwoot GET and an audit read**
  (bounded, at most 2 s) to derive the escalating user and SLA status.
- **Status-change audit rows name the actor `customer` or `agent-bot`** instead
  of a generic system actor.
- **The `role_scopes` table is created on boot** (empty; nothing restricted).
- **New routes**: `GET /authz/me/pii` (routed by the existing `/authz/*` Caddy
  rule) and `GET /admin/ai-activity`.
- **Exports**: text cells starting with `=`, `+`, `-`, `@` get a leading `'`
  in the CSV/XLSX downloads (formula-injection guard; plain phone numbers are
  left as they are).

Fork (step 2): the "last 7 days" filter works (0086); flag-off tenants never
mask contact data (0088).

## 4b. Known Wave 1 gaps this wave closes

See "Known until Wave 2" in the Wave 1 runbook: idle close at ~25 min and the
"in 0 minutes" warning, plain-reply bounce notes, and the escalation-reply AI
draft timeout close with the agent rebuild (step 4); the last-7-days filter
closes with rc17 (step 2).

## 5. AI performance reports: `ensure_views` (part of step 3, Task 27, FIX-47)

Needs `METRICS_PROVIDER=bigquery` (Wave 1) and fork image `rc17` or later
(patch 0083 renders the new Bot report section).

The backend serves `/metrics/ai-performance` from BigQuery views. Three of them
are new and do not exist in the warehouse until the views are re-created:
`v_ai_accuracy`, `v_ai_root_cause`, `v_ai_kb_improvement`. Until then those
three blocks report `unavailable` on the Bot report page (the other five
reports work, they use views that already exist).

After the backend image is rebuilt and recreated, run on the VM in
`/opt/platform/deploy`:

```bash
docker compose -p proton -f docker-compose.tenant.yml --env-file tenants/proton.env \
  exec backend python -c \
  "from chatbot.platform.config import get_settings; \
from chatbot.features.metrics.sync import ensure_views; \
ensure_views(get_settings())"
```

`ensure_views` runs `CREATE OR REPLACE VIEW` for every reporting view, so it is
safe to repeat and also refreshes the existing ones. It does not touch the
`conversations` table. It needs the backend service account to have BigQuery
job and dataset write access (the same access the sync uses).

Check the three views exist:

```bash
bq ls proton_crm | grep -E "v_ai_(accuracy|root_cause|kb_improvement)"
```

Then open Reports > Bot in the CRM: the "AI Performance" section should show no
"temporarily unavailable" note.

## 6. Data-scoped permissions (Task 28, FIX-48)

Off by default (`DATA_SCOPED_RBAC_ENABLED=false`). It requires `RBAC_ENABLED=true`
(the backend refuses to boot without it). Order:

1. Deploy the Chatwoot image carrying patch 0084 (`rc17` or later). The reports
   client then sends the user's Chatwoot session. Without it, `/metrics/*`
   returns 401 once the flag is on.
2. Rebuild and recreate the backend (the `role_scopes` table is created on boot).
3. Configure scopes per role on Roles & Permissions > Data scope.
4. Only after Proton signs off (next section), switch the flag on through an
   ops script under `scripts/ops/`. Agents never edit `tenants/*.env`.

## 7. Data scope limitations for Proton sign-off

Proton must accept these limits before the flag is switched on.

- **The Cases list and Chatwoot's conversation views are not confined by data
  scopes.** Both read Chatwoot directly with the agent's own session, so they
  follow **Chatwoot inbox membership only**. An inbox scope there is only as
  good as the agent's inbox membership. Dealer and department scopes do not
  confine them at all: a dealer-scoped agent who is a member of an inbox sees
  every dealer's cases in that inbox in the Cases list and the inbox views.
  - Recommendation: for each scoped user, set Chatwoot inbox membership to
    match their inbox scope. Do not rely on dealer or department scopes to
    hide conversations in Chatwoot's own screens.
  - Wave 3 option: a backend-proxied Cases list (`GET /cases` on the backend,
    filtered by the same scope) that the fork's Cases page reads instead of
    Chatwoot `/conversations`.
- **What data scopes do confine**: Customer 360 results, the case fields panel,
  `/metrics/dealer-escalation` and `/metrics/case-aging`, escalation routing
  (dealer rows for dealer-only scopes, PIC rows for department-only scopes),
  RSA incidents (by their dealer) and the AI activity monitor.
- **Reports**: today, **inbox-, team- and department-scoped users get no
  reports at all.** Every `/metrics/*` report, the dashboard, the export and
  the anomaly and control-item reports return 403 for them. Only a
  **dealer-only** scoped user gets reports, and only two:
  `/metrics/dealer-escalation` and `/metrics/case-aging`, filtered to their
  dealers. A user scoped on dealers plus anything else also gets none. The
  reason is that no warehouse view has an inbox or team column, and the views
  with a department column sit next to views without one in the same report.
  Every other report is refused rather than shown unfiltered. Adding the
  columns to the views is a later change.
- **Admin surfaces not governed by data scope.** These are gated by their RBAC
  permission only. Anyone holding that permission sees and edits them in full,
  whatever their scope:
  - workforce / presence dashboard;
  - audit log;
  - SLA policies;
  - escalation-ladder policy and settings;
  - case taxonomy admin;
  - DMS/TSP integration admin;
  - custom features (superadmin switchboard).

  Grant those permissions only to unrestricted roles.
- **Dealer region edit point.** A dealer-only scoped user who holds
  `escalation.manage` can edit their own dealer's routing row, including its
  `region`. The region decides which PRO-NET regional contacts (Area/Regional
  Manager, HOD) are CC'd on that dealer's escalations. PRO-NET rows themselves
  are unrestricted-only. If dealers must not re-point their own region, do not
  give dealer roles `escalation.manage`.
- **Administrators**: the RBAC administrator role and platform superadmins are
  never restricted. To confine someone, take them off that role. Do not
  tick "Chatwoot access" on a role whose members are Chatwoot administrators:
  the Chatwoot-access mirror replaces their Chatwoot administrator role with a
  custom role.

## 8. Agent rebuild prerequisites (Task 32)

The agent changes (backend-client timeouts, bounce matching by Message-ID) ship
only with an agent image rebuild. Do NOT rebuild `proton-agent` until:

1. The `ESCALATION_POLICY_*` vars are restored to `tenants/proton.env` (the
   running container holds them; the env file lost them).
2. Bahana changes in `/opt/platform/agent` are reviewed (VM source lags
   `dev-yuda`; apply each commit's diff with `patch --dry-run`, never copy files).
   The agent was not rebuilt in Wave 1, so its diff starts at whatever
   `/opt/platform/agent` was last synced from: compare the VM tree with
   `dev-yuda` first and build the patch from that base, not from 085f4f25.
3. After the rebuild, check every agent key name you set is an agent Settings
   field: `docker exec proton-agent python3 -c "from app.config import Settings;
   print(sorted(Settings.model_fields))"` (names only, no values).

Optional new env keys (all have safe defaults, so none is required):

| Key | Default | Purpose |
|---|---|---|
| `PROTON_BACKEND_TIMEOUT_SECONDS` | 10 | config/short backend calls (incl. `/escalation/contacts` and `/escalation/notify`, 7.9 s observed) |
| `PROTON_BACKEND_SLOW_TIMEOUT_SECONDS` | 60 | `/chat/turn` |
| `PROTON_ASSIST_TIMEOUT_SECONDS` | 30 | `/assist/suggest` (escalation-reply AI draft; was dropped at 10.5 s) |

Suggested proton values if `/escalation/contacts` still times out: `PROTON_BACKEND_TIMEOUT_SECONDS=20`.
Bounce matching needs no new env; it still requires `BOUNCE_HANDLING_ENABLED=true`.
