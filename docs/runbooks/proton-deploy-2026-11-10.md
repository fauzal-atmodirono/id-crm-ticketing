# Proton Wave 1 deploy, one window (10 Nov 2026)

Ships together: backend Tasks 5, 6, 8, 9, 10, 10b (as a patch on the VM copy),
backend env keys, Chatwoot image `v4.15.1-custom-rc16`, and optionally the
agent section. They must go out together: the backend RSA routes need an
`sla.manage` session and the rc16 image (fork patch 0035) sends it.

Never copy dev-yuda files to `/opt/platform` (it lags dev-yuda and crash-looped
prod once). The patch is only our diff, applied with `patch -p1 -F0`.

## 0. Before you start

- The ladder policy row is `dry_run = true`. Keep it that way. Turn it off
  only for the demo window (Escalation page in the CRM, or the row in
  `backend_proton.escalation_ladder_policy`), then back on.
- Do the agent step (`--agent`) only when you accept that `proton-agent` is
  recreated from its existing image (`--no-build`).

## 1. Copy the files to the VM

From the repo root on your Mac (branch `dev-yuda`):

```bash
gcloud compute scp \
  scripts/ops/proton_deploy_2026-11-10.sh \
  scripts/ops/proton_env_2026-11-10.sh \
  scripts/ops/deploy/backend-2026-11-10.patch \
  crm-ticketing:/tmp/ --project lv-playground-genai --zone asia-southeast2-a
gcloud compute ssh crm-ticketing --project lv-playground-genai --zone asia-southeast2-a
```

If you copied these files to the VM before 9 Nov evening, copy them again:
the deploy script changed in the final review (rollback keeps the MFA keys,
config-key check, longer Rails health wait).

On the VM (the three files stay together in `/tmp`, the script finds the
others next to itself):

```bash
cd /opt/platform/deploy
```

## 2. Dry run (default, changes nothing)

```bash
sudo bash /tmp/proton_deploy_2026-11-10.sh --dry-run
```

Expect: `ok: containers up`, `ok: CHATWOOT_IMAGE is rc15`, `ok: backend /healthz
baseline 200`, `ok: /api 200, data_services ok`, `ok: disk free ...`, then GNU patch
prints `checking file ...` for 15 files with no `FAILED`/`Hunk`/`fuzz` lines, a list of env
keys as `replace|append KEY=<set>`, the plan, and `dry-run complete`.
Verified read-only against the VM on 2026-11-09: all 15 files apply cleanly
(no fuzz, no offsets). If any hunk fails, stop and send the output.

## 3. Apply

```bash
sudo bash /tmp/proton_deploy_2026-11-10.sh --apply
```

If the backend `/healthz` is already not 200 before the deploy, or `/openapi.json`
already lacks one of the four route groups (the patch may legitimately add some),
the script stops and says so; investigate, or re-run with `--accept-baseline`. The
post-apply check still requires all four routes.

Stops on the first failure and says which step failed. It:
1. preflight (containers up, current `CHATWOOT_IMAGE` is rc15, backend baseline,
   `/api` ok, disk > 5 GB) and pulls the rc16 image
2. backs up the 15 files and `tenants/proton.env` to `/backups/predeploy-20261110/`,
   copies the scripts and patch there (rollback does not depend on `/tmp`), and tags
   the image `proton-backend` is actually running as `platform-backend:pre-20261110`
   (see section 7)
3. `patch -p1 -F0` in `/opt/platform`
4. writes the backend env keys and `CHATWOOT_IMAGE=...:v4.15.1-custom-rc16`
   (values are never printed; env writes keep the same inode)
5. `docker compose ... pull chatwoot-rails chatwoot-sidekiq`
6. `up -d --build backend`, backend health (`/healthz` 200, no `Traceback` since the
   container started, `/openapi.json` lists `/rsa`, `/admin/taxonomy`, `/escalation`
   routes, warning if `rsa_customer_phone_migration_failed` appears), then the
   Settings check (below), and only then
   `up -d --force-recreate chatwoot-rails chatwoot-sidekiq`
7. fork health: `/api` data_services ok, sidekiq running, rails and sidekiq image tag
   rc16, `/app/login` 200, `features:` on the login page (warning only), and
   `/app/.git_sha` starts with `db674a88`

