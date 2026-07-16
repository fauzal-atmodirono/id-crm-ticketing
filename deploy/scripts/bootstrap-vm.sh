#!/usr/bin/env bash
# Bootstraps a freshly provisioned Debian 12 GCE VM to run the platform:
# installs Docker CE + compose plugin from Docker's official apt repo,
# adds swap, fills in .env secrets, and brings the stack up.
#
# Run this ON the VM, as a sudo-capable user, AFTER copying the app there:
#   gcloud compute scp --recurse deploy agent <vm-name>:/tmp/platform
#   gcloud compute ssh <vm-name>
#   sudo mkdir -p /opt/platform && sudo mv /tmp/platform/* /opt/platform/
#   sudo /opt/platform/deploy/scripts/bootstrap-vm.sh
#
# The script re-execs itself with sudo if not already root, so it also
# works when invoked without the leading `sudo`.
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  exec sudo -E bash "$0" "$@"
fi

PLATFORM_DIR="${PLATFORM_DIR:-/opt/platform}"
DEPLOY_DIR="${PLATFORM_DIR}/deploy"
ACTUAL_USER="${SUDO_USER:-${USER:-root}}"
SWAP_SIZE_MB=4096

echo "==> Bootstrapping platform VM (platform dir: ${PLATFORM_DIR})"

# ---------------------------------------------------------------------------
# 1. Docker CE + compose plugin from Docker's official apt repo
#    (NOT the distro's docker.io package).
# ---------------------------------------------------------------------------
if command -v docker >/dev/null 2>&1 && dpkg -s docker-ce >/dev/null 2>&1; then
  echo "==> Docker CE already installed, skipping"
