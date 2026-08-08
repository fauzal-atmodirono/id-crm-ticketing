# P13 — Ops Hardening: Restore, Monitoring, Retention, Security Policy

**Date:** 2026-08-08
**Programme:** `docs/superpowers/specs/2026-08-08-rfp-partials-program-design.md`
**Plan:** `docs/superpowers/plans/2026-08-08-rfp-p13-ops-hardening.md`
**Closes:** 7 PARTIAL requirements + 4.84 (GAP)
**Effort:** 3 weeks · **Wave:** 2 · **Blocked by:** nothing

---

## 1. Framing: what this package does and does not attempt

**It does not attempt 99.9% uptime.** §8.1.5 commits to 43 minutes of downtime a
month and §9 commits to P1 resolution in under 2 hours, 24/7, on a **single GCE
VM running Docker Compose with a shared Postgres, no HA, no failover and no
second zone**. No application-level work changes that. Multi-zone HA is R17 —
4–6 weeks plus a materially higher run cost — and it is a commercial decision to
put in front of the client, not an engineering task to start.

**What P13 does is make the architecture that exists honestly operable**, and
close the operational and security partials that are genuinely engineering work.
That distinction matters: a monitoring stack does not deliver 99.9%, but its
absence means nobody would *know* about the 43 minutes.

## 2. The problem, precisely

**Backups are kept on the machine they protect.** `deploy/scripts/backup.sh` is
well-written — nightly per-tenant `pg_dump` plus a storage tarball into
`/backups/YYYY-MM-DD/`, 7-day pruning, cron-safe, absolute paths, `set -euo
pipefail`. And it writes to local disk on the same VM that holds the data. **There
is no restore script, no offsite copy, no RTO, no RPO, no DR runbook and no
restore drill.** A VM loss takes the data and the backups together.

**There is no monitoring or alerting anywhere in `deploy/`.** Only a liveness
endpoint (`agent/app/routers/health.py`). No Prometheus, no Grafana, no Cloud
Monitoring, no alerting rules, no resource or storage monitoring. §8.1.11 asks
for proactive health checks. Today the first indication of a problem is a
customer complaint.

**"Retain 7 years" appears only in proposal prose (§4.84).** The single retention
setting is `phone_recording_retention_days=90`, whose own comment says nothing
reads it (P11 fixes that one). No lifecycle rule, no archival job, no purge
scheduler, no BigQuery or GCS retention configuration.

**Updates are a 49-patch fork rebase** (§2.4.4, §8.1.13). Every upstream Chatwoot
security release requires rebasing 49 patches and an `amd64` Cloud Build. That is
a real, recurring, priceable liability and it currently has no tooling.

**There is no non-production environment** (§2.2.7). `add-tenant.sh` makes a
sandbox tenant cheap and the platform is multi-tenant, but no non-prod
environment is defined or stood up, and production deploys are in-place
`docker compose up -d --build` **on the same VM**.

**MFA and SSO cannot be confirmed** (§7.3). A SAML SSO design spec exists
(`2026-08-02-native-saml-sso-security-design.md`); the gap analysis found no
implementation, and no MFA implementation. Both need a login to a running
instance to settle. **No 90-day password expiry policy exists** (§7.6).

## 3. Design

### 3.1 Restore, offsite, and a drill (§2.4.5, §8.1.12)

The three deliverables, in order of how much they reduce risk:

**Offsite copy.** After each nightly run, sync `/backups/YYYY-MM-DD/` to a GCS
bucket in a different region, with object versioning and a lifecycle policy. This
is the change that turns "we have backups" from false into true — a backup on the
machine it protects is a convenience copy, not a backup.

**A restore script.** `deploy/scripts/restore.sh`, taking a tenant and a date,
restoring the database and the storage volumes. Non-obvious requirements:

- **It must be able to restore into a *different* tenant name**, so a drill can
  restore production data into a scratch tenant without touching production. A
  restore script that can only overwrite the thing it is restoring cannot be
  practised, and an unpractised restore script does not work.
