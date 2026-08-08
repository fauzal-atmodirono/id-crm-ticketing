# P8 — AI & Agent Measurement

**Date:** 2026-08-08
**Programme:** `docs/superpowers/specs/2026-08-08-rfp-partials-program-design.md`
**Plan:** `docs/superpowers/plans/2026-08-08-rfp-p8-ai-agent-measurement.md`
**Closes:** 6 PARTIAL requirements + §4.56's AI performance suite (GAP, 6 of 8 reports)
**Effort:** 3 weeks · **Wave:** 3 · **Blocked by:** P4 (query layer), P7 (sentiment)

---

## 1. The problem, precisely

**Prove the AI works.** Every requirement here is about measurement, and each is
PARTIAL because a measurement stops one step short of being reportable.

**Token accounting is half-built and on the wrong half.** `ai_actions` records
`model` and `prompt_tokens` (`agent/app/ai/gemini.py`); **output tokens are never
captured**. Output tokens are typically the expensive half. And the `backend/`
service — which makes the *majority* of Gemini calls (`/assist/*`, `/chat/turn`,
embeddings, the phone Live API) — **records no token counts at all**. There is no
price table, no cost computation and no report. §4.28.2 asks for an AI
cost/pricing model; the metering to back a commercial answer does not exist.

**NPS is built and never invoked.** `v_nps_by_agent` exists and is correctly
restricted to Phone and WhatsApp. `features/chat/nps.py::record_nps` is
**decoupled from the survey flow** (`service.py:760`) and is never called from
any phone code path. So the view is correct and its source column will be sparse
or empty (§4.71).

**CSAT is per channel, not per agent.** `v_csat` aggregates by channel. §4.72 and
B-WA-16 both ask for a **customer rating of agent performance**. Per-agent rating
exists only through the NPS view — whose data is not being collected. So the two
requirements each depend on the other's gap.

**QA is human and not call-specific.** `v_quality` aggregates manual human
`qa_labels`. C1-12 #3 asks for QA performance on **calls**, against an 85%
target. There is no call-specific QA scoring.

**§4.56's AI performance suite: 6 of 8 reports do not exist.** AI Case
Resolution, AI vs Human handling, AI Accuracy & Improvement, AI Deflection Rate,
AI Root Cause Analysis and KB Improvement recommendations. The raw material is
captured — `NO_MATCHES` becomes `Subcategory='Unresolved Query'`, FAQ 👍/👎 lands
in `v_faq_quality`, `ai_actions.decision` and the handoff `reason` are recorded —
and **nothing analyses it**.

**§8.1.15's monthly review has no AI-accuracy or KB-health metric.** Reporting
covers volume, SLA and CSAT. `v_quality` is human QA labelling; `v_faq_quality`
is a 👍/👎 helpful-rate. Neither is an accuracy measure.

## 2. What this package delivers

1. Complete token metering across both services, plus a price table and a cost
   report.
2. NPS wired into the survey flow it was built beside.
3. CSAT aggregated per agent.
4. Call-specific QA scoring.
5. Four of the six missing AI performance reports.
6. AI accuracy and KB health as actual metrics.

## 3. Design

### 3.1 Token metering

A shared shape, recorded by both services:

```python
@dataclass(frozen=True)
class TokenUsage:
    service: str        # "agent" | "backend"
    surface: str        # "orchestrator" | "assist.suggest" | "chat.turn" | "embed" | "phone.live"
    model: str
    prompt_tokens: int | None
    output_tokens: int | None
    cached_tokens: int | None
```

`agent/app/ai/gemini.py` already extracts `usage_metadata`; it reads one field
and must read three. `ai_actions` gains `output_tokens` and `cached_tokens` as
nullable columns.

The backend needs the mechanism from scratch, and the design constraint is that
**it must not be a per-call-site change**. Gemini calls are made from at least
five places there. A wrapper at the client boundary records usage once, and new
call sites are metered by construction rather than by remembering.

`cached_tokens` is included because prompt caching materially changes cost and a
cost model that ignores it will overstate spend, then be wrong in the other
direction when someone "fixes" it by removing the multiplier.

**Price table:** operator-editable, per model, per token class (input / output /
cached), with an effective-from date. Prices change; a hardcoded rate produces a
report that is quietly wrong from the day it changes, and historical cost must be
computed at the rate that applied then.

**Cost report:** `v_ai_cost` by day × service × surface × model, with a
`/metrics/ai-cost` endpoint. This is what makes §4.28.2 answerable commercially —
"what does the AI cost per conversation" becomes a query rather than an estimate.

### 3.2 NPS wiring (§4.71)

`record_nps` exists and is never called. Wire it into the lifecycle survey flow
alongside the CSAT capture, and into the phone post-call path.

