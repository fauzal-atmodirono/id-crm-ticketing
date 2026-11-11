# Logisly UC1 Milestone Checklists Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Logisly driver agent works each trip's milestone checklist: system facts first, then only the missing photo/driver items, with "not OK" items routed to the right team.

**Architecture:** A new pure module `milestones.py` holds the six milestones, the checklist items and the engine (fill system items, list open items, record answers, build the opening message, quiet hours). `trips.py` seeds trips with milestone, facts and checklist state and feeds the agent its context. `router.py` gets `POST /logisly/milestone`; `escalation.py` files routed checklist tickets; the ADK agent gets `record_checklist_tool`; the console shows the stepper.

**Tech Stack:** Python 3.12, FastAPI, google-adk tools, httpx + respx, pytest (asyncio auto). Run from `backend/apps/backend`: `uv run pytest -q src/chatbot/features/logisly`.

**Spec:** `docs/superpowers/specs/2026-11-11-logisly-uc1-milestone-checklists-design.md`

## Global Constraints

- Milestone codes, item ids, sources and teams exactly as in the spec table.
- Driver-facing text in casual Bahasa Indonesia, addresses the driver as "Pak", plain WhatsApp text, at most two questions per message.
- Quiet hours: 22:00 ≤ t < 05:00 WIB (UTC+7).
- Chatwoot labels: `logisly-checklist` and `team-<team>`; priority `high` for checklist tickets.
- All seed people, plates, phones and links fictional. Keep order ids LGS-24017, LGS-24021, LGS-24033 (UC2/UC3 data reuse their shippers).
- Fail-open: a Chatwoot error never fails a milestone change or a chat turn; log and continue.
- Module docstrings explain the why, as in the existing `features/logisly` files.

## Review Focus

1. Driver answers an item the agent already ticked → no duplicate ticket, value overwritten, no re-ask (test in Task 1: `test_record_overwrites_and_ticket_once`).
2. Milestone set to the one the trip is already on → no second opening message (Task 4: `test_same_milestone_twice_sends_once`).
3. Unknown milestone code / item id from the model → tool returns an error string, state unchanged (Task 1: `test_record_unknown_item_is_rejected`).
4. Non-Shopee trip → Shopee items absent from checklist and context (Task 1: `test_shopee_items_only_for_shopee_trips`).
5. Photo item whose plate differs from the trip plate → not OK, routed to alloc (Task 5 prompt rule; Task 1: `test_not_ok_queues_ticket_with_team`).

---

