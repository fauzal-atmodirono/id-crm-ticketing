# Phase 2 — Escalation / PIC Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `proton-conversational-ai` so every escalated case auto-routes to the correct PIC (Person-In-Charge) via dept→PIC config, emails+CCs them with the case summary, sends a WhatsApp alert at escalation time (not only on SLA breach), re-alerts a level-2 target when the breach is unacknowledged, and surfaces `TEMP_CLOSED` + all case states in the Chatwoot right panel.

**Architecture:** All five items (12–16) extend the existing ports-and-adapters backend inside `proton-conversational-ai/apps/backend/src/chatbot/`. A new `PicRegistry` module holds the dept→PIC table (JSON env var, hot-reloadable); it is injected into `ChatwootAdapter` and the SLA engine through constructor parameters, keeping both testable. The email sender is refactored to split a reusable `SmtpEmailSender` (plain SMTP send with To/CC/attachments) out of `SmtpEmailReport` (report-specific scheduling). Chatwoot custom attributes carry the live `case_state` value so agents see it in the right panel without any fork change.

**Tech Stack:** Python 3.12, FastAPI, httpx, Pydantic-settings v2, APScheduler 3.x, Twilio REST, structlog, pytest 8 / pytest-asyncio (asyncio_mode = "auto"), `unittest.mock` (AsyncMock / monkeypatch). Tests co-locate with sources under `src/` per `testpaths = ["src"]`.

## Global Constraints

- Python ≥ 3.12; all async code uses `async def`; `asyncio_mode = "auto"` so no `@pytest.mark.asyncio` decorator needed unless the file overrides `asyncio_mode`.
- Settings loaded via `pydantic_settings.BaseSettings`; new fields added to `chatbot/platform/config.py`; tests construct `Settings(_env_file=None, **overrides)` to stay `.env`-isolated.
- Label conventions: `category_*`, `subcat_*`, `division_*`, `dept_*`, `sla_<int>`, `pic_*` — lower-snake, no spaces (see `_dimension_labels` in `chatwoot.py`).
- SLA thresholds: `sla_response_hours=8`, `sla_resolution_hours=48` — exact values, never hard-coded in new code (always read from `settings`).
- Zammad group and owner are set by passing `group=<string>` to `ZammadClient.create_ticket`; owner assignment uses the Zammad user API (search by email, then PATCH ticket).
- Errors from PIC lookup, email send, and WA alert must never break the caller turn — swallow with `structlog` `_log.warning(...)`, never reraise.
- All HTTP in tests uses `monkeypatch.setattr` on `httpx.AsyncClient` or direct fake-client injection (no real network calls).
- SMTP in tests uses an injectable `smtp_factory` callable (already the pattern in `SmtpEmailReport`).
- File paths are relative to `apps/backend/src/chatbot/`.

---

## File Structure

**New files:**

- `features/chat/pic_registry.py` — `PicEntry` dataclass + `PicRegistry` (loads from JSON env var); single responsibility: dept-key → PIC record lookup.
- `features/chat/escalation_notifier.py` — `EscalationNotifier` orchestrates the three side-effects on escalation: email CC, WhatsApp alert, Chatwoot `case_state` attribute write. Called from `ChatwootAdapter._escalate_to_zammad` (renamed `_do_escalation_sideeffects`).
- `features/metrics/email_sender.py` — `SmtpEmailSender` (bare To/CC send without report-scheduling logic); replaces the duplicated SMTP logic in `SmtpEmailReport`.

**Modified files:**

- `platform/config.py` — add `pic_map_json`, `escalation_level2_whatsapp`, `escalation_tier2_hours`, `escalation_email_enabled`, `escalation_cc_pic`.
- `features/chat/case_state.py` — add `TEMP_CLOSED` to `CaseState`; add `CHATWOOT_CASE_STATE_ATTR = "case_state"` constant.
- `features/chat/adapters/chatwoot.py` — `_escalate_to_zammad` calls `EscalationNotifier`; `create_ticket` and `open_handoff` write `case_state` custom attribute after escalation.
- `features/chat/adapters/zammad.py` — `create_ticket` gains `owner_email: str | None = None`; on success, if `owner_email` set, PATCH the ticket to assign the owner by Zammad user lookup.
- `features/chat/sla.py` — `scan_conversations` gains a `level2_alert` callback; `_fire` calls `level2_alert` when `UNRESOLVED_BREACH` fires AND a configurable tier-2 timer has elapsed.
- `features/metrics/email_port.py` — refactor `SmtpEmailReport.send_report` to delegate to `SmtpEmailSender`; keep the public interface unchanged.
- `features/chat/router.py` — `_log_status_transition` writes `case_state` custom attribute to Chatwoot on every status change; maps `TEMP_CLOSED` from Chatwoot `snoozed`→reopened-then-closed pattern (if `TEMP_CLOSED` ever lands as a webhook status, else set it via the SLA webhook endpoint).

---

## Task 1: `PicRegistry` — dept→PIC lookup table

**Files:**
- Create: `features/chat/pic_registry.py`
- Create: `features/chat/test_pic_registry.py`
- Modify: `platform/config.py`

**Interfaces:**
- Produces:
  - `PicEntry(pic_name: str, pic_email: str, pic_whatsapp: str, zammad_group: str, chatwoot_team_id: int | None)`
  - `PicRegistry(settings: Settings)` with method `lookup(department: str) -> PicEntry | None`
  - `build_pic_registry(settings: Settings) -> PicRegistry`

- [ ] **Step 1: Write the failing test**

```python
# features/chat/test_pic_registry.py
from __future__ import annotations
import json
from chatbot.features.chat.pic_registry import PicEntry, PicRegistry, build_pic_registry
from chatbot.platform.config import Settings


def _settings(pic_map: dict | None = None) -> Settings:
    raw = json.dumps(pic_map) if pic_map else ""
    return Settings(_env_file=None, pic_map_json=raw)


def test_lookup_returns_matching_entry() -> None:
    s = _settings({
        "apps": {
            "pic_name": "Alice Tan",
            "pic_email": "alice@proton.my",
            "pic_whatsapp": "+60123456789",
            "zammad_group": "Apps-Support",
            "chatwoot_team_id": 3,
        }
    })
    reg = build_pic_registry(s)
    entry = reg.lookup("apps")
    assert entry is not None
    assert entry.pic_name == "Alice Tan"
    assert entry.pic_email == "alice@proton.my"
    assert entry.pic_whatsapp == "+60123456789"
    assert entry.zammad_group == "Apps-Support"
    assert entry.chatwoot_team_id == 3


def test_lookup_normalises_department_key() -> None:
    s = _settings({"apps": {"pic_name": "A", "pic_email": "a@b.my",
                             "pic_whatsapp": "+601", "zammad_group": "G"}})
    reg = build_pic_registry(s)
    # dept label from Chatwoot is "dept_apps" — caller strips prefix; test raw key
    assert reg.lookup("Apps") is not None   # case insensitive
    assert reg.lookup("APPS") is not None


def test_lookup_returns_none_for_unknown_dept() -> None:
    s = _settings({"apps": {"pic_name": "A", "pic_email": "a@b.my",
                             "pic_whatsapp": "+601", "zammad_group": "G"}})
    reg = build_pic_registry(s)
    assert reg.lookup("charging") is None


def test_empty_pic_map_json_returns_none() -> None:
    s = Settings(_env_file=None, pic_map_json="")
    reg = build_pic_registry(s)
    assert reg.lookup("apps") is None


def test_malformed_json_returns_none_not_crash() -> None:
    s = Settings(_env_file=None, pic_map_json="{bad json")
    reg = build_pic_registry(s)
    assert reg.lookup("apps") is None


def test_missing_optional_chatwoot_team_id_defaults_to_none() -> None:
    s = _settings({"apps": {"pic_name": "A", "pic_email": "a@b.my",
                             "pic_whatsapp": "+601", "zammad_group": "G"}})
    reg = build_pic_registry(s)
    entry = reg.lookup("apps")
    assert entry is not None
    assert entry.chatwoot_team_id is None
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
cd /path/to/proton-conversational-ai/apps/backend
pytest src/chatbot/features/chat/test_pic_registry.py -v
```

Expected: `ModuleNotFoundError: No module named 'chatbot.features.chat.pic_registry'`

- [ ] **Step 3: Add `pic_map_json` to Settings**

In `platform/config.py`, add inside `class Settings(BaseSettings):`:

