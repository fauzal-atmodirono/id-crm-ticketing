#!/usr/bin/env bash
# Proton rc20 deploy (10 Nov 2026):
#   - personal service on every channel (R47-R53): a recognised customer is
#     greeted by first name + car on phone, WhatsApp, customer app and the
#     test console Chat/Voice tabs (persona picker); unknown = new customer;
#     server-owned sessions (whatsapp-/crm-/...) need the backend key on
#     /chat/turn, /voice/turn, /chat/stream, /chat/csat, /chat/nps
#   - call player inside each "[Call archived…]" note, no gs:// text shown,
#     list preview "📞 Call recording" (patches 0094-0096); superadmins get
#     the full permission list in /authz/permissions (R55)
#   - phone handoff stage 1 skips a Busy/Offline assignee (R54/R56) and an
#     immediate fan-out no longer rings everyone twice
#   - Chatwoot image rc20
#
# Copy to the VM /tmp with backend-rc20.patch, agent-rc20.patch and
# test-console-dist.tgz, then:
#   sudo bash /tmp/proton_deploy_rc20_2026-11-10.sh --dry-run
#   sudo bash /tmp/proton_deploy_rc20_2026-11-10.sh --apply
#   sudo bash /tmp/proton_deploy_rc20_2026-11-10.sh --rollback   (back to rc19)
# No value is ever printed. Shared `latest` tags stay pinned for aeon360.
set -euo pipefail
MODE="${1:---dry-run}"
D=/opt/platform/deploy
BK=/backups/predeploy-rc20-20261110
SRC=/tmp
C="docker compose -p proton -f docker-compose.tenant.yml --env-file tenants/proton.env"
IMG=asia-southeast1-docker.pkg.dev/lv-playground-genai/proton-images/proton-chatwoot:v4.15.1-custom-rc20
TC=caddy/tenants/apps/test-console
cd "$D"

KEYS=(
  "CHATWOOT_IMAGE=$IMG"
  "CHAT_TURN_CALLER_CONTEXT_ENABLED=true"
  "CHAT_CALLER_CONTEXT_INBOX_IDS=1,3"
)
PATCHES=(backend-rc20.patch agent-rc20.patch)

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
set_env() {
  local f=tenants/proton.env t
  t=$(mktemp); cp "$f" "$t"
  for kv in "${KEYS[@]}"; do
    k="${kv%%=*}"; v="${kv#*=}"
    grep -v "^${k}=" "$t" > "$t.n" || true; mv "$t.n" "$t"; printf '%s=%s\n' "$k" "$v" >> "$t"
    echo "set ${k}=<set>"
  done
  cat "$t" > "$f"; rm -f "$t"   # keep the inode (bind mounts)
}
http_code() {  # $1 = python expression building a Request; prints the HTTP status
  docker exec proton-backend python -c "import urllib.request,urllib.error,json
req=$1
try: print(urllib.request.urlopen(req, timeout=10).status)
except urllib.error.HTTPError as e: print(e.code)" 2>/dev/null || echo "?"
}
checks() {
  echo "recording route without a session: HTTP $(http_code "urllib.request.Request('http://127.0.0.1:8080/calls/1/recording')") (401 expected)"
  echo "note recording route, bad message_id: HTTP $(http_code "urllib.request.Request('http://127.0.0.1:8080/calls/1/recording?message_id=x')") (401 or 422 expected)"
  echo "/chat/turn with a whatsapp- session and no key: HTTP $(http_code "urllib.request.Request('http://127.0.0.1:8080/chat/turn', data=json.dumps({'session_id':'whatsapp-+60000000000','text':'hi'}).encode(), headers={'content-type':'application/json'})") (403 expected)"
}

case "$MODE" in
--dry-run)
  echo "==> DRY RUN (nothing changes)"
  for f in "${PATCHES[@]}" test-console-dist.tgz; do [ -s "$SRC/$f" ] || { echo "missing $SRC/$f"; exit 1; }; done
  ( cd /opt/platform && for p in "${PATCHES[@]}"; do
      out=$(patch -p1 -F0 --dry-run < "$SRC/$p" 2>&1) || { echo "FAIL: $p does not apply:"; echo "$out" | grep -v '^checking file' | head; exit 1; }
      echo "$p: $(echo "$out" | grep -c '^checking file') files apply cleanly"
    done )
  for kv in "${KEYS[@]}"; do echo "would set ${kv%%=*}=<set>"; done
  grep -q '^PROTON_BACKEND_KEY=.' tenants/proton.env && echo "ok: PROTON_BACKEND_KEY set (agent crm- turns pass the new guard)" || { echo "FAIL: PROTON_BACKEND_KEY missing — WhatsApp AI would get 403"; exit 1; }
  docker pull -q "$IMG" >/dev/null && echo "ok: rc20 image pulls"
  tar -tzf "$SRC/test-console-dist.tgz" | grep -q 'index.html' && echo "ok: test console bundle has index.html"
  [ -e "$BK/applied" ] && echo "NOTE: already applied (marker $BK/applied)"
  echo "==> dry-run complete";;
