#!/usr/bin/env bash
# Re-déploiement + re-test E2E de Mirador après une correction de code.
#
# Enchaîne les étapes manuelles à faire après le merge d'un fix (le CD auto
# GitHub Actions est inactif tant que les secrets SCW_* ne sont pas configurés) :
#   1. redéploie le CODE des 2 fonctions (webhook + traitement) — update_code.sh
#   2. purge les messages poison restés bloqués (queue mirador-webhooks + DLQ)
#   3. affiche la marche à suivre pour le re-test E2E réel
#
# Ne touche PAS aux variables d'environnement / secrets des fonctions (posés une
# fois par deploy.sh). Idempotent : relançable sans effet de bord (hors purge).
#
# Prérequis : ~/.config/mirador/secrets.env rempli, profil scw `telemach`.
# Usage :
#   ./redeploy.sh            # redéploie puis demande confirmation avant de purger
#   ./redeploy.sh --sans-purge   # redéploie seulement
#   ./redeploy.sh --oui      # redéploie et purge sans confirmation
set -euo pipefail

PROFILE="${SCW_PROFILE-telemach}"
REGION="${SCW_REGION:-fr-par}"
PROJECT_ID="${MIRADOR_PROJECT_ID:-4135267b-fd5a-4079-b716-8b24240deabd}"
NAMESPACE="mirador"
ICI="$(cd "$(dirname "$0")" && pwd)"
SECRETS="${MIRADOR_SECRETS:-$HOME/.config/mirador/secrets.env}"  # hors dépôt (voir README § Variables)

PURGE="demander"
for arg in "$@"; do
  case "$arg" in
    --sans-purge) PURGE="non" ;;
    --oui) PURGE="oui" ;;
    *) echo "argument inconnu : $arg" >&2; exit 2 ;;
  esac
done

# Charge le fichier de secrets (nécessaire pour les credentials MnQ de la purge).
if [ ! -f "$SECRETS" ]; then
  echo "❌ $SECRETS introuvable (voir README.md § Variables)." >&2
  exit 1
fi
# shellcheck source=/dev/null
set -a && . "$SECRETS" && set +a

scw() {
  if [ -n "$PROFILE" ]; then command scw --profile "$PROFILE" "$@"; else command scw "$@"; fi
}
_id_par_nom() { python3 -c "import sys,json,os;print(next((x['id'] for x in json.load(sys.stdin) if x['name']==os.environ['N']),''))"; }

echo "== Résolution des fonctions (namespace $NAMESPACE) =="
NS_ID=$(scw function namespace list project-id="$PROJECT_ID" region="$REGION" -o json | N=$NAMESPACE _id_par_nom)
[ -n "$NS_ID" ] || { echo "❌ namespace $NAMESPACE introuvable — lancer d'abord deploy.sh." >&2; exit 1; }
WEBHOOK_ID=$(scw function function list namespace-id="$NS_ID" region="$REGION" -o json | N=webhook _id_par_nom)
TRAITEMENT_ID=$(scw function function list namespace-id="$NS_ID" region="$REGION" -o json | N=traitement _id_par_nom)
if [ -z "$WEBHOOK_ID" ] || [ -z "$TRAITEMENT_ID" ]; then
  echo "❌ fonctions webhook/traitement introuvables — lancer deploy.sh." >&2; exit 1
fi
echo "   webhook=$WEBHOOK_ID  traitement=$TRAITEMENT_ID"

echo "== Redéploiement du code (traitement puis webhook) =="
# traitement d'abord : le consommateur corrigé doit être en place avant qu'un
# nouvel événement n'arrive dans la file.
SCW_PROFILE="$PROFILE" bash "$ICI/update_code.sh" "$TRAITEMENT_ID"
SCW_PROFILE="$PROFILE" bash "$ICI/update_code.sh" "$WEBHOOK_ID"

if [ "$PURGE" = "demander" ]; then
  read -r -p "Purger mirador-webhooks + DLQ (messages poison du test précédent) ? [o/N] " rep
  case "$rep" in o|O) PURGE="oui" ;; *) PURGE="non" ;; esac
fi
if [ "$PURGE" = "oui" ]; then
  echo "== Purge des queues =="
  "${PYTHON:-python3}" "$ICI/purger_queues.py"
else
  echo "== Purge ignorée =="
fi

WEBHOOK_URL=$(scw function function get function-id="$WEBHOOK_ID" region="$REGION" -o json \
  | python3 -c 'import sys,json;print("https://"+json.load(sys.stdin)["domain_name"]+"/webhooks/github")')

cat <<EOF

✅ Redéploiement terminé.
   URL webhook : $WEBHOOK_URL

== Re-test E2E réel (manuel, côté GitHub) ==
1. Déclencher un run en ÉCHEC sur le dépôt de test (aboigues/k8t), p. ex. relancer
   un workflow voué à échouer, ou pousser un commit qui casse la CI.
2. GitHub envoie les events workflow_run (requested/in_progress → ignorés 204 ;
   completed → 202 enqueue). Le trigger SQS déclenche 'traitement'.
3. Vérifier le résultat attendu (selon la gravité) :
   - une ISSUE d'escalade ouverte sur aboigues/k8t (échec sur main), OU
   - une RELANCE / PR (intervention auto), OU
   - une simple journalisation.

== Commandes de vérification (à lancer quand tu veux) ==
   # Logs de la fonction traitement (remonter les erreurs éventuelles) :
   scw --profile $PROFILE function function logs function-id=$TRAITEMENT_ID region=$REGION
   # Profondeur des queues (doit revenir à 0 après traitement) :
   set -a && . $SECRETS && set +a
   python3 -c "import boto3,os;s=boto3.client('sqs',endpoint_url=os.environ['SQS_ENDPOINT_URL'],region_name='$REGION',aws_access_key_id=os.environ['MNQ_ACCESS_KEY'],aws_secret_access_key=os.environ['MNQ_SECRET_KEY']);print(s.get_queue_attributes(QueueUrl=os.environ['SQS_QUEUE_URL'],AttributeNames=['ApproximateNumberOfMessages'])['Attributes'])"
   # Issues ouvertes par Mirador :
   gh issue list --repo aboigues/k8t --label mirador
EOF
