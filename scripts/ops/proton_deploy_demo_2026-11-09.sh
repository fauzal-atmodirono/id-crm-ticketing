#!/usr/bin/env bash
# Proton demo enhancement deploy (9 Nov 2026): caller-aware AI phone calls,
# demo customer app, pre-filled handoff note + one-click dealer escalation,
# demo console (seed/reset), login fixes (patches 0092/0093), Chatwoot rc18.
#
# Copy to the VM /tmp together with backend-demo.patch, agent-demo.patch and
# test-console-dist.tgz, then from /opt/platform/deploy:
#   sudo bash /tmp/proton_deploy_demo_2026-11-09.sh --dry-run
#   sudo bash /tmp/proton_deploy_demo_2026-11-09.sh --apply
#   sudo bash /tmp/proton_deploy_demo_2026-11-09.sh --rollback
# Values are never printed. Shared image tags stay pinned for aeon360.
set -euo pipefail
MODE="${1:---dry-run}"
D=/opt/platform/deploy
BK=/backups/predeploy-demo-20261109
SRC=/tmp
C="docker compose -p proton -f docker-compose.tenant.yml --env-file tenants/proton.env"
IMG=asia-southeast1-docker.pkg.dev/lv-playground-genai/proton-images/proton-chatwoot:v4.15.1-custom-rc18
cd "$D"

KEYS=(
  "CHATWOOT_IMAGE=$IMG"
  "SESSION_IDLE_TIMEOUT_MINUTES=15"
  "PHONE_CALLER_CONTEXT_ENABLED=true"
  "PHONE_WEB_CALL_AS_ENABLED=true"
  "PHONE_WEB_CALL_AS_NUMBERS=+60000000101,+60000000102,+60000000103,+60000000104,+60000000105"
  "HANDOFF_NOTE_ENABLED=true"
  "HANDOFF_RETURN_AI_REPLY=true"
  "DEMO_CONSOLE_ENABLED=true"
  "DEMO_CUSTOMER_APP_ENABLED=true"
  "METRICS_EXCLUDE_DEMO_SEED=true"
  "CHAT_CALLER_CONTEXT_ENABLED=true"
  "CHAT_CALLER_CONTEXT_INBOX_IDS=1"
  "ESCALATION_ALL_CHANNELS_ENABLED=true"
)

backend_healthy() {
  for i in $(seq 1 60); do
    docker exec proton-backend python -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz').status==200 else 1)" 2>/dev/null && { echo "ok: backend healthz"; return 0; }
    sleep 5
  done
  echo "FAIL: backend not healthy"; docker logs proton-backend --tail 30 2>&1 | cut -c1-200; return 1
}
agent_healthy() {
  for i in $(seq 1 40); do
    docker exec proton-agent python -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status==200 else 1)" 2>/dev/null && { echo "ok: agent healthz"; return 0; }
    sleep 3
  done
  echo "FAIL: agent not healthy"; docker logs proton-agent --tail 30 2>&1 | cut -c1-200; return 1
}
rails_healthy() {
  for i in $(seq 1 60); do
    docker exec proton-chatwoot-rails wget -qO- http://127.0.0.1:3000/api 2>/dev/null | grep -q '"data_services":"ok"' && { echo "ok: rails /api data_services ok"; return 0; }
    sleep 5
  done
  echo "WARN: rails not healthy after 5 min — re-check /api by hand before rolling back"; return 1
}
caddy_reload() {
  docker exec platform-infra-caddy-1 caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1 || return 1
  docker exec platform-infra-caddy-1 caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1
}
set_env() {
  local f=tenants/proton.env t
  t=$(mktemp); cp "$f" "$t"
  for kv in "${KEYS[@]}"; do
    k="${kv%%=*}"; v="${kv#*=}"
    grep -v "^${k}=" "$t" > "$t.n" || true; mv "$t.n" "$t"; printf '%s=%s\n' "$k" "$v" >> "$t"
    echo "set ${k}=<set>"
  done
  cat "$t" > "$f"; rm -f "$t"
}

case "$MODE" in
--dry-run)
  echo "==> DRY RUN (nothing changes)"
  for f in backend-demo.patch agent-demo.patch test-console-dist.tgz; do [ -s "$SRC/$f" ] || { echo "missing $SRC/$f"; exit 1; }; done
  ( cd /opt/platform && patch -p1 -F0 --dry-run < "$SRC/backend-demo.patch" | grep -c "^checking file" | sed 's/^/backend files: /' \
    && patch -p1 -F0 --dry-run < "$SRC/agent-demo.patch" | grep -c "^checking file" | sed 's/^/agent files: /' )
  for kv in "${KEYS[@]}"; do echo "would set ${kv%%=*}=<set>"; done
  docker pull -q "$IMG" >/dev/null && echo "ok: rc18 image pulls"
  [ -e "$BK/applied" ] && echo "NOTE: already applied (marker $BK/applied)"
  echo "==> dry-run complete";;
