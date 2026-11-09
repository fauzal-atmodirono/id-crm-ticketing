# Bahana Investor Profiling (Feature B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the WhatsApp agent capture a nasabah's investment goal, horizon, drawdown reaction and experience through ordinary conversation, store them on the Chatwoot contact, and use them to personalize later replies — without ever touching the `risk_profile` that gates product eligibility.

**Architecture:** A fourth Gemini tool (`record_investor_preference`) alongside today's three. A pure canonicalization module turns the model's free-form arguments into a fixed vocabulary; a merge-aware Chatwoot write persists them as contact custom attributes; `customer_context` renders them back into the next turn's prompt. Everything is behind a default-off flag, and every write path is structurally incapable of setting `risk_profile`.

**Tech Stack:** Python 3.11+, FastAPI, `google-genai` (`types.FunctionDeclaration`), SQLAlchemy 2.0 async, pytest with `asyncio_mode=auto`, `respx` for HTTP stubbing.

**Spec:** `docs/superpowers/specs/2026-08-26-bahana-advisory-personalization-design.md` — this plan implements **Stage 1** of §8 (feature B, §5). Stages 2–6 are separate plans; stages 3–6 are blocked on the data feed in §9.

## Global Constraints

- All commands run from `agent/`. Tests: `pytest`, no flags needed.
- **The conversational profile never writes `risk_profile`** (spec §2.3, §5.3). No function in this plan accepts or emits that key. Task 7 pins it.
- **`chatwoot.update_contact` REPLACES `custom_attributes` wholesale**, it does not merge (see its docstring at `app/clients/chatwoot.py:101`). Every write in this plan goes through the merge helper from Task 3. Sending a subset silently deletes every other attribute on the contact.
- **Background tasks never raise** for expected "nothing to do" cases (`CLAUDE.md`). Log and return.
- New env vars go in **both** `app/config.py` and `deploy/tenants/example.env`, names matching case-insensitively and verbatim.
- Attribute keys are a contract shared by four places that never import each other: `app/services/customer_context.py::_PROFILE_FIELDS`, `app/services/investor_profile.py`, `deploy/scripts/seed_demo_data/client.py::build_nasabah_custom_attributes`, and `deploy/scripts/bahana_bq_to_crm_sync.py`. A typo does not error — it silently blanks a sidebar row.
- Default off. With `INVESTOR_PROFILING_ENABLED=false`, behaviour must be byte-identical to today for every existing tenant.
- Match the house style: module docstrings explain the *why* and the idempotency/concurrency reasoning.

---

### Task 1: Canonicalize the four answers

A pure module: model arguments in, fixed vocabulary out. Pure so the vocabulary can be asserted directly without a conversation, a model, or a network.

**Files:**
- Create: `agent/app/services/investor_profile.py`
- Test: `agent/tests/test_investor_profile.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `ATTRIBUTE_KEYS: tuple[str, ...]` — `("investor_goal", "investor_horizon", "investor_drawdown_reaction", "investor_experience", "preference_captured_at")`
  - `canonical_attributes(args: object, captured_at: str) -> dict[str, str]` — the attributes to merge onto the contact; `{}` when nothing recognisable was supplied.
  - `implied_risk_tier(args: object) -> int | None` — 1/2/3 (Konservatif/Moderat/Agresif) implied by the drawdown answer, or `None`. Used only to flag divergence for a human; never to set a profile.

- [ ] **Step 1: Write the failing test**

```python
"""Feature B canonicalization: the model's free-form answers become a fixed
vocabulary, or nothing at all."""

from app.services import investor_profile as ip


def test_maps_each_answer_to_its_indonesian_display_value():
    out = ip.canonical_attributes(
        {
            "goal": "retirement",
            "horizon": "very_long",
            "drawdown_reaction": "hold",
            "experience": "beginner",
        },
        captured_at="2026-08-26T10:00:00Z",
    )
    assert out == {
        "investor_goal": "Dana pensiun",
        "investor_horizon": "> 10 tahun",
        "investor_drawdown_reaction": "Tetap menahan",
        "investor_experience": "Pemula",
        "preference_captured_at": "2026-08-26T10:00:00Z",
    }


