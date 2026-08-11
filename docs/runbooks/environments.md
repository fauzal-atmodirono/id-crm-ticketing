# Environments and the promotion path

**Owner:** platform engineer on duty · **Last reviewed:** 2026-08-11

---

## 1. What exists today

| Environment | Where | Status |
|---|---|---|
| Production | one GCE VM, Docker Compose, tenants `default`, `proton`, `wahchan` | Live |
| Non-production | — | **Does not exist.** Never provisioned. |

**Production deploys build in place, on the production host.** The documented
procedure is:

```bash
docker compose -p <tenant> -f docker-compose.tenant.yml --env-file tenants/<t>.env \
  up -d --build backend agent
```

That `--build` runs a Docker build on the machine serving customers, using its
CPU and its disk, and if the build fails the tenant can be left with a stopped
service and no previous image to fall back to. It is also the only test the
change gets: there is nowhere else for it to run first.

**Nothing in §2 has been provisioned or exercised.** No GCP credentials existed
in the environment this runbook was written in. It is a design and a procedure,
not a description of something running.

## 2. The non-production environment

### 2.1 A separate VM, not a tenant on the production VM

`add-tenant.sh` makes a new tenant cheap, so "just add a `staging` tenant" is the
obvious move and it is the wrong one. A non-prod tenant on the production VM
shares that VM's **Postgres server, Docker daemon, kernel and disk**. Therefore
it cannot:

- test a Postgres major-version upgrade — there is one server, shared;
- test a Docker or host OS change;
- absorb a load test without degrading production;
- contain a mistake. A runaway migration, a disk-filling import or an OOM in
  "staging" takes production down with it.

It shares the blast radius of the thing it exists to protect, which makes it a
worse-than-useless kind of confidence.

### 2.2 Provisioning it

`provision-gce.sh` already takes every relevant knob as an environment variable,
so no change to the script is needed — the environment definition is the
deliverable:

```bash
# Non-production. Smaller machine, smaller disk, its own IP, its own firewall
# rule and network tag so its exposure is separate from production's.
PROJECT_ID=<project> \
VM_NAME=crm-nonprod \
MACHINE_TYPE=e2-standard-2 \
BOOT_DISK_SIZE=40GB \
NETWORK_TAG=crm-nonprod \
ADDRESS_NAME=crm-nonprod-ip \
  ./deploy/scripts/provision-gce.sh
```

Then bootstrap and stand up one tenant, exactly as production was built:

```bash
gcloud compute scp --recurse --zone=<zone> deploy agent backend crm-nonprod:/tmp/platform
gcloud compute ssh --zone=<zone> crm-nonprod \
  --command="sudo mkdir -p /opt/platform && sudo mv /tmp/platform/* /opt/platform/"
gcloud compute ssh --zone=<zone> crm-nonprod
sudo /opt/platform/deploy/scripts/bootstrap-vm.sh
cd /opt/platform/deploy && ./scripts/add-tenant.sh staging
```

**Two things that must differ from production, and are easy to forget:**

1. **Real credentials do not belong here.** A non-prod tenant with the
   production Gemini key, the production SMTP account or a real WhatsApp number
   can send a message to an actual customer from what everyone believes is a test
   system. Use Mailpit for mail (it is already the default), a separate Gemini
   key, and no production channel tokens.
2. **`AGENT_MODE`** — keeping non-prod on the default `suggest` means AI replies
   land as private notes rather than being sent. If you set `auto` to test it,
   set it back.

### 2.3 Costs

An `e2-standard-2` with a 40 GB disk is roughly a third of the production VM.
That is the price of not testing changes on the machine serving customers. It is
a small number against one bad in-place build.

## 3. The promotion path

The point is to stop the production host being the place a change is first
executed.

```
   build (off-VM)  →  deploy to non-prod  →  verify  →  deploy to prod
```

### 3.1 Build off-VM, once

Images, not source. This is already mandatory for the Chatwoot fork image and
should be the rule for `agent` and `backend` too.

