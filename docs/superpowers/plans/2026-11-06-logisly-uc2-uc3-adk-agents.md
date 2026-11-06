# Logisly UC2 + UC3 ADK Agents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Two demo agents built with Google ADK, deployed to Vertex AI Agent Engine and registered in Gemini Enterprise, which is the frontend.
- **UC2 purchasing agent:** the user uploads quotation and tender files in the chat. Gemini reads them and records them through typed tools. Deterministic code compares the quotes against the pricing DB and business rules, and the agent recommends per lane.
- **UC3 product manager agent:** the user uploads requirement sources. The agent drafts a PRD, checks consistency and capacity, and proposes and applies issue changes on a mock GitHub after the user approves.

**Architecture:**
- One uv project at `apps/logisly-agents/`, package `logisly_agents`.
- Every number the agents report comes from deterministic Python tools: baselines, normalisation, flags, scores, capacity. The model reads the uploads, fills tool arguments, explains, and decides which tool to call.
- All per-conversation state lives in ADK session state (`tool_context.state`), because Agent Engine instances are stateless. This covers recorded quotations, the mock GitHub working copy and saved documents. Each new chat starts clean.
- Reference data is read-only and bundled with the deployment: pricing DB, business rules, templates, conventions, team, issue snapshot.
- Uploaded Excel, Word, email and text files are converted to text parts by a `before_model_callback`, because Gemini reads PDF and images natively but not xlsx or docx.
- Human approval is conversational: the agent shows what it will write and calls a write tool only after the user agrees. ADK's `require_confirmation` stays available behind a setting for `adk web` rehearsals. Gemini Enterprise may not render ADK confirmation events.

**Tech Stack:**
- Python 3.13 via uv, `google-adk==2.2.0` (the version the backend locks), `google-genai`, `pydantic-settings`.
- Libraries: `python-docx`, `openpyxl`, `pyyaml`, sqlite3.
- Dev tools: pytest, ruff.
- Deployment: Vertex AI Agent Engine, then a Gemini Enterprise registration.

**Spec:** there is no separate spec; the user skipped the design questions. The decisions are in the "Design" section. The data contract is `demo-data/logisly/README.md`, and the answer keys are `demo-data/logisly/uc2/expected/` and `demo-data/logisly/uc3/expected/`.

## Global Constraints

- **Branch and location:** work on branch `logisly-demo` of `~/Archive/id-crm-ticketing`. New code goes under `apps/logisly-agents/`, except the generator change in Task 3.
- **Model and auth:**
  - Gemini on Vertex AI with ADC. Never an API key.
  - Defaults: `GOOGLE_GENAI_USE_VERTEXAI=true`, `GOOGLE_CLOUD_PROJECT=lv-playground-genai`, `GOOGLE_CLOUD_LOCATION=global`, model `gemini-3.5-flash`.
  - Verified 2026-11-06: `gemini-3.5-flash` works only at location `global`.
- **ADK version:** pin `google-adk==2.2.0`.
- **Secrets:** never commit credentials. `.env` is git-ignored and `.env.example` is committed.
- **Copy:** agent instructions and tool messages are in Bahasa Indonesia; the agent replies in the user's language. Never mention other clients (no "Proton"). No em dashes in agent instructions or user-facing tool messages.
- **Demo date:** the "as of" date defaults to `2026-11-09` (setting `LOGISLY_AS_OF`).
- **Running tests:**
  - Run from `apps/logisly-agents`: `uv run pytest -q`.
  - Tests that call Vertex are `@pytest.mark.live` and excluded by default (`addopts = -m "not live"`). Run them with `uv run pytest -q -m live`.
- **Lint:** `uv run ruff check .` is clean at the end of every task.
- **Commits:** end every commit message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Commit only files under `apps/logisly-agents/`, `demo-data/logisly/` and `docs/superpowers/plans/`. Never commit the untracked `docs/proposals/*.xlsx`.

## Design

**UC2 demo:**
1. The user uploads the tender PDF and the quotation files from `demo-data/logisly/uc2/inbox/attachments/`, in one message or several. The one body-only quotation (Samudra Truk) is pasted as text, from `04_quote_sti.eml`.
2. For each document, the agent calls `record_tender(...)` or `record_quotation(...)`. The typed arguments are the extraction.
3. The agent calls `compare_quotes()`, then summarises and recommends per lane.
4. The user asks follow-ups, such as "penawaran Mitra Armada berapa?" or "kenapa Garuda tidak dipilih?". These use `list_quotations`, `price_history` and `query_pricing_sql`.
5. The agent drafts follow-up emails as text in the chat. `record_decision` is called after the user approves or rejects.