def test_unknown_values_are_dropped_not_guessed():
    out = ip.canonical_attributes(
        {"goal": "buying a yacht", "experience": "beginner"},
        captured_at="2026-08-26T10:00:00Z",
    )
    assert "investor_goal" not in out
    assert out["investor_experience"] == "Pemula"


def test_nothing_recognisable_yields_no_attributes_at_all():
    # Not even the timestamp: an empty capture must not stamp the contact,
    # or the sidebar shows "profiled" for a conversation that captured nothing.
    assert ip.canonical_attributes({"goal": "???"}, captured_at="x") == {}
    assert ip.canonical_attributes(None, captured_at="x") == {}
    assert ip.canonical_attributes("not a dict", captured_at="x") == {}


def test_never_emits_risk_profile():
    out = ip.canonical_attributes(
        {"risk_profile": "Agresif", "experience": "experienced"},
        captured_at="2026-08-26T10:00:00Z",
    )
    assert "risk_profile" not in out
    assert set(out) <= set(ip.ATTRIBUTE_KEYS)


def test_implied_risk_tier_reads_the_drawdown_answer():
    assert ip.implied_risk_tier({"drawdown_reaction": "sell_all"}) == 1
    assert ip.implied_risk_tier({"drawdown_reaction": "hold"}) == 2
    assert ip.implied_risk_tier({"drawdown_reaction": "buy_more"}) == 3
    assert ip.implied_risk_tier({"drawdown_reaction": "shrug"}) is None
    assert ip.implied_risk_tier({}) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_investor_profile.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.investor_profile'`

- [ ] **Step 3: Write minimal implementation**

```python
"""Turn the model's answers to the four profiling questions into a fixed
vocabulary of contact attributes.

Pure and side-effect free, for the same reason `customer_context` is: the
orchestrator does the writing, this decides what is allowed to be written.

Two decisions are load-bearing:

**An unrecognised answer is dropped, never guessed.** The model is asked for an
enum and will occasionally return prose. Mapping "something like retirement I
guess" onto `Dana pensiun` invents a fact about a customer; leaving the field
empty asks the question again next turn, which is the cheaper mistake.

**`risk_profile` cannot be written from here.** There is no mapping for it and
`canonical_attributes` returns only keys in `ATTRIBUTE_KEYS`, so no prompt, no
model output and no future caller can reach the field that gates product
eligibility (design spec §2.3, §5.3). `implied_risk_tier` exists to tell a
human that the customer's answers and their KYC record disagree -- it is a
notification, not a write.
"""

from __future__ import annotations

_GOALS = {
    "retirement": "Dana pensiun",
    "education": "Pendidikan",
    "house": "Membeli rumah",
    "wealth_growth": "Pertumbuhan aset",
    "emergency_fund": "Dana darurat",
    "other": "Lainnya",
}

_HORIZONS = {
    "short": "< 1 tahun",
    "medium": "1-3 tahun",
    "long": "3-10 tahun",
    "very_long": "> 10 tahun",
}

_DRAWDOWN = {
    "sell_all": "Menjual seluruhnya",
    "sell_some": "Menjual sebagian",
    "hold": "Tetap menahan",
    "buy_more": "Menambah posisi",
}

_EXPERIENCE = {
    "beginner": "Pemula",
    "intermediate": "Menengah",
    "experienced": "Berpengalaman",
}

# Which risk tier each drawdown answer implies. Used ONLY to raise a review
# flag for a human when it disagrees with the KYC record -- see the module
# docstring. 1 = Konservatif, 2 = Moderat, 3 = Agresif.
_IMPLIED_TIER = {"sell_all": 1, "sell_some": 1, "hold": 2, "buy_more": 3}

_FIELDS: tuple[tuple[str, str, dict[str, str]], ...] = (
    ("goal", "investor_goal", _GOALS),
    ("horizon", "investor_horizon", _HORIZONS),
    ("drawdown_reaction", "investor_drawdown_reaction", _DRAWDOWN),
    ("experience", "investor_experience", _EXPERIENCE),
)

ATTRIBUTE_KEYS: tuple[str, ...] = tuple(
    attribute for _, attribute, _ in _FIELDS
) + ("preference_captured_at",)


def _lookup(args: dict, arg_name: str, vocabulary: dict[str, str]) -> str | None:
    raw = args.get(arg_name)
    if not isinstance(raw, str):
        return None
    return vocabulary.get(raw.strip().lower())


def canonical_attributes(args: object, captured_at: str) -> dict[str, str]:
    """The contact attributes to merge for this capture, or `{}`.

    Accepts `object` rather than `dict` because this is fed straight from a
    model's function-call arguments, where a null or a scalar can appear where
    an object belongs. Returning `{}` is the fail-open path.
    """
    if not isinstance(args, dict):
        return {}

    out: dict[str, str] = {}
    for arg_name, attribute, vocabulary in _FIELDS:
        value = _lookup(args, arg_name, vocabulary)
        if value is not None:
            out[attribute] = value

    if not out:
        return {}
    out["preference_captured_at"] = captured_at
    return out


def implied_risk_tier(args: object) -> int | None:
    """The risk tier this customer's drawdown answer implies, or None.

    Never written anywhere near `risk_profile`. Its only consumer applies a
    review label so a licensed human can reconcile the disagreement.
    """
    if not isinstance(args, dict):
        return None
    raw = args.get("drawdown_reaction")
    if not isinstance(raw, str):
        return None
    return _IMPLIED_TIER.get(raw.strip().lower())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_investor_profile.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add app/services/investor_profile.py tests/test_investor_profile.py
git commit -m "feat(bahana): canonicalize the four investor-profiling answers"
```

