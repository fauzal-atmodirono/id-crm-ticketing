# Proton Feedback Follow-ups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the six partial/not-built items from the 2026-08-06 Proton call — above all, make a dealer's email reply land back on the originating conversation with an AI-drafted customer response waiting for the agent.

**Architecture:** Escalation mail goes out carrying a correlation token (`Reply-To: …+case<id>@…` plus a `[CASE-<id>]` subject tag). The reply is re-ingested by Chatwoot's existing Email inbox as a throwaway conversation; a new `message_created` background task in `agent/` reads the token, verifies the sender against the escalation routing table, and copies the reply onto conversation `#N` — as a private note for an internal sender, as a public incoming message for the customer. No new mailbox, no new credentials, no mail-routing change.

**Tech Stack:** FastAPI + SQLAlchemy 2.0 async (`agent/`), FastAPI + Firestore + APScheduler (`backend/`), pytest + respx, Vue 2/3 SFC patches against the Chatwoot fork.

**Spec:** `docs/superpowers/specs/2026-08-07-proton-feedback-followups-design.md`

## Global Constraints

- Every new setting **defaults off / empty**, so `default` and `wahchan` tenants stay byte-identical until opted in.
- Every new env var must be added to **both** `agent/app/config.py` (or `backend/.../platform/config.py`) **and** `deploy/tenants/example.env`. Names map case-insensitively and must match verbatim.
- Background tasks in `agent/app/services/` **never raise** for expected "nothing to do" cases — log and return.
- Webhook handlers keep the verify → dedupe → 200-fast → dispatch shape. No slow work inline.
- Agent tests: `cd agent && pytest`. Never hit postgres, live Chatwoot, or Gemini — `respx` for HTTP, sqlite for DB.
- Backend tests: `cd backend/apps/backend && uv run pytest`. Test files live beside the module (`src/chatbot/features/chat/test_*.py`), `asyncio_mode = "auto"`.
- Settings in tests are overridden with `monkeypatch.setattr(get_settings(), "<field>", value)` followed by `get_proton_config_client.cache_clear()` where the client is cached.
- The Chatwoot fork image is built **off-VM, for `amd64`, via Cloud Build only** (`deploy/chatwoot-fork/cloudbuild.yaml`). Never on the prod VM, never from a local Mac.
- Commit after every task. Branch: `dev-yuda`. Never merge to `main`.

---

## Phase 0 — Spikes (do first; both later phases depend on them)

### Task 1: Verify the mail round-trip and webhook payload

**Files:**
- Create: `docs/testing/2026-08-07-escalation-reply-spike.md`

No code. This answers two questions the whole of Phase 1 rests on. Nothing in this repo reads `content_attributes.email` today.

- [ ] **Step 1: Send a tagged test mail through the tenant's relay**

From any mailbox, send to the proton Email inbox address with a `Reply-To` you control, then reply to it. Confirm the reply arrives at the plus-addressed variant:

```bash
# On the VM, or any host with the tenant's SMTP creds:
python3 - <<'PY'
import smtplib
from email.message import EmailMessage
msg = EmailMessage()
msg["From"] = "Support <devotech29@gmail.com>"
msg["To"] = "<your-test-mailbox>"
msg["Reply-To"] = "devotech29+case9999@gmail.com"
msg["Subject"] = "[CASE-9999] spike: reply routing"
msg.set_content("Reply to this mail without editing the subject.")
with smtplib.SMTP("smtp.gmail.com", 587) as s:
    s.starttls(); s.login("<user>", "<app-password>"); s.send_message(msg)
PY
```

Reply from the test mailbox. Record: did the reply reach the inbox, and did the `To:` header retain `+case9999`?

- [ ] **Step 2: Capture the webhook payload Chatwoot emits for that reply**

Temporarily point the account webhook at a request bin, or read the agent logs after adding a one-line debug log. Record the full `message_created` payload, specifically whether `content_attributes.email` exists and which of `to`, `cc`, `subject`, `in_reply_to` it carries.

- [ ] **Step 3: Write up the findings**

Create `docs/testing/2026-08-07-escalation-reply-spike.md` recording, for each question, the observed answer and a verbatim payload excerpt. State explicitly which correlation key Phase 1 will use as primary (`content_attributes.email.to`) and whether the subject fallback is required.

**If plus-addressing is stripped AND the subject tag survives:** Phase 1 proceeds with the subject as the primary key — change Task 8's extraction order, nothing else.
**If neither survives:** stop and escalate. The spec's approach B (dedicated mailbox) becomes necessary and Phase 1 will not land by Tuesday.

- [ ] **Step 4: Commit**

```bash
git add docs/testing/2026-08-07-escalation-reply-spike.md
git commit -m "docs(testing): escalation reply routing spike findings"
```

---

### Task 2: Execute the existing escalation E2E script

**Files:**
- Modify: `docs/testing/2026-08-06-escalation-email-e2e-scenario.md`

The outbound legs have never been run. Building the reply loop on an unproven forward leg is the main schedule risk.

- [ ] **Step 1: Run TC-01 through TC-06 exactly as written**

Follow `docs/testing/2026-08-06-escalation-email-e2e-scenario.md` §4 on the proton tenant. Do not fix anything yet — record what happens.

- [ ] **Step 2: Record results in a new "Execution log" section**

Append to the doc:

```markdown
## 6. Execution log

**Run date:** 2026-08-07 | **Executed by:** <name> | **Build:** <agent/backend image tags>

| Case | Result | Notes |
|---|---|---|
| TC-01 | PASS/FAIL | … |
| TC-02 | PASS/FAIL | … |
| TC-03 | PASS/FAIL | … |
| TC-04 | PASS/FAIL | … |
| TC-05 | PASS/FAIL | … |
| TC-06 | PASS/FAIL | … |
```

- [ ] **Step 3: File each failure as its own task before starting Phase 1**

Any FAIL is a blocking bug in a path Phase 1 extends. Fix it first, with a regression test in `agent/tests/test_sync_escalation.py` or the matching backend test module.

- [ ] **Step 4: Commit**

```bash
git add docs/testing/2026-08-06-escalation-email-e2e-scenario.md
git commit -m "docs(testing): record escalation E2E execution results"
```

---

## Phase 1 — The reply loop

### Task 3: Add `reply_to` to the SMTP sender

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/metrics/email_sender.py`
- Test: `backend/apps/backend/src/chatbot/features/metrics/test_email_sender_reply_to.py`

**Interfaces:**
- Produces: `SmtpEmailSender.send(to, cc, subject, body, attachments, reply_to: str | None = None) -> None`

- [ ] **Step 1: Write the failing test**

```python
"""Reply-To header support on the shared SMTP sender (escalation reply loop)."""

from __future__ import annotations

from chatbot.features.metrics.email_sender import SmtpEmailSender


class _FakeSMTP:
    sent: list = []

    def __init__(self, host: str, port: int) -> None:
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        pass

    def login(self, user, password):
        pass

    def send_message(self, msg):
        _FakeSMTP.sent.append(msg)


class _Settings:
    smtp_host = "smtp.test"
    smtp_port = 587
    smtp_user = ""
    smtp_password = ""
    smtp_from = "Support <support@test>"


def test_send_sets_reply_to_when_given():
    _FakeSMTP.sent = []
    sender = SmtpEmailSender(_Settings(), smtp_factory=_FakeSMTP)

    sender.send(
        to=["dealer@test"],
        cc=[],
        subject="[CASE-42] hello",
        body="body",
        attachments=[],
        reply_to="support+case42@test",
    )

    assert _FakeSMTP.sent[0]["Reply-To"] == "support+case42@test"