### Task 1: Milestone and checklist engine

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/logisly/milestones.py`
- Test: `backend/apps/backend/src/chatbot/features/logisly/test_logisly_milestones.py`

**Interfaces:**
- Produces:
  - `MILESTONES: list[tuple[str, str]]` code → Bahasa label: `assigned` "Pre check driver", `to_pickup` "Menuju lokasi muat", `at_pickup` "Di lokasi muat", `in_transit` "Dalam pengiriman", `at_drop` "Di lokasi bongkar", `unloaded` "Bongkar selesai".
  - `@dataclass(frozen=True) class Item: id: str; label: str; source: Literal["system","photo","driver"]; ask: str; team: str | None; fact: str | None; shopee_only: bool = False` (`ask` = Bahasa question or instruction; `fact` = key in `trip["facts"]` for system items).
  - `CHECKLISTS: dict[str, list[Item]]` per spec table (`to_pickup` → `[]`).
  - `checklist_for(trip: dict, milestone: str) -> list[Item]` (drops `shopee_only` items unless `trip["shipper_kind"] == "shopee"`).
  - `apply_system_checks(trip: dict, milestone: str) -> list[Item]` fills every system item from `trip["facts"][item.fact]` (True → ok, False → not_ok, missing → stays pending and is asked like a driver item); returns the not-OK items.
  - `open_items(trip: dict, milestone: str) -> list[Item]` items still `pending`, in checklist order.
  - `record_item(trip: dict, milestone: str, item_id: str, ok: bool, value: str, source: str, session_id: str | None) -> str` writes `trip["checklists"][milestone][item_id] = {"status","value","source","at"}`; on a transition to `not_ok` for an item with a team, appends `{"session_id","order_id","milestone","item_id","team","value"}` to `PENDING_TICKETS` (only once per item); returns `"ok"`, `"not_ok"` or `"ERROR: …"` for an unknown milestone/item.
  - `is_complete(trip: dict, milestone: str) -> bool`
  - `driver_fixes(trip: dict, milestone: str) -> list[str]` Bahasa lines for not-OK system items whose fix is the driver's (driver_app → install link `trip["driver_app_link"]`, onsite / unloading_registered → tap the status in the Shopee app, pod_uploaded → upload POD).
  - `opening_message(trip: dict, milestone: str) -> str | None` greeting naming the order and milestone, then `driver_fixes`, then the first two open items' `ask`; `None` when both are empty.
  - `is_quiet(now: datetime) -> bool`
  - `PENDING_TICKETS: list[dict]`

- [ ] **Step 1: Write the failing tests** in `test_logisly_milestones.py` using a minimal trip dict fixture (`order_id`, `plate`, `shipper_kind`, `facts`, `checklists={}`, `driver_name`, `driver_app_link`):
  - `test_checklists_match_spec`: `[i.id for i in CHECKLISTS["at_pickup"]] == ["unit_photo","onsite","load_eta","driver_app","do_in_app"]`, `CHECKLISTS["to_pickup"] == []`, every team in `{None,"alloc","ct","sales","mm","warehouse"}`.
  - `test_system_checks_fill_from_facts`: facts `{"driver_app_active": False, "do_in_app": True, "shopee_onsite": True}` on a shopee trip at `at_pickup` → returned not-OK ids `["driver_app"]`; `open_items` ids `["unit_photo","load_eta"]`.
  - `test_missing_fact_is_asked`: no `do_in_app` fact → `do_in_app` in `open_items`.
  - `test_shopee_items_only_for_shopee_trips`: `shipper_kind="fmcg"` → `"onsite"` not in `checklist_for(...)` ids.
  - `test_opening_asks_only_open_items_and_fixes`: message contains the install link and the `unit_photo` ask, not the `do_in_app` ask, and at most two "?".
  - `test_opening_none_when_nothing_open`: all facts True and every non-system item recorded ok → `opening_message` is `None`.
  - `test_not_ok_queues_ticket_with_team`: `record_item(..., "unit_photo", ok=False, value="plat di foto B 1234 XX", source="photo", session_id="crm-7")` → `PENDING_TICKETS[-1]["team"] == "alloc"`.
  - `test_record_overwrites_and_ticket_once`: recording the same not-OK twice leaves one ticket; recording it ok afterwards sets status ok.
  - `test_record_unknown_item_is_rejected`: returns string starting `"ERROR"`, `trip["checklists"]` unchanged.
  - `test_is_quiet`: 23:30 WIB and 04:59 WIB True; 05:00 and 21:59 WIB False.

- [ ] **Step 2: Run** `uv run pytest -q src/chatbot/features/logisly/test_logisly_milestones.py` → FAIL (module missing).
- [ ] **Step 3: Implement `milestones.py`** with the interfaces above; item `ask` texts in Bahasa following the agents in the call samples (e.g. `load_eta`: "Kira-kira jam berapa mulai muat, Pak?"; `unit_photo` at pickup: "Boleh kirim foto unit di lokasi muat, Pak? Pastikan nopol dan jam kelihatan.").
- [ ] **Step 4: Run** the same command → PASS.
- [ ] **Step 5: Commit** `feat(logisly): milestone checklist engine`.

### Task 2: Trips carry milestone, facts and checklist; agent context

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/logisly/trips.py`
- Modify tests that read seed fields: `test_logisly_router.py`, `test_logisly_escalation.py`, `test_logisly_driver_chat.py` (only where they assert `milestone` text or `status`)
- Test: `backend/apps/backend/src/chatbot/features/logisly/test_logisly_trips.py`

**Interfaces:**
- Consumes: Task 1 `MILESTONES`, `checklist_for`, `open_items`, `apply_system_checks`.
- Produces:
  - Seed trip keys added: `shipper`, `shipper_kind` (`"shopee"` or `"fmcg"`), `milestone` (code), `facts: dict[str,bool]`, `checklists: {}`, `do_weight`, `dts_number`, `load_map_link`, `driver_app_link`, `rad` (requested arrival "YYYY-MM-DD HH:MM WIB"). Scenarios: LGS-24017 at `assigned`, FMCG, plate fact True, unit still unloading elsewhere (driver item); LGS-24021 at `to_pickup`, Shopee, `driver_app_active` False, `do_in_app` False; LGS-24033 at `in_transit`, FMCG, `gps_on_track` True, `eta_within_rad` False.
  - `TripStore.set_milestone(order_id: str, milestone: str, source: str) -> dict | None` sets the code, appends event `{"status": f"milestone:{milestone}", "note": f"{label} ({source})"}`, runs `apply_system_checks`.
  - `milestone_label(code: str) -> str`
  - `describe(trip)` adds `Milestone: <label>` and, when the current milestone has items, one line per item `- <id> (<source>): <status>[: value]`.
  - `register_session(session_id, order_id, anomaly, opening, milestone: str = "")`; `context_for_session` when `milestone` is set says the conversation was opened for that milestone, lists open item ids with their `ask`, lists answer-ready facts (DO weight, DTS number, load map link, driver app link) and says: tick with `record_checklist_tool`, do not ask about items already ok.