**UC3 demo:**
1. The user uploads the four files from `demo-data/logisly/uc3/sources/`.
2. The agent reads the templates, conventions, team and calendar, and searches the mock issues.
3. It returns the PRD in the chat in the template's structure, and keeps it with `save_document`.
4. It reports conflicts, duplicates and gaps, and calls `capacity_check`.
5. It proposes issue creates and updates. After the user approves, it applies them to the mock tracker.

**Session state keys:**
- `uc2:tender` (one dict) and `uc2:quotations` (a list of dicts with a stable `quote_id` = `Q<n>`)
- `uc2:decisions` (a list)
- `uc3:tracker` (`{"issues": [...], "next": {repo: n}}`)
- `uc3:docs` (name → markdown)
- Values are JSON-safe (pydantic `model_dump(mode="json")`).

## Review Focus

1. A quotation line whose route matches no tender lane is reported in `unmatched_lines`. It does not crash and is not silently dropped. Test in Task 5.
2. The same quotation recorded twice (same transporter, same document date, same lines) replaces the earlier record instead of duplicating it. A later document marked revision supersedes the earlier one from the same transporter. Test in Task 4.
3. An upload of an unsupported type (zip, an oversized xlsx) is replaced by a short text part saying it could not be read; the model call does not fail. A corrupt xlsx is handled the same way. Test in Task 2.
4. `query_pricing_sql` given a write statement or several statements returns an error dict; the DB is opened read-only anyway. Test in Task 3.
5. A UC3 write on an unknown ref (`driver-app#999`), or a create missing a required label class, returns an error dict and leaves the state unchanged. Test in Task 8.

---

### Task 1: Project scaffold and settings

**Files:**
- Create: `apps/logisly-agents/pyproject.toml`, `.gitignore`, `.env.example`, `justfile`, `README.md` (stub), `logisly_agents/__init__.py`, `logisly_agents/config.py`
- Test: `tests/conftest.py`, `tests/unit/test_config.py`

**Interfaces:**
- Produces: `Settings` (pydantic-settings, env prefix `LOGISLY_`, reads `.env`) with these fields:
  - `data_dir: Path`: default `<repo>/demo-data/logisly`, resolved from `config.py`'s location. When `logisly_agents/data/` exists (the deploy bundle, Task 10), that wins.
  - `model: str = "gemini-3.5-flash"`
  - `as_of: date = date(2026, 11, 9)`
  - `require_confirmation: bool = False`
- Produces: `get_settings() -> Settings`, cached with `lru_cache`. The conftest autouse fixture clears the cache.
- Produces: `configure_vertex_env() -> None`. It `os.environ.setdefault`s the three `GOOGLE_*` defaults.

- [ ] **Step 1:** Write `pyproject.toml`:
  - Dependencies: `google-adk==2.2.0`, `google-genai>=2.8.0`, `pydantic-settings>=2.6`, `python-docx>=1.1`, `openpyxl>=3.1`, `pyyaml>=6.0`.
  - Dev group: `pytest>=8`, `pytest-asyncio>=0.24`, `ruff>=0.8`, `pillow>=10` (to import `gen_uc2.py` in tests).
  - Hatch wheel `packages=["logisly_agents"]`.
  - Ruff: line-length 110, rules `E,F,I,B,UP`.
  - Pytest: `testpaths=["tests"]`, `addopts='-m "not live"'`, marker `live`, `asyncio_mode="auto"`.
  - Run `uv sync`; `uv run python -c "import google.adk; print(google.adk.__version__)"` prints `2.2.0`.
- [ ] **Step 2:** Write `.gitignore` (`.env`, `.venv/`, `__pycache__/`, `logisly_agents/data/`, `var/`) and `.env.example` (the GOOGLE_* defaults). The `justfile` has `sync`, `test`, `test-live`, `lint`, `web` → `uv run adk web adk_apps`, and `deploy`, which Task 10 adds.
- [ ] **Step 3: Failing tests:**
  - `test_defaults_point_at_demo_data`: `data_dir/"uc2"/"business_rules.yaml"` exists, and `model == "gemini-3.5-flash"`.
  - `test_env_override`: `LOGISLY_AS_OF=2026-12-01` gives `as_of == date(2026,12,1)`.
  - `test_configure_vertex_env_keeps_existing`: a preset `GOOGLE_CLOUD_LOCATION` is kept, and an unset one becomes `global`.
- [ ] **Step 4:** Implement; tests and ruff pass. Commit `feat(logisly-agents): scaffold uv project and settings`.

