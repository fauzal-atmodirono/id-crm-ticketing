# Logisly UC3 on Jira + Logisly PRD format: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adapt the existing UC3 product-manager agent so that it:
- writes Logisly's PRD format and publishes it as a Google Doc;
- proposes and applies stories on a mock Jira (project LOG);
- works from sample sources about UC1's milestone-checklist driver follow-up.

**Architecture:** this is the same ADK agent in `apps/logisly-agents/logisly_agents/uc3/`.
- `MockJira` (session state key `uc3:jira`) replaces `MockTracker` (mock GitHub).
- A new `gdoc.py` converts PRD markdown to HTML and uploads it with conversion to a Google Doc through the Drive v3 REST API, using an ADC `AuthorizedSession`. It is fail-open.
- Demo data under `demo-data/logisly/uc3/` is rewritten: `github/` → `jira/`, new templates, new sources and new answer keys.

**Tech Stack:**
- Python 3.12+, uv, `google-adk==2.2.0`, `google-auth` (already transitive).
- New dependency: `markdown>=3.6`.
- Tools: pytest, ruff; `python-docx` for the generator.

**Spec:** `docs/superpowers/specs/2026-11-12-logisly-uc3-jira-prd-design.md`.

## Global Constraints

- **Location:**
  - Work in `/private/tmp/claude-501/-Users-yudaadipratama-Archive-id-crm-ticketing/8e383b08-a63d-4cde-83a1-72fc4851b708/scratchpad/wt`, branch `logisly-demo`.
  - Never touch `/Users/yudaadipratama/Archive/id-crm-ticketing`. That checkout is on another branch.
  - Never push, merge or deploy.
- **Commands:**
  - Run them from `apps/logisly-agents/`: `uv run pytest` and `uv run ruff check . && uv run ruff format --check .`.
  - Live tests (`-m live`) are not run by implementers.
- **People and data:**
  - Every person, shipper, plate and phone number is fictional. Reuse the names already in `demo-data/logisly/uc3/team/roster.yaml` and the UC1 trips.
  - Never use names from call transcripts or chat screenshots.
- **Language:** source documents, PRD body text and issue text are in Bahasa Indonesia. Code, identifiers and docstrings in English are fine. The existing tool docstrings are Indonesian; keep that style.
- **Jira:**
  - Project `LOG`.
  - Issue types `Epic`, `Story`, `Task`, `Bug`.
  - Statuses `To Do`, `In Progress`, `Done`, `Won't Do`.
  - Resolutions `Done`, `Duplicate`, `Won't Do`.
  - Link types `duplicates`, `blocks`, `relates to`.
  - Components `Driver App`, `Platform`, `Ops Console`, `Shipper Portal`, `Data`, `AI Agent`.
  - Story points are one of 1, 2, 3, 5, 8 and are required on Story.
- **Team mapping for capacity:**
  - `mobile` = {Driver App}
  - `backend` = {Platform, AI Agent}
  - `frontend` = {Ops Console, Shipper Portal}
- **Fail-open:**
  - Every tool returns a dict and never raises.
  - Drive failures return `{"status": "unavailable", "reason": ...}` without credentials or tokens in the text.
- Commit trailer: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Planted facts (data contract shared by Tasks 1–4)

**Sources** (`demo-data/logisly/uc3/sources/`):
- `01_request_milestone-follow-up.docx` (Head of Ops):
  - Asks for checklist follow-up per milestone.
  - Target pilot live "4 Desember 2026" for FMCG shippers.
  - Quiet hours "21:00–06:00".
  - KPI table: "Pertanyaan ke driver per trip | 18 | 6" and "Kasus terlambat terdeteksi sebelum muat | 40% | 80%".
- `02_meeting-notes_2026-11-10_product-sync.md`:
  - Release target "18 Desember 2026" (end of Sprint 26.24).
  - Quiet hours agreed "22:00–05:00" per CT.
  - The Shopee SOP comes in phase 2, Sprint 27.01, after code freeze.
  - The auto milestone trigger depends on LOG-3405.