- [ ] **Step 1: Write failing tests** in `test_logisly_trips.py`: `test_seed_has_milestones_and_facts` (each seed trip's `milestone` is a known code, `facts` non-empty); `test_set_milestone_runs_system_checks` (LGS-24021 → `at_pickup` makes `checklists["at_pickup"]["driver_app"]["status"] == "not_ok"` and logs a `milestone:at_pickup` event); `test_context_lists_open_items_and_answer_facts` (after `register_session("crm-9","LGS-24021","","...", milestone="at_pickup")`, context contains `unit_photo`, the DTS number and `record_checklist_tool`, and not `do_in_app`'s ask once it is recorded ok).
- [ ] **Step 2: Run** `uv run pytest -q src/chatbot/features/logisly` → new tests FAIL.
- [ ] **Step 3: Implement** the trips changes; keep `STATUSES`, `ANOMALIES`, `render` working; adjust old tests only where they asserted the old free-text milestone.
- [ ] **Step 4: Run** the whole logisly folder → PASS.
- [ ] **Step 5: Commit** `feat(logisly): trips carry milestone, facts and checklist state`.

### Task 3: Routed checklist tickets and completion notes

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/logisly/escalation.py`
- Test: `backend/apps/backend/src/chatbot/features/logisly/test_logisly_escalation.py`

**Interfaces:**
- Consumes: Task 1 `PENDING_TICKETS`, `is_complete`, `CHECKLISTS`; Task 2 `TRIPS`, `milestone_label`.
- Produces:
  - `TEAMS: dict[str, str]` team → Bahasa name (`alloc` "Tim Alloc", `ct` "Tim CT", `sales` "Tim Sales", `mm` "Tim MM", `warehouse` "Gudang").
  - `async def file_checklist_tickets(settings, session_id: str, channel: ChatwootDriverChannel | None = None) -> int` removes this session's entries from `PENDING_TICKETS`, posts one private note per entry ("⚠️ Checklist <milestone label>: <item label> tidak sesuai → <team name>", value, trip line), adds labels `logisly-checklist` + `team-<team>`, priority `high`; returns how many it filed. Fail-open.
  - `async def note_checklist_complete(settings, session_id: str, order_id: str, milestone: str, channel=None) -> bool` posts "✅ Checklist <label> lengkap" with one line per item (source + value) once per (session, milestone); returns whether it posted.

- [ ] **Step 1: Write failing tests** (respx on the Chatwoot routes the existing tests use): `test_checklist_ticket_note_labels_and_priority` (one pending alloc entry → note text contains "Tim Alloc", labels POST body has `team-alloc` and `logisly-checklist`, priority `high`, `PENDING_TICKETS` empty after); `test_checklist_ticket_failopen` (Chatwoot 500 → returns 0, no exception); `test_complete_note_once` (second call returns False, one note request).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(logisly): route not-OK checklist items to teams`.

### Task 4: `POST /logisly/milestone`

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/logisly/router.py`
- Test: `backend/apps/backend/src/chatbot/features/logisly/test_logisly_router.py`

**Interfaces:**
- Consumes: Task 1 `opening_message`, `is_quiet`; Task 2 `set_milestone`, `register_session(..., milestone=)`; Task 3 `file_checklist_tickets`; existing `driver_address`, `ChatwootDriverChannel`, `ladder`.
- Produces:
  - `class MilestoneBody(BaseModel): order_id: str; milestone: str; source: Literal["gps","app","manual"] = "manual"; channel: str = ""; force: bool = False`
  - `POST /logisly/milestone` → `{"order_id","milestone","sent": bool, "deferred": bool, "conversation_id": int|None, "message": str|None, "not_ok": [item ids]}`. 404 unknown order, 422 unknown milestone. Same milestone as current → `sent: False`, no Chatwoot call. Quiet hours and not `force` → `deferred: True`, checklist still applied. A message is sent when `opening_message` is not None or a system item is not OK; then the session is registered with `milestone`, system not-OK items are queued and `file_checklist_tickets` runs, and the existing ladder starts when configured. The router takes an injectable `now: Callable[[], datetime]` (default UTC now) for tests.
  - `GET /logisly/trips` adds `"milestones": [[code,label],…]`.

- [ ] **Step 1: Write failing tests**: `test_milestone_opens_conversation_with_only_open_items` (LGS-24021 → `at_pickup` at 10:00 WIB: one conversation POST whose content contains the install link and the photo ask; response `not_ok` contains `driver_app` and `do_in_app`; a private note with `team-alloc`); `test_same_milestone_twice_sends_once`; `test_quiet_hours_defer_unless_forced` (23:00 WIB → `deferred` true, no conversation; `force` → sent); `test_nothing_open_sends_nothing` (all facts true and items pre-recorded → `sent` false); `test_milestone_validates_order_and_code`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(logisly): milestone trigger opens a checklist conversation`.

