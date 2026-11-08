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

On the VM (the three files stay together in `/tmp`, the script finds the
others next to itself):

```bash
cd /opt/platform/deploy
```

## 2. Dry run (default, changes nothing)

```bash
sudo bash /tmp/proton_deploy_2026-11-10.sh --dry-run
```

Expect: `ok: containers up`, `ok: /api 200, data_services ok`, `ok: disk free ...`,
`patching file` lines for 15 files with no `FAILED`/`Hunk` lines, a list of env
keys as `replace|append KEY=<set>`, the plan, and `dry-run complete`.
Verified read-only against the VM on 2026-11-09: all 15 files apply cleanly
(no fuzz, no offsets). If any hunk fails, stop and send the output.

## 3. Apply

```bash
sudo bash /tmp/proton_deploy_2026-11-10.sh --apply
```

Stops on the first failure and says which step failed. It:
1. preflight (containers up, `/api` ok, disk > 5 GB) and pulls the rc16 image
2. backs up the 15 files and `tenants/proton.env` to `/backups/predeploy-20261110/`
3. `patch -p1 -F0` in `/opt/platform`
4. writes the backend env keys and `CHATWOOT_IMAGE=...:v4.15.1-custom-rc16`
   (values are never printed; env writes keep the same inode)
5. `docker compose ... pull chatwoot-rails chatwoot-sidekiq`
6. `up -d --build backend`, then `up -d --force-recreate chatwoot-rails chatwoot-sidekiq`
7. health: `/api` data_services ok, backend `/healthz` 200, no `Traceback` in
   `docker logs proton-backend --since 3m`, rails image tag is rc16

Expect the last lines: `ok: rails image tag v4.15.1-custom-rc16` and `APPLY DONE`.
Rails takes up to about 3 minutes to come up; the script waits.

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

## 5. Rollback

```bash
sudo bash /tmp/proton_deploy_2026-11-10.sh --rollback
```

Restores the backed-up files (and deletes files the patch created), restores
`proton.env` from `/backups/predeploy-20261110/proton.env`, forces
`CHATWOOT_IMAGE` to rc15, recreates backend, rails, sidekiq (and agent if
`--agent` ran), then runs the same health checks against rc15. The backup folder
is kept.

## 6. After

- Record the deployed commits (`085f4f25` and earlier Wave 1 backend commits) in
  the sheet Notes.
- Ladder `dry_run`: stays ON. Turn it off only for the demo window.