def test_send_omits_reply_to_by_default():
    _FakeSMTP.sent = []
    sender = SmtpEmailSender(_Settings(), smtp_factory=_FakeSMTP)

    sender.send(to=["dealer@test"], cc=[], subject="hello", body="body", attachments=[])

    assert _FakeSMTP.sent[0]["Reply-To"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/metrics/test_email_sender_reply_to.py -v`
Expected: FAIL — `send() got an unexpected keyword argument 'reply_to'`

- [ ] **Step 3: Implement**

In `email_sender.py`, add the parameter and set the header. Keep it keyword-only so no positional call site can break:

```python
    def send(
        self,
        to: list[str],
        cc: list[str],
        subject: str,
        body: str,
        attachments: list[Attachment],
        *,
        reply_to: str | None = None,
    ) -> None:
        """Send an email synchronously. Swallows and logs all errors.

        ``reply_to`` carries the escalation correlation token (see
        escalation_notifier); omitted by default so existing callers produce
        byte-identical mail.
        """
        if not to or not self._s.smtp_host:
            return
        msg = EmailMessage()
        msg["From"] = self._s.smtp_from
        msg["To"] = ", ".join(to)
        if cc:
            msg["Cc"] = ", ".join(cc)
        if reply_to:
            msg["Reply-To"] = reply_to
        msg["Subject"] = subject
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/metrics/ -v`
Expected: PASS, including the pre-existing sender tests.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/backend/src/chatbot/features/metrics/email_sender.py \
        backend/apps/backend/src/chatbot/features/metrics/test_email_sender_reply_to.py
git commit -m "feat(backend): optional Reply-To on SmtpEmailSender"
```

---

### Task 4: Tag escalation mail with the case token

**Files:**
- Modify: `backend/apps/backend/src/chatbot/platform/config.py`
- Modify: `backend/apps/backend/src/chatbot/features/chat/escalation_notifier.py`
- Modify: `deploy/tenants/example.env`
- Test: `backend/apps/backend/src/chatbot/features/chat/test_escalation_reply_tagging.py`

**Interfaces:**
- Consumes: `SmtpEmailSender.send(..., reply_to=...)` from Task 3.
- Produces: `EscalationNotifier._reply_to_for(conv_id: str) -> str | None`; PIC and dealer subjects gain a `[CASE-<id>]` tag; the customer ack gets `reply_to` but **no** subject tag.

- [ ] **Step 1: Write the failing test**

```python
"""Correlation tagging on escalation mail (Reply-To + [CASE-n] subject)."""

from __future__ import annotations

from chatbot.features.chat.escalation_notifier import EscalationNotifier
from chatbot.features.chat.pic_registry import PicEntry


class _Sender:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def send(self, to, cc, subject, body, attachments, *, reply_to=None):
        self.calls.append({"to": to, "subject": subject, "reply_to": reply_to})


class _Settings:
    escalation_email_enabled = True
    email_escalation_ack_enabled = True
    email_escalation_ack_template = "ack body"
    escalation_cc_pic = True
    escalation_reply_to_template = "support+case{conv_id}@test"
    dealer_email_map_json = ""


class _DealerStore:
    async def get(self, dealer):
        class _R:
            emails = ["dealer@test"]
        return _R()


class _Registry:
    async def lookup(self, dept):
        return PicEntry(department=dept, name="Aduy", email="pic@test", whatsapp="", cc_emails=["cc@test"])


async def _noop_cw(conv_id, attrs):
    return None


def _notifier(sender):
    return EscalationNotifier(
        _Settings(), _Registry(), sender, None, _noop_cw, dealer_store=_DealerStore()
    )


async def test_pic_and_dealer_mail_carry_token_customer_ack_does_not():
    sender = _Sender()
    await _notifier(sender).notify_email_channel_escalation(
        conv_id="42",
        title="my car will not start",
        body="transcript",
        department="sales",
        dealer="komang_motor",
        customer_email="customer@test",
    )

    by_to = {c["to"][0]: c for c in sender.calls}

    assert by_to["customer@test"]["reply_to"] == "support+case42@test"
    assert "[CASE-42]" not in by_to["customer@test"]["subject"]

    assert by_to["pic@test"]["reply_to"] == "support+case42@test"
    assert by_to["pic@test"]["subject"].startswith("[Escalation] [CASE-42]")

    assert by_to["dealer@test"]["reply_to"] == "support+case42@test"
    assert "[CASE-42]" in by_to["dealer@test"]["subject"]


async def test_empty_template_leaves_mail_untagged():
    sender = _Sender()
    settings = _Settings()
    settings.escalation_reply_to_template = ""
    notifier = EscalationNotifier(
        settings, _Registry(), sender, None, _noop_cw, dealer_store=_DealerStore()
    )

    await notifier.notify_email_channel_escalation(
        conv_id="42", title="t", body="b", department="sales", dealer=None,
        customer_email="customer@test",
    )

    assert all(c["reply_to"] is None for c in sender.calls)
    assert all("[CASE-" not in c["subject"] for c in sender.calls)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/chat/test_escalation_reply_tagging.py -v`
Expected: FAIL — `_Settings` has no attribute used by the notifier / `reply_to` never set.

- [ ] **Step 3: Add the setting**

In `backend/.../platform/config.py`, beside the other escalation settings:

```python
    # Reply-To template for escalation mail, e.g.
    # "support+case{conv_id}@proton.example". Empty (default) means no
    # Reply-To and no [CASE-n] subject tag -- mail is byte-identical to
    # pre-reply-loop behavior. `{conv_id}` is the only placeholder.
    escalation_reply_to_template: str = ""
```

In `deploy/tenants/example.env`, beside the other escalation vars:

```bash
# Escalation reply correlation. Empty disables the reply loop entirely.
# Must be an address that delivers to the tenant's Email inbox mailbox.
ESCALATION_REPLY_TO_TEMPLATE=
```

- [ ] **Step 4: Implement the tagging**

In `escalation_notifier.py`, add the helper and use it in the three senders:

```python
    def _reply_to_for(self, conv_id: str) -> str | None:
        """Correlation Reply-To for this conversation, or None when the
        template is unset (the default -- mail then goes out untagged)."""
        template = (self._settings.escalation_reply_to_template or "").strip()
        if not template:
            return None
        try:
            return template.format(conv_id=conv_id)
        except (KeyError, IndexError):
            _log.warning("escalation_reply_to_template_invalid", template=template)
            return None

    def _case_tag(self, conv_id: str) -> str:
        """`[CASE-n] ` subject prefix, or "" when the reply loop is off.

        Internal mail only -- the customer ack never carries a visible tag.
        """
        return f"[CASE-{conv_id}] " if self._reply_to_for(conv_id) else ""
```

`_send_customer_ack` needs the conversation id to build its Reply-To, so change its signature and its one call site:

```python
    def _send_customer_ack(self, to_email: str, *, conv_id: str, title: str) -> None:
        try:
            self._email_sender.send(
                to=[to_email],
                cc=[],
                subject=f"Update on your case: {title}",
                body=self._settings.email_escalation_ack_template,
                attachments=[],
                reply_to=self._reply_to_for(conv_id),
            )
        except Exception as exc:
            _log.warning("escalation_customer_ack_failed", to_email=to_email, error=str(exc))
```

In `notify_email_channel_escalation`, update the ack call to
`self._send_customer_ack(customer_email, conv_id=conv_id, title=title)`.

In `_send_email` (PIC), the subject becomes
`f"[Escalation] {self._case_tag(conv_id)}{title}"` and the send gains
`reply_to=self._reply_to_for(conv_id)`.

In `_send_dealer_forward`, the subject becomes
`f"[Escalation - Dealer Forward] {self._case_tag(conv_id)}{title}"` and the
send gains `reply_to=self._reply_to_for(conv_id)`.

- [ ] **Step 5: Run tests**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/chat/ -k "escalation" -v`
Expected: PASS, including the pre-existing escalation tests (they assert untagged subjects and still pass because the template defaults to empty).

- [ ] **Step 6: Commit**

```bash
git add backend/apps/backend/src/chatbot/platform/config.py \
        backend/apps/backend/src/chatbot/features/chat/escalation_notifier.py \
        backend/apps/backend/src/chatbot/features/chat/test_escalation_reply_tagging.py \
        deploy/tenants/example.env
git commit -m "feat(backend): tag escalation mail with case correlation token"
```

---

### Task 5: Dealer groups — many emails per dealer

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/chat/pic_store.py`
- Modify: `backend/apps/backend/src/chatbot/features/chat/escalation_notifier.py`
- Modify: `backend/apps/backend/src/chatbot/features/chat/pic_admin_router.py`
- Test: `backend/apps/backend/src/chatbot/features/chat/test_dealer_groups.py`

**Interfaces:**
- Produces: `DealerRecord(dealer: str, emails: list[str])`; `DealerStore.set(dealer, emails: list[str])`; `build_dealer_email_map(settings) -> dict[str, list[str]]`; `DealerUpsertBody(emails: list[str], email: str | None)`.

The reader accepts both the old `email` string and the new `emails` list, so existing Firestore documents and `DEALER_EMAIL_MAP_JSON` values keep working with **no migration**.

- [ ] **Step 1: Write the failing test**

```python
"""Dealer rows as groups: many member emails, old single-email shape still read."""

from __future__ import annotations

from chatbot.features.chat.escalation_notifier import build_dealer_email_map
from chatbot.features.chat.pic_store import _dealer_record_from_dict


class _Settings:
    dealer_email_map_json = ""


def test_reads_new_list_shape():
    rec = _dealer_record_from_dict({"dealer": "komang", "emails": ["a@t", "b@t"]}, "komang")
    assert rec.emails == ["a@t", "b@t"]


def test_reads_legacy_string_shape():
    rec = _dealer_record_from_dict({"dealer": "komang", "email": "a@t"}, "komang")
    assert rec.emails == ["a@t"]


def test_env_map_accepts_string_and_list():
    settings = _Settings()
    settings.dealer_email_map_json = '{"komang": "a@t", "other": ["b@t", "c@t"]}'
    assert build_dealer_email_map(settings) == {"komang": ["a@t"], "other": ["b@t", "c@t"]}


def test_env_map_drops_malformed_entries():
    settings = _Settings()
    settings.dealer_email_map_json = '{"ok": ["a@t"], "bad": 7, "empty": []}'
    assert build_dealer_email_map(settings) == {"ok": ["a@t"]}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/chat/test_dealer_groups.py -v`
Expected: FAIL — `cannot import name '_dealer_record_from_dict'`

- [ ] **Step 3: Implement the store change**

In `pic_store.py`, replace the `DealerRecord` dataclass and add the shared reader:

```python
@dataclass(frozen=True)
class DealerRecord:
    dealer: str
    emails: list[str] = field(default_factory=list)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, DealerRecord):
            return NotImplemented
        return self.dealer == other.dealer and list(self.emails) == list(other.emails)

    def __hash__(self) -> int:
        return hash((self.dealer, tuple(self.emails)))


def _dealer_record_from_dict(data: dict, fallback_key: str) -> DealerRecord:
    """Build a DealerRecord from a Firestore document body.

    Accepts BOTH shapes so no migration is needed: the new `emails` list and
    the original single `email` string written before dealers became groups.
    """
    emails = data.get("emails")
    if isinstance(emails, list):
        members = [str(e) for e in emails if e]
    else:
        legacy = data.get("email")
        members = [str(legacy)] if legacy else []
    return DealerRecord(dealer=str(data.get("dealer", fallback_key)), emails=members)
```

`DealerStore.get` becomes `return _dealer_record_from_dict(data, dealer)`; `list_all` uses the same helper per snapshot; `set` becomes:

```python
    async def set(self, dealer: str, emails: list[str]) -> None:
        try:
            await asyncio.to_thread(
                self._doc_ref(dealer).set, {"dealer": dealer, "emails": list(emails)}
            )
        except Exception as e:
            _log.error("dealer_store_set_failed", dealer=dealer, error=str(e))
```

- [ ] **Step 4: Implement the env-map and send changes**

In `escalation_notifier.py`, `build_dealer_email_map` now returns lists:

```python
def build_dealer_email_map(settings: Settings) -> dict[str, list[str]]:
    """Parse dealer_email_map_json into a lower-cased slug -> [email] dict.

    A value may be a single string (the original shape) or a list of
    addresses (dealers as groups). Returns {} on absent/blank/malformed JSON,
    and silently drops entries that are neither a non-empty string nor a list
    containing at least one address.
    """
    raw = (settings.dealer_email_map_json or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    result: dict[str, list[str]] = {}
    for key, val in data.items():
        if not isinstance(key, str):
            continue
        if isinstance(val, str) and val:
            result[key.lower()] = [val]
        elif isinstance(val, list):
            members = [str(v) for v in val if isinstance(v, str) and v]
            if members:
                result[key.lower()] = members
    return result
```

In `_send_dealer_forward`, resolve to a list and send to all members:

```python
        emails: list[str] = []
        if self._dealer_store is not None:
            record = await self._dealer_store.get(dealer_slug.lower())
            if record is not None:
                emails = list(record.emails)
        if not emails:
            emails = list(self._dealer_email_map.get(dealer_slug.lower()) or [])
        if not emails:
            _log.info("escalation_dealer_unmapped", dealer=dealer_slug)
            return
```

and the send becomes `to=emails`.

- [ ] **Step 5: Implement the admin router change**

In `pic_admin_router.py`:

```python
class DealerUpsertBody(BaseModel):
    """`emails` is the group's member list. `email` is accepted for
    compatibility with the pre-groups UI and is folded into `emails`."""

    emails: list[str] = Field(default_factory=list)
    email: str | None = None

    def members(self) -> list[str]:
        merged = [e for e in self.emails if e]
        if self.email and self.email not in merged:
            merged.append(self.email)
        return merged
```

and the handler:

```python
    @router.put("/dealers/{dealer}", dependencies=[Depends(manage_escalation)])
    async def upsert_dealer(dealer: str, body: DealerUpsertBody) -> dict:
        await dealer_store.set(dealer, emails=body.members())
        return {"dealer": dealer, "status": "ok"}
```

- [ ] **Step 6: Run tests**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/chat/ -v`
Expected: PASS. Fix any pre-existing test that constructs `DealerRecord(dealer=…, email=…)` to use `emails=[…]`.

- [ ] **Step 7: Commit**

```bash
git add backend/apps/backend/src/chatbot/features/chat/pic_store.py \
        backend/apps/backend/src/chatbot/features/chat/escalation_notifier.py \
        backend/apps/backend/src/chatbot/features/chat/pic_admin_router.py \
        backend/apps/backend/src/chatbot/features/chat/test_dealer_groups.py
git commit -m "feat(backend): dealer rows become groups with member email lists"
```

---

### Task 6: `GET /escalation/contacts` — the sender allowlist

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/chat/escalation_router.py`
- Test: `backend/apps/backend/src/chatbot/features/chat/test_escalation_contacts.py`

**Interfaces:**
- Produces: `GET /escalation/contacts` → `{"contacts": [{"email": str, "name": str, "kind": "pic" | "dealer"}]}`, authenticated with the same `x-api-key` as `/escalation/notify`.

This is what stops a customer from injecting a private note by guessing a conversation id.

- [ ] **Step 1: Write the failing test**

```python
"""GET /escalation/contacts — the escalation sender allowlist."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.chat.escalation_router import build_escalation_router
from chatbot.features.chat.pic_store import DealerRecord, PicRecord


class _Settings:
    proton_backend_key = "secret"


class _PicStore:
    async def list_all(self):
        return [
            PicRecord(
                department="sales",
                pic_name="Aduy",
                pic_email="pic@test",
                pic_whatsapp="",
                cc_emails=["cc@test"],
            )
        ]


class _DealerStore:
    async def list_all(self):
        return [DealerRecord(dealer="komang", emails=["a@test", "b@test"])]


def _client():
    app = FastAPI()
    app.include_router(
        build_escalation_router(
            notifier=None,
            chatwoot_request=None,
            settings=_Settings(),
            pic_store=_PicStore(),
            dealer_store=_DealerStore(),
        )
    )
    return TestClient(app)


def test_lists_pic_cc_and_dealer_addresses():
    res = _client().get("/escalation/contacts", headers={"x-api-key": "secret"})
    assert res.status_code == 200
    by_email = {c["email"]: c for c in res.json()["contacts"]}
    assert by_email["pic@test"]["kind"] == "pic"
    assert by_email["cc@test"]["kind"] == "pic"
    assert by_email["a@test"]["kind"] == "dealer"
    assert by_email["b@test"]["kind"] == "dealer"


def test_requires_api_key():
    assert _client().get("/escalation/contacts").status_code == 401


def test_emails_are_lowercased_and_deduped():
    res = _client().get("/escalation/contacts", headers={"x-api-key": "secret"})
    emails = [c["email"] for c in res.json()["contacts"]]
    assert emails == [e.lower() for e in emails]
    assert len(emails) == len(set(emails))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/chat/test_escalation_contacts.py -v`
Expected: FAIL — `build_escalation_router() got an unexpected keyword argument 'pic_store'`

- [ ] **Step 3: Implement**

`build_escalation_router` gains two optional stores (optional so existing call sites keep working until Step 4 updates them):

```python
def build_escalation_router(
    notifier: EscalationNotifier,
    chatwoot_request: _CWRequest,
    settings: Settings,
    pic_store: PicStore | None = None,
    dealer_store: DealerStore | None = None,
) -> APIRouter:
```

and the endpoint:

```python
    @router.get("/escalation/contacts", dependencies=[Depends(auth)])
    async def contacts() -> dict[str, list[dict[str, str]]]:
        """Every address the escalation mail can reach, for the agent
        service's reply-sender allowlist. Best-effort: a store failure
        yields fewer contacts, never a 5xx -- the agent treats a short list
        as 'sender unknown' and simply does not link the reply.
        """
        seen: set[str] = set()
        out: list[dict[str, str]] = []

        def _add(email: str | None, name: str, kind: str) -> None:
            key = (email or "").strip().lower()
            if not key or key in seen:
                return
            seen.add(key)
            out.append({"email": key, "name": name, "kind": kind})

        if pic_store is not None:
            for rec in await pic_store.list_all():
                _add(rec.pic_email, rec.pic_name, "pic")
                for cc in rec.cc_emails:
                    _add(cc, f"{rec.pic_name} (CC)", "pic")
        if dealer_store is not None:
            for rec in await dealer_store.list_all():
                for member in rec.emails:
                    _add(member, rec.dealer, "dealer")
        return {"contacts": out}
```

- [ ] **Step 4: Pass the stores at the composition root**

Find where `build_escalation_router` is wired (`grep -rn "build_escalation_router" backend/apps/backend/src --include=*.py`) and pass the same `PicStore`/`DealerStore` instances already constructed for `build_pic_admin_router`.

- [ ] **Step 5: Run tests**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/chat/ -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/apps/backend/src/chatbot/features/chat/escalation_router.py \
        backend/apps/backend/src/chatbot/features/chat/test_escalation_contacts.py
git commit -m "feat(backend): GET /escalation/contacts sender allowlist"
```

---

### Task 7: Agent client support — contacts, draft, incoming messages

**Files:**
- Modify: `agent/app/clients/proton.py`
- Modify: `agent/app/clients/chatwoot.py:58-75`
- Test: `agent/tests/test_escalation_reply_clients.py`

**Interfaces:**
- Produces:
  - `ProtonConfigClient.get_escalation_contacts() -> dict[str, str] | None` — lowercase email → display name, `None` on any failure (uncached: the allowlist must reflect an operator edit immediately).
  - `ProtonConfigClient.suggest_reply(conversation_id: str, messages: list[str]) -> str | None`
  - `ChatwootClient.create_message(..., message_type: str | None = None)`

- [ ] **Step 1: Write the failing test**

```python
"""Client support for the escalation reply loop."""

import httpx
import respx

from app.clients.chatwoot import ChatwootClient
from app.clients.proton import ProtonConfigClient

CHATWOOT = "http://chatwoot-rails:3000"
PROTON = "http://proton-backend:8080"


@respx.mock
async def test_get_escalation_contacts_maps_email_to_name():
    respx.get(f"{PROTON}/escalation/contacts").mock(
        return_value=httpx.Response(
            200,
            json={"contacts": [
                {"email": "Pic@Test", "name": "Aduy", "kind": "pic"},
                {"email": "a@test", "name": "komang", "kind": "dealer"},
            ]},
        )
    )
    client = ProtonConfigClient(PROTON, "k")
    assert await client.get_escalation_contacts() == {"pic@test": "Aduy", "a@test": "komang"}
    await client.aclose()


@respx.mock
async def test_get_escalation_contacts_returns_none_on_error():
    respx.get(f"{PROTON}/escalation/contacts").mock(return_value=httpx.Response(500))
    client = ProtonConfigClient(PROTON, "k")
    assert await client.get_escalation_contacts() is None
    await client.aclose()


@respx.mock
async def test_suggest_reply_returns_draft():
    respx.post(f"{PROTON}/assist/suggest").mock(
        return_value=httpx.Response(200, json={"draft": "Dear customer, ...", "sources": []})
    )
    client = ProtonConfigClient(PROTON, "k")
    assert await client.suggest_reply("42", ["hello"]) == "Dear customer, ..."
    await client.aclose()


@respx.mock
async def test_suggest_reply_returns_none_on_error():
    respx.post(f"{PROTON}/assist/suggest").mock(return_value=httpx.Response(503))
    client = ProtonConfigClient(PROTON, "k")
    assert await client.suggest_reply("42", ["hello"]) is None
    await client.aclose()


@respx.mock
async def test_create_message_sends_message_type_when_given():
    route = respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages").mock(
        return_value=httpx.Response(200, json={"id": 7})
    )
    client = ChatwootClient(CHATWOOT, "token", 1)
    await client.create_message(42, "hi", private=False, message_type="incoming")
    assert route.calls.last.request.read().decode().count("incoming") == 1
    await client.aclose()


