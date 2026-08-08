# P13 — Ops Hardening: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the single-VM architecture that exists honestly operable — backups that survive losing the VM, a restore that has actually been run, alerts that reach a human, and a retention policy that runs.

**Architecture:** Cloud Monitoring rather than a self-hosted stack, because a monitor on the VM it monitors goes down with it. Backups sync offsite to a different region. The restore script can restore *into a different tenant*, which is the property that makes a drill possible — and an unpractised restore script does not work.

**Tech Stack:** bash, gcloud/gsutil, Cloud Monitoring, BigQuery/GCS lifecycle policies, Docker Compose, pytest where applicable.

**Spec:** `docs/superpowers/specs/2026-08-08-rfp-p13-ops-hardening-design.md`

## ⚠️ Scope boundary

**This package does not deliver 99.9% uptime (§8.1.5) or P1 `<2h` 24/7 (§9).**
Those need multi-zone HA (R17, 4–6 weeks plus a materially higher run cost) and a
24/7 on-call rota. No work in this plan changes them, and no task here should be
reported as progress toward them. That is a commercial conversation to have
**before the SLA is signed.**

## Global Constraints

- **`--dry-run` is the default on every destructive script.** A destructive
  default will one day meet a wrong argument.
- **Verify before destroy.** The restore checks the archive is present, complete
  and loadable before dropping anything.
- **A restore must be able to target a different tenant**, or it can never be
  drilled.
- **Never run a drill against production.** Scratch tenant, scratch VM.
- **Monitoring must not live on the VM it monitors.**
- **Every alert names an owner.** An alert with no owner is a log line.
- **Do not silently resolve the recordings-vs-7-years conflict.** Raise it.
- Work on branch `dev-yuda`. Never merge to `main`.

---

## File Structure

| File | Responsibility |
|---|---|
| `deploy/scripts/backup.sh` | **Modify.** GCS sync after the local run |
| `deploy/scripts/restore.sh` | **New.** Verify-then-restore, cross-tenant, dry-run default |
| `deploy/scripts/archive-old-data.sh` | **New.** Postgres → GCS beyond the hot window |
| `deploy/monitoring/` | **New.** Alert policies, dashboards, uptime checks |
| `deploy/chatwoot-fork/rebase.sh` | **New.** Patch-series rebase with a full failure report |
| `deploy/chatwoot-fork/PATCH-INVENTORY.md` | **New.** What each of the 49 patches does |
| `docs/runbooks/disaster-recovery.md` | **New.** Measured RTO/RPO, drill record |
| `docs/runbooks/monitoring-alerts.md` | **New.** Alert → owner → action |

---

### Task 1: Offsite backup copy

**Files:**
- Modify: `deploy/scripts/backup.sh`
- Create: a GCS bucket definition + lifecycle policy

**Requirements:**
- Sync `/backups/YYYY-MM-DD/` to a bucket in a **different region** after each run.
- Object versioning on; lifecycle Standard → Nearline (30 d) → Coldline (365 d).
- The sync failing is **loud** — a non-zero exit and an alert. A silent offsite
  failure recreates exactly the situation this task exists to fix, while looking
  solved.

**Verification:**

```
- [ ] A nightly run produces an object in the remote-region bucket
- [ ] The object's checksum matches the local archive
- [ ] Versioning is enabled
- [ ] Lifecycle transitions are configured
- [ ] A deliberately broken sync exits non-zero and alerts
- [ ] The local 7-day pruning still works and is unchanged
```

**Note:** keep the existing script's discipline — `set -euo pipefail`, absolute
paths, no interactive prompts. It is cron-safe today and must stay so.

---

### Task 2: The restore script

**Files:**
- Create: `deploy/scripts/restore.sh`

**Interface:**
`restore.sh --tenant <src> --date <YYYY-MM-DD> [--into <dst-tenant>] [--apply]`

**Requirements:**
- **`--dry-run` is implied unless `--apply` is passed**, and dry-run prints every
  action it would take.
- `--into` restores into a *different* tenant — the property that makes a drill
  possible without touching production.
- Verify before destroy: archive present, checksum valid, `pg_restore --list`
  parses, storage tarball intact. Only then drop and load.
- Restores from GCS when the local copy is gone — the actual disaster case.
- Refuses to restore into a tenant with an active container unless `--force`,
  and says so.

**Verification:**