--apply)
  [ -e "$BK/applied" ] && { echo "already applied; use --rollback first"; exit 1; }
  mkdir -p "$BK"
  cp -p tenants/proton.env "$BK/proton.env"
  cp -p caddy/tenants/proton.caddy "$BK/proton.caddy"
  cp "$SRC/backend-demo.patch" "$SRC/agent-demo.patch" "$SRC/test-console-dist.tgz" "$0" "$BK/" 2>/dev/null || true
  ( cd /opt/platform && grep -hE '^\+\+\+ b/' "$SRC/backend-demo.patch" "$SRC/agent-demo.patch" | sed 's#^+++ b/##' | sort -u > "$BK/files.txt" \
    && tar -czf "$BK/files.tgz" $(while read -r f; do [ -e "$f" ] && echo "$f"; done < "$BK/files.txt") )
  [ -d caddy/tenants/apps/test-console ] && tar -C caddy/tenants/apps -czf "$BK/test-console-old.tgz" test-console
  docker tag "$(docker inspect -f '{{.Image}}' proton-backend)" platform-backend:pre-demo
  docker tag "$(docker inspect -f '{{.Image}}' proton-agent)" platform-agent:pre-demo
  docker inspect -f '{{.Config.Image}}' proton-chatwoot-rails > "$BK/rails-image"
  echo "ok: backup in $BK"
  ( cd /opt/platform && patch -p1 -F0 < "$SRC/backend-demo.patch" >/dev/null && patch -p1 -F0 < "$SRC/agent-demo.patch" >/dev/null )
  touch "$BK/applied"; echo "ok: patches applied"
  set_env
  # backend + agent: build from patched source, then keep `latest` pinned for aeon360
  $C up -d --build backend 2>&1 | tail -1; backend_healthy
  docker tag platform-backend:latest platform-backend:proton-demo
  docker tag platform-backend:pre-20261110 platform-backend:latest
  $C up -d --build agent 2>&1 | tail -1; agent_healthy
  docker tag platform-agent:latest platform-agent:proton-demo
  docker tag platform-agent:pre-wave2 platform-agent:latest
  echo "tracebacks: backend=$(docker logs proton-backend 2>&1 | grep -c Traceback || true) agent=$(docker logs proton-agent 2>&1 | grep -c Traceback || true)"
  # Chatwoot rc18
  $C pull -q chatwoot-rails chatwoot-sidekiq 2>&1 | tail -1
  $C up -d --force-recreate chatwoot-rails chatwoot-sidekiq 2>&1 | tail -1; rails_healthy || true
  echo "rails image: $(docker inspect -f '{{.Config.Image}}' proton-chatwoot-rails | sed 's#.*:##')  git_sha: $(docker exec proton-chatwoot-rails cat /app/.git_sha 2>/dev/null)"
  echo "idle minutes on the login page: $(docker exec proton-chatwoot-rails sh -c 'wget -qO- http://127.0.0.1:3000/app/login' | grep -o '__PROTON_IDLE_TIMEOUT_MINUTES__ = [0-9]*' | head -1)"
  # test console + Caddy: hide /apps/test-console/api/agent/* (agent-only endpoint)
  rm -rf caddy/tenants/apps/test-console.new && mkdir -p caddy/tenants/apps/test-console.new
  tar -xzf "$SRC/test-console-dist.tgz" -C caddy/tenants/apps/test-console.new 2>/dev/null
  rm -rf caddy/tenants/apps/test-console && mv caddy/tenants/apps/test-console.new caddy/tenants/apps/test-console
  if ! grep -q "/apps/test-console/api/agent/" caddy/tenants/proton.caddy; then
    python3 - caddy/tenants/proton.caddy <<'PY'
import sys
f = sys.argv[1]; t = open(f).read()
anchor = "\thandle_path /apps/test-console/api/* {\n"
block = "\t# agent-only endpoint: never via the test console proxy\n\thandle /apps/test-console/api/agent/* {\n\t\trespond 404\n\t}\n"
assert t.count(anchor) >= 1, "test-console api block not found"
open(f, "w").write(t.replace(anchor, block + anchor))
PY
  fi
  if caddy_reload; then echo "ok: caddy reloaded"; else cp -p "$BK/proton.caddy" caddy/tenants/proton.caddy; caddy_reload || true; echo "WARN: caddy validate failed, vhost restored"; fi
  echo "demo seed status (read-only):"
  $C exec -T backend python -m chatbot.features.demo.seed --status 2>&1 | tail -15 || true
  echo "==> APPLY DONE";;
--rollback)
  [ -d "$BK" ] || { echo "no backup at $BK"; exit 1; }
  cp -p tenants/proton.env "$BK/proton.env.pre-rollback"
  ( cd /opt/platform && [ -f "$BK/files.tgz" ] && tar -xzf "$BK/files.tgz" )
  ( cd /opt/platform && while read -r f; do tar -tzf "$BK/files.tgz" "$f" >/dev/null 2>&1 || rm -f "$f"; done < "$BK/files.txt" )
  cat "$BK/proton.env" > tenants/proton.env
  cp -p "$BK/proton.caddy" caddy/tenants/proton.caddy; caddy_reload || true
  [ -f "$BK/test-console-old.tgz" ] && { rm -rf caddy/tenants/apps/test-console; tar -xzf "$BK/test-console-old.tgz" -C caddy/tenants/apps; }
  docker tag platform-backend:pre-demo platform-backend:latest; $C up -d --no-build backend 2>&1 | tail -1
  docker tag platform-agent:pre-demo platform-agent:latest; $C up -d --no-build agent 2>&1 | tail -1
  docker tag platform-backend:pre-20261110 platform-backend:latest; docker tag platform-agent:pre-wave2 platform-agent:latest
  $C up -d --force-recreate chatwoot-rails chatwoot-sidekiq 2>&1 | tail -1
  backend_healthy || true; agent_healthy || true; rails_healthy || true
  rm -f "$BK/applied"; echo "==> ROLLBACK DONE (rails back on $(cat "$BK/rails-image" | sed 's#.*:##'))";;
*) echo "usage: $0 --dry-run|--apply|--rollback"; exit 2;;
esac
