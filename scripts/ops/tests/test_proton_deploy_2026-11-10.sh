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
      *'{{.Image}}'*) echo sha256:abc123 ;;
      *Running*) [ -n "${FAKE_DOWN:-}" ] && { echo false; exit 0; }; echo true ;;
      *StartedAt*) echo 2026-11-10T01:00:00Z ;;
      *Config.Image*) cat "$D/rails.image" ;; esac ;;
  image) [ -e "$D/pre.tag" ] || exit 1 ;;
  tag) case "$*" in *pre-20261110*) touch "$D/pre.tag" ;; esac ;;
  exec) case "$2 $*" in
      "proton-chatwoot-rails "*"/app/login"*) echo 'window.x = { features: [] }' ;;
      "proton-chatwoot-rails "*".git_sha"*) echo "${FAKE_SHA:-db674a88e1}" ;;
      "proton-chatwoot-rails "*) [ -n "${FAKE_API_BAD:-}" ] && exit 1; echo '{"version":"4","data_services":"ok"}' ;;
      "proton-backend "*openapi*) { [ -n "${FAKE_NO_OPENAPI:-}" ] || { [ -n "${FAKE_NO_OPENAPI_POST:-}" ] && [ -e "$D/backend.up" ]; }; } && { echo '{"paths":{}}'; exit 0; }
         echo '{"paths":{"/rsa/incidents":{},"/admin/taxonomy/x":{},"/escalation/notify":{},"/admin/escalation/pic":{}}}' ;;
      "proton-backend "*healthz*) [ -n "${FAKE_BACKEND_DOWN:-}" ] && exit 1; exit 0 ;;
      *printenv*) printf '%s\n' 'ESCALATION_POLICY_L1=a' 'ESCALATION_POLICY_ENABLED=true' 'ESCALATION_POLICY_DRY_RUN=false' 'ESCALATION_POLICY_STEPS_JSON=[{"step_no":1}]' 'OTHER=no' ;;
    esac ;;
  logs) [ -n "${FAKE_TRACE:-}" ] && echo "Traceback (most recent call last)"
        [ -n "${FAKE_PHONE_WARN:-}" ] && echo "rsa_customer_phone_migration_failed"; exit 0 ;;
  pull) exit 0 ;;
  compose) case "$*" in
      *" pull "*) [ -n "${FAKE_PULL_FAIL:-}" ] && exit 1 ;;
      *"up -d"*backend*) touch "$D/backend.up" ;;
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
grep -q 'docker tag sha256:abc123 platform-backend:pre-20261110' "$D/docker.log" || fail "pre tag not from running image"
for f in proton_deploy_2026-11-10.sh proton_env_2026-11-10.sh test.patch; do [ -f "$D/bk/$f" ] || [ -f "$D/bk/backend-2026-11-10.patch" ] || fail "script copies in BK"; done
[ -f "$D/bk/proton_deploy_2026-11-10.sh" ] && [ -f "$D/bk/proton_env_2026-11-10.sh" ] || fail "scripts not copied"
grep -q "rc15" "$D/bk/preflight-chatwoot-image" || fail "preflight image not recorded"
echo "$out" | grep -q 'openapi.json has' || fail "openapi check"
echo "$out" | grep -q 'app/login 200' || fail "login check"
echo "$out" | grep -q 'git_sha db674a88' || fail "sha check"
echo "$out" | grep -q 'sidekiq image tag\|rails + sidekiq image tag' || fail "sidekiq tag"
echo "$out" | grep -q 'sidekiq image tag v4.15.1-custom-rc16' || fail "tag check"
bash "$S" --apply >/dev/null 2>&1 && fail "double apply allowed"

# --- agent
out=$(bash "$S" --agent)
echo "$out" | grep -q 'a=\|"step_no"' && fail "agent printed value"
grep -q '^ESCALATION_POLICY_L1=a$' tenants/proton.env || fail "ESCALATION restored"
grep -q '"step_no":6' tenants/proton.env || fail "step 6 not in STEPS_JSON"
[ "$(grep -c '^ESCALATION_POLICY_STEPS_JSON=' tenants/proton.env)" = 1 ] || fail "steps dup"
grep -qx 'ESCALATION_POLICY_DRY_RUN=true' tenants/proton.env || fail "DRY_RUN not forced true"
echo "$out" | grep -q 'ESCALATION_POLICY_ENABLED=true ESCALATION_POLICY_DRY_RUN=true' || fail "booleans not printed"
cp tenants/proton.env "$D/env.after-agent"
grep -q 'up -d --no-build agent' "$D/docker.log" || fail "agent up"
[ -e "$D/bk/agent-ran" ] || fail "agent marker"

# --- rollback
: > "$D/docker.log"
out=$(bash "$S" --rollback)
[ -f "$D/bk/proton.env.pre-rollback" ] && cmp -s "$D/env.after-agent" "$D/bk/proton.env.pre-rollback" || fail "pre-rollback copy"
grep -qx 'ESCALATION_POLICY_L1=a' tenants/proton.env || fail "ESCALATION line lost by rollback"
grep -q '"step_no":6' tenants/proton.env || fail "STEPS_JSON lost by rollback"
grep -qx 'ESCALATION_POLICY_DRY_RUN=true' tenants/proton.env || fail "rollback DRY_RUN"
grep -qx "CHATWOOT_IMAGE=${NEW%rc16}rc15" tenants/proton.env || fail "rollback image not rc15"
grep -q 'SLA_INBOX_IDS' tenants/proton.env && fail "backend keys survived rollback"
grep -q 'docker tag platform-backend:pre-20261110 platform-backend:latest' "$D/docker.log" || fail "rb retag"
grep -q 'up -d --no-build --force-recreate backend' "$D/docker.log" || fail "rb backend not no-build"
grep -v '^ESCALATION_POLICY_' tenants/proton.env | cmp -s - "$D/env.orig" || fail "rollback env (non-escalation lines) differs"
diff -r "$D/src.orig" "$R/backend" >/dev/null || fail "rollback source"
[ ! -e "$R/backend/src/c.py" ] || fail "new file not removed"
grep -q 'up -d --force-recreate chatwoot-rails chatwoot-sidekiq' "$D/docker.log" || fail "rb rails"
grep -q 'up -d --no-build agent' "$D/docker.log" || fail "rb agent"
echo "$out" | grep -q 'sidekiq image tag v4.15.1-custom-rc15' || fail "rb tag"

