#!/usr/bin/env bash
# Login bounce, part 2 (9 Nov 2026): after the cookie fix, the rc17 idle-timeout
# monitor (patch 0079) calls DELETE /auth/sign_out right after the dashboard
# loads. Turn the idle timeout off (SESSION_IDLE_TIMEOUT_MINUTES=0) until the
# monitor is fixed in code, and recreate Rails + Sidekiq on the same image.
# Backs up proton.env, keeps its inode.
# Run ON THE VM: sudo bash /tmp/proton_idle_timeout_off_2026-11-09.sh
set -euo pipefail
cd /opt/platform/deploy
F=tenants/proton.env
cp -p "$F" /backups/proton.env.pre-idle-off-20261109
t=$(mktemp); grep -v '^SESSION_IDLE_TIMEOUT_MINUTES=' "$F" > "$t" || true; printf 'SESSION_IDLE_TIMEOUT_MINUTES=0\n' >> "$t"; cat "$t" > "$F"; rm -f "$t"
echo "ok: SESSION_IDLE_TIMEOUT_MINUTES=0"
docker compose -p proton -f docker-compose.tenant.yml --env-file "$F" up -d --no-build --force-recreate chatwoot-rails chatwoot-sidekiq 2>&1 | tail -2
for i in $(seq 1 60); do
  if docker exec proton-chatwoot-rails wget -qO- http://127.0.0.1:3000/api 2>/dev/null | grep -q '"data_services":"ok"'; then
    echo "ok: rails healthy after $((i*5))s"; break
  fi
  sleep 5
done
echo "idle minutes on the login page: $(docker exec proton-chatwoot-rails sh -c 'wget -qO- http://127.0.0.1:3000/app/login' | grep -o '__PROTON_IDLE_TIMEOUT_MINUTES__ = [0-9]*' | head -1)"
