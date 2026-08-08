# P1 — Working-Hours SLA Enforcement & After-Hours Instrumentation

**Date:** 2026-08-08
**Programme:** `docs/superpowers/specs/2026-08-08-rfp-partials-program-design.md`
**Plan:** `docs/superpowers/plans/2026-08-08-rfp-p1-working-hours-sla.md`
**Closes:** 15 PARTIAL requirements + 1 GAP (4.52) as a side effect
**Effort:** 1–2 weeks · **Wave:** 1 · **Blocked by:** nothing

---

## 1. The problem, precisely

The system contains two clocks and uses the wrong one for enforcement.

`backend/.../features/metrics/business_hours.py::working_minutes_between` sums
the minutes between two timestamps that fall inside a Chatwoot inbox's native
`working_hours` config. It is correct, tested, and already used by the reporting
sync to populate `first_response_working_minutes` and
`resolution_working_minutes` on every conversation row in BigQuery.

`backend/.../features/chat/sla.py::_conversation_age_seconds` computes
`(now - created_at).total_seconds()` — straight through nights, weekends and
public holidays — and every threshold comparison in `scan_conversations` is
against that number.

Every SLA target PRO-NET has written down is in **working** hours: "acknowledge
within 2 working hours" (B-SM-06), "update the customer within 4 working hours"
(B-EM-05), "complaint resolution `<24wh` / 24–48 / 48–72 / `>72wh`" (C1-12 #7),
"social media response `<2WH`" (#13), "email response `<2WD`" (#14).

So today: a WhatsApp message arriving at 18:00 Friday breaches its 2-hour
acknowledgement target at 20:00 Friday, alerting a PIC who is not working, and
is recorded as a breach. The *report* on that same case, computed from
`first_response_working_minutes`, will say it was answered in 30 working
minutes — comfortably inside target. **Both numbers are in the system, they
disagree, and the disagreement is visible to the client.**

A second, smaller defect compounds it. `agent/app/services/lifecycle_scanner.py`
computes `in_hours = business_hours.is_within_business_hours(inbox, now)` at
line 143, uses it to pick between two auto-close grace values, and discards it.
Nothing writes it to the conversation. Consequently §4.52 (after-hours case
volume) is classified GAP not because the system cannot tell in-hours from
out-of-hours — it does so on every scan — but because it never remembers.

A third: there is **no acknowledgement event**. `_has_first_agent_response`
infers acknowledgement from Chatwoot's `first_reply_created_at` or a prior
`FIRST_RESPONSE` audit transition. That is a *reply*, not an *acknowledgement*.
Appendix B's SOP distinguishes them — B-WA-14 gives the agent 2 minutes to
acknowledge and B-EM-05 gives 4 working hours to *update* — and the escalation
ladder's "no acknowledgement received" condition is currently unrepresentable.

## 2. What this package delivers

1. **One clock.** SLA enforcement measures elapsed **working** minutes, using the
   same helper the reporting side already uses.
2. **A persisted in-hours flag** stamped at intake, so after-hours volume and
   business-vs-non-business response time become reportable.
3. **A real acknowledgement event**, distinct from the first reply.
4. **Next-business-hour scheduling**, so "attend next business hour" (B-WA-10,
   B-EM-04) means something.
5. **The two views** that read the working-minutes columns already being stored.
6. **Appendix B's after-hours reply text**, provisioned and asserted.

## 3. Design

### 3.1 Working-hours enforcement in `sla.py`

The obstacle is not the arithmetic — `working_minutes_between` does that — it is
that `scan_conversations` never sees an inbox. It iterates conversations from
`fetch_conversations(settings)`; each carries `inbox_id`, and nothing fetches the
inbox record that holds `working_hours`, `working_hours_enabled` and `timezone`.

**Design: a per-scan inbox cache, injected.**

```python
# features/chat/sla_clock.py  (new, pure + one I/O collaborator)

class InboxCache:
    """Fetch-once-per-scan cache of Chatwoot inbox records.

    scan_conversations iterates hundreds of conversations across a handful of
    inboxes; fetching per conversation would multiply the API calls by ~100x
    for no new information. Scoped to a single scan so an operator's
    working-hours edit takes effect on the next sweep, not on a restart.
    """
    async def get(self, inbox_id: int | None) -> dict: ...


def elapsed_minutes(
    start: datetime, now: datetime, inbox: dict, *, working_hours: bool
) -> float:
    """The single entry point every threshold comparison goes through.

    working_hours=False reproduces today's wall-clock arithmetic exactly,
    to the second. That is what makes the flag safe to default off.
    """
```

`scan_conversations` gains one parameter, `inbox_cache: InboxCache | None = None`,
and `_conversation_age_seconds` is replaced by a call to `elapsed_minutes`. When
`sla_working_hours_enabled` is false — the default — `elapsed_minutes` returns
`(now - start).total_seconds() / 60`, which is today's behaviour to the second.

**The fail-open rule is inherited, not invented.** `working_minutes_between`
already falls back to plain calendar minutes when an inbox has no working hours
configured. An inbox fetch that fails returns `{}`, which hits that same
fallback. A tenant that has never configured working hours therefore sees no
change at all when the flag is switched on — which is the correct behaviour and
also the migration story.

**Thresholds change unit, not meaning.** `sla_response_hours`,
`sla_resolution_hours`, `escalation_tier2_hours`, the per-channel
`ack_minutes_by_channel` map and the per-conversation `sla_minutes` label all
keep their current names and values. Under the flag they are interpreted as
*working* hours/minutes. This is deliberate: PRO-NET's targets were always
working-hours targets, so the existing configured numbers become correct rather
than needing re-entry. The change is documented in the tenant env comments and
in the SLA Policies admin help text, because "2 hours" silently changing meaning
is exactly the kind of thing that burns an operator.

**Per-inbox override.** `SlaPolicyRepository` already resolves per-inbox
overrides for `response_hours`, `resolution_hours`, `tier2_hours` and
`engine_enabled`. It gains `working_hours_enabled: bool | None` on the same
pattern — `None` inherits the global setting, so byte-identical behaviour is
preserved for every existing stored policy.

### 3.2 The persisted in-hours flag

Stamped **at intake**, on the conversation, as a Chatwoot custom attribute:

| Attribute | Type | Written by | Written when |
|---|---|---|---|
| `received_in_business_hours` | bool | agent service | first inbound message on the conversation |
| `received_at_local` | ISO-8601 string, inbox timezone | agent service | same write |

Intake is the right moment and the only correct one: business hours can be
edited by an operator at any time, so a flag computed later would answer "was
this in hours according to today's config", not "was it in hours when it
arrived". Stamping once, at arrival, makes the value a fact rather than a
derivation.

The write is **idempotent and never overwrites** — the same guard
`maybe_stamp_dealer_escalation` already uses in `agent/app/services/sync.py`.
This matters because `conversation_updated` fires on every label and attribute
write.

`received_at_local` costs one extra field and removes an entire class of
question later ("was that 3 p.m. Malaysia or 3 p.m. UTC"), which the reporting
layer currently answers wrongly by bucketing UTC calendar days (P4 fixes the
bucketing; this field makes the fix checkable).

The flag then flows through the existing sync into BigQuery as a column on
`CONVERSATIONS_SCHEMA`, which is what unlocks §4.52.

### 3.3 The acknowledgement event

A new audit transition, `ACKNOWLEDGED`, recorded in the existing
Firestore-backed `AuditEntry` store alongside `FIRST_RESPONSE`.

An acknowledgement is recorded when **either**:

- an agent posts a message on the conversation (today's inferred signal — kept,
  so nothing regresses), **or**
- an agent explicitly acknowledges via a new lightweight action, which is what
  makes the distinction real.

The explicit path matters for the escalation ladder: a PIC who receives an
escalation email and replies to it is acknowledging the *escalation*, not
replying to the customer, and `escalation_replies.py` already links those
replies back onto the case. That linker gains one line: record `ACKNOWLEDGED`.

`_has_first_agent_response` keeps its name and its inference. A new
`_has_acknowledgement` reads the `ACKNOWLEDGED` state. Where the SOP says
"acknowledge", enforcement reads the new signal; where it says "respond" or
"update", it reads the existing one. **The two are no longer the same question.**

### 3.4 Next-business-hour scheduling

B-WA-10 and B-EM-04 both say an unresolved out-of-hours case is "assigned to an
agent, to be attended the next business hour". Today the case sits open with no
scheduled moment.

Design: the case is stamped with a computed `attend_after` timestamp — the next
instant inside the inbox's working hours — and the existing SLA scheduler's
sweep skips enforcement on a conversation whose `attend_after` is in the future,
rather than accruing breach time against an agent who is not at work. The
timestamp is computed by a new pure function beside the clock helper:

```python
def next_working_instant(after: datetime, inbox: dict) -> datetime:
    """The first instant at or after `after` that falls inside working hours.

    Returns `after` unchanged when it is already inside working hours, and
    when the inbox has no working-hours config (matching the fail-open
    fallback in working_minutes_between).
    """
```

This is the same calendar walk `working_minutes_between` already performs, so it
lives in the same module and shares its tests' fixtures. It is a *scheduling*
aid, not a second calendar implementation.

### 3.5 The two views that were always affordable

`first_response_working_minutes` and `resolution_working_minutes` are computed
and stored on every row today and **read by no view**. Two views close three
requirements:

- **`v_first_response_by_hours_split`** — first response, split by
  `received_in_business_hours`, by channel. Closes 4.53 and C1-12 #14.
- **`v_volume_after_hours`** — case volume by `received_in_business_hours` × day
  × channel. Closes 4.52 (the GAP) and 3.1.3's reporting half.

Both follow the existing `bigquery_schema.py::view_ddls` pattern. Neither needs
new data collection — only P1's flag and columns that already exist.

### 3.6 After-hours auto-reply text (B-WA-04, 3.1.4)

The text-channel after-hours reply is Chatwoot's native per-inbox out-of-office
message, deliberately not owned by this repo. The gap is not code — it is that
Appendix B's exact wording has never been provisioned or verified.

P1 delivers a **provisioning script and a verification test**, not a new feature:
`deploy/scripts/provision-after-hours-replies.py` writes Appendix B's bilingual
text to each configured inbox via the Chatwoot API, and a test asserts the
deployed text matches the appendix verbatim. This is the honest closure of a
requirement whose implementation belongs to upstream Chatwoot.

The voice-channel half of 3.1.4 — the AI bridge has no after-hours message at
all — is **P11**, not here.

## 4. What could go wrong

| Risk | Mitigation |
|---|---|
| Switching the flag on silently changes what "2 hours" means, and an operator does not notice | The setting is documented in `example.env`, surfaced in the SLA Policies admin UI with explicit help text, and the plan's final task is a migration note for the runbook |
| A tenant with no working-hours config sees unexpected behaviour | Impossible by construction: `working_minutes_between` falls back to calendar minutes, so that tenant's behaviour is unchanged whether the flag is on or off |
| The inbox cache serves stale working hours after an operator edit | Cache is per-scan, not per-process; the next sweep picks up the edit |
| Breach alerts stop firing entirely because a bug makes elapsed working minutes always ~0 | The plan's task 3 asserts a golden case: a Friday-18:00 arrival breaches a 2-working-hour target at Monday 10:00, not Friday 20:00, and *does* breach |
| The `ACKNOWLEDGED` state double-counts against `FIRST_RESPONSE` | They are separate audit states; the plan asserts a case can be acknowledged without a first response and vice versa |

## 5. Testing

Following the repo's convention — sqlite, `respx`, injected clocks, no real
Chatwoot or Gemini.

- **Clock arithmetic** (`test_sla_clock.py`): flag off reproduces wall-clock to
  the second; a weekend spans zero working minutes; an inbox with no config
  falls back; timezone honoured (MYT, UTC+8).
- **Enforcement** (`test_sla_working_hours.py`): the Friday-18:00 golden case in
  both directions; per-inbox override; policy-store `None` inherits.
- **Intake stamp** (`test_sync_business_hours_stamp.py`): stamped once, never
  overwritten, survives repeated `conversation_updated`, fails open on API error.
- **Acknowledgement** (`test_sla_acknowledgement.py`): explicit ack without a
  reply; reply without an explicit ack; PIC email reply records an ack.
- **Views** (`test_bigquery_schema.py` additions): both new views appear in
  `view_ddls` and reference only columns present in `CONVERSATIONS_SCHEMA`.

## 6. Feature flags

| Setting | Default | Effect |
|---|---|---|
| `SLA_WORKING_HOURS_ENABLED` | `false` | Off = today's wall-clock enforcement, byte-identical |
| `BUSINESS_HOURS_STAMP_ENABLED` | `false` | Off = no intake stamp written |
| `SLA_ACK_EVENT_ENABLED` | `false` | Off = acknowledgement inferred from first reply, as today |
| `NEXT_BUSINESS_HOUR_SCHEDULING_ENABLED` | `false` | Off = no `attend_after` stamp, no enforcement skip |

Four flags rather than one, because they are independently valuable and
independently risky. The stamp is safe to switch on immediately and starts
accumulating data the views need; the enforcement change should follow a
deliberate operator conversation.

## 7. Requirements closed

3.1.3, 3.1.4 (text half), 3.2.4, 4.34, 4.53, 4.54, B-WA-03, B-WA-04, B-WA-10,
B-WA-14, B-EM-04, B-EM-05, B-SM-06 (mechanism; the channel itself stays blocked
on Meta verification), C1-12 #6, C1-12 #14 — plus **4.52**, which is GAP today
only because 3.1.3 discards the flag.