---

### Task 2: Feature flag

**Files:**
- Modify: `agent/app/config.py:53` (beside `demo_persona_slugs_enabled`)
- Modify: `deploy/tenants/example.env:48` (beside `DEMO_PERSONA_SLUGS_ENABLED`)
- Test: `agent/tests/test_investor_profile.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Settings.investor_profiling_enabled: bool` (default `False`).

- [ ] **Step 1: Write the failing test**

Append to `agent/tests/test_investor_profile.py`:

```python
def test_profiling_is_off_unless_a_tenant_opts_in():
    from app.config import Settings

    assert Settings().investor_profiling_enabled is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_investor_profile.py::test_profiling_is_off_unless_a_tenant_opts_in -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'investor_profiling_enabled'`

- [ ] **Step 3: Write minimal implementation**

In `agent/app/config.py`, directly after `demo_persona_slugs_enabled: bool = False`:

```python
    investor_profiling_enabled: bool = False
```

In `deploy/tenants/example.env`, directly after the `DEMO_PERSONA_SLUGS_ENABLED=false` line and its comment block:

```bash
# When true, the agent-bot may ask the four investor-profiling questions
# (goal, horizon, reaction to a 20% drawdown, experience) in conversation and
# store the answers on the contact. These shape tone and which eligible
# products surface first; they NEVER change the risk_profile that gates
# eligibility -- that stays the KYC record from account opening.
INVESTOR_PROFILING_ENABLED=false
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_investor_profile.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add app/config.py ../deploy/tenants/example.env tests/test_investor_profile.py
git commit -m "feat(bahana): INVESTOR_PROFILING_ENABLED, default off"
```

---

### Task 3: A contact write that merges instead of replacing

`update_contact` sends whatever it is given and Chatwoot replaces the whole `custom_attributes` object. The persona-slug path gets away with it because it writes a complete fixture. Feature B writes four keys onto a contact that already carries a portfolio, so it must read first.

**Files:**
- Modify: `agent/app/clients/chatwoot.py` (add after `update_contact`, ends line 115)
- Test: `agent/tests/test_investor_profile_wiring.py`

**Interfaces:**
- Consumes: `ChatwootClient.get_contact`, `ChatwootClient.update_contact`.
- Produces: `async ChatwootClient.merge_contact_attributes(contact_id: int, attributes: dict[str, str]) -> bool` — `True` if a write happened, `False` if there was nothing to change.

- [ ] **Step 1: Write the failing test**

