# Proton CRM Feature Guide Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce `docs/client-materials/PROTON - CRM Feature Guide.docx` — a complete English operator handbook covering every CRM menu/submenu with definitions, steps, scenarios, integrations, and screenshots.

**Architecture:** Chapters are drafted as markdown source files in `docs/client-materials/feature-guide-src/` (durable + reviewable in git), each grounded in the fork patches / `agent/` / `backend/` code. A python-docx build script renders them into the client template's styling. Screenshots are captured last from the live Proton tenant and slotted in via `[[SCREENSHOT: ...]]` markers; the builder renders a visible placeholder box for any missing image so the doc builds at every stage.

**Tech Stack:** python-docx 1.2.0 (already installed), template `docs/client-materials/Google Docs template - Short version.docx` (has `Title`, `Subtitle`, `Heading 1..6`, `normal` styles), Claude-in-Chrome for screenshots.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-05-crm-feature-guide-design.md`. Audience: Proton operators — user-guide tone, **no code internals, no file/patch names, no env vars** in the doc body.
- Language: English. Deliverable filename: `docs/client-materials/PROTON - CRM Feature Guide.docx`.
- Every feature section follows the fixed template: **What it is / Where to find it / How to use it / Example scenario / Integrations & automation** (scenario + integrations only when meaningful) + screenshot marker(s).
- Content must be grounded in actual behaviour (patches 0001–0045, `agent/app/`, `backend/.../features/`) — never invent UI labels or behaviour. When a label is uncertain, write it and add `<!-- VERIFY-LIVE: ... -->` for the screenshot session to confirm.
- Exclude features the fork strips/hides (enterprise cruft, security settings nav, dead enterprise nav — patches 0008, 0029, 0032).
- Commit after every task. Branch: `dev-yuda` (never merge to main).

## Markdown subset (contract between chapter files and the builder)

Chapter files may use ONLY:
- `#` (chapter title, one per file), `##` (feature section), `###`/`####` (sub-sections; `###` is used for the fixed template headings "What it is" etc.)
- paragraphs; `**bold**`, `*italic*`, `` `code` `` (rendered as monospace run)
- `-` bullet lists and `1.` numbered lists (one level of nesting with 2-space indent)
- pipe tables with a `|---|` separator row
- `> ` blockquote → rendered as a shaded "Note" paragraph
- `[[SCREENSHOT: <id> | <caption>]]` on its own line → image `feature-guide-assets/<id>.png` if present, else a bordered placeholder box containing the caption
- `<!-- VERIFY-LIVE: ... -->` comments → ignored by the builder

File naming: `NN-slug.md` (`01-introduction.md` … `12-integrations.md`, `13-glossary.md`). Screenshot ids: `chNN-short-slug` (e.g. `ch02-copilot-panel`).

---

### Task 1: Chapter skeletons + feature inventory

**Files:**
- Create: `docs/client-materials/feature-guide-src/OUTLINE.md`
- Create: `docs/client-materials/feature-guide-src/01-introduction.md` … `13-glossary.md` (13 skeleton files)

**Interfaces:**
- Produces: the 13 chapter files with `#` title and every planned `##` feature section heading in place (body text TBD is fine at *skeleton* stage only — later tasks fill them; no `##` heading may be added/removed later without updating OUTLINE.md), plus OUTLINE.md mapping every `##` section → its evidence source (patch number(s) / code path) for the drafting tasks.

- [ ] **Step 1: Build the feature inventory.** Read patch subjects + skim each patch's SPA-visible surface: `ls deploy/chatwoot-fork/patches/` then for each of 0003, 0005, 0007, 0009–0028, 0033–0045 grep the patch for `label:`, `name:`, route paths, and i18n strings to get exact operator-visible menu labels. Also list native Chatwoot menus operators see (Conversations, Contacts, Reports, Campaigns, Help Center, Settings submenus) — these come from upstream Chatwoot v4 knowledge, flagged `<!-- VERIFY-LIVE -->` where uncertain.
- [ ] **Step 2: Write OUTLINE.md** — a table: chapter file → `##` section title → evidence (patch/code path) → planned screenshot ids. Chapters per the spec outline (12 chapters + glossary).
- [ ] **Step 3: Write the 13 skeleton files** — each with `#` title, all `##` headings, and under each `##` the five `###` template headings plus `[[SCREENSHOT: ...]]` markers with drafted captions.
- [ ] **Step 4: Verify** — `grep -c '^## ' docs/client-materials/feature-guide-src/*.md` shows every chapter has its sections; every `##` in the files appears in OUTLINE.md (spot-check).
- [ ] **Step 5: Commit** — `git add docs/client-materials/feature-guide-src && git commit -m "docs(feature-guide): chapter skeletons + feature inventory"`