```python
    # Phase 2 — dept→PIC mapping. JSON object keyed by department slug
    # (matches the dept_<x> label key, e.g. "apps", "sales"). Each value:
    # {pic_name, pic_email, pic_whatsapp, zammad_group, chatwoot_team_id?}.
    # Empty string disables PIC routing (no lookup attempted).
    pic_map_json: str = ""

    # Phase 2 — escalation notifications
    escalation_email_enabled: bool = False
    escalation_cc_pic: bool = True
    escalation_level2_whatsapp: str = ""   # E.164, e.g. "+60112345678"
    escalation_tier2_hours: float = 4.0   # hours after first breach before level-2 alert
```

- [ ] **Step 4: Implement `PicRegistry`**

Create `features/chat/pic_registry.py`:

```python
"""dept→PIC lookup registry (Phase 2, item 12).

Loaded once from the PIC_MAP_JSON environment variable (a JSON object keyed by
department slug). Lookup normalises the key to lowercase so callers do not need
to worry about casing.
"""
from __future__ import annotations

import json
import structlog
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class PicEntry:
    pic_name: str
    pic_email: str
    pic_whatsapp: str
    zammad_group: str
    chatwoot_team_id: int | None = None


class PicRegistry:
    """In-memory dept→PIC table loaded from JSON config.

    Lookup is O(1) dict access after a one-time parse at construction.
    Keys are lower-cased at both load and lookup time so label values like
    ``dept_Apps`` and ``dept_apps`` resolve identically.
    """

    def __init__(self, table: dict[str, PicEntry]) -> None:
        self._table = table

    def lookup(self, department: str) -> PicEntry | None:
        """Return the PicEntry for *department* (case-insensitive) or None."""
        return self._table.get(department.lower())


def build_pic_registry(settings: Settings) -> PicRegistry:
    """Parse PIC_MAP_JSON and return a PicRegistry.

    Returns an empty registry (all lookups return None) when the JSON is
    absent, empty, or malformed — so a misconfigured map never crashes the app.
    """
    raw = (settings.pic_map_json or "").strip()
    if not raw:
        return PicRegistry({})
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        _log.warning("pic_map_json_parse_failed", error=str(exc))
        return PicRegistry({})
    if not isinstance(data, dict):
        _log.warning("pic_map_json_not_a_dict", got=type(data).__name__)
        return PicRegistry({})
    table: dict[str, PicEntry] = {}
    for key, val in data.items():
        if not isinstance(val, dict):
            continue
        try:
            table[key.lower()] = PicEntry(
                pic_name=str(val["pic_name"]),
                pic_email=str(val["pic_email"]),
                pic_whatsapp=str(val["pic_whatsapp"]),
                zammad_group=str(val["zammad_group"]),
                chatwoot_team_id=int(val["chatwoot_team_id"])
                if val.get("chatwoot_team_id") is not None
                else None,
            )
        except (KeyError, TypeError, ValueError) as exc:
            _log.warning("pic_map_entry_invalid", dept=key, error=str(exc))
    return PicRegistry(table)
```

- [ ] **Step 5: Run tests — all should pass**

```bash
pytest src/chatbot/features/chat/test_pic_registry.py -v
```

Expected: 6 PASSED

- [ ] **Step 6: Commit**

```bash
git add src/chatbot/features/chat/pic_registry.py \
        src/chatbot/features/chat/test_pic_registry.py \
        src/chatbot/platform/config.py
git commit -m "feat(phase2): add PicRegistry and pic_map_json config (item 12)"
```

---

## Task 2: `SmtpEmailSender` — reusable bare SMTP sender

**Files:**
- Create: `features/metrics/email_sender.py`
- Create: `features/metrics/test_email_sender.py`
- Modify: `features/metrics/email_port.py`

**Interfaces:**
- Consumes: `Settings.smtp_host`, `Settings.smtp_port`, `Settings.smtp_user`, `Settings.smtp_password`, `Settings.smtp_from`
- Produces:
  - `SmtpEmailSender(settings: Settings, *, smtp_factory: Callable | None = None)` with method `send(to: list[str], cc: list[str], subject: str, body: str, attachments: list[Attachment]) -> None`
  - `Attachment = tuple[str, bytes, str]` (filename, content, mimetype) — same type alias as in `email_port.py`

- [ ] **Step 1: Write the failing test**

```python
# features/metrics/test_email_sender.py
from __future__ import annotations

from email.message import EmailMessage
from typing import Any
from unittest.mock import MagicMock

from chatbot.features.metrics.email_sender import SmtpEmailSender
from chatbot.platform.config import Settings


def _settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "smtp_user": "user@example.com",
        "smtp_password": "secret",
        "smtp_from": "noreply@proton.my",
    }
    base.update(kw)
    return Settings(_env_file=None, **base)


def _capture_smtp() -> tuple[list[EmailMessage], MagicMock]:
    sent: list[EmailMessage] = []

    class _FakeSMTP:
        def __init__(self, host: str, port: int) -> None:
            pass
        def __enter__(self) -> _FakeSMTP:
            return self
        def __exit__(self, *_: Any) -> None:
            pass
        def starttls(self) -> None:
            pass
        def login(self, user: str, pw: str) -> None:
            pass
        def send_message(self, msg: EmailMessage) -> None:
            sent.append(msg)

    return sent, _FakeSMTP  # type: ignore[return-value]


def test_send_sets_from_to_and_subject() -> None:
    sent, factory = _capture_smtp()
    sender = SmtpEmailSender(_settings(), smtp_factory=factory)
    sender.send(
        to=["alice@proton.my"],
        cc=[],
        subject="Case #42 escalated",
        body="Please review.",
        attachments=[],
    )
    assert len(sent) == 1
    assert sent[0]["From"] == "noreply@proton.my"
    assert "alice@proton.my" in sent[0]["To"]
    assert sent[0]["Subject"] == "Case #42 escalated"


def test_send_adds_cc_header() -> None:
    sent, factory = _capture_smtp()
    sender = SmtpEmailSender(_settings(), smtp_factory=factory)
    sender.send(
        to=["alice@proton.my"],
        cc=["manager@proton.my"],
        subject="s",
        body="b",
        attachments=[],
    )
    assert "manager@proton.my" in sent[0]["Cc"]


def test_send_attaches_files() -> None:
    sent, factory = _capture_smtp()
    sender = SmtpEmailSender(_settings(), smtp_factory=factory)
    sender.send(
        to=["a@b.my"],
        cc=[],
        subject="s",
        body="b",
        attachments=[("report.pdf", b"%PDF", "application/pdf")],
    )
    payloads = list(sent[0].iter_attachments())
    assert len(payloads) == 1
    assert payloads[0].get_filename() == "report.pdf"


def test_send_no_op_when_to_is_empty() -> None:
    sent, factory = _capture_smtp()
    sender = SmtpEmailSender(_settings(), smtp_factory=factory)
    sender.send(to=[], cc=[], subject="s", body="b", attachments=[])
    assert sent == []


def test_send_no_op_when_smtp_host_is_empty() -> None:
    sent, factory = _capture_smtp()
    sender = SmtpEmailSender(_settings(smtp_host=""), smtp_factory=factory)
    sender.send(to=["a@b.my"], cc=[], subject="s", body="b", attachments=[])
    assert sent == []
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
pytest src/chatbot/features/metrics/test_email_sender.py -v
```

Expected: `ModuleNotFoundError: No module named 'chatbot.features.metrics.email_sender'`

- [ ] **Step 3: Implement `SmtpEmailSender`**

Create `features/metrics/email_sender.py`:

```python
"""Reusable bare SMTP email sender (Phase 2, item 13).

Splits the transport concern out of SmtpEmailReport so escalation emails
(To + CC + case summary) and scheduled metric reports can both use the same
underlying send path.
"""
from __future__ import annotations

from collections.abc import Callable
from email.message import EmailMessage
from smtplib import SMTP
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

Attachment = tuple[str, bytes, str]  # (filename, content, mimetype)


class SmtpEmailSender:
    """Sends a single email (To + CC + optional attachments) via STARTTLS SMTP.

    No-op when ``smtp_host`` is empty or ``to`` is empty — callers must never
    need to guard these conditions themselves.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        smtp_factory: Callable[[str, int], Any] | None = None,
    ) -> None:
        self._s = settings
        self._smtp = smtp_factory or SMTP

    def send(
        self,
        to: list[str],
        cc: list[str],
        subject: str,
        body: str,
        attachments: list[Attachment],
    ) -> None:
        """Send an email synchronously. Swallows and logs all errors."""
        if not to or not self._s.smtp_host:
            return
        msg = EmailMessage()
        msg["From"] = self._s.smtp_from
        msg["To"] = ", ".join(to)
        if cc:
            msg["Cc"] = ", ".join(cc)
        msg["Subject"] = subject
        msg.set_content(body)
        for filename, content, mimetype in attachments:
            maintype, _, subtype = mimetype.partition("/")
            msg.add_attachment(
                content,
                maintype=maintype or "application",
                subtype=subtype or "octet-stream",
                filename=filename,
            )
        try:
            with self._smtp(self._s.smtp_host, self._s.smtp_port) as smtp:
                smtp.starttls()
                if self._s.smtp_user:
                    smtp.login(self._s.smtp_user, self._s.smtp_password)
                smtp.send_message(msg)
            _log.info("email_sent", to_count=len(to), cc_count=len(cc), subject=subject)
        except Exception as exc:
            _log.warning("email_send_failed", subject=subject, error=str(exc))
```