```bash
# Chatwoot fork image — amd64, off-VM, never on the prod VM and never from an
# arm64 Mac (the VM's pull fails with "no matching manifest").
gcloud builds submit deploy/chatwoot-fork/ \
  --config deploy/chatwoot-fork/cloudbuild.yaml \
  --substitutions _REGISTRY=<artifact-registry-repo>

# agent and backend — the same idea, built once and tagged, rather than built
# twice from source on two hosts.
gcloud builds submit agent/    --tag <registry>/platform-agent:<git-sha>
gcloud builds submit backend/  --tag <registry>/platform-backend:<git-sha>
```

**`docker-compose.tenant.yml` currently pins `platform-agent:latest` and
`platform-backend:latest`**, which is what makes an in-place `--build` the only
way to update them. Moving to `${AGENT_IMAGE:-platform-agent:latest}` and
`${BACKEND_IMAGE:-platform-backend:latest}` would let a tenant env name an
immutable tag, and would make a rollback `docker compose up -d` with the previous
tag instead of a rebuild. **That change has not been made** — it is a
one-line-per-service edit to a compose file outside this work's scope, and until
it is made step 3.4 below still has to build in place.

### 3.2 Deploy to non-prod

```bash
gcloud compute ssh --zone=<zone> crm-nonprod
cd /opt/platform/deploy
docker compose -p staging -f docker-compose.tenant.yml --env-file tenants/staging.env pull
docker compose -p staging -f docker-compose.tenant.yml --env-file tenants/staging.env up -d
```

### 3.3 Verify — the list, because "it came up" is not verification

- [ ] Every service reports healthy: `docker compose -p staging ... ps`
- [ ] `curl -fsS http://staging.agent.<ip>.nip.io/healthz` → `{"status":"ok"}`
- [ ] Chatwoot loads and you can log in
- [ ] The specific thing you changed does the thing you changed it to do
- [ ] **If a fork patch changed:** the page it touches actually renders. A patch
      can apply cleanly and still break the Vite build or the runtime — see
      `deploy/chatwoot-fork/PATCH-INVENTORY.md` and `rebase.sh`
- [ ] Nothing new in `docker compose ... logs --tail=200` for each service
- [ ] If a database column was added: the `ALTER TABLE` ran, because this repo has
      no Alembic and `create_all` does nothing to an existing table
      (`docs/analysis/2026-08-09-blocked-work-register.md` §3c-1)

### 3.4 Deploy to production

```bash
gcloud compute ssh --zone=<zone> crm-ticketing
cd /opt/platform/deploy
# Sync the source first — /opt/platform is synced source, NOT a git checkout, so
# there is nothing to `git pull` here.
docker compose -p <tenant> -f docker-compose.tenant.yml --env-file tenants/<t>.env pull
docker compose -p <tenant> -f docker-compose.tenant.yml --env-file tenants/<t>.env up -d
```

Deploy one tenant, watch it, then the next. All tenants at once turns one bad
release into three outages.

**Rollback** is the previous image tag plus `up -d` — but only once §3.1's
compose change is made. Today, with `:latest` pinned, rollback means rebuilding
the previous source in place, which is the situation this whole section exists to
remove.

## 4. What is still owed

| Item | Status |
|---|---|
| A non-prod VM | **Never provisioned.** §2.2 is untested. |
| A tenant standing up on it end to end | Never done. |
| The promotion path exercised once | Never done. |
| Production no longer building in place | **Not achieved.** Needs the compose image-tag change in §3.1. |
| Images pushed to Artifact Registry for `agent`/`backend` | Never done; they are built on the VM today. |

Recorded in `docs/analysis/2026-08-09-blocked-work-register.md`.

## 5. Related

- `docs/runbooks/disaster-recovery.md` — rebuilding after losing the VM, which
  shares most of §2.2's steps.
- `deploy/chatwoot-fork/rebase.sh` — what to run when upstream Chatwoot cuts a
  release, before any of this.
- `CLAUDE.md`, "Deploy notes" — the amd64/arm64 trap and why the fork image is
  never built on the VM.
