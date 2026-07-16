# CRM SLA Escalation + Audit Trail Implementation Plan (Short-Term Item 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a structured case audit trail (agent / from→to state / time / remark), an explicit WIP case state, and a secret-verified SLA-escalation webhook that Zendesk's 8h/48h time-based Automations call — recording an audit entry and firing an optional WhatsApp-to-PIC alert.

**Architecture:** An `AuditLogPort` (Firestore + in-memory adapters, mirroring the existing handoff store) stores append-only case events. A pure `CaseState` enum + `record_transition` helper writes an audit entry per status change. A new `POST /webhooks/zendesk-sla-escalation` (header-secret verified, like the existing Zendesk Support webhook) records the escalation and — when the payload carries a PIC WhatsApp number — alerts the PIC via the existing Twilio adapter. A read endpoint surfaces the trail. SLA *timing* stays in Zendesk (policies + Automations); we own the audit + alert.

**Tech Stack:** Python 3.12, `google-cloud-firestore`, `fastapi`, `httpx` (Twilio), `pytest`. Run backend commands from `apps/backend/`.

## Global Constraints

- Run from `apps/backend/`; venv `.venv/`.
- Gates before each commit: `.venv/bin/ruff format .` · `.venv/bin/ruff check . --fix` · `.venv/bin/mypy src/ --strict` · `.venv/bin/pytest src/`.
- Baselines (ZERO new): full suite currently **387** passing; mypy --strict **3** pre-existing (firestore_session_service.py:12, service.py:180, test_service.py:10); ruff **1** pre-existing (PLC0415 vertex_search.py).
- New webhook + endpoint additive; the SLA webhook is secret-verified (`sla_webhook_secret`); when the secret is unset it behaves like the existing Zendesk webhooks (no enforcement in dev).
- All new I/O behind `AuditLogPort` with an in-memory mock; state/transition logic pure and unit-tested.
- Firestore adapter mirrors `adapters/handoff_store.py` exactly (sync SDK dispatched via `asyncio.to_thread`; falls back to in-memory when unavailable).
- Conventional commits `feat(chat): …`. Branch `feature/crm-sla-escalation-audit`. Do not push.

## File structure

- `src/chatbot/features/chat/ports.py` — add `AuditEntry` dataclass + `AuditLogPort` Protocol.
- Create `src/chatbot/features/chat/case_state.py` — `CaseState` enum + `record_transition`.
- Create `src/chatbot/features/chat/adapters/audit_log.py` — `InMemoryAuditLog`, `FirestoreAuditLog`, `build_audit_log`.
- `src/chatbot/features/chat/router.py` — thread `audit_log` into `ChatRouter`/`build_chat_router`; add `sla_escalation_webhook` + `case_audit` methods + routes.
- `src/chatbot/platform/config.py` + `.env.example` — `firestore_audit_collection`, `sla_webhook_secret`.
- `src/chatbot/main.py` — construct + inject `audit_log`.
- Co-located `test_*.py`; docs in `docs/dashboards/sla-escalation-audit.md`.

---

### Task 1: AuditLogPort + adapters + config

**Files:**
- Modify: `src/chatbot/features/chat/ports.py`
- Create: `src/chatbot/features/chat/adapters/audit_log.py`
- Modify: `src/chatbot/platform/config.py`, `.env.example`
- Test: `src/chatbot/features/chat/adapters/test_audit_log.py`

**Interfaces:**
- Produces in `ports.py`: `@dataclass(frozen=True) AuditEntry{ticket_id:str, session_id:str, actor:str, from_state:str, to_state:str, at:str, remark:str}`; `AuditLogPort` Protocol with `async append(self, entry: AuditEntry) -> None` and `async list_for_ticket(self, ticket_id: str) -> list[AuditEntry]`.
- Produces in `audit_log.py`: `InMemoryAuditLog`, `FirestoreAuditLog(settings)`, `build_audit_log(settings) -> AuditLogPort`.
- Config: `firestore_audit_collection: str = "case_audit_log"`, `sla_webhook_secret: str = ""`.

- [ ] **Step 1: Write failing tests** — `adapters/test_audit_log.py`:

