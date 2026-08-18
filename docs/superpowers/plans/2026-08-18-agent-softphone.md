# In-CRM Agent Softphone Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the Gemini Live phone bridge hands off a live call, ring the human agent's browser inside the Chatwoot CRM instead of dialling a static PSTN hunt-group number.

**Architecture:** The bridge already redirects a live call's TwiML on handoff (`CallControl.redirect`) and already has an unreachable `<Client>` branch in `dial_twiml`. We add a Twilio Voice token with `incoming_allow=True` whose identity is derived server-side from the agent's validated Chatwoot session, a TTL registry of browsers actually holding a registered `Device`, and a resolver that returns the conversation's assigned agent as a `<Client>` target — chained *ahead* of the existing PSTN resolver so nothing regresses. A second stage fans out to everyone available when the assignee doesn't pick up; the existing bilingual apology remains the final fallback.

**Tech Stack:** Python 3.12 / FastAPI / pydantic-settings / `twilio` SDK / Firestore (`google-cloud-firestore`) / pytest — backend. Vue 3 `<script setup>` / `@twilio/voice-sdk` 2.18.3 / pnpm / Vite — Chatwoot fork patch.

**Spec:** `docs/superpowers/specs/2026-08-18-agent-softphone-design.md`

## Global Constraints

- **Every new flag defaults off.** With `phone_agent_softphone_enabled=false` behaviour must be byte-identical to today. This is the acceptance bar for every backend task.
- **Never raise into the audio pump.** `_attempt_transfer()` runs inline inside `pump()`, the sole Gemini→Twilio audio forwarder. Every new call it makes must be fail-open (return `None`/empty, log, never propagate) and bounded.
- **Identity is server-derived.** `agent_<chatwoot_user_id>` comes from `TokenValidator.resolve_user_id()` on the caller's validated session. Never from a request body or query parameter.
- **The caller-side token is untouched.** `token.py::mint_voice_token` keeps `incoming_allow=False`.
- **Config names must match verbatim** across `platform/config.py` and `deploy/tenants/example.env` (project CLAUDE.md).
- **Tenant scope is `proton` only.** Do not enable flags or "fix" parity for `default` or `wahchan`.
- **Branch is `dev-yuda`.** Never merge to `main`.
- Backend commands run from `backend/apps/backend`: `.venv/bin/pytest src/`, `.venv/bin/ruff format .`, `.venv/bin/ruff check . --fix`, `.venv/bin/mypy src/ --strict`.
- Twilio hard limits: **max 10 nouns per `<Dial>`**; `<Dial timeout>` must be an integer.
- Chatwoot fork image builds **off-VM, for amd64**, via Cloud Build. A local Mac `docker build` produces an arm64 image the VM cannot pull.

---

## File Structure

**Backend — create:**

| File | Responsibility |
|---|---|
| `features/chat/phone/agent_token.py` | Mint an agent Voice token (`incoming_allow=True`) |
| `features/chat/phone/softphone_registry.py` | TTL registry of registered agent softphones |
| `features/chat/phone/agent_client_resolver.py` | `AgentClientResolver` + `ChainedResolver` |
| `features/chat/phone/softphone_router.py` | `/voice/agent/{token,heartbeat,unregister}` |
| `features/chat/phone/test_agent_token.py` | |
| `features/chat/phone/test_softphone_registry.py` | |
| `features/chat/phone/test_agent_client_resolver.py` | |
| `features/chat/phone/test_softphone_router.py` | |

**Backend — modify:**

| File | Change |
|---|---|
| `platform/config.py` | 6 new settings + a flag dependency |
| `features/authz/deps.py` | `require_permission_with_identity` |
| `features/authz/seed.py` | `voice.answer` permission |
| `features/chat/phone/handoff_target.py` | `<Client>` long form + `<Parameter>`; PSTN-only caller-id guard; `fanout_twiml` |
| `features/chat/phone/bridge.py` | Construct + prefetch the chained resolver; pass `<Parameter>` values |
| `features/chat/router.py` | Stage-1 action URL; new fan-out route + handler |
| `main.py` | Mount the softphone router; construct the registry |
| `deploy/tenants/example.env` | Document the 6 new settings |

**Fork patch — create:** `deploy/chatwoot-fork/patches/0068-agent-softphone.patch`, containing:

| Path inside the patch | Responsibility |
|---|---|
| `app/javascript/dashboard/api/protonVoice.js` | Authenticated calls to the three backend endpoints |
| `app/javascript/dashboard/composables/useProtonSoftphone.js` | Owns the Twilio `Device`, heartbeat, call state |
| `app/javascript/dashboard/components-next/softphone/ProtonSoftphonePanel.vue` | Ring + in-call UI |
| `app/javascript/dashboard/components-next/sidebar/Sidebar.vue` | Mount point (modify) |

**Fork — modify:** `deploy/chatwoot-fork/Dockerfile` (add the `@twilio/voice-sdk` install line).

---

### Task 1: Settings, flag dependency, and the `voice.answer` permission

Pure declarations, no behaviour. Everything downstream reads these names, so they land first and land exactly.

**Files:**
- Modify: `backend/apps/backend/src/chatbot/platform/config.py` (settings block ending at `phone_handoff_caller_id`, ~line 525; `_phone_flag_dependencies`, ~line 1339)
- Modify: `backend/apps/backend/src/chatbot/features/authz/seed.py:47` (next to `call_recording.listen`)
- Modify: `deploy/tenants/example.env` (phone flags block, ~line 1119)
- Test: `backend/apps/backend/src/chatbot/platform/test_config_phone.py`
- Test: `backend/apps/backend/src/chatbot/features/authz/test_seed.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Settings.phone_agent_softphone_enabled: bool`, `.phone_agent_token_ttl_seconds: int`, `.phone_softphone_registration_ttl_seconds: int`, `.phone_agent_ring_timeout_seconds: int`, `.phone_fanout_ring_timeout_seconds: int`, `.phone_fanout_max_agents: int`; permission string `"voice.answer"`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/apps/backend/src/chatbot/platform/test_config_phone.py`:

```python
def test_agent_softphone_defaults_off():
    """The whole feature must be inert until a tenant opts in."""
    from chatbot.platform.config import get_settings

    s = get_settings()
    assert s.phone_agent_softphone_enabled is False
    assert s.phone_agent_token_ttl_seconds == 300
    assert s.phone_softphone_registration_ttl_seconds == 90
    assert s.phone_agent_ring_timeout_seconds == 20
    assert s.phone_fanout_ring_timeout_seconds == 25
    assert s.phone_fanout_max_agents == 10


def test_agent_softphone_requires_handoff_enabled():
    """Stage 1's <Dial action> is /webhooks/phone/dial-status, which
    router.py only registers when phone_handoff_enabled is on. Without it
    Twilio would POST the dial outcome to a 404 and drop a live caller."""
    import pytest

    from chatbot.platform.config import Settings

    with pytest.raises(ValueError, match="PHONE_AGENT_SOFTPHONE_ENABLED requires"):
        Settings(
            phone_agent_softphone_enabled=True,
            phone_handoff_enabled=False,
            phone_transcript_live_enabled=True,
        )
```

Append to `backend/apps/backend/src/chatbot/features/authz/test_seed.py`:

```python
def test_voice_answer_permission_is_seeded_and_not_default_granted():
    """A Voice grant with incoming_allow=True is a BILLABLE capability on the
    tenant's Twilio account, not just a UI affordance -- an operator ticks it
    on deliberately."""
    from chatbot.features.authz.seed import DEFAULT_ROLE_PERMISSIONS, PERMISSIONS

    assert "voice.answer" in PERMISSIONS
    for role, perms in DEFAULT_ROLE_PERMISSIONS.items():
        assert "voice.answer" not in perms, f"{role} must not get voice.answer by default"
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend/apps/backend && .venv/bin/pytest src/chatbot/platform/test_config_phone.py -k agent_softphone src/chatbot/features/authz/test_seed.py -k voice_answer -v
```

Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'phone_agent_softphone_enabled'` and `KeyError`/assertion on `voice.answer`.

> If `DEFAULT_ROLE_PERMISSIONS` / `PERMISSIONS` are named differently in `seed.py`, use the real names — read the file first and adjust the test, not the production constant.

- [ ] **Step 3: Add the settings**

In `config.py`, immediately after `phone_handoff_caller_id: str = ""`:

```python
    # --- In-CRM agent softphone (see docs/superpowers/specs/
    # 2026-08-18-agent-softphone-design.md) -------------------------------
    # Default off -> the chained resolver never returns a <Client> target and
    # handoff behaviour is byte-identical to the PSTN hunt group above.
    # DEPENDS ON phone_handoff_enabled: stage 1's <Dial action> is
    # /webhooks/phone/dial-status, which router.py registers only when THAT
    # flag is on -- see _phone_flag_dependencies below.
    phone_agent_softphone_enabled: bool = False
    # Agent Voice token TTL. Short on purpose: unlike the caller-side token
    # this one carries incoming_allow=True, so a leak lets the holder RECEIVE
    # transferred customer calls. The browser re-mints on tokenWillExpire.
    phone_agent_token_ttl_seconds: int = 300
    # How long a softphone registration stays valid without a heartbeat. The
    # browser beats every 30s, so 90 tolerates three misses. Advisory only --
    # a stale entry costs at most one wasted ring, never a stranded caller.
    phone_softphone_registration_ttl_seconds: int = 90
    # <Dial timeout> for stage 1 (the conversation's assigned agent alone).
    # Shorter than the fan-out: one person who may be away should not hold a
    # live caller for long before everyone else gets a chance.
    phone_agent_ring_timeout_seconds: int = 20
    # <Dial timeout> for stage 2 (fan-out to everyone available).
    phone_fanout_ring_timeout_seconds: int = 25
    # Twilio allows at most 10 nouns in a single <Dial>. Exceeding it is a
    # TwiML error that drops a live call, so this is a hard cap, not a
    # preference.
    phone_fanout_max_agents: int = 10
```

In `_phone_flag_dependencies`, after the existing `raise`:

```python
        if self.phone_agent_softphone_enabled and not self.phone_handoff_enabled:
            raise ValueError(
                "PHONE_AGENT_SOFTPHONE_ENABLED requires PHONE_HANDOFF_ENABLED=true. "
                "Stage 1 dials with action=/webhooks/phone/dial-status, and router.py "
                "registers that route only when PHONE_HANDOFF_ENABLED is on -- so with "
                "handoff off Twilio would POST the dial outcome to a 404 on a call that "
                "is still live, and the caller would be dropped with no apology and no "
                "unanswered_handoff tag."
            )
```

- [ ] **Step 4: Add the permission**

In `features/authz/seed.py`, next to `"call_recording.listen"`:

```python
    "voice.answer": "Answer transferred phone calls in the browser softphone",
```

Do **not** add it to any default role's permission list.

- [ ] **Step 5: Document the env vars**

In `deploy/tenants/example.env`, in the commented phone-flags block:

```bash
# --- In-CRM agent softphone -------------------------------------------------
# Ring the conversation's assigned agent in their browser (then fan out to
# everyone available) instead of dialling PHONE_HANDOFF_TARGET_NUMBER.
# REQUIRES PHONE_HANDOFF_ENABLED=true -- the app refuses to boot otherwise.
# Also requires the fork's `agent_softphone` feature and the `voice.answer`
# permission granted to the agents who should receive calls.
# PHONE_AGENT_SOFTPHONE_ENABLED=false
# PHONE_AGENT_TOKEN_TTL_SECONDS=300
# PHONE_SOFTPHONE_REGISTRATION_TTL_SECONDS=90
# PHONE_AGENT_RING_TIMEOUT_SECONDS=20
# PHONE_FANOUT_RING_TIMEOUT_SECONDS=25
# PHONE_FANOUT_MAX_AGENTS=10          # Twilio's hard <Dial> noun limit
```