@respx.mock
async def test_create_message_omits_message_type_by_default():
    route = respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages").mock(
        return_value=httpx.Response(200, json={"id": 7})
    )
    client = ChatwootClient(CHATWOOT, "token", 1)
    await client.create_message(42, "hi")
    assert "message_type" not in route.calls.last.request.read().decode()
    await client.aclose()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && pytest tests/test_escalation_reply_clients.py -v`
Expected: FAIL — `ProtonConfigClient has no attribute 'get_escalation_contacts'`

> If `ChatwootClient.__init__` takes different positional arguments than
> `(base_url, token, account_id)`, read `agent/app/clients/chatwoot.py:1-45`
> and match it — do not change the constructor.

- [ ] **Step 3: Implement the Chatwoot change**

```python
    async def create_message(
        self,
        conversation_id: int,
        content: str,
        private: bool = True,
        token_override: str | None = None,
        content_attributes: dict | None = None,
        message_type: str | None = None,
    ) -> Any:
        """Post a message. `message_type` is normally left unset (Chatwoot
        infers "outgoing" from the API token). The escalation reply linker
        passes "incoming" so a customer's mailed reply reads as the
        customer's own message rather than an agent note -- which also
        reopens the conversation, exactly as a real inbound message would.
        """
        body: dict[str, Any] = {"content": content, "private": private}
        if content_attributes:
            body["content_attributes"] = content_attributes
        if message_type:
            body["message_type"] = message_type
```

- [ ] **Step 4: Implement the Proton client changes**

```python
    async def get_escalation_contacts(self) -> dict[str, str] | None:
        """Lower-cased email -> display name for every escalation contact
        (PIC, PIC CC, dealer group members).

        Deliberately NOT cached: this is a security allowlist, and an
        operator adding a dealer in the admin UI must take effect on the
        next reply, not up to a TTL later. Returns None on any failure so
        the caller can tell "unknown sender" from "could not check".
        """
        try:
            response = await self._client.get("/escalation/contacts")
            response.raise_for_status()
            data = response.json()
        except Exception:
            logger.debug("proton_config: get_escalation_contacts failed", exc_info=True)
            return None
        contacts = (data or {}).get("contacts")
        if not isinstance(contacts, list):
            return None
        out: dict[str, str] = {}
        for entry in contacts:
            if not isinstance(entry, dict):
                continue
            email = str(entry.get("email") or "").strip().lower()
            if email:
                out[email] = str(entry.get("name") or "")
        return out

    async def suggest_reply(
        self, conversation_id: str, messages: list[str]
    ) -> str | None:
        """KB-grounded customer-facing draft (POST /assist/suggest). None on
        any failure -- the reply note is posted regardless; only the draft
        is lost."""
        try:
            response = await self._client.post(
                "/assist/suggest",
                json={"conversation_id": conversation_id, "messages": messages},
            )
            response.raise_for_status()
            draft = (response.json() or {}).get("draft")
        except Exception:
            logger.debug("proton_config: suggest_reply failed", exc_info=True)
            return None
        text = str(draft or "").strip()
        return text or None
```

- [ ] **Step 5: Run tests**

Run: `cd agent && pytest tests/test_escalation_reply_clients.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add agent/app/clients/proton.py agent/app/clients/chatwoot.py \
        agent/tests/test_escalation_reply_clients.py
git commit -m "feat(agent): client support for escalation reply linking"
```

---

### Task 8: Token extraction and quoted-trail stripping

**Files:**
- Create: `agent/app/services/escalation_replies.py`
- Test: `agent/tests/test_escalation_reply_parsing.py`

**Interfaces:**
- Produces:
  - `extract_case_id(message: dict) -> int | None`
  - `strip_quoted_trail(text: str) -> str`

Pure functions, no I/O — test them alone before wiring anything.

- [ ] **Step 1: Write the failing test**

```python
"""Correlation-token extraction and quoted-trail stripping."""

from app.services.escalation_replies import extract_case_id, strip_quoted_trail


def test_extracts_from_to_header():
    msg = {"content_attributes": {"email": {"to": ["support+case42@test"]}}}
    assert extract_case_id(msg) == 42


def test_extracts_from_cc_header():
    msg = {"content_attributes": {"email": {"cc": ["support+case7@test"]}}}
    assert extract_case_id(msg) == 7


def test_falls_back_to_subject_tag():
    msg = {"content_attributes": {"email": {"subject": "Re: [CASE-99] my car"}}}
    assert extract_case_id(msg) == 99


def test_to_header_wins_over_subject():
    msg = {"content_attributes": {
        "email": {"to": ["support+case42@test"], "subject": "Re: [CASE-99] x"}
    }}
    assert extract_case_id(msg) == 42


def test_returns_none_without_a_token():
    assert extract_case_id({"content_attributes": {"email": {"to": ["support@test"]}}}) is None
    assert extract_case_id({}) is None
    assert extract_case_id({"content_attributes": {"email": {"to": "not-a-list"}}}) is None


def test_strips_gmail_style_trail():
    body = "We fixed it.\n\nOn Thu, 6 Aug 2026 at 10:00, Support <s@t> wrote:\n> original\n> more"
    assert strip_quoted_trail(body) == "We fixed it."


def test_strips_outlook_style_trail():
    body = "Parts ordered.\r\n\r\n-----Original Message-----\r\nFrom: Support\r\nsomething"
    assert strip_quoted_trail(body) == "Parts ordered."


def test_strips_leading_quote_block():
    assert strip_quoted_trail("Done.\n\n> quoted line\n> another") == "Done."


def test_leaves_clean_body_untouched():
    assert strip_quoted_trail("Just a reply.") == "Just a reply."


def test_never_returns_empty_when_input_is_only_a_trail():
    body = "On Thu, 6 Aug 2026 at 10:00, Support <s@t> wrote:\n> original"
    assert strip_quoted_trail(body) == body.strip()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && pytest tests/test_escalation_reply_parsing.py -v`
Expected: FAIL — `No module named 'app.services.escalation_replies'`

- [ ] **Step 3: Implement**

```python
"""Link an emailed reply back onto the conversation it was escalated from.

Escalation mail leaves with a correlation token (`Reply-To:
…+case<id>@…` plus a `[CASE-<id>]` subject tag -- see the backend's
EscalationNotifier). The reply comes back through the tenant's ordinary
Email inbox, so Chatwoot files it as a NEW conversation with no connection
to the original. This module puts that connection back: it reads the token,
verifies the sender, copies the reply onto the original conversation, and
resolves the throwaway one.

Fail-open like every other background task here: an unparseable payload, an
unknown sender, or an unreachable backend means the reply is simply not
linked -- logged and skipped, never raised. For the sender check that
posture is also the safe one: refusing to link an unverifiable sender is
what stops someone guessing a conversation id to inject a private note.
"""

import logging
import re

logger = logging.getLogger(__name__)

# `support+case42@host` in a To/Cc header — the primary correlation key.
_ADDRESS_TOKEN = re.compile(r"\+case(\d+)@", re.IGNORECASE)
# `[CASE-42]` in the subject — the fallback for relays that strip
# plus-addressing.
_SUBJECT_TOKEN = re.compile(r"\[CASE-(\d+)\]", re.IGNORECASE)

