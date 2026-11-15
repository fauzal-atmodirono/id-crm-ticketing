# Escalation Routing UI Refresh (+ availability menu scroll) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make CRM > Escalation Routing readable at a glance (what is routed where, what is missing, what is escalating right now) and let operators edit it without wide inline-edit tables; and fix the sidebar availability menu that cannot be scrolled to its lower entries.

**Architecture:** Two new Chatwoot-fork patches. `0100` fixes the availability dropdown height. `0101` replaces the 1,017-line single page (built up by patches 0039, 0046, 0051, 0052, 0070, 0071, 0076) with a tabbed shell plus small components. **Frontend only:** every API call, payload and permission (`escalation.manage`) stays exactly as today; no backend change.

**Tech Stack:** Vue 3 (Options API in the existing page; new components may use `<script setup>` like `components-next`), Tailwind with Chatwoot `n-*` tokens, `dashboard/api/protonAdmin` (`listPics`, `upsertPic`, `deletePic`, `listDealers`, `upsertDealer`, `deleteDealer`, `adminRequest`), `useCustomFeatures().t` term dictionary. Fork pipeline: `deploy/chatwoot-fork/` (patches `git apply`-ed onto upstream v4.15.1 at Cloud Build time).

**Spec:** user, 16 Nov 2026: "the Escalation Routing view UI, it's a mess to see and understand" and "I can't scroll down to choose my availability status". Design decisions below were made by Claude — **confirm them before execution.**

## Design decisions (confirm before execution)

Today's problems, from the current page: four stacked blocks on one scroll (ladder form → in-flight table → PIC add form → 7-column PIC table → large dealer add form → dealer table); add forms are always open above the lists; editing turns a table row into a row of 32–48-px-wide inputs; nothing says what is missing (a dealer with no Principal silently skips step 3); the ladder's five timers are a grid of number boxes with no picture of the sequence.