### Task 2: Upload-to-text callback

**Files:**
- Create: `logisly_agents/uploads.py`
- Test: `tests/unit/test_uploads.py`

**Interfaces:**
- Produces: `convert_uploads(callback_context, llm_request) -> None`, an ADK `before_model_callback` (check the exact signature in ADK 2.2.0 `LlmAgent.before_model_callback`). For every `inline_data` part in `llm_request.contents`:
  - **Unchanged:** `application/pdf`, `image/*`, `audio/*`, `video/*`, `text/plain`.
  - **xlsx** (`application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`, or a `.xlsx` display name): replaced by a text part `"[Lampiran <name>]\n"` plus one line per non-empty row, cells joined by `" | "`, across all sheets with a `## <sheet>` header.
  - **docx:** paragraphs and table rows (`" | "`) in document order.
  - **`message/rfc822`, or a `.eml` name:** headers (From, To, Subject, Date) plus the plain body. Attachments are listed by name only.
  - **`text/markdown`, `text/csv`, or `.md`/`.txt`/`.csv`:** decoded UTF-8 text.
  - **Anything else, or a conversion error:** the text part `"[Lampiran <name> tidak dapat dibaca: <reason>]"`.
  - Converted text is capped at 60,000 characters, with `"... (dipotong)"` appended.
- Produces: `part_to_text(name: str | None, mime: str, data: bytes) -> str | None`. This is the pure function the callback uses; it returns `None` for kinds that stay unchanged.

- [ ] **Step 1: Failing tests,** using the real demo files:
  - `test_xlsx_keeps_footnote`: `part_to_text` of `uc2/inbox/attachments/Penawaran_CV_Mitra_Armada_Jaya_Q-MAJ-1.xlsx` contains `"3850000"` and `"*belum termasuk tol"`.
  - `test_docx_with_table`: for `uc3/sources/01_request_driver-app-ai-assistant.docx`, contains `"minggu kedua Januari 2027"` and `"Kasus selesai tanpa ops | 20-30% | 60%"`.
  - `test_eml`: for `uc3/sources/03_email_cs_epod-request.eml`, contains `"31 Desember 2026"` and `"Subject: Fwd: Permintaan bukti terima digital"`.
  - `test_pdf_untouched`: `part_to_text(..., "application/pdf", ...) is None`.
  - `test_corrupt_xlsx`: bytes `b"not a zip"` with the xlsx mime give text starting `"[Lampiran"` that contains `"tidak dapat dibaca"`.
  - `test_callback_rewrites_request`: build an `LlmRequest` with contents `[Content(role="user", parts=[Part.from_text("cek"), Part.from_bytes(xlsx_bytes, mime)])]`. After the callback, part 2 has `.text` and no `inline_data`, and part 1 is unchanged.
- [ ] **Step 2:** Implement; tests and ruff pass. Commit `feat(logisly-agents): convert office and email uploads to text for Gemini`.

### Task 3: UC2 pricing repository (+ toll columns in the generator)

**Files:**
- Modify: `demo-data/logisly/gen_uc2.py`:
  - The `lanes` table gains `base_toll_idr` (= `Lane.toll`); `truck_types` gains `toll_factor`. Add both to `SQLSERVER_DDL`.
  - Regenerate (`cd demo-data/logisly && /usr/local/bin/python3 gen_uc2.py`).
  - Confirm `git diff --stat demo-data/logisly/uc2/expected` is empty.
- Create: `logisly_agents/uc2/__init__.py`, `logisly_agents/uc2/pricing.py`
- Test: `tests/unit/uc2/test_pricing.py`

**Interfaces:**
- Produces: `PricingRepo(db_path: Path)`, which opens SQLite read-only (`file:...?mode=ro`, `uri=True`). Methods:
  - `lane_for_route(origin: str, destination: str) -> str | None`:
    - Normalise to lowercase and strip punctuation; drop a trailing `", <regency>"`; treat "jakarta utara" and "tanjung priok" as "jakarta" only when no exact lane exists.
    - The match: normalised lane origin and destination each equal or contain the input, or the input contains them.
    - Returns `None` when no lane or more than one lane matches.
  - `truck_code(name: str) -> str | None`: case- and space-insensitive match on name or code.
  - `transporter_by_name(name: str) -> dict | None`: ignores `PT`/`CV`, case and punctuation.
  - `customer_by_name(name: str) -> dict | None`
  - `baseline(lane_id: str, truck: str, periods: Sequence[str]) -> float | None`: median of `rate_history.rate_idr`.
  - `on_time(transporter_id: str) -> float`: mean of all `on_time_pct` rows, rounded to 1 decimal.
  - `toll(lane_id: str, truck: str) -> float`: `base_toll_idr * toll_factor`.
  - `capacity_ton(truck: str) -> float`
  - `contract_rate(customer_id: str, lane_id: str, truck: str) -> dict | None`
  - `price_history(lane_id: str, truck: str) -> list[dict]`: monthly median, min, max and count.
  - `run_select(sql: str, limit: int = 200) -> dict`: returns `{"columns","rows"}` or `{"error"}`. It accepts one `SELECT`/`WITH` statement only, after stripping comments; at most one trailing `;`.

