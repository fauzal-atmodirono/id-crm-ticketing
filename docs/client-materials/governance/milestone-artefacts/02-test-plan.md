# 02 — Test plan

**Requirement:** §6.1, §6.3.2 · **Status:** ready to sign

## What it is

The test plan is `../qa-plan.md` in full. This artefact exists to give it the
name §6.3.2 asks for and to point at it, not to restate it.

## Traceability

| Evidence | Path |
|---|---|
| **The plan itself** | `docs/client-materials/governance/qa-plan.md` |
| Test levels, conventions, severity, entry/exit criteria | ibid., §2, §3, §6, §7 |
| Backend suite | `backend/apps/backend/src/chatbot/**/test_*.py` |
| Agent-service suite | `agent/tests/` |
| Both-flag-states gate | `deploy/scripts/check-suites-both-flag-states.sh` |
| Reachability tests | `backend/apps/backend/src/chatbot/test_p*_wiring.py` |
| SIT script | `docs/client-materials/sit/2026-08-08-sit-script.md` |

## What a reviewer should read first

**§5, "What none of this proves."** The plan is signable because it is accurate,
and the largest thing it accurately says is that the automated evidence is strong
on logic and structure and **silent on integration**: no real model has ever
answered, none of the 33 BigQuery views has ever been executed, no real call or
WhatsApp message has been sent, and no fork patch has been applied to a real
checkout.

Two conventions in it are worth a reviewer's attention because they are unusual
and load-bearing: **coverage is expressed behaviourally rather than as a line
percentage** (on a codebase where nine features had green tests and could not run,
a percentage would have been met throughout and measured nothing), and **a value
that was not measured must render as unavailable rather than `0`**, enforced by
tests rather than by reviewers remembering.

**Signing this artefact is not signing that the system is tested.** It is signing
that the plan describes the practice truthfully, including its limits.