1. **Tabs**, remembered in the URL query (`?tab=`): **Overview** · **Departments** · **{{ t('partner', 'plural') }}** · **Ladder**.
2. **Overview** — three parts, top to bottom:
   - a status banner: ladder `Off` / `Dry run (nothing is emailed)` / `LIVE — emailing real contacts`, from the ladder GET `effective` block (amber for live);
   - "Needs attention" checklist computed client-side: departments without a PIC email; dealers with no members and no named contacts; dealers missing Principal (step 3 skipped) or Owner (step 4 skipped); dealers without a PRO-NET region (no ARM/HOD CC). Each item links to its tab with the record's drawer open;
   - **In flight** table (today's data) with the case number linking to the conversation, the state as a coloured chip, "due in" as text.
3. **Departments** and **{{ partner plural }}** tabs: a search box, then a compact list — one row per record, at most 4 columns (Departments: name · PIC name + email · CC count · tier-2 or "PIC (fallback)"; dealers: name · region · member count · ladder coverage as 4 role pills, filled or empty with "skipped" tooltip). Row click or **Edit** opens a right-side **drawer** holding the full form; **Add** button top right opens the same drawer empty. **Delete** lives in the drawer footer with a confirm. No inline table editing anywhere.
4. **Dealer drawer** sections: *Basics* (group name, PRO-NET region), *Members* (one email per chip, add with Enter/comma, invalid email shown red and blocks Save), *Ladder contacts* (CRE, Sales/Aftersales Mgr → Step 1; Principal → Step 3; Owner → Step 4; each with the existing "blank = rung skipped" hint).
5. **Ladder** tab: the banner and the Enabled / Dry run / Check-every controls on top; then a vertical **timeline** of steps 1–6 — each step a card showing *when* (editable working hours for 2–5; "0 h" for 1; "48 h, fixed" for 6), *who is emailed* (role names from the dealer roles), *CC*; Save at the bottom. The "real contacts" warning stays verbatim.
6. Wording keeps the term dictionary (`t('partner')`) everywhere the current page uses it; copy is plain English, no internal codes (`step3_hours` never shows).

## Global Constraints

- No backend/API change: same endpoints (`/admin/escalation/...` via `protonAdmin.js` + `LADDER_PATH = '/admin/escalation/ladder'` and `${LADDER_PATH}/in-flight`), same request bodies (`cc_emails` and `emails` stay arrays; blank ladder number → `null`, never `''` or `0`, via the existing `numOrNull`).
- Keep `name: 'ProtonEscalationRoutingPage'` and the route; the page stays gated exactly as today.
- New fork files go under `app/javascript/dashboard/views/escalationRouting/`; the page file stays the entry point.
- This sandbox cannot reach github.com: build patch context from the existing patch chain (`deploy/chatwoot-fork/tests/simulate_patch_chain.sh <file>`), never from a guessed upstream file. Only Cloud Build proves the vite build.
- Generate patches with `rtk proxy git diff` / `git format-patch` (RTK filters plain `git diff` output).
- Build for amd64 with Cloud Build only (`gcloud builds submit deploy/chatwoot-fork/ --config deploy/chatwoot-fork/cloudbuild.yaml`), never on the prod VM, never a local arm64 build.

## Review Focus

- A dealer saved with only named contacts and no members must still save (today's rule: "at least one member email or named contact") — drawer validation test.
- Editing a record and pressing Cancel must leave the list unchanged (the old page copied into `editPicForm`; the drawer must work on a copy too).
- Ladder blank fields must send `null`, not `0` — a 0-hour reminder would email principals immediately.
- An in-flight load error must show its message and not hide the rest of the Overview (today `inFlightError` is shown inline; keep it scoped to that card).
- A long PIC/dealer list (40+ dealers) must stay usable: search filters by name, email and region, case-insensitive.

---

### Task 1: Availability menu scrolls (patch 0100)

**Files:**
- Create: `deploy/chatwoot-fork/patches/0100-availability-menu-scroll.patch` (modifies upstream `app/javascript/dashboard/components-next/sidebar/SidebarProfileMenuStatus.vue`, already patched by 0065)
- Test: `deploy/chatwoot-fork/tests/test_availability_menu_scroll_patch.py`

- [ ] **Step 1: Get the real post-0065 file.** The user runs (read-only): `! gcloud compute ssh crm-ticketing --project lv-playground-genai --zone asia-southeast2-a --command 'sudo docker exec proton-chatwoot-rails cat /app/app/javascript/dashboard/components-next/sidebar/SidebarProfileMenuStatus.vue'`. If the source is not in the image, add a one-off Cloud Build step that clones v4.15.1, applies patches up to 0065 and prints the file. Confirm the cause on the live page first: with the browser at a normal laptop height, open the menu — the catalogue (custom statuses + Offline, see 0065) runs past the viewport bottom.
- [ ] **Step 2: Failing test** — asserts patch 0100 touches only that file, adds `max-h-[60vh] overflow-y-auto` (or the equivalent the real `DropdownBody` accepts) to the `DropdownBody` that renders `availabilityStatuses`, and removes nothing else.
- [ ] **Step 3: Write the patch** against the real file from Step 1 (context lines copied from it, not guessed). If `DropdownBody` sits inside a container positioned from the bottom, also make it open upward when there is no room — decide from the real markup.
- [ ] **Step 4: Run** `cd deploy/chatwoot-fork && python -m pytest tests/test_availability_menu_scroll_patch.py -v` → PASS.
- [ ] **Step 5: Commit** the patch and test.

---

### Task 2: Shell with tabs, shared helpers, Overview tab (patch 0101, part 1)

**Files:**
- Modify: `app/javascript/dashboard/views/ProtonEscalationRoutingPage.vue` (becomes the shell: loads pics, dealers, ladder, in-flight once; holds them; renders tabs)
- Create: `app/javascript/dashboard/views/escalationRouting/routingHelpers.js`, `OverviewTab.vue`, `LadderStatusBanner.vue`
- Test: `deploy/chatwoot-fork/tests/test_escalation_routing_refresh_patch.py`

Work in a scratch repo built by `tests/simulate_patch_chain.sh ProtonEscalationRoutingPage.vue` (it leaves the post-0076 file); export the diff as `patches/0101-escalation-routing-refresh.patch` after each task (one patch, re-exported).

**Interfaces:**
- Produces (`routingHelpers.js`): `splitEmails(value) -> string[]`, `numOrNull(value) -> number|null`, `isEmail(s) -> boolean`, `DEALER_ROLES` (moved verbatim from the page), `LADDER_RUNGS` (moved verbatim), `contactsFrom(form) -> object`, `attentionItems(pics, dealers) -> Array<{kind: 'pic'|'dealer', id: string, text: string}>`, `ladderMode(effective) -> 'off'|'dry_run'|'live'`.
- Produces (shell): props passed to every tab — `pics`, `dealers`, `ladder` (`{form, effective}`), `inFlight`, `inFlightError`; events from tabs — `saved` (shell reloads that collection), `open-record({tab, id})`.

- [ ] **Step 1: Failing test** — checks in the exported patch: the shell has a tab bar with the four labels (`Overview`, `Departments`, `t('partner', 'plural')`, `Ladder`) and reads/writes `$route.query.tab`; `attentionItems` covers the five cases (missing PIC email; dealer with no members and no contacts; missing `principal` → "step 3 skipped"; missing `owner` → "step 4 skipped"; missing `region`); `LadderStatusBanner` has the three modes; in-flight rows link to the conversation route with the case id.
- [ ] **Step 2: Run** `python -m pytest tests/test_escalation_routing_refresh_patch.py -v` → FAIL (patch missing).
- [ ] **Step 3: Implement** the shell, helpers and Overview per Design decisions 1–2. In-flight state chip: `climbing` = slate-12 text on n-alpha-2, other states slate-10 (keep `inFlightLabel` mapping).
- [ ] **Step 4: Run** the test and `tests/simulate_patch_chain.sh ProtonEscalationRoutingPage.vue` → all `ok`.
- [ ] **Step 5: Commit** the patch + test.

---

### Task 3: Departments tab and PIC drawer

**Files:**
- Create: `escalationRouting/DepartmentsTab.vue`, `escalationRouting/PicDrawer.vue`, `escalationRouting/RecordDrawer.vue` (generic right-side drawer: title, body slot, footer with Save / Cancel / Delete)
- Test: extend `test_escalation_routing_refresh_patch.py`

**Interfaces:**
- Consumes: `splitEmails`, `isEmail` (Task 2); shell props/events.
- Produces: `RecordDrawer` props `open: boolean`, `title: string`, `saving: boolean`, `deletable: boolean`; emits `save`, `cancel`, `delete`. `PicDrawer` props `pic: object|null` (null = add); emits `saved`.

- [ ] **Step 1: Failing test** — the department list renders at most 4 data columns (name, PIC, CC count, tier-2) and no `<input>` inside `<tbody>`; `PicDrawer` edits a copy (`{ ...pic }` / `structuredClone`), calls `upsertPic` with `cc_emails` as an array, requires department + PIC name + valid PIC email; Delete calls `deletePic` after `window.confirm` with today's wording; the search filters by department, PIC name and email, lower-cased.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** test + chain simulation → pass.
- [ ] **Step 5: Commit.**

---

### Task 4: Dealers tab and dealer drawer

**Files:**
- Create: `escalationRouting/DealersTab.vue`, `escalationRouting/DealerDrawer.vue`, `escalationRouting/EmailChips.vue`
- Test: extend `test_escalation_routing_refresh_patch.py`

**Interfaces:**
- Consumes: `RecordDrawer` (Task 3), `DEALER_ROLES`, `contactsFrom`, `isEmail` (Task 2).
- Produces: `EmailChips` — `v-model` an array of strings; emits `invalid(boolean)`.

- [ ] **Step 1: Failing test** — row shows name, region (or "No region — ARM/HOD not CC'd"), member count, and 4 role pills keyed by `DEALER_ROLES`; drawer has the three sections *Basics*, *Members*, *Ladder contacts* with the rung labels from `DEALER_ROLES`; Save is disabled while `EmailChips` reports invalid; saving with no members but one named contact is allowed; `upsertDealer` body keeps `emails` (array), `region`, `contacts` exactly as the old `createDealer` built them.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → pass.
- [ ] **Step 5: Commit.**

---

### Task 5: Ladder tab timeline

**Files:**
- Create: `escalationRouting/LadderTab.vue`
- Test: extend `test_escalation_routing_refresh_patch.py`

**Interfaces:**
- Consumes: `LADDER_RUNGS`, `numOrNull`, `LadderStatusBanner` (Task 2); the shell's `ladder` prop; saves through the exact `adminRequest(LADDER_PATH, …)` call (same method and body) as today's `saveLadder` — move that method, don't rewrite it.

- [ ] **Step 1: Failing test** — six step cards in order; steps 2–5 bind to `step2_hours`…`step5_hours`; step 1 shows "0 h", step 6 shows "48 h" and is not an input; the "Reminders will be emailed to real … contacts" warning is present verbatim when enabled and not dry run; blank inputs are sent through `numOrNull`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → pass, and the whole fork suite: `cd deploy/chatwoot-fork && python -m pytest tests -q`.
- [ ] **Step 5: Commit**, then update `PATCH-INVENTORY.md` rows for 0100 and 0101.

---

### Task 6: Build, deploy to proton, verify (ops)

- [ ] **Step 1:** Cloud Build `-rc23` with `_REGISTRY` = the proton AR repo; must succeed (it is the only vite build proof).
- [ ] **Step 2:** Write `scripts/ops/proton_deploy_rc23_<date>.sh` from the rc22 script's Chatwoot half only (set `CHATWOOT_IMAGE`, pull, recreate `chatwoot-rails chatwoot-sidekiq`, `rails_healthy`, `--rollback` to rc22). No backend change. The user runs dry-run then apply.
- [ ] **Step 3:** Check in the browser as an `escalation.manage` user: every tab loads; the Needs attention list matches the data; add → edit → cancel → delete a throwaway department and dealer (`zz_test`) and confirm the list returns to its prior state; ladder save round-trips (`In force now` text updates); availability menu scrolls to Offline at a 768-px-high window.
- [ ] **Step 4:** Update the Proton Live Demo Guide only if the demo walks through this page (segment 4 step 6 opens "Escalation Routing > ladder policy" — rename that to "Escalation Routing > Ladder tab").
