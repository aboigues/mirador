#!/usr/bin/env bash
# Provisionne les ressources Scaleway de Mirador (idempotent).
#
# Prérequis : CLI scw configurée avec le profil ciblant le bon projet.
# Usage : ./provision.sh
set -euo pipefail

PROFILE="${SCW_PROFILE:-telemach}"
REGION="${SCW_REGION:-fr-par}"
BUCKET="${MIRADOR_BUCKET:-telemach-mirador-audit}"
SECRETS_FILE="$(dirname "$0")/.secrets.env"   # ignoré par git

scw() { command scw --profile "$PROFILE" "$@"; }

echo "== 1. Bucket Object Storage ($BUCKET) =="
if scw object bucket get name="$BUCKET" region="$REGION" >/dev/null 2>&1; then
  echo "   déjà présent."
else
  scw object bucket create name="$BUCKET" region="$REGION" >/dev/null
  echo "   créé."
fi

echo "== 2. Activation MnQ SQS =="
scw mnq sqs activate region="$REGION" >/dev/null
echo "   activé (endpoint https://sqs.mnq.$REGION.scaleway.com)."

echo "== 3. Credentials MnQ (SQS) =="
CREDS_JSON="$(scw mnq sqs create-credentials name=mirador \
  permissions.can-publish=true permissions.can-receive=true permissions.can-manage=true \
  region="$REGION" -o json)"
AK="$(echo "$CREDS_JSON" | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_key"])')"
SK="$(echo "$CREDS_JSON" | python3 -c 'import sys,json;print(json.load(sys.stdin)["secret_key"])')"
{
  echo "# Credentials MnQ (SQS) — NE PAS COMMITER"
  echo "MNQ_ACCESS_KEY=$AK"
  echo "MNQ_SECRET_KEY=$SK"
} > "$SECRETS_FILE"
echo "   credentials MnQ écrites dans $SECRETS_FILE (access_key: $AK)"

echo "== 4. Queues FIFO (mirador-webhooks, mirador-writes + DLQ) =="
python3 "$(dirname "$0")/creer_queues.py" <(echo "$CREDS_JSON")

echo
echo "✅ Provisioning terminé. Secrets MnQ dans $SECRETS_FILE."
echo "⚠️  Le bucket exige une clé IAM avec droit Object Storage sur CE projet"
echo "    (les clés IAM par défaut ne suffisent pas — voir README.md § Credentials)."
