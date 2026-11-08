# Proton RFP fix plan Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix every gap in the tracker's "Fix plan" tab (FIX-01 to FIX-55) that Devoteam can fix without Proton input, deploy the pre-demo wave before the 17 Nov PRO-NET demo, re-test, and record results in the sheet.

**Architecture:** Two waves. Wave 1 (Tasks 1-16, by 13 Nov) is live config, one user-run env script, a BigQuery pipeline and small test-driven fixes in `backend/` and the Chatwoot fork, deployed to proton. Wave 2 (Tasks 17-30, 18 Nov to 12 Dec) is the larger builds, developed and tested on `dev-yuda` and deployed after the demo. Items that need Proton are listed at the end and stay as they are in the sheet, with a note.

**Tech Stack:** FastAPI backend (`backend/apps/backend`, pytest, uv), Chatwoot v4.15.1 fork (`deploy/chatwoot-fork/patches`, Vue SPA plus Rails patches), Docker Compose on GCE VM `crm-ticketing` (asia-southeast2-a, project `lv-playground-genai`), Twilio Voice, Firestore, BigQuery, Cloud Monitoring, gws CLI for Sheets.

**Spec:** Tracker sheet https://docs.google.com/spreadsheets/d/1mqZphjnBxU4BYUCwc0Nu-Lf0yYbqGEV590Oo_76uqDU (tabs "Fix plan", "RFP mapping", module tabs with Auto result columns K-M); RFP `docs/client-materials/RFP 2026_028/RFP 2026_028 CRM 260728 TECHNICAL v1.0.pdf`; our answers `docs/proposals/build_pronet_rfp2026_028_response_docx.py`.

## Global Constraints

- User decisions (8 Nov): reports = BigQuery pipeline; WhatsApp AI mode stays **auto**; add a **48-hour** unresolved ladder step; untick Chatwoot access on RBAC role `proton_team`; inbox 5 uses the Devoteam Gmail `devotech29@gmail.com` until Proton IT gives the pronet.my mailbox.
- Wave 1 must be green by **12 Nov**; rehearsal **13 Nov**; a Wave 1 fix not green by 12 Nov waits for Wave 2. Wave 2 deploys after **17 Nov**.
- Work in worktree `/Users/yudaadipratama/Archive/id-crm-ticketing-fix` on branch `dev-yuda`. Never merge to `main`; never push unless asked. Remove the worktree at the end so the user can check out `dev-yuda` in `id-crm-ticketing`.
- VM `/opt/platform` lags `dev-yuda`: deploy backend changes by applying each commit's diff to the VM copy of each touched file (`patch --dry-run` first), never by copying files. Recreate with `docker compose -p proton -f docker-compose.tenant.yml --env-file tenants/proton.env up -d --build backend`.
- **Never rebuild or recreate `proton-agent`** (its running env holds `ESCALATION_POLICY_*` vars the env file lost). Any agent-side change waits until those vars are restored to the env file by the user.
- Chatwoot image: Cloud Build only (`gcloud builds submit deploy/chatwoot-fork/ --config deploy/chatwoot-fork/cloudbuild.yaml --substitutions _REGISTRY=<proton AR repo>`), tags `v4.15.1-custom-rc16` (Wave 1) and `rc17+` (Wave 2). Then `pull` and `up -d --force-recreate chatwoot-rails chatwoot-sidekiq`.
- Tenant env edits go into scripts under `scripts/ops/` that the user runs; agents never edit `tenants/*.env`.
- Never print secrets. Never message real customers (inbox 3 WhatsApp; inbox 5 except mail from Devoteam test senders). Test data tagged `[TEST]`, label `audit_test`. Test emails only to `devotech29+<role>@gmail.com`.
- Keep `TAXONOMY_CHATWOOT_SYNC_ENABLED` off. Create no Chatwoot automation rules.
- Backend tests: `cd backend/apps/backend && uv run pytest -q`. Agent tests: `cd agent && pytest -q`.

## Review Focus

- A replied or acknowledged dealer case still gets the 48 h unresolved alert; a resolved one never does (Task 6).
- An agent without `taxonomy.manage` gets 403 on taxonomy writes while any logged-in user can still read the tree for the conversation sidebar (Task 5).
- RSA endpoints with only the shared key are refused once RBAC is on, without breaking the RSA page for `sla.manage` users (Task 8).
- The env script touches `tenants/proton.env` only; aeon360 and bahana behave exactly as before (Task 3).
- Unticking Chatwoot access leaves users 10-13 as `administrator`, never with no role (Task 1).

