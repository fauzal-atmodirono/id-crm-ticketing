#!/usr/bin/env bash
# Proton rc21 deploy (10 Nov 2026), fixes found by the Playwright run:
#   - RSA-001: an RSA incident needs a vehicle model (API 422 names the field;
#     form marks it required; editing an old record without a model now asks
#     for one before saving)                               (patch 0097)
#   - CASE-002: Cases page "Sort by" (Newest first default, Oldest first,
#     Recently updated) and a "Created" column            (patch 0098)
#   - KNOW-006: an AI tool with no parameters saves (empty schema → {})
#   - C360-002: Customer 360 vehicle-number search returns the customer, via
#     the contact's vehicle_no, a case's vehicle_plate, or the phone on an RSA
#     incident with exactly that plate — always within the caller's data scope
#   - creates the custom attribute definitions those lookups need if missing:
#     conversation vehicle_plate, contact vehicle_no (text)
#   - Chatwoot image rc21
#
# Copy to the VM /tmp with backend-rc21.patch, then:
#   sudo bash /tmp/proton_deploy_rc21_2026-11-10.sh --dry-run
#   sudo bash /tmp/proton_deploy_rc21_2026-11-10.sh --apply
#   sudo bash /tmp/proton_deploy_rc21_2026-11-10.sh --rollback   (back to rc20)
# No value is ever printed. Shared `latest` tags stay pinned for aeon360.
set -euo pipefail
MODE="${1:---dry-run}"
D=/opt/platform/deploy
BK=/backups/predeploy-rc21-20261110
SRC=/tmp
C="docker compose -p proton -f docker-compose.tenant.yml --env-file tenants/proton.env"
IMG=asia-southeast1-docker.pkg.dev/lv-playground-genai/proton-images/proton-chatwoot:v4.15.1-custom-rc21
PATCHES=(backend-rc21.patch)
cd "$D"

backend_healthy() {
  for i in $(seq 1 60); do
    docker exec proton-backend python -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz').status==200 else 1)" 2>/dev/null && { echo "ok: backend healthz"; return 0; }
    sleep 5
  done
  echo "FAIL: backend not healthy"; docker logs proton-backend --tail 30 2>&1 | cut -c1-200; return 1
}
rails_healthy() {
  for i in $(seq 1 60); do
    docker exec proton-chatwoot-rails wget -qO- http://127.0.0.1:3000/api 2>/dev/null | grep -q '"data_services":"ok"' && { echo "ok: rails /api data_services ok"; return 0; }
    sleep 5
  done
  echo "WARN: rails not healthy after 5 min — re-check /api by hand before rolling back"; return 1
}
set_image() {
  local f=tenants/proton.env t
  t=$(mktemp); grep -v '^CHATWOOT_IMAGE=' "$f" > "$t" || true; printf 'CHATWOOT_IMAGE=%s\n' "$IMG" >> "$t"
  cat "$t" > "$f"; rm -f "$t"; echo "set CHATWOOT_IMAGE=<set>"   # keep the inode (bind mounts)
}
# Prints "created <model>:<key>" or "exists <model>:<key>" per definition.
attr_defs() {
  docker exec proton-chatwoot-rails bundle exec rails runner '
    acct = Account.find(1)
    [["conversation_attribute", "vehicle_plate", "Vehicle plate"], ["contact_attribute", "vehicle_no", "Vehicle no."]].each do |model, key, name|
      d = CustomAttributeDefinition.find_by(account_id: acct.id, attribute_model: model, attribute_key: key)
      if d
        puts "exists #{model}:#{key}"
      elsif ARGV[0] == "apply"
        CustomAttributeDefinition.create!(account_id: acct.id, attribute_model: model, attribute_key: key,
                                          attribute_display_name: name, attribute_display_type: "text")
        puts "created #{model}:#{key}"
      else
        puts "missing #{model}:#{key}"
      end
    end' "$1" 2>/dev/null | grep -E '^(exists|created|missing) '
}

case "$MODE" in
--dry-run)
  echo "==> DRY RUN (nothing changes)"
  for f in "${PATCHES[@]}"; do [ -s "$SRC/$f" ] || { echo "missing $SRC/$f"; exit 1; }; done
  ( cd /opt/platform && for p in "${PATCHES[@]}"; do
      out=$(patch -p1 -F0 --dry-run < "$SRC/$p" 2>&1) || { echo "FAIL: $p does not apply:"; echo "$out" | grep -v '^checking file' | head; exit 1; }
      echo "$p: $(echo "$out" | grep -c '^checking file') files apply cleanly"
    done )
  echo "would set CHATWOOT_IMAGE=<set>"
  attr_defs check
  docker pull -q "$IMG" >/dev/null && echo "ok: rc21 image pulls"
  [ -e "$BK/applied" ] && echo "NOTE: already applied (marker $BK/applied)"
  echo "==> dry-run complete";;
