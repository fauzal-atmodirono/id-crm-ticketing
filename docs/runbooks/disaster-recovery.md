# Disaster recovery runbook

**Owner:** platform engineer on duty · **Last reviewed:** 2026-08-11

> ### Read this box before you read anything else
>
> **No restore has ever been executed and the RTO is UNMEASURED.** The scripts
> this runbook drives (`deploy/scripts/restore.sh`, and the offsite copy in
> `deploy/scripts/backup.sh`) were written on 2026-08-11 and exercised only
> against stub `docker` and `gsutil` commands on a laptop. No GCE VM, no live
> Postgres, no GCS bucket and no credentials existed in the environment they
> were written in.
>
> So: the procedure below is a **plan you are executing for the first time**,
> not a drill you are repeating. Expect to hit something this document does not
> mention, and **write down what it was**. Section 7 is the rehearsal that turns
> this from a document into a capability, and it is **owed** — see
> `docs/analysis/2026-08-09-blocked-work-register.md` §3c-4.
>
> **Do not quote an RTO from this file.** There is no measured number in it,
> deliberately. An RTO in a proposal that nobody has measured is a guess with a
> number on it.

---

## 1. What this architecture can and cannot survive

The platform is **one GCE VM running Docker Compose**: shared Caddy, one shared
Postgres, Mailpit, plus one app stack per tenant. There is no high availability,
no failover, no second zone and no replica.

| Failure | Recoverable? | How |
|---|---|---|
| One tenant's database corrupted or wrongly wiped | Yes | §4, restore that tenant |
| Someone drops a tenant's data by accident | Yes | §4 |
| Chatwoot upgrade goes wrong | Yes | §4 (databases) + re-pull the previous image |
| The VM's disk fills | Yes, but it is an outage for everyone | §6 |
| The VM is deleted or the zone is lost | Yes, **if** the offsite copy is configured | §5, full rebuild |
| The VM is deleted and `BACKUP_GCS_BUCKET` was never set | **No. The data is gone.** | Nothing. Check §2 *today*. |

**The 99.9 % availability (§8.1.5) and P1-under-2-hours (§9) commitments in the
RFP are not supportable on this architecture.** Nothing in this runbook changes
that; recovery is not availability. Those need multi-zone HA (gap R17) and a
24/7 on-call rota, which are commercial decisions rather than engineering tasks.

## 2. Prerequisites — check these before you need them, not during

Run this checklist now. Every "no" is an outage you cannot recover from.

```bash
# 1. Is the offsite copy actually configured? An unset bucket means backups
#    exist only on the VM they protect.
grep -r BACKUP_GCS_BUCKET /etc/cron.d/ /etc/crontab /var/spool/cron/ 2>/dev/null
sudo crontab -l | grep backup.sh
```

- [ ] `BACKUP_GCS_BUCKET` is set in the environment the cron job runs in.
      Setting it in a shell you later close does nothing — cron does not inherit
      it. Put it in the crontab line itself or in `/etc/cron.d/platform-backup`.
- [ ] The bucket is in a **different region** from the VM (`asia-southeast2` VM →
      pick another region). A bucket in the same region shares the failure.
- [ ] Object **versioning** is on, so a bad night cannot overwrite a good one.
- [ ] Someone other than the person reading this can reach the bucket. A backup
      only one person can restore is a single point of failure with a pulse.
- [ ] The last three nightly runs ended with `Offsite copy verified` in
      `/var/log/platform-backup.log`.

Creating the bucket (once, from a workstation with `gcloud` authenticated):

```bash
PROJECT=<gcp-project>
BUCKET=<project>-platform-backups
# A DIFFERENT region from the VM's zone. VM in asia-southeast2 -> bucket in asia-southeast1.
gsutil mb -p "$PROJECT" -l asia-southeast1 -b on "gs://$BUCKET"
gsutil versioning set on "gs://$BUCKET"
gsutil lifecycle set deploy/gcs/backup-bucket-lifecycle.json "gs://$BUCKET"
# The VM's service account needs to write to it:
gsutil iam ch "serviceAccount:<vm-service-account>:roles/storage.objectAdmin" "gs://$BUCKET"
```

Then add the bucket to the cron line:

```
0 3 * * * BACKUP_GCS_BUCKET=<bucket> /opt/platform/deploy/scripts/backup.sh >> /var/log/platform-backup.log 2>&1
```

**Neither the bucket nor that cron line has been created by anyone yet.** The
lifecycle JSON in `deploy/gcs/` is written and has never been applied.

## 3. RPO — how much data a recovery loses

Backups run **once a night at 03:00**. There is no WAL archiving and no
point-in-time recovery. Therefore:

