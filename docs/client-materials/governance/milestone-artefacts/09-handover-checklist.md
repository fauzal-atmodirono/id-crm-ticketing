# 09 — Handover checklist

**Requirement:** §6.3.2 · **Status:** ready to sign as a checklist; items within it are open

## What it is

The single list of everything that must transfer for another team to run this
platform without its authors. New — there was no analogue.

## Traceability

Each item names where its evidence lives. An unticked box with a path is a known
gap; an unticked box with no path is work nobody has started.

### Documentation
- [x] Architecture and trust boundaries — `docs/client-materials/handover/architecture.md`
- [ ] …**reviewed by someone outside the project** (artefact 07)
- [x] API schema, verified against a booted app — `handover/api-schema.md`
- [x] Configuration reference, generated — `handover/configuration.md`
- [x] QA plan — `governance/qa-plan.md`
- [x] Risk register — `governance/risk-register.md`
- [x] Engineer onboarding — `CLAUDE.md`
- [x] Deploy runbook — `README.md`
- [x] Operational runbooks — `docs/runbooks/`
- [x] **Blocked-work register** — `docs/analysis/2026-08-09-blocked-work-register.md`
- [ ] Role-differentiated training (artefact 05)

### Access and credentials
- [ ] GCP project ownership and IAM transferred
- [ ] Service-account keys rotated and re-issued to the receiving team
- [ ] Twilio account ownership transferred
- [ ] Artifact Registry access
- [ ] VM SSH access and the break-glass path
- [ ] Chatwoot super-admin credentials per tenant
- [ ] Domain and DNS control
- [ ] **Google account app-passwords audited** — an unidentified external sender is still mailing from the account (`docs/analysis/...blocked-work-register.md` §2.1); only the account owner can close it

### Operational readiness
- [x] Backup script — `deploy/scripts/backup.sh`
- [x] Restore script — `deploy/scripts/restore.sh`
- [ ] **Restore rehearsed, and timed** (`../risk-register.md` R12) — the sharpest open item on this list
- [x] Monitoring — `deploy/monitoring/`
- [ ] Alert destinations pointed at the receiving team's on-call
- [ ] On-call rotation staffed (required by the 2-hour P1 commitment; not costed)
- [x] Fork rebase tooling — `deploy/chatwoot-fork/rebase.sh`, with a derived `PATCH-INVENTORY.md`
- [ ] **A rebase performed once, by the receiving team** — 59 patches, and the count grows with every feature (R2)
- [ ] Data retention policy agreed, with a number for `presence_events` (R11)

### Verification owed before handover completes
- [ ] Nine fork patches (0052–0060) built and seen rendering (R7)
- [ ] SIT executed against the agreed script (artefact 03)
- [ ] `ensure_views()` run; the two `ALTER TABLE` migrations applied (R13)
- [ ] AI calibration baselines measured with real credentials (R8)
- [ ] One real Twilio call, end to end (R10)
- [ ] Live E2E email cases executed against a readable mailbox

### Commercial and governance
- [ ] **Vendor response reconciled** — it marks ≥17 unbuilt requirements "Fully OOTB" (**R6, the most urgent item in the analysis**)
- [ ] SLA renegotiated or HA funded — 99.9% is not supportable on one VM (R1)
- [ ] Fork rebase priced as recurring effort (R2)
- [ ] Q4 (DMS spec), Q5 (HQ escalation), Q7 (PII masking scope) answered
- [ ] A date for Meta Business verification (R4)
- [ ] Governance forum named (R-GOV-1)

## The honest summary

**Documentation is in good shape. Operational verification is not.** Every
unticked box in "Verification owed" is something that has never been done once,
and the four commercial rows cannot be closed by engineering at all.

**If only one item is actioned before handover, it is the restore rehearsal** —
one VM, no failover, and a backup nobody has ever restored. **If only one more, it
is the vendor-response reconciliation**, because that one gets worse with time
rather than merely staying open.
