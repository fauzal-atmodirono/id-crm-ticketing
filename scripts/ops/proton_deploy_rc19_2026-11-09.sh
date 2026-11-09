#!/usr/bin/env bash
# Proton rc19 deploy (9 Nov 2026), after the CTO call test:
#   - phone call flow: fast start (notice streamed, lookup + Live connect in
#     parallel), one answer per caller turn, caller audio ignored during the
#     PDPA notice, greeting at the mark, Tamil/digits count as speech,
#     language nudge OFF (it caused the double answers)
#   - agent: no chat lifecycle messages (disclaimer/survey/idle) on phone calls
#   - call recording player in the conversation sidebar (patch 0094): signed
#     5-min links, bound to the conversation via GCS metadata, audit per play,
#     Proton glossary transcripts, Re-transcribe
#   - Chatwoot image rc19 (= rc18 + patch 0094)
#
# Copy to the VM /tmp with backend-rc19.patch and agent-rc19.patch, then:
#   sudo bash /tmp/proton_deploy_rc19_2026-11-09.sh --dry-run
#   sudo bash /tmp/proton_deploy_rc19_2026-11-09.sh --apply
#   sudo bash /tmp/proton_deploy_rc19_2026-11-09.sh --rollback
# No value is ever printed. The recording signing secret is generated on the
# VM and only written to tenants/proton.env. Shared `latest` image tags stay
# pinned for aeon360. GCS: proton-backend SA already has objectAdmin on the
# archive bucket, so no IAM change is needed.
set -euo pipefail
MODE="${1:---dry-run}"
D=/opt/platform/deploy
BK=/backups/predeploy-rc19-20261109
SRC=/tmp
C="docker compose -p proton -f docker-compose.tenant.yml --env-file tenants/proton.env"
IMG=asia-southeast1-docker.pkg.dev/lv-playground-genai/proton-images/proton-chatwoot:v4.15.1-custom-rc19
cd "$D"

KEYS=(
  "CHATWOOT_IMAGE=$IMG"
  "PHONE_FAST_START_ENABLED=true"
  "PHONE_LANGUAGE_NUDGE_ENABLED=false"
  "PHONE_TRANSCRIPT_GLOSSARY_ENABLED=true"
  "CALL_RECORDING_RETRIEVAL_ENABLED=true"
)
PATCHES=(backend-rc19.patch agent-rc19.patch)

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
  if grep -qE '^CALL_RECORDING_SIGNING_SECRET=.{32,}' "$t"; then
    echo "kept CALL_RECORDING_SIGNING_SECRET=<already set>"
  else
    grep -v '^CALL_RECORDING_SIGNING_SECRET=' "$t" > "$t.n" || true; mv "$t.n" "$t"
    printf 'CALL_RECORDING_SIGNING_SECRET=%s\n' "$(openssl rand -hex 32 2>/dev/null || python3 -c 'import secrets;print(secrets.token_hex(32))')" >> "$t"
    echo "set CALL_RECORDING_SIGNING_SECRET=<generated, 64 hex>"
  fi
  cat "$t" > "$f"; rm -f "$t"   # keep the inode (bind mounts)
}
# 401 without a session = route mounted and the flag on; 404 = flag off
recording_route_check() {
  local code
  code=$(docker exec proton-backend python -c "import urllib.request,urllib.error
try: print(urllib.request.urlopen('http://127.0.0.1:8080/calls/1/recording').status)
except urllib.error.HTTPError as e: print(e.code)" 2>/dev/null || echo "?")
  echo "recording route without a session: HTTP $code (401 expected)"
}

case "$MODE" in
--dry-run)
  echo "==> DRY RUN (nothing changes)"
  for f in "${PATCHES[@]}"; do [ -s "$SRC/$f" ] || { echo "missing $SRC/$f"; exit 1; }; done
  ( cd /opt/platform && for p in "${PATCHES[@]}"; do
      out=$(patch -p1 -F0 --dry-run < "$SRC/$p" 2>&1) || { echo "FAIL: $p does not apply:"; echo "$out" | grep -v '^checking file' | head; exit 1; }
      echo "$p: $(echo "$out" | grep -c '^checking file') files apply cleanly"
    done )
  for kv in "${KEYS[@]}"; do echo "would set ${kv%%=*}=<set>"; done
  grep -qE '^CALL_RECORDING_SIGNING_SECRET=.{32,}' tenants/proton.env && echo "signing secret: already set (kept)" || echo "signing secret: would generate"
  docker pull -q "$IMG" >/dev/null && echo "ok: rc19 image pulls"
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
  docker tag "$(docker inspect -f '{{.Image}}' proton-backend)" platform-backend:pre-rc19
  docker tag "$(docker inspect -f '{{.Image}}' proton-agent)" platform-agent:pre-rc19
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
  recording_route_check
  # Chatwoot rc19
  $C pull -q chatwoot-rails chatwoot-sidekiq 2>&1 | tail -1
  $C up -d --force-recreate chatwoot-rails chatwoot-sidekiq 2>&1 | tail -1; rails_healthy || true
  echo "rails image: $(docker inspect -f '{{.Config.Image}}' proton-chatwoot-rails | sed 's#.*:##')  git_sha: $(docker exec proton-chatwoot-rails cat /app/.git_sha 2>/dev/null)"
  echo "==> APPLY DONE — make one test call, then: docker logs proton-backend 2>&1 | grep phone_call_timing | tail -3"
  ;;
--rollback)
  [ -d "$BK" ] || { echo "no backup at $BK"; exit 1; }
  cp -p tenants/proton.env "$BK/proton.env.pre-rollback"
  ( cd /opt/platform && [ -f "$BK/files.tgz" ] && tar -xzf "$BK/files.tgz" )
  ( cd /opt/platform && while read -r f; do tar -tzf "$BK/files.tgz" "$f" >/dev/null 2>&1 || rm -f "$f"; done < "$BK/files.txt" )
  cat "$BK/proton.env" > tenants/proton.env
  docker tag platform-backend:pre-rc19 platform-backend:latest; $C up -d --no-build --force-recreate backend 2>&1 | tail -1
  docker tag platform-agent:pre-rc19 platform-agent:latest; $C up -d --no-build --force-recreate agent 2>&1 | tail -1
  docker tag platform-backend:pre-rc19 platform-backend:proton-demo; docker tag platform-agent:pre-rc19 platform-agent:proton-demo
  docker tag platform-backend:pre-20261110 platform-backend:latest; docker tag platform-agent:pre-wave2 platform-agent:latest
  $C up -d --force-recreate chatwoot-rails chatwoot-sidekiq 2>&1 | tail -1
  backend_healthy || true; agent_healthy || true; rails_healthy || true
  rm -f "$BK/applied"; echo "==> ROLLBACK DONE (rails back on $(sed 's#.*:##' "$BK/rails-image"))";;
*) echo "usage: $0 --dry-run|--apply|--rollback"; exit 2;;
esac