### Task 5: Agent tool, prompt and post-turn hook

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/chat/agents.py` (logisly tools block ~line 310-365)
- Modify: `backend/apps/backend/src/chatbot/features/chat/prompts.py` (`LOGISLY_AGENT_INSTRUCTION`, `LOGISLY_MEDIA_INSTRUCTION`)
- Modify: `backend/apps/backend/src/chatbot/features/chat/service.py` (after the handoff branch, before `products = …`)
- Test: `backend/apps/backend/src/chatbot/features/logisly/test_logisly_persona.py`

**Interfaces:**
- Consumes: Task 1 `record_item`, `is_complete`; Task 2 `TRIPS`, `ACTIVE_SESSIONS`; Task 3 `file_checklist_tickets`, `note_checklist_complete`.
- Produces:
  - `record_checklist_tool(tool_context, order_id: str, item_id: str, ok: bool, value: str, source: str = "driver")` — milestone taken from the trip's current `milestone`; session id from `tool_context.state.get("session_id")` or `tool_context._invocation_context.session.id` (whichever the existing tools already rely on; check before choosing); returns `record_item`'s result as `[internal] …`. Registered first in the logisly tool list.
  - Prompt section "## Milestone checklist": tick every item a reply answers, several per reply; photo items: read the overlay (plate, time, place), plate ≠ trip plate → not OK with the plate seen as value; never ask an item already ok; ask at most two open items per message; answer the driver's own question first from the trip context (DO weight, DTS number, load map link, install link); "belum bisa login" → send the install link and tick nothing.
  - `ChatService._after_logisly_turn(session_id)` for persona logisly and `crm-` sessions: `file_checklist_tickets`, then `note_checklist_complete` when the session's trip milestone `is_complete`. Wrapped in try/except, logs on failure.

- [ ] **Step 1: Write failing tests** in `test_logisly_persona.py`: `test_logisly_prompt_has_checklist_rules` (instruction contains `record_checklist_tool`, "two", "install"); `test_logisly_tools_include_record_checklist` (build the logisly agent the way the existing persona test does and assert the tool name is present).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** tool, prompt section, hook. **Step 4: Run** `uv run pytest -q src/chatbot/features/logisly src/chatbot/features/chat -k "logisly or persona"` → PASS.
- [ ] **Step 5: Commit** `feat(logisly): agent ticks the milestone checklist`.

### Task 6: Console stepper and controls

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/logisly/console.py`
- Test: `backend/apps/backend/src/chatbot/features/logisly/test_logisly_router.py` (`test_console_has_milestone_controls`: page contains `/logisly/milestone` and "Next milestone")

**Interfaces:**
- Consumes: Task 4 endpoint and `milestones` list in `/logisly/trips`.
- Produces: per trip a six-step stepper (current highlighted), the current checklist as rows with a source tag (Sistem / Foto / Driver) and status (✓ / ✗ / …), buttons "Next milestone (GPS)", "Next milestone (App)", a manual milestone `<select>` + "Set", a "Force (ignore quiet hours)" checkbox, and the existing anomaly and call buttons kept for `in_transit`.

- [ ] **Step 1: Write the failing test.** **Step 2: Run** → FAIL. **Step 3: Implement** in the existing page style (Montserrat, existing CSS tokens, dark mode). **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(logisly): console shows milestone checklists`.

### Task 7: Full suite and live check

- [ ] **Step 1:** `uv run pytest -q src/chatbot/features/logisly src/chatbot/features/chat` → all pass; `uv run ruff check src/chatbot/features/logisly src/chatbot/features/chat` clean.
- [ ] **Step 2:** Local run (`cd backend/apps/backend && AGENT_PERSONA=logisly uv run uvicorn chatbot.main:app --port 8080`), open `/logisly/console?key=…`, move LGS-24021 to `at_pickup`: the opening carries the install link and the photo ask; reply "sya blum bisa login" → agent re-sends the link; send a plate photo → `unit_photo` ticked. Note results in the commit message of any fix.
- [ ] **Step 3:** Update `demo-data/logisly/README.md` with a short UC1 section (scenarios per order id and the milestone script). Commit `docs(logisly): UC1 milestone demo script`.