> **RPO: up to 24 hours.** A failure at 02:00 loses almost a full day of
> conversations, contacts and attachments.

This follows from the schedule; it is not a measurement. **It has not been put
to the client and not been accepted by them.** If 24 hours is unacceptable the
options are more frequent dumps (cheap, still coarse) or Postgres WAL archiving
(a real project, and a cost). That is a conversation to have before the SLA is
signed, not after the first incident.

## 4. Restoring one tenant (the common case)

Use this when a tenant's data is damaged but the VM is fine.

**Every command is a dry run until you add `--apply`.** Read the plan the dry
run prints before you go further; it lists exactly what will be destroyed.

```bash
cd /opt/platform/deploy

# 1. What have we got? Newest first.
ls -1t /backups/

# 2. Dry run. Verifies checksums and that every dump parses. Changes nothing.
./scripts/restore.sh --tenant proton --date 2026-08-10

# 3. If the tenant is live, stop it first — restore.sh refuses to work
#    underneath running containers, and --force is not the answer here.
docker compose -p proton -f docker-compose.tenant.yml --env-file tenants/proton.env \
  stop chatwoot-rails chatwoot-sidekiq agent backend

# 4. Do it. You will be asked to type the tenant name back.
./scripts/restore.sh --tenant proton --date 2026-08-10 --apply
```

**Expected output, and how to tell success from partial success:**

| You see | Means |
|---|---|
| `checksums OK` | The archive is intact. If instead you see `checksum verification FAILED`, **stop** — nothing was touched. Try the previous night. |
| `<tenant>-<app>.dump parses` for each app | The dumps are loadable. |
| `WARNING: no SHA256SUMS manifest` | Backup predates 2026-08-11. Integrity is unverified; the parse check is all you have. Proceed only if you have no newer archive. |
| `NOTE: no <tenant>-backend.dump` | Archive predates 2026-08-11. The knowledge base and RBAC tables **cannot** be restored from it. This is a partial recovery — say so, do not report a clean restore. |
| `pg_restore reported errors` | Often benign (`does not exist, skipping` from `--clean` against a fresh database). **Judge by the row counts below, not by this line.** |
| `MATCH: conversation, contact and message counts equal the source` | The database half succeeded. |
| `MISMATCH` | **Not a successful restore.** Do not hand it back to users. Investigate. |
| `count comparison UNAVAILABLE` | The archive carries no counts file (pre-2026-08-11). Unverified, *not* passed. Check by hand. |

**Then check the three things the script cannot** — it says so itself at the end:

1. Log in to Chatwoot and open a restored conversation.
2. Download an attachment on it. This is what proves the *storage volume*
   restored, not just the database. A restore that looks perfect and has lost
   every attachment is the failure mode this step exists to catch.
3. `curl -fsS http://<tenant>.agent.<ip>.nip.io/healthz` returns `{"status":"ok"}`.

## 5. Rebuilding after losing the VM (the real disaster)

This is the path that has never been walked. Work through it in order and
**record how long each step takes** — that is how §7's RTO gets measured.

```bash
# --- On a workstation, not the (now absent) VM -------------------------------
# 1. Confirm the offsite copy exists BEFORE building anything.
gsutil ls "gs://<bucket>/platform-backups/" | tail -5
gsutil ls -l "gs://<bucket>/platform-backups/<latest-date>/"
#    Expect: <tenant>-chatwoot.dump, <tenant>-agent.dump, <tenant>-backend.dump,
#            <tenant>-chatwoot_storage.tar.gz, <tenant>-counts.json, SHA256SUMS
#    If SHA256SUMS is absent the archive predates 2026-08-11 — still restorable,
#    but unverifiable. Note it in the incident record.

# 2. Provision a new VM, static IP and firewall rule.
PROJECT_ID=<project> ./deploy/scripts/provision-gce.sh
#    NOTE: this reuses the reserved static IP if it survived. If it did not, the
#    public hostnames change (they are nip.io names derived from the IP), which
#    means CHATWOOT_FRONTEND_URL and every configured webhook URL change too.
#    Budget time for that; it is not in the scripts.

# 3. Copy the source across and bootstrap Docker + shared infra.
gcloud compute scp --recurse --zone=<zone> deploy agent backend <vm>:/tmp/platform
gcloud compute ssh --zone=<zone> <vm> --command="sudo mkdir -p /opt/platform && sudo mv /tmp/platform/* /opt/platform/"
gcloud compute ssh --zone=<zone> <vm>
sudo /opt/platform/deploy/scripts/bootstrap-vm.sh

# --- On the new VM ----------------------------------------------------------
# 4. Recreate each tenant EMPTY. restore.sh replaces a tenant's data; it does
#    not create the tenant, its Postgres roles or its Caddy route.
cd /opt/platform/deploy && ./scripts/add-tenant.sh proton

# 5. Restore into it from GCS. There is no local /backups on a new VM, so the
#    fallback to the offsite copy is automatic — but be explicit.
export BACKUP_GCS_BUCKET=<bucket>
./scripts/restore.sh --tenant proton --date <latest-date> --from-gcs          # dry run
./scripts/restore.sh --tenant proton --date <latest-date> --from-gcs --apply
```

