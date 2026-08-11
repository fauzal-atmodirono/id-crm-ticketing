# Data retention runbook

**Owner:** platform engineer on duty · **Last reviewed:** 2026-08-11

---

## 1. The open question that has to be settled first

> §4.84 requires all operations data to be retained ≥ 7 years.
> `PHONE_RECORDING_RETENTION_DAYS` is 90. **These are contradictory for call
> audio.** We have assumed §4.84 refers to transactional and case data, not to
> seven years of dual-channel call recordings — which would be a materially
> different storage cost and a different data-protection position. **This
> assumption needs the client's confirmation before the retention policy is
> signed off.**

It is written as an assumption on purpose. Deciding it either way inside
engineering would either commit the client to a storage bill they never agreed
to, or delete evidence they believed they were keeping. Neither is ours to
choose.

Everything below is built on that assumption. If the client says recordings are
in scope for the seven years, §4 changes and so does the cost model.

## 2. What retention actually exists today, per store

**Read the "Enforced?" column carefully.** Three of these are code that exists
and has no scheduler, which is not the same as a policy that runs.

| Store | Policy | Enforced? |
|---|---|---|
| Nightly backups, local (`/backups`) | 7 days, then deleted | **Yes.** `backup.sh` prunes with `find -mtime +7`, every night. |
| Nightly backups, offsite (GCS) | Standard 30 d → Nearline 365 d → Coldline, kept indefinitely | **No.** The lifecycle JSON exists (`deploy/gcs/backup-bucket-lifecycle.json`); no bucket exists to apply it to. |
| `agent_<t>.ai_actions`, `agent_<t>.processed_deliveries` | Archived to GCS then purged beyond `ARCHIVE_HOT_WINDOW_DAYS` (default 730) | **No.** `archive-old-data.sh` exists, has never been run anywhere, and has no cron entry. |
| Chatwoot conversations, messages, contacts, attachments | **Kept forever** | Yes, by omission — nothing deletes them. See §5. |
| Call recordings | 90 days (`PHONE_RECORDING_RETENTION_DAYS`) | **No.** See §3. |
| Authorisation audit log | 365 days (`AUDIT_LOG_RETENTION_DAYS`) | **No, and it is worse than unscheduled.** See §3. |
| BigQuery analytics tables | 7 years | **No.** See §4. |

## 3. The two purge jobs that exist and do not run

Both were written as pure functions taking a list of records and a delete
callback. Both are unit-tested. **Neither has a caller anywhere in the
codebase** — verified by search on 2026-08-11 — so no scheduler invokes them, no
cron entry runs them, and nothing on a live tenant is being purged by either.

### 3.1 Call-recording retention

`backend/apps/backend/src/chatbot/features/chat/phone/retention.py`,
`run_retention_purge_job()`.

- Gated on `phone_retention_job_enabled`, a real `Settings` field defaulting to
  **`false`**, so it is off even if something did call it.
- Reads `phone_recording_retention_days` (default 90) and marks each recording
  older than the cutoff as `is_deleted`, clearing `recording_url`.
- **It is passed a list of recordings.** It does not query for them and does not
  page. Whoever calls it has to supply the whole candidate set.

**What it would take to run:** a scheduler (there is none in the backend), a
query that lists recordings due for purge, and `PHONE_RETENTION_JOB_ENABLED=true`
on the tenant.

### 3.2 Authorisation audit-log purge

`backend/apps/backend/src/chatbot/features/authz/audit_purge.py`,
`run_audit_log_purge_job()`.

- Reads `audit_log_retention_days`, defaulting to 365, and
  `audit_purge_job_enabled`, defaulting to *true when absent*.
- **Neither of those is a `Settings` field.** Both are read with `getattr(...,
  default)`, and `grep` finds no `audit_log_retention_days` or
  `audit_purge_job_enabled` in `platform/config.py`. So **no environment
  variable can configure or disable this job**: setting
  `AUDIT_LOG_RETENTION_DAYS=90` in a tenant env file has no effect, because
  pydantic-settings never binds it to anything the function reads.
- Its own test asserts the disable path using
  `settings.model_copy(update={"audit_purge_job_enabled": False})`, which sets an
  attribute pydantic never declared. The test passes; the flag it tests does not
  exist as configuration.

**What it would take to run:** the two settings added to `Settings` and to
`deploy/tenants/example.env`, a scheduler, and a query that lists audit rows.
Until then, the audit log grows without bound and its documented 365-day
retention is not in force. **Do not describe audit-log retention as
configurable.**

## 4. BigQuery — 7 years, not configured

The reporting layer's tables are created with `create_table(..., exists_ok=True)`
and **no expiration is set**, so no table or partition expiration exists to
enforce or to breach. §4.84's seven years is currently satisfied by nothing
deleting anything, which is not the same as a policy.

To set it (once per dataset, from a workstation with `bq` authenticated):

```bash
# 7 years in seconds, allowing for two leap days: (7*365 + 2) * 86400
SEVEN_YEARS=220924800
bq update --default_table_expiration "$SEVEN_YEARS" \
          --default_partition_expiration "$SEVEN_YEARS" <project>:<dataset>
bq show --format=prettyjson <project>:<dataset> \
  | jq '{defaultTableExpirationMs, defaultPartitionExpirationMs}'
```

Two warnings before running it:

1. **`--default_table_expiration` applies to tables created *after* it is set.**
   Existing tables keep whatever they have (nothing). Each existing table needs
   `bq update --expiration <seconds> <project>:<dataset>.<table>` as well.