```
- [ ] Dry-run prints the plan and changes nothing
- [ ] --apply restores into a scratch tenant
- [ ] A corrupt archive is rejected before anything is dropped
- [ ] A missing local copy falls back to GCS
- [ ] Restoring into a running tenant is refused without --force
- [ ] Conversation counts in the restored tenant match the source
- [ ] A sampled conversation's messages match
```

---

### Task 3: The drill, and the measured RTO

**Files:**
- Create: `docs/runbooks/disaster-recovery.md`

**This is the deliverable that makes the previous two tasks real.** Execute:

1. Restore the previous night's `proton` backup into a scratch tenant on a
   scratch VM, **from GCS, not from the local copy** — the local copy is exactly
   what a real disaster removes.
2. Time it end to end.
3. Verify: conversation count, contact count, a sampled conversation's messages,
   Chatwoot storage attachments present.
4. Record the measured RTO in the runbook.
5. State the RPO from the backup schedule (nightly → up to 24 h of loss) and
   flag whether the client has accepted it.

**The measured number is the point.** An RTO in a proposal that nobody has
measured is a guess with a number on it, and §9 commits to P1 resolution in under
two hours — a figure that cannot be honoured if a full restore takes three.

**If the drill shows the restore exceeds the committed SLA, that is a finding to
report, not a number to round down.**

```
- [ ] Restore executed from GCS onto a scratch VM
- [ ] RTO measured and recorded
- [ ] Data integrity verified on four dimensions
- [ ] RPO stated and its acceptability flagged
- [ ] The runbook names who runs this and how often
```

---

### Task 4: Monitoring and alerting

**Files:**
- Create: `deploy/monitoring/alert-policies.yaml`, `deploy/monitoring/dashboards/`
- Create: `docs/runbooks/monitoring-alerts.md`
- Modify: `agent/app/routers/health.py` and the backend health surface — expose the business-layer signals

**The four layers, in priority order** (business first — the host layer is the
easy part and the least likely to be the first symptom):

