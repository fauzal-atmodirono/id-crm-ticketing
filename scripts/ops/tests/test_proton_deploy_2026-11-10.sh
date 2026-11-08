#!/usr/bin/env bash
# Dry-run / apply / agent / rollback of proton_deploy_2026-11-10.sh against a fake
# tree with shims for docker (no real docker, curl or network). patch is real.
set -euo pipefail
S="$(cd "$(dirname "$0")/.." && pwd)/proton_deploy_2026-11-10.sh"
E="$(cd "$(dirname "$0")/.." && pwd)/proton_env_2026-11-10.sh"
D="$(mktemp -d)"; trap 'rm -rf "$D"' EXIT
fail(){ echo "FAIL: $1"; exit 1; }
R="$D/opt/platform"; mkdir -p "$R/deploy/tenants" "$R/backend/src" "$D/bin" "$D/bk"
cd "$R/deploy"; touch docker-compose.tenant.yml
printf 'one\ntwo\nthree\n' > "$R/backend/src/a.py"
printf 'x\ny\n' > "$R/backend/src/b.py"
cat > tenants/proton.env <<'X'
TENANT=proton
SECRET_KEY_BASE=sup3r=secret/+
CHATWOOT_IMAGE=asia-southeast1-docker.pkg.dev/lv-playground-genai/proton-images/proton-chatwoot:v4.15.1-custom-rc15
X
cp tenants/proton.env "$D/env.orig"
# patch: modifies a.py and b.py, creates c.py
mkdir -p "$D/p/a/backend/src" "$D/p/b/backend/src"
cp "$R/backend/src/a.py" "$D/p/a/backend/src/a.py"; cp "$R/backend/src/b.py" "$D/p/a/backend/src/b.py"
printf 'one\nTWO\nthree\n' > "$D/p/b/backend/src/a.py"; printf 'x\ny\nz\n' > "$D/p/b/backend/src/b.py"
printf 'new\n' > "$D/p/b/backend/src/c.py"
( cd "$D/p" && diff -ruN a b | sed 's#^\(---\|+++\) \(a\|b\)/#\1 \2/#' > "$D/test.patch" ) || true
grep -q '^+++ b/backend/src/c.py' "$D/test.patch" || fail "test patch build"
cp -r "$R/backend" "$D/src.orig"

# fake docker
echo "$PWD" > "$D/cwd"
cat > "$D/bin/docker" <<'SH'
#!/usr/bin/env bash
D="$FAKE_D"; echo "docker $*" >> "$D/docker.log"
img(){ sed -n 's/^CHATWOOT_IMAGE=//p' tenants/proton.env; }
case "$1" in
  inspect) case "$*" in
      *Running*) [ -n "${FAKE_DOWN:-}" ] && { echo false; exit 0; }; echo true ;;
      *Config.Image*) cat "$D/rails.image" ;; esac ;;
  exec) case "$2" in
      proton-chatwoot-rails) [ -n "${FAKE_API_BAD:-}" ] && exit 1; echo '{"version":"4","data_services":"ok"}' ;;
      proton-backend|proton-agent)
        if [ "$3" = printenv ]; then printf '%s\n' 'ESCALATION_POLICY_L1=a' 'ESCALATION_POLICY_STEPS_JSON=[{"step_no":1}]' 'OTHER=no'; fi ;;
    esac ;;
  logs) [ -n "${FAKE_TRACE:-}" ] && echo "Traceback (most recent call last)"; exit 0 ;;
  pull) exit 0 ;;
  compose) case "$*" in
      *"up -d"*chatwoot-rails*) img > "$D/rails.image" ;;
    esac ;;
esac
exit 0
SH
chmod +x "$D/bin/docker"
echo "$(sed -n 's/^CHATWOOT_IMAGE=//p' tenants/proton.env)" > "$D/rails.image"
export FAKE_D="$D" PATH="$D/bin:$PATH" PATCH_FILE="$D/test.patch" ENV_SCRIPT="$E" \
  BACKUP_DIR="$D/bk" DEPLOY_ROOT="$R" MIN_DISK_KB=1 HEALTH_TRIES=1 HEALTH_SLEEP=0 NO_ROOT_CHECK=1
NEW=asia-southeast1-docker.pkg.dev/lv-playground-genai/proton-images/proton-chatwoot:v4.15.1-custom-rc16
bash -n "$S"