```python
"""Feature B wiring: the contact write merges, and the orchestrator dispatches."""

import httpx
import pytest
import respx

from app.clients.chatwoot import ChatwootClient


def _client() -> ChatwootClient:
    return ChatwootClient(
        base_url="http://cw.test", api_access_token="t", account_id=1
    )


@respx.mock
async def test_merge_preserves_attributes_it_was_not_given():
    respx.get("http://cw.test/api/v1/accounts/1/contacts/7").mock(
        return_value=httpx.Response(
            200,
            json={
                "payload": {
                    "id": 7,
                    "custom_attributes": {
                        "risk_profile": "Konservatif",
                        "holdings": "BBCA, BBRI",
                    },
                }
            },
        )
    )
    route = respx.put("http://cw.test/api/v1/accounts/1/contacts/7").mock(
        return_value=httpx.Response(200, json={})
    )

    client = _client()
    wrote = await client.merge_contact_attributes(7, {"investor_horizon": "> 10 tahun"})
    await client.aclose()

    assert wrote is True
    sent = route.calls[0].request
    import json as _json

    body = _json.loads(sent.content)["custom_attributes"]
    # The portfolio the contact already carried must survive the write.
    assert body["risk_profile"] == "Konservatif"
    assert body["holdings"] == "BBCA, BBRI"
    assert body["investor_horizon"] == "> 10 tahun"


@respx.mock
async def test_merge_writes_nothing_when_given_nothing():
    get = respx.get("http://cw.test/api/v1/accounts/1/contacts/7").mock(
        return_value=httpx.Response(200, json={"payload": {"id": 7}})
    )
    put = respx.put("http://cw.test/api/v1/accounts/1/contacts/7").mock(
        return_value=httpx.Response(200, json={})
    )

    client = _client()
    wrote = await client.merge_contact_attributes(7, {})
    await client.aclose()

    assert wrote is False
    assert not put.called
    assert not get.called
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_investor_profile_wiring.py -v`
Expected: FAIL — `AttributeError: 'ChatwootClient' object has no attribute 'merge_contact_attributes'`

- [ ] **Step 3: Write minimal implementation**

Add to `agent/app/clients/chatwoot.py` immediately after `update_contact`:

```python
    async def merge_contact_attributes(
        self, contact_id: int, attributes: dict[str, str]
    ) -> bool:
        """Add `attributes` to a contact, keeping everything already there.

        `update_contact` REPLACES `custom_attributes`, so a caller writing a
        subset silently deletes the rest -- for a Bahana nasabah that is their
        whole portfolio. This reads the current object and merges over it.

        Returns True if a write happened. An empty `attributes` short-circuits
        before the GET: a capture that recognised nothing must not cost two
        API calls, and must not touch the contact at all.

        Not atomic, and deliberately not: Chatwoot has no compare-and-swap on
        contacts. A concurrent write between the GET and the PUT loses. The
        exposure is one contact's attributes during a single chat turn, and
        the alternative is a lock we would have to hold across a network call.
        """
        if not attributes:
            return False
        contact = await self.get_contact(contact_id)
        body = contact.get("payload") if isinstance(contact, dict) else None
        body = body if isinstance(body, dict) else contact
        current = body.get("custom_attributes") if isinstance(body, dict) else None
        merged = dict(current) if isinstance(current, dict) else {}
        merged.update(attributes)
        await self.update_contact(contact_id, {"custom_attributes": merged})
        return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_investor_profile_wiring.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add app/clients/chatwoot.py tests/test_investor_profile_wiring.py
git commit -m "feat(bahana): merge-aware contact attribute write"
```

---

### Task 4: The tool declaration