| Layer | Alert |
|---|---|
| **Business** | Metrics sync stale > 2× interval; SLA scanner not run in 30 min; escalation send-failure count > 0 (P2's counter); webhook error rate spike |
| **Application** | Health endpoint failing > 2 min; agent-bot decision failures |
| **Container** | Any service down > 2 min; restart loop |
| **Host** | **Disk > 80%**; sustained memory pressure; CPU saturation |

**Disk is the highest-value host alert.** Backups, Postgres, Docker images and
Chatwoot storage share one disk and `backup.sh` writes to it nightly. Disk
exhaustion is the most likely single-VM failure and it takes everything down
together.

**A silent SLA scanner is the highest-value business alert.** Nothing appears
broken — alerts simply stop firing, and the first symptom is a missed escalation
nobody can explain.

**Verification — every alert fired deliberately:**

```
- [ ] Fill a scratch VM's disk past 80% → alert received
- [ ] Stop a container → alert received within 2 min
- [ ] Stall the metrics sync → staleness alert received
- [ ] Block SMTP → escalation-failure alert received
- [ ] Stop the SLA scanner → not-run alert received within 30 min
- [ ] Every alert in the runbook names an owner and a first action
```

---

### Task 5: Retention and archival

**Files:**
- Create: `deploy/scripts/archive-old-data.sh`
- Modify: BigQuery dataset/table configuration
- Create: `docs/runbooks/data-retention.md`

**Requirements:**
- BigQuery table and partition expiration set to 7 years.
- Postgres rows beyond `ARCHIVE_HOT_WINDOW_DAYS` (default 730) archived to GCS
  and purged, with a **self-describing** format: a manifest plus
  newline-delimited JSON, readable **without the application**.
- GCS lifecycle for cost.
- The archive job is idempotent and resumable.

**Raise the conflict in the runbook, in these terms:**

> §4.84 requires all operations data to be retained ≥7 years.
> `PHONE_RECORDING_RETENTION_DAYS` is 90. These are contradictory for call audio.
> We have assumed §4.84 refers to transactional and case data, not to seven years
> of dual-channel call recordings — which would be a materially different storage
> cost and a different data-protection position. **This assumption needs the
> client's confirmation before the retention policy is signed off.**

**Verification:**

```
- [ ] BigQuery expirations configured and visible
- [ ] The archive job round-trips a record
- [ ] An archived record is readable with only jq — no application involved
- [ ] Re-running the job archives nothing twice
- [ ] The recordings-vs-7-years conflict is documented as an open question
```

---

### Task 6: Fork-rebase tooling

**Files:**
- Create: `deploy/chatwoot-fork/rebase.sh`
- Create: `deploy/chatwoot-fork/PATCH-INVENTORY.md`
- Create: a CI job applying the series against the pinned ref

**Requirements:**
- `rebase.sh <upstream-ref>` applies all 49 patches in order and **reports every
  failure, not just the first.** Knowing 3 of 49 conflict is a half-day;
  discovering them one at a time is a week.
- The inventory lists each patch: number, purpose, files touched, conflict risk.
- CI applies the series on every change so a broken series fails at commit.

**Verification:**

```
- [ ] The script applies the full series against the current pinned ref
- [ ] A deliberately broken patch is reported alongside the others, not instead of them
- [ ] The inventory covers all 49 patches
- [ ] CI fails on a broken series
```

**Note for whoever prices §2.4.4:** this reduces the fork liability; it does not
remove it. 49 patches against a fast-moving upstream is a standing commitment.

---

### Task 7: Non-production environment

**Files:**
- Modify: `deploy/scripts/provision-gce.sh`
- Create: `docs/runbooks/environments.md`

**Requirements:**
- A **separate VM**, smaller machine type. Not a tenant on the production VM: a
  non-prod tenant sharing production's Postgres, Docker daemon and disk cannot
  test a Postgres upgrade, cannot absorb a load test, and shares the blast radius
  of the thing it exists to protect.
- A promotion path — build → deploy to non-prod → verify → deploy to prod —
  replacing in-place `docker compose up -d --build` on the production host.

**Verification:**

```
- [ ] A non-prod VM provisions from the script
- [ ] A tenant stands up on it end to end
- [ ] The promotion path is documented and exercised once
- [ ] Production deploys no longer build in place
```

---

### Task 8: MFA, SSO and password policy

**Files:**
- Create: `docs/analysis/2026-08-08-auth-verification.md`
- Then, only if needed: a fork patch for password expiry

**Verify first, build second.** §7.3's current status is "no evidence found",
and turning that into a documented yes or no is worth more than speculatively
building something that may already exist.

```
- [ ] Log in to a running instance; document whether MFA is available and enabled
- [ ] Document whether the SAML SSO design (2026-08-02 spec) is implemented
- [ ] Document the current password policy
- [ ] Implement 90-day expiry if absent (`PASSWORD_EXPIRY_DAYS`, default 0 = off)
- [ ] Verify by attempting a login with an expired password
```

**Report the findings honestly** — including "MFA is available and was never
enabled", which is a configuration finding and a better outcome than a build.

---

### Task 9: Settings and runbook index

**Files:**
- Modify: `deploy/tenants/example.env`, `deploy/infra.env`
- Modify: `README.md`

```
- [ ] The five settings are present and documented
- [ ] MONITORING_ENABLED defaults false; PASSWORD_EXPIRY_DAYS defaults 0
- [ ] README links every new runbook
- [ ] The §8.1.5 / §9 scope boundary is stated in the README, not only here
```

**The README statement (the deliverable):**

> This platform runs on a **single GCE VM with Docker Compose**. There is no
> high availability, no failover and no second zone. Backups are now copied
> offsite and a restore has been drilled with a measured RTO — see
> `docs/runbooks/disaster-recovery.md`. **The 99.9% availability and P1 `<2h`
> commitments in the RFP are not supportable on this architecture**; they require
> multi-zone HA (gap R17) and a 24/7 on-call rota, both of which are commercial
> decisions rather than engineering tasks.

---

## Definition of done

- [ ] Backups land in a different region, versioned, with a loud failure path.
- [ ] The restore script has been **run**, from GCS, onto a scratch VM.
- [ ] RTO measured and recorded; if it exceeds the committed SLA, that is reported.
- [ ] Every alert fired deliberately and confirmed to reach a human, each with a named owner.
- [ ] An archived record is readable with `jq` alone.
- [ ] The recordings-vs-7-years conflict is raised as an open question, not resolved by assumption.
- [ ] `rebase.sh` reports all failing patches; the inventory covers all 49.
- [ ] A non-prod VM exists and production no longer builds in place.
- [ ] MFA/SSO status documented from a live instance.
- [ ] The §8.1.5 / §9 scope boundary is written where a reader of the README will see it.
- [ ] Nothing merged to `main`.
