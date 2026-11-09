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
# Secrets: this script never prints a value from proton.env. It prints key
# NAMES, the Chatwoot image tag, and (--agent) the two non-secret booleans
# ESCALATION_POLICY_ENABLED / ESCALATION_POLICY_DRY_RUN. All env writes are inode-preserving
# (cat tmp > file), never mv/sed -i (bind-mount trap).
#
# Rollback targets: files from the tar backup, proton.env from its backup, and
# CHATWOOT_IMAGE forced back to the rc15 tag. Files the patch created are deleted.
# The backend returns to the image tagged platform-backend:pre-20261110 (the
# image proton-backend was RUNNING, tagged before the build; rollback never
# rebuilds, because platform-backend:latest is shared by every tenant). Rollback
# runs every restore step first, driven by the markers in $BK (started,
# applied, backend-touched, rails-touched, agent-ran), and only then runs the
# health checks as warnings. If --agent ran, the ESCALATION_POLICY_* lines
# are re-applied after the env restore, because the restore would otherwise
# wipe the only copy of them once proton-agent is recreated. We re-apply rather
# than skip recreating the agent: the lifecycle keys it also received must be
# rolled back, and that needs a recreate. The ACTIVE_RECORD_ENCRYPTION_* lines
# (added later by proton_mfa_keys.sh, FIX-25) are always re-applied the same
# way: restoring the pre-Wave-1 env would otherwise delete the MFA keys, which
# are permanent once any user enrols or a channel credential is saved.
#
# Settings check: after the backend recreate (and after --agent) every env key
# NAME the env script wrote is checked against the running container's
# Settings.model_fields; a key with no field does nothing, so the names are
# printed as a WARN and the operator decides. Never aborts, never prints values.
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
# 60 x 5 s = 5 min: Rails has taken up to ~3 min to boot on the VM; 36 tries
# (3 min) left no margin and a slow boot would read as a failed deploy.
HEALTH_TRIES="${HEALTH_TRIES:-60}"          # x HEALTH_SLEEP seconds
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
OPENAPI_PATHS=('"/rsa/incidents"' '"/admin/taxonomy' '"/escalation/notify"' '"/admin/escalation')
openapi_missing() {  # prints the missing paths, space-led, or nothing
  local body pth out=""; body="$(openapi_body || true)"
  for pth in "${OPENAPI_PATHS[@]}"; do case "$body" in *"$pth"*) ;; *) out="$out $pth" ;; esac; done
  printf '%s' "$out"
}
openapi_body() { docker exec "$BACKEND" python3 -c \
  "import urllib.request; print(urllib.request.urlopen('http://localhost:8080/openapi.json', timeout=10).read().decode())" 2>/dev/null; }
# settings_check CONTAINER MODULE ENV_SCRIPT_ARGS...: WARN (never fail) when an
# env key name the env script writes is not a field of MODULE.Settings inside
# CONTAINER. Only names cross into the container; values are never read.
settings_check() {
  local c="$1" mod="$2" what="$3"; shift 3
  local names missing
  names="$(ENV_FILE="$ENV_FILE" bash "$ENV_SCRIPT" --names "$@" | tr '\n' ' ')"
  if ! missing="$(docker exec "$c" python3 -c \
      "import sys; from $mod import Settings; f = set(Settings.model_fields); print(' '.join(n for n in sys.argv[1:] if n.lower() not in f))" \
      $names 2>/dev/null)"; then
    echo "WARN: could not read $c Settings fields; $what env keys not checked"
    return 0
  fi
  missing="$(echo "$missing" | tr -s ' ' | sed 's/^ //; s/ $//')"
  if [ -n "$missing" ]; then
    echo "WARN: $c Settings has no field for: $missing"
    echo "      (these keys do nothing in the running image; decide whether to continue)"
  else
    echo "ok: every $what env key is $( [ "$what" = agent ] && echo an || echo a ) $what Settings field"
  fi
}
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
    local missing; missing="$(openapi_missing)"
    if [ -z "$missing" ]; then echo "ok: /openapi.json baseline has all 4 route groups"
    elif [ "$accept_baseline" = 1 ]; then echo "WARN: /openapi.json lacks before deploy:$missing (continuing, --accept-baseline; the post-apply check still requires them)"
    else die "/openapi.json lacks BEFORE the deploy:$missing. If the patch adds them this is expected: re-run with --accept-baseline"; fi
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
  say "plan for --apply (stops on first failure):"
  echo "  1 backup $(patched_files | wc -l) patched files + $ENV_FILE -> $BK/, copy scripts+patch there"
  echo "  2 tag the image proton-backend is running as $BACKEND_PRE_TAG (rollback image)"
  echo "  3 patch -p1 -F0 in $ROOT"
  echo "  4 env script (backend keys) + CHATWOOT_IMAGE=${NEW_IMAGE##*:}"
  echo "  5 ${DC[*]} pull chatwoot-rails chatwoot-sidekiq"
  echo "  6 ${DC[*]} up -d --build backend, then backend health (/healthz, no Traceback, /openapi.json routes)"
  echo "  7 ${DC[*]} up -d --force-recreate chatwoot-rails chatwoot-sidekiq"
  echo "  8 fork health: /api, sidekiq, image tags, /app/login, /app/.git_sha"
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
    run docker tag "$(docker inspect -f '{{.Image}}' "$BACKEND")" "$BACKEND_PRE_TAG" || die "cannot tag the running backend image"
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
  touch "$BK/backend-touched"
  run "${DC[@]}" up -d --build backend || die "backend build/up failed; run --rollback"
  backend_health
  settings_check "$BACKEND" chatbot.platform.config backend
  step="recreate rails+sidekiq"
  touch "$BK/rails-touched"
  run "${DC[@]}" up -d --force-recreate chatwoot-rails chatwoot-sidekiq || die "rails recreate failed; run --rollback"

  fork_health "$NEW_IMAGE" full
  say "APPLY DONE. Record in the sheet Notes; next (optional): --agent"
}