# Where a mail client starts quoting what it is replying to. Anchored at a
# line start so the words can't match mid-sentence.
_TRAIL_MARKERS = (
    re.compile(r"^On .{0,200}\bwrote:\s*$", re.MULTILINE),
    re.compile(r"^-{2,}\s*Original Message\s*-{2,}\s*$", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^_{5,}\s*$", re.MULTILINE),
    re.compile(r"^From: .+$", re.MULTILINE),
    re.compile(r"^>", re.MULTILINE),
)


def _email_meta(message: dict) -> dict:
    attrs = message.get("content_attributes")
    if not isinstance(attrs, dict):
        return {}
    meta = attrs.get("email")
    return meta if isinstance(meta, dict) else {}


def extract_case_id(message: dict) -> int | None:
    """The escalated conversation id this message is a reply to, or None.

    Checks the To/Cc addresses first (a header the sender cannot edit by
    accident), then the subject tag.
    """
    meta = _email_meta(message)
    for key in ("to", "cc"):
        addresses = meta.get(key)
        if isinstance(addresses, str):
            addresses = [addresses]
        if not isinstance(addresses, list):
            continue
        for address in addresses:
            match = _ADDRESS_TOKEN.search(str(address))
            if match:
                return int(match.group(1))
    match = _SUBJECT_TOKEN.search(str(meta.get("subject") or ""))
    return int(match.group(1)) if match else None


def strip_quoted_trail(text: str) -> str:
    """Drop everything from the first quote marker onward.

    Keeps the reply the sender actually typed, so neither the private note
    nor the AI draft re-ingests the whole thread. If stripping would leave
    nothing (the sender top-quoted with no new text), the original is
    returned instead -- an over-eager strip that silently drops the reply is
    worse than a noisy note.
    """
    cut = len(text)
    for marker in _TRAIL_MARKERS:
        match = marker.search(text)
        if match and match.start() < cut:
            cut = match.start()
    stripped = text[:cut].strip()
    return stripped or text.strip()
```

- [ ] **Step 4: Run tests**

Run: `cd agent && pytest tests/test_escalation_reply_parsing.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent/app/services/escalation_replies.py agent/tests/test_escalation_reply_parsing.py
git commit -m "feat(agent): parse escalation reply correlation tokens"
```

---

### Task 9: Link internal replies (dealer / PIC)

**Files:**
- Modify: `agent/app/services/escalation_replies.py`
- Modify: `agent/app/config.py`
- Modify: `deploy/tenants/example.env`
- Test: `agent/tests/test_escalation_reply_linking.py`

**Interfaces:**
- Consumes: `extract_case_id`, `strip_quoted_trail` (Task 8); `ProtonConfigClient.get_escalation_contacts`, `suggest_reply` (Task 7).
- Produces: `maybe_link_escalation_reply(payload: dict) -> None`.

- [ ] **Step 1: Write the failing test**

```python
"""Linking an internal (dealer/PIC) emailed reply onto the escalated conversation."""

import json

import httpx
import respx

from app.clients.deps import get_proton_config_client
from app.config import get_settings
from app.services import escalation_replies

CHATWOOT = "http://chatwoot-rails:3000"
PROTON = "http://proton-backend:8080"


def _payload(*, to="support+case42@test", sender="a@test", inbox_id=4, message_type="incoming"):
    return {
        "event": "message_created",
        "id": 900,
        "message_type": message_type,
        "content": "We fixed it.\n\nOn Thu, Support <s@t> wrote:\n> original",
        "conversation": {"id": 777},
        "inbox": {"id": inbox_id},
        "sender": {"email": sender, "name": "Komang"},
        "content_attributes": {"email": {"to": [to], "subject": "Re: [CASE-42] x"}},
    }


def _enable(monkeypatch):
    monkeypatch.setattr(get_settings(), "escalation_reply_linking_enabled", True)
    monkeypatch.setattr(get_settings(), "escalation_reply_draft_enabled", False)
    monkeypatch.setattr(get_settings(), "proton_backend_url", PROTON)
    monkeypatch.setattr(get_settings(), "proton_backend_key", "k")
    get_proton_config_client.cache_clear()


def _stub_chatwoot(*, conv_attrs=None):
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/inboxes/4").mock(
        return_value=httpx.Response(200, json={"id": 4, "channel_type": "Channel::Email"})
    )
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/42").mock(
        return_value=httpx.Response(
            200,
            json={"id": 42, "custom_attributes": conv_attrs or {},
                  "meta": {"sender": {"email": "customer@test"}}},
        )
    )
    respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/custom_attributes").mock(
        return_value=httpx.Response(200, json={})
    )
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/labels").mock(
        return_value=httpx.Response(200, json={"payload": []})
    )
    respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/labels").mock(
        return_value=httpx.Response(200, json={})
    )
    respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/777/toggle_status").mock(
        return_value=httpx.Response(200, json={})
    )
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/777/labels").mock(
        return_value=httpx.Response(200, json={"payload": []})
    )
    respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/777/labels").mock(
        return_value=httpx.Response(200, json={})
    )
    respx.get(f"{PROTON}/escalation/contacts").mock(
        return_value=httpx.Response(
            200, json={"contacts": [{"email": "a@test", "name": "Komang", "kind": "dealer"}]}
        )
    )
    return respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )


@respx.mock
async def test_posts_private_note_with_stripped_body(monkeypatch):
    _enable(monkeypatch)
    messages = _stub_chatwoot()

    await escalation_replies.maybe_link_escalation_reply(_payload())

    body = json.loads(messages.calls.last.request.read())
    assert body["private"] is True
    assert "We fixed it." in body["content"]
    assert "> original" not in body["content"]
    assert "Komang" in body["content"]
    get_proton_config_client.cache_clear()


@respx.mock
async def test_stamps_dealer_replied_at_and_labels(monkeypatch):
    _enable(monkeypatch)
    _stub_chatwoot()

    await escalation_replies.maybe_link_escalation_reply(_payload())

    posted = [
        json.loads(c.request.read())
        for c in respx.calls
        if c.request.url.path.endswith("/conversations/42/custom_attributes")
    ]
    assert posted and "dealer_replied_at" in posted[0]["custom_attributes"]
    labelled = [
        json.loads(c.request.read())
        for c in respx.calls
        if c.request.url.path.endswith("/conversations/42/labels")
        and c.request.method == "POST"
    ]
    assert labelled and "dealer_replied" in labelled[0]["labels"]
    get_proton_config_client.cache_clear()


@respx.mock
async def test_skips_unknown_sender(monkeypatch):
    _enable(monkeypatch)
    messages = _stub_chatwoot()

    await escalation_replies.maybe_link_escalation_reply(_payload(sender="stranger@test"))

    assert not messages.called
    get_proton_config_client.cache_clear()


@respx.mock
async def test_skips_when_contacts_unavailable(monkeypatch):
    _enable(monkeypatch)
    messages = _stub_chatwoot()
    respx.get(f"{PROTON}/escalation/contacts").mock(return_value=httpx.Response(500))

    await escalation_replies.maybe_link_escalation_reply(_payload())

    assert not messages.called
    get_proton_config_client.cache_clear()


@respx.mock
async def test_skips_when_already_stamped(monkeypatch):
    _enable(monkeypatch)
    messages = _stub_chatwoot(conv_attrs={"dealer_replied_at": "2026-08-06T00:00:00+00:00"})

    await escalation_replies.maybe_link_escalation_reply(_payload())

    assert not messages.called
    get_proton_config_client.cache_clear()


@respx.mock
async def test_ignores_outgoing_messages(monkeypatch):
    _enable(monkeypatch)
    messages = _stub_chatwoot()

    await escalation_replies.maybe_link_escalation_reply(_payload(message_type="outgoing"))

    assert not messages.called
    get_proton_config_client.cache_clear()


@respx.mock
async def test_noop_when_flag_disabled(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(get_settings(), "escalation_reply_linking_enabled", False)
    messages = _stub_chatwoot()

    await escalation_replies.maybe_link_escalation_reply(_payload())

    assert not messages.called
    get_proton_config_client.cache_clear()


@respx.mock
async def test_noop_without_token(monkeypatch):
    _enable(monkeypatch)
    messages = _stub_chatwoot()
    payload = _payload(to="support@test")
    payload["content_attributes"]["email"]["subject"] = "Re: no tag here"

    await escalation_replies.maybe_link_escalation_reply(payload)

    assert not messages.called
    get_proton_config_client.cache_clear()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && pytest tests/test_escalation_reply_linking.py -v`
Expected: FAIL — `module 'app.services.escalation_replies' has no attribute 'maybe_link_escalation_reply'`

- [ ] **Step 3: Add the settings**

`agent/app/config.py`, near the other escalation settings:

```python
    # Link an emailed reply (dealer/PIC/customer) back onto the conversation
    # it was escalated from. Requires the backend's
    # ESCALATION_REPLY_TO_TEMPLATE to be set, or no mail carries a token.
    escalation_reply_linking_enabled: bool = False
    # Post an AI-drafted customer reply as a second private note alongside a
    # linked internal reply. Never sends anything to the customer.
    escalation_reply_draft_enabled: bool = False
```

`deploy/tenants/example.env`:

```bash
# Escalation reply loop (agent). Both default off.
ESCALATION_REPLY_LINKING_ENABLED=false
ESCALATION_REPLY_DRAFT_ENABLED=false
```

- [ ] **Step 4: Implement**

Append to `agent/app/services/escalation_replies.py`:

```python
from datetime import datetime, timezone

from app.clients.deps import get_chatwoot_client, get_proton_config_client
from app.config import get_settings

_REPLIED_ATTR = "dealer_replied_at"
_REPLIED_LABEL = "dealer_replied"
_ORPHAN_LABEL = "escalation_reply"


async def maybe_link_escalation_reply(payload: dict) -> None:
    """Handle a Chatwoot `message_created` event: if this is an emailed
    reply carrying an escalation token, copy it onto the escalated
    conversation and close the throwaway one it arrived in."""
    settings = get_settings()
    if not settings.escalation_reply_linking_enabled:
        return
    if payload.get("message_type") != "incoming":
        return

    case_id = extract_case_id(payload)
    if case_id is None:
        return

    sender_email = str((payload.get("sender") or {}).get("email") or "").strip().lower()
    if not sender_email:
        return

    reply_conv_id = (payload.get("conversation") or {}).get("id")
    inbox_id = (payload.get("inbox") or {}).get("id")
    if inbox_id is None:
        return

    chatwoot = get_chatwoot_client()
    try:
        inbox = await chatwoot.get_inbox(inbox_id)
        if (inbox or {}).get("channel_type") != "Channel::Email":
            return

        conversation = await chatwoot.get_conversation(case_id)
        if conversation is None:
            logger.info("escalation_replies: conversation %s not found", case_id)
            return
        existing = (conversation or {}).get("custom_attributes") or {}
        if existing.get(_REPLIED_ATTR):
            logger.info(
                "escalation_replies: conversation %s already linked a reply, skipping",
                case_id,
            )
            return

        proton = get_proton_config_client()
        if proton is None:
            return
        contacts = await proton.get_escalation_contacts()
        if contacts is None:
            logger.warning(
                "escalation_replies: contact allowlist unavailable, not linking reply "
                "from %s to conversation %s",
                sender_email,
                case_id,
            )
            return
        if sender_email not in contacts:
            logger.info(
                "escalation_replies: sender %s is not an escalation contact, skipping",
                sender_email,
            )
            return

        text = strip_quoted_trail(str(payload.get("content") or ""))
        if not text:
            return
        sender_name = contacts.get(sender_email) or sender_email
        await chatwoot.create_message(
            case_id,
            f"Reply from {sender_name} <{sender_email}>:\n\n{text}",
            private=True,
        )
        await chatwoot.set_custom_attributes(
            case_id, {_REPLIED_ATTR: datetime.now(timezone.utc).isoformat()}
        )
        await chatwoot.add_labels(case_id, [_REPLIED_LABEL])

        if settings.escalation_reply_draft_enabled:
            await _post_draft(case_id, text)

        if reply_conv_id is not None:
            await chatwoot.add_labels(reply_conv_id, [_ORPHAN_LABEL])
            await chatwoot.toggle_status(reply_conv_id, "resolved")
    except Exception:
        logger.exception(
            "escalation_replies: failed to link reply to conversation %s", case_id
        )


async def _post_draft(case_id: int, reply_text: str) -> None:
    """Post a KB-grounded customer-facing draft as a second private note.

    Best-effort: the linked note above is the deliverable, the draft is a
    convenience. A backend failure logs and returns.
    """
    proton = get_proton_config_client()
    if proton is None:
        return
    draft = await proton.suggest_reply(
        str(case_id), [f"The dealer replied: {reply_text}"]
    )
    if not draft:
        return
    await get_chatwoot_client().create_message(
        case_id,
        f"Suggested customer reply (draft — review before sending):\n\n{draft}",
        private=True,
    )
```

- [ ] **Step 5: Run tests**

Run: `cd agent && pytest tests/test_escalation_reply_linking.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add agent/app/services/escalation_replies.py agent/app/config.py \
        agent/tests/test_escalation_reply_linking.py deploy/tenants/example.env
git commit -m "feat(agent): link dealer/PIC email replies onto the escalated conversation"
```

---

### Task 10: Link customer replies to the acknowledgement

**Files:**
- Modify: `agent/app/services/escalation_replies.py`
- Test: `agent/tests/test_escalation_reply_customer.py`

**Interfaces:**
- Consumes: everything from Task 9.
- Produces: customer-sender branch inside `maybe_link_escalation_reply` — posts a **public incoming** message, no `dealer_replied_at` stamp, no draft.

A customer replying to the ack currently opens an orphan conversation, because the ack is sent by raw SMTP and Chatwoot never knew its Message-ID.

- [ ] **Step 1: Write the failing test**

```python
"""A customer's reply to the escalation acknowledgement lands on the original."""

import json

import httpx
import respx

from app.clients.deps import get_proton_config_client
from app.config import get_settings
from app.services import escalation_replies

CHATWOOT = "http://chatwoot-rails:3000"
PROTON = "http://proton-backend:8080"


def _payload(sender="customer@test"):
    return {
        "event": "message_created",
        "id": 901,
        "message_type": "incoming",
        "content": "Any update?",
        "conversation": {"id": 778},
        "inbox": {"id": 4},
        "sender": {"email": sender, "name": "Jane"},
        "content_attributes": {"email": {"to": ["support+case42@test"]}},
    }


def _enable(monkeypatch):
    monkeypatch.setattr(get_settings(), "escalation_reply_linking_enabled", True)
    monkeypatch.setattr(get_settings(), "escalation_reply_draft_enabled", False)
    monkeypatch.setattr(get_settings(), "proton_backend_url", PROTON)
    monkeypatch.setattr(get_settings(), "proton_backend_key", "k")
    get_proton_config_client.cache_clear()


def _stub():
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/inboxes/4").mock(
        return_value=httpx.Response(200, json={"id": 4, "channel_type": "Channel::Email"})
    )
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/42").mock(
        return_value=httpx.Response(
            200,
            json={"id": 42, "custom_attributes": {},
                  "meta": {"sender": {"email": "customer@test"}}},
        )
    )
    respx.get(f"{PROTON}/escalation/contacts").mock(
        return_value=httpx.Response(
            200, json={"contacts": [{"email": "a@test", "name": "Komang", "kind": "dealer"}]}
        )
    )
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/778/labels").mock(
        return_value=httpx.Response(200, json={"payload": []})
    )
    respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/778/labels").mock(
        return_value=httpx.Response(200, json={})
    )
    respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/778/toggle_status").mock(
        return_value=httpx.Response(200, json={})
    )
    return respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages").mock(
        return_value=httpx.Response(200, json={"id": 2})
    )