```python
import pytest

from chatbot.features.chat.adapters.audit_log import InMemoryAuditLog
from chatbot.features.chat.ports import AuditEntry


def _entry(ticket_id: str = "T1", to_state: str = "WIP") -> AuditEntry:
    return AuditEntry(
        ticket_id=ticket_id, session_id="whatsapp-+60", actor="agent_ali",
        from_state="OPEN", to_state=to_state, at="2026-07-16T09:00:00+00:00", remark="started",
    )


@pytest.mark.asyncio
async def test_append_then_list_for_ticket() -> None:
    log = InMemoryAuditLog()
    await log.append(_entry())
    await log.append(_entry(to_state="PENDING"))
    rows = await log.list_for_ticket("T1")
    assert [r.to_state for r in rows] == ["WIP", "PENDING"]


@pytest.mark.asyncio
async def test_list_isolates_by_ticket() -> None:
    log = InMemoryAuditLog()
    await log.append(_entry(ticket_id="T1"))
    await log.append(_entry(ticket_id="T2"))
    assert len(await log.list_for_ticket("T1")) == 1
```

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/pytest src/chatbot/features/chat/adapters/test_audit_log.py -q`
Expected: FAIL (`ImportError`).

- [ ] **Step 3: Implement `AuditEntry` + `AuditLogPort`** — in `ports.py`, add near the other dataclasses/ports (after `MetricsPort`). Import `dataclass` if not already imported (check the top of the file; add `from dataclasses import dataclass` if missing):

```python
@dataclass(frozen=True)
class AuditEntry:
    ticket_id: str
    session_id: str
    actor: str
    from_state: str
    to_state: str
    at: str
    remark: str


class AuditLogPort(Protocol):
    """Append-only case status audit trail (agent / from→to / time / remark)."""

    async def append(self, entry: AuditEntry) -> None: ...

    async def list_for_ticket(self, ticket_id: str) -> list[AuditEntry]: ...
```

- [ ] **Step 4: Implement `audit_log.py`** — mirror `adapters/handoff_store.py`:

```python
"""Case audit-trail stores: in-memory (tests/dev) + Firestore (persistent)."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

import structlog
from google.cloud import firestore

from chatbot.features.chat.ports import AuditEntry, AuditLogPort

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


class InMemoryAuditLog(AuditLogPort):
    def __init__(self) -> None:
        self._by_ticket: dict[str, list[AuditEntry]] = {}

    async def append(self, entry: AuditEntry) -> None:
        self._by_ticket.setdefault(entry.ticket_id, []).append(entry)

    async def list_for_ticket(self, ticket_id: str) -> list[AuditEntry]:
        return list(self._by_ticket.get(ticket_id, []))


class FirestoreAuditLog(AuditLogPort):
    """One document per event under `<collection>/<auto-id>`; queried by ticket_id."""

    def __init__(self, settings: Settings) -> None:
        self._client = firestore.Client(
            project=settings.firestore_project_id,
            database=settings.firestore_database_id,
        )
        self._collection_name = settings.firestore_audit_collection
        _log.info("firestore_audit_log_initialized", collection=self._collection_name)

    def _collection(self) -> Any:
        return self._client.collection(self._collection_name)

    async def append(self, entry: AuditEntry) -> None:
        def _write() -> None:
            self._collection().add(asdict(entry))

        await asyncio.to_thread(_write)

    async def list_for_ticket(self, ticket_id: str) -> list[AuditEntry]:
        def _query() -> list[AuditEntry]:
            docs = self._collection().where("ticket_id", "==", ticket_id).stream()
            rows = [AuditEntry(**doc.to_dict()) for doc in docs]
            return sorted(rows, key=lambda r: r.at)

        return await asyncio.to_thread(_query)


def build_audit_log(settings: Settings) -> AuditLogPort:
    """Firestore when handoff_store is firestore; else in-memory (dev/tests)."""
    if settings.handoff_store == "firestore":
        try:
            return FirestoreAuditLog(settings)
        except Exception as e:
            _log.warning("firestore_audit_log_init_failed_falling_back_to_memory", error=str(e))
    return InMemoryAuditLog()
```

- [ ] **Step 5: Config** — add to `Settings` (near `firestore_*`/`zendesk_*_webhook_secret` fields): `firestore_audit_collection: str = "case_audit_log"` and `sla_webhook_secret: str = ""`. Add commented `.env.example` entries.

- [ ] **Step 6: Run to verify pass**

Run: `.venv/bin/pytest src/chatbot/features/chat/adapters/test_audit_log.py -q`
Expected: PASS.

- [ ] **Step 7: Gates + commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict
git add src/chatbot/features/chat/ports.py src/chatbot/features/chat/adapters/audit_log.py src/chatbot/platform/config.py .env.example src/chatbot/features/chat/adapters/test_audit_log.py
git commit -m "feat(chat): add AuditLogPort with Firestore + in-memory adapters"
```

---

### Task 2: Case state + transition helper

**Files:**
- Create: `src/chatbot/features/chat/case_state.py`
- Test: `src/chatbot/features/chat/test_case_state.py`

**Interfaces:**
- Consumes: `AuditLogPort`, `AuditEntry`.
- Produces: `class CaseState(StrEnum)` with `NEW, OPEN, WIP, PENDING, SOLVED`. `async record_transition(audit: AuditLogPort, *, ticket_id: str, session_id: str, actor: str, from_state: CaseState, to_state: CaseState, at: str, remark: str = "") -> None` — appends one `AuditEntry` (states stored as their string values).

- [ ] **Step 1: Write failing test** — `test_case_state.py`:

```python
import pytest

from chatbot.features.chat.adapters.audit_log import InMemoryAuditLog
from chatbot.features.chat.case_state import CaseState, record_transition


def test_case_state_has_wip() -> None:
    assert CaseState.WIP == "WIP"
    assert {s.value for s in CaseState} == {"NEW", "OPEN", "WIP", "PENDING", "SOLVED"}


@pytest.mark.asyncio
async def test_record_transition_appends_one_entry() -> None:
    log = InMemoryAuditLog()
    await record_transition(
        log, ticket_id="T1", session_id="sim-1", actor="agent_ali",
        from_state=CaseState.OPEN, to_state=CaseState.WIP,
        at="2026-07-16T09:00:00+00:00", remark="picked up",
    )
    rows = await log.list_for_ticket("T1")
    assert len(rows) == 1
    assert rows[0].from_state == "OPEN" and rows[0].to_state == "WIP"
    assert rows[0].actor == "agent_ali" and rows[0].remark == "picked up"
```

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_case_state.py -q`
Expected: FAIL (`ImportError`).

- [ ] **Step 3: Implement `case_state.py`**

```python
"""Case status model + audit-recording transition helper."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from chatbot.features.chat.ports import AuditEntry