- [ ] **Step 4: Refactor `SmtpEmailReport` to delegate to `SmtpEmailSender`**

Open `features/metrics/email_port.py`. Replace the `SmtpEmailReport.send_report` body so it delegates to `SmtpEmailSender`:

```python
# features/metrics/email_port.py  (modified section only — keep all imports + Protocol + MockEmailReport)

from chatbot.features.metrics.email_sender import Attachment, SmtpEmailSender  # add this import


class SmtpEmailReport:
    def __init__(
        self, settings: Settings, *, smtp_factory: Callable[[str, int], Any] | None = None
    ) -> None:
        self._s = settings
        self._sender = SmtpEmailSender(settings, smtp_factory=smtp_factory)

    def send_report(
        self, recipients: list[str], subject: str, body: str, attachments: list[Attachment]
    ) -> None:
        self._sender.send(to=recipients, cc=[], subject=subject, body=body, attachments=attachments)
        _log.info("metrics_report_email_sent", recipients=len(recipients))
```

- [ ] **Step 5: Run tests**

```bash
pytest src/chatbot/features/metrics/test_email_sender.py \
       src/chatbot/features/metrics/ -v --tb=short
```

Expected: all email_sender tests PASS; existing email_port tests still PASS.

- [ ] **Step 6: Commit**

```bash
git add src/chatbot/features/metrics/email_sender.py \
        src/chatbot/features/metrics/test_email_sender.py \
        src/chatbot/features/metrics/email_port.py
git commit -m "feat(phase2): add SmtpEmailSender; refactor SmtpEmailReport to delegate (item 13)"
```

---

## Task 3: `EscalationNotifier` — email CC + WA alert + case_state attribute

**Files:**
- Create: `features/chat/escalation_notifier.py`
- Create: `features/chat/test_escalation_notifier.py`

**Interfaces:**
- Consumes: `PicEntry` (Task 1), `SmtpEmailSender` (Task 2), `TwilioChannelAdapter` (existing), `ChatwootAdapter._request` (for custom attribute write), `Settings`
- Produces:
  - `EscalationNotifier(settings, pic_registry, email_sender, twilio_adapter, chatwoot_request)`
  - `async notify(conv_id: str, ticket_id: str, title: str, body: str, department: str | None, zammad_ticket_number: str | None) -> PicEntry | None`

- [ ] **Step 1: Write the failing tests**

```python
# features/chat/test_escalation_notifier.py
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from chatbot.features.chat.escalation_notifier import EscalationNotifier
from chatbot.features.chat.pic_registry import PicEntry, PicRegistry
from chatbot.platform.config import Settings


def _settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {
        "escalation_email_enabled": True,
        "escalation_cc_pic": True,
        "smtp_host": "smtp.example.com",
        "smtp_from": "noreply@proton.my",
        "twilio_account_sid": "AC1",
        "twilio_auth_token": "tok",
        "twilio_whatsapp_number": "whatsapp:+60100000000",
    }
    base.update(kw)
    return Settings(_env_file=None, **base)


_APPS_PIC = PicEntry(
    pic_name="Alice Tan",
    pic_email="alice@proton.my",
    pic_whatsapp="+60123456789",
    zammad_group="Apps-Support",
    chatwoot_team_id=3,
)


def _registry(pic: PicEntry | None = _APPS_PIC) -> PicRegistry:
    if pic is None:
        return PicRegistry({})
    return PicRegistry({"apps": pic})


async def test_notify_sends_email_and_wa_when_dept_matched() -> None:
    sent_emails: list[dict[str, Any]] = []
    sent_wa: list[tuple[str, str]] = []
    cw_calls: list[tuple[str, str, Any]] = []

    class _FakeEmailSender:
        def send(self, to: list[str], cc: list[str], subject: str,
                 body: str, attachments: list) -> None:
            sent_emails.append({"to": to, "cc": cc, "subject": subject, "body": body})

    async def _fake_cw(method: str, path: str, payload: Any = None) -> dict:
        cw_calls.append((method, path, payload))
        return {}

    class _FakeTwilio:
        async def send_message(self, conversation_id: str, text: str) -> None:
            sent_wa.append((conversation_id, text))

    notifier = EscalationNotifier(
        settings=_settings(),
        pic_registry=_registry(),
        email_sender=_FakeEmailSender(),  # type: ignore[arg-type]
        twilio_adapter=_FakeTwilio(),  # type: ignore[arg-type]
        chatwoot_request=_fake_cw,
    )

    pic = await notifier.notify(
        conv_id="42",
        ticket_id="ZAM-777",
        title="Battery fault",
        body="Customer has a dead battery on X50.",
        department="apps",
        zammad_ticket_number="12034",
    )

    assert pic is not None
    assert pic.pic_name == "Alice Tan"

    # Email: To = PIC email; body contains title + ticket number
    assert len(sent_emails) == 1
    em = sent_emails[0]
    assert em["to"] == ["alice@proton.my"]
    assert "Battery fault" in em["body"]
    assert "12034" in em["body"]

    # WhatsApp alert sent to PIC
    assert len(sent_wa) == 1
    wa_to, wa_text = sent_wa[0]
    assert "alice" in wa_to.lower() or "+60123456789" in wa_to
    assert "Battery fault" in wa_text or "42" in wa_text

    # Chatwoot custom attribute "case_state" set
    attr_calls = [(m, p, pl) for m, p, pl in cw_calls
                  if "/custom_attributes" in p]
    assert len(attr_calls) >= 1
    attrs = attr_calls[0][2]["custom_attributes"]
    assert "case_state" in attrs


async def test_notify_no_op_when_department_not_in_registry() -> None:
    sent_emails: list[Any] = []

    class _FakeEmailSender:
        def send(self, **_: Any) -> None:
            sent_emails.append(True)

    notifier = EscalationNotifier(
        settings=_settings(),
        pic_registry=_registry(None),
        email_sender=_FakeEmailSender(),  # type: ignore[arg-type]
        twilio_adapter=None,
        chatwoot_request=AsyncMock(return_value={}),
    )
    pic = await notifier.notify(
        conv_id="1", ticket_id="Z1", title="t", body="b",
        department="charging", zammad_ticket_number=None,
    )
    assert pic is None
    assert sent_emails == []


async def test_notify_skips_email_when_disabled() -> None:
    sent_emails: list[Any] = []

    class _FakeEmailSender:
        def send(self, **_: Any) -> None:
            sent_emails.append(True)

    notifier = EscalationNotifier(
        settings=_settings(escalation_email_enabled=False),
        pic_registry=_registry(),
        email_sender=_FakeEmailSender(),  # type: ignore[arg-type]
        twilio_adapter=None,
        chatwoot_request=AsyncMock(return_value={}),
    )
    await notifier.notify(conv_id="1", ticket_id="Z1", title="t", body="b",
                          department="apps", zammad_ticket_number=None)
    assert sent_emails == []


async def test_notify_skips_wa_when_no_twilio_adapter() -> None:
    sent_emails: list[Any] = []

    class _FakeEmailSender:
        def send(self, to: list[str], **_: Any) -> None:
            sent_emails.append(to)

    notifier = EscalationNotifier(
        settings=_settings(),
        pic_registry=_registry(),
        email_sender=_FakeEmailSender(),  # type: ignore[arg-type]
        twilio_adapter=None,  # no Twilio
        chatwoot_request=AsyncMock(return_value={}),
    )
    pic = await notifier.notify(conv_id="1", ticket_id="Z1", title="t", body="b",
                                department="apps", zammad_ticket_number="1")
    # email still sent; no WA error
    assert pic is not None
    assert len(sent_emails) == 1
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
pytest src/chatbot/features/chat/test_escalation_notifier.py -v
```

Expected: `ModuleNotFoundError: No module named 'chatbot.features.chat.escalation_notifier'`

- [ ] **Step 3: Implement `EscalationNotifier`**

Create `features/chat/escalation_notifier.py`:

```python
"""EscalationNotifier — side-effects on case escalation (Phase 2, items 13+14).

Orchestrates:
1. Email the PIC (To) + optional CC; body = case summary + Zammad link.
2. WhatsApp alert to the PIC's registered number via Twilio.
3. Write `case_state=WIP` to the Chatwoot conversation custom attributes.

All three are best-effort: a failure in any step logs a warning and does NOT
propagate — the escalation itself (Zammad ticket + Chatwoot labels) has already
succeeded before this is called.
"""
from __future__ import annotations

import textwrap
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

import structlog

from chatbot.features.chat.case_state import CHATWOOT_CASE_STATE_ATTR, CaseState

if TYPE_CHECKING:
    from chatbot.features.chat.adapters.twilio_channel import TwilioChannelAdapter
    from chatbot.features.chat.pic_registry import PicEntry, PicRegistry
    from chatbot.features.metrics.email_sender import SmtpEmailSender
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

# Type alias: the ChatwootAdapter._request signature we inject.
_CWRequest = Callable[..., Coroutine[Any, Any, dict[str, Any] | None]]


class EscalationNotifier:
    """Fire the three escalation side-effects for a newly escalated case."""

    def __init__(
        self,
        settings: "Settings",
        pic_registry: "PicRegistry",
        email_sender: "SmtpEmailSender",
        twilio_adapter: "TwilioChannelAdapter | None",
        chatwoot_request: _CWRequest,
    ) -> None:
        self._settings = settings
        self._pic_registry = pic_registry
        self._email_sender = email_sender
        self._twilio = twilio_adapter
        self._cw = chatwoot_request

    async def notify(
        self,
        *,
        conv_id: str,
        ticket_id: str,
        title: str,
        body: str,
        department: str | None,
        zammad_ticket_number: str | None,
    ) -> "PicEntry | None":
        """Run all three side-effects; return the resolved PicEntry or None."""
        pic = self._resolve_pic(department)

        await self._write_case_state(conv_id)

        if pic is None:
            _log.info("escalation_notifier_no_pic_for_dept", department=department)
            return None

        if self._settings.escalation_email_enabled:
            self._send_email(pic, title=title, body=body,
                             ticket_id=ticket_id,
                             zammad_ticket_number=zammad_ticket_number)
        await self._send_wa(pic, conv_id=conv_id, title=title)
        return pic

    def _resolve_pic(self, department: str | None) -> "PicEntry | None":
        if not department:
            return None
        # dept label is "dept_apps" — strip prefix if present
        key = department.removeprefix("dept_")
        return self._pic_registry.lookup(key)

    def _send_email(
        self,
        pic: "PicEntry",
        *,
        title: str,
        body: str,
        ticket_id: str,
        zammad_ticket_number: str | None,
    ) -> None:
        zammad_ref = (
            f"Zammad ticket #{zammad_ticket_number}" if zammad_ticket_number else ticket_id
        )
        email_body = textwrap.dedent(f"""\
            A case has been escalated to your team.

            Subject  : {title}
            Reference: {zammad_ref}

            --- Summary ---
            {body}

            Please action this case promptly.
        """)
        try:
            self._email_sender.send(
                to=[pic.pic_email],
                cc=[],
                subject=f"[Escalation] {title}",
                body=email_body,
                attachments=[],
            )
        except Exception as exc:
            _log.warning("escalation_email_failed", pic_email=pic.pic_email, error=str(exc))

    async def _send_wa(self, pic: "PicEntry", *, conv_id: str, title: str) -> None:
        if self._twilio is None:
            return
        to = "whatsapp:" + pic.pic_whatsapp.removeprefix("whatsapp:")
        text = f"🔔 New escalation (case {conv_id}): {title}. Please review and action."
        try:
            await self._twilio.send_message(conversation_id=to, text=text)
        except Exception as exc:
            _log.warning("escalation_wa_failed", to=to, error=str(exc))

    async def _write_case_state(self, conv_id: str) -> None:
        try:
            await self._cw(
                "POST",
                f"/conversations/{conv_id}/custom_attributes",
                {"custom_attributes": {CHATWOOT_CASE_STATE_ATTR: CaseState.WIP.value}},
            )
        except Exception as exc:
            _log.warning("escalation_case_state_write_failed", conv_id=conv_id, error=str(exc))
```

- [ ] **Step 4: Run tests**

```bash
pytest src/chatbot/features/chat/test_escalation_notifier.py -v
```

Expected: 4 PASSED

- [ ] **Step 5: Commit**

```bash
git add src/chatbot/features/chat/escalation_notifier.py \
        src/chatbot/features/chat/test_escalation_notifier.py
git commit -m "feat(phase2): add EscalationNotifier (email CC + WA alert + case_state) (items 13,14)"
```

---

## Task 4: `TEMP_CLOSED` state + `case_state` custom attribute propagation

**Files:**
- Modify: `features/chat/case_state.py`
- Modify: `features/chat/router.py` (`_log_status_transition`)
- Create: `features/chat/test_case_state_temp_closed.py`

**Interfaces:**
- Consumes: `CaseState` enum, `ChatwootAdapter._request`, `AuditLogPort`
- Produces: `CaseState.TEMP_CLOSED`, `CHATWOOT_CASE_STATE_ATTR = "case_state"` constant; `_log_status_transition` writes the attribute to Chatwoot

- [ ] **Step 1: Write the failing tests**

```python
# features/chat/test_case_state_temp_closed.py
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from chatbot.features.chat.adapters.audit_log import InMemoryAuditLog
from chatbot.features.chat.case_state import (
    CHATWOOT_CASE_STATE_ATTR,
    CaseState,
    record_transition,
)
from chatbot.platform.config import Settings


def test_temp_closed_is_in_case_state() -> None:
    assert CaseState.TEMP_CLOSED == "TEMP_CLOSED"


def test_chatwoot_case_state_attr_constant() -> None:
    assert CHATWOOT_CASE_STATE_ATTR == "case_state"


async def test_record_transition_stores_temp_closed() -> None:
    audit = InMemoryAuditLog()
    await record_transition(
        audit,
        ticket_id="99",
        session_id="chatwoot-conv-99",
        actor="agent",
        from_state=CaseState.WIP,
        to_state=CaseState.TEMP_CLOSED,
        at="2026-07-18T10:00:00+00:00",
        remark="temporarily closed pending parts",
    )
    entries = await audit.list_for_ticket("99")
    assert len(entries) == 1
    assert entries[0].to_state == "TEMP_CLOSED"
```

- [ ] **Step 2: Run test to confirm failure**

```bash
pytest src/chatbot/features/chat/test_case_state_temp_closed.py -v
```

Expected: `FAILED test_temp_closed_is_in_case_state` — `CaseState has no member TEMP_CLOSED`

- [ ] **Step 3: Add `TEMP_CLOSED` and `CHATWOOT_CASE_STATE_ATTR` to `case_state.py`**

Open `features/chat/case_state.py`. Replace the `CaseState` class and add the constant:

```python
"""Case status model + audit-recording transition helper."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from chatbot.features.chat.ports import AuditEntry

if TYPE_CHECKING:
    from chatbot.features.chat.ports import AuditLogPort

# Chatwoot custom-attribute key used to surface the case state in the agent UI.
CHATWOOT_CASE_STATE_ATTR = "case_state"


class CaseState(StrEnum):
    NEW = "NEW"
    OPEN = "OPEN"
    WIP = "WIP"
    PENDING = "PENDING"
    TEMP_CLOSED = "TEMP_CLOSED"
    SOLVED = "SOLVED"


async def record_transition(
    audit: "AuditLogPort",
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

- [ ] **Step 4: Extend `_log_status_transition` in `router.py` to write `case_state` attribute**

In `features/chat/router.py`, locate `_log_status_transition`. After appending the audit entry, add a Chatwoot custom-attribute write. The `_log_status_transition` must become `async` if it isn't already (it already is — it's called with `await`). Add at the end of the method body, after the `_log.info(...)` call:

```python
    async def _log_status_transition(self, conv_id: str, payload: ChatwootWebhookPayload) -> None:
        # ... existing code unchanged up to the _log.info call ...

        # Surface the case state in the Chatwoot right panel as a custom attribute.
        # This is best-effort: a failure here must not break the webhook handler.
        try:
            from chatbot.features.chat.case_state import CHATWOOT_CASE_STATE_ATTR  # local import to avoid circular
            ticketing = self.orchestrator._ticketing_port
            if hasattr(ticketing, "_request"):
                await ticketing._request(
                    "POST",
                    f"/conversations/{conv_id}/custom_attributes",
                    {"custom_attributes": {CHATWOOT_CASE_STATE_ATTR: to_state.value}},
                )
        except Exception as exc:
            _log.warning("case_state_attr_write_failed", conv_id=conv_id, error=str(exc))