@respx.mock
async def test_customer_reply_posts_public_incoming_message(monkeypatch):
    _enable(monkeypatch)
    messages = _stub()

    await escalation_replies.maybe_link_escalation_reply(_payload())

    body = json.loads(messages.calls.last.request.read())
    assert body["private"] is False
    assert body["message_type"] == "incoming"
    assert body["content"] == "Any update?"
    get_proton_config_client.cache_clear()


@respx.mock
async def test_customer_reply_does_not_stamp_dealer_replied_at(monkeypatch):
    _enable(monkeypatch)
    _stub()

    await escalation_replies.maybe_link_escalation_reply(_payload())

    assert not [
        c for c in respx.calls
        if c.request.url.path.endswith("/conversations/42/custom_attributes")
    ]
    get_proton_config_client.cache_clear()


@respx.mock
async def test_customer_reply_still_skipped_when_email_does_not_match(monkeypatch):
    _enable(monkeypatch)
    messages = _stub()

    await escalation_replies.maybe_link_escalation_reply(_payload(sender="stranger@test"))

    assert not messages.called
    get_proton_config_client.cache_clear()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && pytest tests/test_escalation_reply_customer.py -v`
Expected: FAIL — the customer is not in the contact allowlist, so nothing is posted.

- [ ] **Step 3: Implement the sender classification**

In `maybe_link_escalation_reply`, replace the allowlist block with a
classification that also recognises the conversation's own contact. Insert
after `existing` is read and before the contacts lookup:

```python
        contact_email = str(
            ((conversation.get("meta") or {}).get("sender") or {}).get("email") or ""
        ).strip().lower()
        is_customer = bool(contact_email) and sender_email == contact_email
```

then guard the allowlist lookup and the internal-only side effects:

```python
        if not is_customer:
            proton = get_proton_config_client()
            if proton is None:
                return
            contacts = await proton.get_escalation_contacts()
            if contacts is None:
                logger.warning(
                    "escalation_replies: contact allowlist unavailable, not linking "
                    "reply from %s to conversation %s",
                    sender_email,
                    case_id,
                )
                return
            if sender_email not in contacts:
                logger.info(
                    "escalation_replies: sender %s is not an escalation contact, skipping",
                    sender_email,
                )
                return
            sender_name = contacts.get(sender_email) or sender_email
```

and split the post:

```python
        text = strip_quoted_trail(str(payload.get("content") or ""))
        if not text:
            return

        if is_customer:
            # The customer's own words belong in the customer thread as an
            # inbound message, not as an agent note -- which also reopens
            # the conversation exactly as a real inbound message would.
            await chatwoot.create_message(
                case_id, text, private=False, message_type="incoming"
            )
        else:
            await chatwoot.create_message(
                case_id,
                f"Reply from {sender_name} <{sender_email}>:\n\n{text}",
                private=True,
            )
            await chatwoot.set_custom_attributes(
                case_id, {_REPLIED_ATTR: datetime.now(timezone.utc).isoformat()}
            )
            await chatwoot.add_labels(case_id, [_REPLIED_LABEL])
            if settings.escalation_reply_draft_enabled:
                await _post_draft(case_id, text)
```

> The `existing.get(_REPLIED_ATTR)` early return must now apply **only** to
> the internal branch — a customer may reply many times. Move that check
> inside the `else` branch above, keeping its log line unchanged.

- [ ] **Step 4: Run the full reply-loop suite**

Run: `cd agent && pytest tests/test_escalation_reply_customer.py tests/test_escalation_reply_linking.py tests/test_escalation_reply_parsing.py -v`
Expected: PASS. `test_skips_when_already_stamped` from Task 9 must still pass — it uses an internal sender.

- [ ] **Step 5: Commit**

```bash
git add agent/app/services/escalation_replies.py agent/tests/test_escalation_reply_customer.py
git commit -m "feat(agent): link customer ack replies onto the original conversation"
```

---

### Task 11: Wire `message_created` into the webhook router

**Files:**
- Modify: `agent/app/routers/chatwoot.py:52-66`
- Test: `agent/tests/test_webhook_message_created.py`

**Interfaces:**
- Consumes: `escalation_replies.maybe_link_escalation_reply` (Tasks 9-10).

The account webhook has no `message_created` branch today; the event is logged as unhandled.

- [ ] **Step 1: Write the failing test**

```python
"""The account webhook dispatches message_created to the reply linker."""

import hashlib
import hmac
import json
import time

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app


def _signed(body: bytes):
    ts = str(int(time.time()))
    secret = get_settings().chatwoot_webhook_secret
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return {
        "X-Chatwoot-Signature": f"sha256={sig}",
        "X-Chatwoot-Timestamp": ts,
        "X-Chatwoot-Delivery": f"test-{ts}-{hash(body) & 0xFFFF}",
        "Content-Type": "application/json",
    }


def test_message_created_dispatches_to_reply_linker(monkeypatch):
    seen = []

    async def _fake(payload):
        seen.append(payload)

    monkeypatch.setattr(
        "app.services.escalation_replies.maybe_link_escalation_reply", _fake
    )

    body = json.dumps({"event": "message_created", "id": 1}).encode()
    with TestClient(create_app()) as client:
        res = client.post("/webhooks/chatwoot", content=body, headers=_signed(body))

    assert res.status_code == 200
    assert seen and seen[0]["event"] == "message_created"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && pytest tests/test_webhook_message_created.py -v`
Expected: FAIL — `assert seen` fails; the event falls through to the unhandled branch.

> If `create_app` needs extra env, copy the bootstrap from
> `agent/tests/conftest.py` — do not add new env vars for this test.

- [ ] **Step 3: Implement**

In `agent/app/routers/chatwoot.py`, import the module alongside the others:

```python
from app.services import escalation_replies, lifecycle, orchestrator, sync
```

and add the branch after `conversation_created`:

```python
    elif event == "message_created":
        background_tasks.add_task(
            escalation_replies.maybe_link_escalation_reply, payload
        )
```

- [ ] **Step 4: Run the whole agent suite**

Run: `cd agent && pytest`
Expected: PASS — every test, not just the new file.

- [ ] **Step 5: Document the Chatwoot-side subscription**

Append to `deploy/tenants/example.env` beside the reply-loop vars:

```bash
# NOTE: the reply loop also needs `message_created` added to the account
# webhook's subscribed events in Chatwoot (Settings -> Integrations ->
# Webhooks). Without it the agent never sees dealer replies.
```

- [ ] **Step 6: Commit**

```bash
git add agent/app/routers/chatwoot.py agent/tests/test_webhook_message_created.py \
        deploy/tenants/example.env
git commit -m "feat(agent): dispatch message_created to the escalation reply linker"
```

---

### Task 12: Escalation Routing page — groups vocabulary

**Files:**
- Create: `deploy/chatwoot-fork/patches/0046-escalation-groups.patch`

**Interfaces:**
- Consumes: `PUT /admin/escalation/dealers/{dealer}` accepting `emails: list[str]` (Task 5).

Patch `0039` already ships a `splitEmails` helper and a comma-separated CC field — reuse both. Per the memory note, this sandbox cannot clone upstream: build the patch by copying the surrounding context out of `0039-escalation-routing-admin.patch` rather than diffing a fresh checkout.

- [ ] **Step 1: Read the patch you are extending**

```bash
grep -n "cc_emails\|dealerForm\|splitEmails\|upsertDealer" \
  deploy/chatwoot-fork/patches/0039-escalation-routing-admin.patch
```

Note the exact line context around the dealer form, the dealer table row, and `upsertDealer`.

- [ ] **Step 2: Write the patch**

Create `deploy/chatwoot-fork/patches/0046-escalation-groups.patch` changing, in the Escalation Routing page:

1. `EMPTY_DEALER_FORM` — `email: ''` becomes `emails: ''` (comma-separated in the UI, same as `cc_emails`).
2. `submitDealer` / `updateDealer` — send `{ emails: splitEmails(this.dealerForm.emails) }` instead of `{ email: … }`.
3. `startEditDealer` — `emails: Array.isArray(dealer.emails) ? dealer.emails.join(', ') : (dealer.email || '')`, so a row still on the legacy shape edits cleanly.
4. The dealer table cell renders `Array.isArray(dealer.emails) ? dealer.emails.join(', ') : dealer.email`.
5. Labels: the dealer section header becomes **Dealer groups**, its two columns **Group name** and **Members**; the PIC section's CC column becomes **Members (CC)**. Field help text: `Comma-separated. Every member receives the escalation email.`

- [ ] **Step 3: Verify the patch applies cleanly**

```bash
cd deploy/chatwoot-fork && ls patches/
# Dockerfile globs patches/*.patch in filename order — 0046 applies after 0039.
git apply --check --directory=<upstream-checkout> patches/0046-escalation-groups.patch
```

If no upstream checkout is available in this environment, verify by inspection that every context line in the patch matches the corresponding `+` line added by `0039`.

- [ ] **Step 4: Commit**

```bash
git add deploy/chatwoot-fork/patches/0046-escalation-groups.patch
git commit -m "feat(fork): escalation routing page speaks groups and member lists"
```

---

## Phase 2 — The timers

### Task 13: Scope the SLA scan beyond one inbox

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/metrics/sync.py:50-90`
- Modify: `backend/apps/backend/src/chatbot/platform/config.py`
- Modify: `deploy/tenants/example.env`
- Test: `backend/apps/backend/src/chatbot/features/metrics/test_sync_inbox_scope.py`

**Interfaces:**
- Produces: `sla_inbox_ids: str = ""` — comma-separated inbox ids; empty means **every** inbox.

`fetch_conversations` hard-filters to `settings.chatwoot_inbox_id`, so email conversations are invisible to the SLA engine unless that single var happens to point at the Email inbox. Without this task, feedback #2 cannot be satisfied for email at all.

- [ ] **Step 1: Write the failing test**

```python
"""SLA scan inbox scoping: one inbox, several, or all."""

from __future__ import annotations

from chatbot.features.metrics.sync import fetch_conversations


class _Settings:
    chatwoot_api_url = "http://cw"
    chatwoot_account_id = 1
    chatwoot_api_token = "t"
    chatwoot_inbox_id = 2
    sla_inbox_ids = ""


def _recording_get_page(urls: list[str]):
    def _get_page(url: str) -> dict:
        urls.append(url)
        return {"data": {"payload": []}}

    return _get_page


def test_defaults_to_the_single_configured_inbox():
    urls: list[str] = []
    settings = _Settings()
    fetch_conversations(settings, get_page=_recording_get_page(urls))
    assert "inbox_id=2" in urls[0]


def test_scans_each_listed_inbox():
    urls: list[str] = []
    settings = _Settings()
    settings.sla_inbox_ids = "2, 4"
    fetch_conversations(settings, get_page=_recording_get_page(urls))
    assert any("inbox_id=2" in u for u in urls)
    assert any("inbox_id=4" in u for u in urls)


def test_all_inboxes_when_explicitly_set_to_star():
    urls: list[str] = []
    settings = _Settings()
    settings.sla_inbox_ids = "*"
    fetch_conversations(settings, get_page=_recording_get_page(urls))
    assert all("inbox_id=" not in u for u in urls)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/metrics/test_sync_inbox_scope.py -v`
Expected: FAIL — `sla_inbox_ids` is ignored; the star case still filters.

> Read `fetch_conversations`'s existing paging loop first and match the
> response shape your `_get_page` stub returns to what the real code reads.
> Adjust the stub, not the production paging logic.

