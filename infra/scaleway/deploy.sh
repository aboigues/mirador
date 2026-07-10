#!/usr/bin/env bash
# Déploiement INITIAL complet des fonctions Scaleway de Mirador (idempotent).
# Crée le namespace, les 2 fonctions (avec env + secrets), déploie le code, et
# branche le trigger SQS. Pour les mises à jour de code seules → update_code.sh
# (ou le workflow GitHub Actions).
#
# Prérequis : infra/scaleway/.secrets.env rempli (voir README.md § Variables).
# Usage : ./deploy.sh
set -euo pipefail

PROFILE="${SCW_PROFILE-telemach}"
REGION="${SCW_REGION:-fr-par}"
PROJECT_ID="${MIRADOR_PROJECT_ID:-4135267b-fd5a-4079-b716-8b24240deabd}"
NAMESPACE="mirador"
RUNTIME="python312"
ICI="$(cd "$(dirname "$0")" && pwd)"

# shellcheck source=/dev/null
[ -f "$ICI/.secrets.env" ] && set -a && . "$ICI/.secrets.env" && set +a

scw() {
  if [ -n "$PROFILE" ]; then command scw --profile "$PROFILE" "$@"; else command scw "$@"; fi
}
_id_par_nom() { python3 -c "import sys,json,os;print(next((x['id'] for x in json.load(sys.stdin) if x['name']==os.environ['N']),''))"; }

# --- Variables d'environnement (non secrètes) ------------------------------
ENV_ARGS=(
  "environment-variables.BUCKET_NAME=${BUCKET_NAME:-telemach-mirador-audit}"
  "environment-variables.S3_ENDPOINT_URL=${S3_ENDPOINT_URL:-https://s3.fr-par.scw.cloud}"
  "environment-variables.SQS_ENDPOINT_URL=${SQS_ENDPOINT_URL:-https://sqs.mnq.fr-par.scaleway.com}"
  "environment-variables.SQS_QUEUE_URL=${SQS_QUEUE_URL:?SQS_QUEUE_URL requis}"
  "environment-variables.AWS_REGION=${AWS_REGION:-fr-par}"
  "environment-variables.GITHUB_APP_ID=${GITHUB_APP_ID:?GITHUB_APP_ID requis}"
  "environment-variables.MIRADOR_DEPOTS=${MIRADOR_DEPOTS:?MIRADOR_DEPOTS requis}"
)

# --- Secrets (chiffrés) ----------------------------------------------------
_SECRETS=(WEBHOOK_SECRET ANTHROPIC_API_KEY GITHUB_APP_PRIVATE_KEY
          AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY MNQ_ACCESS_KEY MNQ_SECRET_KEY)
SECRET_ARGS=(); i=0
for nom in "${_SECRETS[@]}"; do
  SECRET_ARGS+=("secret-environment-variables.$i.key=$nom"
                "secret-environment-variables.$i.value=${!nom:?secret $nom requis}")
  i=$((i+1))
done

echo "== Namespace ($NAMESPACE) =="
NS_ID=$(N=$NAMESPACE; scw function namespace list project-id="$PROJECT_ID" region="$REGION" -o json | N=$NAMESPACE _id_par_nom)
if [ -z "$NS_ID" ]; then
  NS_ID=$(scw function namespace create name="$NAMESPACE" project-id="$PROJECT_ID" region="$REGION" -o json | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
  echo "   créé : $NS_ID"
else
  echo "   existant : $NS_ID"
fi

# deployer_fonction <nom> <handler> <privacy> <memory> <timeout> <max-scale>
deployer_fonction() {
  local nom="$1" handler="$2" privacy="$3" mem="$4" tmo="$5" maxs="$6"
  echo "== Fonction $nom (handler=$handler) =="
  local fid
  fid=$(N=$nom; scw function function list namespace-id="$NS_ID" region="$REGION" -o json | N=$nom _id_par_nom)
  local base=(handler="$handler" runtime="$RUNTIME" privacy="$privacy"
              memory-limit="$mem" timeout="$tmo" min-scale=0 max-scale="$maxs"
              region="$REGION" "${ENV_ARGS[@]}" "${SECRET_ARGS[@]}")
  if [ -z "$fid" ]; then
    fid=$(scw function function create namespace-id="$NS_ID" name="$nom" "${base[@]}" -o json | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
    echo "   créée : $fid"
  else
    scw function function update function-id="$fid" "${base[@]}" >/dev/null
    echo "   mise à jour : $fid"
  fi
  SCW_PROFILE="$PROFILE" bash "$ICI/update_code.sh" "$fid"
  echo "$fid"
}

WEBHOOK_ID=$(deployer_fonction webhook handler.webhook public 256 30s 5 | tail -1)
TRAITEMENT_ID=$(deployer_fonction traitement handler.traitement private 512 300s 1 | tail -1)

echo "== Trigger SQS (traitement ← mirador-webhooks.fifo) =="
if ! scw function trigger list function-id="$TRAITEMENT_ID" region="$REGION" -o json | grep -q mirador-webhooks; then
  scw function trigger create name=mirador-webhooks function-id="$TRAITEMENT_ID" \
    scw-sqs-config.queue=mirador-webhooks.fifo \
    scw-sqs-config.mnq-project-id="$PROJECT_ID" \
    scw-sqs-config.mnq-region="$REGION" region="$REGION" >/dev/null
  echo "   créé"
else
  echo "   existant"
fi

echo
echo "== URL du webhook (à mettre dans la GitHub App § webhook) =="
scw function function get function-id="$WEBHOOK_ID" region="$REGION" -o json \
  | python3 -c 'import sys,json;print("https://"+json.load(sys.stdin)["domain_name"]+"/webhooks/github")'