--apply)
  [ -e "$BK/applied" ] && { echo "already applied; use --rollback first"; exit 1; }
  for f in "${PATCHES[@]}" test-console-dist.tgz; do [ -s "$SRC/$f" ] || { echo "missing $SRC/$f"; exit 1; }; done
  grep -q '^PROTON_BACKEND_KEY=.' tenants/proton.env || { echo "PROTON_BACKEND_KEY missing — refusing (WhatsApp AI would break)"; exit 1; }
  mkdir -p "$BK"
  cp -p tenants/proton.env "$BK/proton.env"
  for f in "${PATCHES[@]}" test-console-dist.tgz; do cp "$SRC/$f" "$BK/"; done; cp "$0" "$BK/" 2>/dev/null || true
  ( cd /opt/platform && for p in "${PATCHES[@]}"; do grep -hE '^\+\+\+ b/' "$SRC/$p"; done | sed 's#^+++ b/##' | sort -u > "$BK/files.txt" \
    && tar -czf "$BK/files.tgz" $(while read -r f; do [ -e "$f" ] && echo "$f"; done < "$BK/files.txt") )
  [ -d "$TC" ] && tar -C caddy/tenants/apps -czf "$BK/test-console-old.tgz" test-console
  docker tag "$(docker inspect -f '{{.Image}}' proton-backend)" platform-backend:pre-rc20
  docker tag "$(docker inspect -f '{{.Image}}' proton-agent)" platform-agent:pre-rc20
  docker inspect -f '{{.Config.Image}}' proton-chatwoot-rails > "$BK/rails-image"
  echo "ok: backup in $BK"
  ( cd /opt/platform && for p in "${PATCHES[@]}"; do patch -p1 -F0 < "$SRC/$p" >/dev/null; done )
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
  checks
  # test console (content only; no Caddy change)
  rm -rf "$TC.new" && mkdir -p "$TC.new" && tar -xzf "$SRC/test-console-dist.tgz" -C "$TC.new"
  rm -rf "$TC" && mv "$TC.new" "$TC"; echo "ok: test console replaced"
  # Chatwoot rc20
  $C pull -q chatwoot-rails chatwoot-sidekiq 2>&1 | tail -1
  $C up -d --force-recreate chatwoot-rails chatwoot-sidekiq 2>&1 | tail -1; rails_healthy || true
  echo "rails image: $(docker inspect -f '{{.Config.Image}}' proton-chatwoot-rails | sed 's#.*:##')  git_sha: $(docker exec proton-chatwoot-rails cat /app/.git_sha 2>/dev/null)"
  echo "==> APPLY DONE — check: a call note shows '▶ Play recording'; test console Chat tab as Ahmad answers 'who am I?' with his name"
  ;;
--rollback)
  [ -d "$BK" ] || { echo "no backup at $BK"; exit 1; }
  cp -p tenants/proton.env "$BK/proton.env.pre-rollback"
  ( cd /opt/platform && [ -f "$BK/files.tgz" ] && tar -xzf "$BK/files.tgz" )
  ( cd /opt/platform && while read -r f; do tar -tzf "$BK/files.tgz" "$f" >/dev/null 2>&1 || rm -f "$f"; done < "$BK/files.txt" )
  cat "$BK/proton.env" > tenants/proton.env
  [ -f "$BK/test-console-old.tgz" ] && { rm -rf "$TC"; tar -xzf "$BK/test-console-old.tgz" -C caddy/tenants/apps; }
  docker tag platform-backend:pre-rc20 platform-backend:latest; $C up -d --no-build --force-recreate backend 2>&1 | tail -1
  docker tag platform-agent:pre-rc20 platform-agent:latest; $C up -d --no-build --force-recreate agent 2>&1 | tail -1
  docker tag platform-backend:pre-rc20 platform-backend:proton-demo; docker tag platform-agent:pre-rc20 platform-agent:proton-demo
  docker tag platform-backend:pre-20261110 platform-backend:latest; docker tag platform-agent:pre-wave2 platform-agent:latest
  $C up -d --force-recreate chatwoot-rails chatwoot-sidekiq 2>&1 | tail -1
  backend_healthy || true; agent_healthy || true; rails_healthy || true
  rm -f "$BK/applied"; echo "==> ROLLBACK DONE (rails back on $(sed 's#.*:##' "$BK/rails-image"))";;
*) echo "usage: $0 --dry-run|--apply|--rollback"; exit 2;;
esac