- [ ] **Step 3: Add the setting**

`backend/.../platform/config.py`, beside the other `sla_*` values:

```python
    # Inboxes the SLA engine scans. Empty (default) = the single
    # chatwoot_inbox_id, preserving pre-existing behavior exactly. A
    # comma-separated list scans each. "*" scans every inbox in the account
    # -- needed for the email escalation timers, since the Email inbox is
    # normally not chatwoot_inbox_id.
    sla_inbox_ids: str = ""
```

`deploy/tenants/example.env`:

```bash
# SLA engine inbox scope: blank = CHATWOOT_INBOX_ID only, "4,5" = those
# inboxes, "*" = all. Email timers need the Email inbox listed here.
SLA_INBOX_IDS=
```

- [ ] **Step 4: Implement**

Refactor the body of `fetch_conversations` so the existing paging loop
becomes a helper called once per scope, leaving the loop itself untouched:

```python
def _inbox_scope(settings: Settings) -> list[int | None]:
    """Inbox ids to scan. `[None]` means 'no inbox filter' (all inboxes)."""
    raw = (getattr(settings, "sla_inbox_ids", "") or "").strip()
    if not raw:
        return [settings.chatwoot_inbox_id]
    if raw == "*":
        return [None]
    ids: list[int | None] = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            ids.append(int(part))
    return ids or [settings.chatwoot_inbox_id]
```

and build each page URL with the filter omitted when the scope entry is
`None`:

```python
        filter_part = "" if inbox_id is None else f"&inbox_id={inbox_id}"
        url = f"{base}?status=all{filter_part}&page={page_num}"
```

Accumulate results across scope entries into the same `conversations` list.

- [ ] **Step 5: Run tests**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/metrics/ -v`
Expected: PASS, including the pre-existing sync tests.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/backend/src/chatbot/features/metrics/sync.py \
        backend/apps/backend/src/chatbot/features/metrics/test_sync_inbox_scope.py \
        backend/apps/backend/src/chatbot/platform/config.py deploy/tenants/example.env
git commit -m "feat(backend): SLA scan scope across multiple inboxes"
```

---

### Task 14: Give the alert callback per-conversation labels

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/chat/sla.py:280-400`
- Test: `backend/apps/backend/src/chatbot/features/chat/test_sla_alert_labels.py`

**Interfaces:**
- Produces: alert callback signature becomes
  `Callable[[str, str, str, list[str]], Any]` — `(ticket_id, to_state, remark, labels)`.

The alert closure is built once per scan and has no per-conversation context; PIC routing needs the conversation's `dept_<slug>` label.

- [ ] **Step 1: Write the failing test**

```python
"""The SLA alert callback receives the conversation's labels."""

from __future__ import annotations

from chatbot.features.chat.sla import _fire


class _Audit:
    def __init__(self) -> None:
        self.entries: list = []

    async def append(self, entry):
        self.entries.append(entry)

    async def list_for_ticket(self, ticket_id):
        return list(self.entries)


async def test_fire_passes_labels_to_the_alert():
    from datetime import UTC, datetime

    seen: list = []

    async def _alert(ticket_id, to_state, remark, labels):
        seen.append((ticket_id, to_state, labels))

    await _fire(
        _Audit(),
        ticket_id="42",
        session_id="s",
        to_state="SLA_BREACH_NO_RESPONSE",
        remark="r",
        clock=datetime.now(UTC),
        alert=_alert,
        labels=["dept_sales", "escalate"],
    )

    assert seen == [("42", "SLA_BREACH_NO_RESPONSE", ["dept_sales", "escalate"])]


async def test_fire_survives_an_alert_that_raises():
    from datetime import UTC, datetime

    async def _alert(ticket_id, to_state, remark, labels):
        raise RuntimeError("twilio down")

    entry = await _fire(
        _Audit(),
        ticket_id="42",
        session_id="s",
        to_state="SLA_BREACH_UNRESOLVED",
        remark="r",
        clock=datetime.now(UTC),
        alert=_alert,
        labels=[],
    )

    assert entry.to_state == "SLA_BREACH_UNRESOLVED"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/chat/test_sla_alert_labels.py -v`
Expected: FAIL — `_fire() got an unexpected keyword argument 'labels'`

- [ ] **Step 3: Implement**

`_fire` gains a keyword-only `labels: list[str]` and passes it through:

```python
async def _fire(
    audit: AuditLogPort,
    *,
    ticket_id: str,
    session_id: str,
    to_state: str,
    remark: str,
    clock: datetime,
    alert: Callable[[str, str, str, list[str]], Any] | None,
    labels: list[str],
) -> AuditEntry:
```

and at the call site inside it:

```python
            result = alert(ticket_id, to_state, remark, labels)
```

Update all three `_fire(...)` call sites in `scan_conversations` (lines ~295,
~311, ~393) to pass `labels=_labels(conv)` — `_labels` already exists at
line 402. Update `_build_pic_alert`'s inner `_alert` to accept the fourth
argument (ignore it for now — Task 15 uses it), and update the type
annotations on `scan_conversations`'s `alert` parameter.

Leave `level2_alert` on its three-argument shape; it is fired outside `_fire`.

- [ ] **Step 4: Run tests**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/chat/ -k sla -v`
Expected: PASS, including the pre-existing SLA tests.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/backend/src/chatbot/features/chat/sla.py \
        backend/apps/backend/src/chatbot/features/chat/test_sla_alert_labels.py
git commit -m "refactor(backend): SLA alert callback receives conversation labels"
```

---

### Task 15: Email + conversation-note delivery for SLA alerts

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/chat/sla.py:467-490`
- Modify: `backend/apps/backend/src/chatbot/platform/config.py`
- Modify: `deploy/tenants/example.env`
- Test: `backend/apps/backend/src/chatbot/features/chat/test_sla_alert_delivery.py`

**Interfaces:**
- Consumes: the four-argument alert callback (Task 14); `PicRegistry.lookup` (existing); `SmtpEmailSender.send` (existing).
- Produces: `_build_pic_alert(settings, twilio_adapter, *, pic_registry=None, email_sender=None, note_poster=None)`.

- [ ] **Step 1: Write the failing test**

```python
"""SLA alerts reach the department PIC group by email and the conversation as a note."""

from __future__ import annotations

from chatbot.features.chat.pic_registry import PicEntry
from chatbot.features.chat.sla import _build_pic_alert


class _Settings:
    sla_pic_whatsapp = ""
    sla_alert_email_enabled = True
    sla_alert_note_enabled = True


class _Registry:
    async def lookup(self, dept):
        if dept != "sales":
            return None
        return PicEntry(
            department="sales", name="Aduy", email="pic@test", whatsapp="", cc_emails=["cc@test"]
        )


class _Sender:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def send(self, to, cc, subject, body, attachments, *, reply_to=None):
        self.calls.append({"to": to, "cc": cc, "subject": subject})


class _Notes:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, conv_id: str, text: str) -> None:
        self.calls.append((conv_id, text))


async def test_emails_the_department_group_and_posts_a_note():
    sender, notes = _Sender(), _Notes()
    alert = _build_pic_alert(
        _Settings(), None, pic_registry=_Registry(), email_sender=sender, note_poster=notes
    )

    await alert("42", "SLA_BREACH_NO_RESPONSE", "no first reply after 8h", ["dept_sales"])

    assert sender.calls[0]["to"] == ["pic@test"]
    assert sender.calls[0]["cc"] == ["cc@test"]
    assert "42" in sender.calls[0]["subject"]
    assert notes.calls[0][0] == "42"
    assert "SLA" in notes.calls[0][1]


async def test_posts_note_even_when_department_is_unmapped():
    sender, notes = _Sender(), _Notes()
    alert = _build_pic_alert(
        _Settings(), None, pic_registry=_Registry(), email_sender=sender, note_poster=notes
    )

    await alert("42", "SLA_BREACH_UNRESOLVED", "still open", ["dept_unknown"])

    assert sender.calls == []
    assert notes.calls and notes.calls[0][0] == "42"


async def test_disabled_flags_produce_no_alert_at_all():
    settings = _Settings()
    settings.sla_alert_email_enabled = False
    settings.sla_alert_note_enabled = False
    assert (
        _build_pic_alert(settings, None, pic_registry=_Registry(), email_sender=_Sender())
        is None
    )


async def test_email_failure_does_not_stop_the_note():
    class _Boom(_Sender):
        def send(self, *a, **kw):
            raise RuntimeError("smtp down")

    notes = _Notes()
    alert = _build_pic_alert(
        _Settings(), None, pic_registry=_Registry(), email_sender=_Boom(), note_poster=notes
    )

    await alert("42", "SLA_BREACH_NO_RESPONSE", "r", ["dept_sales"])

    assert notes.calls
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/chat/test_sla_alert_delivery.py -v`
Expected: FAIL — `_build_pic_alert() got an unexpected keyword argument 'pic_registry'`

- [ ] **Step 3: Add the settings**

```python
    # SLA breach/reminder delivery beyond the WhatsApp ping. Email goes to
    # the department PIC group resolved from the conversation's dept_<slug>
    # label; the note is a private message on the conversation itself.
    sla_alert_email_enabled: bool = False
    sla_alert_note_enabled: bool = False
```

`deploy/tenants/example.env`:

```bash
SLA_ALERT_EMAIL_ENABLED=false
SLA_ALERT_NOTE_ENABLED=false
```

- [ ] **Step 4: Implement**

```python
_DEPT_LABEL_PREFIX = "dept_"


def _build_pic_alert(
    settings: Settings,
    twilio_adapter: TwilioChannelAdapter | None,
    *,
    pic_registry: PicRegistry | None = None,
    email_sender: SmtpEmailSender | None = None,
    note_poster: Callable[[str, str], Any] | None = None,
) -> Callable[[str, str, str, list[str]], Any] | None:
    """Build the SLA alert callback: WhatsApp ping, PIC-group email, and a
    private note on the conversation.

    Returns None when no leg is configured, so the scan records the audit
    transition and attempts nothing. Every leg is independent and
    best-effort -- one failing must not suppress the others, because these
    are the only signals an operator gets that a case is breaching.
    """
    pic_number = settings.sla_pic_whatsapp
    wa_to = "whatsapp:" + pic_number.removeprefix("whatsapp:") if pic_number else ""
    want_wa = bool(wa_to) and twilio_adapter is not None
    want_email = bool(settings.sla_alert_email_enabled) and email_sender is not None
    want_note = bool(settings.sla_alert_note_enabled) and note_poster is not None
    if not (want_wa or want_email or want_note):
        return None

    async def _alert(ticket_id: str, to_state: str, remark: str, labels: list[str]) -> None:
        text = f"⚠️ SLA breach ({to_state}) on case {ticket_id}. {remark}"

        if want_wa:
            try:
                await twilio_adapter.send_message(conversation_id=wa_to, text=text)
            except Exception as e:
                _log.warning("sla_alert_wa_failed", ticket_id=ticket_id, error=str(e))

        if want_email and pic_registry is not None:
            department = next(
                (
                    lbl[len(_DEPT_LABEL_PREFIX):]
                    for lbl in labels
                    if lbl.startswith(_DEPT_LABEL_PREFIX)
                ),
                None,
            )
            pic = await pic_registry.lookup(department) if department else None
            if pic is None:
                _log.info("sla_alert_no_pic_for_dept", ticket_id=ticket_id, department=department)
            else:
                try:
                    email_sender.send(
                        to=[pic.email],
                        cc=list(pic.cc_emails or []),
                        subject=f"[SLA] {to_state} on case {ticket_id}",
                        body=f"{remark}\n\nReference: Chatwoot conversation #{ticket_id}",
                        attachments=[],
                    )
                except Exception as e:
                    _log.warning("sla_alert_email_failed", ticket_id=ticket_id, error=str(e))

        if want_note:
            try:
                result = note_poster(ticket_id, text)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                _log.warning("sla_alert_note_failed", ticket_id=ticket_id, error=str(e))

    return _alert
```

- [ ] **Step 5: Wire the new dependencies at the call site**

In `run_sla_scan_job`, accept and forward the three optional collaborators:

```python
def run_sla_scan_job(
    settings: Settings,
    audit: AuditLogPort,
    *,
    twilio_adapter: TwilioChannelAdapter | None = None,
    policy_repo: SlaPolicyRepository | None = None,
    pic_registry: PicRegistry | None = None,
    email_sender: SmtpEmailSender | None = None,
    note_poster: Callable[[str, str], Any] | None = None,
) -> list[AuditEntry]:
```

passing them into `_build_pic_alert`. Then find the scheduler wiring
(`grep -rn "run_sla_scan_job\|start_sla_scheduler" backend/apps/backend/src --include=*.py`)
and pass the `PicRegistry` and `SmtpEmailSender` already constructed for the
escalation notifier, plus a `note_poster` that posts a private Chatwoot
message on the conversation via the same adapter method the notifier uses.