if TYPE_CHECKING:
    from chatbot.features.chat.ports import AuditLogPort


class CaseState(StrEnum):
    NEW = "NEW"
    OPEN = "OPEN"
    WIP = "WIP"
    PENDING = "PENDING"
    SOLVED = "SOLVED"


async def record_transition(
    audit: AuditLogPort,
    *,
    ticket_id: str,
    session_id: str,
    actor: str,
    from_state: CaseState,
    to_state: CaseState,
    at: str,
    remark: str = "",
) -> None:
    await audit.append(
        AuditEntry(
            ticket_id=ticket_id,
            session_id=session_id,
            actor=actor,
            from_state=from_state.value,
            to_state=to_state.value,
            at=at,
            remark=remark,
        )
    )
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_case_state.py -q`
Expected: PASS.

- [ ] **Step 5: Gates + commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict
git add src/chatbot/features/chat/case_state.py src/chatbot/features/chat/test_case_state.py
git commit -m "feat(chat): add CaseState (incl WIP) + audit-recording record_transition"
```

---

### Task 3: SLA escalation webhook + WA-to-PIC alert

**Files:**
- Modify: `src/chatbot/features/chat/router.py` (`ChatRouter.__init__`, `build_chat_router`, new method + route)
- Test: `src/chatbot/features/chat/test_sla_webhook.py`

**Interfaces:**
- Consumes: `AuditLogPort`, `record_transition`, `CaseState`, the existing `self._twilio_adapter`.
- Produces: `ChatRouter.__init__` gains `audit_log: AuditLogPort | None = None`; `build_chat_router(...)` gains an `audit_log` param threaded through. New `POST /webhooks/zendesk-sla-escalation` handler `sla_escalation_webhook(payload, x_proton_webhook_secret)`: verifies `self.orchestrator._settings.sla_webhook_secret` (same pattern as `zendesk_support_webhook`); records an audit transition (to `WIP` for level `escalate`, to `PENDING` for level `alarm` — actor `"sla-automation"`); if `payload.pic_whatsapp` is set and `self._twilio_adapter` present, sends a WA alert. Returns `{"status": "recorded"}`.

