#!/usr/bin/env bash
# One-window deploy of Proton Wave 1 (backend patch + env keys + chatwoot image
# rc16 + optional agent section). Run ON THE VM from /opt/platform/deploy, as root:
#
#     sudo bash proton_deploy_2026-11-10.sh [--dry-run]   # default: plan + patch --dry-run
#     sudo bash proton_deploy_2026-11-10.sh --apply [--accept-baseline]
#     sudo bash proton_deploy_2026-11-10.sh --agent       # only AFTER --apply
#     sudo bash proton_deploy_2026-11-10.sh --rollback
#
# Why one window: the backend's RSA routes now require an sla.manage session and
# the rc16 image carries fork patch 0035 that sends it; deploying either half
# alone breaks the RSA page (401). Backend files, env keys, image and recreate
# therefore go out together.
#
# Why a patch and not a copy: /opt/platform is synced source that lags dev-yuda.
# Copying files wholesale imports their future import graph and crash-looped
# prod once. backend-2026-11-10.patch is only OUR diff (7d816f9f..HEAD, non-test
# files under backend/apps/backend/src) and is applied with patch -p1.
#
# Secrets: this script never prints a value from proton.env. The only value it
# prints is the Chatwoot image tag. All env writes are inode-preserving
# (cat tmp > file), never mv/sed -i (bind-mount trap).
#
# Rollback targets: files from the tar backup, proton.env from its backup, and
# CHATWOOT_IMAGE forced back to the rc15 tag. Files the patch created are deleted.
# The backend returns to the image tagged platform-backend:pre-20261110 (tagged
# before the build; no rebuild). If --agent ran, the ESCALATION_POLICY_* lines
# are re-applied after the env restore, because the restore would otherwise
# wipe the only copy of them once proton-agent is recreated. We re-apply rather
# than skip recreating the agent: the lifecycle keys it also received must be
# rolled back, and that needs a recreate.
# NOTE: platform-backend:latest and /opt/platform are shared by every tenant.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATCH_FILE="${PATCH_FILE:-$SCRIPT_DIR/backend-2026-11-10.patch}"
ENV_SCRIPT="${ENV_SCRIPT:-$SCRIPT_DIR/proton_env_2026-11-10.sh}"
ENV_FILE="${ENV_FILE:-tenants/proton.env}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.tenant.yml}"
ROOT="${DEPLOY_ROOT:-$(cd .. && pwd)}"      # /opt/platform
BK="${BACKUP_DIR:-/backups/predeploy-20261110}"
MIN_DISK_KB="${MIN_DISK_KB:-5242880}"       # 5 GB
HEALTH_TRIES="${HEALTH_TRIES:-36}"          # x HEALTH_SLEEP seconds
HEALTH_SLEEP="${HEALTH_SLEEP:-5}"

REPO="asia-southeast1-docker.pkg.dev/lv-playground-genai/proton-images/proton-chatwoot"
NEW_IMAGE="$REPO:v4.15.1-custom-rc16"
NEW_SHA="db674a88"                          # fork build commit, /app/.git_sha prefix
BACKEND_PRE_TAG="platform-backend:pre-20261110"
OLD_IMAGE="$REPO:v4.15.1-custom-rc15"

# Escalation step table for proton-agent's copied ESCALATION_POLICY_STEPS_JSON.
# The agent's running value is a stale 5-step table; the backend reads this same
# env file, so copying it verbatim would drop the 48h rung (step 6). This is the
# SOP default table including step 6, verified to round-trip through load_steps.
STEPS_JSON='[{"step_no":1,"delay_working_hours":0,"to_roles":["cre","sales_aftersales_mgr"],"cc_roles":["principal","area_regional_mgr","hod"],"label":""},{"step_no":2,"delay_working_hours":2,"to_roles":[],"cc_roles":[],"label":"ACKNOWLEDGEMENT DUE"},{"step_no":3,"delay_working_hours":4,"to_roles":["principal"],"cc_roles":["owner","sales_aftersales_mgr","cre","area_regional_mgr","hod"],"label":"1ST REMINDER"},{"step_no":4,"delay_working_hours":8,"to_roles":["owner"],"cc_roles":["principal","sales_aftersales_mgr","cre","area_regional_mgr","hod"],"label":"2ND REMINDER"},{"step_no":5,"delay_working_hours":8,"to_roles":["principal","owner"],"cc_roles":["sales_aftersales_mgr","cre","area_regional_mgr","hod"],"label":"FINAL ESCALATION - TELEPHONE","channel":"phone"},{"step_no":6,"delay_working_hours":48,"to_roles":["owner"],"cc_roles":["principal","area_regional_mgr","hod"],"label":"48H UNRESOLVED","fires_after_reply":true}]'