- `03_email_cs_unloading-update.eml`: CS forwards a complaint from fictional shipper "PT Sinar Pangan Nusantara". It asks for unloading / POD updates by "31 Desember 2026".
- `04_ops-chat-export_ct-followup.txt`: CT group chat.
  - The bot sends "Posisi di mana pak?" every 30 minutes all night.
  - Drivers' photos are ignored.
  - WhatsApp cost line "Rp 38 juta/bulan".

**Jira snapshot** (`demo-data/logisly/uc3/jira/issues_snapshot.json`, `{"project": "LOG", "issues": [...]}`). It has 14 issues, LOG-3394..LOG-3420 (the max is LOG-3420, so the next key is LOG-3421), including:
- `LOG-3398`: Story, Done, Platform. "As CT, I want the bot to remind drivers on WhatsApp every 30 minutes during a trip so that we get location updates". This is the current behaviour the new feature supersedes.
- `LOG-3405`: Story, In Progress, Platform, Sprint 26.22. "[BE] As Logisly, I want GPS geofence events for pickup and drop locations so that trip status updates automatically". It blocks the automatic milestone trigger.
- `LOG-3412`: Story, To Do, Driver App, created 2026-10-20. "[Driver App] As Driver, I want to upload the POD photo after unloading so that the shipper gets proof of delivery faster".
- `LOG-3417`: Story, To Do, Driver App, created 2026-11-05. A newer duplicate of LOG-3412 (similar summary). The expected action is to transition it to Won't Do / Duplicate with a `duplicates` link to LOG-3412, and to enrich LOG-3412.
- About 10 unrelated issues across Shipper Portal, Data, Ops Console and Bug, as noise.

**Answer keys** (`demo-data/logisly/uc3/expected/`):
- `prd_uc1-milestone-checklist.md`: Logisly format (spec §What changes 1). Its content matches the UC1 spec's milestones, checklist items, teams and quiet hours 22:00–05:00.
- `backlog.json`: one Epic, at least 8 Stories (≤ 8 SP each, one component each), the LOG-3417 transition and the LOG-3412 update.
- `consistency_report.md`: the date conflicts (4 Des vs 18 Des; 31 Des falls in code freeze 21 Des–3 Jan), the quiet-hours conflict (21–06 vs 22–05), the duplicate pair, LOG-3398 superseded, the LOG-3405 dependency and the capacity check.

## Review Focus

1. A story proposal that references an Epic created in the same proposal (by `temp_id`) must resolve to the real key on apply. An unknown temp id is an error item, not a crash. Test in Task 2.
2. Transitioning an issue that is already `Won't Do` / `Done` again, or applying the same proposal twice, does not create duplicates. Apply reports "already applied". Test in Task 2.
3. `publish_prd` with no folder configured, or with a Drive 403/5xx, returns `status: unavailable`, still saves the markdown to `uc3:docs`, and puts no token in the reason. Test in Task 3.
4. `publish_prd` called twice with the same name updates the same file id (PATCH), and does not create a second Doc. Test in Task 3.
5. PRD markdown tables (the Stories table with multi-line acceptance) survive the HTML conversion as `<table>` with one row per story. Test in Task 3.

---

### Task 1: Demo data on the UC1 topic, in Jira and Logisly PRD format

**Files:**
- Modify: `demo-data/logisly/gen_uc3.py`. It generates the docx source and any other generated files; read it first.
- Create:
  - `demo-data/logisly/uc3/jira/conventions.yaml`
  - `demo-data/logisly/uc3/jira/issues_snapshot.json`
- Delete: `demo-data/logisly/uc3/github/`
- Rewrite: `demo-data/logisly/uc3/templates/prd_template.md` (Logisly format) and `templates/user_story_template.md` ("As <role>, I want … so that …" plus a numbered Acceptance list, optional `[Driver App]/[BE]/[FE]/[Ops Console]` prefix).
- Replace: the four `uc3/sources/*` files with the four named in Planted facts.
- Modify: `uc3/team/roster.yaml`. Each person gets `components: [...]` in place of `repos`; keep names and capacity.
- Rewrite: the `uc3/expected/*` answer keys. Remove `prd_driver-app-ai-assistant.md`.
- Test: `apps/logisly-agents/tests/unit/uc3/test_demo_data.py`.