---

## Wave 1: before the demo

### Task 1: Live config and routing data (FIX-01, FIX-07 data, FIX-26 label)

- [ ] **Step 1 (FIX-01):** Remove the four `chatwoot.*` permissions from RBAC role `proton_team` (Roles & Permissions page as user 1 or 9, or the roles API with a session). Keep its other permissions.
- [ ] **Step 2:** Rails runner `AccountUser.where(user_id: [10,11,12,13]).pluck(:user_id,:role,:custom_role_id)` -> all `administrator`, `custom_role_id` nil; clear `custom_role_id` on those four rows only if the mirror left it.
- [ ] **Step 3 (FIX-07):** In Escalation Routing set a PIC for product, network, charging, apps, others, marketing and technical: name `[TEST] PIC <dept>`, email `devotech29+pic_<dept>@gmail.com`. Dealer `petaling_jaya`: CRE, Sales/AS Mgr, Principal, ARM, HOD, Owner = `devotech29+<role>@gmail.com`. Escalation manager `devotech29+escmgr@gmail.com`. Sheet note: real PICs come from Proton.
- [ ] **Step 4:** Ladder dry run ON (the demo runbook turns it off for the demo window only).
- [ ] **Step 5 (FIX-26):** Rename custom status `busy` label to `Busy (On Call)`.
- [ ] **Step 6: Verify:** users 10-13 administrator; 0 departments without PIC; ladder `dry_run=true`.

### Task 2: Email inbox end to end (FIX-02, FIX-20)