### Task 2: Draft Ch1 Introduction + Ch2 Conversations

**Files:**
- Modify: `docs/client-materials/feature-guide-src/01-introduction.md`, `02-conversations.md`

**Interfaces:**
- Consumes: OUTLINE.md evidence map from Task 1.
- Produces: fully drafted chapters (no empty sections, no TBD).

- [ ] **Step 1: Research.** Read the SPA surface of patches 0004 (contact panel default), 0005 (Ask Copilot panel), 0007 (suggest sources in ReplyBox), 0023 (inbox inactivity timing — operator-visible bits), 0024 (agent priorities), 0037/0038 (default All tab / All status), 0045 (DMS integration card); `agent/app/services/orchestrator.py` + `lifecycle.py` for what suggest-vs-auto mode looks like to an agent (private note + reopen vs direct send); `backend/.../features/assist/router.py` for the copilot/suggest/summarize actions the UI calls.
- [ ] **Step 2: Draft Ch1** — platform overview, login, screen layout (sidebar, conversation list, reply box, contact panel), roles agent vs administrator, availability statuses, EN/ID language note.
- [ ] **Step 3: Draft Ch2** — one `##` per feature per OUTLINE.md: inbox views & default All tab/status, statuses & snooze, assignment & teams, labels, priorities, private notes, canned responses, mentions, Ask Copilot panel, Suggest-a-reply (+ Sources line), Summarize conversation, AI auto-draft behaviour (what a suggested private note looks like; what auto mode does), contact side panel, DMS integration card (vehicle/service info), resolving & transcripts. Each in the fixed 5-heading template with a realistic Proton example scenario.
- [ ] **Step 4: Verify** — `grep -n 'TBD\|TODO' 01-*.md 02-*.md` returns nothing; every `### How to use it` contains a numbered list; screenshot markers use valid `chNN-` ids.
- [ ] **Step 5: Commit** — `git commit -am "docs(feature-guide): draft ch1 introduction + ch2 conversations"`

### Task 3: Draft Ch3 Contacts & Customer 360 + Ch5 Cases + Ch6 RSA Incident Log

**Files:**
- Modify: `03-contacts.md`, `05-cases.md`, `06-rsa.md` (in `feature-guide-src/`)

**Interfaces:** Consumes OUTLINE.md; produces fully drafted chapters.

- [ ] **Step 1: Research.** Patches 0041 (Customer 360: search by phone/vehicle, what the results aggregate), 0036 (case category hierarchy), 0043 (cases list UI), 0035 (RSA incident log UI + fields); `backend/.../customer360_router.py`, `backend/.../features/rsa/`, and the case-related backend surface referenced by 0043's comments. Native Contacts (list, profile, notes, segments) from upstream knowledge + VERIFY-LIVE flags.
- [ ] **Step 2: Draft Ch3** — contacts list/search/segments, contact profile & history, notes, then Customer 360 (`##`): search by phone or vehicle number, what the result shows (contact, conversations, RSA incidents), who can use it (permission-gated), scenario: dealer calls asking about a customer's open issues.
- [ ] **Step 3: Draft Ch5** — cases list, case categories (hierarchy), how a case relates to a conversation, lifecycle/status, scenario: complaint tracked as a case across multiple contacts.
- [ ] **Step 4: Draft Ch6** — logging an RSA incident (fields: location, vehicle, issue…, per patch 0035), statuses/updates, how incidents surface in Customer 360 and reports, scenario: breakdown call → incident logged → towing dispatched → closed.
- [ ] **Step 5: Verify** — no TBD/TODO; template headings complete; commit `git commit -am "docs(feature-guide): draft ch3 contacts+customer360, ch5 cases, ch6 rsa"`

