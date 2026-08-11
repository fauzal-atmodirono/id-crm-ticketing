# 03 — Test report

**Requirement:** §2.2.6, §6.3.2 · **Status: NOT READY TO SIGN**

> The automated half exists and is current. **The integration half does not: the
> SIT has not been executed.** This artefact must not be signed as a test report
> for the delivery.

## Traceability

| Evidence | Path | State |
|---|---|---|
| Backend suite result | run `GOOGLE_API_KEY=test-key uv run pytest -q` in `backend/apps/backend` | **Green** |
| Agent-service suite result | run `./.venv/bin/python -m pytest -q` in `agent` | **438 passed** |
| Configuration-document tests | `scripts/test_generate_config_doc.py` | **8 passed** |
| Both-flag-states gate | `deploy/scripts/check-suites-both-flag-states.sh` | Green at the last recorded run |
| **SIT report** | `docs/client-materials/sit/2026-08-08-sit-report.md` | **0 of 155 cases executed** |
| What the suites cannot prove | `../qa-plan.md` §5 | — |
| Live E2E email cases | `docs/testing/2026-08-06-escalation-email-e2e-scenario.md` | Execution log largely empty |
| AI calibration baseline | `docs/testing/2026-08-08-ai-calibration-baseline.md` | `TBD — unmeasured` |

## Why it cannot be signed

A test report for a delivery is a statement that the delivery was tested. The
automated suites are substantial and green, and they were produced in an
environment with **no live credentials of any kind** — so they are evidence about
logic, not about integration.

Specifically unverified: every AI quality figure (the stub harness scores 97–100%
because one author wrote both the labels and the rules being scored), all 33
BigQuery views, the entire voice path, every fork patch's rendered UI, and the
restore procedure. See `../risk-register.md` R7, R8, R10, R12, R13.

**What would make this signable:** execute the SIT against the agreed script, in a
non-production environment with real credentials, and record the results —
including the failures. `docs/client-materials/sit/2026-08-08-sit-report.md` §3
lists the eight steps in order.

**Suite counts must be re-taken on a quiet working tree before being quoted.**
The last measurement was taken with another workstream's uncommitted changes
present, which inflated it by 20 tests. A count is also a measure of volume, not
of reachability.