```

- [ ] **Step 5: Add the import at the top of `router.py`** (already imported via `from chatbot.features.chat.case_state import CaseState, record_transition` — just add `CHATWOOT_CASE_STATE_ATTR` to that import):

```python
from chatbot.features.chat.case_state import CHATWOOT_CASE_STATE_ATTR, CaseState, record_transition
```

- [ ] **Step 6: Add a test for the attribute write in the existing `test_chatwoot_audit_producer.py` (or the new test file)**

In `features/chat/test_case_state_temp_closed.py`, add:

```python
async def test_router_status_transition_writes_case_state_attribute() -> None:
    """_log_status_transition writes case_state to Chatwoot custom_attributes."""
    from unittest.mock import AsyncMock, MagicMock

    from chatbot.features.chat.adapters.audit_log import InMemoryAuditLog
    from chatbot.features.chat.router import ChatRouter
    from chatbot.features.chat.schemas import ChatwootWebhookPayload
    from chatbot.features.chat.case_state import CHATWOOT_CASE_STATE_ATTR

    cw_calls: list[tuple[str, str, Any]] = []

    class _FakeTicketing:
        async def _request(self, method: str, path: str, payload: Any = None) -> dict:
            cw_calls.append((method, path, payload))
            return {}

    class _FakeOrchestrator:
        _settings = Settings(_env_file=None, chatwoot_webhook_secret="")
        _ticketing_port = _FakeTicketing()

    audit = InMemoryAuditLog()
    router = ChatRouter.__new__(ChatRouter)
    router.orchestrator = _FakeOrchestrator()  # type: ignore[assignment]
    router._audit_log = audit
    router._handoff_bridge = None
    router._human_agent_bridge = None
    router._twilio_adapter = None

    payload = ChatwootWebhookPayload.model_validate({
        "event": "conversation_status_changed",
        "message_type": "outgoing",
        "private": False,
        "content": None,
        "conversation": {"id": 55, "status": "pending", "inbox_id": 0},
        "sender": {"name": "Agen1", "email": None},
    })

    await router._log_status_transition("55", payload)

    attr_calls = [pl for _, p, pl in cw_calls if "/custom_attributes" in p]
    assert len(attr_calls) == 1
    assert attr_calls[0]["custom_attributes"][CHATWOOT_CASE_STATE_ATTR] == "WIP"
```

- [ ] **Step 7: Run tests**

```bash
pytest src/chatbot/features/chat/test_case_state_temp_closed.py \
       src/chatbot/features/chat/test_case_state.py -v
```

Expected: all PASSED

- [ ] **Step 8: Commit**

```bash
git add src/chatbot/features/chat/case_state.py \
        src/chatbot/features/chat/router.py \
        src/chatbot/features/chat/test_case_state_temp_closed.py
git commit -m "feat(phase2): add TEMP_CLOSED state; write case_state custom attribute on status change (item 16)"
```

---

## Task 5: Wire `EscalationNotifier` into `ChatwootAdapter` (dept→PIC + Zammad group/owner)

**Files:**
- Modify: `features/chat/adapters/chatwoot.py`
- Modify: `features/chat/adapters/zammad.py`
- Create: `features/chat/test_escalation_pic_wiring.py`

**Interfaces:**
- Consumes: `PicRegistry.lookup` (Task 1), `EscalationNotifier.notify` (Task 3), `ZammadClient.create_ticket(group=...)` (existing param), `ZammadClient.assign_owner` (new method)
- Produces: `ZammadClient.assign_owner(ticket_id: str, owner_email: str) -> None`; `ChatwootAdapter.__init__` gains `pic_registry: PicRegistry | None = None`, `escalation_notifier: EscalationNotifier | None = None`

- [ ] **Step 1: Add `assign_owner` to `ZammadClient`**

Open `features/chat/adapters/zammad.py`. After `add_note`, add:

```python
    async def assign_owner(self, ticket_id: str, owner_email: str) -> None:
        """Assign a Zammad ticket to an agent by their email address.

        Looks up the agent user id by email (GET /api/v1/users?query=<email>),
        then PATCHes the ticket owner_id. Best-effort — errors are logged and
        swallowed so the caller never breaks on a Zammad user-not-found.
        """
        users = await self._request("GET", f"/api/v1/users?query={owner_email}")
        if not isinstance(users, list) or not users:
            _log.warning("zammad_owner_lookup_empty", email=owner_email)
            return
        user_id = users[0].get("id")
        if user_id is None:
            _log.warning("zammad_owner_no_id", email=owner_email)
            return
        await self._request("PUT", f"/api/v1/tickets/{ticket_id}", {"owner_id": int(user_id)})
        _log.info("zammad_ticket_owner_assigned", ticket_id=ticket_id, owner_email=owner_email)
```

- [ ] **Step 2: Write failing tests**

```python
# features/chat/test_escalation_pic_wiring.py
from __future__ import annotations

import json
from typing import Any

import pytest

from chatbot.features.chat.adapters.chatwoot import ChatwootAdapter
from chatbot.features.chat.adapters.zammad import ZammadClient, ZammadTicket
from chatbot.features.chat.escalation_notifier import EscalationNotifier
from chatbot.features.chat.models import HandoffOpenPayload, Message
from chatbot.features.chat.pic_registry import PicEntry, PicRegistry, build_pic_registry
from chatbot.platform.config import Settings


def _settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {
        "chatwoot_account_id": 1,
        "chatwoot_inbox_id": 7,
        "chatwoot_escalation_label": "ai-escalation",
        "chatwoot_complaint_label": "escalate",
        "zammad_direct_ticketing": True,
        "escalation_email_enabled": False,  # email off so only WA + attr tested here
        "escalation_cc_pic": False,
        "pic_map_json": json.dumps({
            "apps": {
                "pic_name": "Alice Tan",
                "pic_email": "alice@proton.my",
                "pic_whatsapp": "+60123456789",
                "zammad_group": "Apps-Support",
                "chatwoot_team_id": 3,
            }
        }),
    }
    base.update(kw)
    return Settings(_env_file=None, **base)


class _FakeCW:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict | None]] = []

    async def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        self.calls.append((method, path, payload))
        if method == "POST" and path.endswith("/conversations"):
            return {"id": 99}
        return {}


class _FakeZammad:
    def __init__(self) -> None:
        self.create_calls: list[dict] = []
        self.assign_calls: list[tuple[str, str]] = []
        self.ticket = ZammadTicket(number="42", id="999")

    async def create_ticket(self, title: str, body: str, customer_email: str,
                             priority: str, group: str | None = None) -> ZammadTicket | None:
        self.create_calls.append({"group": group})
        return self.ticket

    async def add_note(self, ticket_id: str, text: str) -> None:
        pass

    async def assign_owner(self, ticket_id: str, owner_email: str) -> None:
        self.assign_calls.append((ticket_id, owner_email))


class _FakeTwilio:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send_message(self, conversation_id: str, text: str) -> None:
        self.sent.append((conversation_id, text))


async def test_open_handoff_complaint_uses_pic_zammad_group() -> None:
    s = _settings()
    fake_cw = _FakeCW()
    fake_zam = _FakeZammad()
    fake_twilio = _FakeTwilio()

    registry = build_pic_registry(s)
    notifier = EscalationNotifier(
        settings=s,
        pic_registry=registry,
        email_sender=None,  # type: ignore[arg-type]  email disabled in settings
        twilio_adapter=fake_twilio,
        chatwoot_request=fake_cw._request,
    )

    adapter = ChatwootAdapter(
        settings=s,
        zammad=fake_zam,  # type: ignore[arg-type]
        pic_registry=registry,
        escalation_notifier=notifier,
    )
    adapter._request = fake_cw._request  # type: ignore[method-assign]

    payload = HandoffOpenPayload(
        session_id="sim-1",
        customer_name="Budi",
        customer_email="",
        ai_summary="App crashes on login",
        transcript=(Message(role="user", text="the app crashes"),),
        urgency="high",
        reason="negative_sentiment",
        department="apps",
    )
    await adapter.open_handoff(payload)

    # Zammad ticket uses the PIC's group
    assert len(fake_zam.create_calls) == 1
    assert fake_zam.create_calls[0]["group"] == "Apps-Support"

    # Chatwoot team assignment uses the PIC's team_id
    team_calls = [pl for _, p, pl in fake_cw.calls
                  if "/assignments" in p and pl and pl.get("team_id") == 3]
    assert len(team_calls) == 1

    # WA alert sent to PIC
    assert len(fake_twilio.sent) == 1
    wa_to, _ = fake_twilio.sent[0]
    assert "+60123456789" in wa_to