else
  echo "==> Installing Docker CE + compose plugin"
  apt-get update -y
  apt-get install -y ca-certificates curl gnupg openssl

  install -m 0755 -d /etc/apt/keyrings
  if [[ ! -f /etc/apt/keyrings/docker.asc ]]; then
    curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
  fi

  # shellcheck disable=SC1091
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian ${VERSION_CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list

  apt-get update -y
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi

if [[ "${ACTUAL_USER}" != "root" ]]; then
  usermod -aG docker "${ACTUAL_USER}"
  echo "==> Added ${ACTUAL_USER} to the docker group (re-login required for non-sudo docker use)"
fi

# ---------------------------------------------------------------------------
# 2. 4GB swapfile + fstab entry + vm.swappiness=10
# ---------------------------------------------------------------------------
if swapon --show=NAME --noheadings | grep -q '^/swapfile$'; then
  echo "==> /swapfile already active, skipping"
else
  if [[ ! -f /swapfile ]]; then
    echo "==> Creating ${SWAP_SIZE_MB}MB swapfile at /swapfile"
    fallocate -l "${SWAP_SIZE_MB}M" /swapfile || dd if=/dev/zero of=/swapfile bs=1M count="${SWAP_SIZE_MB}"
    chmod 600 /swapfile
    mkswap /swapfile
  fi
  swapon /swapfile
fi

if ! grep -q '^/swapfile ' /etc/fstab; then
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

cat > /etc/sysctl.d/99-swappiness.conf <<'EOF'
vm.swappiness=10
EOF
sysctl -w vm.swappiness=10 >/dev/null

# ---------------------------------------------------------------------------
# 3. /opt/platform must already contain deploy/ and agent/ (scp'd in)
# ---------------------------------------------------------------------------
mkdir -p "${PLATFORM_DIR}"

if [[ ! -d "${DEPLOY_DIR}" ]]; then
  cat <<EOF >&2
ERROR: ${DEPLOY_DIR} not found.

Copy the app onto the VM first, e.g. from your workstation:
  gcloud compute scp --recurse deploy agent <vm-name>:/tmp/platform
  gcloud compute ssh <vm-name> --command="sudo mkdir -p ${PLATFORM_DIR} && sudo mv /tmp/platform/* ${PLATFORM_DIR}/"

Then re-run this script.
EOF
  exit 1
fi

cd "${DEPLOY_DIR}"

# ---------------------------------------------------------------------------
# 4. infra.env: create from template, fill blank/"changeme" secrets.
# ---------------------------------------------------------------------------
if [[ ! -f infra.env ]]; then
  echo "==> Creating infra.env from infra.env.example"
  cp infra.env.example infra.env
fi

fill_if_blank() {
  local var="$1" value="$2" current
  current="$(grep -E "^${var}=" infra.env | head -n1 | cut -d= -f2-)"
  if [[ -z "${current}" || "${current}" == "changeme" ]]; then
    sed -i -e '/^'"${var}"'=/d' infra.env
    printf '%s=%s\n' "${var}" "${value}" >> infra.env
    echo "==> Filled ${var}"
  fi
}

fill_if_blank POSTGRES_PASSWORD "$(openssl rand -hex 16)"
fill_if_blank MAILPIT_AUTH_USER "admin"

MAILPIT_PW="$(openssl rand -hex 12)"
MAILPIT_HASH="$(echo "$MAILPIT_PW" | docker run --rm -i caddy:2-alpine caddy hash-password --plaintext - 2>/dev/null | tail -1)"
if [[ -n "${MAILPIT_HASH}" ]]; then
  # Caddy reads this hash from a rendered Caddyfile snippet (not via env), so
  # store it literally — no $$ escaping needed here.
  fill_if_blank MAILPIT_AUTH_HASH "${MAILPIT_HASH}"
  echo "==> Mailpit UI password (save this now, it is not stored): ${MAILPIT_PW}"
else
  echo "WARNING: Failed to generate Mailpit password hash; check Docker" >&2
fi

# ---------------------------------------------------------------------------
# 5. Auto-detect PUBLIC_IP from GCE metadata (dash form for nip.io)
# ---------------------------------------------------------------------------
DETECTED_IP="$(curl -s -H "Metadata-Flavor: Google" \
  "http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip" || true)"

if [[ -n "${DETECTED_IP}" ]]; then
  PUBLIC_IP_DASH="${DETECTED_IP//./-}"
  sed -i "s|^PUBLIC_IP=.*|PUBLIC_IP=${PUBLIC_IP_DASH}|" infra.env
  echo "==> Detected external IP ${DETECTED_IP}, set PUBLIC_IP=${PUBLIC_IP_DASH}"
else
  echo "WARNING: could not auto-detect external IP from GCE metadata; check PUBLIC_IP in infra.env manually" >&2
  PUBLIC_IP_DASH="$(grep -E '^PUBLIC_IP=' infra.env | head -n1 | cut -d= -f2-)"
fi

# ---------------------------------------------------------------------------
# 6. Bring up shared infra, wait for postgres, then provision any tenants
#    named in the TENANTS env var (space-separated). Tenants can also be added
#    later with scripts/add-tenant.sh.
# ---------------------------------------------------------------------------
wait_healthy() {
  local service="$1" timeout="${2:-180}" waited=0 cid health
  echo "==> Waiting for ${service} to become healthy (timeout ${timeout}s)"
  cid="$(docker compose -p platform-infra -f docker-compose.infra.yml ps -q "${service}")"
  while true; do
    health="$(docker inspect --format='{{.State.Health.Status}}' "${cid}" 2>/dev/null || echo unknown)"
    if [[ "${health}" == "healthy" ]]; then
      echo "==> ${service} is healthy"
      return 0
    fi
    if (( waited >= timeout )); then
      echo "ERROR: ${service} did not become healthy within ${timeout}s (last status: ${health})" >&2
      docker compose -p platform-infra -f docker-compose.infra.yml logs --tail=50 "${service}" >&2 || true
      exit 1
    fi
    sleep 3
    waited=$((waited + 3))
  done
}

echo "==> Starting shared infra (caddy, postgres, mailpit)"
docker compose -p platform-infra -f docker-compose.infra.yml --env-file infra.env up -d
wait_healthy postgres

for tenant in ${TENANTS:-}; do
  echo "==> Provisioning tenant '${tenant}'"
  ./scripts/add-tenant.sh "${tenant}"
done

cat <<EOF

==> Bootstrap complete. Shared infra is up.

Provision customers with:
  cd ${DEPLOY_DIR} && ./scripts/add-tenant.sh <tenant-name>

Each tenant is then reachable at (once its containers finish starting):
  http://<tenant>.crm.${PUBLIC_IP_DASH}.nip.io      (Chatwoot)
  http://<tenant>.tickets.${PUBLIC_IP_DASH}.nip.io  (Zammad)
  http://<tenant>.agent.${PUBLIC_IP_DASH}.nip.io    (agent)
  http://<tenant>.mail.${PUBLIC_IP_DASH}.nip.io     (shared Mailpit)

See the root README.md for per-tenant Phase-2/Phase-3 wiring steps.
EOF