- [ ] **Step 1: Write failing test** — `test_sla_webhook.py` (uses FastAPI TestClient over just the router; construct `ChatRouter` with a stub orchestrator exposing `_settings`, an `InMemoryAuditLog`, and a fake twilio adapter). Mirror the construction used by existing router tests in this package (grep `ChatRouter(` in `test_*` to copy their stub setup):

```python
# Pseudocode shape — adapt to the existing router-test harness in this package:
# 1. build a ChatRouter with InMemoryAuditLog() + a fake twilio adapter recording sends
# 2. POST /webhooks/zendesk-sla-escalation with a valid secret + payload
#    {ticket_id, session_id, level: "escalate", pic_whatsapp: "+60123"}
# 3. assert 200 {"status":"recorded"}; audit.list_for_ticket has one entry to_state WIP
# 4. assert the fake twilio adapter received one send to whatsapp:+60123
# 5. POST with a wrong secret → 401
```

Write it concretely against the existing harness (find it first: `grep -rn "ChatRouter(" src/chatbot/features/chat/test_*.py`). The three assertions (records audit / sends WA / rejects bad secret) are required.

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_sla_webhook.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement** — in `router.py`:
  - Add a Pydantic model near the other request models: `class SlaEscalationPayload(BaseModel): ticket_id: str; session_id: str = ""; level: str = "escalate"; pic_whatsapp: str = ""; remark: str = ""`.
  - Add `audit_log: AuditLogPort | None = None` to `ChatRouter.__init__`, store `self._audit_log = audit_log`; register the route in `__init__`: `self.router.add_api_route("/webhooks/zendesk-sla-escalation", self.sla_escalation_webhook, methods=["POST"])`.
  - Add the handler method:

```python
    async def sla_escalation_webhook(
        self,
        payload: SlaEscalationPayload,
        x_proton_webhook_secret: str | None = Header(default=None),
    ) -> dict[str, str]:
        expected = self.orchestrator._settings.sla_webhook_secret
        if expected and (not x_proton_webhook_secret or x_proton_webhook_secret != expected):
            _log.warning("sla_escalation_webhook_signature_invalid")
            raise HTTPException(status_code=401, detail="Invalid signature")
        if self._audit_log is None:
            raise HTTPException(status_code=503, detail="Audit log not configured")
        to_state = CaseState.WIP if payload.level == "escalate" else CaseState.PENDING
        await record_transition(
            self._audit_log,
            ticket_id=payload.ticket_id,
            session_id=payload.session_id,
            actor="sla-automation",
            from_state=CaseState.OPEN,
            to_state=to_state,
            at=datetime.now(UTC).isoformat(),
            remark=payload.remark or f"SLA {payload.level}",
        )
        if payload.pic_whatsapp and self._twilio_adapter is not None:
            await self._twilio_adapter.send_message(
                conversation_id="whatsapp:" + payload.pic_whatsapp.lstrip("whatsapp:"),
                text=f"⚠️ SLA {payload.level} on case {payload.ticket_id}. Please action.",
            )
        return {"status": "recorded"}
```

  - Add imports at the top of `router.py`: `from datetime import UTC, datetime` (if not present), `from chatbot.features.chat.case_state import CaseState, record_transition`, and `AuditLogPort` from ports.
  - Thread `audit_log` through `build_chat_router` (add the param and pass it into `ChatRouter(...)`).

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_sla_webhook.py -q`
Expected: PASS.

- [ ] **Step 5: Gates + commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict
git add src/chatbot/features/chat/router.py src/chatbot/features/chat/test_sla_webhook.py
git commit -m "feat(chat): add SLA-escalation webhook with audit + WA-to-PIC alert"
```

---

### Task 4: Audit read endpoint

**Files:**
- Modify: `src/chatbot/features/chat/router.py`
- Test: `src/chatbot/features/chat/test_case_audit_endpoint.py`

**Interfaces:**
- Produces: `GET /cases/{ticket_id}/audit` → `{"ticket_id": ..., "audit": [asdict(AuditEntry), ...]}` via `self._audit_log.list_for_ticket`. 503 when audit log unconfigured.