- [ ] **Step 1: Failing tests:**
  - `test_lane_matching`:
    - `("Cikarang, Bekasi","Surabaya")→"LN-02"`
    - `("Cikarang","Surabaya")→"LN-02"`
    - `("Karawang","Surabaya")→"LN-03"`
    - `("Jakarta","Bandar Lampung")→"LN-10"`
    - `("Cibitung","Bandung")→"LN-04"`
    - `("Bogor","Surabaya")→None`
  - `test_baselines_match_answer_key`: `baseline` over `("2026-08","2026-09","2026-10")` equals `expected/recommendation.json` `lanes[Tn]["baseline_median_90d"]` for `LN-02/FUSO_WB`, `LN-01/CDD_BOX`, `LN-10/TRONTON_WB`, `LN-04/CDE_BOX` (T1..T4).
  - `test_on_time`: TRP-001 gives 93.8 and TRP-002 gives 82.3.
  - `test_toll`: `toll("LN-01","CDD_BOX") == 975000`.
  - `test_run_select_rejects_writes`: `"DELETE FROM lanes"`, `"select 1; drop table lanes"` and `"PRAGMA journal_mode=WAL"` each return `{"error": ...}`. `"SELECT count(*) FROM customers"` returns rows `[[600]]`.
- [ ] **Step 2:** Make the generator change and regenerate; implement; tests and ruff pass. Commit `feat(logisly-agents): UC2 pricing repository; toll columns in demo DB`.

### Task 4: UC2 schemas and the session quotation store

**Files:**
- Create: `logisly_agents/uc2/schemas.py`, `logisly_agents/uc2/store.py`
- Test: `tests/unit/uc2/test_store.py`

**Interfaces:**
- Produces, in `schemas.py` (pydantic):
  - `QuoteLine` has these fields:
    - `origin: str`, `destination: str`, `truck_type: str`
    - `price_idr: int`, `unit: Literal["trip","ton","kg","other"] = "trip"`
    - `trips_per_month: int | None = None`
    - `excludes_toll: bool = False`, `note: str = ""`
  - `TenderLine`: `origin`, `destination`, `truck_type`, `trips_per_month: int`.
  - `Tender`:
    - `reference: str`, `customer: str`, `lanes: list[TenderLine]`
    - `period: str = ""`, `submission_deadline: str = ""`
    - `requirements: list[str] = []`, `min_insurance_idr: int | None = None`
  - `Quotation` has these fields:
    - `transporter: str`, `document_name: str`, `document_date: date`
    - `rfq_reference: str = ""`, `is_revision: bool = False`
    - `lines: list[QuoteLine]`
    - `payment_term_days: int | None = None`, `valid_until: date | None = None`
    - `includes: list[str] = []`, `insurance_idr: int | None = None`
    - `gps_tracking: bool | None = None`, `notes: str = ""`
- Produces, in `store.py`. All functions take `state: MutableMapping[str, Any]` (an ADK `State` or a dict in tests) and store JSON-safe dumps.
  - `set_tender(state, tender: Tender) -> dict`
  - `get_tender(state) -> Tender | None`
  - `add_quotation(state, q: Quotation) -> dict` returns `{"quote_id", "replaced": bool, "supersedes": str | None}`:
    - Same transporter (case-insensitive) with the same date and identical lines replaces the existing record and keeps its id.
    - `is_revision`, or a later `document_date` from the same transporter, marks the older one `superseded_by`.
  - `list_quotations(state, include_superseded: bool = True) -> list[dict]`, where each dict is the quotation dump plus `quote_id` and `superseded_by`.
  - `add_decision(state, lane: str, quote_id: str, decision: str, note: str) -> dict`