- **`--dry-run` is the default.** A destructive script whose default is
  destructive will one day be run with a wrong argument.
- **It verifies before it destroys**: checks the archive is present, complete and
  loadable *before* dropping anything.

**A drill, recorded.** Restore the previous night's backup into a scratch tenant,
verify conversation counts and a sample record, time it, and write the result
into a DR runbook with the measured RTO. **The measured number is the deliverable**
— an RTO in a proposal that nobody has measured is a guess with a number on it.

RPO follows from the schedule: nightly backups mean up to 24 hours of loss. If
that is unacceptable to the client, the answer is more frequent backups or
Postgres WAL archiving, and that is a costed conversation — stated, not silently
assumed away.

### 3.2 Monitoring and alerting (§8.1.11)

Cloud Monitoring rather than a self-hosted Prometheus/Grafana pair, for one
reason: **a monitoring stack on the VM it monitors goes down with it.** The whole
point is to be told when the host has a problem.

Four layers:

| Layer | Signals | Alert on |
|---|---|---|
| Host | CPU, memory, **disk**, load | Disk > 80%; sustained memory pressure |
| Container | Per-service up/down, restart count | Any service down > 2 min; restart loop |
| Application | Health endpoints, webhook error rate, queue depth | Health failing > 2 min; error-rate spike |
| Business | Sync freshness, SLA scanner last-run, escalation send failures | Sync stale > 2× interval; scanner not run in 30 min |

**Disk is first for a specific reason:** backups, Postgres, Docker images and
Chatwoot storage share one disk, and `backup.sh` writes to it nightly. Disk
exhaustion is the most likely single-VM failure here and it takes everything down
at once.

**The business layer matters more than the host layer for this system.** A silent
SLA scanner is worse than a brief CPU spike: nothing appears broken, alerts
simply stop firing, and the first symptom is a missed escalation nobody can
explain. P2's escalation-failure counter feeds directly into this.

Alerts route to a channel a human actually watches, and the runbook names an
owner per alert. An alert with no owner is a log line.

### 3.3 Retention (§4.84)

Seven years is a **storage-cost commitment**, and the design's first job is to
make that visible before it is signed:

| Store | Mechanism | Note |
|---|---|---|
| BigQuery | Table expiration + partition expiration, set to 7 years | Cheap; the natural home for long-term analytics |
| Postgres (Chatwoot) | Archive-then-purge to GCS beyond a hot window | Keeping 7 years hot on one VM's disk is not viable |
| GCS backups | Lifecycle rule: Standard → Nearline → Coldline | Cost management |
| Call recordings | 90 days (P11 enforces it) | **Conflicts with 7 years — see below** |

**The recording conflict must be raised, not silently resolved.**
`PHONE_RECORDING_RETENTION_DAYS=90` and "retain all operations data ≥7 years" are
contradictory for call audio. Almost certainly the client means transactional and
case data, not seven years of voice recordings — but that is an assumption about
a compliance requirement, and this design records it as a question rather than
picking an answer. Seven years of dual-channel call audio is a materially
different storage bill.

The archive job writes a documented, self-describing format — a manifest plus
newline-delimited JSON — so restoring a 2030 record does not require the 2026
application. An archive only readable by the system that wrote it is not an
archive.

### 3.4 Fork-rebase tooling (§2.4.4, §8.1.13)

49 patches, rebased against every upstream Chatwoot security release, then an
`amd64` Cloud Build. No tooling exists.

- **`deploy/chatwoot-fork/rebase.sh`** — pins the current upstream ref, fetches
  the target, applies patches in order, and **reports precisely which patches
  fail** rather than stopping at the first. Knowing that 3 of 49 conflict is a
  half-day; discovering them one at a time is a week.
- **A patch inventory** — each patch's number, purpose, files touched and
  conflict risk. Half of the rebase cost is rediscovering what patch `0031` was
  for.
- **CI** applying the patch series against the pinned ref on every change, so a
  broken series is caught at commit rather than at build.