- [ ] **Step 6: Run the tests to verify they pass**

```bash
cd backend/apps/backend && .venv/bin/pytest src/chatbot/platform/test_config_phone.py src/chatbot/features/authz/test_seed.py -v
```

Expected: PASS. Then confirm nothing else regressed:

```bash
.venv/bin/pytest src/ -q
```

- [ ] **Step 7: Commit**

```bash
git add backend/apps/backend/src/chatbot/platform/config.py \
        backend/apps/backend/src/chatbot/platform/test_config_phone.py \
        backend/apps/backend/src/chatbot/features/authz/seed.py \
        backend/apps/backend/src/chatbot/features/authz/test_seed.py \
        deploy/tenants/example.env
git commit -m "feat(phone): settings and voice.answer permission for the agent softphone"
```

---

### Task 2: Mint the agent Voice token

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/chat/phone/agent_token.py`
- Create: `backend/apps/backend/src/chatbot/features/chat/phone/test_agent_token.py`

**Interfaces:**
- Consumes: `Settings.phone_agent_token_ttl_seconds` (Task 1).
- Produces: `mint_agent_voice_token(settings: Settings, chatwoot_user_id: int) -> str`; `agent_identity(chatwoot_user_id: int) -> str`; `agent_id_from_identity(identity: str) -> int | None`.

- [ ] **Step 1: Write the failing test**

Create `test_agent_token.py`:

```python
"""The agent-side Voice token -- the one difference from the caller-side
token in token.py is incoming_allow=True, which is exactly what makes a leak
matter, so these tests are about that bit and about identity derivation."""

from __future__ import annotations

import pytest

from chatbot.features.chat.phone.agent_token import (
    agent_id_from_identity,
    agent_identity,
    mint_agent_voice_token,
)


@pytest.fixture
def settings():
    from chatbot.platform.config import get_settings

    return get_settings().model_copy(
        update={
            "twilio_account_sid": "AC" + "0" * 32,
            "twilio_api_key_sid": "SK" + "0" * 32,
            "twilio_api_key_secret": "secret-value",
            "twilio_twiml_app_sid": "AP" + "0" * 32,
            "phone_agent_token_ttl_seconds": 300,
        }
    )


def test_identity_round_trips():
    assert agent_identity(17) == "agent_17"
    assert agent_id_from_identity("agent_17") == 17


def test_identity_parse_rejects_junk():
    """A <Client> identity comes back from Twilio's callback as a string.
    Anything that is not one of OUR identities must be None, not a crash and
    not a coincidental integer."""
    for junk in ["", "17", "agent_", "agent_abc", "proton-web-caller", "agent_1_2"]:
        assert agent_id_from_identity(junk) is None


def test_token_grants_incoming(settings):
    """incoming_allow=True is the entire unlock: without it the browser can
    place calls but Twilio will not route a <Dial><Client> to it."""
    import jwt

    token = mint_agent_voice_token(settings, chatwoot_user_id=17)
    claims = jwt.decode(token, options={"verify_signature": False})
    assert claims["grants"]["voice"]["incoming"]["allow"] is True
    assert claims["grants"]["identity"] == "agent_17"


def test_caller_side_token_still_refuses_incoming(settings):
    """Regression guard: the demo customer softphone must never gain the
    ability to receive transferred calls."""
    import jwt

    from chatbot.features.chat.phone.token import mint_voice_token

    claims = jwt.decode(
        mint_voice_token(settings, "proton-web-caller"),
        options={"verify_signature": False},
    )
    assert claims["grants"]["voice"]["incoming"]["allow"] is False
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd backend/apps/backend && .venv/bin/pytest src/chatbot/features/chat/phone/test_agent_token.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'chatbot.features.chat.phone.agent_token'`.

> `PyJWT` ships as a transitive dependency of the `twilio` SDK. If the import fails, add `pyjwt` to the dev dependencies in `pyproject.toml` rather than skipping the assertion — the whole point of this test is reading the claim.

- [ ] **Step 3: Write the implementation**

Create `agent_token.py`:

```python
"""Mint the AGENT-side Twilio Voice access token.

Deliberately a separate module from `token.py` rather than a flag on
`mint_voice_token`. That function serves a public, unauthenticated SPA
endpoint and its `incoming_allow=False` is a security property, not a
default -- a shared function with an `incoming` parameter would put the
caller-side token one wrong argument away from being able to receive
transferred customer calls. Two functions cannot be confused at a call site.

The identity is `agent_<chatwoot_user_id>` and is ALWAYS derived from a
validated Chatwoot session by the caller (see `softphone_router.py`), never
from request data: a client-supplied identity would let any authenticated
agent register as a colleague and intercept their transferred calls.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from twilio.jwt.access_token import AccessToken
from twilio.jwt.access_token.grants import VoiceGrant

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_IDENTITY_PREFIX = "agent_"


def agent_identity(chatwoot_user_id: int) -> str:
    return f"{_IDENTITY_PREFIX}{chatwoot_user_id}"


def agent_id_from_identity(identity: str) -> int | None:
    """Inverse of `agent_identity`. `None` for anything that is not one of
    ours -- Twilio hands back whatever string was dialled, including the
    caller-side `proton-web-caller` identity, so this must never guess."""
    if not identity.startswith(_IDENTITY_PREFIX):
        return None
    suffix = identity[len(_IDENTITY_PREFIX) :]
    if not suffix.isdigit():
        return None
    return int(suffix)


def mint_agent_voice_token(settings: Settings, chatwoot_user_id: int) -> str:
    """Access token allowing this agent's browser to RECEIVE calls dialled to
    `agent_<id>`, and to place calls through our TwiML app."""
    identity = agent_identity(chatwoot_user_id)
    token = AccessToken(
        settings.twilio_account_sid,
        settings.twilio_api_key_sid,
        settings.twilio_api_key_secret,
        identity=identity,
        ttl=settings.phone_agent_token_ttl_seconds,
    )
    token.add_grant(
        VoiceGrant(
            outgoing_application_sid=settings.twilio_twiml_app_sid,
            incoming_allow=True,
        )
    )
    return str(token.to_jwt())
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd backend/apps/backend && .venv/bin/pytest src/chatbot/features/chat/phone/test_agent_token.py -v
```

Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/apps/backend/src/chatbot/features/chat/phone/agent_token.py \
        backend/apps/backend/src/chatbot/features/chat/phone/test_agent_token.py
git commit -m "feat(phone): mint agent Voice tokens with incoming_allow"
```

---

### Task 3: The softphone registration registry

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/chat/phone/softphone_registry.py`
- Create: `backend/apps/backend/src/chatbot/features/chat/phone/test_softphone_registry.py`

**Interfaces:**
- Consumes: `Settings.phone_softphone_registration_ttl_seconds`, `.firestore_project_id`, `.firestore_database_id`.
- Produces: `SoftphoneRegistry(settings, clock=None)` with `async heartbeat(agent_id: int) -> None`, `async unregister(agent_id: int) -> None`, `async registered_ids() -> set[int]`.

- [ ] **Step 1: Write the failing test**

Create `test_softphone_registry.py`:

```python
"""The registry is an OPTIMISATION, never a gate. Every test here is really
asking the same question: can a bad answer from this store strand a live
caller? It must not -- the worst it may cost is one wasted ring."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from chatbot.features.chat.phone.softphone_registry import SoftphoneRegistry


class FakeCollection:
    """Stands in for the Firestore collection: {doc_id: {"agent_id", "at"}}."""

    def __init__(self) -> None:
        self.docs: dict[str, dict] = {}
        self.fail = False

    def set(self, doc_id: str, data: dict) -> None:
        if self.fail:
            raise RuntimeError("firestore unavailable")
        self.docs[doc_id] = data

    def delete(self, doc_id: str) -> None:
        if self.fail:
            raise RuntimeError("firestore unavailable")
        self.docs.pop(doc_id, None)

    def all(self) -> list[dict]:
        if self.fail:
            raise RuntimeError("firestore unavailable")
        return list(self.docs.values())


@pytest.fixture
def settings():
    from chatbot.platform.config import get_settings

    return get_settings().model_copy(update={"phone_softphone_registration_ttl_seconds": 90})


@pytest.fixture
def registry(settings):
    now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
    reg = SoftphoneRegistry(settings, clock=lambda: now)
    reg._collection = FakeCollection()  # type: ignore[assignment]
    return reg


async def test_heartbeat_then_registered(registry):
    await registry.heartbeat(17)
    assert await registry.registered_ids() == {17}


async def test_unregister_removes(registry):
    await registry.heartbeat(17)
    await registry.unregister(17)
    assert await registry.registered_ids() == set()


async def test_entry_older_than_ttl_is_ignored(registry, settings):
    """A tab that closed without unregistering must age out, or we would ring
    a dead identity and burn a stage."""
    stale = registry._now() - timedelta(seconds=settings.phone_softphone_registration_ttl_seconds + 1)
    registry._collection.docs["agent-17"] = {"agent_id": 17, "at": stale}
    assert await registry.registered_ids() == set()


async def test_store_failure_returns_empty_and_does_not_raise(registry):
    """This is read from _attempt_transfer, which runs INLINE in the audio
    pump. An exception here would be dead air on a live call."""
    registry._collection.fail = True
    assert await registry.registered_ids() == set()


