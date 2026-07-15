#!/usr/bin/env bash
# Provisionne les ressources MnQ de Mirador dans le projet dédié (idempotent).
#
# Le BUCKET n'est PAS créé ici : `scw object bucket create` place le bucket dans
# le projet des credentials S3 utilisées, et la clé par défaut vise le mauvais
# projet. Le bucket doit être créé avec la clé applicative scopée sur le projet
# MIRADOR (voir README.md § Bucket).
#
# Prérequis : CLI scw configurée (profil telemach).
# Usage : ./provision.sh
set -euo pipefail

PROFILE="${SCW_PROFILE:-telemach}"
REGION="${SCW_REGION:-fr-par}"
PROJECT_ID="${MIRADOR_PROJECT_ID:-4135267b-fd5a-4079-b716-8b24240deabd}"  # projet MIRADOR
SECRETS_FILE="$(dirname "$0")/.secrets.env"   # ignoré par git

scw() { command scw --profile "$PROFILE" "$@"; }

echo "== 1. Activation MnQ SQS (projet $PROJECT_ID) =="
scw mnq sqs activate project-id="$PROJECT_ID" region="$REGION" >/dev/null
echo "   activé (endpoint https://sqs.mnq.$REGION.scaleway.com)."

echo "== 2. Credentials MnQ (SQS) =="
CREDS_JSON="$(scw mnq sqs create-credentials name=mirador project-id="$PROJECT_ID" \
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

echo "== 3. Queues standard (mirador-webhooks, mirador-writes + DLQ) =="
python3 "$(dirname "$0")/creer_queues.py" <(echo "$CREDS_JSON")

echo
echo "✅ MnQ provisionné dans le projet $PROJECT_ID. Secrets MnQ dans $SECRETS_FILE."
echo "➡️  Bucket : créer avec la clé applicative scopée MIRADOR (README.md § Bucket)."
