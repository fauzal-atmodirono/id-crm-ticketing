#!/usr/bin/env bash
# Provisions the single GCE VM that hosts the whole platform: a static
# external IP, a Debian 12 VM, and a firewall rule opening 80/443 to it.
# Safe to re-run: every resource is guarded with a "describe || create"
# check so an interrupted run can just be re-invoked.
#
# Usage:
#   PROJECT_ID=my-gcp-project ./deploy/scripts/provision-gce.sh
#
# After it finishes, copy the app onto the VM and run bootstrap-vm.sh (see
# the root README.md "GCE deploy" section for the full sequence).
set -euo pipefail

# ---------------------------------------------------------------------------
# Config — override via environment before invoking, e.g.
#   PROJECT_ID=my-project ZONE=asia-southeast2-a ./provision-gce.sh
# ---------------------------------------------------------------------------
PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID to your GCP project id}"
ZONE="${ZONE:-asia-southeast2-a}"
REGION="${ZONE%-*}"
VM_NAME="${VM_NAME:-crm-ticketing}"
MACHINE_TYPE="${MACHINE_TYPE:-e2-standard-4}"
ADDRESS_NAME="${ADDRESS_NAME:-${VM_NAME}-ip}"
NETWORK_TAG="${NETWORK_TAG:-crm-ticketing}"
FIREWALL_RULE_NAME="${FIREWALL_RULE_NAME:-allow-${NETWORK_TAG}-http}"
BOOT_DISK_SIZE="${BOOT_DISK_SIZE:-60GB}"
BOOT_DISK_TYPE="${BOOT_DISK_TYPE:-pd-balanced}"
IMAGE_FAMILY="${IMAGE_FAMILY:-debian-12}"
IMAGE_PROJECT="${IMAGE_PROJECT:-debian-cloud}"

echo "==> Project: ${PROJECT_ID}  Zone: ${ZONE}  VM: ${VM_NAME}"

# ---------------------------------------------------------------------------
# 1. Reserve a static external IP
# ---------------------------------------------------------------------------
if gcloud compute addresses describe "${ADDRESS_NAME}" \
    --project="${PROJECT_ID}" --region="${REGION}" >/dev/null 2>&1; then
  echo "==> Static address ${ADDRESS_NAME} already exists, skipping create"
else
  echo "==> Reserving static address ${ADDRESS_NAME}"
  gcloud compute addresses create "${ADDRESS_NAME}" \
    --project="${PROJECT_ID}" \
    --region="${REGION}"
fi

EXTERNAL_IP="$(gcloud compute addresses describe "${ADDRESS_NAME}" \
  --project="${PROJECT_ID}" --region="${REGION}" \
  --format='value(address)')"

# ---------------------------------------------------------------------------
# 2. Create the VM (idempotent)
# ---------------------------------------------------------------------------
if gcloud compute instances describe "${VM_NAME}" \
    --project="${PROJECT_ID}" --zone="${ZONE}" >/dev/null 2>&1; then
  echo "==> VM ${VM_NAME} already exists, skipping create"
else
  echo "==> Creating VM ${VM_NAME}"
  gcloud compute instances create "${VM_NAME}" \
    --project="${PROJECT_ID}" \
    --zone="${ZONE}" \
    --machine-type="${MACHINE_TYPE}" \
    --image-family="${IMAGE_FAMILY}" \
    --image-project="${IMAGE_PROJECT}" \
    --boot-disk-size="${BOOT_DISK_SIZE}" \
    --boot-disk-type="${BOOT_DISK_TYPE}" \
    --tags="${NETWORK_TAG}" \
    --address="${EXTERNAL_IP}"
fi

# ---------------------------------------------------------------------------
# 3. Firewall: allow tcp:80,443 to instances tagged crm-ticketing
# ---------------------------------------------------------------------------
if gcloud compute firewall-rules describe "${FIREWALL_RULE_NAME}" \
    --project="${PROJECT_ID}" >/dev/null 2>&1; then
  echo "==> Firewall rule ${FIREWALL_RULE_NAME} already exists, skipping create"
else
  echo "==> Creating firewall rule ${FIREWALL_RULE_NAME}"
  gcloud compute firewall-rules create "${FIREWALL_RULE_NAME}" \
    --project="${PROJECT_ID}" \
    --network=default \
    --direction=INGRESS \
    --action=ALLOW \
    --rules=tcp:80,tcp:443 \
    --target-tags="${NETWORK_TAG}" \
    --source-ranges=0.0.0.0/0
fi

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
PUBLIC_IP_DASH="${EXTERNAL_IP//./-}"

cat <<EOF

==> Provisioning complete.

External IP:      ${EXTERNAL_IP}
PUBLIC_IP (.env): ${PUBLIC_IP_DASH}

Next steps:
  1. Copy the app onto the VM:
       gcloud compute scp --recurse --zone="${ZONE}" --project="${PROJECT_ID}" \\
         deploy agent ${VM_NAME}:/tmp/platform
       gcloud compute ssh --zone="${ZONE}" --project="${PROJECT_ID}" ${VM_NAME} \\
         --command="sudo mkdir -p /opt/platform && sudo mv /tmp/platform/* /opt/platform/"
  2. SSH in and run the bootstrap script:
       gcloud compute ssh --zone="${ZONE}" --project="${PROJECT_ID}" ${VM_NAME}
       sudo PUBLIC_IP=${PUBLIC_IP_DASH} /opt/platform/deploy/scripts/bootstrap-vm.sh
  3. Once it prints the URLs, visit:
       http://crm.${PUBLIC_IP_DASH}.nip.io
       http://tickets.${PUBLIC_IP_DASH}.nip.io
       http://agent.${PUBLIC_IP_DASH}.nip.io
       http://mail.${PUBLIC_IP_DASH}.nip.io

See the root README.md "GCE deploy" section for the full walkthrough.
EOF