**What will still be missing after step 5, and must be redone by hand.** None of
this is in the backup, so none of it comes back with the data:

- [ ] Per-tenant API tokens and secrets in `deploy/tenants/<tenant>.env` —
      `add-tenant.sh` generated *new* ones in step 4. The Chatwoot access token,
      the Gemini key, the webhook secrets: all need re-filling (README §5–6).
- [ ] The Chatwoot → agent **webhook** and the **agent bot** registration, which
      live in Chatwoot's own configuration and are recreated by the restore only
      if they were inside the restored database. Verify both.
- [ ] The custom Chatwoot image. Re-pull `proton-chatwoot:<ver>-custom` from
      Artifact Registry; **do not build it on the VM** (16 GB, arm64/amd64 trap —
      see the deploy notes in `CLAUDE.md`).
- [ ] Cron: the nightly `backup.sh` line, **with `BACKUP_GCS_BUCKET`**. A rebuilt
      VM with no backup cron is the same disaster waiting again.
- [ ] Redis/Sidekiq queues. Not backed up, correctly — but in-flight jobs at the
      moment of failure are lost.

## 6. Disk exhaustion

Not strictly disaster recovery, but it is the **most likely** single-VM failure:
backups, Postgres, Docker images and Chatwoot storage all share one disk, and
`backup.sh` writes to it every night.

```bash
df -h /                                  # the number that matters
du -sh /backups/* | sort -h | tail       # usually the biggest movable thing
docker system df                         # then images/build cache
```

Immediate relief, least destructive first:

```bash
sudo find /backups -mindepth 1 -maxdepth 1 -type d -mtime +3 -exec rm -rf {} +   # keep 3 days, not 7
docker image prune -af --filter "until=168h"
docker builder prune -af
```

If `/backups` is what filled the disk, **check the offsite copy succeeded before
deleting anything from it.** `grep 'Offsite copy verified' /var/log/platform-backup.log`.

## 7. The drill — OWED, and what makes this runbook real

**Status: never executed.** Until it is, treat everything above as untested.

Run it against a **scratch tenant on a scratch VM**. Never against production —
`--into` exists precisely so that is unnecessary.

1. Provision a scratch VM (`VM_NAME=crm-drill MACHINE_TYPE=e2-standard-2`).
2. Bootstrap it, then `add-tenant.sh drill`.
3. Restore the previous night's `proton` backup into it **from GCS, not from a
   local copy** — the local copy is exactly what a real disaster removes:
   `./scripts/restore.sh --tenant proton --date <yesterday> --into drill --from-gcs --apply`
4. **Time it end to end**, from "start of step 1" to "a restored conversation
   opens in the browser". Not just the script's own elapsed figure — the script
   prints the data-restore time only, and says so.
5. Verify on four dimensions: conversation count, contact count, one sampled
   conversation's messages read correctly, and one attachment downloads.
6. Record the result in the table below, including whatever went wrong.
7. `remove-tenant.sh drill --purge-volumes` and delete the scratch VM.

**Cadence once it works: quarterly, and after any change to `backup.sh`,
`restore.sh` or the Postgres version.** Owner: the platform engineer on duty.

### Drill record

| Date | Ref | From | Into | Measured RTO | Integrity checks | Notes |
|---|---|---|---|---|---|---|
| — | — | — | — | **never measured** | — | No drill has been run. This row exists so its emptiness is visible rather than implied. |

**When the first drill produces a number, compare it with §9's two-hour P1
commitment and report the comparison honestly.** If a full restore takes three
hours, that is a finding for the commercial conversation, not a number to round
down.

## 8. Related

- `deploy/scripts/backup.sh` — what the archive contains, and its limits.
- `deploy/scripts/restore.sh` — the header states exactly what it overwrites.
- `docs/runbooks/monitoring-alerts.md` — how you find out a backup stopped
  running. **Nothing alerts on that today.**
- `docs/runbooks/data-retention.md` — how long archives are kept, and where.
- `docs/runbooks/environments.md` — the non-prod environment and promotion path.