- [ ] **Step 1: Failing tests:**
  - `test_duplicate_replaces`: adding the same quotation twice leaves 1 record, and the second result has `replaced is True`.
  - `test_revision_supersedes`: Trans Cahaya on 2026-11-03, then Trans Cahaya with `is_revision=True` on 2026-11-05. Then `list_quotations(state, include_superseded=False)` has 1 record (the 11-05 one), and the first is `superseded_by` the second's id.
  - `test_state_is_json_safe`: `json.dumps(dict(state))` succeeds after adding the tender, two quotations and a decision.
- [ ] **Step 2:** Implement; tests and ruff pass. Commit `feat(logisly-agents): UC2 schemas and session quotation store`.

### Task 5: UC2 evaluation engine

**Files:**
- Create: `logisly_agents/uc2/rules.py`, `logisly_agents/uc2/evaluate.py`
- Test: `tests/unit/uc2/test_evaluate.py`

**Interfaces:**
- Consumes: `PricingRepo` (Task 3); `Tender`, `Quotation`, and the `list_quotations` dicts (Task 4).
- Produces: `Rules` (pydantic) and `load_rules(path: Path) -> Rules`, from `business_rules.yaml`.
- Produces: `baseline_periods(as_of: date, months: int = 3) -> tuple[str, ...]`, the full months before `as_of`'s month. For 2026-11-09 that is `("2026-08","2026-09","2026-10")`.
- Produces: `compare(tender: Tender, quotes: list[dict], repo: PricingRepo, rules: Rules, as_of: date) -> dict` returning `{"lanes": {...}, "unmatched_lines": [...], "superseded": {old_id: new_id}}`.
  - `lanes` is keyed `"T1".."Tn"` in tender order.
  - Each lane has exactly the keys of `expected/recommendation.json["lanes"][Tn]`, and quote entries have the same keys, with `quote_id` being the store id.
  - `customer_id` comes from `repo.customer_by_name(tender.customer)`.

**The algorithm to port exactly:** the per-lane loop, scoring and follow-up in `demo-data/logisly/gen_uc2.py` `evaluate()`.
- Skip superseded quotes.
- Normalise per ton as price × the required truck's `capacity_ton`. Add `repo.toll()` when `excludes_toll`.
- Blocking issues:
  - `truck_type_mismatch`: the line's truck code is not the tender's
  - `incomplete`: the payment term or validity is missing
  - `expired_validity`: `valid_until < as_of`
- Note `"masa berlaku < 21 hari"` when validity runs fewer than `min_validity_days` past the document date.
- Flags come from the rules' thresholds.
- Score: `0.5*price + 0.3*on_time + 0.2*compliance`:
  - price `(hi-p)/(hi-lo)`, or 1.0 when equal
  - on_time `clamp((ot-80)/20)`
  - compliance `max(0, 1-0.5*count(flags except above_baseline))`
- Sell price: `round_to_50k(buy/(1-target_pct/100))`.
- Follow-up: one entry per quote with any blocking issue.

- [ ] **Step 1: Failing test** `test_matches_answer_key`:
  - `sys.path.insert` `demo-data/logisly`, import `gen_uc2`.
  - Build the `Tender` from `TENDER`, `CUSTOMER` and `common.LANE`, and one `Quotation` per `QUOTES` entry via `quote_lines(q, baselines)`, where the baselines come from `PricingRepo`. Mark `Q-TCL-2` `is_revision=True`.
  - Add them to a dict state in the order of `QUOTES`. Map `Q-*` ids to store ids by transporter and date.
  - Run `compare(as_of=date(2026,11,9))`. For T1..T4, these equal the answer key:
    - `recommended`
    - `buy_per_trip`
    - `proposed_sell_per_trip_at_15pct_margin`
    - `current_contract_sell`
    - every quote's `normalised_per_trip`, `blocking_issues`, sorted `flags`, and `vs_baseline_pct`
- [ ] **Step 2: Failing test** `test_unmatched_line_is_reported`: an extra line `("Bogor","Surabaya")` shows in `unmatched_lines`, and the T1..T4 recommendations are unchanged.
- [ ] **Step 3: Failing test** `test_rules_loaded`: `scoring.price == 0.5` and `margin.target_pct == 15`.
- [ ] **Step 4:** Implement; tests and ruff pass. Commit `feat(logisly-agents): UC2 deterministic quotation comparison`.

### Task 6: UC2 ADK agent

**Files:**
- Create: `logisly_agents/uc2/tools.py`, `logisly_agents/uc2/agent.py`, `adk_apps/uc2_purchasing/__init__.py` (`from . import agent`), `adk_apps/uc2_purchasing/agent.py` (`configure_vertex_env()`, then re-export `root_agent`)
- Test: `tests/unit/uc2/test_tools.py`, `tests/unit/uc2/test_agent.py`, `tests/live/test_uc2_agent_live.py`