**Interfaces:**
- Produces: the planted facts above, verbatim.
- Produces: `conventions.yaml` keys `project`, `issue_types`, `statuses`, `resolutions`, `link_types`, `components` (map name → {owner, team}), `labels` (free list, e.g. `uc1-milestone`, `pilot`, `phase-2`), `story_points: [1,2,3,5,8]`, `summary_format`, `definition_of_ready`, `definition_of_done`.
- Each snapshot issue has these keys: `key, type, summary, description, acceptance (list), status, resolution (str|null), components (list), labels, story_points (int|null), sprint (str|null), assignee (str|null), epic (str|null), links ([{type,key}]), comments ([{author,created,body}]), created, updated`.

- [ ] **Step 1:** Write `test_demo_data.py` (pure file checks, no agent):
  - The snapshot max key is `LOG-3420`.
  - LOG-3417 and LOG-3412 both exist, with status `To Do` and component `Driver App`.
  - LOG-3405 is `In Progress`.
  - Every issue's components ⊆ conventions components, and every Story has story_points in [1,2,3,5,8] or is Done.
  - Every roster person's components ⊆ conventions components.
  - The docx text (read via python-docx) contains "4 Desember 2026" and "21:00–06:00".
  - The notes contain "18 Desember 2026" and "22:00–05:00".
  - The eml contains "31 Desember 2026".
  - `prd_template.md` contains, in order, the headings `Background`, `Impact`, `Requirements & Timeline`, `Flow`, `Timeline`, `Mock Up`, `Stories`, `Pertanyaan terbuka`.
  - The expected PRD has the same headings, and `backlog.json` has ≥ 8 stories plus the LOG-3417 transition.
- [ ] **Step 2:** Run the test and confirm it fails. Generate the data (`cd demo-data/logisly && /usr/local/bin/python3 gen_uc3.py`) and write the static files.
- [ ] **Step 3:** Make it pass, and run `rg -n "github|repo" demo-data/logisly/uc3`. The output must be empty, apart from a deliberate mention in the README if any.
- [ ] **Step 4:** Commit `feat(logisly-uc3): demo data on UC1 milestone follow-up, Jira and Logisly PRD format`.

### Task 2: Mock Jira tracker and tools

**Files:**
- Create: `apps/logisly-agents/logisly_agents/uc3/jira.py`.
- Delete: `uc3/tracker.py` and `tests/unit/uc3/test_tracker.py`.
- Modify:
  - `uc3/tools.py`
  - `uc3/capacity.py` (components; team mapping from Global Constraints)
  - `uc3/context.py` (`jira/conventions.yaml`)
  - `tests/unit/uc3/test_tools.py`, `tests/unit/uc3/test_capacity.py`
- Test: `tests/unit/uc3/test_jira.py`.

**Interfaces:**
- Produces `class MockJira(state, snapshot_path, conventions, calendar, as_of=None)`, with `KEY = "uc3:jira"`. The state is `{"issues": [...], "next": int}`. Keep the copy → modify → re-assign pattern and the validate-before-write rule of the old tracker.
  - `search(text="", status="open"|"done"|"all", component="", issue_type="", label="") -> list[dict]`: summaries with key, type, summary, status, components, sprint, assignee, story_points. `open` = To Do + In Progress.
  - `get(key) -> dict`: the full issue. An unknown key raises `TrackerError`.
  - `create(issue_type, summary, description, acceptance, components, labels, story_points=None, sprint=None, assignee=None, epic=None, links=None) -> dict` with `{"key": "LOG-3421", ...}`.
  - `update(key, add_labels=(), remove_labels=(), sprint=None, assignee=None, story_points=None, description_append="", acceptance_append=(), add_links=()) -> dict`.
  - `comment(key, body) -> dict`.
  - `transition(key, status, resolution="", comment="", duplicate_of="") -> dict`. `duplicate_of` adds a `duplicates` link and forces resolution `Duplicate`.
  - Validation raises `TrackerError` for: an unknown type, status, component, sprint or link type; a missing or nonexistent key; a Story without valid story points; an epic key that is not an Epic.