Expect the last lines: `ok: rails + sidekiq image tag v4.15.1-custom-rc16`, `ok: /app/.git_sha db674a88`
and `APPLY DONE`. The apply also adds `customer_phone` to the RSA table (additive and
harmless, but not removed by rollback).
Rails takes up to about 3 minutes to come up; the script waits up to 5 minutes
(`HEALTH_TRIES=60` x 5 s; raised from 36 so a slow boot is not read as a failure).

Settings check: after the backend recreate the script checks, inside
`proton-backend`, that every env key name it wrote is a field of
`chatbot.platform.config.Settings` (only names go into the container, values are
never read). Expect `ok: every backend env key is a backend Settings field`. A
`WARN: proton-backend Settings has no field for: ...` line does not stop the window:
those keys do nothing in the running image. Decide whether that matters for the demo
(usually it means the patch did not land as expected); `--agent` runs the same check
against `proton-agent` with the lifecycle keys, and since the agent image is not
rebuilt a WARN there is expected for any key newer than that image.

Then in the browser: `/app/login` returns 200, sidebar renders, translate picker,
FAQ strip, RSA phone field and the step 6 (48h) row are visible, RSA page loads
(no 401).

## 4. Agent section (optional, after apply)

```bash
sudo bash /tmp/proton_deploy_2026-11-10.sh --agent
```

Restores `ESCALATION_POLICY_*` from the running `proton-agent` into the env
file, sets the lifecycle keys, then `up -d --no-build agent` and waits for the
agent `/healthz`.

Note: the running agent holds a stale 5-step `ESCALATION_POLICY_STEPS_JSON`
(timings of minutes, no step 6). The backend reads the same env file, so copying
it verbatim would remove the 48 h rung. The script overwrites that one key with
the 6-step SOP table (roles identical, delays 0/2/4/8/8/48; the stored ladder
policy still overrides the delays of steps 2-5). Only the key name is printed.

**Do not run a bare `docker compose ... up -d` between `--apply` and `--agent`.**
Before `--agent` the env file has no agent keys and a plain `up -d` would recreate
`proton-agent` and lose its running `ESCALATION_POLICY_*` values.

## 5. Rollback

**Slow Rails boot is not a failed deploy.** If the apply stops at `rails /api 200 +
data_services ok did not become healthy`, first re-check by hand before rolling back:

```bash
sudo docker exec proton-chatwoot-rails wget -qO- http://127.0.0.1:3000/api
```

If that now shows `"data_services":"ok"`, Rails was only slow: check `/app/login`
in the browser and carry on (the remaining fork checks are the image tag and
`/app/.git_sha`, which you can read with `docker inspect` / `docker exec ... cat
/app/.git_sha`). Roll back only if `/api` still fails after a few more minutes.

```bash
sudo bash /tmp/proton_deploy_2026-11-10.sh --rollback
```

If `/tmp` is gone, run the copy in the backup: `sudo bash /backups/predeploy-20261110/proton_deploy_2026-11-10.sh --rollback`.

Saves the current env to `/backups/predeploy-20261110/proton.env.pre-rollback`,
restores the backed-up files (and deletes files the patch created), restores
`proton.env`, forces `CHATWOOT_IMAGE` to rc15, and, if `--agent` ran, re-applies the
`ESCALATION_POLICY_*` lines (the restore would otherwise delete the only copy; the
agent is still recreated so its lifecycle keys roll back). It always re-applies the
`ACTIVE_RECORD_ENCRYPTION_*` lines too (the MFA keys, if
`docs/runbooks/proton-mfa-keys.md` ran after Wave 1): the pre-Wave-1 backup has none,
and those keys are permanent once a user enrols or a channel credential is saved.
Names only are printed (`re-applied N ACTIVE_RECORD_ENCRYPTION_* lines`). Backend returns to
`platform-backend:pre-20261110` without a rebuild (rollback never builds; if that tag
is missing it stops with instructions). Rollback does every restore first and skips
steps the apply never reached (it uses the markers in the backup folder); health
checks run last and only warn, with next steps in the summary. The backup folder is
kept; markers are cleared only when everything was restored.

## 6. After

