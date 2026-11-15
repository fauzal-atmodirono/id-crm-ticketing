#!/usr/bin/env bash
# Proton backend hotfix (16 Nov 2026), dev-yuda commit 3a3a5a6f:
#   phone_stream read only the FIRST Media Streams frame, but Twilio sends
#   {"event":"connected"} before "start". Result: the caller lookup done at
#   /voice/phone/incoming was thrown away (test-console "Call as Ahmad" was
#   asked for his number), and once the PDPA notice audio was cached the
#   notice was never played on the stream. The stream now skips pre-start
#   frames (bounded at 5).
#
# Backend only. No env, Chatwoot, agent or /opt/platform source change: the
# new image is the image proton runs TODAY plus one patched file (router.py,
# taken from that image), so nothing merged into /opt/platform since rc22
# (e.g. Logisly UC1 for the default tenant) reaches proton.
# The shared `latest` tag is put back afterwards, so aeon360 is untouched.
#
# Copy to the VM /tmp with backend-phone-start-frame.patch, then:
#   sudo bash /tmp/proton_deploy_phone_start_frame_2026-11-16.sh --dry-run
#   sudo bash /tmp/proton_deploy_phone_start_frame_2026-11-16.sh --apply
#   sudo bash /tmp/proton_deploy_phone_start_frame_2026-11-16.sh --rollback
# No secret value is ever printed.
set -euo pipefail
MODE="${1:---dry-run}"
D=/opt/platform/deploy
BK=/backups/predeploy-phone-start-frame-20261116
SRC=/tmp
P=backend-phone-start-frame.patch
NEW=platform-backend:proton-phone-start-frame
C="docker compose -p proton -f docker-compose.tenant.yml --env-file tenants/proton.env"
cd "$D"

backend_healthy() {
  for i in $(seq 1 60); do
    docker exec proton-backend python -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz').status==200 else 1)" 2>/dev/null && { echo "ok: backend healthz"; return 0; }
    sleep 5
  done
  echo "FAIL: backend not healthy"; docker logs proton-backend --tail 30 2>&1 | cut -c1-200; return 1
}
router_path() {
  docker exec proton-backend python -c "import chatbot.features.chat.router as r;print(r.__file__)"
}
fixed_in_container() {
  local n; n=$(docker exec proton-backend sh -c "grep -c _receive_start_frame $(router_path)" 2>/dev/null || true)
  echo "${n:-0}"
}
# Recreate proton-backend on image $1 without touching what `latest` means
# for anyone else: point latest at $1 just for the recreate, then restore it.
recreate_on() {
  local saved; saved=$(docker image inspect -f '{{.Id}}' platform-backend:latest)
  docker tag "$1" platform-backend:latest
  $C up -d --no-build --force-recreate backend 2>&1 | tail -1
  docker tag "$saved" platform-backend:latest
  docker tag "$1" platform-backend:proton-demo
}
patched_copy() {  # $1 = workdir; leaves $1/router.py patched
  docker cp "proton-backend:$(router_path)" "$1/router.py"
  patch -F0 "$1/router.py" < "$SRC/$P" >/dev/null
}

case "$MODE" in
--dry-run)
  echo "==> DRY RUN (nothing changes)"
  [ -s "$SRC/$P" ] || { echo "missing $SRC/$P"; exit 1; }
  [ "$(fixed_in_container)" != "0" ] && echo "NOTE: fix already in the running container"
  W=$(mktemp -d); trap 'rm -rf "$W"' EXIT
  docker cp "proton-backend:$(router_path)" "$W/router.py"
  out=$(patch -F0 --dry-run "$W/router.py" < "$SRC/$P" 2>&1) || { echo "FAIL: patch does not apply to the running router.py:"; echo "$out" | head; exit 1; }
  echo "ok: patch applies to the running router.py ($(router_path))"
  echo "running proton-backend image: $(docker inspect -f '{{.Image}}' proton-backend | cut -c8-19)"
  echo "latest (kept as is):          $(docker image inspect -f '{{.Id}}' platform-backend:latest | cut -c8-19)"
  [ -e "$BK/applied" ] && echo "NOTE: already applied (marker $BK/applied)"
  echo "==> dry-run complete";;
--apply)
  [ -e "$BK/applied" ] && { echo "already applied; use --rollback first"; exit 1; }
  [ -s "$SRC/$P" ] || { echo "missing $SRC/$P"; exit 1; }
  mkdir -p "$BK"; cp "$SRC/$P" "$BK/"; cp "$0" "$BK/" 2>/dev/null || true
  RP=$(router_path)
  docker tag "$(docker inspect -f '{{.Image}}' proton-backend)" platform-backend:pre-phone-start-frame
  echo "ok: running image tagged platform-backend:pre-phone-start-frame"
  W=$(mktemp -d); trap 'rm -rf "$W"' EXIT
  patched_copy "$W"
  printf 'FROM platform-backend:pre-phone-start-frame\nCOPY router.py %s\n' "$RP" > "$W/Dockerfile"
  docker build -q -t "$NEW" "$W" >/dev/null && echo "ok: built $NEW"
  touch "$BK/applied"
  recreate_on "$NEW"; backend_healthy
  echo "fix in container: $(fixed_in_container) (expect 1)"
  echo "tracebacks: backend=$(docker logs proton-backend 2>&1 | grep -c Traceback || true)"
  echo "==> APPLY DONE — make one test-console call 'Call as Ahmad'; the log should show prefetched=True"
  ;;
--rollback)
  docker image inspect platform-backend:pre-phone-start-frame >/dev/null 2>&1 || { echo "no pre-phone-start-frame image"; exit 1; }
  recreate_on platform-backend:pre-phone-start-frame
  backend_healthy || true
  echo "fix in container: $(fixed_in_container) (expect 0)"
  rm -f "$BK/applied"; echo "==> ROLLBACK DONE";;
*) echo "usage: $0 --dry-run|--apply|--rollback"; exit 2;;
esac