DC=(docker compose -p proton -f "$COMPOSE_FILE" --env-file "$ENV_FILE")
RAILS=proton-chatwoot-rails; SIDEKIQ=proton-chatwoot-sidekiq
BACKEND=proton-backend; AGENT=proton-agent

mode=""; accept_baseline=0
for a in "$@"; do
  case "$a" in
    --accept-baseline) accept_baseline=1 ;;
    --dry-run|--apply|--agent|--rollback)
      [ -z "$mode" ] || { echo "only one mode allowed" >&2; exit 2; }
      mode="$a" ;;
    *) echo "usage: $0 [--dry-run|--apply [--accept-baseline]|--agent|--rollback]" >&2; exit 2 ;;
  esac
done
mode="${mode:---dry-run}"

step=""
TMP_CUR=""
cleanup() { [ -z "$TMP_CUR" ] || rm -f "$TMP_CUR" "$TMP_CUR.new"; }
trap cleanup EXIT
say()  { echo "==> $*"; }
die()  { echo "FAILED at: ${step:-start}: $*" >&2
         case "$mode" in --apply|--agent)
           echo "Nothing further was run. Inspect, or run --rollback." >&2 ;; esac
         exit 1; }
run()  { echo "+ $*"; "$@"; }

patched_files() { awk -F'\t' '/^\+\+\+ b\//{sub(/^\+\+\+ b\//,"",$1); print $1}' "$PATCH_FILE" | sort -u; }

# set_kv KEY VALUE: replace (all duplicates) or append, inode-preserving.
set_kv() {
  local k="$1" v="$2" tmp
  tmp="$(mktemp "${ENV_FILE}.XXXXXX")"; TMP_CUR="$tmp"
  cp -p "$ENV_FILE" "$tmp"
  [ -s "$tmp" ] && [ "$(tail -c1 "$tmp" | wc -l)" -eq 0 ] && echo >> "$tmp"
  if grep -q "^${k}=" "$tmp"; then
    K="$k" V="$v" awk 'BEGIN{FS=OFS="="} $1==ENVIRON["K"]{print ENVIRON["K"] "=" ENVIRON["V"]; next} {print}' \
      "$tmp" > "$tmp.new"
    cat "$tmp.new" > "$tmp"; rm -f "$tmp.new"
  else
    printf '%s=%s\n' "$k" "$v" >> "$tmp"
  fi
  cat "$tmp" > "$ENV_FILE"; rm -f "$tmp" "$tmp.new"; TMP_CUR=""
}
get_kv() { sed -n "s/^$1=//p" "$ENV_FILE" | tail -1; }

wait_for() {  # wait_for "description" cmd...
  local d="$1"; shift; local i=0
  until "$@" >/dev/null 2>&1; do
    i=$((i+1)); [ "$i" -le "$HEALTH_TRIES" ] || die "$d did not become healthy"
    sleep "$HEALTH_SLEEP"
  done
  echo "ok: $d"
}

