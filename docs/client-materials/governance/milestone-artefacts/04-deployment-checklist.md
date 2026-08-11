# 04 — Deployment checklist

**Requirement:** §6.3.2 · **Status:** ready to sign, with one standing caveat

## Traceability

| Evidence | Path |
|---|---|
| Full deploy and wiring runbook | `README.md` |
| Shared infrastructure compose | `deploy/docker-compose.infra.yml` |
| Per-tenant compose | `deploy/docker-compose.tenant.yml` |
| Tenant provisioning | `deploy/scripts/add-tenant.sh` · `remove-tenant.sh` |
| VM bootstrap | `deploy/scripts/bootstrap-vm.sh` · `provision-gce.sh` |
| Backup | `deploy/scripts/backup.sh` |
| Restore | `deploy/scripts/restore.sh` |
| Data archiving | `deploy/scripts/archive-old-data.sh` |
| Fork rebase tooling | `deploy/chatwoot-fork/rebase.sh` (P13 task 6) |
| Fork image build | `deploy/chatwoot-fork/cloudbuild.yaml` |
| Configuration reference | `docs/client-materials/handover/configuration.md` |
| Operational runbooks | `docs/runbooks/` |
| Monitoring | `deploy/monitoring/` |

## Checklist

**Shared infrastructure (once per VM)**
- [ ] VM provisioned (`provision-gce.sh`), bootstrapped (`bootstrap-vm.sh`)
- [ ] Service-account key mounted for ADC
- [ ] `docker compose -p platform-infra -f deploy/docker-compose.infra.yml --env-file deploy/infra.env up -d`
- [ ] Caddy issuing certificates; Postgres and Mailpit healthy

**Per tenant**
- [ ] `deploy/scripts/add-tenant.sh <name>`
- [ ] `deploy/tenants/<name>.env` completed — **including the eight Twilio credentials, which appear in no template** (`../risk-register.md` R10)
- [ ] Chatwoot setup wizard run; API, platform and bot tokens generated
- [ ] **Both webhook secrets set, and set to DIFFERENT values.** Identical secrets are a working misconfiguration whose symptom is a feature that silently never fires
- [ ] Webhooks registered in Chatwoot, including `message_created` (it has been missed on a live tenant before)
- [ ] `docker compose -p <tenant> -f docker-compose.tenant.yml --env-file tenants/<tenant>.env up -d --build backend agent`
- [ ] Health checks green
- [ ] Feature flags set deliberately; new default-off flags left off

**Chatwoot custom image**
- [ ] Built via **Cloud Build, off-VM, for `amd64`** — `gcloud builds submit deploy/chatwoot-fork/ --config deploy/chatwoot-fork/cloudbuild.yaml`
- [ ] Pulled on the VM; `chatwoot-rails` and `chatwoot-sidekiq` recreated

**Migrations owed on already-deployed tenants** — this repository has **no
Alembic**, and `create_all` does nothing to an existing table
- [ ] `ALTER TABLE ai_actions ADD COLUMN IF NOT EXISTS output_tokens INTEGER, cached_tokens INTEGER` against `AGENT_DATABASE_URL`
- [ ] `qa_labels` rubric columns in BigQuery, if `CALL_QA_ENABLED`
- [ ] `sla_policies` tier-2 columns
- [ ] `ensure_views()` once per tenant — **note it re-creates every view; run `scripts/compare-reporting-timezone.py` first if `REPORTING_TIMEZONE` is not UTC**

## The standing caveat

**Never build the Chatwoot image on the production VM, and never from an arm64
Mac.** The Vite build is heavy enough to disturb a 16 GB production VM, and an
arm64 image fails the VM's pull with "no matching manifest" — which presents as a
deploy failure rather than as an architecture mismatch.

**And `/opt/platform` on the VM is synced source, not a git repository.** There is
no `git pull` deploy path.