**Interfaces:**
- Consumes: Tasks 2 to 5.
- Produces, in `tools.py`: ADK tool functions taking `tool_context: ToolContext` last. They return JSON-safe dicts and never raise; errors come back as `{"error": str}`. Indonesian docstrings, because ADK shows them to the model.
  - `record_tender(tender: Tender, tool_context) -> dict`
  - `record_quotation(quotation: Quotation, tool_context) -> dict`. Check that ADK 2.2.0 builds the function declaration from a pydantic-typed parameter. If it does not, take `quotation: dict` and validate inside with `Quotation.model_validate`, returning validation errors as `{"error"}`.
  - `list_quotations(transporter: str = "", route: str = "", tool_context) -> dict`
  - `compare_quotes(tool_context) -> dict`: returns `{"error": "Belum ada dokumen tender"}` when no tender is recorded.
  - `price_history(origin: str, destination: str, truck_type: str) -> dict`
  - `query_pricing_sql(sql: str) -> dict`
  - `record_decision(lane: str, quote_id: str, decision: str, note: str = "", tool_context) -> dict`
- Produces: `build_uc2_agent(settings) -> LlmAgent`, with `root_agent` in `agent.py`.
  - Name `logisly_purchasing`, `before_model_callback=convert_uploads`.
  - `record_decision` is wrapped in `FunctionTool(..., require_confirmation=settings.require_confirmation)`.
  - The instruction (Bahasa Indonesia) says:
    - it is Logisly's purchasing assistant
    - for each uploaded or pasted document, decide whether it is a tender, a quotation or neither, and record it with the matching tool
    - copy figures exactly as printed, including the unit, "belum termasuk tol" footnotes, and missing payment terms or validity as empty
    - after recording, call `compare_quotes`
    - every number in a reply comes from a tool result
    - recommend per lane with the reasons and the problems found
    - offer follow-up email drafts, written in the chat, for blocked quotes
    - ask before `record_decision`; the final decision belongs to the purchasing team
    - reply briefly in the user's language, with tables for comparisons

- [ ] **Step 1: Failing tests:**
  - `test_tools_end_to_end_without_model`: on a dict-backed fake `ToolContext` (a simple object with `.state = {}`), call `record_tender` and `record_quotation` with the Task 5 fixtures, then `compare_quotes`. T4 recommends Prima Fleet's id.
  - `test_compare_without_tender`: the result has key `"error"`.
  - `test_query_sql_error_is_dict`
  - `test_agent_wiring`: the tool names are exactly the 7 above; the callback is `convert_uploads`; `record_decision` requires confirmation only when `LOGISLY_REQUIRE_CONFIRMATION=true`.
  - `test_adk_entry_imports`: `importlib.import_module("adk_apps.uc2_purchasing.agent").root_agent.name == "logisly_purchasing"`.
- [ ] **Step 2: Live test** (`@pytest.mark.live`): use `InMemoryRunner` with one user message.
  - The message is the text "Ini dokumen tender dan penawaran untuk RFQ-LGS-2611-007. Tolong catat dan beri rekomendasi.", plus `Part.from_bytes` for the tender PDF and the attachments of Q-TCL-1, Q-MAJ-1, Q-LJE-1, Q-GAS-1, Q-PFN-1 and Q-TCL-2, plus the text of the Samudra body from `04_quote_sti.eml`.
  - Afterwards the session state has a tender with 4 lanes and 7 quotations, and `compare_quotes` run on that state recommends Trans Cahaya for T1 to T3 and Prima Fleet for T4.
  - If it fails, fix the instruction or the docstrings, not the assertions.
- [ ] **Step 3:** Implement; unit tests and ruff pass; the live test passes. Commit `feat(logisly-agents): UC2 purchasing agent`.

### Task 7: UC3 context and capacity check

**Files:**
- Create: `logisly_agents/uc3/__init__.py`, `logisly_agents/uc3/context.py`, `logisly_agents/uc3/capacity.py`
- Test: `tests/unit/uc3/test_context.py`, `tests/unit/uc3/test_capacity.py`

**Interfaces:**
- Produces: `read_context(uc3_dir: Path) -> dict` with keys `templates` (filename → text), `conventions`, `roster` and `calendar` (parsed YAML).
- Produces: `capacity_check(items: list[dict], roster: dict, calendar: dict) -> dict`:
  - `items` are `{"assignee": github_login, "sp": int, "sprint": str}`.
  - The window is sprints whose `end` is before `code_freeze.start`.
  - Teams map by roster repos: `mobile` = driver-app, `backend` = platform-api, `frontend` = shipper-portal or ops-console.
  - Capacity is `capacity_sp` × the window sprints, minus leave pro-rated by the Mon–Fri days of leave inside window sprints.
  - Committed comes from `roster.committed` within the window.
  - The result is `{"window", "teams": {team: {"capacity","committed","free","proposed","utilisation_pct"}}, "after_window", "no_team", "unknown_assignees"}`, with `utilisation_pct = round(proposed/free*100)`.