### Task 4: Draft Ch4 Knowledge

**Files:**
- Modify: `04-knowledge.md`

**Interfaces:** Consumes OUTLINE.md; produces the fully drafted chapter.

- [ ] **Step 1: Research.** Patches 0009 (nav: FAQs, Documents, Playground, Inboxes, Tools, Settings), 0010 (FAQs), 0011+0021 (Documents/uploads), 0012 (Assistants), 0013+0022 (Settings: persona, temperature, guardrails, response guidelines, language, welcome/handoff/resolution + 7 lifecycle messages), 0014 (Playground), 0015 (Tools), 0016 (Scenarios), 0017 (Inboxes), 0040 (FAQ bulk CSV upload — required CSV columns from `backend /kb/faq/bulk`); backend `assistants_store.py` for what each Settings field does to the bot.
- [ ] **Step 2: Draft** one `##` per submenu. For Settings, explain in operator terms what each field changes about the AI's behaviour and that empty fields keep default behaviour. For FAQs include the bulk CSV upload flow with the exact expected columns. Scenario examples: tuning persona tone; testing a new FAQ in Playground before it goes live; assigning an assistant to a WhatsApp inbox.
- [ ] **Step 3: Verify + commit** — no TBD; `git commit -am "docs(feature-guide): draft ch4 knowledge"`

### Task 5: Draft Ch7 Reports + Ch8 Campaigns & Help Center

**Files:**
- Modify: `07-reports.md`, `08-campaigns-helpcenter.md`

**Interfaces:** Consumes OUTLINE.md; produces fully drafted chapters.

- [ ] **Step 1: Research.** Patches 0020 (native reports merge — what report pages exist), 0034 (Agent reports, Department reports, Case list report), 0044 (Weekly Report page — sections/metrics, per backend `metrics/insights_router.py`), 0025 (SLA reporting angle), `agent/app/services/sync.py::maybe_stamp_dealer_escalation` (what the dealer turnaround timestamp means in reports). Native campaigns/help-center from upstream knowledge + VERIFY-LIVE.
- [ ] **Step 2: Draft Ch7** — overview reports, CSAT, agent/label/inbox reports, Proton extensions (agent, department, case list), Weekly Report (what each section shows, how to use it in the weekly client meeting), SLA reports, dealer-escalation turnaround explanation.
- [ ] **Step 3: Draft Ch8** — brief: one-off vs ongoing campaigns; help-center portal basics (articles, categories, publishing). Mark clearly if a menu is hidden in the Proton deployment (`<!-- VERIFY-LIVE -->`).
- [ ] **Step 4: Verify + commit** — `git commit -am "docs(feature-guide): draft ch7 reports + ch8 campaigns/help-center"`

### Task 6: Draft Ch9 Administration

**Files:**
- Modify: `09-administration.md`

**Interfaces:** Consumes OUTLINE.md; produces the fully drafted chapter.