- Record the deployed commits (`085f4f25` and earlier Wave 1 backend commits) in
  the sheet Notes.
- Ladder `dry_run`: stays ON. Turn it off only for the demo window. `--agent` also
  forces `ESCALATION_POLICY_DRY_RUN=true` in the env fallback and prints the
  ENABLED/DRY_RUN booleans; the DB row stays authoritative.

### Post-deploy AI persona (operator, Knowledge > Settings, inbox 3)

`MEDIA_DIAGNOSIS_PROMPT_ENABLED=true` is set by the env script (built-in photo rule:
confidence + at most one follow-up). The persona is currently empty; after the deploy
add this to the persona `instructions`:

> Photo replies: when the customer sends a photo (for example a dashboard warning light), your reply MUST have three parts in this order: (1) one sentence saying what you see, with how confident you are; (2) one practical line on what to do now; (3) exactly one follow-up question as the LAST sentence, ending with a question mark, asking for the car model and whether the light is steady or blinking. Never assume the car model from the photo and never link to a model page for a warning light. Service timing: never state or hint at a duration; say it depends on the service type, then ask which car model and which service centre, ending with a question mark.

And in the assistant **Handoff message** (breakdown case), as the first line:

> If you are on the road: switch on your hazard lights, move to a safe place away from traffic, and stay out of the car if it is on a highway. A teammate is joining you now. Please share your location and plate number.

Then re-run the three replay inputs from the Task 12 report (dashboard-light photo,
service-timing question, breakdown) against `/chat/turn` and check: photo reply has the
three parts with the question last; the service reply asks questions instead of handing
off; the breakdown handoff shows the safety line.

## 6b. Known until Wave 2

These are expected after Wave 1 and are fixed in code on `dev-yuda`, shipping with
Wave 2 (`docs/runbooks/proton-wave2-deploy.md`). Do not open them as new bugs during
the rehearsal:

- **Idle close after ~25 min in office hours** with the running agent image (the
  warn/close timing fix is in the agent rebuild, not in this window).
- **Out-of-hours idle warning reads "in 0 minutes"** (same agent rebuild).
- **A bounce of a plain agent reply gets no private note** (bounce matching by
  Message-ID ships in the agent rebuild).
- **AI draft on an escalation reply can be dropped at 10 s** (the backend call
  timeout becomes a setting, `PROTON_BACKEND_TIMEOUT_SECONDS`, in the agent rebuild).
- **"Last 7 days" (days_before) conversation filter still returns 500** until the
  rc17 image (fork patch 0086).

## 7. The backend image is shared by every tenant

`platform-backend:latest` is hard-coded in `docker-compose.tenant.yml` (no per-tenant
env var), and `/opt/platform` is shared too. The apply builds the new backend into
`platform-backend:latest`, so **aeon360 and bahana would pick up the new code on their
next `up -d --build backend` or recreate**. Until reviewed, do not recreate their
backends. To pin them, after the proton apply is healthy:

```bash
docker tag platform-backend:latest platform-backend:proton-20261110
docker tag platform-backend:pre-20261110 platform-backend:latest
```

proton's running container keeps the new image. Note a later proton recreate without
`--build` then starts from `latest` (old); use `--build` for proton, or retag
`proton-20261110` back to `latest`, and re-pin afterwards. The patched source in
`/opt/platform/backend` is also what any `--build` uses, so the others still must not
rebuild until reviewed.

## Wave 2 note: AI activity monitor (FIX-43) needs an enable step

Not part of the 10 Nov Wave 1 window — do not do this now. It applies only after the rc17 image and the Wave 2 backend ship.

The page ships with backend route `GET /admin/ai-activity` and fork patch 0081
(Chatwoot image rc17+). It stays invisible until both gates agree:

1. A superadmin turns on the custom feature `ai_activity` ("AI activity monitor",
   group AI) on the Custom Features page for the tenant. An absent key is OFF.
2. The user's role has permission `ai_activity.view`. `administrator` gets it from
   the authz seed on boot (`seed_defaults`); confirm it on proton after the backend
   recreate, and grant it to any custom role that should see the page.

The page labels its modes "inferred": they come from Chatwoot state (pending,
paused, escalated), not the agent's `ai_actions` log. No agent change is involved.