--apply)
  [ -e "$BK/applied" ] && { echo "already applied; use --rollback first"; exit 1; }
  for f in "${PATCHES[@]}"; do [ -s "$SRC/$f" ] || { echo "missing $SRC/$f"; exit 1; }; done
  mkdir -p "$BK"
  cp -p tenants/proton.env "$BK/proton.env"
  for f in "${PATCHES[@]}"; do cp "$SRC/$f" "$BK/"; done; cp "$0" "$BK/" 2>/dev/null || true
  ( cd /opt/platform && for p in "${PATCHES[@]}"; do grep -hE '^\+\+\+ b/' "$SRC/$p"; done | sed 's#^+++ b/##' | sort -u > "$BK/files.txt" \
    && tar -czf "$BK/files.tgz" $(while read -r f; do [ -e "$f" ] && echo "$f"; done < "$BK/files.txt") )
  docker tag "$(docker inspect -f '{{.Image}}' proton-backend)" platform-backend:pre-rc21
  docker inspect -f '{{.Config.Image}}' proton-chatwoot-rails > "$BK/rails-image"
  echo "ok: backup in $BK"
  ( cd /opt/platform && for p in "${PATCHES[@]}"; do patch -p1 -F0 < "$SRC/$p" >/dev/null; done )
  touch "$BK/applied"; echo "ok: patches applied"
  set_image
  # backend: build from patched source, then keep `latest` pinned for aeon360
  $C up -d --build backend 2>&1 | tail -1; backend_healthy
  docker tag platform-backend:latest platform-backend:proton-demo
  docker tag platform-backend:pre-20261110 platform-backend:latest
  echo "tracebacks: backend=$(docker logs proton-backend 2>&1 | grep -c Traceback || true)"
  # Chatwoot rc21
  $C pull -q chatwoot-rails chatwoot-sidekiq 2>&1 | tail -1
  $C up -d --force-recreate chatwoot-rails chatwoot-sidekiq 2>&1 | tail -1; rails_healthy || true
  echo "rails image: $(docker inspect -f '{{.Config.Image}}' proton-chatwoot-rails | sed 's#.*:##')  git_sha: $(docker exec proton-chatwoot-rails cat /app/.git_sha 2>/dev/null)"
  # custom attribute definitions (only the ones this run creates are recorded for rollback)
  attr_defs apply | tee "$BK/attr-defs.txt"
  echo "==> APPLY DONE — check: RSA form shows Vehicle model *; Cases page has 'Sort by' and 'Created'"
  ;;
--rollback)
  [ -d "$BK" ] || { echo "no backup at $BK"; exit 1; }
  cp -p tenants/proton.env "$BK/proton.env.pre-rollback"
  ( cd /opt/platform && [ -f "$BK/files.tgz" ] && tar -xzf "$BK/files.tgz" )
  ( cd /opt/platform && while read -r f; do tar -tzf "$BK/files.tgz" "$f" >/dev/null 2>&1 || rm -f "$f"; done < "$BK/files.txt" )
  cat "$BK/proton.env" > tenants/proton.env
  docker tag platform-backend:pre-rc21 platform-backend:latest; $C up -d --no-build --force-recreate backend 2>&1 | tail -1
  docker tag platform-backend:pre-rc21 platform-backend:proton-demo
  docker tag platform-backend:pre-20261110 platform-backend:latest
  $C up -d --force-recreate chatwoot-rails chatwoot-sidekiq 2>&1 | tail -1
  backend_healthy || true; rails_healthy || true
  if grep -q '^created ' "$BK/attr-defs.txt" 2>/dev/null; then
    echo "NOTE: this deploy created: $(grep '^created ' "$BK/attr-defs.txt" | cut -d' ' -f2 | tr '\n' ' ')— left in place (harmless; values stored on records stay readable). Remove in CRM > Settings > Custom attributes if wanted."
  fi
  rm -f "$BK/applied"; echo "==> ROLLBACK DONE (rails back on $(sed 's#.*:##' "$BK/rails-image"))";;
*) echo "usage: $0 --dry-run|--apply|--rollback"; exit 2;;
esac