- Tools in `tools.py` (Indonesian docstrings, all fail-open):
  - `search_issues(tool_context, text="", status="open", component="", issue_type="")`
  - `get_issue(tool_context, key)`
  - `create_issue(tool_context, issue_type, summary, description, acceptance: list[str], components: list[str], labels: list[str] | None = None, story_points: int = 0, sprint="", assignee="", epic="")`
  - `update_issue(tool_context, key, ...)`
  - `comment_issue(tool_context, key, body)`
  - `transition_issue(tool_context, key, status, resolution="", comment="", duplicate_of="")`
  - `propose_changes(tool_context, creates, updates, transitions)`: validates only. Items get ids P1..Pn. A create may carry `temp_id`, and another create's `epic` or a link key may name that temp id.
  - `apply_proposal(tool_context, ids=None)`: applies in proposal order, resolves temp ids to the created keys, and reports `applied`, `already_applied` and `errors`, as today.
  - `capacity_check(items)`: unchanged signature; `assignee` is the roster person id.
- `get_conventions`, `get_team`, `get_templates` and `save_document` stay.
- Keep the existing split and estimate warnings, adapted to Jira:
  - **Split:** summary combines capabilities.
  - **Estimate:** a Story with story_points > 8 or missing.

- [ ] **Step 1:** Port `test_tracker.py` to `test_jira.py`. It must cover:
  - **Seeding:** next key LOG-3421, and seeding is idempotent.
  - **Each validation error:** leaves state unchanged.
  - **Duplicate transition:** LOG-3417 → Won't Do / Duplicate, with a link to LOG-3412.
  - **Search:** `status="open"` excludes Done.
- [ ] **Step 2:** Port `test_tools.py`. It must cover:
  - **Epic by temp id:** propose with an Epic `temp_id: "E1"` and two Stories with `epic: "E1"`; apply sets both stories' epic to the new Epic's key (Review Focus 1).
  - **Unknown temp id:** an unknown `epic: "E9"` gives an error item.
  - **Apply twice:** applying twice reports already_applied and creates no new issues (Review Focus 2).
  - **Missing story points:** a Story without story points gets the estimate warning or error.
- [ ] **Step 3:** Update `test_capacity.py` for components.
- [ ] **Step 4:** Implement until `uv run pytest tests/unit` and ruff pass. Commit `feat(logisly-uc3): mock Jira tracker replaces mock GitHub`.

### Task 3: Publish the PRD as a Google Doc

**Files:**
- Create: `apps/logisly-agents/logisly_agents/uc3/gdoc.py`.
- Modify:
  - `logisly_agents/config.py` (`prd_folder_id: str = ""`)
  - `uc3/tools.py` (new tool `publish_prd`)
  - `pyproject.toml` (+ `markdown>=3.6`; `uv lock`)
  - `.env.example`
- Test: `tests/unit/uc3/test_gdoc.py`.

**Interfaces:**
- `markdown_to_html(md: str) -> str`:
  - Uses the `markdown` extensions `tables`, `sane_lists` and `nl2br`.
  - The output is a full HTML document with inline CSS: table borders and Arial.
  - Line breaks inside table cells (`<br>`) are kept, so multi-line acceptance survives.
- `publish(name, markdown, folder_id, file_id=None, session=None) -> {"file_id": str, "url": str}`:
  - Multipart upload to `https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&supportsAllDrives=true&fields=id,webViewLink`. The metadata is `{name, mimeType: "application/vnd.google-apps.document", parents: [folder_id]}`, followed by the HTML part `text/html`.
  - With `file_id`, it PATCHes `.../files/{file_id}?uploadType=multipart&supportsAllDrives=true&fields=id,webViewLink` with metadata `{name}`.
  - `session` defaults to `google.auth.transport.requests.AuthorizedSession(google.auth.default(scopes=["https://www.googleapis.com/auth/drive"])[0])`.
  - It raises on a non-2xx response.
