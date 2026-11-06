#!/usr/bin/env bash
# Deploy one Logisly agent to Cloud Run as an A2A service.
# Usage: ./deploy.sh <uc2|uc3>
set -euo pipefail

AGENT="${1:-}"
case "$AGENT" in uc2 | uc3) ;; *) echo "usage: $0 <uc2|uc3>" >&2; exit 2 ;; esac

PROJECT="${GCP_PROJECT:-lv-playground-genai}"
REGION="${GCP_REGION:-asia-southeast2}"
SERVICE="logisly-${AGENT}"
HERE="$(cd "$(dirname "$0")" && pwd)"
DATA="$HERE/../../demo-data/logisly"

# Stage the demo data into the build context (the Dockerfile cannot reach outside it).
STAGE="$HERE/.stage/data"
rm -rf "$HERE/.stage"
mkdir -p "$STAGE/uc2/pricing" "$STAGE/uc3"
cp "$DATA/uc2/pricing/pricing.db" "$STAGE/uc2/pricing/"
cp "$DATA/uc2/business_rules.yaml" "$STAGE/uc2/"
cp -R "$DATA/uc3/templates" "$DATA/uc3/github" "$DATA/uc3/team" "$STAGE/uc3/"
trap 'rm -rf "$HERE/.stage"' EXIT

cd "$HERE"
gcloud run deploy "$SERVICE" \
  --project "$PROJECT" --region "$REGION" \
  --source . \
  --no-allow-unauthenticated \
  --min-instances=1 --max-instances=1 \
  --memory=1Gi --cpu=1 --timeout=900 \
  --set-env-vars "LOGISLY_AGENT=${AGENT},GOOGLE_GENAI_USE_VERTEXAI=true,GOOGLE_CLOUD_PROJECT=${PROJECT},GOOGLE_CLOUD_LOCATION=global"

URL="$(gcloud run services describe "$SERVICE" --project "$PROJECT" --region "$REGION" --format='value(status.url)')"
gcloud run services update "$SERVICE" --project "$PROJECT" --region "$REGION" \
  --update-env-vars "LOGISLY_PUBLIC_URL=${URL}"
echo "service url: ${URL}"
echo "agent card:  ${URL}/.well-known/agent-card.json"

# IAM is never changed by this script. Report whether the runtime SA can call Gemini.
SA="$(gcloud run services describe "$SERVICE" --project "$PROJECT" --region "$REGION" --format='value(spec.template.spec.serviceAccountName)')"
if [ -z "$SA" ]; then
  NUM="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')"
  SA="${NUM}-compute@developer.gserviceaccount.com"
fi
if gcloud projects get-iam-policy "$PROJECT" \
  --flatten='bindings[].members' \
  --filter="bindings.members:serviceAccount:${SA} AND bindings.role:(roles/aiplatform.user OR roles/editor OR roles/owner)" \
  --format='value(bindings.role)' | grep -q .; then
  echo "iam: ${SA} can call Vertex AI"
else
  echo "iam: ${SA} lacks roles/aiplatform.user. A project admin should run:"
  echo "  gcloud projects add-iam-policy-binding ${PROJECT} --member=serviceAccount:${SA} --role=roles/aiplatform.user"
fi