async def test_heartbeat_failure_does_not_raise(registry):
    registry._collection.fail = True
    await registry.heartbeat(17)  # must not raise
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd backend/apps/backend && .venv/bin/pytest src/chatbot/features/chat/phone/test_softphone_registry.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named '...softphone_registry'`.

- [ ] **Step 3: Write the implementation**

Create `softphone_registry.py`:

```python
"""Which agents currently hold a REGISTERED Twilio Device in a browser tab.

Distinct from Chatwoot availability (`features/routing/presence.py`), which
says whether an agent is at work. An agent can be `online` in Chatwoot with
no CRM tab open, and a `<Dial><Client>` to an unregistered identity fails
immediately -- so dialling on availability alone burns a ring stage on a
dead identity.

Firestore-backed rather than an in-process dict because the backend runs
multiple workers: the worker that mints a token and receives heartbeats is
usually NOT the worker holding the websocket for the call being transferred.

**Advisory, never authoritative.** Every method fails to the empty/no-op
answer. A stale, empty, or wrong result can cost one wasted ring or one
skipped stage; it can never prevent the PSTN fallback or the apology, both
of which hang off Twilio's dial-status callback and fire regardless of
anything this module returns. Keep that property when editing.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Callable

import structlog
from google.cloud import firestore

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

_COLLECTION = "softphone_registrations"


class SoftphoneRegistry:
    def __init__(self, settings: Settings, clock: Callable[[], datetime] | None = None) -> None:
        self._settings = settings
        self._clock = clock or (lambda: datetime.now(UTC))

    def _now(self) -> datetime:
        return self._clock()

    def _client(self) -> firestore.Client:
        return firestore.Client(
            project=self._settings.firestore_project_id,
            database=self._settings.firestore_database_id,
        )

    def _doc_id(self, agent_id: int) -> str:
        return f"agent-{agent_id}"

    def _collection_ref(self) -> Any:
        return self._client().collection(_COLLECTION)

    # `_collection` is an indirection seam the tests replace wholesale; it is
    # the only place this class touches Firestore's API surface.
    @property
    def _collection(self) -> Any:
        return _FirestoreCollection(self._collection_ref())

    async def heartbeat(self, agent_id: int) -> None:
        """Record (or refresh) this agent's registration. Fail-open: a browser
        whose heartbeat fails simply ages out of the fan-out."""
        try:
            await asyncio.to_thread(
                self._collection.set,
                self._doc_id(agent_id),
                {"agent_id": agent_id, "at": self._now()},
            )
        except Exception as e:
            _log.error("softphone_heartbeat_failed", agent_id=agent_id, error=str(e))

    async def unregister(self, agent_id: int) -> None:
        try:
            await asyncio.to_thread(self._collection.delete, self._doc_id(agent_id))
        except Exception as e:
            _log.error("softphone_unregister_failed", agent_id=agent_id, error=str(e))

    async def registered_ids(self) -> set[int]:
        """Agent ids whose last heartbeat is within the TTL. Empty on any
        failure -- see the module docstring."""
        ttl = timedelta(seconds=self._settings.phone_softphone_registration_ttl_seconds)
        cutoff = self._now() - ttl
        try:
            docs = await asyncio.to_thread(self._collection.all)
        except Exception as e:
            _log.error("softphone_registry_read_failed", error=str(e))
            return set()
        ids: set[int] = set()
        for doc in docs:
            at = doc.get("at")
            agent_id = doc.get("agent_id")
            if not isinstance(agent_id, int) or not isinstance(at, datetime):
                continue
            if at.tzinfo is None:
                at = at.replace(tzinfo=UTC)
            if at >= cutoff:
                ids.add(agent_id)
        return ids


class _FirestoreCollection:
    """Thin adapter so `SoftphoneRegistry` depends on three verbs, not on the
    Firestore client shape."""

    def __init__(self, ref: Any) -> None:
        self._ref = ref

    def set(self, doc_id: str, data: dict[str, Any]) -> None:
        self._ref.document(doc_id).set(data)

    def delete(self, doc_id: str) -> None:
        self._ref.document(doc_id).delete()

    def all(self) -> list[dict[str, Any]]:
        return [d.to_dict() or {} for d in self._ref.stream()]
```

> The `_collection` property builds a new adapter per access, which the tests replace by assigning an instance attribute of the same name. If `mypy --strict` objects to shadowing a property with an attribute, convert `_collection` to a plain `__init__`-assigned attribute built lazily — keep the three-verb interface either way.

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd backend/apps/backend && .venv/bin/pytest src/chatbot/features/chat/phone/test_softphone_registry.py -v
```

Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/apps/backend/src/chatbot/features/chat/phone/softphone_registry.py \
        backend/apps/backend/src/chatbot/features/chat/phone/test_softphone_registry.py
git commit -m "feat(phone): TTL registry of registered agent softphones"
```

---

### Task 4: Identity-returning auth dependency + the softphone router

The existing `require_permission` returns `None` and, when `rbac_enabled` is off, accepts a **shared secret** with no user attached. Neither works here: this endpoint's entire security model is *which* agent is asking.

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/authz/deps.py`
- Create: `backend/apps/backend/src/chatbot/features/chat/phone/softphone_router.py`
- Create: `backend/apps/backend/src/chatbot/features/chat/phone/test_softphone_router.py`
- Test: `backend/apps/backend/src/chatbot/features/authz/test_deps.py`

**Interfaces:**
- Consumes: `mint_agent_voice_token`, `agent_identity` (Task 2); `SoftphoneRegistry` (Task 3); `TokenValidator.resolve_user_id`, `AuthzRepository.permissions_for_user`.
- Produces: `require_permission_with_identity(permission, *, repo, validator, settings) -> Callable[..., Awaitable[int]]`; `build_softphone_router(settings, registry, repo=None, validator=None) -> APIRouter` exposing `POST /voice/agent/token` → `{"token": str, "identity": str}`, `POST /voice/agent/heartbeat` → `{"status": "ok"}`, `POST /voice/agent/unregister` → `{"status": "ok"}`.

- [ ] **Step 1: Write the failing tests**

Append to `features/authz/test_deps.py`:

```python
async def test_identity_dependency_refuses_shared_secret_even_with_rbac_off():
    """require_permission falls back to a shared-secret check when RBAC is
    off. This variant must NOT: a shared secret identifies a service, not a
    person, and the token it guards is minted FOR a specific person."""
    import pytest
    from fastapi import HTTPException

    from chatbot.features.authz.deps import require_permission_with_identity
    from chatbot.platform.config import get_settings

    settings = get_settings().model_copy(
        update={"rbac_enabled": False, "proton_backend_key": "shared-secret"}
    )
    check = require_permission_with_identity("voice.answer", settings=settings)
    with pytest.raises(HTTPException) as exc:
        await check(
            x_api_key="shared-secret",
            x_chatwoot_access_token=None,
            x_chatwoot_client=None,
            x_chatwoot_uid=None,
        )
    assert exc.value.status_code == 401


async def test_identity_dependency_returns_the_resolved_user_id():
    from unittest.mock import AsyncMock

    from chatbot.features.authz.deps import require_permission_with_identity
    from chatbot.platform.config import get_settings

    validator = AsyncMock()
    validator.resolve_user_id.return_value = 17
    repo = AsyncMock()
    repo.permissions_for_user.return_value = {"voice.answer"}

    check = require_permission_with_identity(
        "voice.answer",
        repo=repo,
        validator=validator,
        settings=get_settings().model_copy(update={"rbac_enabled": True}),
    )
    assert await check(
        x_api_key=None,
        x_chatwoot_access_token="tok",
        x_chatwoot_client="cli",
        x_chatwoot_uid="a@b.c",
    ) == 17
```

Create `test_softphone_router.py`:

```python
"""The token endpoint is the one place a billable, call-receiving credential
is issued, so these tests are mostly about who can get one and whose name is
on it."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.chat.phone.softphone_router import build_softphone_router


@pytest.fixture
def settings():
    from chatbot.platform.config import get_settings

    return get_settings().model_copy(
        update={
            "rbac_enabled": True,
            "phone_agent_softphone_enabled": True,
            "twilio_account_sid": "AC" + "0" * 32,
            "twilio_api_key_sid": "SK" + "0" * 32,
            "twilio_api_key_secret": "secret-value",
            "twilio_twiml_app_sid": "AP" + "0" * 32,
        }
    )


@pytest.fixture
def registry():
    reg = AsyncMock()
    reg.registered_ids.return_value = set()
    return reg


def _client(settings, registry, user_id=17, perms=frozenset({"voice.answer"})):
    validator = AsyncMock()
    validator.resolve_user_id.return_value = user_id
    repo = AsyncMock()
    repo.permissions_for_user.return_value = set(perms)
    app = FastAPI()
    app.include_router(
        build_softphone_router(settings, registry, repo=repo, validator=validator)
    )
    return TestClient(app)


_AUTH = {
    "x-chatwoot-access-token": "tok",
    "x-chatwoot-client": "cli",
    "x-chatwoot-uid": "agent@proton.local",
}


def test_token_identity_comes_from_the_session_not_the_body(settings, registry):
    """The attack this blocks: an authenticated agent asking for a token in a
    COLLEAGUE's name, which would let them receive that colleague's
    transferred customer calls."""
    res = _client(settings, registry).post(
        "/voice/agent/token", json={"identity": "agent_999"}, headers=_AUTH
    )
    assert res.status_code == 200
    assert res.json()["identity"] == "agent_17"


def test_token_requires_the_permission(settings, registry):
    res = _client(settings, registry, perms=frozenset()).post(
        "/voice/agent/token", json={}, headers=_AUTH
    )
    assert res.status_code == 403


def test_token_requires_a_session(settings, registry):
    res = _client(settings, registry).post("/voice/agent/token", json={})
    assert res.status_code == 401


def test_heartbeat_registers_the_session_user(settings, registry):
    res = _client(settings, registry).post("/voice/agent/heartbeat", json={}, headers=_AUTH)
    assert res.status_code == 200
    registry.heartbeat.assert_awaited_once_with(17)


def test_unregister_removes_the_session_user(settings, registry):
    res = _client(settings, registry).post("/voice/agent/unregister", json={}, headers=_AUTH)
    assert res.status_code == 200
    registry.unregister.assert_awaited_once_with(17)


def test_routes_404_when_the_feature_is_off(registry):
    from chatbot.platform.config import get_settings

    off = get_settings().model_copy(
        update={"rbac_enabled": True, "phone_agent_softphone_enabled": False}
    )
    res = _client(off, registry).post("/voice/agent/token", json={}, headers=_AUTH)
    assert res.status_code == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend/apps/backend && .venv/bin/pytest src/chatbot/features/authz/test_deps.py -k identity src/chatbot/features/chat/phone/test_softphone_router.py -v
```

Expected: FAIL — `ImportError: cannot import name 'require_permission_with_identity'` and `ModuleNotFoundError` for `softphone_router`.

- [ ] **Step 3: Add the dependency**

Append to `features/authz/deps.py`:

```python
def require_permission_with_identity(
    permission: str,
    *,
    repo: AuthzRepository | None = None,
    validator: TokenValidator | None = None,
    settings: Settings,
):
    """Like `require_permission`, but RETURNS the resolved Chatwoot user id
    and never honours the shared-secret path.

    That second difference is the point, not an oversight. `require_permission`
    falls back to `_shared_secret_check` when `rbac_enabled` is off, which is
    correct for endpoints that merely need to be *authorised* -- but a shared
    secret identifies a service, not a person, and the only caller of this
    dependency mints a credential in a specific person's name. With RBAC off
    there is no person, so there is no token to mint: 401.
    """

    async def _check(
        x_api_key: str | None = Header(default=None),
        x_chatwoot_access_token: str | None = Header(default=None),
        x_chatwoot_client: str | None = Header(default=None),
        x_chatwoot_uid: str | None = Header(default=None),
    ) -> int:
        if (
            not settings.rbac_enabled
            or not x_chatwoot_access_token
            or not x_chatwoot_client
            or not x_chatwoot_uid
            or repo is None
            or validator is None
        ):
            raise HTTPException(status_code=401, detail="Chatwoot session required")

        user_id = await validator.resolve_user_id(
            x_chatwoot_access_token, x_chatwoot_client, x_chatwoot_uid
        )
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")

        perms = await repo.permissions_for_user(user_id)
        if permission not in perms:
            raise HTTPException(status_code=403, detail=f"Missing permission: {permission}")
        return user_id

    return _check
