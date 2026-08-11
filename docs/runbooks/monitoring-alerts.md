# Monitoring and alerting runbook

**Owner:** platform engineer on duty · **Last reviewed:** 2026-08-11

> ### The honest state of monitoring on this platform
>
> **There is no monitoring stack, and no alert reaches a human today.**
>
> Verified by search across the whole repository on 2026-08-11: no Prometheus,
> no Grafana, no Cloud Monitoring configuration, no OpenTelemetry, no Ops Agent
> installed by `deploy/scripts/bootstrap-vm.sh`, no alerting rules, no
> notification channel, no uptime check, no metric exporter. The only
> observability that exists is listed in §1 and all of it is **pull**: it tells
> you something when you go and look.
>
> **Today, the first indication of a problem is a customer complaint.** That is
> the finding. §8.1.11 asks for proactive health checks and this platform does
> not have them.
>
> §2 is what an operator has to add, with the config to do it. §3 is the alert
> table with an owner and a first action per alert, which becomes real the moment
> §2 is done. **None of §2 has been applied and none of §3's alerts has ever
> fired** — no GCP credentials existed in the environment this was written in.

---

## 1. What can be observed today

Everything here requires someone to go and look. Nothing pushes.

| Signal | How to read it | What it is worth |
|---|---|---|
| `agent` liveness | `GET /healthz` on `<tenant>.agent.<ip>.nip.io` → `{"status":"ok"}` or 503 | Real: it executes `SELECT 1` against the agent database, so a 200 means the process *and* its Postgres connection are alive. |
| `backend` liveness | `GET /` on the backend → `{status, crm_provider, voice_provider, model}` | **Thin.** A static dict from the process. It cannot fail while the web server is up, so it proves almost nothing. See §5. |
| Container health | `docker compose -p <tenant> -f docker-compose.tenant.yml --env-file tenants/<t>.env ps` | Real per-service healthchecks are defined for redis, chatwoot-rails, agent and backend. |
| Container restarts | `docker ps --format '{{.Names}}\t{{.Status}}'` — a restart loop shows as a low uptime | Enough to spot a crash loop by eye. |
| Host disk | `df -h /` | **The most important number on this VM.** See §3. |
| Host memory / CPU | `free -m`, `top`, `vmstat 1 5` | The VM has a 4 GB swapfile with `vm.swappiness=10`, so memory pressure shows as swap-in before it shows as an OOM kill. |
| Application logs | `docker compose -p <tenant> ... logs --tail=200 <service>` | The backend uses `structlog`, so events are structured and greppable by event name. |
| Backup outcome | `grep -E 'Backup complete|ALERT' /var/log/platform-backup.log` | Only if the cron line redirects there, as README §8 instructs. |
| Escalation send failures | A **private note** on the affected Chatwoot conversation, plus an `escalation_*_failed` structlog event | See §4 — this is not a counter and there is nothing to alert on numerically. |

## 2. What to add, and how

Cloud Monitoring, not a self-hosted Prometheus/Grafana pair, for one reason: **a
monitoring stack on the VM it monitors goes down with it**, and being told the
host has a problem is the entire point.

### 2.1 Install the Ops Agent on the VM

This is the prerequisite for every host and container alert in §3. It is not
installed today and `bootstrap-vm.sh` does not install it.

```bash
# On the VM:
curl -sSO https://dl.google.com/cloudagents/add-google-cloud-ops-agent-repo.sh
sudo bash add-google-cloud-ops-agent-repo.sh --also-install
sudo systemctl status google-cloud-ops-agent
```

The VM's service account needs `roles/monitoring.metricWriter` and
`roles/logging.logWriter`.

### 2.2 Create a notification channel first

An alert policy with no channel is a log line with extra steps. Create the
channel **before** the policies, because the policies reference it.

```bash
gcloud beta monitoring channels create \
  --display-name="Platform on-call" \
  --type=email \
  --channel-labels=email_address=<a-mailbox-a-human-watches>
gcloud beta monitoring channels list --format='value(name,displayName)'
```

### 2.3 Apply the policies

`deploy/monitoring/alert-policies.yaml` holds one document per policy, each
annotated with its owner and first action. Apply them one at a time so a
rejection tells you which:

```bash
# Split and create. CHANNEL is the full channels/... name from 2.2.
CHANNEL=projects/<project>/notificationChannels/<id>
csplit -z -f policy- -b '%02d.yaml' deploy/monitoring/alert-policies.yaml '/^---$/' '{*}'
for f in policy-*.yaml; do
  gcloud alpha monitoring policies create --policy-from-file="$f" \
    --notification-channels="$CHANNEL" || echo "FAILED: $f"
done
```

### 2.4 Verify each one by firing it deliberately

An alert nobody has fired is a configuration, not an alert. **On a scratch VM,
never production:**

| Alert | How to fire it | Expected |
|---|---|---|
| Disk > 80 % | `fallocate -l 20G /tmp/filler` (then delete it) | Notification within ~5 min |
| Service down | `docker stop <tenant>-agent` | Notification within 2 min |
| Health endpoint failing | Block the uptime check's path in Caddy | Notification within 2 min |
| Backup did not run | Skip a night (or move the cron line) | Notification next morning |

**None of these has been done.** Record the date each one first fired, here:

| Alert | First fired and confirmed received |
|---|---|
| Disk > 80 % | never |
| Service down | never |
| Health endpoint failing | never |
| Backup did not run | never |

## 3. The alerts, in priority order

Business first. The host layer is the easy part and the least likely to be the
first symptom of anything.

**Every alert names an owner. An alert with no owner is a log line.**

### Business layer