- [ ] **Step 1: Write failing test** — `test_case_audit_endpoint.py` (same harness as Task 3; append two entries via the router's audit log, GET the endpoint, assert both returned in order).

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_case_audit_endpoint.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement** — register `self.router.add_api_route("/cases/{ticket_id}/audit", self.case_audit, methods=["GET"])` in `__init__`, and add:

```python
    async def case_audit(self, ticket_id: str) -> dict[str, Any]:
        if self._audit_log is None:
            raise HTTPException(status_code=503, detail="Audit log not configured")
        rows = await self._audit_log.list_for_ticket(ticket_id)
        return {"ticket_id": ticket_id, "audit": [asdict(r) for r in rows]}
```

Add `from dataclasses import asdict` if not already imported.

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_case_audit_endpoint.py -q`
Expected: PASS.

- [ ] **Step 5: Gates + commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict
git add src/chatbot/features/chat/router.py src/chatbot/features/chat/test_case_audit_endpoint.py
git commit -m "feat(chat): add GET /cases/{ticket_id}/audit read endpoint"
```

---

### Task 5: Wiring + full suite + docs

**Files:**
- Modify: `src/chatbot/main.py`
- Create: `docs/dashboards/sla-escalation-audit.md`
- Test: existing chat-router wiring/integration test (extend)

**Interfaces:**
- Consumes: `build_audit_log`, threaded into `build_chat_router`.

- [ ] **Step 1: Wire in `main.py`** — where `build_chat_router(...)` is called, construct `audit_log = build_audit_log(settings)` and pass `audit_log=audit_log`. Find the call site (`grep -n build_chat_router src/chatbot/main.py`) and match the existing dependency-passing style. Add the import `from chatbot.features.chat.adapters.audit_log import build_audit_log`.

- [ ] **Step 2: Boot + smoke** — extend the app-boot integration test: POST `/webhooks/zendesk-sla-escalation` (no secret configured in test settings) with a payload → 200 `{"status":"recorded"}`, then GET `/cases/{ticket_id}/audit` → the recorded entry. Reuse the existing boot harness.

Run: `.venv/bin/pytest src/chatbot -k "wiring or main or sla or audit" -q`
Expected: PASS.

- [ ] **Step 3: Full suite + gates**

Run: `.venv/bin/pytest src/ -q` → all green.
Run: `.venv/bin/ruff format . && .venv/bin/ruff check . && .venv/bin/mypy src/ --strict` → 1 ruff / 3 mypy baseline only.

- [ ] **Step 4: Docs** — `docs/dashboards/sla-escalation-audit.md`: document the audit trail (fields, Firestore collection, in-memory fallback), `POST /webhooks/zendesk-sla-escalation` (payload + `sla_webhook_secret` header) and the Zendesk config (SLA policy + two time-based Automations at 8h `escalate` / 48h `alarm` calling the webhook), `GET /cases/{ticket_id}/audit`, WA-to-PIC alert (optional `pic_whatsapp`), and the CaseState/WIP model. Note the deferred follow-up: mirroring the audit trail to BigQuery `case_audit_events` for lifecycle reporting (Item F).

- [ ] **Step 5: Commit**

```bash
git add src/chatbot/main.py docs/dashboards/sla-escalation-audit.md src/chatbot/features/chat/
git commit -m "feat(chat): wire audit log into chat router; SLA/audit docs"
```

---

## Self-review

**Spec coverage (Item 3 of the design):**
- `AuditLogPort` + Firestore adapter + mock → Task 1. ✓
- Explicit WIP case state → Task 2 (`CaseState.WIP`). ✓
- Structured audit (agent/from→to/time/remark) → Tasks 1-2 (`AuditEntry` + `record_transition`). ✓
- SLA-escalation webhook (secret-verified) → Task 3. ✓
- Optional WA-to-PIC alert → Task 3. ✓
- Audit surfacing → Task 4 (read endpoint). ✓
- Zendesk SLA policy + 8h/48h Automation config → Task 5 docs. ✓
- Wiring → Task 5. ✓

**Placeholder scan:** Tasks 3-4 tests say "adapt to the existing router-test harness / grep `ChatRouter(`" because the exact stub-orchestrator construction lives in this package's existing tests — the required assertions are concrete. No TBD/TODO.

**Type consistency:** `AuditEntry` field names identical across ports, adapters, `record_transition`, and the read endpoint's `asdict`. `CaseState` values used consistently. `audit_log` param name consistent across `ChatRouter.__init__`, `build_chat_router`, and `main.py`.

## Deferred to later plans / follow-ups
- **BigQuery mirror** of the audit trail (`case_audit_events` + `v_case_audit`) for lifecycle reporting — documented follow-up, not in this increment.
- Wiring WIP into the live AI conversation flow (agents transition state in Zendesk; we record via webhook) — out of scope.
- Item 4 (Zendesk-native routing config), Item 5 (agent-assist FAQ).
