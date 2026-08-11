# 10 — Support handover

**Requirement:** §6.3.2 · **Status:** partly ready — the runbooks exist, the support model does not

> The operational runbooks are delivered. **The support model — tiers, hours,
> staffing, escalation path, response targets — has not been defined by anyone**,
> and it is not an engineering artefact.

## Traceability

| Evidence | Path | State |
|---|---|---|
| Operational runbooks | `docs/runbooks/` | **Delivered** |
| Monitoring configuration | `deploy/monitoring/` | **Delivered** |
| Deep health checks | `backend/apps/backend/src/chatbot/features/health_enrichment.py` | Delivered |
| Backup / restore / archive | `deploy/scripts/{backup,restore,archive-old-data}.sh` | Delivered |
| Fork rebase tooling | `deploy/chatwoot-fork/rebase.sh` + `PATCH-INVENTORY.md` | Delivered |
| Audit-log purge | `backend/apps/backend/src/chatbot/features/authz/audit_purge.py` | Delivered |
| Defect severity definitions | `../qa-plan.md` §6 | Delivered |
| Known-issue register | `docs/analysis/2026-08-09-blocked-work-register.md` | **Current and substantial** |
| Risk register | `../risk-register.md` | Delivered |
| **Support tiers and hours** | — | **NOT DEFINED** |
| **On-call rotation** | — | **NOT STAFFED** |
| **Escalation path to the vendor** | — | **NOT DEFINED** |
| **Response and resolution targets** | — | Committed in the RFP, **not supportable** (R1) |

## What a receiving support team most needs

**Read `docs/analysis/2026-08-09-blocked-work-register.md` before taking a single
ticket.** It is the most operationally valuable document in the repository: it
lists what is built-but-unreachable, what is deliberately blank, and what is owed
— which means it pre-answers a large share of the "is this a bug?" questions.

Five behaviours will be reported as bugs and are not:

1. **Five of the fourteen control items are blank**, and must stay blank. A zero
   would assert a 0% call-abandon rate on a platform with no call queue. "Tidying"
   them will look like an improvement (R5).
2. **`v_dealer_escalation` keys on the escalation date, not the creation date**, so
   its monthly total deliberately does not sum to that month's case count.
3. **Chatwoot's native `busy` and `offline` never count as an absence.** Alerting on
   them would page an administrator after every agent logs off, every night.
4. **`/metrics/ai-cost`, `/metrics/anomalies/hourly` and `/metrics/freshness` can
   answer "unavailable"** rather than a number. That is a contract: with no
   warehouse there is no evidence of zero spend.
5. **`REPORTING_TIMEZONE` re-buckets all history** when changed. Run
   `scripts/compare-reporting-timezone.py` first and keep the output (R14).

## What must be defined before support can begin

- [ ] Support tiers, hours of cover, and who staffs each
- [ ] Escalation path from PROTON's users to the vendor, with named contacts
- [ ] Response and resolution targets that are **actually supportable** — and note the RFP's 99.9% and 2-hour P1 are **not**, on one VM with no failover (R1)
- [ ] On-call rotation, staffed and costed
- [ ] Alert destinations pointed at that rotation
- [ ] Change process for a fork rebase on an upstream security release (R2)
- [ ] A decision on who owns `presence_events` retention (R11)

**The gap between the committed response targets and what a single VM can support
is the item to settle first**, because it will otherwise be discovered during the
first incident, by the person on the phone.