- [ ] **Step 1:** Conversations 168-173 (real mail imported on first sync): resolve them and add label `audit_test` (do not delete without the user's yes).
- [ ] **Step 2:** The user sends `[TEST] Brake noise X70` from a Devoteam mailbox to `devotech29@gmail.com`. Check it appears in inbox 5 within 2 min; reply from the CRM; check the reply arrives in the same thread.
- [ ] **Step 3 (FIX-20):** On that conversation run: department suggestion note; `dept_aftersales` + `escalate` -> two emails within 15 s (ack to sender, forward to `devotech29+pic_aftersales@gmail.com`, Reply-To `devotech29+case<id>@gmail.com`), `escalation_notified_at` set; reply from the PIC address -> private note, AI draft, `escalation_replied` label within 40 s; group email to a `[TEST]` dealer group includes the 2 photos; reply to `nobody@invalid-domain-test.example` -> bounce note.
- [ ] **Step 4: Verify:** CONV-006/007/020/021/022/031, ESC-008/010 Pass.

### Task 3: Env script (FIX-06 scope, FIX-11, FIX-28 to FIX-39)

**Files:**
- Create: `scripts/ops/proton_env_2026-11-10.sh`

- [ ] **Step 1:** Bash, `set -euo pipefail`; backs up `tenants/proton.env` to `proton.env.bak-20261110`; for each key replaces or appends `KEY=value` in `tenants/proton.env` only; prints `KEY=<set>`; ends with the backend recreate command. Keys: `SLA_INBOX_IDS=3,5`; `METRICS_PROVIDER=bigquery`; `METRICS_SYNC_ENABLED=true`; `BIGQUERY_DATASET=proton_crm`; `SENTIMENT_CLASSIFIER_ENABLED`, `SENTIMENT_TONE_ADJUSTMENT_ENABLED` (FIX-28); the NPS survey flag from `platform/config.py` and `CSAT_BY_AGENT_ENABLED` (FIX-29); `SLA_WORKING_HOURS_ENABLED` (FIX-30); `PHONE_AFTER_HOURS_ENABLED`, `PHONE_VOICEMAIL_INGEST_ENABLED` (FIX-31); `ESCALATION_PRESENCE_CHECK_ENABLED` (FIX-32); `RESOLVED_CASE_INDEX_ENABLED`, `AUTO_SUMMARY_ON_RESOLVE_ENABLED` (FIX-33); `TOKEN_METERING_ENABLED`, `AI_COST_REPORTING_ENABLED` (FIX-34); `ACW_ENABLED` (FIX-35); `ANOMALY_HOURLY_ENABLED` (FIX-36); `PRESENCE_THRESHOLD_ALERTS_ENABLED` (FIX-37); `FOLLOW_UP_DATE_ENABLED` (FIX-38); `ROUTING_FAIR_SHARE_ENABLED`, `ROUTING_SWEEP_ENABLED` (FIX-39), all `=true`.
- [ ] **Step 2:** Confirm each key exists in `backend/apps/backend/src/chatbot/platform/config.py` and is read by the backend. Any key read only by `agent/` goes in the script header as "needs agent recreate, not applied".
- [ ] **Step 3:** `bash -n` passes; run against a copy of a fake env and diff. Commit `chore(ops): proton env script for 10 Nov fixes`.
- [ ] **Step 4:** Hand to the user: `! bash scripts/ops/proton_env_2026-11-10.sh` (run from `/opt/platform/deploy` on the VM via ssh).
- [ ] **Step 5: Agent section** (separate flag `--agent`, run only after the backend part): first copy every `ESCALATION_POLICY_*` variable from the running `proton-agent` container into `tenants/proton.env` (read with `docker exec proton-agent printenv`, never print values), then set the agent keys from Task 4b Step 5, then recreate the agent from its existing image without building (`up -d --no-build agent`). Test the copy step against a fake container env in the shell test.

### Task 4: SLA policy and inbox 4 clean-up (FIX-06)

- [ ] **Step 1:** After Task 3: SLA Policies page, Scope = Tenant default, first response 15 min, resolution 8 h, warning at 80%, Tier-2 contact `devotech29+tier2@gmail.com`.
- [ ] **Step 2:** Remove inbox 4 entries from SLA Policies and Knowledge > Inboxes (delete the orphan rows in their stores).
- [ ] **Step 3: Verify:** a `[TEST]` email in inbox 5 shows an SLA timer; warning, breach note and PIC email fire on a 2-minute test policy scoped to inbox 5 (restore the real thresholds after).

### Task 4b: Proton process-flow wording and timings (FIX-56)

Source of truth: `docs/client-materials/CRM Process Flow (1).xlsx` (sheets WhatsApp Process, Social Media, Email, IVR Call; cell text and flowchart shapes). Copy Proton's wording verbatim, except obvious typos ("Does your case is resolve?" -> "Is your case resolved? YES/NO"; "pelanngan" -> "pelanggan"), and list every correction in the sheet Notes.

- [ ] **Step 1: Business hours.** Inboxes 3 (WhatsApp) and 5 (Email): Mon-Fri 08:30-17:30, Sat-Sun and public holidays 09:00-17:00 (Asia/Kuala_Lumpur).
- [ ] **Step 2: After-hours auto-reply** (WhatsApp B18, Social B20) as the out-of-office message on inbox 3 (and future Messenger/Instagram inboxes).
- [ ] **Step 3: Lifecycle messages** in Knowledge > Settings for the WhatsApp assistant: welcome = the AI DISCLAIMER (WhatsApp B7); idle warning = "Your chat will close in 5 minutes if we do not hear from you"; auto-close = "Closed due to inactivity"; resolve check = "Is your case resolved? YES/NO"; AI rating survey prompt.
- [ ] **Step 4: Email auto-acknowledgement** text (Email A11) into the email ack template; rules: once per new email/thread, never on a reply in the same thread, never on agent replies.
- [ ] **Step 5: Timings** into the env script (Task 3, agent section): idle warning after 10 min, close 5 min later (15 min total in hours; 10 min total out of hours: `LIFECYCLE_IDLE_CLOSE_OUT_OF_HOURS_GRACE_MINUTES=0`), `LIFECYCLE_DISCLAIMER_ENABLED=true`, `LIFECYCLE_SURVEY_ENABLED=true`, `EMAIL_AUTOACK_ENABLED=true`, `LIFECYCLE_AUTO_CATEGORIZE=true` (bot-resolved cases get a category).
- [ ] **Step 6: Per-channel response targets** into SLA (Task 4 and `SLA_ACK_MINUTES_BY_CHANNEL_JSON`): call/WhatsApp escalation to live agent acknowledged within 2 min; social within 2 working hours; email status update within 4 working hours; call answered within 20 s.
- [ ] **Step 7: IVR** (IVR Call sheet): bilingual welcome and language menu (English 1 / Bahasa Melayu 2), then RSA 1 / Inquiry 2 / Complaint 3 / repeat 0 in each language; queue-busy prompt after 10 s of ringing (EN and BM text, B29/B30); after-hours message plus voicemail; female voice. Map onto `PHONE_DTMF_MENU_ENABLED` and the phone prompt settings in `platform/config.py`; anything with no setting becomes a Wave 2 code item in the sheet.
- [ ] **Step 8: Verify:** a WhatsApp-path replay shows the disclaimer first; an out-of-hours message gets the auto-reply; a new test email gets exactly one acknowledgement; a reply in that thread gets none.

Out of CRM scope (record in sheet Notes only): the SSI Process sheet (dealer survey in the e.MAS app).

### Task 5: Taxonomy admin auth (FIX-10)

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/taxonomy/router.py`, `backend/apps/backend/src/chatbot/features/authz/deps.py`, `backend/apps/backend/src/chatbot/main.py`
- Test: `backend/apps/backend/src/chatbot/features/taxonomy/test_router.py`

**Interfaces:**
- Produces: `require_authenticated(*, repo, validator, settings)` in `authz/deps.py` (same headers and token resolution as `require_permission`; any valid session passes; shared-secret fallback when RBAC is off).

- [ ] **Step 1: Failing tests:** `test_write_with_permission_succeeds` (200), `test_write_without_permission_is_403`, `test_tree_without_session_is_401_when_rbac_on`, `test_tree_with_any_session_is_200`.
- [ ] **Step 2:** `uv run pytest src/chatbot/features/taxonomy/test_router.py -v` -> 4 FAIL.
- [ ] **Step 3:** Router builder takes `authz_repo` and `validator`; writes use `require_permission("taxonomy.manage", repo=authz_repo, validator=validator, settings=settings)`; `GET /tree` uses `require_authenticated`.
- [ ] **Step 4:** Tests and full suite green.
- [ ] **Step 5:** Commit `fix(taxonomy): wire RBAC repo/validator into admin routes`.

### Task 6: 48-hour unresolved ladder step (FIX-07)

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/chat/escalation_policy.py` (`EscalationStep`, `DEFAULT_STEPS`, `load_steps`), `escalation_ladder.py` (sweep and in-flight view)
- Test: the existing ladder and policy test files next to them

**Interfaces:**
- Produces: `EscalationStep.fires_after_reply: bool = False`; step 6 in `DEFAULT_STEPS`: `step_no=6, delay_working_hours=48.0, to_roles=("owner",), cc_roles=("principal","area_regional_mgr","hod"), label="48H UNRESOLVED", fires_after_reply=True`.

- [ ] **Step 1: Failing tests:** `test_step6_fires_at_48_working_hours_when_unresolved`, `test_step6_fires_even_if_dealer_replied`, `test_step6_fires_even_if_acknowledged`, `test_step6_never_fires_when_resolved`, `test_steps_1_to_5_still_stop_on_reply`, `test_load_steps_reads_fires_after_reply_default_false`.
- [ ] **Step 2:** Run -> FAIL.
- [ ] **Step 3:** `_should_skip` keeps its reasons; the sweep treats `dealer_replied` and `acknowledged_by_agent` as skip only when the due step has `fires_after_reply=False`; `resolved` always skips. Ladder settings page (patch 0071) shows a read-only "Step 6 — 48 h unresolved" row (UI change ships in Task 13).
- [ ] **Step 4:** Tests and full suite green.
- [ ] **Step 5:** Commit `feat(escalation): 48h unresolved step per RFP 3.2.4`.

### Task 7: FAQ content and FAQ strip (FIX-09) — use superpowers:systematic-debugging for the strip

- [ ] **Step 1:** Build `scratchpad/demo_faqs.csv` (question,answer,keywords): 20 rows on service booking, service interval, warranty, charging, RSA and apps login, each answer taken from the indexed Knowledge documents (doc title in keywords). Bulk upload. Sheet note: content to be reviewed by Proton.
- [ ] **Step 2:** Reproduce the missing strip on conv 160 in Chrome: capture the SPA feature list and the `/kb/suggest` call. Write the root cause in the task report before changing anything.
- [ ] **Step 3:** Fix in `deploy/chatwoot-fork/patches/0056-faq-composer-apply.patch` (or config if it is config). Patch must still `git apply` on upstream.
- [ ] **Step 4:** Commit `fix(fork): FAQ strip renders when suggestions exist` (ships in Task 13).

### Task 8: RSA routes behind RBAC (FIX-24)

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/rsa/rsa_router.py`, `main.py`; `deploy/chatwoot-fork/patches/0035-rsa-incident-log.patch` if the page does not send session headers
- Test: `backend/apps/backend/src/chatbot/features/rsa/test_rsa_router.py`

- [ ] **Step 1: Failing tests:** `test_rsa_shared_key_only_is_401_when_rbac_on`, `test_rsa_write_without_sla_manage_is_403`, `test_rsa_with_sla_manage_session_is_200`, `test_rsa_shared_key_ok_when_rbac_off`.
- [ ] **Step 2-4:** `require_permission("sla.manage", repo=..., validator=..., settings=...)` on every `/rsa/*` route; page sends session headers; tests green.
- [ ] **Step 5:** Commit `fix(rsa): require sla.manage session on RSA routes`.

### Task 9: Customer 360 plate search and RSA phone (FIX-22)

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/chat/customer360_router.py`, `features/rsa/rsa_repository.py`, `features/rsa/rsa_db.py`, RSA page in patch 0035 (phone field)
- Test: `features/chat/test_customer360_router.py` (create if missing), `features/rsa/test_rsa_repository.py`

- [ ] **Step 1: Failing tests:** `test_vehicle_search_matches_car_plate_attribute` (the attribute the Cases "Car Plate" column reads, patch 0043), `test_vehicle_search_normalises_spaces_and_case` (`"bmt 5518"` finds `BMT5518`), `test_phone_search_returns_rsa_incidents_with_that_phone`, `test_rsa_phone_optional`.
- [ ] **Step 2-4:** Vehicle search is one Postgres query on a normalised expression (no per-conversation scan), target < 3 s on proton data. Tests green.
- [ ] **Step 5:** Commit `feat(customer360): plate search and RSA phone link`.

### Task 10: Translate language picker (FIX-21)

**Files:**
- Modify: `deploy/chatwoot-fork/patches/0055-translate-action.patch`; backend translate route if it lacks a `target` parameter
- Test: backend translate route test

- [ ] **Step 1: Failing test:** `test_translate_accepts_target_ms_zh_ta_en` (400 on anything else); `test_outbound_tamil_still_403_when_flag_off`.
- [ ] **Step 2-4:** Translate opens a menu English / Bahasa Malaysia / 中文 / தமிழ்; default target = English for customer messages; draft translation uses the picked language. Tests green.
- [ ] **Step 5:** Commit `feat(fork): translate language picker` (ships in Task 13).

### Task 11: BigQuery reporting pipeline and Power BI (FIX-11, FIX-12)

- [ ] **Step 1:** Create dataset `lv-playground-genai:proton_crm` in the same location as `demo_proton`; grant the proton-backend service account Data Editor on it and Job User on the project (identify the SA without printing the key).
- [ ] **Step 2:** After Task 3: run one sync, then `ensure_views()`.
- [ ] **Step 3: Verify:** `proton_crm.conversations` row count = Chatwoot proton conversation count; `/metrics/dashboard` non-empty; Weekly Report shows Case Volume.
- [ ] **Step 4 (FIX-12):** Power BI Desktop connects to `proton_crm` views; one channel-source page saved as `docs/client-materials/proton-powerbi-starter.pbix` (instructions in `docs/runbooks/powerbi-proton.md`).

### Task 12: AI checks on WhatsApp (FIX-08, FIX-13)

- [ ] **Step 1 (FIX-08):** After Task 7 FAQs: replay "brp lama siap? nk service", "kete sy rosak kat highway, tlg" and the warning-light photo through the WhatsApp agent path with the Bahana replay harness pattern (offline, no customer). Expected: correct intent each, a follow-up question on the photo. If not: tighten the agent instruction in Knowledge > Settings (persona augments only), re-run.
- [ ] **Step 2:** Confirm a WhatsApp-path conversation gets `case_*` taxonomy filled (replay or a Devoteam handset).
- [ ] **Step 3 (FIX-13):** Devoteam handset calls in EN, BM, ZH, TA; each answered in the caller's language. If one fails, adjust `PHONE_LANGUAGE_NUDGE` settings in the next env script.

### Task 13: Fork image rc16 (Tasks 6 UI row, 7, 8, 9, 10)

- [ ] **Step 1:** Cloud Build tag `v4.15.1-custom-rc16` with every Wave 1 fork patch that is green.
- [ ] **Step 2:** Update the proton compose image tag, `pull`, `up -d --force-recreate chatwoot-rails chatwoot-sidekiq`.
- [ ] **Step 3: Verify:** `/app/login` 200; sidebar renders; translate picker, FAQ strip, RSA phone field and step 6 row visible.

### Task 14: Deploy backend Wave 1 (Tasks 5, 6, 8, 9, 10)

- [ ] **Step 1:** For each commit, apply its diff to the VM copy with `patch -p1 --dry-run`, then for real; stop if any hunk fails.
- [ ] **Step 2:** Recreate backend only; `docker logs proton-backend --since 5m` has no tracebacks; `/healthz` 200.
- [ ] **Step 3:** Record deployed commits in the sheet Notes.

### Task 15: Demo prep (FIX-40, FIX-41, FIX-42)

- [ ] **Step 1 (FIX-41):** Update the exec deck (Google Slides `1_jZ50_G_nyzeh0xXweLLhJuUIvGVvSBS0Z6Qi8yZ8eg`) wording: ladder 2/4/8 h plus 48 h; email-only escalation; Tamil outbound off; no email auto-ack; reports now live; Wave 2 and Proton-dependent items shown as the delivery plan with dates.
- [ ] **Step 2 (FIX-42):** Update the demo guide Google Doc `14ZAT5glL_Ln4s0Vz7M0z-pt8qBnL4dz8FCZDh5dyLqo`: avoid "last 7 days" filter, hold/transfer, plate search if Task 9 is not deployed; email now via `devotech29@gmail.com`.
- [ ] **Step 3 (FIX-40):** 13 Nov rehearsal from the guide's run of show; log issues in the sheet.

### Task 16: Re-test and update the sheet (Wave 1)

- [ ] **Step 1:** Presence index READY (`gcloud firestore indexes composite list --database=proton-db`), then re-run the five automated groups with `scratchpad/AUTO_BRIEF.md`, writing `auto_results_*_rerun.json`.
- [ ] **Step 2:** Write results to columns K-M with run date "12 Nov 2026".
- [ ] **Step 3:** In "Fix plan" set Status and Re-test result per FIX id with one-line evidence.
- [ ] **Step 4:** Clean-up: only resolved `[TEST]` conversations and their contacts remain.

## Wave 2: after the demo (develop from 14 Nov, deploy from 18 Nov)

Each task below starts with superpowers:brainstorming for the open design choices listed, then follows the same TDD steps (failing tests, implement, green, commit) and ships in Fork image rc17+ / a backend deploy as in Tasks 13-14.

### Task 17: PDF export and scheduled report email (FIX-14) — target 21 Nov
- **Files:** `backend/.../features/metrics/export.py`, `export_router.py`, `scheduler.py`; Weekly Report page patch 0044.
- **Tests:** `test_weekly_report_pdf_has_all_sections`, `test_scheduled_email_sends_pdf_and_xlsx_to_configured_list`, `test_schedule_off_by_default`.
- **Uses:** SMTP from inbox 5 Gmail via a dedicated report sender setting (env script).

### Task 18: Abandoned-call rate (FIX-16) — target 25 Nov
- **Files:** voice status-callback handler in `backend/.../features/` (Twilio call status), metrics query port, call report section.
- **Tests:** `test_queue_hangup_counts_as_abandoned`, `test_answered_call_not_abandoned`, `test_abandon_rate_by_day`.

### Task 19: Escalation audit events (FIX-17) — target 26 Nov
- **Files:** audit store (`features/chat/adapters/audit_l*`), escalation notify, ladder sweep, reply linking, SLA alerts.
- **Tests:** `test_escalation_notify_writes_audit_with_user_and_sla_status`, `test_ladder_step_writes_audit`, `test_reply_link_writes_audit`, `test_admin_role_change_writes_before_after`.

### Task 20: Idle timeout (FIX-18) — target 26 Nov
- **Files:** new fork patch `0079-session-idle-timeout.patch` (Devise/devise_token_auth token lifespan from `SESSION_IDLE_TIMEOUT_MINUTES`, default 30; SPA logs out after idle).
- **Tests:** Rails request spec `expired_token_returns_401`; SPA unit test for idle logout.

### Task 21: Softphone hold and transfer (FIX-15) — target 28 Nov
- **Files:** `deploy/chatwoot-fork/patches/0069-agent-softphone.patch`, backend voice/call router (Twilio conference for hold and transfer).
- **Design choices:** warm vs cold transfer; hold media URL (placeholder tone until Proton supplies their recording).
- **Tests:** `test_hold_moves_caller_to_hold_media`, `test_transfer_to_agent_rings_target_and_keeps_case`, `test_resume_from_hold`.

### Task 22: FAQ rating in the strip (FIX-23) — target 21 Nov
- **Files:** patch 0056, `features/metrics/faq_feedback*.py`, FAQ admin page.
- **Tests:** `test_feedback_post_records_score_per_faq`, `test_faq_list_shows_average_score`.

### Task 23: MFA (FIX-25) — target 21 Nov
- **Files:** patch 0032 (show MFA section only), env script adding ActiveRecord encryption keys (user runs).
- **Tests:** enrol one Devoteam test user; login asks for code.

### Task 24: AI activity monitor (FIX-43) — target 28 Nov
- **Files:** new backend route `GET /admin/ai-activity` (reads `ai_actions` and handoff sessions), new fork page.
- **Tests:** `test_lists_live_ai_conversations_with_mode_and_last_action`, `test_requires_permission`.

### Task 25: Password expiry (FIX-44) — target 28 Nov
- **Files:** fork Rails patch: `password_changed_at`, prompt after 90 days.
- **Tests:** `test_login_after_90_days_requires_change`, `test_self_service_change_resets_clock`.

### Task 26: AI duplicate case detection and merge (FIX-46) — target 5 Dec
- **Files:** agent-side service on `conversation_created` (match open cases by phone, email, plate, chassis), private note with merge suggestion, PIC notification; agent recreate needs the restored env first (Global Constraints).
- **Tests:** `test_same_phone_open_case_suggests_merge`, `test_resolved_case_not_matched`, `test_merge_notifies_assigned_pic`.

### Task 27: AI performance reports (FIX-47) — target 12 Dec
- **Files:** `features/metrics/bigquery_schema.py` views for AI resolution, AI vs human, AI CSAT, AI escalation reasons, accuracy, deflection, root cause, KB suggestions; Bot report sections.
- **Tests:** one view test per report against fixture rows (`test_ai_deflection_rate_view`, etc.).

### Task 28: Data-scoped permissions (FIX-48) — target 12 Dec
- **Files:** `features/authz/` (scope by inbox, team, dealer), remove the startup refusal for `DATA_SCOPED_RBAC_ENABLED`.
- **Design choices:** default scopes proposed to Proton for sign-off (sheet note).
- **Tests:** `test_scoped_user_sees_only_their_inbox_conversations`, `test_reports_filtered_by_scope`.

### Task 29: PII masking mechanism (FIX-49) — target 12 Dec
- **Files:** masking service in backend (phone, IC number, email patterns) applied to non-privileged roles in conversation and report views.
- **Design choices:** default rules (mask IC and full phone for roles without `pii.view`); Proton compliance confirms the final rule list (sheet note).
- **Tests:** `test_ic_number_masked_for_agent`, `test_unmasked_for_pii_view`.

### Task 30: Backlog fixes (FIX-51, FIX-52, FIX-53) — target 5 Dec
- FIX-51: reproduce `days_before` 500 in Chatwoot filter; fix in a fork patch or document upstream bug.
- FIX-52: `LIFECYCLE` sweep interval <= grace period (env script) or reword the warning.
- FIX-53: FAQ bulk import all-or-nothing; test `test_bulk_with_one_bad_row_imports_nothing`.
- Then re-test and update the sheet as in Task 16, run date of that wave.

## Needs Proton: left as is in the sheet, with notes

- **FIX-27** agent user 15 role: Proton to confirm who "Jacips Business" is.
- **FIX-45** Messenger and Instagram: Proton grants Meta business page admin access.
- **FIX-50** DMS / TSP: Proton provides API spec, test endpoint and credentials.
- **FIX-55** inbox 5 to the real `e.m…@pronet.my` mailbox: Proton IT provides IMAP/SMTP access; until then `devotech29@gmail.com`.
- Real PIC and dealer contacts (part of FIX-07), Proton's hold recording (Task 21), final PII rules (Task 29) and data-scope rules (Task 28): Devoteam builds with defaults, Proton supplies the final values.
- **FIX-54** email recall: declared "Non" in our response; no action.