**Note for whoever prices §2.4.4:** this reduces the cost of the fork liability;
it does not remove it. 49 patches against a fast-moving upstream is a standing
commitment and should be priced as one.

### 3.5 Non-production environment (§2.2.7)

A second VM, not a second tenant on the production VM. A "non-prod tenant"
sharing production's Postgres, Docker daemon and disk cannot test a Postgres
upgrade, cannot absorb a load test, and shares the blast radius of the thing it
exists to protect.

`provision-gce.sh` already exists and does most of this; the deliverable is a
documented environment definition, a smaller machine type, and a promotion path
(build → deploy to non-prod → verify → deploy to prod) replacing in-place
`--build` on the production host.

### 3.6 MFA, SSO and password policy (§7.3, §7.6)

**Verification before implementation.** Two of these could not be settled from
code, and the honest first step is to log in to a running instance and find out:

- Is MFA available and enabled in this Chatwoot version?
- Is the SAML SSO design implemented anywhere?

Then, and only then, implement what is missing. **90-day password expiry** is
almost certainly not present and is a small fork patch plus a policy setting.

The verification is itself a deliverable: §7.3's current status is "I found no
evidence", and turning that into a documented yes or no is worth more than
speculatively building something that already exists.

## 4. What could go wrong

| Risk | Mitigation |
|---|---|
| A restore script that has never been run does not work | The drill is a required deliverable, with a measured RTO |
| A restore overwrites production during a drill | Restores into a different tenant name; `--dry-run` default; verify-before-destroy |
| The monitoring stack dies with the host it monitors | Cloud Monitoring, not self-hosted on the VM |
| Alert fatigue | Owner named per alert; business-layer alerts prioritised over host noise |
| Seven years of call audio silently accepted | The conflict is raised as a question, not resolved by assumption |
| An archive nobody can read in 2030 | Self-describing manifest + NDJSON |
| The fork rebase is assumed cheap because tooling exists | Explicitly priced as a standing liability |
| A non-prod tenant on the prod VM gives false confidence | Separate VM |

## 5. Testing

Mostly operational verification rather than unit tests, and the plan records
evidence rather than assertions.

- **Restore** — restore the previous night's backup into a scratch tenant;
  conversation counts and a sampled record match; RTO recorded.
- **Offsite** — object present in GCS in a different region; versioning on;
  lifecycle applied.
- **Monitoring** — each alert fired deliberately (fill the disk on a scratch VM,
  stop a container, stall the sync) and confirmed to reach a human.
- **Retention** — the archive job round-trips a record; BigQuery expiration set;
  a restored archive is readable without the application.
- **Rebase** — the script reports all failing patches, not just the first; CI
  catches a deliberately broken patch.
- **Security** — MFA and SSO status documented from a live instance; the 90-day
  policy verified by an expired-password login attempt.

## 6. Settings

| Setting | Default | Effect |
|---|---|---|
| `BACKUP_GCS_BUCKET` | unset | Unset = today's local-only backups |
| `BACKUP_RETENTION_DAYS` | `7` | Local; GCS lifecycle handles long-term |
| `ARCHIVE_HOT_WINDOW_DAYS` | `730` | Postgres hot window before archival |
| `MONITORING_ENABLED` | `false` | Off = no metric export |
| `PASSWORD_EXPIRY_DAYS` | `0` | 0 = no expiry, as today |

## 7. Requirements closed

2.2.7, 2.4.4, 2.4.5, 7.6, 8.1.11, 8.1.12, 8.1.13, and **4.84** (GAP) — plus a
documented answer for **7.3**, which today is "no evidence found" and will become
a verified yes or no.

**Explicitly not closed:** §8.1.5 (99.9% uptime) and §9 (P1 `<2h`, 24/7). Those
need multi-zone HA (R17) and a 24/7 on-call rota. This package makes the current
architecture observable and recoverable; it does not make it highly available,
and no engineering inside this scope can. **That gap should be closed
commercially, before the SLA is signed.**