- [ ] **Step 6: Run tests**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/chat/ -k sla -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/apps/backend/src/chatbot/features/chat/sla.py \
        backend/apps/backend/src/chatbot/features/chat/test_sla_alert_delivery.py \
        backend/apps/backend/src/chatbot/platform/config.py deploy/tenants/example.env
git commit -m "feat(backend): SLA alerts reach the PIC group by email and the conversation"
```

---

### Task 16: Expose tier-2 and warning thresholds in the SLA admin

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/chat/sla_policy_db.py`
- Modify: `backend/apps/backend/src/chatbot/features/chat/sla_policy_repository.py:13-20`
- Modify: `backend/apps/backend/src/chatbot/features/chat/sla_policy_router.py:26-40`
- Modify: `backend/apps/backend/src/chatbot/features/chat/sla.py`
- Create: `deploy/chatwoot-fork/patches/0047-sla-policy-thresholds.patch`
- Test: `backend/apps/backend/src/chatbot/features/chat/test_sla_policy_thresholds.py`

**Interfaces:**
- Produces: `SlaPolicyValues` gains `tier2_hours: float | None` and `reminder_warning_minutes: float | None`; both resolve store → env exactly as `response_hours` does.

Raphael asked for the 24h/48h thresholds by name; response/resolution hours are already editable, these two are not.

- [ ] **Step 1: Write the failing test**

```python
"""tier2_hours / reminder_warning_minutes are operator-editable, env-backed."""

from __future__ import annotations

from chatbot.features.chat.sla_policy_db import SlaPolicyValues


def test_values_carry_the_two_new_fields():
    v = SlaPolicyValues(tier2_hours=6.0, reminder_warning_minutes=90.0)
    assert v.tier2_hours == 6.0
    assert v.reminder_warning_minutes == 90.0


def test_fields_default_to_none_meaning_inherit_env():
    v = SlaPolicyValues()
    assert v.tier2_hours is None
    assert v.reminder_warning_minutes is None
```

Add a resolution test mirroring the existing one for `response_hours` (find
it with `grep -rn "response_hours" backend/apps/backend/src/chatbot/features/chat/test_*.py`)
so store-set beats env and store-unset falls back to
`settings.escalation_tier2_hours` / `settings.tasks_reminder_warning_minutes`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/chat/test_sla_policy_thresholds.py -v`
Expected: FAIL — `SlaPolicyValues() got an unexpected keyword argument 'tier2_hours'`

- [ ] **Step 3: Implement the backend chain**

Add both columns to the `SlaPolicy` model and both fields to
`SlaPolicyValues` in `sla_policy_db.py` (nullable floats, default `None`).
Add both names to `_FIELDS` in `sla_policy_repository.py`. Add both to
`SlaPolicyBody` and `_to_dict` in `sla_policy_router.py`. In `sla.py`, where
the resolved policy already overrides `response_hours`/`resolution_hours`,
apply the same store-then-env resolution for the tier-2 threshold and the
reminder-warning window.

> There is no Alembic in this repo — the tables are created by
> `Base.metadata.create_all`. Adding a column to an existing deployed
> database therefore needs a one-line `ALTER TABLE` in the deploy notes.
> Record it in the Task 19 deploy checklist; do not add a migration
> framework.

- [ ] **Step 4: Write the fork patch**

`deploy/chatwoot-fork/patches/0047-sla-policy-thresholds.patch` adds two
number inputs to the SLA Policies admin page introduced by patch `0025`,
labelled **Tier-2 re-alert after (hours)** and **Warn before breach
(minutes)**, bound to the two new body fields, with the same
blank-means-inherit affordance the existing fields use. Build it by copying
context out of `0025-sla-policies-admin.patch`.

- [ ] **Step 5: Run tests**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/chat/ -k sla -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/apps/backend/src/chatbot/features/chat/sla_policy_db.py \
        backend/apps/backend/src/chatbot/features/chat/sla_policy_repository.py \
        backend/apps/backend/src/chatbot/features/chat/sla_policy_router.py \
        backend/apps/backend/src/chatbot/features/chat/sla.py \
        backend/apps/backend/src/chatbot/features/chat/test_sla_policy_thresholds.py \
        deploy/chatwoot-fork/patches/0047-sla-policy-thresholds.patch
git commit -m "feat: tier-2 and warning thresholds editable in SLA policy admin"
```

---

## Phase 3 — The small asks

### Task 17: Show the assigned agent in the lists

**Files:**
- Create: `deploy/chatwoot-fork/patches/0048-assignee-visible.patch`

Jinny could only see an avatar. Native Chatwoot shows the assignee avatar on the card and the name in the header; the custom Cases list has no agent column at all.

- [ ] **Step 1: Locate the two insertion points**

```bash
grep -n "Car Plate\|<th class=\"px-3 py-2" deploy/chatwoot-fork/patches/0043-cases-list.patch
grep -rn "assignee" deploy/chatwoot-fork/patches/*.patch
```

The Cases list table header block is the anchor for the new column; the
conversation card component is upstream and must be located in the built
image (`app/javascript/dashboard/components/widgets/conversation/ConversationCard.vue`).

- [ ] **Step 2: Write the patch**

Add to `deploy/chatwoot-fork/patches/0048-assignee-visible.patch`:

1. Cases list — an `<th>Agent</th>` between **Status** and the preceding
   column, and the matching `<td>` rendering
   `row.meta?.assignee?.name || '—'`. If the Cases list's row objects do not
   carry `meta`, extend the row mapper in the same patch to copy
   `assignee_name` from the conversation payload; do not fetch per row.
2. Conversation card — the assignee name as a truncated text label beside
   the existing avatar, rendered only when an assignee exists. Keep this
   edit to a single added element so future upstream rebases stay cheap.

- [ ] **Step 3: Verify by inspection**

Confirm every context line matches what `0043` adds, and that the patch
applies after it in filename order.

- [ ] **Step 4: Commit**

```bash
git add deploy/chatwoot-fork/patches/0048-assignee-visible.patch
git commit -m "feat(fork): show the assigned agent in the cases list and conversation card"
```

---

### Task 18: Operator-editable email acknowledgement templates

**Files:**
- Modify: `agent/app/clients/proton.py`
- Modify: `agent/app/services/lifecycle.py:209-232`
- Modify: `backend/apps/backend/src/chatbot/features/chat/escalation_notifier.py`
- Create: `deploy/chatwoot-fork/patches/0049-email-templates.patch`
- Test: `agent/tests/test_email_autoack_template_source.py`

**Interfaces:**
- Consumes: the tenant-settings store behind `/kb/settings` (`get_effective_value`).
- Produces: `ProtonConfigClient.get_email_autoack_template() -> str | None`.

Both templates are env-only today, so an operator cannot change the words Proton's customers actually receive.

- [ ] **Step 1: Write the failing test**

```python
"""The email auto-ack body comes from the tenant store, with env as fallback."""

import httpx
import respx

from app.clients.deps import get_proton_config_client
from app.clients.proton import ProtonConfigClient
from app.config import get_settings

PROTON = "http://proton-backend:8080"


@respx.mock
async def test_returns_store_value():
    respx.get(f"{PROTON}/kb/settings").mock(
        return_value=httpx.Response(
            200, json={"email_autoack_template": {"value": "Stored body", "source": "override"}}
        )
    )
    client = ProtonConfigClient(PROTON, "k")
    assert await client.get_email_autoack_template() == "Stored body"
    await client.aclose()


@respx.mock
async def test_returns_none_when_unset_so_caller_uses_env():
    respx.get(f"{PROTON}/kb/settings").mock(return_value=httpx.Response(200, json={}))
    client = ProtonConfigClient(PROTON, "k")
    assert await client.get_email_autoack_template() is None
    await client.aclose()


@respx.mock
async def test_returns_none_on_backend_error():
    respx.get(f"{PROTON}/kb/settings").mock(return_value=httpx.Response(500))
    client = ProtonConfigClient(PROTON, "k")
    assert await client.get_email_autoack_template() is None
    await client.aclose()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && pytest tests/test_email_autoack_template_source.py -v`
Expected: FAIL — no attribute `get_email_autoack_template`

> Read how `get_assistant_messages` reads `/kb/settings` in
> `agent/app/clients/proton.py` and reuse its cached fetch helper rather
> than issuing a second uncached request.

- [ ] **Step 3: Implement the agent side**

Add `get_email_autoack_template` to `ProtonConfigClient` following the
existing `/kb/settings` accessor pattern (cached, returns `None` on any
failure or when the key is absent/blank).

In `lifecycle.py`, the email branch prefers the store and falls back to env:

```python
    if channel_type == "Channel::Email":
        if not settings.email_autoack_enabled:
            return
        text = settings.email_autoack_template
        proton = get_proton_config_client()
        if proton is not None:
            stored = await proton.get_email_autoack_template()
            if stored:
                text = stored
        if not text:
            logger.warning(
                "lifecycle: email_autoack_enabled but template is empty for conversation %s; "
                "nothing posted",
                conversation_id,
            )
```

- [ ] **Step 4: Implement the backend side**

In `escalation_notifier.py`, `_send_customer_ack` resolves
`email_escalation_ack_template` through the same tenant-settings
`get_effective_value` helper the assist router already uses, falling back to
`self._settings.email_escalation_ack_template`. Keep the method
non-async-safe: if resolution requires an await, make `_send_customer_ack`
async and await it at its single call site.

- [ ] **Step 5: Write the fork patch**