```

- [ ] **Step 4: Write the router**

Create `softphone_router.py`:

```python
"""Endpoints the CRM's softphone panel calls: get a token, say "still here",
say "gone".

Mounted from `main.py`. Modelled on `recording_router.py` (a `build_*_router`
factory taking settings) rather than added to `ChatRouter`, which is already
~1900 lines and has no authz collaborators.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, status

from chatbot.features.authz.deps import require_permission_with_identity
from chatbot.features.chat.phone.agent_token import agent_identity, mint_agent_voice_token

if TYPE_CHECKING:
    from chatbot.features.authz.identity import TokenValidator
    from chatbot.features.authz.repository import AuthzRepository
    from chatbot.features.chat.phone.softphone_registry import SoftphoneRegistry
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


def build_softphone_router(
    settings: Settings,
    registry: SoftphoneRegistry,
    repo: AuthzRepository | None = None,
    validator: TokenValidator | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/voice/agent", tags=["voice-softphone"])

    identity_dep = require_permission_with_identity(
        "voice.answer", repo=repo, validator=validator, settings=settings
    )

    def _check_enabled() -> None:
        if not settings.phone_agent_softphone_enabled:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Agent softphone is disabled (PHONE_AGENT_SOFTPHONE_ENABLED=false)",
            )

    @router.post("/token")
    async def agent_token(agent_id: int = Depends(identity_dep)) -> dict[str, str]:
        """Mint a Voice token for the CALLER'S OWN identity.

        Note there is no request model: this endpoint deliberately reads
        nothing from the body. Any `identity` a client sends is ignored,
        because honouring it would let an authenticated agent register as a
        colleague and intercept their transferred calls.
        """
        _check_enabled()
        _log.info("softphone_token_issued", agent_id=agent_id)
        return {
            "token": mint_agent_voice_token(settings, agent_id),
            "identity": agent_identity(agent_id),
        }

    @router.post("/heartbeat")
    async def agent_heartbeat(agent_id: int = Depends(identity_dep)) -> dict[str, Any]:
        _check_enabled()
        await registry.heartbeat(agent_id)
        return {"status": "ok"}

    @router.post("/unregister")
    async def agent_unregister(agent_id: int = Depends(identity_dep)) -> dict[str, Any]:
        _check_enabled()
        await registry.unregister(agent_id)
        return {"status": "ok"}

    return router
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd backend/apps/backend && .venv/bin/pytest src/chatbot/features/authz/test_deps.py src/chatbot/features/chat/phone/test_softphone_router.py -v
```

Expected: PASS. `test_routes_404_when_the_feature_is_off` proves the flag gate; `test_token_identity_comes_from_the_session_not_the_body` proves the identity rule.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/backend/src/chatbot/features/authz/deps.py \
        backend/apps/backend/src/chatbot/features/authz/test_deps.py \
        backend/apps/backend/src/chatbot/features/chat/phone/softphone_router.py \
        backend/apps/backend/src/chatbot/features/chat/phone/test_softphone_router.py
git commit -m "feat(phone): authenticated agent softphone token and heartbeat endpoints"
```

---

### Task 5: `<Client>` TwiML with parameters, and the fan-out builder

Three changes to `handoff_target.py`, all in the TwiML layer.

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/chat/phone/handoff_target.py:126-210`
- Test: `backend/apps/backend/src/chatbot/features/chat/phone/test_twiml.py`

**Interfaces:**
- Consumes: `HandoffTarget` (existing).
- Produces: `dial_twiml(target, action_url, timeout, caller_id, parameters: dict[str, str] | None = None) -> str`; `fanout_twiml(identities: list[str], action_url: str, timeout: int, parameters: dict[str, str] | None = None) -> str`.

- [ ] **Step 1: Write the failing tests**

Append to `test_twiml.py`:

```python
def test_client_dial_uses_the_long_form_with_parameters():
    """The shorthand <Client>id</Client> has nowhere to put context. The
    ringing browser needs to know who is calling and why BEFORE the agent
    decides to accept, and <Parameter> children are how Twilio delivers that
    (they arrive as call.customParameters in the JS SDK)."""
    from chatbot.features.chat.phone.handoff_target import HandoffTarget, dial_twiml

    xml = dial_twiml(
        HandoffTarget(kind="client", value="agent_17"),
        "https://example.test/webhooks/phone/dial-status",
        20,
        "",
        {"conversation_id": "42", "reason": "billing dispute"},
    )
    assert "<Client><Identity>agent_17</Identity>" in xml
    assert '<Parameter name="conversation_id" value="42"/>' in xml
    assert '<Parameter name="reason" value="billing dispute"/>' in xml
    assert 'timeout="20"' in xml


def test_client_dial_needs_no_caller_id():
    """Twilio error 13214 (a client: caller id rejected for a PSTN <Number>)
    is what motivates the caller-id guard elsewhere. It does not apply to
    <Client>, and emitting an empty callerId attribute would be junk TwiML."""
    from chatbot.features.chat.phone.handoff_target import HandoffTarget, dial_twiml

    xml = dial_twiml(
        HandoffTarget(kind="client", value="agent_17"), "https://e.test/a", 20, ""
    )
    assert "callerId" not in xml


def test_parameter_values_are_escaped():
    """`reason` and `summary` are MODEL-GENERATED strings going into an XML
    attribute. Unescaped, a quote character produces TwiML Twilio cannot
    parse -- which drops a call that is still live."""
    from chatbot.features.chat.phone.handoff_target import HandoffTarget, dial_twiml

    xml = dial_twiml(
        HandoffTarget(kind="client", value="agent_17"),
        "https://e.test/a",
        20,
        "",
        {"reason": 'he said "no" & left <angrily>'},
    )
    assert '"no"' not in xml.split('name="reason"')[1].split("/>")[0]
    assert "&quot;" in xml or "&#34;" in xml
    assert "<angrily>" not in xml

    import xml.etree.ElementTree as ET

    ET.fromstring(xml)  # must parse


def test_number_dial_is_unchanged():
    """Regression guard: the PSTN path is the fallback that protects every
    caller when the softphone path finds nobody."""
    from chatbot.features.chat.phone.handoff_target import HandoffTarget, dial_twiml

    xml = dial_twiml(
        HandoffTarget(kind="pstn", value="+60388889999"), "https://e.test/a", 30, "+60311112222"
    )
    assert "<Number>+60388889999</Number>" in xml
    assert 'callerId="+60311112222"' in xml
    assert "<Identity>" not in xml


def test_fanout_emits_one_client_per_identity():
    from chatbot.features.chat.phone.handoff_target import fanout_twiml

    xml = fanout_twiml(["agent_1", "agent_2", "agent_3"], "https://e.test/f", 25)
    assert xml.count("<Client>") == 3
    assert "<Identity>agent_2</Identity>" in xml
    assert 'timeout="25"' in xml


def test_fanout_with_no_identities_returns_empty_string():
    """Callers must be able to ask "is there anyone to ring?" without
    building a <Dial> with zero nouns, which is a TwiML error."""
    from chatbot.features.chat.phone.handoff_target import fanout_twiml

    assert fanout_twiml([], "https://e.test/f", 25) == ""
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend/apps/backend && .venv/bin/pytest src/chatbot/features/chat/phone/test_twiml.py -v
```

Expected: FAIL — `ImportError: cannot import name 'fanout_twiml'`, and the `<Identity>` assertions fail against the current shorthand output.

- [ ] **Step 3: Rewrite the TwiML builders**

Replace `dial_twiml` in `handoff_target.py` and add `fanout_twiml` beside it:

```python
def _parameters_xml(parameters: dict[str, str] | None) -> str:
    """`<Parameter>` children, escaped. Values here include MODEL-GENERATED
    text (the handoff `reason`/`summary`), i.e. untrusted input going into an
    XML attribute -- `quoteattr` is load-bearing, not tidiness. Malformed
    TwiML on a live call drops the caller."""
    if not parameters:
        return ""
    return "".join(
        f"<Parameter name={quoteattr(name)} value={quoteattr(value)}/>"
        for name, value in parameters.items()
    )


def _client_noun(identity: str, parameters: dict[str, str] | None = None) -> str:
    """The LONG form. The shorthand `<Client>id</Client>` accepts no children,
    so it cannot carry the context the ringing browser needs."""
    return f"<Client><Identity>{escape(identity)}</Identity>{_parameters_xml(parameters)}</Client>"


def dial_twiml(
    target: HandoffTarget,
    action_url: str,
    timeout: int,
    caller_id: str,
    parameters: dict[str, str] | None = None,
) -> str:
    """TwiML that dials `target` and posts the outcome to `action_url`
    (see `/webhooks/phone/dial-status`). `timeout` is Twilio's `<Dial>` ring
    timeout in seconds before it gives up and fires `action` with
    `DialCallStatus=no-answer`.

    `caller_id` applies to the PSTN branch ONLY. A `<Number>` `<Dial>` with no
    `callerId` falls back to the parent leg's `From`, which on this repo's
    browser-softphone inbound path is a `client:` identifier Twilio rejects
    for a PSTN caller id (error 13214) -- see `HandoffTargetResolver.
    resolve()`, which refuses to resolve a PSTN target at all when
    `phone_handoff_caller_id` is unconfigured. `<Client>` has no such
    restriction, so the attribute is omitted entirely on that branch rather
    than emitted empty.

    `parameters` are ignored on the PSTN branch: a phone has nowhere to put
    them. They exist for `<Client>`, where they arrive in the browser as
    `call.customParameters`.
    """
    if target.kind == "client":
        noun = _client_noun(target.value, parameters)
        caller_id_attr = ""
    else:
        noun = f"<Number>{escape(target.value)}</Number>"
        caller_id_attr = f" callerId={quoteattr(caller_id)}" if caller_id else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        # `int(...)` is load-bearing, not decoration: `timeout` reaches here from
        # a settings field, and pydantic will happily hand back a float for a
        # value written `15.0` in a tenant env. Twilio rejects a non-integer
        # `<Dial timeout>` attribute, which fails the dial -- i.e. the handoff
        # silently does not connect.
        f'<Response><Dial action={quoteattr(action_url)} timeout="{int(timeout)}"'
        f"{caller_id_attr}>"
        f"{noun}</Dial></Response>"
    )


def fanout_twiml(
    identities: list[str],
    action_url: str,
    timeout: int,
    parameters: dict[str, str] | None = None,
) -> str:
    """Stage 2: ring every available agent at once, first accept wins.

    Returns `""` when `identities` is empty so callers can branch on "is
    there anyone to ring?" without constructing a `<Dial>` with zero nouns,
    which is a TwiML error on a live call.

    Twilio allows at most 10 nouns per `<Dial>`; enforcing that cap is the
    CALLER's job (`settings.phone_fanout_max_agents`), because silently
    truncating here would hide from the caller that some agents were never
    rung.
    """
    if not identities:
        return ""
    nouns = "".join(_client_noun(i, parameters) for i in identities)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Response><Dial action={quoteattr(action_url)} timeout="{int(timeout)}">'
        f"{nouns}</Dial></Response>"
    )
```

- [ ] **Step 4: Make the caller-id guard PSTN-only**

In `HandoffTargetResolver.resolve()`, the caller-id guard currently runs before the target kind is known. This resolver only ever returns `kind="pstn"`, so leave the guard where it is but say so explicitly — add to the existing comment above it:

```python
        # This resolver only ever produces a PSTN target, so this guard is
        # correctly unconditional HERE. It must NOT be copied into a resolver
        # that can return kind="client" (see agent_client_resolver.py):
        # error 13214 is a <Number> restriction, and applying it to <Client>
        # would silently disable the softphone for any tenant that never
        # configured a PSTN caller id.
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd backend/apps/backend && .venv/bin/pytest src/chatbot/features/chat/phone/test_twiml.py src/chatbot/features/chat/phone/test_handoff_targets.py -v
```

Expected: PASS, including the pre-existing `handoff_targets` tests — the `<Number>` output must be unchanged.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/backend/src/chatbot/features/chat/phone/handoff_target.py \
        backend/apps/backend/src/chatbot/features/chat/phone/test_twiml.py
git commit -m "feat(phone): <Client> dial with parameters, and the fan-out TwiML builder"
```

---

### Task 6: `AgentClientResolver` and `ChainedResolver`

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/chat/phone/agent_client_resolver.py`
- Create: `backend/apps/backend/src/chatbot/features/chat/phone/test_agent_client_resolver.py`

**Interfaces:**
- Consumes: `HandoffTarget` (Task 5); `SoftphoneRegistry.registered_ids` (Task 3); `agent_identity` (Task 2); `ConversationLogPort.get_conversation_assignee(ticket_id: str) -> str | None`.
- Produces: `AgentClientResolver(settings, log_port, registry, ticket_id_provider: Callable[[], str | None])` with `async prefetch() -> None` and `async resolve() -> HandoffTarget | None`; `ChainedResolver(resolvers: list)` with the same two methods.

- [ ] **Step 1: Write the failing test**

Create `test_agent_client_resolver.py`:

```python
"""Stage 1: ring the agent this conversation is already assigned to.

