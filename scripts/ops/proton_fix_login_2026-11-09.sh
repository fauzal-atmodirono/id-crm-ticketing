#!/usr/bin/env bash
# Login bounce fix (9 Nov 2026). SESSION_TOKEN_LIFESPAN_HOURS=12 makes Chatwoot's
# SPA store its session cookie with expires=differenceInDays(12h)=0, so the cookie
# dies at once and every fresh login bounces back to /app/login. Remove the key
# (back to Chatwoot's 2-month token; the 15-min idle timeout still applies) and
# recreate Rails + Sidekiq on the same image. Backs up proton.env, keeps its inode.
# Run ON THE VM: sudo bash /tmp/proton_fix_login_2026-11-09.sh
set -euo pipefail
cd /opt/platform/deploy
F=tenants/proton.env
mkdir -p /backups
cp -p "$F" /backups/proton.env.pre-login-fix-20261109
t=$(mktemp); grep -v '^SESSION_TOKEN_LIFESPAN_HOURS=' "$F" > "$t" || true; cat "$t" > "$F"; rm -f "$t"
echo "ok: SESSION_TOKEN_LIFESPAN_HOURS lines left: $(grep -c '^SESSION_TOKEN_LIFESPAN_HOURS=' "$F" || true)"
docker compose -p proton -f docker-compose.tenant.yml --env-file "$F" up -d --no-build --force-recreate chatwoot-rails chatwoot-sidekiq 2>&1 | tail -2
for i in $(seq 1 60); do
  if docker exec proton-chatwoot-rails wget -qO- http://127.0.0.1:3000/api 2>/dev/null | grep -q '"data_services":"ok"'; then
    echo "ok: rails healthy after $((i*5))s"; break
  fi
  sleep 5
done
echo "rails image tag: $(docker inspect -f '{{.Config.Image}}' proton-chatwoot-rails | sed 's#.*:##')"
echo "token lifespan env in container: [$(docker exec proton-chatwoot-rails printenv SESSION_TOKEN_LIFESPAN_HOURS || true)]"
