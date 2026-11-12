#!/usr/bin/env bash
# Proton: give the backend + agent their own Chatwoot user (10 Nov 2026).
#
# Why: CHATWOOT_API_TOKEN belonged to Chatwoot user 1 ("Yuda", SuperAdmin).
# Agent 1 was deleted in CRM Settings > Agents at 04:29 UTC on 10 Nov, which
# deleted the user and its access token, so every backend/agent call to
# Chatwoot now gets 401 (WhatsApp AI replies, demo console, Customer 360,
# handoff notes, recordings).
#
# What it does:
#   1. creates (or reuses) the Chatwoot user "Proton AI Bot", confirmed (no
#      e-mail is sent), random unused password, administrator of account 1
#   2. replaces the old token value in tenants/proton.env (every key that held
#      it, normally CHATWOOT_API_TOKEN) with the bot's token — never printed
#   3. recreates proton-backend and proton-agent on their current proton-demo
#      images (no rebuild); shared `latest` tags stay pinned for aeon360
#   4. checks both containers can call Chatwoot again
#
#   sudo bash /tmp/proton_bot_user_2026-11-10.sh --dry-run
#   sudo bash /tmp/proton_bot_user_2026-11-10.sh --apply
set -euo pipefail
MODE="${1:---dry-run}"
D=/opt/platform/deploy
BK=/backups/bot-user-20261110
C="docker compose -p proton -f docker-compose.tenant.yml --env-file tenants/proton.env"
BOT_EMAIL="proton-ai-bot@crm.invalid"
cd "$D"

check_token() {  # $1 container → prints HTTP status of /api/v1/profile and the user name prefix
  docker exec "$1" python -c "
import json, os, urllib.request, urllib.error
base=(os.environ.get('CHATWOOT_API_URL') or os.environ.get('CHATWOOT_URL') or 'http://proton-chatwoot-rails:3000').rstrip('/')
try:
    r=urllib.request.urlopen(urllib.request.Request(base+'/api/v1/profile',headers={'api_access_token':os.environ.get('CHATWOOT_API_TOKEN','')}),timeout=10)
    p=json.loads(r.read()); print(r.status, 'user', p.get('id'), (p.get('name') or '')[:13])
except urllib.error.HTTPError as e: print(e.code)
" 2>/dev/null || echo "?"
}
old_keys() {  # names of proton.env keys whose value equals the current CHATWOOT_API_TOKEN
  local old; old=$(grep -E '^CHATWOOT_API_TOKEN=' tenants/proton.env | tail -1 | cut -d= -f2-)
  [ -n "$old" ] || return 0
  awk -F= -v o="$old" 'index($0,"=")>0 { k=$1; v=substr($0,index($0,"=")+1); if (v==o) print k }' tenants/proton.env | sort -u
}

case "$MODE" in
--dry-run)
  echo "==> DRY RUN (nothing changes)"
  echo "backend → Chatwoot: $(check_token proton-backend)"
  echo "agent   → Chatwoot: $(check_token proton-agent)"
  echo "env keys holding the old token: $(old_keys | tr '\n' ' ')"
  docker exec proton-chatwoot-rails bundle exec rails runner "u = User.find_by(email: '$BOT_EMAIL'); puts \"bot user: #{u ? \"exists (id #{u.id})\" : \"will be created\"}\"" 2>/dev/null | grep '^bot user'
  for i in platform-backend:proton-demo platform-agent:proton-demo platform-backend:pre-20261110 platform-agent:pre-wave2; do docker image inspect "$i" >/dev/null 2>&1 && echo "ok: image $i" || echo "MISSING image $i"; done
  echo "==> dry-run complete";;
--apply)
  mkdir -p "$BK"; cp -p tenants/proton.env "$BK/proton.env"; echo "ok: backup in $BK"
  KEYS=$(old_keys); [ -n "$KEYS" ] || KEYS=CHATWOOT_API_TOKEN
  NEW=$(docker exec proton-chatwoot-rails bundle exec rails runner "
    acct = Account.find(1)
    u = User.find_by(email: '$BOT_EMAIL')
    unless u
      u = User.new(name: 'Proton AI Bot', display_name: 'Proton AI Bot', email: '$BOT_EMAIL', password: SecureRandom.base58(32) + 'aA1!')
      u.skip_confirmation!
      u.save!
    end
    au = AccountUser.find_or_initialize_by(account_id: acct.id, user_id: u.id)
    au.role = :administrator
    au.save!
    u.create_access_token! unless u.access_token
    STDERR.puts \"bot user id #{u.id} administrator of account #{acct.id}\"
    puts \"TOKEN=#{u.access_token.token}\"
  " 2> >(grep -E '^bot user id' >&2) | grep '^TOKEN=' | tail -1 | cut -d= -f2-)
  [ ${#NEW} -ge 20 ] || { echo "FAIL: could not create/read the bot token; nothing changed in proton.env"; exit 1; }
  t=$(mktemp); cp tenants/proton.env "$t"
  for k in $KEYS; do
    grep -v "^${k}=" "$t" > "$t.n" || true; mv "$t.n" "$t"; printf '%s=%s\n' "$k" "$NEW" >> "$t"; echo "set ${k}=<bot token>"
  done
  cat "$t" > tenants/proton.env; rm -f "$t"; unset NEW   # keep the inode (bind mounts)
  # recreate on the CURRENT proton images, then re-pin latest for aeon360
  docker tag platform-backend:proton-demo platform-backend:latest
  docker tag platform-agent:proton-demo platform-agent:latest
  $C up -d --no-build --force-recreate backend agent 2>&1 | tail -2
  docker tag platform-backend:pre-20261110 platform-backend:latest
  docker tag platform-agent:pre-wave2 platform-agent:latest
  sleep 20
  echo "backend → Chatwoot: $(check_token proton-backend)   (200 user <id> Proton AI Bot expected)"
  echo "agent   → Chatwoot: $(check_token proton-agent)"
  echo "==> APPLY DONE";;
*) echo "usage: $0 --dry-run|--apply"; exit 2;;
esac