running() { [ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null)" = "true" ]; }
rails_api_ok() { docker exec "$RAILS" wget -qO- http://127.0.0.1:3000/api 2>/dev/null \
                   | grep -q '"data_services"[ ]*:[ ]*"ok"'; }
backend_ok() { docker exec "$BACKEND" python3 -c \
  "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/healthz', timeout=5).status == 200 else 1)"; }
login_ok() { docker exec "$RAILS" wget -qO- http://127.0.0.1:3000/app/login 2>/dev/null; }
openapi_body() { docker exec "$BACKEND" python3 -c \
  "import urllib.request; print(urllib.request.urlopen('http://localhost:8080/openapi.json', timeout=10).read().decode())" 2>/dev/null; }
agent_ok() { docker exec "$AGENT" python3 -c \
  "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/healthz', timeout=5).status == 200 else 1)"; }

preflight() {
  step="preflight"
  [ -f "$COMPOSE_FILE" ] && [ -f "$ENV_FILE" ] || die "run from /opt/platform/deploy ($COMPOSE_FILE / $ENV_FILE missing)"
  [ -f "$PATCH_FILE" ] || die "patch not found: $PATCH_FILE"
  [ -f "$ENV_SCRIPT" ] || die "env script not found: $ENV_SCRIPT"
  [ "$(patched_files | wc -l)" -gt 0 ] || die "patch lists no files (bad path/format): $PATCH_FILE"
  for c in "$BACKEND" "$RAILS" "$SIDEKIQ" "$AGENT"; do
    running "$c" || die "container $c is not running"
  done
  echo "ok: containers up"
  if [ "$mode" = "--apply" ] || [ "$mode" = "--dry-run" ]; then
    grep -qx "CHATWOOT_IMAGE=$OLD_IMAGE" "$ENV_FILE" \
      || die "CHATWOOT_IMAGE in $ENV_FILE is not the expected rc15 tag (${OLD_IMAGE##*:}); refusing"
    echo "ok: CHATWOOT_IMAGE is rc15"
    if backend_ok; then echo "ok: backend /healthz baseline 200"
    elif [ "$accept_baseline" = 1 ]; then echo "WARN: backend /healthz is NOT 200 before deploy; continuing (--accept-baseline)"
    else die "backend /healthz is not 200 BEFORE the deploy (baseline). Fix it, or re-run with --accept-baseline"; fi
  fi
  rails_api_ok || die "rails /api not 200 with data_services ok"
  echo "ok: /api 200, data_services ok"
  local avail; avail="$(df -Pk "$ROOT" | awk 'NR==2{print $4}')"
  [ "${avail:-0}" -gt "$MIN_DISK_KB" ] || die "free disk ${avail} KB <= ${MIN_DISK_KB} KB"
  echo "ok: disk free ${avail} KB"
}

patch_check() {
  step="patch --dry-run"
  say "patch -p1 --dry-run (files: $(patched_files | wc -l))"
  ( cd "$ROOT" && patch -p1 -F0 --dry-run < "$PATCH_FILE" ) || die "patch does not apply cleanly; nothing changed"
}

do_dry_run() {
  say "DRY RUN: no file, env or container is changed"
  preflight
  patch_check
  say "env keys that --apply would write (names only):"
  ENV_FILE="$ENV_FILE" bash "$ENV_SCRIPT" --dry-run
  echo "[dry-run] would set CHATWOOT_IMAGE=${NEW_IMAGE}"
  say "plan for --apply:"
  echo "  1 backup $(patched_files | wc -l) patched files + $ENV_FILE -> $BK/"
  echo "  1b tag platform-backend:latest as $BACKEND_PRE_TAG (rollback image), copy scripts+patch into $BK"
  echo "  2 patch -p1 in $ROOT"
  echo "  3 env script (backend keys) + CHATWOOT_IMAGE=${NEW_IMAGE##*:}"
  echo "  4 ${DC[*]} pull chatwoot-rails chatwoot-sidekiq"
  echo "  5 ${DC[*]} up -d --build backend"
  echo "  6 ${DC[*]} up -d --force-recreate chatwoot-rails chatwoot-sidekiq"
  echo "  7 health: /api, /healthz, no Traceback (3m), rails image tag"
  say "plan for --agent (later): env script --agent, STEPS_JSON with step 6, ${DC[*]} up -d --no-build agent"
  say "dry-run complete"
}

do_apply() {
  preflight
  [ ! -e "$BK/applied" ] || die "already applied (marker $BK/applied). Run --rollback first."
  patch_check

  step="pre-pull image"
  run docker pull "$NEW_IMAGE" >/dev/null || die "cannot pull $NEW_IMAGE (registry auth?); nothing changed"

  step="backup"
  mkdir -p "$BK"
  : > "$BK/new-files.txt"
  local existing=() f
  while IFS= read -r f; do
    if [ -e "$ROOT/$f" ]; then existing+=("$f"); else echo "$f" >> "$BK/new-files.txt"; fi
  done < <(patched_files)
  [ "${#existing[@]}" -gt 0 ] && tar czf "$BK/vm-files.tgz" -C "$ROOT" "${existing[@]}"
  cp -p "$ENV_FILE" "$BK/proton.env"
  echo "ok: backup in $BK (${#existing[@]} files + proton.env)"
  echo "$OLD_IMAGE" > "$BK/preflight-chatwoot-image"
  if [ "$SCRIPT_DIR" != "$BK" ]; then
    cp -p "${BASH_SOURCE[0]}" "$ENV_SCRIPT" "$PATCH_FILE" "$BK/"
  fi
  if docker image inspect "$BACKEND_PRE_TAG" >/dev/null 2>&1; then
    echo "ok: $BACKEND_PRE_TAG already exists (kept)"
  else
    run docker tag platform-backend:latest "$BACKEND_PRE_TAG" || die "cannot tag current backend image"
  fi
  touch "$BK/started"

  step="patch"
  say "patch -p1"
  ( cd "$ROOT" && patch -p1 -F0 --no-backup-if-mismatch < "$PATCH_FILE" ) || die "patch failed mid-way; run --rollback"

  step="env keys"
  say "backend env keys"
  ENV_FILE="$ENV_FILE" bash "$ENV_SCRIPT" | grep -v '^  docker compose' || die "env script failed; run --rollback"
  set_kv CHATWOOT_IMAGE "$NEW_IMAGE"
  echo "CHATWOOT_IMAGE=${NEW_IMAGE##*/}"
  grep -qx "CHATWOOT_IMAGE=$NEW_IMAGE" "$ENV_FILE" || die "CHATWOOT_IMAGE not written; run --rollback"
  touch "$BK/applied"

  step="compose pull"
  run "${DC[@]}" pull chatwoot-rails chatwoot-sidekiq || die "pull failed; run --rollback"
  step="recreate backend"
  run "${DC[@]}" up -d --build backend || die "backend build/up failed; run --rollback"
  backend_health
  step="recreate rails+sidekiq"
  run "${DC[@]}" up -d --force-recreate chatwoot-rails chatwoot-sidekiq || die "rails recreate failed; run --rollback"

  fork_health "$NEW_IMAGE" full
  say "APPLY DONE. Record in the sheet Notes; next (optional): --agent"
}

backend_health() {
  step="backend health"
  wait_for "backend /healthz 200" backend_ok
  local since logs body
  since="$(docker inspect -f '{{.State.StartedAt}}' "$BACKEND")"
  # Capture first, then match in-shell: grep -q in a pipe can SIGPIPE the
  # producer and, under pipefail, mask a match.
  logs="$(docker logs --since "$since" "$BACKEND" 2>&1 || true)"
  case "$logs" in *Traceback*) die "Traceback in $BACKEND logs since start ($since)" ;; esac
  echo "ok: no Traceback in backend logs since container start"
  case "$logs" in *rsa_customer_phone_migration_failed*)
    echo "WARN: rsa_customer_phone_migration_failed in backend logs (RSA phone lookup degrades; fail-open)" ;; esac
  body="$(openapi_body || true)"
  local pth
  for pth in '"/rsa/incidents"' '"/admin/taxonomy' '"/escalation/notify"' '"/admin/escalation'; do
    case "$body" in *"$pth"*) ;; *) die "/openapi.json lacks $pth (new backend code not live?)" ;; esac
  done
  echo "ok: /openapi.json has /rsa, /admin/taxonomy, /escalation routes"
}