| Alert | Owner | First action | Buildable today? |
|---|---|---|---|
| **Nightly backup did not run** | platform engineer | `grep 'Backup complete' /var/log/platform-backup.log`; if absent, run `backup.sh` by hand and check `df -h`. A missing backup plus the disk-full alert is the same incident. | **Yes** — from the log line, via a log-based metric with an absence condition. This is the highest-value alert on the list: it is silent, and its consequence is §5 of the DR runbook being unavailable. |
| **Offsite sync failed** | platform engineer | `grep ALERT /var/log/platform-backup.log`; re-run `backup.sh`; check the bucket's IAM. Until fixed, backups exist only on the VM they protect. | **Yes** — `backup.sh` writes `ALERT:` to stderr and to syslog and exits non-zero. |
| **Escalation send failure** | support lead | Open the conversation named in the private note; check Mailpit/SMTP; resend by hand. | **Partly.** There is no counter — see §4. A log-based metric on the `escalation_*_failed` structlog events is the buildable version. |
| **Lifecycle scanner stopped ticking** | platform engineer | `docker logs <tenant>-agent \| grep lifecycle_scanner`; restart the agent. Nothing looks broken when this stops — idle conversations simply stop being warned and closed, and the first symptom is a stale queue nobody can explain. | **Partly.** The scanner logs a start line and logs exceptions per tick, but emits no per-tick heartbeat, so absence is hard to detect. Needs one log line per tick to be alertable. See §5. |
| **Metrics sync stale** | reporting owner | Check `METRICS_SYNC_ENABLED` and the sync job's logs. | **No.** No freshness signal is emitted today. |

### Application layer

| Alert | Owner | First action | Buildable today? |
|---|---|---|---|
| **agent `/healthz` failing > 2 min** | platform engineer | It fails on a database error, so check Postgres first: `docker compose -p platform-infra -f docker-compose.infra.yml ps postgres`. | **Yes** — a Cloud Monitoring uptime check against the public URL. |
| **backend health failing > 2 min** | platform engineer | `docker logs <tenant>-backend --tail=200`. | **Weakly.** The endpoint is a static dict and cannot report a broken dependency; it only detects the process being gone. See §5. |
| **Webhook error-rate spike** | platform engineer | `docker logs <tenant>-agent`; check the HMAC secrets match Chatwoot's, since a rotated secret shows up as a wall of 401s. | **No.** No request metrics are exported. |

### Container layer

| Alert | Owner | First action | Buildable today? |
|---|---|---|---|
| **Any service down > 2 min** | platform engineer | `docker compose ... ps`, then `logs --tail=200` on the dead one, then `up -d`. | **Yes**, once the Ops Agent is installed. |
| **Restart loop** | platform engineer | `docker logs` the flapping container; a Rails container looping usually means Postgres or Redis is unreachable, not Rails. | **Yes**, once the Ops Agent is installed. |

### Host layer

| Alert | Owner | First action | Buildable today? |
|---|---|---|---|
| **Disk > 80 %** | platform engineer | §6 of the DR runbook. Check `/backups` first, then Docker images. | **Yes**, once the Ops Agent is installed. |
| **Sustained memory pressure** | platform engineer | `free -m`; sustained swap-in means a service needs a memory limit raised or the VM needs resizing. | **Yes**, once the Ops Agent is installed. |
| **CPU saturation** | platform engineer | `top`; usually Sidekiq or a Vite build that should not be running on this VM. | **Yes**, once the Ops Agent is installed. |

**Disk is the highest-value host alert.** Backups, Postgres, Docker images and
Chatwoot storage share one disk, and `backup.sh` writes to it nightly. Disk
exhaustion is the most likely single-VM failure here and it takes everything
down at once.

## 4. Things that read like metrics and are not

Worth stating plainly, because each was described elsewhere in a way that
suggests a number exists to alert on.

- **The escalation "failure counter" is not a counter.** What exists is
  `escalation_failure_note_enabled` (default `false`), which posts a **private
  note naming the recipient** on the affected Chatwoot conversation when a send
  fails, plus a structlog event. It is an audit trail for a human reading that
  conversation, not a metric. A numeric alert needs a log-based metric built on
  the structlog events.
- **The `backend` health endpoint is not a dependency check.** It returns a
  static dict. A health check that cannot fail is not a health check.
- **The recording-retention and audit-purge jobs have no scheduler**, so their
  absence cannot be alerted on — there is nothing to be absent. See
  `docs/runbooks/data-retention.md`.

## 5. Gaps that need code, not configuration

Out of scope for this runbook; recorded so they are not mistaken for oversights.

1. **`features/health_enrichment.py` exists and nothing calls it.** It reports
   per-subsystem status (database, crm, voice, knowledge) and would be the
   backend's real health surface, but `main.py`'s `health_check()` still returns
   a static dict, so the deep check is unreachable. Mounting it is a one-line
   change in a file outside this work's scope.
   Note also that as written it reports every subsystem as `ok` unconditionally —
   it names the configured provider rather than probing it — so mounting it as-is
   would produce a health check that still cannot fail. It needs real probes
   before an alert should trust it.
2. **The lifecycle scanner emits no per-tick heartbeat.** One `structlog` line
   per completed tick would make "the scanner stopped" alertable via a
   log-based metric with an absence condition. Today only its start line and its
   exceptions are logged.
3. **No request or error-rate metrics** are exported by either service, so the
   webhook error-rate alert cannot be built.
4. **No freshness timestamp** is written by the metrics sync, so staleness
   cannot be measured.

## 6. Related

- `docs/runbooks/disaster-recovery.md` — §6 for disk exhaustion, §2 for whether
  the backup is even reaching a second machine.
- `docs/runbooks/data-retention.md` — the retention jobs and their (absent)
  schedulers.
- `deploy/monitoring/alert-policies.yaml` — the policy definitions §2.3 applies.
