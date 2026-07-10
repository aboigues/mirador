#!/usr/bin/env bash
# Met à jour le CODE d'une fonction Scaleway existante (build zip → upload → deploy).
# Ne touche PAS aux variables d'environnement / secrets (posées par deploy.sh).
# C'est le cœur du déploiement continu (workflow GitHub Actions).
#
# Auth : profil scw en local (SCW_PROFILE=telemach par défaut) OU credentials
# d'environnement (SCW_ACCESS_KEY / SCW_SECRET_KEY) en CI — dans ce cas exporter
# SCW_PROFILE="" pour ne pas passer --profile.
#
# Usage : ./update_code.sh <function-id>
set -euo pipefail

PROFILE="${SCW_PROFILE-telemach}"
REGION="${SCW_REGION:-fr-par}"
FN_ID="${1:?function-id requis}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ZIP="$(mktemp -d)/mirador.zip"

scw() {
  if [ -n "$PROFILE" ]; then command scw --profile "$PROFILE" "$@"; else command scw "$@"; fi
}

echo "== Build du zip (handler.py + requirements.txt + src) =="
( cd "$ROOT" && zip -qr "$ZIP" handler.py requirements.txt src \
    -x '*/__pycache__/*' '*.pyc' '*.pyo' )
SIZE=$(stat -c%s "$ZIP")
echo "   $ZIP ($SIZE octets)"

echo "== Upload =="
URL=$(scw function function get-upload-url function-id="$FN_ID" \
        content-length="$SIZE" region="$REGION" -o json \
      | python3 -c 'import sys,json;print(json.load(sys.stdin)["url"])')
curl -fsS -X PUT -H "Content-Type: application/octet-stream" \
     --upload-file "$ZIP" "$URL"
echo "   uploadé"

echo "== Déploiement =="
scw function function deploy function-id="$FN_ID" region="$REGION" >/dev/null
echo "✅ code déployé (function-id=$FN_ID)"