The recurring assertion is "resolves to None" -- because None here is not a
failure, it is the fall-through that hands the caller to the next resolver
and ultimately to the PSTN hunt group. A resolver that raised, or that
returned a target for an unregistered agent, would cost a live caller a ring
timeout or the whole call."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from chatbot.features.chat.phone.agent_client_resolver import (
    AgentClientResolver,
    ChainedResolver,
)
from chatbot.features.chat.phone.handoff_target import HandoffTarget


@pytest.fixture
def settings():
    from chatbot.platform.config import get_settings

    return get_settings().model_copy(update={"phone_agent_softphone_enabled": True})


@pytest.fixture
def log_port():
    port = AsyncMock()
    port.get_conversation_assignee.return_value = "17"
    return port


@pytest.fixture
def registry():
    reg = AsyncMock()
    reg.registered_ids.return_value = {17}
    return reg


def _resolver(settings, log_port, registry, ticket_id="42"):
    return AgentClientResolver(settings, log_port, registry, lambda: ticket_id)


async def test_assigned_and_registered_resolves_to_a_client_target(settings, log_port, registry):
    target = await _resolver(settings, log_port, registry).resolve()
    assert target == HandoffTarget(kind="client", value="agent_17")


async def test_assigned_but_not_registered_resolves_none(settings, log_port, registry):
    """Assigned in Chatwoot but no CRM tab open. Dialling would burn the whole
    stage-1 ring on an identity Twilio cannot route to."""
    registry.registered_ids.return_value = set()
    assert await _resolver(settings, log_port, registry).resolve() is None


async def test_no_assignee_resolves_none(settings, log_port, registry):
    log_port.get_conversation_assignee.return_value = None
    assert await _resolver(settings, log_port, registry).resolve() is None


async def test_flag_off_resolves_none_without_any_lookup(settings, log_port, registry):
    off = settings.model_copy(update={"phone_agent_softphone_enabled": False})
    assert await _resolver(off, log_port, registry).resolve() is None
    log_port.get_conversation_assignee.assert_not_awaited()
    registry.registered_ids.assert_not_awaited()


async def test_missing_ticket_id_resolves_none_with_zero_port_calls(settings, log_port, registry):
    """`ticket_id` is unset when the call-start create failed or the tenant
    runs chatwoot_enabled=False. This path runs INLINE in the audio pump, so
    the win is making zero round trips, not just returning None."""
    resolver = AgentClientResolver(settings, log_port, registry, lambda: None)
    assert await resolver.resolve() is None
    log_port.get_conversation_assignee.assert_not_awaited()
    registry.registered_ids.assert_not_awaited()


async def test_port_failure_resolves_none_and_does_not_raise(settings, log_port, registry):
    log_port.get_conversation_assignee.side_effect = RuntimeError("chatwoot down")
    assert await _resolver(settings, log_port, registry).resolve() is None


async def test_non_numeric_assignee_resolves_none(settings, log_port, registry):
    log_port.get_conversation_assignee.return_value = "not-an-id"
    assert await _resolver(settings, log_port, registry).resolve() is None


async def test_prefetch_makes_resolve_do_no_io(settings, log_port, registry):
    """Same reasoning as HandoffTargetResolver.prefetch(): _attempt_transfer
    runs inline in pump(), so a round trip there is dead air the caller
    actually hears."""
    resolver = _resolver(settings, log_port, registry)
    await resolver.prefetch()
    log_port.get_conversation_assignee.reset_mock()
    registry.registered_ids.reset_mock()

    assert await resolver.resolve() == HandoffTarget(kind="client", value="agent_17")
    log_port.get_conversation_assignee.assert_not_awaited()
    registry.registered_ids.assert_not_awaited()


async def test_prefetch_failure_leaves_resolve_working(settings, log_port, registry):
    resolver = _resolver(settings, log_port, registry)
    log_port.get_conversation_assignee.side_effect = RuntimeError("boom")
    await resolver.prefetch()
    log_port.get_conversation_assignee.side_effect = None
    assert await resolver.resolve() == HandoffTarget(kind="client", value="agent_17")


async def test_chain_returns_the_first_non_none():
    first, second = AsyncMock(), AsyncMock()
    first.resolve.return_value = None
    second.resolve.return_value = HandoffTarget(kind="pstn", value="+60388889999")

    assert await ChainedResolver([first, second]).resolve() == HandoffTarget(
        kind="pstn", value="+60388889999"
    )


async def test_chain_stops_at_the_first_hit():
    first, second = AsyncMock(), AsyncMock()
    first.resolve.return_value = HandoffTarget(kind="client", value="agent_17")

    await ChainedResolver([first, second]).resolve()
    second.resolve.assert_not_awaited()


