#!/usr/bin/env bash
# Proton rc24 deploy (16 Nov 2026), Chatwoot only. rc24 = rc23 + the 0101
# review follow-ups (2eb69957): edits keep the stored key, Save commits a typed
# chip, Add buttons follow /authz/me/scope. --rollback returns to rc23.
#   - Escalation Routing refresh (patch 0101): tabs, side drawers, Needs
#     attention, ladder as an Off / Dry run / Live timeline. Same API calls.
#   - Sidebar availability list scrolls (patch 0100).
# No backend, agent or env change other than CHATWOOT_IMAGE.
#
# Copy to the VM /tmp, then:
#   sudo bash /tmp/proton_deploy_rc24_2026-11-16.sh --dry-run
#   sudo bash /tmp/proton_deploy_rc24_2026-11-16.sh --apply
#   sudo bash /tmp/proton_deploy_rc24_2026-11-16.sh --rollback   (back to the image in force before)
# No value is ever printed. aeon360 and default are not touched.
set -euo pipefail
MODE="${1:---dry-run}"
D=/opt/platform/deploy
BK=/backups/predeploy-rc24-20261116
C="docker compose -p proton -f docker-compose.tenant.yml --env-file tenants/proton.env"
IMG=asia-southeast1-docker.pkg.dev/lv-playground-genai/proton-images/proton-chatwoot:v4.15.1-custom-rc24
cd "$D"

rails_healthy() {
  for i in $(seq 1 60); do
    docker exec proton-chatwoot-rails wget -qO- http://127.0.0.1:3000/api 2>/dev/null | grep -q '"data_services":"ok"' && { echo "ok: rails /api data_services ok"; return 0; }
    sleep 5
  done
  echo "WARN: rails not healthy after 5 min"; return 1
}
set_image() {  # $1 = image; keeps the file's inode (bind mounts)
  local f=tenants/proton.env t
  t=$(mktemp); grep -v '^CHATWOOT_IMAGE=' "$f" > "$t" || true; printf 'CHATWOOT_IMAGE=%s\n' "$1" >> "$t"
  cat "$t" > "$f"; rm -f "$t"; echo "set CHATWOOT_IMAGE=<set>"
}

case "$MODE" in
--dry-run)
  echo "==> DRY RUN (nothing changes)"
  echo "rails image now: $(docker inspect -f '{{.Config.Image}}' proton-chatwoot-rails | sed 's#.*:##')"
  docker pull -q "$IMG" >/dev/null && echo "ok: rc24 image pulls"
  [ -e "$BK/applied" ] && echo "NOTE: already applied (marker $BK/applied)"
  echo "==> dry-run complete";;
--apply)
  [ -e "$BK/applied" ] && { echo "already applied; use --rollback first"; exit 1; }
  docker pull -q "$IMG" >/dev/null && echo "ok: rc24 image pulls"
  mkdir -p "$BK"; cp -p tenants/proton.env "$BK/proton.env"; cp "$0" "$BK/" 2>/dev/null || true
  docker inspect -f '{{.Config.Image}}' proton-chatwoot-rails > "$BK/rails-image"
  echo "ok: backup in $BK (was $(sed 's#.*:##' "$BK/rails-image"))"
  set_image "$IMG"; touch "$BK/applied"
  $C up -d --no-build --force-recreate chatwoot-rails chatwoot-sidekiq 2>&1 | tail -2
  rails_healthy || true
  echo "rails image: $(docker inspect -f '{{.Config.Image}}' proton-chatwoot-rails | sed 's#.*:##')  git_sha: $(docker exec proton-chatwoot-rails cat /app/.git_sha 2>/dev/null)"
  echo "==> APPLY DONE";;
--rollback)
  [ -f "$BK/rails-image" ] || { echo "no backup at $BK"; exit 1; }
  set_image "$(cat "$BK/rails-image")"
  $C up -d --no-build --force-recreate chatwoot-rails chatwoot-sidekiq 2>&1 | tail -2
  rails_healthy || true
  rm -f "$BK/applied"; echo "==> ROLLBACK DONE (rails on $(sed 's#.*:##' "$BK/rails-image"))";;
*) echo "usage: $0 --dry-run|--apply|--rollback"; exit 2;;
esac