- [ ] **Step 1: Research.** Patches 0025 (SLA policies admin), 0026 (Audit log), 0027+0028+0031 (Roles & permissions + access permissions), 0039 (Escalation routing: PIC per department, dealer directory), 0023 (inbox inactivity timing settings), 0030 (admin auth fix — no operator-visible content, skip); native settings submenus (Agents, Teams, Inboxes, Labels, Custom Attributes, Automation, Macros, Canned Responses, Integrations, Account settings) from upstream + VERIFY-LIVE.
- [ ] **Step 2: Draft** — one `##` per settings submenu, custom pages in depth: SLA policies (create/targets/breach), Audit log (what's recorded, filtering), Roles & permissions (creating a role, permission list incl. `escalation.manage`, `customer360.view` in operator terms), Escalation routing (edit PIC contact per department; dealer slug → email; takes effect without redeploy), inbox inactivity timing. Scenario examples: onboarding a new agent with a limited role; changing the PIC for After-Sales.
- [ ] **Step 3: Verify + commit** — `git commit -am "docs(feature-guide): draft ch9 administration"`

### Task 7: Draft Ch10 AI behaviour + Ch11 End-to-end scenarios + Ch12 Integrations + Glossary

**Files:**
- Modify: `10-ai-behaviour.md`, `11-scenarios.md`, `12-integrations.md`, `13-glossary.md`

**Interfaces:** Consumes all drafted chapters (cross-references by chapter name only, no numbers-only refs).

- [ ] **Step 1: Research.** `agent/app/services/orchestrator.py` (pending-only, debounce, suggest vs auto), `agent/app/ai/gemini.py` + `tools.py` (the three actions: reply / handoff / escalate in operator terms), `agent/app/services/sync.py` (escalate label → EM-7 two-thread email; dealer_ label → timestamp), `lifecycle.py` (7 lifecycle messages), phone/IVR + DMS deployment notes (memory: Package F deployed) — check `docs/roadmap/2026-08-01-next-development-roadmap.md` and recent commits for the phone/IVR operator surface.
- [ ] **Step 2: Draft Ch10** — when the bot answers vs stays silent (pending status), what "suggest" mode looks like (private note + conversation reopened) vs "auto" mode, how/when it hands off to a human, what the `escalate` and `dealer_<slug>` labels trigger (customer ack email + PIC/dealer forward; turnaround timestamp), the lifecycle messages customers receive, phone/IVR touchpoint.
- [ ] **Step 3: Draft Ch11** — 6 end-to-end walkthroughs, each a numbered narrative with pointers to the relevant chapters: (1) WhatsApp inquiry → AI suggested reply → agent edits & sends → resolve; (2) complaint → escalate label → PIC/dealer email → resolution → turnaround report; (3) RSA call → incident log → Customer 360 lookup on follow-up; (4) FAQ batch → CSV upload → Playground test → live bot answer; (5) weekly reporting routine with the Weekly Report page; (6) new agent onboarding (role, team, inbox assignment).
- [ ] **Step 4: Draft Ch12** — integration table (WhatsApp, Email + escalation emails, Phone/IVR, Gemini AI, DMS, Knowledge base, BI/reporting) + one short prose block each: what it connects, what operators see, who to contact when it misbehaves.
- [ ] **Step 5: Draft Glossary** — ~20 terms (handoff, escalation, PIC, dealer slug, SLA, CSAT, RSA, DMS, IVR, persona, guardrails, lifecycle message, segment, macro, canned response…).
- [ ] **Step 6: Verify + commit** — no TBD anywhere in `feature-guide-src/`; `git commit -am "docs(feature-guide): draft ch10-13 ai, scenarios, integrations, glossary"`

### Task 8: Build script

**Files:**
- Create: `docs/client-materials/build_crm_feature_guide.py`
- Create: `docs/client-materials/feature-guide-assets/` (empty, `.gitkeep`)

**Interfaces:**
- Consumes: the markdown subset defined in Global Constraints; template docx styles `Title`, `Subtitle`, `Heading 1..4`, `normal`.
- Produces: `PROTON - CRM Feature Guide.docx`; missing screenshots render as bordered placeholder boxes so the build never fails on absent PNGs.

- [ ] **Step 1: Write the builder.** Single script, stdlib + python-docx only:
  - Load the template (`Document(TEMPLATE)`), delete its body paragraphs, keep styles/page setup.
  - Cover page: `Title` = "PROTON e.MAS — CRM Feature Guide", `Subtitle` = date + "Operator Handbook", page break.
  - TOC: insert a real Word TOC field (`fldSimple` XML with instr `TOC \o "1-2" \h \z \u`) so Word populates it on open (note in doc: right-click → Update Field).
  - Parse each `NN-*.md` in order with a small line-based parser implementing exactly the markdown subset (headings→styles H1..H4, bold/italic/code inline runs via a regex tokenizer, bullets→`List Bullet`, numbers→`List Number` (fall back to manual numbering if style missing), tables→`add_table` with header bold, blockquote→shaded paragraph, `[[SCREENSHOT: id | caption]]`→`add_picture(assets/id.png, width=Inches(6))` + italic caption, or placeholder box (1×1 table with border + caption text) when the PNG is missing; skip `<!-- -->` comments).
  - Chapter = new page (`add_page_break` before each file except the first).
- [ ] **Step 2: Build.** Run `python3 docs/client-materials/build_crm_feature_guide.py`; expect it to print a summary: chapters, sections, screenshots found/missing.
- [ ] **Step 3: Verify output.** Reopen with python-docx: assert >0 `Heading 1` count equals number of chapter files, no line of source text lost (spot-check 3 known sentences appear), doc opens in a viewer (user spot-check optional). Fix parser gaps until clean.
- [ ] **Step 4: Commit** — `git add docs/client-materials/build_crm_feature_guide.py docs/client-materials/feature-guide-assets && git commit -m "docs(feature-guide): python-docx builder rendering md chapters into client template"` (do NOT commit the generated .docx yet).

### Task 9: Screenshot capture (interactive — needs user)

**Files:**
- Create: `docs/client-materials/feature-guide-assets/*.png` (one per marker id)

**Interfaces:**
- Consumes: the full marker list — `grep -ho 'SCREENSHOT: [a-z0-9-]*' docs/client-materials/feature-guide-src/*.md | sort -u`.
- Produces: PNGs named exactly `<id>.png`; a `CAPTURE-NOTES.md` listing any UI label that differed from the drafted text (feeds Task 10 corrections).

- [ ] **Step 1: Preflight with the user.** Ask the user to log into the Proton tenant as an administrator in Chrome. Do not proceed until confirmed.
- [ ] **Step 2: Capture.** One Claude-in-Chrome session: `tabs_context_mcp` first; resize window to a consistent size (1440×900); walk the marker list menu by menu; screenshot each view; save/convert to `feature-guide-assets/<id>.png`. Rules: read-only navigation — never click delete/send/save on real data; prefer views without customer PII (pick innocuous conversations); note every label/menu mismatch vs the drafts in `CAPTURE-NOTES.md`, resolving the `<!-- VERIFY-LIVE -->` flags.
- [ ] **Step 3: Verify** — every marker id has a PNG (diff the grep list vs `ls feature-guide-assets`); any impossible captures (feature not visible on live tenant) recorded in CAPTURE-NOTES.md with reason.
- [ ] **Step 4: Commit** — `git add docs/client-materials/feature-guide-assets && git commit -m "docs(feature-guide): live-tenant screenshots"`

### Task 10: Corrections, final build, review

**Files:**
- Modify: chapter files per CAPTURE-NOTES.md; Create: `docs/client-materials/PROTON - CRM Feature Guide.docx`

- [ ] **Step 1: Apply corrections** — fix every mismatch from CAPTURE-NOTES.md; remove all resolved `<!-- VERIFY-LIVE -->` comments (any unresolved → ask the user).
- [ ] **Step 2: Final build** — run the builder; summary must report 0 missing screenshots (or only the ones consciously waived in CAPTURE-NOTES.md).
- [ ] **Step 3: Accuracy self-review** — reread each chapter against OUTLINE.md's evidence column; verify no code internals/patch numbers/env vars leaked into the doc body; spelling pass; confirm the fixed 5-heading template everywhere.
- [ ] **Step 4: Commit + hand to user** — `git add 'docs/client-materials/PROTON - CRM Feature Guide.docx' feature-guide-src && git commit -m "docs(feature-guide): final operator handbook with screenshots"`; ask the user to open the .docx (update the TOC field) and review.

---

## Self-review (done at plan time)

- **Spec coverage:** all 12 chapters + glossary (Tasks 2–7), fixed template (Global Constraints), template-styled docx builder (Task 8), live screenshots (Task 9), review (Task 10). Covered.
- **Placeholders:** none — each drafting task names its exact evidence sources and section contents; builder behaviour is specified per markdown construct.
- **Consistency:** file naming `NN-slug.md` and marker syntax defined once in Global Constraints and used identically in Tasks 1, 8, 9.