async def test_open_handoff_no_pic_falls_back_to_default_group() -> None:
    s = _settings(pic_map_json="")  # empty → no PIC for any dept
    fake_cw = _FakeCW()
    fake_zam = _FakeZammad()

    registry = build_pic_registry(s)
    adapter = ChatwootAdapter(
        settings=s,
        zammad=fake_zam,  # type: ignore[arg-type]
        pic_registry=registry,
        escalation_notifier=None,
    )
    adapter._request = fake_cw._request  # type: ignore[method-assign]

    payload = HandoffOpenPayload(
        session_id="sim-2",
        customer_name="C",
        customer_email="",
        ai_summary="s",
        transcript=(Message(role="user", text="help"),),
        urgency="high",
        reason="negative_sentiment",
        department="apps",
    )
    await adapter.open_handoff(payload)

    # Falls back to the settings-configured default group
    assert fake_zam.create_calls[0]["group"] is None  # no override when no PIC


async def test_zammad_assign_owner_calls_user_lookup_and_patch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, Any]] = []

    class _FakeResp:
        def __init__(self, body: Any) -> None:
            self._body = body
            self.content = json.dumps(body).encode()
        def raise_for_status(self) -> None: pass
        def json(self) -> Any: return self._body

    class _Client:
        async def __aenter__(self) -> _Client: return self
        async def __aexit__(self, *_: Any) -> bool: return False
        async def request(self, method: str, url: str, **kw: Any) -> _FakeResp:
            calls.append((method, url, kw.get("json")))
            if "users" in url:
                return _FakeResp([{"id": 5, "email": "alice@proton.my"}])
            return _FakeResp({})

    monkeypatch.setattr(
        "chatbot.features.chat.adapters.zammad.httpx.AsyncClient",
        lambda *_a, **_k: _Client(),
    )

    client = ZammadClient(Settings(_env_file=None, zammad_enabled=True,
                                    zammad_api_url="https://zam.example", zammad_api_token="t"))
    await client.assign_owner("999", "alice@proton.my")

    methods = [c[0] for c in calls]
    assert "GET" in methods
    assert "PUT" in methods
    put = next(c for c in calls if c[0] == "PUT")
    assert put[2] == {"owner_id": 5}
```

- [ ] **Step 3: Run tests to confirm failure**

```bash
pytest src/chatbot/features/chat/test_escalation_pic_wiring.py -v
```

Expected: `TypeError: ChatwootAdapter.__init__() got unexpected keyword argument 'pic_registry'`

- [ ] **Step 4: Extend `ChatwootAdapter.__init__` to accept `pic_registry` and `escalation_notifier`**

In `features/chat/adapters/chatwoot.py`, modify the `__init__` signature and body:

```python
    def __init__(
        self,
        settings: Settings,
        zammad: ZammadClient | None = None,
        pic_registry: Any | None = None,  # PicRegistry, typed Any to avoid circular import at module level
        escalation_notifier: Any | None = None,  # EscalationNotifier
    ) -> None:
        self._settings = settings
        self._zammad = zammad
        self._pic_registry = pic_registry
        self._escalation_notifier = escalation_notifier
        self._paused_sessions: set[str] = set()
        self._conv_by_session: dict[str, str] = {}
```

Add at the top of the file (with other `TYPE_CHECKING` imports):

```python
if TYPE_CHECKING:
    from chatbot.features.chat.adapters.zammad import ZammadClient
    from chatbot.features.chat.escalation_notifier import EscalationNotifier
    from chatbot.features.chat.pic_registry import PicRegistry
    from chatbot.platform.config import Settings
```

- [ ] **Step 5: Modify `_escalate_to_zammad` to use PIC group and call `EscalationNotifier`**

Replace the body of `_escalate_to_zammad` in `chatwoot.py`:

```python
    async def _escalate_to_zammad(
        self,
        conv_id: str,
        title: str,
        body: str,
        session_id: str,
        urgency: str | None,
        reason: str | None,
        department: str | None = None,
    ) -> None:
        """Create a Zammad ticket and fire escalation side-effects (PIC routing,
        email CC, WA alert, case_state attribute). No-op when direct ticketing
        is inactive or the case is not a complaint. Errors from side-effects
        are swallowed so the turn never breaks.
        """
        if not self._direct_zammad_active() or self._zammad is None:
            return
        if not self._is_complaint(reason, urgency):
            return

        # Resolve PIC for the department — used for Zammad group override.
        pic = None
        if self._pic_registry is not None and department:
            key = department.removeprefix("dept_")
            pic = self._pic_registry.lookup(key)

        group = pic.zammad_group if pic is not None else None

        ticket = await self._zammad.create_ticket(
            title=title,
            body=body,
            customer_email=self._synth_customer_email(session_id),
            priority=(urgency or "medium"),
            group=group,
        )
        if ticket is None:
            return

        # Assign Zammad ticket to the PIC's email if available.
        if pic is not None:
            await self._zammad.assign_owner(ticket.id, pic.pic_email)

        zoom_url = f"{self._settings.zammad_api_url.rstrip('/')}/#ticket/zoom/{ticket.id}"
        await self.add_private_note(
            conv_id, f"Escalated to Zammad ticket #{ticket.number}: {zoom_url}"
        )

        # Fire escalation side-effects (email CC, WA alert, case_state write).
        if self._escalation_notifier is not None:
            await self._escalation_notifier.notify(
                conv_id=conv_id,
                ticket_id=ticket.id,
                title=title,
                body=body,
                department=department,
                zammad_ticket_number=ticket.number,
            )
```

- [ ] **Step 6: Update `create_ticket` and `open_handoff` calls to pass `department` to `_escalate_to_zammad`**

In `create_ticket`, replace:
```python
        await self._escalate_to_zammad(conv_id, title, body, session_id, urgency, None)
```
With:
```python
        await self._escalate_to_zammad(conv_id, title, body, session_id, urgency, None,
                                        department=department)
```

In `open_handoff`, replace:
```python
        await self._escalate_to_zammad(
            conv_id,
            payload.ai_summary,
            note,
            payload.session_id,
            payload.urgency,
            payload.reason,
        )
```
With:
```python
        await self._escalate_to_zammad(
            conv_id,
            payload.ai_summary,
            note,
            payload.session_id,
            payload.urgency,
            payload.reason,
            department=payload.department,
        )
```

Also update `open_handoff`'s Chatwoot team assignment to use PIC's `chatwoot_team_id` when available:

```python
        # Use PIC team_id if available; fall back to the global setting.
        team_id_to_use = None
        if self._pic_registry is not None and payload.department:
            _key = payload.department.removeprefix("dept_")
            _pic = self._pic_registry.lookup(_key)
            if _pic is not None and _pic.chatwoot_team_id is not None:
                team_id_to_use = _pic.chatwoot_team_id
        if team_id_to_use is None:
            team_id_to_use = self._settings.chatwoot_agent_team_id or None
        if team_id_to_use:
            await self._request(
                "POST",
                f"/conversations/{conv_id}/assignments",
                {"team_id": team_id_to_use},
            )
```

Replace the existing `chatwoot_agent_team_id` block in both `create_ticket` and `open_handoff` with the above pattern.

- [ ] **Step 7: Update `HandoffOpenPayload` to carry `department`**

In `features/chat/models.py`, check if `HandoffOpenPayload` already has `department`. Open the file:
<br>If `department: str | None = None` is already there (it may be, since `_dimension_labels` passes it), no change needed. If missing, add:

```python
    department: str | None = None
```

- [ ] **Step 8: Run the wiring tests**

```bash
pytest src/chatbot/features/chat/test_escalation_pic_wiring.py \
       src/chatbot/features/chat/test_zammad_direct_ticketing.py -v
```

Expected: all PASSED (existing Zammad tests must still pass — the `group` param was already optional and `assign_owner` is new).

- [ ] **Step 9: Commit**

```bash
git add src/chatbot/features/chat/adapters/chatwoot.py \
        src/chatbot/features/chat/adapters/zammad.py \
        src/chatbot/features/chat/test_escalation_pic_wiring.py \
        src/chatbot/features/chat/models.py