fork_health() {
  step="rails health"
  local want="$1" kind="${2:-basic}"
  wait_for "rails /api 200 + data_services ok" rails_api_ok
  running "$SIDEKIQ" || die "$SIDEKIQ is not running"
  local have
  for c in "$RAILS" "$SIDEKIQ"; do
    have="$(docker inspect -f '{{.Config.Image}}' "$c")"
    [ "$have" = "$want" ] || die "$c image is ${have##*:}, expected ${want##*:}"
  done
  echo "ok: rails + sidekiq image tag ${want##*:}"
  if [ "$kind" = full ]; then
    local page sha
    page="$(login_ok)" || die "/app/login not 200"
    echo "ok: /app/login 200"
    case "$page" in *'features:'*) echo "ok: login page has features:" ;;
      *) echo "WARN: 'features:' not found in /app/login (fork bundle check inconclusive)" ;; esac
    sha="$(docker exec "$RAILS" cat /app/.git_sha 2>/dev/null || true)"
    case "$sha" in "$NEW_SHA"*) echo "ok: /app/.git_sha $NEW_SHA" ;;
      *) die "/app/.git_sha is not $NEW_SHA (got: ${sha:-empty})" ;; esac
  fi
}

do_agent() {
  preflight
  [ -e "$BK/applied" ] || die "run --apply first"
  step="agent env"
  say "agent env keys (restores ESCALATION_POLICY_* from the running proton-agent)"
  ENV_FILE="$ENV_FILE" bash "$ENV_SCRIPT" --agent | grep -v '^  docker compose' || die "agent env script failed"
  # Step 6 guard: see STEPS_JSON comment. Names only are printed.
  set_kv ESCALATION_POLICY_STEPS_JSON "$STEPS_JSON"
  echo "ESCALATION_POLICY_STEPS_JSON=<set> (6-step table incl. 48h)"
  # The DB row stays authoritative; this only protects the env fallback.
  set_kv ESCALATION_POLICY_DRY_RUN true
  [ "$(get_kv ESCALATION_POLICY_DRY_RUN)" = true ] || die "ESCALATION_POLICY_DRY_RUN is not true; refusing to recreate agent"
  echo "ESCALATION_POLICY_ENABLED=$(get_kv ESCALATION_POLICY_ENABLED) ESCALATION_POLICY_DRY_RUN=$(get_kv ESCALATION_POLICY_DRY_RUN)"
  step="recreate agent"
  run "${DC[@]}" up -d --no-build agent || die "agent up failed; run --rollback"
  wait_for "agent /healthz 200" agent_ok
  touch "$BK/agent-ran"
  say "AGENT DONE"
}