async def test_chain_survives_a_raising_resolver():
    """One broken resolver must not deny the caller the fallback behind it."""
    first, second = AsyncMock(), AsyncMock()
    first.resolve.side_effect = RuntimeError("boom")
    second.resolve.return_value = HandoffTarget(kind="pstn", value="+60388889999")

    assert await ChainedResolver([first, second]).resolve() is not None
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd backend/apps/backend && .venv/bin/pytest src/chatbot/features/chat/phone/test_agent_client_resolver.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named '...agent_client_resolver'`.

- [ ] **Step 3: Write the implementation**

Create `agent_client_resolver.py`:

```python
"""Stage 1 of the handoff: the conversation's ASSIGNED agent, in their browser.

The second implementation of the `resolve() -> HandoffTarget | None` interface
that `handoff_target.py`'s module docstring anticipated. `None` from `resolve()`
means "not this resolver" and is the normal, expected answer -- `ChainedResolver`
then falls through to the PSTN hunt group, so a tenant that never enables the
softphone is unaffected and a tenant that does still has the old behaviour as a
floor.

Deliberately NOT copied from `HandoffTargetResolver`: its caller-id guard
(Twilio error 13214) is a `<Number>` restriction and would wrongly disable this
resolver for any tenant without a PSTN caller id configured.

Takes a `ticket_id_provider` callable rather than a `PhoneBridge` so the phone
package has no import cycle, and so the resolver reads the bridge's CURRENT
ticket id at resolve time -- the ticket is created by a detached task at call
start and may not exist yet when the resolver is constructed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Protocol

import structlog

from chatbot.features.chat.phone.agent_token import agent_identity
from chatbot.features.chat.phone.handoff_target import HandoffTarget

if TYPE_CHECKING:
    from chatbot.features.chat.phone.softphone_registry import SoftphoneRegistry
    from chatbot.features.chat.ports import ConversationLogPort
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


class _Resolver(Protocol):
    async def resolve(self) -> HandoffTarget | None: ...


class AgentClientResolver:
    def __init__(
        self,
        settings: Settings,
        log_port: ConversationLogPort,
        registry: SoftphoneRegistry,
        ticket_id_provider: Callable[[], str | None],
    ) -> None:
        self._settings = settings
        self._log_port = log_port
        self._registry = registry
        self._ticket_id_provider = ticket_id_provider
        # Warmed by prefetch(); None = cold, in which case resolve() does the
        # lookups inline (bounded by the caller's asyncio.wait_for).
        self._target: HandoffTarget | None = None
        self._warm = False

    async def prefetch(self) -> None:
        """Warm the answer off the audio path. Fire-and-forget from call
        start; never raises."""
        try:
            self._target = await self._lookup()
            self._warm = True
        except Exception as e:  # pragma: no cover -- _lookup never raises
            _log.error("agent_client_prefetch_failed", error=str(e))

    async def resolve(self) -> HandoffTarget | None:
        if not self._settings.phone_agent_softphone_enabled:
            return None
        if self._warm:
            return self._target
        return await self._lookup()

    async def _lookup(self) -> HandoffTarget | None:
        if not self._settings.phone_agent_softphone_enabled:
            return None
        ticket_id = self._ticket_id_provider()
        if not ticket_id:
            # The call-start create failed, or chatwoot_enabled is False.
            # Returning here costs zero round trips -- see the test.
            return None
        try:
            assignee = await self._log_port.get_conversation_assignee(ticket_id)
        except Exception as e:
            _log.error("agent_client_assignee_lookup_failed", ticket_id=ticket_id, error=str(e))
            return None
        if not assignee:
            return None
        agent_id = _as_agent_id(assignee)
        if agent_id is None:
            _log.warning("agent_client_assignee_not_numeric", assignee=str(assignee))
            return None
        try:
            registered = await self._registry.registered_ids()
        except Exception as e:  # pragma: no cover -- registered_ids never raises
            _log.error("agent_client_registry_failed", error=str(e))
            return None
        if agent_id not in registered:
            _log.info("agent_client_assignee_not_registered", agent_id=agent_id)
            return None
        return HandoffTarget(kind="client", value=agent_identity(agent_id))


def _as_agent_id(assignee: Any) -> int | None:
    try:
        return int(str(assignee).strip())
    except (TypeError, ValueError):
        return None


class ChainedResolver:
    """Try each resolver in order; first non-None wins.

    A raising resolver is skipped rather than allowed to propagate: one broken
    resolver must not deny the caller the fallback sitting behind it. This runs
    inline in the audio pump.
    """

    def __init__(self, resolvers: list[_Resolver]) -> None:
        self._resolvers = resolvers

    async def prefetch(self) -> None:
        for resolver in self._resolvers:
            prefetch = getattr(resolver, "prefetch", None)
            if prefetch is None:
                continue
            try:
                await prefetch()
            except Exception as e:  # pragma: no cover -- prefetches never raise
                _log.error("chained_resolver_prefetch_failed", error=str(e))

    async def resolve(self) -> HandoffTarget | None:
        for resolver in self._resolvers:
            try:
                target = await resolver.resolve()
            except Exception as e:
                _log.error("chained_resolver_failed", error=str(e))
                continue
            if target is not None:
                return target
        return None
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd backend/apps/backend && .venv/bin/pytest src/chatbot/features/chat/phone/test_agent_client_resolver.py -v
```

Expected: PASS (12 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/apps/backend/src/chatbot/features/chat/phone/agent_client_resolver.py \
        backend/apps/backend/src/chatbot/features/chat/phone/test_agent_client_resolver.py
git commit -m "feat(phone): resolve the assigned agent's softphone as a handoff target"
```

---

### Task 7: Wire the chained resolver into the bridge

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/chat/phone/bridge.py` (`__init__` ~126-220; the prefetch site ~283-292; `_attempt_transfer` ~596-612)
- Test: `backend/apps/backend/src/chatbot/features/chat/phone/test_bridge.py`

**Interfaces:**
- Consumes: `AgentClientResolver`, `ChainedResolver` (Task 6); `dial_twiml(..., parameters=...)` (Task 5).
- Produces: `PhoneBridge(..., softphone_registry: SoftphoneRegistry | None = None)`; `PhoneBridge._handoff_parameters() -> dict[str, str]`.

- [ ] **Step 1: Write the failing test**

Append to `test_bridge.py`:

```python
async def test_transfer_prefers_a_registered_assignee_over_the_pstn_number(
    bridge_with_softphone,
):
    """The whole feature in one assertion: with an assigned, registered agent
    the live call is redirected to their browser, not to the hunt group."""
    bridge = bridge_with_softphone
    bridge.call_sid = "CA123"
    bridge.ticket_id = "42"

    assert await bridge._attempt_transfer() == "transferring"

    twiml = bridge._call_control.redirect.await_args.args[1]
    assert "<Identity>agent_17</Identity>" in twiml
    assert "<Number>" not in twiml


async def test_transfer_passes_context_to_the_ringing_browser(bridge_with_softphone):
    """The agent decides whether to accept BEFORE they can see the CRM, so the
    reason and the conversation link have to travel with the ring."""
    bridge = bridge_with_softphone
    bridge.call_sid = "CA123"
    bridge.ticket_id = "42"
    bridge.handoff = {"reason": "angry about a billing charge", "summary": "s"}

    await bridge._attempt_transfer()

    twiml = bridge._call_control.redirect.await_args.args[1]
    assert 'name="conversation_id" value="42"' in twiml
    assert "angry about a billing charge" in twiml


async def test_transfer_falls_back_to_pstn_when_nobody_is_registered(
    bridge_with_softphone,
):
    bridge = bridge_with_softphone
    bridge._softphone_registry.registered_ids.return_value = set()
    bridge.call_sid = "CA123"
    bridge.ticket_id = "42"

    assert await bridge._attempt_transfer() == "transferring"
    assert "<Number>" in bridge._call_control.redirect.await_args.args[1]


async def test_softphone_disabled_is_byte_identical_to_today(bridge_with_softphone):
    """The acceptance bar for the whole feature."""
    bridge = bridge_with_softphone
    bridge._settings = bridge._settings.model_copy(
        update={"phone_agent_softphone_enabled": False}
    )
    bridge.call_sid = "CA123"
    bridge.ticket_id = "42"

    await bridge._attempt_transfer()
    twiml = bridge._call_control.redirect.await_args.args[1]
    assert "<Number>" in twiml
    assert "<Client>" not in twiml
```

Add the fixture next to the existing bridge fixtures in `test_bridge.py` — model it on whichever fixture already builds a `PhoneBridge` with an `AsyncMock` `CallControl`, adding:

```python
@pytest.fixture
def bridge_with_softphone(bridge):  # reuse the existing bridge fixture
    """A bridge whose tenant has BOTH the softphone and the PSTN fallback
    configured, with agent 17 assigned and registered."""
    from unittest.mock import AsyncMock

    bridge._settings = bridge._settings.model_copy(
        update={
            "phone_agent_softphone_enabled": True,
            "phone_handoff_enabled": True,
            "phone_handoff_target_number": "+60388889999",
            "phone_handoff_caller_id": "+60311112222",
            "twilio_webhook_base_url": "https://example.test",
        }
    )
    registry = AsyncMock()
    registry.registered_ids.return_value = {17}
    bridge._softphone_registry = registry
    bridge._log_port.get_conversation_assignee = AsyncMock(return_value="17")
    bridge._call_control.redirect = AsyncMock(return_value=True)
    bridge._rebuild_handoff_resolver()
    return bridge
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd backend/apps/backend && .venv/bin/pytest src/chatbot/features/chat/phone/test_bridge.py -k softphone -v
```

Expected: FAIL — `AttributeError: 'PhoneBridge' object has no attribute '_rebuild_handoff_resolver'`.

- [ ] **Step 3: Wire it in**

In `bridge.py`, add the import:

```python
from chatbot.features.chat.phone.agent_client_resolver import AgentClientResolver, ChainedResolver
```

In `__init__`, accept the registry and build the chain. Replace the existing `self._handoff_resolver = (...)` assignment with:

```python
        self._softphone_registry = softphone_registry
        self._rebuild_handoff_resolver(handoff_resolver)
```

and add the method:

```python
    def _rebuild_handoff_resolver(self, injected: Any | None = None) -> None:
        """Compose the handoff resolver chain: the assigned agent's softphone
        first, the static PSTN hunt group behind it.

        Order is the feature. Stage 1 returning None is the normal case (no
        assignee, nobody registered, flag off) and must fall through to the
        behaviour every tenant has today -- which is why this is a chain and
        not a replacement.

        `injected` keeps the existing test seam: a caller that passes its own
        resolver gets exactly that resolver, unchained.
        """
        if injected is not None:
            self._handoff_resolver = injected
            return
        pstn = HandoffTargetResolver(self._settings, self._log_port)
        if not self._settings.phone_agent_softphone_enabled or self._softphone_registry is None:
            self._handoff_resolver = pstn
            return
        self._handoff_resolver = ChainedResolver(
            [
                AgentClientResolver(
                    self._settings,
                    self._log_port,
                    self._softphone_registry,
                    lambda: self.ticket_id,
                ),
                pstn,
            ]
        )
```

Add `softphone_registry: SoftphoneRegistry | None = None` to the `__init__` signature next to `handoff_resolver`.

Add the parameters builder:

```python
    def _handoff_parameters(self) -> dict[str, str]:
        """Context the ringing browser shows BEFORE the agent accepts.

        `reason`/`summary` are model-generated; `handoff_target._parameters_xml`
        escapes them. Truncated because Twilio caps the total TwiML size and a
        rambling summary on a live call is not worth a TwiML error.
        """
        handoff = self.handoff or {}
        params = {
            "conversation_id": str(self.ticket_id or ""),
            "reason": str(handoff.get("reason") or "")[:200],
            "summary": str(handoff.get("summary") or "")[:400],
        }
        return {k: v for k, v in params.items() if v}
```

In `_attempt_transfer`, pass the ring timeout and parameters:

```python
        timeout = (
            self._settings.phone_agent_ring_timeout_seconds
            if target.kind == "client"
            else self._settings.phone_handoff_timeout_seconds
        )
        twiml = dial_twiml(
            target,
            action_url,
            timeout,
            self._settings.phone_handoff_caller_id,
            self._handoff_parameters(),
        )
```

Finally, in the existing prefetch site (`handle_twilio`, ~line 283), widen the condition so the chain is warmed too:

```python
        if (
            self._settings.phone_handoff_enabled
            or self._settings.phone_agent_softphone_enabled
        ) and (self._handoff_prefetch_task is None or self._handoff_prefetch_task.done()):
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend/apps/backend && .venv/bin/pytest src/chatbot/features/chat/phone/test_bridge.py src/chatbot/features/chat/phone/test_handoff.py -v
```

Expected: PASS, including every pre-existing handoff test — `test_softphone_disabled_is_byte_identical_to_today` is the guard that matters.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/backend/src/chatbot/features/chat/phone/bridge.py \
        backend/apps/backend/src/chatbot/features/chat/phone/test_bridge.py
git commit -m "feat(phone): chain the softphone resolver ahead of the PSTN hunt group"
```

---

### Task 8: Stage 2 — the fan-out route

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/chat/router.py` (route registration ~465-471; `phone_dial_status_webhook` ~1741)
- Test: `backend/apps/backend/src/chatbot/features/chat/phone/test_handoff.py`

**Interfaces:**
- Consumes: `fanout_twiml` (Task 5); `SoftphoneRegistry.registered_ids` (Task 3); `PresenceFetcher` → `AgentRecord.availability_status`; `agent_identity` (Task 2).
- Produces: route `POST /webhooks/phone/dial-status/fanout`; `ChatRouter._fanout_identities() -> list[str]`.

- [ ] **Step 1: Write the failing test**

Append to `test_handoff.py`:

```python
async def test_stage_one_action_url_points_at_the_fanout_route(bridge_with_softphone):
    """Stage 1 must hand its outcome to the stage-2 handler, or an unanswered
    assignee ends the call instead of ringing everyone else."""
    bridge = bridge_with_softphone
    bridge.call_sid = "CA123"
    bridge.ticket_id = "42"

    await bridge._attempt_transfer()

    twiml = bridge._call_control.redirect.await_args.args[1]
    assert "/webhooks/phone/dial-status/fanout" in twiml


def test_fanout_route_absent_when_the_feature_is_off(app_client_softphone_off):
    res = app_client_softphone_off.post("/webhooks/phone/dial-status/fanout", data={})
    assert res.status_code == 404


def test_fanout_signature_is_verified_against_its_own_path(app_client_softphone_on):
    """The trap this guards: Twilio signs the EXACT url it posts to. Verifying
    a stage-2 callback against the stage-1 path yields a mismatch, a 401, and
    a dropped caller who is still on the line."""
    res = app_client_softphone_on.post(
        "/webhooks/phone/dial-status/fanout",
        data={"CallSid": "CA123", "DialCallStatus": "no-answer"},
        headers={"X-Twilio-Signature": "wrong"},
    )
    assert res.status_code == 401


async def test_fanout_rings_available_registered_agents(chat_router_softphone):
    router, registry, presence = chat_router_softphone
    registry.registered_ids.return_value = {1, 2, 3}
    presence.fetch_agents.return_value = [
        _agent(1, "online"),
        _agent(2, "busy"),
        _agent(3, "online"),
    ]

    identities = await router._fanout_identities()

    assert identities == ["agent_1", "agent_3"]  # busy agent excluded


async def test_fanout_is_capped_at_the_twilio_noun_limit(chat_router_softphone):
    """More than 10 nouns in a <Dial> is a TwiML error, which drops the call."""
    router, registry, presence = chat_router_softphone
    router.orchestrator._settings = router.orchestrator._settings.model_copy(
        update={"phone_fanout_max_agents": 10}
    )
    registry.registered_ids.return_value = set(range(1, 16))
    presence.fetch_agents.return_value = [_agent(i, "online") for i in range(1, 16)]

    assert len(await router._fanout_identities()) == 10


async def test_fanout_with_nobody_available_falls_through_to_the_apology(
    chat_router_softphone,
):
    router, registry, presence = chat_router_softphone
    registry.registered_ids.return_value = set()
    presence.fetch_agents.return_value = []

    assert await router._fanout_identities() == []


def _agent(agent_id: int, status: str):
    from chatbot.features.routing.presence import AgentRecord

    return AgentRecord(id=agent_id, name=f"Agent {agent_id}", availability_status=status)
```

Build `chat_router_softphone`, `app_client_softphone_on`, and `app_client_softphone_off` fixtures on whatever app/router fixtures `test_handoff.py` already uses for the dial-status tests — read the top of that file and follow its existing construction rather than inventing a second one.

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd backend/apps/backend && .venv/bin/pytest src/chatbot/features/chat/phone/test_handoff.py -k "fanout or stage_one" -v
```

Expected: FAIL — `AttributeError: 'ChatRouter' object has no attribute '_fanout_identities'` and 404 on the new path.

- [ ] **Step 3: Register the route**

In `ChatRouter.__init__`, after the existing `phone_handoff_enabled` block:

```python
        # Stage 2 of the softphone handoff. A SEPARATE PATH, not a query
        # parameter on the route above: Twilio signs the exact URL it posts
        # to, including any query string, but phone_dial_status_webhook
        # reconstructs the URL for verification as
        # f"{twilio_webhook_base_url}/webhooks/phone/dial-status" and drops
        # the query -- so `?stage=2` would fail the signature check, answer
        # 401, and drop a caller who is still on the line.
        if orchestrator._settings.phone_agent_softphone_enabled:
            self.router.add_api_route(
                "/webhooks/phone/dial-status/fanout",
                self.phone_dial_status_fanout_webhook,
                methods=["POST"],
            )
```

Also add the registry and presence collaborators to `ChatRouter.__init__`, following the `acw_controller` precedent (`None`-defaulted so every existing caller and test is untouched):

```python
        softphone_registry: SoftphoneRegistry | None = None,
        presence_fetcher: PresenceFetcher | None = None,
```

- [ ] **Step 4: Implement the handler**

Add to `ChatRouter`:

```python
    async def _fanout_identities(self) -> list[str]:
        """Agents to ring in stage 2: registered softphone AND `online` in
        Chatwoot.

        `busy` and `offline` are excluded deliberately -- `busy` in Chatwoot
        means already on something, and ringing them would interrupt one
        customer to serve another.

        Capped at `phone_fanout_max_agents` because Twilio rejects a `<Dial>`
        with more than 10 nouns, and a TwiML error here drops a live call. The
        cap is LOGGED when it bites: silently truncating would read as "we rang
        everyone" when we did not.
        """
        settings = self.orchestrator._settings
        if self._softphone_registry is None or self._presence_fetcher is None:
            return []
        try:
            registered = await self._softphone_registry.registered_ids()
            agents = await self._presence_fetcher.fetch_agents()
        except Exception as e:
            _log.error("phone_fanout_lookup_failed", error=str(e))
            return []
        eligible = sorted(
            a.id for a in agents if a.id in registered and a.availability_status == "online"
        )
        cap = settings.phone_fanout_max_agents
        if len(eligible) > cap:
            _log.warning(
                "phone_fanout_capped", eligible=len(eligible), cap=cap, dropped=eligible[cap:]
            )
            eligible = eligible[:cap]
        return [agent_identity(i) for i in eligible]

    async def phone_dial_status_fanout_webhook(
        self, request: Request, background_tasks: BackgroundTasks
    ) -> Response:
        """Stage 1's `<Dial action>`: the assigned agent's ring has ended.

        `completed` means they took it -- delegate to the stage-1 handler,
        which already does ACW entry and returns hang-up TwiML. Anything else
        means they did not, so ring everyone available instead of apologising
        immediately. Only when THAT `<Dial>` also fails (it posts to the
        stage-1 route) does the caller hear the apology and the case get its
        `unanswered_handoff` tag.
        """
        settings = self.orchestrator._settings
        token = settings.twilio_auth_token
        if not token:
            _log.warning("phone_fanout_no_auth_token_configured")
            raise HTTPException(status_code=401, detail="Dial status webhook not configured")

        form = await request.form()
        params = {k: str(v) for k, v in form.items()}
        base = settings.twilio_webhook_base_url
        # Verified against THIS route's own path -- see the registration
        # comment in __init__ for why the two stages are separate paths.
        verify_url = (
            f"{base.rstrip('/')}/webhooks/phone/dial-status/fanout" if base else str(request.url)
        )
        if not verify_twilio_signature(
            token, verify_url, params, request.headers.get("X-Twilio-Signature")
        ):
            _log.warning("phone_fanout_signature_invalid")
            raise HTTPException(status_code=401, detail="Invalid signature")

        if params.get("DialCallStatus", "") == "completed":
            return await self.phone_dial_status_webhook(request, background_tasks)

        identities = await self._fanout_identities()
        if not identities:
            _log.info("phone_fanout_nobody_available", call_sid=params.get("CallSid", ""))
            return await self.phone_dial_status_webhook(request, background_tasks)

        _log.info(
            "phone_fanout_dialing",
            call_sid=params.get("CallSid", ""),
            agents=len(identities),
        )
        return Response(
            content=fanout_twiml(
                identities,
                self._dial_status_action_url(),
                settings.phone_fanout_ring_timeout_seconds,
            ),
            media_type="application/xml",
        )
```

> `phone_dial_status_fanout_webhook` re-reads `request.form()` when it delegates. Starlette caches the parsed form on the request, so the second read is free — but verify that in the test run rather than assuming it.

Add a `ChatRouter._dial_status_action_url()` helper mirroring `PhoneBridge._dial_status_action_url()` (returns `f"{twilio_webhook_base_url}/webhooks/phone/dial-status"`, or `""` when unconfigured).

In `PhoneBridge._dial_status_action_url`, point stage 1 at the fan-out route when the softphone is on:

```python
    def _dial_status_action_url(self) -> str:
        base = self._settings.twilio_webhook_base_url
        if not base:
            return ""
        # Stage 1 hands its outcome to the stage-2 handler; without the
        # softphone there is no stage 2 and the outcome is final.
        suffix = "/fanout" if self._settings.phone_agent_softphone_enabled else ""
        return f"{base.rstrip('/')}/webhooks/phone/dial-status{suffix}"
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd backend/apps/backend && .venv/bin/pytest src/chatbot/features/chat/phone/ -v
```

Expected: PASS across the whole phone package.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/backend/src/chatbot/features/chat/router.py \
        backend/apps/backend/src/chatbot/features/chat/phone/bridge.py \
        backend/apps/backend/src/chatbot/features/chat/phone/test_handoff.py
git commit -m "feat(phone): fan out to available agents when the assignee does not answer"
```

---

### Task 9: Application wiring

**Files:**
- Modify: `backend/apps/backend/src/chatbot/main.py`
- Test: `backend/apps/backend/src/chatbot/test_p11_wiring.py`

**Interfaces:**
- Consumes: everything from Tasks 2-8.
- Produces: a booted app serving `/voice/agent/token` and, when enabled, `/webhooks/phone/dial-status/fanout`.

- [ ] **Step 1: Write the failing test**

Append to `test_p11_wiring.py`:

```python
def test_softphone_token_route_is_mounted_and_answers_401_not_404():
    """The wiring assertion this file exists for: a route that 404s is not
    mounted; one that 401s is mounted and refusing an unauthenticated caller."""
    from fastapi.testclient import TestClient

    from chatbot.main import create_app

    client = TestClient(create_app())
    assert client.post("/voice/agent/token").status_code in (401, 404)
    # 404 only when the feature flag is off, which is the default; assert the
    # route EXISTS by checking the app's route table directly.
    paths = {r.path for r in create_app().routes}
    assert "/voice/agent/token" in paths


def test_softphone_registry_is_constructed_when_enabled(monkeypatch):
    monkeypatch.setenv("PHONE_AGENT_SOFTPHONE_ENABLED", "true")
    monkeypatch.setenv("PHONE_HANDOFF_ENABLED", "true")
    monkeypatch.setenv("PHONE_TRANSCRIPT_LIVE_ENABLED", "true")

    from chatbot.platform.config import get_settings

    get_settings.cache_clear()
    from chatbot.main import create_app

    paths = {r.path for r in create_app().routes}
    assert "/webhooks/phone/dial-status/fanout" in paths
    get_settings.cache_clear()
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd backend/apps/backend && .venv/bin/pytest src/chatbot/test_p11_wiring.py -k softphone -v
```

Expected: FAIL — `/voice/agent/token` is not in the route table.

- [ ] **Step 3: Wire it up**

In `main.py`, near the existing authz block that builds `authz_repo` / `authz_validator` (~line 874), add:

```python
    from chatbot.features.chat.phone.softphone_registry import SoftphoneRegistry
    from chatbot.features.chat.phone.softphone_router import build_softphone_router

    softphone_registry = SoftphoneRegistry(settings)
    app.include_router(
        build_softphone_router(
            settings,
            softphone_registry,
            repo=authz_repo,
            validator=authz_validator,
        )
    )
```

Pass the collaborators into `ChatRouter` where it is constructed:

```python
        softphone_registry=softphone_registry,
        presence_fetcher=presence_fetcher,
```

and into `PhoneBridge` where `phone_stream` constructs it:

```python
            softphone_registry=self._softphone_registry,
```

> `presence_fetcher` is already constructed for the routing feature. Reuse that instance — do not build a second one; it holds its own HTTP configuration.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend/apps/backend && .venv/bin/pytest src/ -q
```

Expected: the full suite passes.

- [ ] **Step 5: Lint and type-check**

```bash
cd backend/apps/backend && .venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict
```

Fix anything reported before committing.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/backend/src/chatbot/main.py \
        backend/apps/backend/src/chatbot/test_p11_wiring.py
git commit -m "feat(phone): mount the agent softphone router and registry"
```

---

### Task 10: Fork patch 0068 — the softphone UI

**Files:**
- Modify: `deploy/chatwoot-fork/Dockerfile`
- Create: `deploy/chatwoot-fork/patches/0068-agent-softphone.patch`

**Interfaces:**
- Consumes: `POST /voice/agent/{token,heartbeat,unregister}` (Task 4); `call.customParameters` keys `conversation_id`, `reason`, `summary` (Task 7).
- Produces: nothing downstream.

**Context for whoever implements this:** patches are `git apply`-ed onto upstream `chatwoot/chatwoot:v4.15.1` at image-build time, and the sandbox cannot clone upstream. Fetch the single file you need to diff against:

```bash
curl -s https://raw.githubusercontent.com/chatwoot/chatwoot/v4.15.1/app/javascript/dashboard/components-next/sidebar/Sidebar.vue -o /tmp/sidebar.vue
```

The `</aside>` close tag is at line 1003 and `</template>` at 1004 — that is your anchor. Follow patch `0057-inbound-alerts.patch` for the exact header/diff format, including the `new file mode 100644` stanzas.

- [ ] **Step 1: Add the dependency to the Dockerfile**

```dockerfile
RUN pnpm install --frozen-lockfile
# Patch 0068's softphone needs Twilio's Voice JS SDK, which upstream does not
# depend on. Added AFTER the frozen install so upstream's dependency graph
# stays byte-reproducible and exactly one dependency is added, explicitly and
# pinned. Editing package.json in the patch instead would break
# --frozen-lockfile, since we cannot regenerate pnpm-lock.yaml here.
RUN pnpm add @twilio/voice-sdk@2.18.3
RUN pnpm exec vite build
```

- [ ] **Step 2: Write `protonVoice.js` into the patch**

```javascript
// protonVoice.js — OUR file. The softphone's three backend calls. Uses the
// same devise_token_auth triplet forwarding as protonAdmin.js's adminRequest,
// because /voice/agent/* is gated per-USER (require_permission_with_identity),
// not by the shared backend key: the token minted is FOR the calling agent and
// the backend derives the identity from this session. Sending an identity from
// here would be pointless -- the backend ignores request bodies on purpose.
import { useProtonConfig } from 'dashboard/composables/useProtonConfig';
import Auth from './auth';

async function voiceRequest(path) {
  const { backendUrl, backendKey } = useProtonConfig();
  if (!backendUrl) throw new Error('PROTON_BACKEND_URL not configured');

  const authData = Auth.hasAuthCookie() ? Auth.getAuthData() : null;

  const response = await fetch(`${backendUrl}${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(backendKey ? { 'x-api-key': backendKey } : {}),
      ...(authData
        ? {
            'x-chatwoot-access-token': authData['access-token'],
            'x-chatwoot-client': authData.client,
            'x-chatwoot-uid': authData.uid,
          }
        : {}),
    },
  });

  if (!response.ok) {
    throw new Error(`${path} failed: ${response.status}`);
  }
  return response.json();
}

export const fetchAgentVoiceToken = () => voiceRequest('/voice/agent/token');
export const sendSoftphoneHeartbeat = () => voiceRequest('/voice/agent/heartbeat');
export const unregisterSoftphone = () => voiceRequest('/voice/agent/unregister');
```

- [ ] **Step 3: Write `useProtonSoftphone.js` into the patch**

```javascript
// useProtonSoftphone.js — OUR file. Owns the Twilio Device for this tab.
//
// Installed from Sidebar.vue because the sidebar is mounted on every dashboard
// page: a phone that only rings while the agent happens to be looking at the
// right conversation is not a phone. Same reasoning as useProtonInboundAlerts.
//
// Fail-quiet by design: every failure here leaves the agent working normally
// and simply absent from the fan-out. A softphone that throws into the CRM
// would be worse than one that does not ring.
import { ref, onBeforeUnmount } from 'vue';
import { Device } from '@twilio/voice-sdk';
import {
  fetchAgentVoiceToken,
  sendSoftphoneHeartbeat,
  unregisterSoftphone,
} from 'dashboard/api/protonVoice';

const HEARTBEAT_MS = 30000;

export function useProtonSoftphone() {
  const status = ref('idle'); // idle | registering | ready | ringing | in-call | error
  const incoming = ref(null); // { conversationId, from, reason, summary }
  const elapsed = ref(0);
  const muted = ref(false);

  let device = null;
  let call = null;
  let heartbeatTimer = null;
  let elapsedTimer = null;

  function startElapsed() {
    elapsed.value = 0;
    elapsedTimer = window.setInterval(() => {
      elapsed.value += 1;
    }, 1000);
  }

  function stopElapsed() {
    if (elapsedTimer) window.clearInterval(elapsedTimer);
    elapsedTimer = null;
  }

  function onIncoming(incomingCall) {
    call = incomingCall;
    // Context travels as <Parameter> children on the <Client> noun -- the
    // agent decides whether to accept before they can see the CRM.
    const params = incomingCall.customParameters || new Map();
    incoming.value = {
      conversationId: params.get('conversation_id') || '',
      from: incomingCall.parameters?.From || '',
      reason: params.get('reason') || '',
      summary: params.get('summary') || '',
    };
    status.value = 'ringing';

    incomingCall.on('accept', () => {
      status.value = 'in-call';
      startElapsed();
    });
    incomingCall.on('disconnect', reset);
    incomingCall.on('cancel', reset);
    incomingCall.on('reject', reset);
  }

  function reset() {
    stopElapsed();
    incoming.value = null;
    muted.value = false;
    call = null;
    status.value = device ? 'ready' : 'idle';
  }

  async function register() {
    try {
      status.value = 'registering';
      const { token } = await fetchAgentVoiceToken();
      device = new Device(token, { closeProtection: true });
      device.on('incoming', onIncoming);
      device.on('tokenWillExpire', async () => {
        try {
          const next = await fetchAgentVoiceToken();
          device.updateToken(next.token);
        } catch {
          // Next heartbeat cycle retries; an expired token only means this
          // tab drops out of the fan-out.
        }
      });
      device.on('error', () => {
        status.value = 'error';
      });
      await device.register();
      status.value = 'ready';
      await sendSoftphoneHeartbeat();
      heartbeatTimer = window.setInterval(() => {
        sendSoftphoneHeartbeat().catch(() => {});
      }, HEARTBEAT_MS);
    } catch {
      status.value = 'error';
    }
  }

  function accept() {
    call?.accept();
  }

  function reject() {
    call?.reject();
  }

  function hangup() {
    call?.disconnect();
  }

  function toggleMute() {
    if (!call) return;
    muted.value = !muted.value;
    call.mute(muted.value);
  }

  onBeforeUnmount(() => {
    if (heartbeatTimer) window.clearInterval(heartbeatTimer);
    stopElapsed();
    unregisterSoftphone().catch(() => {});
    device?.destroy();
  });

  return {
    status,
    incoming,
    elapsed,
    muted,
    register,
    accept,
    reject,
    hangup,
    toggleMute,
  };
}
```

- [ ] **Step 4: Write `ProtonSoftphonePanel.vue` into the patch**

```vue
<script setup>
// ProtonSoftphonePanel.vue — OUR file. Ring + in-call UI for a transferred
// phone call. Teleported to <body> so it floats above the app rather than
// being clipped by the sidebar it is mounted from.
import { computed, onMounted } from 'vue';
import { useRouter } from 'vue-router';
import { useProtonSoftphone } from 'dashboard/composables/useProtonSoftphone';
import Button from 'dashboard/components-next/button/Button.vue';

const router = useRouter();
const { status, incoming, elapsed, muted, register, accept, reject, hangup, toggleMute } =
  useProtonSoftphone();

onMounted(register);

const visible = computed(() => status.value === 'ringing' || status.value === 'in-call');

const elapsedLabel = computed(() => {
  const m = Math.floor(elapsed.value / 60);
  const s = elapsed.value % 60;
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
});

function openConversation() {
  if (incoming.value?.conversationId) {
    router.push({ name: 'inbox_conversation', params: { conversation_id: incoming.value.conversationId } });
  }
}
</script>

<template>
  <Teleport to="body">
    <div
      v-if="visible"
      class="fixed z-50 flex flex-col gap-3 p-4 shadow-lg bottom-6 ltr:right-6 rtl:left-6 w-80 rounded-xl bg-n-solid-1 border border-n-weak"
    >
      <div class="flex items-center justify-between">
        <span class="text-sm font-medium text-n-slate-12">
          {{ status === 'ringing' ? 'Incoming call' : 'On call' }}
        </span>
        <span v-if="status === 'in-call'" class="font-mono text-sm text-n-slate-11">
          {{ elapsedLabel }}
        </span>
      </div>

      <div class="text-base font-semibold text-n-slate-12">{{ incoming?.from || 'Unknown number' }}</div>
      <p v-if="incoming?.reason" class="text-sm text-n-slate-11">{{ incoming.reason }}</p>

      <button
        v-if="incoming?.conversationId"
        class="text-sm text-left underline text-n-brand"
        @click="openConversation"
      >
        Open conversation
      </button>

      <div v-if="status === 'ringing'" class="flex gap-2">
        <Button label="Accept" color="teal" class="flex-1" @click="accept" />
        <Button label="Reject" color="ruby" variant="faded" class="flex-1" @click="reject" />
      </div>
      <div v-else class="flex gap-2">
        <Button
          :label="muted ? 'Unmute' : 'Mute'"
          color="slate"
          variant="faded"
          class="flex-1"
          @click="toggleMute"
        />
        <Button label="Hang up" color="ruby" class="flex-1" @click="hangup" />
      </div>
    </div>
  </Teleport>
</template>
```

> `Button`'s prop names and the `n-*` colour tokens are Chatwoot's own. Verify them against a component the fork already patches (e.g. `ProtonSlaPoliciesPage.vue` in patch 0025) and adjust — a wrong prop renders a broken button, not a build error.

- [ ] **Step 5: Add the Sidebar.vue mount to the patch**

Two hunks. In the `<script setup>` imports:

```javascript
import ProtonSoftphonePanel from 'dashboard/components-next/softphone/ProtonSoftphonePanel.vue';
```

And immediately before `</aside>` (upstream line 1003):

```vue
      <!-- Transferred-call softphone. Rendered from the sidebar because the
           sidebar is on every dashboard page; the panel teleports itself to
           <body>, so it is not clipped by this aside. Gated twice: the
           `agent_softphone` feature AND the `voice.answer` permission. -->
      <ProtonSoftphonePanel
        v-if="protonHasFeature('agent_softphone') && protonHasPermission('voice.answer')"
      />
```

`protonHasFeature` and `protonHasPermission` are already destructured in Sidebar.vue by patches 0002 and 0025 — do not re-declare them.

- [ ] **Step 6: Verify the patch applies and builds**

```bash
cd deploy/chatwoot-fork && gcloud builds submit . --config cloudbuild.yaml \
  --substitutions _REGISTRY=<AR repo>,_PROTON_BUILD_SHA=$(git rev-parse --short HEAD)
```

Expected: the `applying 0068-agent-softphone.patch` line succeeds and `vite build` completes. **A failed `git apply` fails the build by design** — that is the verification. Never build this image locally on a Mac (arm64) or on the prod VM.

- [ ] **Step 7: Commit**

```bash
git add deploy/chatwoot-fork/Dockerfile deploy/chatwoot-fork/patches/0068-agent-softphone.patch
git commit -m "feat(fork): in-CRM agent softphone panel for transferred calls"
```

---

### Task 11: Manual verification plan

The automated suite covers token shape, resolver branches, TwiML, and routing. It cannot cover a real browser ringing on a real Twilio call — the same gap `backend/docs/testing/phone-channel-smoke-test.md` documents for the bridge.

**Files:**
- Create: `docs/testing/agent-softphone-verification.md`

- [ ] **Step 1: Write the verification plan**

Create the file with these scenarios, each stating setup, action, and the observable pass condition:

| # | Scenario | Pass condition |
|---|---|---|
| A | Agent opens the CRM with `voice.answer` granted | Panel hidden; backend logs `softphone_token_issued`; a heartbeat lands every 30s |
| B | Agent without `voice.answer` | No panel, no token request, `403` if forced |
| C | Call in, AI answers, asks for a human; conversation assigned to the open agent | That agent's browser rings within ~2s showing the caller number and the AI's reason |
| D | Agent accepts | Two-way audio; timer runs; `dial-status/fanout` receives `completed`; ACW entry logged |
| E | Agent rejects | Fan-out rings the other available agents; the rejecting agent does not ring again |
| F | Assignee's tab closed mid-call | Stage 1 fails fast; fan-out rings within the stage-1 timeout |
| G | Nobody registered | PSTN hunt group dials, exactly as before the feature |
| H | Nobody answers anywhere | Bilingual apology plays; conversation tagged `unanswered_handoff` |
| I | Two tabs open for one agent | Both ring; first accept wins; the other stops cleanly |
| J | Mute / hang up | Caller stops hearing the agent; hang-up ends the call for both |
| K | Flag off | Handoff dials the PSTN number; no softphone route reachable; no behavioural difference from today |

- [ ] **Step 2: Commit**

```bash
git add docs/testing/agent-softphone-verification.md
git commit -m "docs(testing): manual verification plan for the agent softphone"
```

---

## Self-Review Notes

**Spec coverage.** Every numbered component in the spec maps to a task: §1 agent token → Task 2 (+ endpoint in Task 4); §2 registry → Task 3; §3 resolver → Task 6 (+ its two required edits to existing code in Task 5); §4 fork UI → Task 10; §5 call outcome → Task 8. Spec sections without their own task are folded in where they belong: configuration → Task 1, security → Tasks 2/4, failure modes → the test lists in Tasks 3/6/8, the build constraint → Task 10 Step 1, rollout → Task 11.

**Two refinements to the spec, both discovered while reading the real code:**

1. The spec said the token endpoint uses `require_permission("voice.answer")`. The existing `require_permission` returns `None` and accepts a **shared secret** when `rbac_enabled` is off — neither works when the endpoint's whole job is minting a credential in a specific person's name. Task 4 adds `require_permission_with_identity`, which returns the user id and refuses the shared-secret path.
2. The spec's stage-1 `action` URL was `/webhooks/phone/dial-status`. It has to be the fan-out route, or an unanswered assignee ends the call instead of ringing anyone else. Task 8 Step 4 changes `PhoneBridge._dial_status_action_url()` accordingly, gated on the flag.

**Known soft spot.** Task 10's Vue code depends on Chatwoot's own `Button` props and `n-*` colour tokens, which are not verifiable from this checkout. Step 4 flags this and points at patch 0025 as the reference. Nothing else in the plan depends on unread upstream code.