# --- traceback fails health, patch mismatch fails before any change
rm -f "$D/bk/"* ; cp "$D/env.orig" tenants/proton.env
FAKE_TRACE=1 bash "$S" --apply >/dev/null 2>&1 && fail "traceback not caught"
bash "$S" --rollback >/dev/null 2>&1 || true
rm -f "$D/bk/"*; printf 'DRIFT\ntwo\nthree\n' > "$R/backend/src/a.py"; cp "$R/backend/src/a.py" "$D/drift.a"
cp tenants/proton.env "$D/env.before"
bash "$S" --apply >/dev/null 2>&1 && fail "apply with drift"
cmp -s "$D/env.before" tenants/proton.env || fail "env changed on drift"
cmp -s "$D/drift.a" "$R/backend/src/a.py" || fail "source changed on drift"

# --- new health / preflight cases
reset_all(){ rm -f "$D/bk/"* "$D/pre.tag" "$D/backend.up" "$D/docker.log"; cp "$D/env.orig" tenants/proton.env; cp -r "$D/src.orig/." "$R/backend/"; rm -f "$R/backend/src/c.py"; echo "${NEW%rc16}rc15" > "$D/rails.image"; }
reset_all
FAKE_NO_OPENAPI=1 bash "$S" --apply >/dev/null 2>&1 && fail "baseline openapi gap not caught"
[ ! -e "$D/bk/started" ] || fail "backup started despite baseline openapi gap"
reset_all
FAKE_NO_OPENAPI_POST=1 bash "$S" --apply >/dev/null 2>&1 && fail "post-apply openapi gap not caught"
[ -e "$D/bk/started" ] || fail "post-apply case should have started"
reset_all
FAKE_SHA=zzz bash "$S" --apply >/dev/null 2>&1 && fail "bad git sha not caught"
reset_all
out=$(FAKE_PHONE_WARN=1 bash "$S" --apply 2>&1) || fail "phone warn must not fail"
echo "$out" | grep -q 'WARN: rsa_customer_phone_migration_failed' || fail "phone warn missing"
reset_all
FAKE_BACKEND_DOWN=1 bash "$S" --apply >/dev/null 2>&1 && fail "baseline not required"
[ ! -e "$D/bk/applied" ] || fail "applied despite bad baseline"
out=$(FAKE_BACKEND_DOWN=1 bash "$S" --apply --accept-baseline 2>&1) && fail "should still fail health" || true
echo "$out" | grep -q 'accept-baseline' || fail "accept-baseline warn"
[ -e "$D/bk/applied" ] || fail "flag did not pass preflight"
reset_all
sed -i.bak 's/rc15/rc14/' tenants/proton.env; rm -f tenants/proton.env.bak
bash "$S" --apply >/dev/null 2>&1 && fail "wrong current image accepted"

# --- rollback completes every step even if backend health fails
reset_all
bash "$S" --apply >/dev/null 2>&1 || fail "apply for rollback test"
: > "$D/docker.log"
out=$(FAKE_BACKEND_DOWN=1 bash "$S" --rollback 2>&1) || fail "rollback should exit 0 with health warnings: $out"
grep -q 'up -d --force-recreate chatwoot-rails chatwoot-sidekiq' "$D/docker.log" || fail "rails not restored after backend health failure"
[ ! -e "$D/bk/applied" ] && [ ! -e "$D/bk/started" ] && [ ! -e "$D/bk/backend-touched" ] || fail "markers not cleared"
echo "$out" | grep -q 'WARN: backend health failed' || fail "health warn missing"
echo "$out" | grep -q 'ROLLBACK RESTORED' || fail "summary missing"

# --- rollback skips steps the apply never reached; never builds the backend
reset_all
FAKE_PULL_FAIL=1 bash "$S" --apply >/dev/null 2>&1 && fail "pull failure not caught"
: > "$D/docker.log"
out=$(bash "$S" --rollback 2>&1) || fail "partial rollback failed: $out"
grep -q 'up -d' "$D/docker.log" && fail "rollback recreated containers for steps never reached"
echo "$out" | grep -q 'skip: rails/sidekiq were never recreated' || fail "skip text"
cmp -s "$D/env.orig" tenants/proton.env || fail "partial rollback env"

# --- missing pre tag: stop with instructions, never rebuild, still restore rails
reset_all
bash "$S" --apply >/dev/null 2>&1 || fail "apply for tag test"
rm -f "$D/pre.tag"; : > "$D/docker.log"
out=$(bash "$S" --rollback 2>&1) && fail "missing tag should end non-zero"
grep -q -- '--build' "$D/docker.log" && fail "rollback built the backend"
grep -q 'up -d --force-recreate chatwoot-rails chatwoot-sidekiq' "$D/docker.log" || fail "rails skipped after tag problem"
echo "$out" | grep -q 'Do not rebuild' || fail "instructions missing"
[ -e "$D/bk/started" ] || fail "markers should be kept when incomplete"
echo "PASS"