do_rollback() {
  step="rollback"
  [ -d "$BK" ] || die "no backup dir $BK"
  [ -f "$BK/proton.env" ] || die "no env backup in $BK"
  # Keep the env as it is now: after --agent it holds the only copy of the
  # ESCALATION_POLICY_* values.
  cp -p "$ENV_FILE" "$BK/proton.env.pre-rollback"
  say "restoring files"
  if [ -f "$BK/vm-files.tgz" ]; then tar xzf "$BK/vm-files.tgz" -C "$ROOT"; fi
  if [ -s "$BK/new-files.txt" ]; then
    while IFS= read -r f; do [ -n "$f" ] && rm -f "$ROOT/$f"; done < "$BK/new-files.txt"
  fi
  say "restoring $ENV_FILE (inode-preserving) and CHATWOOT_IMAGE=${OLD_IMAGE##*:}"
  cat "$BK/proton.env" > "$ENV_FILE"
  set_kv CHATWOOT_IMAGE "$OLD_IMAGE"
  if [ -e "$BK/agent-ran" ]; then
    local line k v n=0
    while IFS= read -r line; do
      k="${line%%=*}"; v="${line#*=}"; set_kv "$k" "$v"; n=$((n+1))
    done < <(grep '^ESCALATION_POLICY_' "$BK/proton.env.pre-rollback" || true)
    set_kv ESCALATION_POLICY_DRY_RUN true
    echo "re-applied $n ESCALATION_POLICY_* lines (names only; copy in $BK/proton.env.pre-rollback)"
  fi
  if docker image inspect "$BACKEND_PRE_TAG" >/dev/null 2>&1; then
    run docker tag "$BACKEND_PRE_TAG" platform-backend:latest
    run "${DC[@]}" up -d --no-build --force-recreate backend
  else
    echo "WARN: $BACKEND_PRE_TAG missing; rebuilding backend from restored source"
    run "${DC[@]}" up -d --build backend
  fi
  backend_health
  run "${DC[@]}" up -d --force-recreate chatwoot-rails chatwoot-sidekiq
  if [ -e "$BK/agent-ran" ]; then run "${DC[@]}" up -d --no-build agent; fi
  fork_health "$OLD_IMAGE" basic
  rm -f "$BK/applied" "$BK/agent-ran" "$BK/started"
  say "ROLLBACK DONE (backup kept in $BK)"
}

if [ "${NO_ROOT_CHECK:-}" != 1 ] && [ "$(id -u)" -ne 0 ] && [ "$mode" != "--dry-run" ]; then
  echo "run with sudo" >&2; exit 1
fi

case "$mode" in
  --dry-run)  do_dry_run ;;
  --apply)    do_apply ;;
  --agent)    do_agent ;;
  --rollback) do_rollback ;;
esac