The subtlety worth designing around: **CSAT and NPS are different questions**
("how satisfied were you with this interaction" vs "how likely are you to
recommend"), and asking both at the end of every conversation will halve the
response rate for both. So NPS is sampled — `NPS_SAMPLE_RATE`, default 0.0 (off),
and when enabled it *replaces* the CSAT question for the sampled fraction rather
than being appended to it.

An agent-attributed NPS also needs the agent, and `v_nps_by_agent` groups by
`agent_id`. Attribution is to the agent assigned at the moment the survey is
answered, and this is recorded rather than inferred later — a case reassigned
after the survey would otherwise silently re-attribute the score.

### 3.3 CSAT per agent (§4.72, B-WA-16)

`v_csat` stays as it is — channel-level CSAT is used on existing dashboards and
must not change meaning. A sibling `v_csat_by_agent` groups the same `csat_<n>`
labels by `agent_id`, with the same attribution rule as §3.2.

**Sample-size disclosure is part of the view, not the presentation layer.** An
agent with three ratings and an average of 5.0 is not the best agent; the view
returns the count alongside the score, and any ranking suppresses agents below a
configurable minimum. Publishing a league table built on n=3 is how a performance
metric becomes an industrial-relations problem.

### 3.4 Call QA (C1-12 #3)

Extend the existing manual `qa_labels` mechanism with a call-specific rubric
rather than inventing a second QA system. A QA record gains a `channel` and, for
calls, a scored rubric (greeting, identification, resolution, closing,
compliance), producing a percentage against the 85% target from P5's store.

Manual, deliberately. Automated call QA needs reliable call transcripts, and the
transcript path has never been run against a real Twilio call
(`docs/testing/phone-channel-package-c-verification.md`). Automating scoring on
an unverified transcript would produce confident numbers about nothing.

### 3.5 The AI performance suite (§4.56)

Four of the six missing reports are buildable now from data already captured:

| Report | Source | Definition |
|---|---|---|
| ① AI Case Resolution | `resolved_by` in `CONVERSATIONS_SCHEMA` | Cases resolved with no agent message |
| ② AI vs Human handling | `resolved_by` + `ai_actions` | Split of volume and outcome |
| ④ AI Escalation | `ai_actions.decision`, handoff `reason` | Handoffs by reason |
| ⑥ AI Deflection Rate | ① over total | Share resolved without a human |

The definitions matter more than the SQL, and one in particular:

**Deflection is "resolved with no agent message", not "the bot replied".** A
conversation where the bot answered and the customer then asked for a human is
not deflected. The definition is stated on the report itself, because a
deflection rate is the number a client will quote back, and two reasonable
definitions differ by a factor of two.

③ AI Response Satisfaction is PARTIAL today because `v_csat` is per channel; it
becomes an AI-vs-human CSAT split using §3.3's attribution.

**⑤ AI Accuracy & Improvement, ⑦ Root Cause Analysis and ⑧ KB Improvement
recommendations are not built here.** ⑤ is met by P7's calibration baseline
(§3.8 of that spec) as a *measured* accuracy rather than a report. ⑦ and ⑧ are
AI-analysis features — a model summarising failure patterns — and are a separate
2–3 week package. Claiming them here would be the same mistake the vendor
response already made once.

### 3.6 KB health (§8.1.15)

Three metrics, all from data already collected:

- **Coverage:** share of enquiries that produced a FAQ match above the score
  floor. `NO_MATCHES` → `Subcategory='Unresolved Query'` is already recorded.
- **Helpfulness:** the existing 👍/👎 rate from `v_faq_quality`.
- **Staleness:** age since last edit per FAQ entry, weighted by how often it was
  served — an entry served 400 times and last edited a year ago is the one to
  review.

Together these give §8.1.15 an actual KB-health section instead of prose.

## 4. What could go wrong

| Risk | Mitigation |
|---|---|
| A new backend call site is added and silently unmetered | Metering wraps the client, not the call sites; a test asserts no direct client construction outside the wrapper |
| Cost report is wrong because prices changed | Effective-from dating; historical cost computed at the rate that applied |
| Cached tokens double-counted or ignored | Recorded as their own class and priced separately |
| Adding NPS collapses CSAT response rates | NPS is sampled and replaces rather than appends; default off |
| A per-agent league table on n=3 | Count returned with every score; minimum sample size for rankings |
| Deflection rate quoted under the wrong definition | Definition printed on the report |
| Automated call QA scores unverified transcripts | Call QA stays manual until the phone path is verified against a real call |

## 5. Testing

- **Metering** (`test_token_usage.py`): all three token classes captured; both
  services record; a call site outside the wrapper fails a test; missing usage
  metadata degrades to `None` not `0`.
- **Cost** (`test_ai_cost.py`): price effective-dating; a historical row costs at
  its own rate; cached priced separately.
- **NPS** (`test_nps_wiring.py`): fires at the sample rate; replaces CSAT when
  sampled; attributed to the agent at survey time; not re-attributed on later
  reassignment.
- **CSAT per agent** (`test_csat_by_agent.py`): `v_csat` unchanged; counts
  returned; below-minimum agents excluded from rankings.
- **AI reports** (`test_ai_performance_views.py`): a bot-answered case that later
  reached an agent is **not** counted as deflected; each report's definition
  string present in the response.

## 6. Feature flags

| Setting | Default | Effect |
|---|---|---|
| `TOKEN_METERING_ENABLED` | `false` | Off = today's prompt-tokens-only recording |
| `AI_COST_REPORTING_ENABLED` | `false` | Off = no cost views |
| `NPS_SAMPLE_RATE` | `0.0` | 0 = no NPS asked, exactly as today |
| `CSAT_BY_AGENT_ENABLED` | `false` | Off = channel-level only |
| `CSAT_RANKING_MIN_SAMPLES` | `10` | Below this, an agent is excluded from rankings |
| `CALL_QA_ENABLED` | `false` | Off = today's channel-agnostic QA |

## 7. Requirements closed

4.28.2, 4.71, 4.72, B-WA-16, C1-12 #3, 8.1.15 — plus four of the eight §4.56
reports (①②④⑥) and the AI-vs-human split for ③.

**Explicitly not closed:** §4.56 ⑦ (AI Root Cause Analysis) and ⑧ (KB Improvement
recommendations) are separate AI-analysis features, 2–3 weeks, not claimed here.
⑤ is answered by P7's calibration baseline rather than by a report.