**Files:**
- Modify: `agent/app/ai/tools.py` (add declaration; extend `TOOLS`)
- Test: `agent/tests/test_investor_profile.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `tools.RECORD_INVESTOR_PREFERENCE: types.FunctionDeclaration`, name `"record_investor_preference"`, registered in `tools.TOOLS`.

- [ ] **Step 1: Write the failing test**

Append to `agent/tests/test_investor_profile.py`:

```python
def test_the_profiling_tool_is_offered_and_cannot_take_a_risk_profile():
    from app.ai import tools

    declarations = tools.TOOLS[0].function_declarations
    by_name = {d.name: d for d in declarations}
    assert "record_investor_preference" in by_name

    properties = by_name["record_investor_preference"].parameters.properties
    # The gate stays on the KYC record: the model has no argument for it.
    assert "risk_profile" not in properties
    assert set(properties) == {
        "goal",
        "horizon",
        "drawdown_reaction",
        "experience",
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_investor_profile.py::test_the_profiling_tool_is_offered_and_cannot_take_a_risk_profile -v`
Expected: FAIL — `AssertionError` on `"record_investor_preference" in by_name`

- [ ] **Step 3: Write minimal implementation**

In `agent/app/ai/tools.py`, add before `TOOLS`:

```python
RECORD_INVESTOR_PREFERENCE = types.FunctionDeclaration(
    name="record_investor_preference",
    description=(
        "Record what the customer just told you about how they invest: their "
        "goal, when they need the money, how they would react to a 20% drop, "
        "and how experienced they are. Use this only for answers the customer "
        "actually gave in this conversation -- never infer them. Supply only "
        "the fields they answered; the rest can be asked later. This records "
        "preferences for personalization only and does NOT change the "
        "customer's official risk profile."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "goal": types.Schema(
                type=types.Type.STRING,
                enum=[
                    "retirement",
                    "education",
                    "house",
                    "wealth_growth",
                    "emergency_fund",
                    "other",
                ],
                description="What the customer is investing for.",
            ),
            "horizon": types.Schema(
                type=types.Type.STRING,
                enum=["short", "medium", "long", "very_long"],
                description=(
                    "When they need the money: short <1y, medium 1-3y, "
                    "long 3-10y, very_long >10y."
                ),
            ),
            "drawdown_reaction": types.Schema(
                type=types.Type.STRING,
                enum=["sell_all", "sell_some", "hold", "buy_more"],
                description="What they said they would do if their portfolio fell 20%.",
            ),
            "experience": types.Schema(
                type=types.Type.STRING,
                enum=["beginner", "intermediate", "experienced"],
                description="How experienced an investor they say they are.",
            ),
        },
        required=[],
    ),
)
```

And extend the registration:

```python
TOOLS = [
    types.Tool(
        function_declarations=[
            SEND_REPLY,
            ESCALATE_TO_TICKET,
            HANDOFF_TO_HUMAN,
            RECORD_INVESTOR_PREFERENCE,
        ]
    )
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_investor_profile.py tests/test_gemini.py -v`
Expected: all passed (`test_gemini.py` is included because it asserts against the forced-function-calling action space).

- [ ] **Step 5: Commit**

```bash
git add app/ai/tools.py tests/test_investor_profile.py
git commit -m "feat(bahana): record_investor_preference tool declaration"
```

---

### Task 5: Dispatch the new action

`_execute_decision` currently branches on `send_reply` / `escalate_to_ticket` / `handoff_to_human`. It needs a fourth branch, and it needs the contact id, which it does not currently receive.

**Files:**
- Modify: `agent/app/services/orchestrator.py:911` (`_execute_decision` signature and branches)
- Modify: `agent/app/services/orchestrator.py:570` (the call site — pass `contact_id`)
- Modify: `agent/app/services/orchestrator.py:494` (capture `contact_id` into a variable that outlives the `try`)
- Test: `agent/tests/test_investor_profile_wiring.py`

**Interfaces:**
- Consumes: `investor_profile.canonical_attributes`, `investor_profile.implied_risk_tier`, `ChatwootClient.merge_contact_attributes`, `ChatwootClient.add_labels`.
- Produces: `_execute_decision(conversation_id, decision, mode: str, chatwoot, *, handoff_message: str = "", contact_id: int | None = None, recorded_risk_profile: str = "")`.

- [ ] **Step 1: Write the failing test**

Append to `agent/tests/test_investor_profile_wiring.py`:

```python
class _FakeChatwoot:
    def __init__(self):
        self.merged = None
        self.labels = []
        self.messages = []

    async def merge_contact_attributes(self, contact_id, attributes):
        self.merged = (contact_id, attributes)
        return bool(attributes)

    async def add_labels(self, conversation_id, labels):
        self.labels.append((conversation_id, labels))

    async def create_message(self, conversation_id, text, **kwargs):
        self.messages.append(text)

    async def toggle_status(self, *args, **kwargs):
        pass


async def test_recording_a_preference_writes_the_contact_and_replies(monkeypatch):
    from app.ai.gemini import Decision
    from app.services import orchestrator

    monkeypatch.setattr(
        orchestrator, "_utc_now_iso", lambda: "2026-08-26T10:00:00Z"
    )
    cw = _FakeChatwoot()
    decision = Decision(
        action="record_investor_preference",
        args={"horizon": "very_long", "experience": "beginner"},
    )

    await orchestrator._execute_decision(
        99, decision, "auto", cw, contact_id=7
    )

    contact_id, attributes = cw.merged
    assert contact_id == 7
    assert attributes["investor_horizon"] == "> 10 tahun"
    assert attributes["investor_experience"] == "Pemula"
    assert attributes["preference_captured_at"] == "2026-08-26T10:00:00Z"
    assert "risk_profile" not in attributes


async def test_a_divergent_answer_flags_a_human_and_changes_nothing():
    from app.ai.gemini import Decision
    from app.services import orchestrator

    cw = _FakeChatwoot()
    # Customer says they would buy more at -20%: implies Agresif (tier 3).
    decision = Decision(
        action="record_investor_preference",
        args={"drawdown_reaction": "buy_more"},
    )

    await orchestrator._execute_decision(
        99, decision, "auto", cw, contact_id=7, recorded_risk_profile="Konservatif"
    )

    assert cw.labels == [(99, ["profile_review"])]
    assert "risk_profile" not in cw.merged[1]


async def test_no_contact_id_is_a_skip_not_a_crash():
    from app.ai.gemini import Decision
    from app.services import orchestrator

    cw = _FakeChatwoot()
    decision = Decision(
        action="record_investor_preference", args={"experience": "beginner"}
    )

    await orchestrator._execute_decision(99, decision, "auto", cw, contact_id=None)

    assert cw.merged is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_investor_profile_wiring.py -v`
Expected: FAIL — `TypeError: _execute_decision() got an unexpected keyword argument 'contact_id'`

- [ ] **Step 3: Write minimal implementation**

At the top of `agent/app/services/orchestrator.py`, beside the other service imports (near line 34):

```python
from app.services import investor_profile
```

Add a seam for the timestamp so the test can pin it, near the other module-level helpers:

```python
def _utc_now_iso() -> str:
    """Capture time as an ISO-8601 Z string. A function rather than an inline
    call so a test can pin it without freezing the clock globally."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )
```

(Add `from datetime import datetime, timezone` to the imports if it is not already present.)

Change the `_execute_decision` signature at line 911:

```python
async def _execute_decision(
    conversation_id, decision, mode: str, chatwoot, *, handoff_message: str = "",
    contact_id: int | None = None, recorded_risk_profile: str = "",
) -> None:
```

Add the new branch alongside the existing `if decision.action == ...` chain:

```python
    if decision.action == "record_investor_preference":
        # Personalization only. The four answers land on the contact so the
        # sidebar and the next turn's prompt both see them; the risk_profile
        # that gates eligibility is untouched by construction -- there is no
        # mapping for it in investor_profile (design spec §2.3, §5.3).
        if contact_id is None:
            logger.info(
                "orchestrator: no contact for conversation %s; "
                "cannot record investor preference",
                conversation_id,
            )
            return
        attributes = investor_profile.canonical_attributes(
            decision.args, captured_at=_utc_now_iso()
        )
        if not attributes:
            logger.info(
                "orchestrator: investor preference for conversation %s "
                "recognised no answers; nothing written",
                conversation_id,
            )
            return
        await chatwoot.merge_contact_attributes(contact_id, attributes)

        # Their answers and their KYC record disagree -> tell a human. This
        # notifies; it never reconciles. Only a licensed person changes a
        # risk profile.
        tier = investor_profile.implied_risk_tier(decision.args)
        recorded_tier = {"Konservatif": 1, "Moderat": 2, "Agresif": 3}.get(
            (recorded_risk_profile or "").strip().title()
        )
        if tier is not None and recorded_tier is not None and tier > recorded_tier:
            await chatwoot.add_labels(conversation_id, ["profile_review"])
        return
```

At the call site (line 570), pass the contact through. Immediately before `system_prompt = _build_system_prompt(...)`, the `contact_id` resolved inside the `try` must be hoisted so it survives: initialise `resolved_contact_id: int | None = None` beside `customer_context = ""`, assign `resolved_contact_id = int(contact_id)` where the contact is fetched, and pass it:

```python
        await _execute_decision(
            conversation_id, decision, effective_mode, chatwoot,
            handoff_message=handoff_message,
            contact_id=resolved_contact_id,
            recorded_risk_profile=resolved_risk_profile,
        )
```

where `resolved_risk_profile` is set in the same place from the fetched attributes:

```python
            attributes = (
                body.get("custom_attributes") if isinstance(body, dict) else None
            )
            resolved_risk_profile = (
                str(attributes.get("risk_profile") or "")
                if isinstance(attributes, dict)
                else ""
            )
            customer_context = format_customer_context(attributes)
```

Initialise `resolved_risk_profile = ""` beside `customer_context = ""`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_investor_profile_wiring.py tests/test_orchestrator.py -v`
Expected: all passed. `test_orchestrator.py` must be green — the signature change is keyword-only with defaults precisely so existing call sites and tests are unaffected.

- [ ] **Step 5: Commit**

```bash
git add app/services/orchestrator.py tests/test_investor_profile_wiring.py
git commit -m "feat(bahana): dispatch record_investor_preference to the contact"
```

---

### Task 6: Render the preferences back into the prompt

**Files:**
- Modify: `agent/app/services/customer_context.py` (`_PROFILE_FIELDS`, `_PROFILE_INSTRUCTIONS`)
- Test: `agent/tests/test_customer_context.py`

**Interfaces:**
- Consumes: attribute keys from Task 1.
- Produces: no new symbols; `format_customer_context` output gains three rows and one instruction sentence.

- [ ] **Step 1: Write the failing test**

Append to `agent/tests/test_customer_context.py`:

```python
def test_renders_the_conversational_investor_profile():
    out = format_customer_context(
        {
            "risk_profile": "Moderat",
            "investor_horizon": "> 10 tahun",
            "investor_experience": "Pemula",
            "investor_goal": "Dana pensiun",
        }
    )
    assert "Investment horizon: > 10 tahun" in out
    assert "Investing experience: Pemula" in out
    assert "Stated goal: Dana pensiun" in out
    # Experience must steer how much is explained, or the field is decoration.
    assert "experience" in out.lower()


def test_a_contact_with_no_investor_fields_is_unchanged():
    # Every other tenant's contacts carry none of these keys. Their prompt
    # must not gain an empty row.
    out = format_customer_context({"risk_profile": "Moderat"})
    assert "Investment horizon" not in out
    assert "Investing experience" not in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_customer_context.py -v`
Expected: FAIL — `AssertionError` on `"Investment horizon: > 10 tahun" in out`

- [ ] **Step 3: Write minimal implementation**

In `agent/app/services/customer_context.py`, extend `_PROFILE_FIELDS` (append, so existing field order is untouched):

```python
    ("product_gaps", "Products not yet held"),
    ("investor_goal", "Stated goal"),
    ("investor_horizon", "Investment horizon"),
    ("investor_experience", "Investing experience"),
)
```

Extend `_PROFILE_INSTRUCTIONS` by appending one sentence to the existing string:

```python
    "Where an investing-experience level is recorded, match your explanation "
    "to it: explain what a product actually is to a beginner, and do not "
    "explain the basics to an experienced investor. The stated goal and "
    "horizon are what the customer told us in conversation, not their "
    "official risk profile -- use them for framing, never to justify a "
    "product their risk profile does not allow."
```

Note the existing loop skips any field whose value is absent or blank, so contacts without these keys are unaffected with no further change.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_customer_context.py tests/test_customer_context_wiring.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add app/services/customer_context.py tests/test_customer_context.py
git commit -m "feat(bahana): investor preferences shape tone in the prompt"
```

---

### Task 7: Pin the guarantee, and ask the questions

The spec's central promise for feature B is a negative — nothing here can change what a customer is allowed to be offered. Negatives rot silently unless a test holds them. This task also adds the prompt text that makes the agent actually ask.

**Files:**
- Modify: `agent/app/services/orchestrator.py:77` (the base system prompt's action-space sentence)
- Test: `agent/tests/test_investor_profile.py`

**Interfaces:**
- Consumes: everything above.
- Produces: nothing new.

- [ ] **Step 1: Write the failing test**

Append to `agent/tests/test_investor_profile.py`:

```python
def test_no_profiling_path_can_reach_risk_profile():
    """The one guarantee feature B makes (design spec §2.3, §5.3).

    Asserted three ways, because each could regress independently: the tool
    cannot accept it, the canonicalizer cannot emit it, and the attribute
    contract does not contain it.
    """
    from app.ai import tools
    from app.services import investor_profile as ip

    by_name = {d.name: d for d in tools.TOOLS[0].function_declarations}
    assert "risk_profile" not in by_name["record_investor_preference"].parameters.properties
    assert "risk_profile" not in ip.ATTRIBUTE_KEYS

    hostile = {
        "goal": "retirement",
        "risk_profile": "Agresif",
        "investor_risk_profile": "Agresif",
    }
    assert "risk_profile" not in ip.canonical_attributes(hostile, captured_at="x")


def test_the_agent_is_told_the_profiling_tool_exists():
    from app.services.orchestrator import _build_system_prompt

    prompt = _build_system_prompt(None, "")
    assert "record_investor_preference" in prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_investor_profile.py -v`
Expected: `test_no_profiling_path_can_reach_risk_profile` PASSES (the design already guarantees it — that is the point of pinning it), `test_the_agent_is_told_the_profiling_tool_exists` FAILS.

- [ ] **Step 3: Write minimal implementation**

In `agent/app/services/orchestrator.py`, extend the action-space sentence in the base system prompt (around line 77–79) by adding one clause after the `handoff_to_human` description:

```python
    "or record_investor_preference when the customer tells you what they are "
    "investing for, when they need the money, how they would react to a 20% "
    "drop, or how experienced they are. Ask at most one of those four "
    "questions per reply, only when the conversation gives you a natural "
    "opening, and never instead of answering what they asked. "
```

- [ ] **Step 4: Run the whole suite**

Run: `pytest`
Expected: all passed, including every pre-existing test. Any failure in `test_orchestrator*.py` or `test_gemini.py` means the action space or the base prompt changed in a way an existing test pinned — read that test before changing it, since several of them encode deliberate behaviour.

- [ ] **Step 5: Commit**

```bash
git add app/services/orchestrator.py tests/test_investor_profile.py
git commit -m "feat(bahana): ask the profiling questions; pin the risk_profile guarantee"
```

---

## Verification before calling this done

- [ ] `pytest` — full suite green from `agent/`.
- [x] With `INVESTOR_PROFILING_ENABLED` unset, `Settings().investor_profiling_enabled is False` — pinned by `test_profiling_is_off_unless_a_tenant_opts_in`.
- [x] `INVESTOR_PROFILING_ENABLED` documented in `deploy/tenants/example.env` under the same name used in `app/config.py`.

**The "known gap" was resolved during execution, not shipped.** The plan as
written appended the tool to `TOOLS`, which would have offered it to every
tenant and made "default off" untrue at the only layer that matters — the one
the model sees. What was built instead:

- `TOOLS` stays byte-identical at three declarations; `TOOLS_WITH_PROFILING`
  is a separate four-declaration list (`test_the_default_action_space_is_unchanged`).
- `gemini.decide` gained an optional `tools` override, but the orchestrator
  passes it **only** when the flag is on (`_decide_kwargs`). The disabled path
  is therefore the same call today makes, with the same arity — which is also
  why none of the 13 existing `decide` stubs needed touching.
- The prompt clause is gated by the same flag
  (`_build_system_prompt(..., profiling_enabled=...)`). Advertising an action
  the model was not given makes it try to call one that isn't there, and
  `_extract_decision` turns that into a handoff, so prompt and tool list must
  be gated together.

One small addition beyond the plan: `investor_profile.recorded_risk_tier`,
so the Konservatif/Moderat/Agresif ordering lives in one module rather than
being re-derived in the orchestrator. Tested, and it refuses to guess a tier
for an unrecognised profile.

---

## Subsequent plans

Each is its own plan and its own working deliverable (spec §8):

| Stage | Plan | Blocked on |
|---|---|---|
| 2 | RM suggestion queue — backend router, `suggestion` table, Chatwoot fork patch, built against fixtures | Nothing. **Cloud Build deploy path** — budget it. |
| 3 | Feed ingestion — §3 tables, `v_portfolio_exposure` | Bahana feed (spec §9.1–9.3) |
| 4 | Feature A — concentration into the profile, sync, prompt | Stage 3 |
| 5 | C deterministic — candidate universe, `base_score`, allocator | Stage 3 |
| 6 | C1 LLM ranker — forced tool call, validation, replay harness | Stage 5 |