- [ ] **Step 1: Failing tests:**
  - `test_context_loads`: two templates, and `conventions["repos"]` has 5 keys.
  - `test_capacity_matches_report`: the items are A1 to A5, B1 to B5, P1, P2 and P3 from `uc3/expected/backlog.json` (assignee, `sp:` label, milestone), plus `driver-app#142` (`bayu-p`, 5, Sprint 26.23) and `ops-console#64` (`laras-ayu`, 3, Sprint 26.24). The result is:
    - `mobile == {capacity: 69, committed: 24, free: 45, proposed: 31, utilisation_pct: 69}`
    - `backend == {69, 18, 51, 28, 55}`
    - `frontend == {30, 13, 17, 11, 65}`
    - the window is `["Sprint 26.22","Sprint 26.23","Sprint 26.24"]`
  - `test_phase2_items_after_window`: a "Sprint 27.02" item goes to `after_window`.
  - `test_assignee_buckets`: `tegar-qa` goes to `no_team`, and `ghost` goes to `unknown_assignees`.
- [ ] **Step 2:** Implement; tests and ruff pass. Commit `feat(logisly-agents): UC3 context and capacity check`.

### Task 8: UC3 mock issue tracker in session state

**Files:**
- Create: `logisly_agents/uc3/tracker.py`
- Test: `tests/unit/uc3/test_tracker.py`

**Interfaces:**
- Produces: `MockTracker(state: MutableMapping[str, Any], snapshot_path: Path, conventions: dict, calendar: dict)`. It seeds `state["uc3:tracker"]` from the snapshot when absent, and numbers new issues max+1 per repo. Methods:
  - `search(repo="", state_filter="open", text="", label="") -> list[dict]`, returning summaries without comments.
  - `get(ref: str) -> dict`: ref format `"<repo>#<n>"`; returns the issue with comments.
  - `create(repo, title, body, labels: list[str], milestone: str | None, assignees: list[str]) -> dict`, returning `{"ref"}`.
  - `update(ref, add_labels=(), remove_labels=(), milestone=None, assignees=None, body_append="") -> dict`
  - `comment(ref, body) -> dict`
  - `close(ref, reason: Literal["completed","not_planned","duplicate"], comment="") -> dict`
  - Validation and unknown-ref failures raise `TrackerError` and leave the state untouched.
- **Validation:**
  - The repo is one of the 5 in conventions.
  - Created labels contain exactly one `type:*`, one `area:*` and one `priority:*`, and every label exists in conventions.
  - The title matches `^\[[a-z-]+\] .+`.
  - The milestone, when given, is a sprint in the calendar.
  - `close(..., "duplicate")` adds `status:duplicate`.

- [ ] **Step 1: Failing tests:**
  - `test_seeded`: `search(repo="driver-app", state_filter="all")` has 7 issues; `get("driver-app#142")["comments"]` has 3.
  - `test_create_validates`: missing `priority:*` raises `TrackerError` and the state is unchanged. A valid `[driver-app] Layar chat dengan AI assistant` returns `driver-app#154`.
  - `test_close_duplicate`: `driver-app#148` closed, with label `status:duplicate`.
  - `test_unknown_ref`: `get("driver-app#999")` raises `TrackerError`.
  - `test_fresh_state_reseeds`: a new empty state gives the snapshot back (7 driver-app issues).
- [ ] **Step 2:** Implement; tests and ruff pass. Commit `feat(logisly-agents): UC3 mock issue tracker in session state`.

### Task 9: UC3 ADK agent

**Files:**
- Create: `logisly_agents/uc3/tools.py`, `logisly_agents/uc3/agent.py`, `adk_apps/uc3_product/__init__.py`, `adk_apps/uc3_product/agent.py`
- Test: `tests/unit/uc3/test_tools.py`, `tests/unit/uc3/test_agent.py`, `tests/live/test_uc3_agent_live.py`