`deploy/chatwoot-fork/patches/0049-email-templates.patch` adds two textareas
to the Knowledge Settings messages tab (patch `0022`'s page): **Email
auto-acknowledgement** and **Escalation acknowledgement**, persisted through
the existing `/kb/settings` PUT the page already uses for its other fields.
Empty means "use the deployed default".

- [ ] **Step 6: Run tests**

Run: `cd agent && pytest`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add agent/app/clients/proton.py agent/app/services/lifecycle.py \
        agent/tests/test_email_autoack_template_source.py \
        backend/apps/backend/src/chatbot/features/chat/escalation_notifier.py \
        deploy/chatwoot-fork/patches/0049-email-templates.patch
git commit -m "feat: email acknowledgement templates editable from the CRM"
```

---

### Task 19: Enable and verify WhatsApp voice notes

**Files:**
- Modify: `deploy/tenants/example.env`
- Modify: `docs/testing/2026-08-06-escalation-email-e2e-scenario.md`

Expected to be zero application code: the multimodal path exists behind
`whatsapp_media_understanding_enabled`, which defaults to `False`.

- [ ] **Step 1: Enable the flag on the proton tenant**

```bash
# On the VM, in /opt/platform/deploy/tenants/proton.env
WHATSAPP_MEDIA_UNDERSTANDING_ENABLED=true
```

Restart: `docker compose -p proton -f docker-compose.tenant.yml --env-file tenants/proton.env up -d agent`

- [ ] **Step 2: Send a real voice note and observe**

From a WhatsApp number on the tenant's inbox, send a voice note asking a
question the KB can answer (e.g. charging time). Then:

```bash
docker logs proton-agent --since 5m | grep -i "attachment\|audio\|media"
```

Confirm: the attachment is fetched with mime `audio/ogg`, the turn reaches
Gemini, and the reply addresses the spoken question.

- [ ] **Step 3: Confirm the agent-side rendering**

In the CRM, open the conversation and confirm Chatwoot renders an audio
player for the inbound note.

- [ ] **Step 4: Record the result as TC-10**

Add to the E2E doc a **TC-10 — WhatsApp voice note** case with the steps
above and the observed result. If any step fails, file it as a separate bug
task with the log excerpt — do not fix it inside this task.

- [ ] **Step 5: Document the flag**

In `deploy/tenants/example.env`, ensure the flag has a comment naming what
it enables and its cost implication (audio is sent inline to Gemini within
the per-turn byte budget).

- [ ] **Step 6: Commit**

```bash
git add deploy/tenants/example.env docs/testing/2026-08-06-escalation-email-e2e-scenario.md
git commit -m "docs(testing): voice-note enablement and TC-10"
```

---

## Phase 4 — Ship it

### Task 20: Extend the E2E script with the new cases

**Files:**
- Modify: `docs/testing/2026-08-06-escalation-email-e2e-scenario.md`

This doc is also the deliverable Nazatul asked for — "you just give us the script."

- [ ] **Step 1: Add TC-07 — dealer reply links back**

```markdown
### TC-07 — Dealer reply returns to the case

**Preconditions:** `ESCALATION_REPLY_TO_TEMPLATE` set, `ESCALATION_REPLY_LINKING_ENABLED=true`,
`ESCALATION_REPLY_DRAFT_ENABLED=true`, and `message_created` subscribed on the account webhook.

**Steps**
1. Run TC-02 to produce a dealer forward.
2. From the dealer mailbox, reply to that mail without editing the subject.
3. Wait for the IMAP poll (1–2 min).

**Expected**
- Conversation #N gains a private note `Reply from <dealer> <email>:` with the
  dealer's text and **no** quoted trail.
- A second private note `Suggested customer reply (draft — review before sending)`.
- Conversation #N gains the `dealer_replied` label and a `dealer_replied_at` attribute.
- The conversation created by the dealer's reply is labelled `escalation_reply` and resolved.

**Pass criteria:** both notes present on #N, labels/attribute set, reply conversation resolved.
```

- [ ] **Step 2: Add TC-08 — customer reply to the acknowledgement**

```markdown
### TC-08 — Customer replies to the acknowledgement

**Steps**
1. Run TC-01. From the customer mailbox, reply to the `Update on your case:` mail.
2. Wait for the IMAP poll.

**Expected**
- The reply appears on conversation #N as an **incoming customer message** (not a private note).
- #N reopens.
- No `dealer_replied_at` stamp is written.

**Pass criteria:** message visible on #N as the customer, conversation reopened.
```

- [ ] **Step 3: Add TC-09 — SLA reminder delivery**

```markdown
### TC-09 — SLA reminder reaches the PIC group

**Preconditions:** `SLA_ENGINE_ENABLED=true`, `SLA_ALERT_EMAIL_ENABLED=true`,
`SLA_ALERT_NOTE_ENABLED=true`, the Email inbox id listed in `SLA_INBOX_IDS`,
and a short `response_hours` set on the Email inbox at CRM → SLA Policies
(e.g. 0.05 ≈ 3 min) so the case breaches during the test.

**Steps**
1. Send a new email to the Email inbox; apply `dept_sales`; do not reply.
2. Wait for one SLA scan interval past the threshold.

**Expected**
- `pic@…` receives `[SLA] SLA_BREACH_NO_RESPONSE on case <id>`, CC to the group members.
- The conversation gains a private note naming the breach.
- Re-running the scan does **not** duplicate either (audit-trail dedup).

**Pass criteria:** one email, one note, no duplicates on rescan.

> Reset `response_hours` afterwards.
```

- [ ] **Step 4: Update §2 of the doc with the new settings**

Add every new env var from Tasks 4, 9, 13, 15 to the "Configuration as
deployed" table with its demo value.

- [ ] **Step 5: Commit**

```bash
git add docs/testing/2026-08-06-escalation-email-e2e-scenario.md
git commit -m "docs(testing): TC-07..TC-09 for the reply loop and SLA reminders"
```

---

### Task 21: Build, deploy, verify

**Files:**
- Modify: `docs/superpowers/plans/2026-08-07-proton-feedback-followups.md` (check off the deploy log)

Four fork patches were added (`0046`–`0049`). They batch into **one** image build.

- [ ] **Step 1: Run the full test suite on both services**

```bash
cd agent && pytest
cd ../backend/apps/backend && uv run pytest
```

Expected: PASS. Do not deploy on a red suite.

- [ ] **Step 2: Build the Chatwoot image via Cloud Build**

```bash
gcloud builds submit deploy/chatwoot-fork/ \
  --config deploy/chatwoot-fork/cloudbuild.yaml \
  --substitutions _REGISTRY=<AR repo>
```

Never build this image on the VM or on a local Mac — an `arm64` image fails
the VM's `amd64` pull with "no matching manifest".

- [ ] **Step 3: Apply the SLA policy column addition**

Task 16 added columns to a table created by `Base.metadata.create_all`,
which does not alter existing tables:

```sql
ALTER TABLE sla_policies ADD COLUMN IF NOT EXISTS tier2_hours DOUBLE PRECISION;
ALTER TABLE sla_policies ADD COLUMN IF NOT EXISTS reminder_warning_minutes DOUBLE PRECISION;
```

Confirm the real table and column names against `sla_policy_db.py` before running.

- [ ] **Step 4: Deploy agent + backend, then Chatwoot**

```bash
# sync source to /opt/platform first, then on the VM:
docker compose -p proton -f docker-compose.tenant.yml --env-file tenants/proton.env \
  up -d --build backend agent
docker compose -p proton -f docker-compose.tenant.yml --env-file tenants/proton.env pull
docker compose -p proton -f docker-compose.tenant.yml --env-file tenants/proton.env \
  up -d --force-recreate chatwoot-rails chatwoot-sidekiq
```

- [ ] **Step 5: Set the new env vars and subscribe the webhook**

Set on `tenants/proton.env`: `ESCALATION_REPLY_TO_TEMPLATE`,
`ESCALATION_REPLY_LINKING_ENABLED=true`, `ESCALATION_REPLY_DRAFT_ENABLED=true`,
`SLA_ENGINE_ENABLED=true`, `SLA_ALERT_EMAIL_ENABLED=true`,
`SLA_ALERT_NOTE_ENABLED=true`, `SLA_INBOX_IDS=<email inbox id>`,
`WHATSAPP_MEDIA_UNDERSTANDING_ENABLED=true`.

In Chatwoot → Settings → Integrations → Webhooks, add **`message_created`**
to the account webhook's subscribed events.

Leave `default` and `wahchan` untouched.

- [ ] **Step 6: Run the full E2E script**

Execute TC-01 … TC-10. Record results in the doc's execution log. Every case
must pass before the access details go to Proton.

- [ ] **Step 7: Commit and push**

```bash
git add -A
git commit -m "chore: deploy reply loop, SLA reminders, and admin surfaces to proton"
git push origin dev-yuda
```

---

### Task 22: Channel interaction guide, version 2

**Files:**
- Create: `docs/analysis/2026-08-08-crm-channel-interaction-guide-v2.md`
- Read (do not modify): `docs/analysis/crm-channel-interaction-guide.md`, `docs/analysis/crm-channel-ui-testing-guide.md`, `docs/analysis/crm-process-flow-runbook.md`, `docs/testing/phone-channel-package-c-verification.md`, `docs/client-materials/feature-guide-src/*.md`

No dependency on Task 21 — this can run before or after the deploy.

The existing guide (`crm-channel-interaction-guide.md`, dated 2026-08-04) is
organised around WhatsApp / Social / Email / Phone and is written as prose
walkthroughs. It is **superseded but not deleted**: it stays as the historical
record of what was true on 2026-08-04. Version 2 is a new file covering the
four channels the customer actually asks about — **Web chatbot, Voice bot,
Phone, and Email** — in numbered, click-by-click steps an agent can follow
without prior context, plus WhatsApp (still the most-live channel) and the
cross-channel view.

**Why a v2 rather than an edit:** three things changed under the old guide.
Email escalation is now a working two-thread flow with a **reply loop** (Tasks
3-12) the old guide predates entirely; dealer routing is now **group-based**
(Task 5); and the SLA timers now **notify by email and in-conversation note**
(Tasks 13-16). An agent following the 08-04 guide today would be told email
escalation is "built but not configured" and would have no idea a dealer's
reply comes back into the case.

- [ ] **Step 1: Establish the status of every claim before writing a line**

Every "click here and X happens" in this guide must be true of the deployed
system, not aspirational. For each channel, confirm against the code and the
per-flag defaults:

```bash
# which channels have a real inbox type and what the agent service does with each
grep -rn "Channel::" agent/app/services/ backend/apps/backend/src/chatbot/features/chat/ | grep -v test
# every feature flag that gates behaviour described in the guide
grep -n "enabled" agent/app/config.py backend/apps/backend/src/chatbot/platform/config.py
# the phone/voice scenarios and their accepted limitations
sed -n '1,120p' docs/testing/phone-channel-package-c-verification.md
```

Where a capability is real but **off by default**, the guide says so in the
step itself — not in a footnote. Where it is not built, the guide says
"not available today" rather than describing it in future tense. A guide that
describes an unshipped feature in the present tense is worse than no guide:
an agent will promise it to a customer.

- [ ] **Step 2: Write the shared front matter**

Open with: audience (agents and team leaders working cases day to day),
what changed since the 08-04 guide (the three items above), a pointer to
the old guide as the historical record, and a channel-map table with the
columns: *Customer touchpoint | Chatwoot inbox type | What the AI does before
a human sees it | Status today*. Cover Web chatbot, Voice bot, Phone, Email,
WhatsApp.

Then an "agent's toolkit" table — the actions that are identical on every
channel (AI draft, FAQ suggestion, labels, escalate, reassign, resolve,
contact history) with exactly where each one is in the UI.

- [ ] **Step 3: Write the four channel walkthroughs, numbered step by step**

Each channel gets the same skeleton, so an agent can learn one shape and
apply it everywhere:

1. **What the customer does** — how the conversation starts on this channel.
2. **What happens before you see it** — bot/lifecycle behaviour, with the
   flag that governs each step named inline.
3. **Scenario A — the bot handles it fully.** Numbered steps, what the agent
   sees, and when to leave it alone.
4. **Scenario B — the customer wants a human.** Numbered steps from handoff
   trigger to first agent reply, including where the AI's draft appears and
   how to send it.
5. **Scenario C — the case must be escalated.** Numbered steps: which label
   first, which second, who receives what, and what comes back.
6. **What is not usable on this channel yet** — plainly stated.

Channel-specific content that must appear:

- **Web chatbot:** the website-widget inbox; that the bot answers from the
  same KB as every other channel; how the conversation reaches an agent; the
  fact that a widget visitor may be anonymous and what that means for the
  contact record.
- **Voice bot:** the AI-answered call leg — what the caller hears, that
  speech is transcribed into the conversation, the rating survey, and the
  business-hours-aware roadside-assist routing. Name the accepted limitations
  from the Package C runbook rather than restating them optimistically.
- **Phone:** the human side — live transcript on the ticket, call
  classification, recording attached after hangup, and transfer to a human
  including the two failure modes the runbook documents (unanswered handoff,
  and the refusal to dial without a caller id).
- **Email:** the full current flow, which is the biggest change from v1 —
  auto-acknowledgement on arrival; `dept_<slug>` label **first**, then
  `escalate` (the order matters and the guide must say why); the two threads
  the customer and the PIC/dealer each see; that the customer never sees the
  internal trail; the dealer group receiving the forward; **the dealer's
  reply arriving back on the case as a private note with an AI-drafted
  customer reply beside it**; and that the agent reviews and sends that draft
  rather than it going out automatically.

- [ ] **Step 4: Write the cross-channel scenario**

One customer, one problem, three touchpoints (web chat → phone → email
escalation), showing that it is one contact and one history in the CRM.
Numbered steps, naming what the agent clicks to see the prior conversations.

- [ ] **Step 5: Write the quick-reference and the limitations table**

A one-screen cheat sheet (task → where to click, per channel), and a
limitations table with a *Channel | Limitation | Why | What to tell the
customer* shape. The last column is the point: it turns a known gap into a
script an agent can actually say out loud.

- [ ] **Step 6: Self-check the guide against reality**

Re-read your own draft looking for: any present-tense claim about a
default-off feature that doesn't name the flag; any step that assumes a
button exists without your having confirmed it; any place where v2
contradicts the Package C runbook's accepted limitations. Fix them inline.

- [ ] **Step 7: Commit**

```bash
git add docs/analysis/2026-08-08-crm-channel-interaction-guide-v2.md
git commit -m "docs(analysis): channel interaction guide v2 — step-by-step, four channels"
```

---

## Self-Review

**Spec coverage.** Spec §3.1 → Tasks 3-4. §3.2 → Tasks 8, 9, 11. §3.3 internal → Task 9; customer → Task 10. §3.4 idempotency → Task 9 (`dealer_replied_at` guard) plus the existing `claim_delivery`. §3.5 groups → Tasks 5, 12. §3.6 settings → Tasks 4, 9. §4 SLA scope/delivery/config → Tasks 13, 14, 15, 16. §5 assignee → Task 17; voice notes → Task 19; templates → Task 18. §6 spikes → Tasks 1-2. §7 verification → tests in every task plus Task 20. §8 rollout → Task 21. No spec section is unimplemented.

**Deviation from the spec, deliberate:** §3.3 named `/assist/summarize` for the draft. The plan uses `/assist/suggest`, which returns a KB-grounded `draft` intended as a customer-facing reply; `/summarize` condenses a conversation and would produce an internal summary, not something an agent can send. The spec is updated to match.

**Type consistency.** `DealerRecord.emails: list[str]` is used identically in Tasks 5, 6, 12. The alert callback is four-argument (`ticket_id, to_state, remark, labels`) in Tasks 14 and 15. `get_escalation_contacts` returns `dict[str, str] | None` in Tasks 7, 9, 10. `create_message(..., message_type=...)` is defined in Task 7 and used in Task 10. `_reply_to_for` / `_case_tag` are defined and used only in Task 4.