2. **An expiration on an analytics table is a delete.** Seven years from now
   these tables start dropping partitions. That is the requirement, but it should
   be a decision someone made knowingly, not a side effect of a flag.

**Not run.** No GCP credentials existed in the environment this was written in.
There is a related, separate BigQuery gap already recorded in the blocked-work
register (§3c-2, §3c-3): eleven views that were never created, and a `qa_labels`
`ALTER TABLE` that is owed. Same class of problem, same unblocker — one live run
with credentials.

## 5. What is not archived, and why

`archive-old-data.sh` covers `ai_actions` and `processed_deliveries` in
`agent_<tenant>` and nothing else. That is deliberate.

**Chatwoot's own tables are out of scope.** Trimming `conversations` and
`messages` out of `chatwoot_<tenant>` means walking a foreign-key graph the
application owns — attachments, reporting events, mentions, inbox members — and
Chatwoot's own upgrade migrations assume that graph is intact. Deleting from
underneath it risks corrupting a live CRM to save disk, which is the wrong
trade. If a hot window on conversation history is genuinely required it needs its
own design, its own migration testing, and an understanding of what Chatwoot's
reporting does when the rows it aggregates disappear.

**`conversation_lifecycle` is out of scope** because it is live per-conversation
state, not history.

The practical consequence: **Chatwoot's database grows without bound on a single
VM's disk**, and disk exhaustion is the most likely failure mode this platform
has (see `docs/runbooks/monitoring-alerts.md` §3). Archiving the agent tables
helps at the margin; it does not solve that.

## 6. Running the archive job

```bash
cd /opt/platform/deploy

# Dry run first — always. Reports how many rows are past the window and what it
# would write. Extracts nothing, uploads nothing, deletes nothing.
./scripts/archive-old-data.sh --all-tenants

# Then, with a bucket:
export ARCHIVE_GCS_BUCKET=<bucket>
./scripts/archive-old-data.sh --tenant proton --apply
```

`--apply` is refused without `ARCHIVE_GCS_BUCKET`, because purging rows whose
only copy would sit on the disk you were freeing defeats the point.

**Order of operations, and why:** extract → upload → verify the object is there →
*then* delete. If the upload fails the rows are still in Postgres. Re-running is
safe: the object path is keyed on tenant, table and run date so a retry
overwrites its own object rather than duplicating, and once the purge has
happened the same query selects nothing.

Suggested cron, monthly rather than nightly — this is a slow-moving window and a
job that deletes should not run more often than someone reads its output:

```
0 4 1 * * ARCHIVE_GCS_BUCKET=<bucket> /opt/platform/deploy/scripts/archive-old-data.sh --all-tenants --apply >> /var/log/platform-archive.log 2>&1
```

**Not installed anywhere.**

## 7. Reading an archive back, with no application involved

This is the property that makes it an archive rather than a database export. Each
run writes two objects:

```
gs://<bucket>/platform-archive/<tenant>/<table>/<date>/manifest.json
gs://<bucket>/platform-archive/<tenant>/<table>/<date>/<table>.ndjson
```

```bash
gsutil cp "gs://<bucket>/platform-archive/proton/ai_actions/2028-01-01/*" .
jq . manifest.json                              # what this is, and its cutoff rule
jq -c '{id, decision, created_at}' ai_actions.ndjson
jq -r 'select(.id==101) | .output' ai_actions.ndjson
```

The manifest names the tenant, database, table, date column, cutoff rule, row
count, id bounds, and the exact `jq` command to read the data file. No knowledge
of this repository is required to interpret it.

**Verified**, against a fabricated archive on a laptop: the NDJSON round-trips
through `jq` alone, a text value containing a newline survives, the manifest's
`row_count` matches the line count, and a `null` token count stays `null` rather
than becoming `0`. **Not verified against real data** — see §8.

## 8. What has and has not been verified

| Claim | Status |
|---|---|
| `backup.sh` prunes local backups after 7 days | Pre-existing behaviour, unchanged. Never observed running by this work. |
| GCS lifecycle transitions | **Never applied.** No bucket exists. |
| `archive-old-data.sh` round-trips a record and is idempotent | Exercised against stub `docker`/`gsutil`; re-running archived nothing twice. **Never run against real Postgres or GCS.** |
| An archived record is readable with `jq` alone | Verified on a fabricated archive. |
| BigQuery expirations | **Not configured.** Commands above are untested. |
| Recording purge runs at 90 days | **It does not run at all.** §3.1. |
| Audit purge runs at 365 days | **It does not run at all, and cannot be configured.** §3.2. |

## 9. The schema-migration constraint that affects all of this

**This repo has no Alembic.** Schema is created by
`Base.metadata.create_all` in `init_db`, which does nothing to a table that
already exists. So any new column on an already-deployed tenant needs a manual
`ALTER TABLE`, and any retention work that adds a column inherits that.

There are already two register entries for exactly this: §3c-1 (`ai_actions`
needs `output_tokens`/`cached_tokens` added by hand on every live tenant) and the
`qa_labels` BigQuery one in §3c-3. If §3's purge jobs ever gain a
`purged_at`-style column, it joins that list rather than starting a new one.

## 10. Related

- `deploy/scripts/archive-old-data.sh` — its header states exactly what it
  archives and what it refuses to touch.
- `deploy/gcs/README.md` — the lifecycle rules and why there is no delete rule on
  current backup objects.
- `docs/runbooks/disaster-recovery.md` — §3 for the RPO that follows from the
  backup schedule.
- `docs/analysis/2026-08-09-blocked-work-register.md` — the BigQuery and
  `ALTER TABLE` items this section refers to.