git commit -m "feat(phase2): wire PicRegistry+EscalationNotifier into ChatwootAdapter; add ZammadClient.assign_owner (items 12,13,14)"
```

---

## Task 6: Tiered higher-level escalation in the SLA engine

**Files:**
- Modify: `features/chat/sla.py`
- Create: `features/chat/test_sla_tier2.py`
- Modify: `platform/config.py` (already done in Task 1; `escalation_level2_whatsapp`, `escalation_tier2_hours` added)

**Interfaces:**
- Consumes: `scan_conversations(... level2_alert: Callable | None = None)`, `settings.escalation_level2_whatsapp`, `settings.escalation_tier2_hours`
- Produces: `scan_conversations` gains `level2_alert` callback; `_build_level2_alert(settings, twilio_adapter)` mirrors `_build_pic_alert`; `run_sla_scan_job` passes `level2_alert`

- [ ] **Step 1: Write failing tests**

```python
# features/chat/test_sla_tier2.py
"""Unit tests for the tier-2 higher-level escalation (Phase 2, item 15).

Uses the same injected-clock/fetch/callback pattern as test_sla.py.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from chatbot.features.chat.adapters.audit_log import InMemoryAuditLog
from chatbot.features.chat.ports import AuditEntry
from chatbot.features.chat.sla import (
    NO_RESPONSE_BREACH,
    UNRESOLVED_BREACH,
    scan_conversations,
)
from chatbot.platform.config import Settings

_NOW = datetime(2026, 7, 18, 12, 0, 0, tzinfo=UTC)


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "sla_response_hours": 8,
        "sla_resolution_hours": 48,
        "sla_scan_interval_minutes": 15,
        "chatwoot_inbox_id": 1,
        "escalation_tier2_hours": 4.0,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


def _epoch(hours_ago: float, now: datetime = _NOW) -> int:
    return int((now - timedelta(hours=hours_ago)).timestamp())


def _conv(conv_id: int, **fields: Any) -> dict[str, Any]:
    conv: dict[str, Any] = {"id": conv_id, "status": "open"}
    conv.update(fields)
    return conv


async def test_level2_alert_fires_when_unresolved_breach_older_than_tier2() -> None:
    """UNRESOLVED_BREACH older than tier2 hours → level2_alert fires."""
    audit = InMemoryAuditLog()
    settings = _settings(sla_response_hours=1000, sla_resolution_hours=48,
                         escalation_tier2_hours=4.0)
    # Conv is 56h old (48h breach + 8h more, well past 4h tier2 window)
    conv = _conv(301, status="pending", created_at=_epoch(56))
    # Pre-seed the UNRESOLVED_BREACH audit entry as if the first scan already fired it
    breach_at = _NOW - timedelta(hours=8)  # breach fired 8h ago, tier2 = 4h → should re-alert
    await audit.append(AuditEntry(
        ticket_id="301",
        session_id="chatwoot-conv-301",
        actor="sla-engine",
        from_state="OPEN",
        to_state=UNRESOLVED_BREACH,
        at=breach_at.isoformat(),
        remark="test breach",
    ))

    level2_calls: list[tuple[str, str, str]] = []

    def level2(ticket_id: str, to_state: str, remark: str) -> None:
        level2_calls.append((ticket_id, to_state, remark))

    # This scan should NOT re-fire UNRESOLVED_BREACH (already recorded), but SHOULD
    # fire the level2 alert because the breach is older than tier2_hours.
    fired = await scan_conversations(
        settings, audit, now=_NOW, fetch=lambda _s: [conv], level2_alert=level2
    )
    assert fired == []  # no new breach entries
    assert len(level2_calls) == 1
    assert level2_calls[0][0] == "301"


async def test_level2_alert_does_not_fire_when_breach_too_recent() -> None:
    """If the breach is younger than tier2_hours, level2 must NOT fire yet."""
    audit = InMemoryAuditLog()
    settings = _settings(sla_response_hours=1000, sla_resolution_hours=48,
                         escalation_tier2_hours=4.0)
    conv = _conv(302, status="pending", created_at=_epoch(50))
    breach_at = _NOW - timedelta(hours=2)  # breach fired 2h ago, tier2 = 4h → not yet
    await audit.append(AuditEntry(
        ticket_id="302", session_id="chatwoot-conv-302",
        actor="sla-engine", from_state="OPEN", to_state=UNRESOLVED_BREACH,
        at=breach_at.isoformat(), remark="test breach",
    ))

    level2_calls: list[tuple[str, str, str]] = []
    def level2(ticket_id: str, to_state: str, remark: str) -> None:
        level2_calls.append((ticket_id, to_state, remark))

    await scan_conversations(
        settings, audit, now=_NOW, fetch=lambda _s: [conv], level2_alert=level2
    )
    assert level2_calls == []


async def test_level2_alert_does_not_fire_when_none() -> None:
    """Passing level2_alert=None is a safe no-op (backward compat)."""
    audit = InMemoryAuditLog()
    settings = _settings(sla_response_hours=1000, sla_resolution_hours=48)
    conv = _conv(303, status="pending", created_at=_epoch(60))
    await audit.append(AuditEntry(
        ticket_id="303", session_id="chatwoot-conv-303",
        actor="sla-engine", from_state="OPEN", to_state=UNRESOLVED_BREACH,
        at=(_NOW - timedelta(hours=10)).isoformat(), remark="",
    ))
    # Should not raise even without level2_alert
    fired = await scan_conversations(
        settings, audit, now=_NOW, fetch=lambda _s: [conv]
    )
    assert fired == []  # no new breach
```

- [ ] **Step 2: Run tests to confirm failure**

```bash
pytest src/chatbot/features/chat/test_sla_tier2.py -v
```

Expected: `TypeError: scan_conversations() got an unexpected keyword argument 'level2_alert'`

- [ ] **Step 3: Extend `scan_conversations` to accept and invoke `level2_alert`**

In `features/chat/sla.py`, modify the signature of `scan_conversations`:

```python
async def scan_conversations(
    settings: Settings,
    audit: AuditLogPort,
    *,
    now: datetime | None = None,
    fetch: Callable[[Settings], list[dict[str, Any]]] | None = None,
    alert: Callable[[str, str, str], Any] | None = None,
    level2_alert: Callable[[str, str, str], Any] | None = None,
) -> list[AuditEntry]:
```

At the end of the `for conv in conversations:` loop body (after the two `if` breach blocks), add:

```python
        # Tier-2 escalation: if an UNRESOLVED_BREACH was already fired and is
        # older than escalation_tier2_hours, fire the level2_alert callback.
        if level2_alert is not None and UNRESOLVED_BREACH in prior:
            tier2_threshold = settings.escalation_tier2_hours * _SECONDS_PER_HOUR
            # Find the age of the UNRESOLVED_BREACH entry.
            all_entries = await audit.list_for_ticket(ticket_id)
            breach_entry = next(
                (e for e in all_entries if e.to_state == UNRESOLVED_BREACH), None
            )
            if breach_entry is not None:
                try:
                    breach_at = datetime.fromisoformat(breach_entry.at)
                    breach_age = (clock - breach_at).total_seconds()
                except (ValueError, TypeError):
                    breach_age = 0.0
                if breach_age >= tier2_threshold:
                    try:
                        result = level2_alert(
                            ticket_id,
                            "TIER2_ESCALATION",
                            f"Unresolved breach older than {settings.escalation_tier2_hours}h; re-alerting level-2",
                        )
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception as exc:
                        _log.warning("level2_alert_failed", ticket_id=ticket_id, error=str(exc))
```

Add `_build_level2_alert` alongside `_build_pic_alert`:

```python
def _build_level2_alert(
    settings: Settings, twilio_adapter: TwilioChannelAdapter | None
) -> Callable[[str, str, str], Any] | None:
    """Build the optional level-2 WhatsApp alert callback.

    Uses ``escalation_level2_whatsapp`` for the target number. Returns None
    when no level-2 number or Twilio adapter is configured.
    """
    target = settings.escalation_level2_whatsapp
    if not target or twilio_adapter is None:
        return None
    to = "whatsapp:" + target.removeprefix("whatsapp:")

    async def _alert(ticket_id: str, to_state: str, remark: str) -> None:
        await twilio_adapter.send_message(
            conversation_id=to,
            text=f"⚠️ TIER-2 escalation on case {ticket_id}. {remark}",
        )

    return _alert
```

Update `run_sla_scan_job` to pass `level2_alert`:

```python
def run_sla_scan_job(
    settings: Settings,
    audit: AuditLogPort,
    *,
    twilio_adapter: TwilioChannelAdapter | None = None,
) -> list[AuditEntry]:
    """Synchronous entry point for the scheduler. Best-effort: never raises."""
    alert = _build_pic_alert(settings, twilio_adapter)
    level2_alert = _build_level2_alert(settings, twilio_adapter)
    try:
        return asyncio.run(
            scan_conversations(settings, audit, alert=alert, level2_alert=level2_alert)
        )
    except Exception as e:
        _log.error("sla_scan_job_failed", error=str(e))
        return []
```

- [ ] **Step 4: Run all SLA tests**

```bash
pytest src/chatbot/features/chat/test_sla.py \
       src/chatbot/features/chat/test_sla_tier2.py \
       src/chatbot/features/chat/test_sla_webhook.py -v
```

Expected: all PASSED (existing tests unaffected since `level2_alert` defaults to `None`).

- [ ] **Step 5: Commit**

```bash
git add src/chatbot/features/chat/sla.py \
        src/chatbot/features/chat/test_sla_tier2.py
git commit -m "feat(phase2): add tier-2 level-2 escalation alert to SLA engine (item 15)"
```

---

## Task 7: Integration smoke — wire everything in `app.py` / `main.py`

**Files:**
- Modify: `app.py` or `main.py` (whichever constructs `ChatwootAdapter`, `start_sla_scheduler`) — confirm the exact path first.
- Create: `features/chat/test_phase2_smoke.py`

**Interfaces:**
- Consumes: all produced objects from Tasks 1–6
- Produces: running app with `PicRegistry`, `EscalationNotifier`, `SmtpEmailSender` wired via constructor injection

- [ ] **Step 1: Find the app entry point**

```bash
find /path/to/proton-conversational-ai/apps/backend/src -name "app.py" -o -name "main.py" | grep -v test | grep -v .venv
```

- [ ] **Step 2: Wire `PicRegistry` + `EscalationNotifier` into the app factory**

Open the identified file (e.g. `chatbot/app.py`). Locate where `ChatwootAdapter` is constructed. Inject the new dependencies:

```python
from chatbot.features.chat.pic_registry import build_pic_registry
from chatbot.features.chat.escalation_notifier import EscalationNotifier
from chatbot.features.metrics.email_sender import SmtpEmailSender

# In the app/dependency factory:
pic_registry = build_pic_registry(settings)
email_sender = SmtpEmailSender(settings)

# Construct twilio_adapter first (already exists in code)
# ...

escalation_notifier = EscalationNotifier(
    settings=settings,
    pic_registry=pic_registry,
    email_sender=email_sender,
    twilio_adapter=twilio_adapter,
    chatwoot_request=chatwoot_adapter._request,
)

chatwoot_adapter = ChatwootAdapter(
    settings=settings,
    zammad=zammad_client,
    pic_registry=pic_registry,
    escalation_notifier=escalation_notifier,
)
```

Also pass `level2_alert` to `start_sla_scheduler` by passing the twilio_adapter (it already does this) — `run_sla_scan_job` now builds the level2 callback internally using `settings.escalation_level2_whatsapp`, so no extra change needed.

- [ ] **Step 3: Write a smoke test that imports and constructs without crashing**

```python
# features/chat/test_phase2_smoke.py
from __future__ import annotations

from chatbot.features.chat.case_state import CHATWOOT_CASE_STATE_ATTR, CaseState
from chatbot.features.chat.escalation_notifier import EscalationNotifier
from chatbot.features.chat.pic_registry import PicEntry, PicRegistry, build_pic_registry
from chatbot.features.metrics.email_sender import SmtpEmailSender
from chatbot.platform.config import Settings


def test_imports_and_constructs_without_error() -> None:
    s = Settings(_env_file=None, pic_map_json="", escalation_email_enabled=False)
    registry = build_pic_registry(s)
    sender = SmtpEmailSender(s)
    notifier = EscalationNotifier(
        settings=s,
        pic_registry=registry,
        email_sender=sender,
        twilio_adapter=None,
        chatwoot_request=None,  # type: ignore[arg-type]
    )
    assert notifier is not None
    assert CaseState.TEMP_CLOSED == "TEMP_CLOSED"
    assert CHATWOOT_CASE_STATE_ATTR == "case_state"


def test_pic_entry_full_construction() -> None:
    entry = PicEntry(
        pic_name="Zaid",
        pic_email="zaid@proton.my",
        pic_whatsapp="+60199999999",
        zammad_group="Sales-Support",
        chatwoot_team_id=7,
    )
    assert entry.zammad_group == "Sales-Support"
    assert entry.chatwoot_team_id == 7
```

- [ ] **Step 4: Run the smoke test + full Phase 2 suite**

```bash
pytest src/chatbot/features/chat/test_phase2_smoke.py \
       src/chatbot/features/chat/test_pic_registry.py \
       src/chatbot/features/chat/test_escalation_notifier.py \
       src/chatbot/features/chat/test_escalation_pic_wiring.py \
       src/chatbot/features/chat/test_case_state_temp_closed.py \
       src/chatbot/features/chat/test_sla_tier2.py \
       src/chatbot/features/metrics/test_email_sender.py -v
```

Expected: all PASSED

- [ ] **Step 5: Run the full existing test suite to confirm no regressions**

```bash
pytest src/chatbot/ -v --tb=short -q
```

Expected: all existing tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/chatbot/features/chat/test_phase2_smoke.py
# Add any modified app.py / main.py
git commit -m "feat(phase2): wire PicRegistry+EscalationNotifier into app factory; integration smoke (items 12-16)"
```

---

## Self-Review

### 1. Spec Coverage

| Item | Requirement | Task |
|---|---|---|
| 12 | dept→PIC config-driven mapping; set Zammad group/owner + Chatwoot team at escalation; write `pic_*` label | Tasks 1, 5 |
| 13 | Email CC to PIC with case summary + attachments | Tasks 2, 3 |
| 14 | WA to PIC at escalation time (not only SLA breach) | Task 3 |
| 15 | Tiered higher-level escalation (level-2 timer) | Task 6 |
| 16 | `TEMP_CLOSED` state + surface case states to agents via Chatwoot custom attribute | Task 4 |

All five items are covered. Items 17 and 18 are already shipped and explicitly excluded.

**Gap noted:** Item 12 mentions a `pic_*` label should be written to the conversation. The `_dimension_labels` method in `chatwoot.py` doesn't add a `pic_*` label — it only reads it in `mapping.py`. To satisfy the spec's "write the `pic_*` label", `_dimension_labels` (or the `create_ticket`/`open_handoff` label list) should include `pic_<pic_name_slug>` when a PIC is resolved. **Add this to Task 5 Step 6**: in `_escalate_to_zammad`, after resolving `pic`, the caller should pass `pic.pic_name` back to `create_ticket`/`open_handoff` so a `pic_<slug>` label is merged into the dimension labels. Since the label merge happens in `create_ticket`/`open_handoff` (not in `_escalate_to_zammad`), the cleanest approach is to add a `pic_label(department: str | None) -> str | None` helper to `ChatwootAdapter` that resolves the PIC and returns `f"pic_{_norm(pic.pic_name)}"`, then include it in the labels merge in both `create_ticket` and `open_handoff`.

**Add these two steps to Task 5 (Step 6a and 6b):**

- [ ] **Step 6a: Add `_pic_label` helper to `ChatwootAdapter`**

```python
    def _pic_label(self, department: str | None) -> str | None:
        """Return the pic_<name_slug> label for the resolved PIC, or None."""
        if self._pic_registry is None or not department:
            return None
        key = department.removeprefix("dept_")
        pic = self._pic_registry.lookup(key)
        if pic is None:
            return None
        slug = pic.pic_name.strip().lower().replace(" ", "_")
        return f"pic_{slug}"
```

- [ ] **Step 6b: Include the `pic_*` label in the labels merge in `create_ticket` and `open_handoff`**

In `create_ticket`, after `dimension_labels = self._dimension_labels(...)`, change:

```python
        pic_lbl = self._pic_label(department)
        await self._request(
            "POST",
            f"/conversations/{conv_id}/labels",
            {
                "labels": list(
                    dict.fromkeys(
                        dimension_labels
                        + ([pic_lbl] if pic_lbl else [])
                        + self._escalation_labels()
                        + self._complaint_labels(None, urgency)
                    )
                )
            },
        )
```

Apply the same pattern in `open_handoff` (pass `payload.department` to `_pic_label`).

### 2. Placeholder Scan

No TBD/TODO/placeholder phrases in the plan. All code blocks are complete.

### 3. Type Consistency

- `PicEntry` defined in Task 1; consumed by `EscalationNotifier` (Task 3), `ChatwootAdapter` (Task 5), and tests — consistent.
- `CHATWOOT_CASE_STATE_ATTR` defined in Task 4; imported in `EscalationNotifier` (Task 3) and `router.py` (Task 4) — consistent.
- `scan_conversations(... level2_alert=None)` defined in Task 6; called without `level2_alert` in all existing tests (backward compat) — consistent.
- `SmtpEmailSender.send(to, cc, subject, body, attachments)` defined in Task 2; called in `EscalationNotifier._send_email` — consistent.
- `ZammadClient.assign_owner(ticket_id: str, owner_email: str)` defined in Task 5 and tested in the same task — consistent.