backend_health() {
  step="backend health"
  local kind="${1:-full}"
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
  if [ "$kind" = full ]; then
    body="$(openapi_missing)"
    [ -z "$body" ] || die "/openapi.json lacks$body (new backend code not live?)"
    echo "ok: /openapi.json has /rsa, /admin/taxonomy, /escalation routes"
  fi
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
  touch "$BK/agent-ran"
  run "${DC[@]}" up -d --no-build agent || die "agent up failed; run --rollback"
  wait_for "agent /healthz 200" agent_ok
  # The agent image is not rebuilt: a lifecycle key newer than the running
  # image has no field yet. Warn only.
  settings_check "$AGENT" app.config agent --agent
  say "AGENT DONE"
}

do_rollback() {
  step="rollback"
  [ -d "$BK" ] || die "no backup dir $BK"
  [ -f "$BK/proton.env" ] || die "no env backup in $BK"
  local problems=()
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
  # MFA keys (proton_mfa_keys.sh, after Wave 1) are not in the pre-Wave-1
  # backup; restoring it must not delete them. Re-applied whether or not
  # --agent ran.
  local mline mk mv m=0
  while IFS= read -r mline; do
    mk="${mline%%=*}"; mv="${mline#*=}"; set_kv "$mk" "$mv"; m=$((m+1))
  done < <(grep '^ACTIVE_RECORD_ENCRYPTION_' "$BK/proton.env.pre-rollback" || true)
  [ "$m" -eq 0 ] || echo "re-applied $m ACTIVE_RECORD_ENCRYPTION_* lines (MFA keys kept; names only)"
  # Every restore step runs before any health check; one failure never
  # stops the rest. Steps the apply never reached are skipped.
  if [ -e "$BK/backend-touched" ]; then
    if docker image inspect "$BACKEND_PRE_TAG" >/dev/null 2>&1; then
      run docker tag "$BACKEND_PRE_TAG" platform-backend:latest || problems+=("retag backend")
      run "${DC[@]}" up -d --no-build --force-recreate backend || problems+=("recreate backend")
    else
      problems+=("backend NOT restored: $BACKEND_PRE_TAG is missing. Do not rebuild (platform-backend:latest is shared). Re-create the tag from a known-good image id, then: docker tag <id> platform-backend:latest && ${DC[*]} up -d --no-build --force-recreate backend")
    fi
  else
    echo "skip: backend was never rebuilt"
  fi
  if [ -e "$BK/rails-touched" ]; then
    run "${DC[@]}" up -d --force-recreate chatwoot-rails chatwoot-sidekiq || problems+=("recreate rails/sidekiq")
  else
    echo "skip: rails/sidekiq were never recreated"
  fi
  if [ -e "$BK/agent-ran" ]; then
    run "${DC[@]}" up -d --no-build agent || problems+=("recreate agent")
  fi
  if [ "${#problems[@]}" -eq 0 ]; then
    rm -f "$BK/applied" "$BK/started" "$BK/backend-touched" "$BK/rails-touched" "$BK/agent-ran"
  fi

  say "health checks (warnings only; restores are already done)"
  local warns=0
  if [ -e "$BK/backend-touched" ] || [ -e "$BK/applied" ] || [ "${#problems[@]}" -eq 0 ]; then
    if ( backend_health basic ); then :; else warns=$((warns+1)); echo "WARN: backend health failed after rollback"; fi
    if ( fork_health "$OLD_IMAGE" basic ); then :; else warns=$((warns+1)); echo "WARN: rails/sidekiq health failed after rollback"; fi
  fi
  echo
  if [ "${#problems[@]}" -gt 0 ]; then
    say "ROLLBACK INCOMPLETE (markers kept). Do by hand:"
    printf '  - %s\n' "${problems[@]}"; exit 1
  fi
  if [ "$warns" -gt 0 ]; then
    say "ROLLBACK RESTORED everything, but $warns health check(s) failed. Next: docker ps; docker logs proton-backend --tail 100; give containers a minute and re-check /api and /healthz"
  else
    say "ROLLBACK DONE, all health checks ok (backup kept in $BK)"
  fi
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