**Interfaces:**
- Consumes: Tasks 2, 7 and 8.
- Produces: tools returning dicts that never raise (`{"error"}`):
  - `get_templates()`, `get_conventions()`, `get_team()` (roster and calendar)
  - `search_issues(repo="", state="open", text="", label="", tool_context)`, `get_issue(ref, tool_context)`
  - `capacity_check(items: list[dict])`
  - `save_document(name: str, markdown: str, tool_context) -> {"name", "chars"}`: stores to `state["uc3:docs"]`.
  - `create_issue(...)`, `update_issue(...)`, `comment_issue(ref, body, ...)`, `close_issue(ref, reason, comment="", ...)`: the write tools, wrapped in `FunctionTool(require_confirmation=settings.require_confirmation)`.
- Produces: `build_uc3_agent(settings) -> LlmAgent` named `logisly_product_manager`, with `before_model_callback=convert_uploads`. The instruction, in Bahasa Indonesia, gives this order of work:
  1. Read every uploaded source, load the templates, conventions and team, and search existing issues before proposing anything.
  2. Write the PRD in the template's structure, show it, and `save_document` it.
  3. Report conflicts, duplicates and gaps, each citing its source file or issue ref and who decides.
  4. Call `capacity_check` with the proposed stories.
  5. Present the proposed creates and updates as a table and ask for approval.
  6. Only after the user agrees, call the write tools.
  - The instruction also says: never invent dates or story points not grounded in the sources or conventions, and never close or edit an issue the user did not approve.

- [ ] **Step 1: Failing tests:**
  - `test_agent_wiring`: the tool name set is exactly the 11 above; the callback is `convert_uploads`.
  - `test_write_error_is_dict`: `close_issue("driver-app#999","duplicate")` returns `{"error": ...}`.
  - `test_save_document`: the document lands in state.
- [ ] **Step 2: Live test** (`@pytest.mark.live`):
  - Turn 1 sends the 4 source files as parts (docx and eml as bytes, md and txt as text) with "Siapkan PRD AI assistant driver app dan backlog GitHub dari input minggu ini."
  - Turn 2 sends "Setuju, terapkan semua perubahannya."
  - Assert the state has one doc containing `"Pertanyaan terbuka"`.
  - Assert `driver-app#148` is closed.
  - Assert at least 8 new issues across `driver-app` and `platform-api`.
  - Assert the turn-1 text mentions `"18 Desember"` and `"#131"`.
- [ ] **Step 3:** Implement; unit tests and ruff pass; the live test passes. Commit `feat(logisly-agents): UC3 product manager agent`.

### Task 10: Deploy to Agent Engine and run guide

**Files:**
- Create: `apps/logisly-agents/deploy.py`
- Modify: `apps/logisly-agents/README.md`, `justfile`, `demo-data/logisly/README.md` (one line pointing to the agents)

**Interfaces:**
- Produces: `python deploy.py --agent uc2|uc3 [--project lv-playground-genai] [--region us-central1] [--staging-bucket gs://...]`:
  - It copies the needed demo data into `logisly_agents/data/`: `uc2/pricing/pricing.db`, `uc2/business_rules.yaml`, `uc3/templates/`, `uc3/github/`, `uc3/team/`.
  - It deploys with the Vertex AI SDK Agent Engines API (`vertexai.agent_engines.create` with `AdkApp`; confirm the current call in the installed SDK). The agent runs with model location `global`.
  - It prints the reasoning engine resource name.
  - It never registers in Gemini Enterprise by itself. The README gives the registration command with placeholders for the Gemini Enterprise app ID, which the user supplies.

- [ ] **Step 1:** Run `uv run adk web adk_apps --port 8077` in the background; `curl -s localhost:8077/list-apps` lists `uc2_purchasing` and `uc3_product`. Stop it.
- [ ] **Step 2:** Write `deploy.py` and the `just deploy agent=uc2` target. Run `just deploy agent=uc2` once. The deployed engine answers a smoke query ("Halo, apa yang bisa kamu bantu?") through `remote_app.stream_query`. Record the resource name in the README.
- [ ] **Step 3:** Write the README. It covers:
  - prerequisites: ADC, and a staging bucket
  - `just sync` / `just web` / `just deploy`
  - the demo script for each agent, in Bahasa Indonesia: which files to upload in which order, and the follow-up prompts
  - registering in Gemini Enterprise, with placeholders
  - the env vars table
  - a known risk: whether Gemini Enterprise passes uploaded xlsx and docx files through to the agent must be checked after registration. The fallback is to upload PDFs, or to paste the text.
- [ ] **Step 4:** Run `uv run pytest -q`, `uv run pytest -q -m live` and `uv run ruff check .`. Paste the summary lines into the commit body. Commit `feat(logisly-agents): Agent Engine deploy and run guide`.