# --- dry-run (default) changes nothing
out=$(bash "$S" 2>&1)
cmp -s "$D/env.orig" tenants/proton.env || fail "dry-run changed env"
diff -r "$D/src.orig" "$R/backend" >/dev/null || fail "dry-run changed source"
[ ! -e "$D/bk/applied" ] || fail "dry-run marker"
grep -q 'compose.* up ' "$D/docker.log" && fail "dry-run ran compose up"
echo "$out" | grep -q 'dry-run complete' || fail "dry-run patch output"
echo "$out" | grep -q 'would set CHATWOOT_IMAGE=' || fail "dry-run image line"
echo "$out" | grep -q 'sup3r' && fail "dry-run printed secret"

# --- preconditions fail closed
FAKE_DOWN=1 bash "$S" --apply >/dev/null 2>&1 && fail "apply with container down"
FAKE_API_BAD=1 bash "$S" --apply >/dev/null 2>&1 && fail "apply with api bad"
[ ! -e "$D/bk/vm-files.tgz" ] || fail "backup made on failed preflight"

# --- apply
out=$(bash "$S" --apply 2>&1) || { echo "$out"; fail "apply"; }
echo "$out" | grep -q 'sup3r' && fail "apply printed secret"
[ -f "$D/bk/vm-files.tgz" ] && [ -f "$D/bk/proton.env" ] || fail "backups"
tar tzf "$D/bk/vm-files.tgz" | grep -q 'backend/src/a.py' || fail "tar content"
grep -qx 'TWO' "$R/backend/src/a.py" || fail "patch not applied"
[ -f "$R/backend/src/c.py" ] || fail "new file missing"
grep -qx "CHATWOOT_IMAGE=$NEW" tenants/proton.env || fail "image not rc16"
grep -qx 'SLA_INBOX_IDS=3,5' tenants/proton.env || fail "env keys"
grep -qx 'SECRET_KEY_BASE=sup3r=secret/+' tenants/proton.env || fail "secret changed"
grep -q 'ESCALATION_POLICY_L1' tenants/proton.env && fail "backend apply wrote agent keys"
grep -q 'pull chatwoot-rails chatwoot-sidekiq' "$D/docker.log" || fail "no pull"
grep -q 'up -d --build backend' "$D/docker.log" || fail "no backend up"
grep -q 'up -d --force-recreate chatwoot-rails chatwoot-sidekiq' "$D/docker.log" || fail "no rails up"
grep -q ' agent' "$D/docker.log" && fail "agent touched by apply"
echo "$out" | grep -q 'rails image tag v4.15.1-custom-rc16' || fail "tag check"
bash "$S" --apply >/dev/null 2>&1 && fail "double apply allowed"

# --- agent
out=$(bash "$S" --agent)
echo "$out" | grep -q 'a=\|"step_no"' && fail "agent printed value"
grep -q '^ESCALATION_POLICY_L1=a$' tenants/proton.env || fail "ESCALATION restored"
grep -q '"step_no":6' tenants/proton.env || fail "step 6 not in STEPS_JSON"
[ "$(grep -c '^ESCALATION_POLICY_STEPS_JSON=' tenants/proton.env)" = 1 ] || fail "steps dup"
grep -q 'up -d --no-build agent' "$D/docker.log" || fail "agent up"
[ -e "$D/bk/agent-ran" ] || fail "agent marker"

# --- rollback
: > "$D/docker.log"
out=$(bash "$S" --rollback)
cmp -s "$D/env.orig" tenants/proton.env || fail "rollback env"
diff -r "$D/src.orig" "$R/backend" >/dev/null || fail "rollback source"
[ ! -e "$R/backend/src/c.py" ] || fail "new file not removed"
grep -q 'up -d --build backend' "$D/docker.log" || fail "rb backend"
grep -q 'up -d --force-recreate chatwoot-rails chatwoot-sidekiq' "$D/docker.log" || fail "rb rails"
grep -q 'up -d --no-build agent' "$D/docker.log" || fail "rb agent"
echo "$out" | grep -q 'rails image tag v4.15.1-custom-rc15' || fail "rb tag"

# --- traceback fails health, patch mismatch fails before any change
rm -f "$D/bk/"* ; cp "$D/env.orig" tenants/proton.env
FAKE_TRACE=1 bash "$S" --apply >/dev/null 2>&1 && fail "traceback not caught"
bash "$S" --rollback >/dev/null 2>&1 || true
rm -f "$D/bk/"*; printf 'DRIFT\ntwo\nthree\n' > "$R/backend/src/a.py"; cp "$R/backend/src/a.py" "$D/drift.a"
cp tenants/proton.env "$D/env.before"
bash "$S" --apply >/dev/null 2>&1 && fail "apply with drift"
cmp -s "$D/env.before" tenants/proton.env || fail "env changed on drift"
cmp -s "$D/drift.a" "$R/backend/src/a.py" || fail "source changed on drift"
echo "PASS"
