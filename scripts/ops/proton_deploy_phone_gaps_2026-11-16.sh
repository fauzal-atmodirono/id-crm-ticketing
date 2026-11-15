#!/usr/bin/env bash
# Proton backend: phone call gaps (16 Nov 2026), dev-yuda d315428e..HEAD:
#   - phone_turn_timing / phone_tool_timing logs (no PII: call_sid, durations)
#   - kb_search never leaves the model silent (errors answered; optional cap
#     PHONE_KB_SEARCH_TIMEOUT_SECONDS, default 0 = off)
#   - optional "let me check" filler (PHONE_KB_FILLER_ENABLED, default off)
#   - the PDPA notice is synthesised at startup, so the FIRST call after a
#     restart also gets the streamed notice
#
# Backend only, no env change: flags stay at their defaults (measurement
# only). The new image is the image proton runs TODAY plus five patched files
# taken from it, so nothing else merged into /opt/platform reaches proton.
# The shared `latest` tag is put back afterwards, so aeon360 is untouched.
#
# Copy to the VM /tmp with backend-phone-gaps.patch, then:
#   sudo bash /tmp/proton_deploy_phone_gaps_2026-11-16.sh --dry-run
#   sudo bash /tmp/proton_deploy_phone_gaps_2026-11-16.sh --apply
#   sudo bash /tmp/proton_deploy_phone_gaps_2026-11-16.sh --rollback
# No secret value is ever printed.
set -euo pipefail
MODE="${1:---dry-run}"
D=/opt/platform/deploy
BK=/backups/predeploy-phone-gaps-20261116
SRC=/tmp
P=backend-phone-gaps.patch
NEW=platform-backend:proton-phone-gaps
PRE=platform-backend:pre-phone-gaps
# paths below the chatbot package, as in the patch (a/backend/apps/backend/src/chatbot/<rel>)
FILES="features/chat/phone/bridge.py features/chat/phone/kb_tool.py features/chat/phone/call_start_cache.py features/chat/router.py platform/config.py"
C="docker compose -p proton -f docker-compose.tenant.yml --env-file tenants/proton.env"
cd "$D"

backend_healthy() {
  for i in $(seq 1 60); do
    docker exec proton-backend python -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz').status==200 else 1)" 2>/dev/null && { echo "ok: backend healthz"; return 0; }
    sleep 5
  done
  echo "FAIL: backend not healthy"; docker logs proton-backend --tail 30 2>&1 | cut -c1-200; return 1
}
pkg_dir() { docker exec proton-backend python -c "import chatbot,os;print(os.path.dirname(chatbot.__file__))"; }
applied_in_container() {
  local n; n=$(docker exec proton-backend sh -c "grep -c phone_turn_timing $(pkg_dir)/features/chat/phone/bridge.py" 2>/dev/null || true)
  echo "${n:-0}"
}
recreate_on() {
  local saved; saved=$(docker image inspect -f '{{.Id}}' platform-backend:latest)
  docker tag "$1" platform-backend:latest
  $C up -d --no-build --force-recreate backend 2>&1 | tail -1
  docker tag "$saved" platform-backend:latest
  docker tag "$1" platform-backend:proton-demo
}
# $1 = workdir, $2 = extra patch flags. Copies each file out of the running
# container to $1/src/chatbot/<rel> and applies the patch there.
patch_tree() {
  local pk rel; pk=$(pkg_dir)
  for rel in $FILES; do
    mkdir -p "$1/backend/apps/backend/src/chatbot/$(dirname "$rel")"
    docker cp "proton-backend:$pk/$rel" "$1/backend/apps/backend/src/chatbot/$rel"
  done
  (cd "$1" && patch -p1 -F0 $2 < "$SRC/$P")
}

case "$MODE" in
--dry-run)
  echo "==> DRY RUN (nothing changes)"
  [ -s "$SRC/$P" ] || { echo "missing $SRC/$P"; exit 1; }
  [ "$(applied_in_container)" != "0" ] && echo "NOTE: already in the running container"
  W=$(mktemp -d); trap 'rm -rf "$W"' EXIT
  out=$(patch_tree "$W" --dry-run 2>&1) || { echo "FAIL: patch does not apply to the running files:"; echo "$out" | head -20; exit 1; }
  echo "$out" | sed 's/^/  /'
  echo "ok: patch applies to the running files ($(pkg_dir))"
  echo "running proton-backend image: $(docker inspect -f '{{.Image}}' proton-backend | cut -c8-19)"
  echo "latest (kept as is):          $(docker image inspect -f '{{.Id}}' platform-backend:latest | cut -c8-19)"
  [ -e "$BK/applied" ] && echo "NOTE: already applied (marker $BK/applied)"
  echo "==> dry-run complete";;
--apply)
  [ -e "$BK/applied" ] && { echo "already applied; use --rollback first"; exit 1; }
  [ -s "$SRC/$P" ] || { echo "missing $SRC/$P"; exit 1; }
  mkdir -p "$BK"; cp "$SRC/$P" "$BK/"; cp "$0" "$BK/" 2>/dev/null || true
  PK=$(pkg_dir)
  docker tag "$(docker inspect -f '{{.Image}}' proton-backend)" "$PRE"
  echo "ok: running image tagged $PRE"
  W=$(mktemp -d); trap 'rm -rf "$W"' EXIT
  patch_tree "$W" "" >/dev/null
  { echo "FROM $PRE"; for rel in $FILES; do echo "COPY backend/apps/backend/src/chatbot/$rel $PK/$rel"; done; } > "$W/Dockerfile"
  docker build -q -t "$NEW" "$W" >/dev/null && echo "ok: built $NEW"
  touch "$BK/applied"
  recreate_on "$NEW"; backend_healthy
  echo "in container: $(applied_in_container) (expect 2: the log call and its comment)"
  sleep 15
  echo "notice primed at startup: $(docker logs proton-backend 2>&1 | grep -c phone_announcement_audio_cached || true) (expect 1)"
  echo "tracebacks: backend=$(docker logs proton-backend 2>&1 | grep -c Traceback || true)"
  echo "==> APPLY DONE — make the rehearsal calls, then: docker logs --since 15m proton-backend | grep -E 'phone_(turn|tool|call)_timing'"
  ;;
--rollback)
  docker image inspect "$PRE" >/dev/null 2>&1 || { echo "no $PRE image"; exit 1; }
  recreate_on "$PRE"
  backend_healthy || true
  echo "in container: $(applied_in_container) (expect 0)"
  rm -f "$BK/applied"; echo "==> ROLLBACK DONE";;
*) echo "usage: $0 --dry-run|--apply|--rollback"; exit 2;;
esac
