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
REGION="${SCALEWAY_REGION:-fr-par}"
FN_ID="${1:?function-id requis}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ZIP="$(mktemp -d)/mirador.zip"

scw() {
  if [ -n "$PROFILE" ]; then command scw --profile "$PROFILE" "$@"; else command scw "$@"; fi
}

echo "== Build du zip (code + dépendances vendorées) =="
# Scaleway ne build pas requirements.txt en déploiement bas-niveau → on vendorise
# les dépendances dans le zip. /tmp est sur le FS Linux (rapide, hors drvfs).
BUILD="$(mktemp -d)"
cp "$ROOT/handler.py" "$BUILD/"
cp -r "$ROOT/src" "$BUILD/src"
PIP="${PIP:-$ROOT/.venv/bin/pip}"
echo "   installation des dépendances (wheels musllinux, python 3.12)…"
# Le runtime Scaleway Functions est Alpine/musl → wheels musllinux obligatoires
# (les wheels manylinux/glibc produisent un .so introuvable au runtime).
"$PIP" install -r "$ROOT/requirements.txt" -t "$BUILD" --quiet --no-compile \
    --only-binary=:all: --python-version 3.12 --implementation cp --abi cp312 \
    --platform musllinux_1_2_x86_64 --platform musllinux_1_1_x86_64
# NB : requirements.txt volontairement ABSENT du zip → Scaleway ne relance pas
# de build par-dessus les dépendances déjà vendorées (sinon les .so sont perdus).
( cd "$BUILD" && zip -qr "$ZIP" . -x '*/__pycache__/*' '*.pyc' '*.pyo' )
SIZE=$(stat -c%s "$ZIP")
echo "   $ZIP ($((SIZE/1024/1024)) Mo)"

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