- Tool `publish_prd(tool_context, name: str, markdown: str) -> dict`:
  - Always saves to `uc3:docs` first, as `save_document` does.
  - With an empty folder it returns `{"status": "unavailable", "reason": "LOGISLY_PRD_FOLDER_ID belum diisi", "saved": True}`.
  - Otherwise it looks up `state["uc3:gdocs"][name]` and calls `publish` with that file_id, stores `{file_id, url}` (re-assign), and returns `{"status": "published", "url": ..., "updated": bool}`.
  - On an exception it returns `{"status": "unavailable", "reason": "<ExceptionClass>: <HTTP status if any>", "saved": True}`. Never include the response body or headers.
  - The Doc name is `f"PRD - {name}"`.

- [ ] **Step 1:** Write tests with a fake session that records requests and returns canned responses. They must cover:
  - **Create:** sends POST with the folder in parents and the HTML part containing `<table>`.
  - **Second call:** with the same name it PATCHes the stored id, and `updated` is true (Review Focus 4).
  - **No folder:** gives unavailable, and the doc is saved (Review Focus 3).
  - **Fake 403:** gives unavailable, the reason contains `403`, the reason contains no `Bearer`, and the doc is saved.
  - **Table conversion:** a 3-story Stories table converts to exactly 4 `<tr>` (Review Focus 5).
  - The tool sends no network traffic in unit tests: monkeypatch the session factory.
- [ ] **Step 2:** Implement, run `uv run pytest tests/unit` and ruff, then commit `feat(logisly-uc3): publish the PRD as a Google Doc (Drive conversion, fail-open)`.

### Task 4: Agent instruction, deploy staging, docs and live test

**Files:**
- Modify:
  - `apps/logisly-agents/logisly_agents/uc3/agent.py`
  - `apps/logisly-agents/deploy.sh` (stage `uc3/jira` in place of `uc3/github`; pass `LOGISLY_PRD_FOLDER_ID` to `--set-env-vars`/`--update-env-vars` only when it is set in the environment)
  - `apps/logisly-agents/README.md` (UC3 rows and demo script)
  - `demo-data/logisly/README.md` (UC3 section)
  - `tests/unit/uc3/test_agent.py`
  - `tests/live/test_uc3_agent_live.py`

**Interfaces:** it consumes the Task 2 and Task 3 tools.

**Instruction** (Indonesian, keep the current step structure) changes:
- Jira (keys `LOG-n`) instead of GitHub.
- The PRD follows `prd_template.md` (Logisly format) exactly, and the Stories table uses the user-story template.
- After writing the PRD, call `publish_prd`. Report the link, or say plainly that the Doc is unavailable and the PRD is in chat.
- After `apply_proposal`, fill the Stories table "Jira #" column with the created keys and call `publish_prd` again with the same name.
- Duplicate handling: transition the newer issue to Won't Do with `duplicate_of` the older one, and enrich the older issue.
- A superseded Done story gets a comment and a `relates to` link, never a reopen.
- Dependencies are named by key (e.g. LOG-3405).
- The tool list replaces `close_issue` with `transition_issue` and adds `publish_prd`.

**Demo prompt** in the README: "Siapkan PRD follow-up driver berbasis milestone dan backlog Jira dari input minggu ini."

- [ ] **Step 1:** Update `test_agent.py`:
  - The tool names include `publish_prd` and `transition_issue`.
  - The tool names exclude `close_issue`.
  - The instruction mentions `Jira` and `publish_prd`.
  - The instruction no longer mentions `GitHub`.
- [ ] **Step 2:** Update the live test, which is not run by the implementer:
  - Use the four new sources.
  - Turn-1 text contains "18 Desember" and "LOG-3405".
  - After approval, LOG-3417 has status `Won't Do`.
  - At least 8 new keys ≥ LOG-3421.
  - `uc3:docs` has one doc containing "Stories" and "Pertanyaan terbuka".
- [ ] **Step 3:** Run `bash -n deploy.sh`, `uv run pytest` (unit) and ruff. `rg -n -i "github" apps/logisly-agents/logisly_agents/uc3 apps/logisly-agents/tests/unit/uc3` must be empty.
- [ ] **Step 4:** Commit `feat(logisly-uc3): agent on Jira with Logisly PRD and Google Doc publishing; docs`.
