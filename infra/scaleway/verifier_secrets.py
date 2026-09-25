#!/usr/bin/env python3
"""Pré-vol : valide les secrets présents dans l'environnement, sans rien écrire.

À lancer AVANT de déployer une rotation (cf. docs/rotation-secrets.md) : une clé
fautive se découvre ici, pas en production. Toutes les vérifications sont en
lecture seule — sauf MnQ, qui publie un message inerte (type inconnu du
pipeline, donc ignoré par le traitement).

    set -a && . ~/.config/mirador/secrets.env && set +a
    .venv/bin/python infra/scaleway/verifier_secrets.py

Aucune valeur de secret n'est affichée : seulement un verdict par clé.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

INSTALLATION_ID = 145689603
BUCKET = os.environ.get("BUCKET_NAME", "telemach-mirador-audit")
REGION = os.environ.get("SCW_REGION", "fr-par")
S3_ENDPOINT = os.environ.get("S3_ENDPOINT_URL", "https://s3.fr-par.scw.cloud")
SQS_ENDPOINT = os.environ.get("SQS_ENDPOINT_URL", "https://sqs.mnq.fr-par.scaleway.com")


def _requis(*noms: str) -> list[str]:
    return [n for n in noms if not os.environ.get(n)]


async def _github_app() -> str:
    manquants = _requis("GITHUB_APP_ID", "GITHUB_APP_PRIVATE_KEY")
    if manquants:
        return f"⚠️  ignoré — absent de l'env : {', '.join(manquants)}"
    from src.infrastructure.github.app_auth import GitHubAppAuth

    auth = GitHubAppAuth(os.environ["GITHUB_APP_ID"], os.environ["GITHUB_APP_PRIVATE_KEY"])
    await auth.obtenir_token_installation(INSTALLATION_ID)
    return "✓ token d'installation obtenu (JWT RS256 accepté)"


def _anthropic() -> str:
    if _requis("ANTHROPIC_API_KEY"):
        return "⚠️  ignoré — ANTHROPIC_API_KEY absent de l'env"
    import anthropic

    client = anthropic.Anthropic()
    # count_tokens : valide l'auth sans consommer de tokens facturables.
    reponse = client.messages.count_tokens(
        model="claude-haiku-4-5",
        messages=[{"role": "user", "content": "ping"}],
    )
    return f"✓ clé valide ({reponse.input_tokens} tokens comptés)"


def _object_storage() -> str:
    if _requis("SCW_S3_ACCESS_KEY", "SCW_S3_SECRET_KEY"):
        return "⚠️  ignoré — SCW_S3_ACCESS_KEY / SCW_S3_SECRET_KEY absents"
    import boto3

    s3 = boto3.client(
        "s3", endpoint_url=S3_ENDPOINT, region_name=REGION,
        aws_access_key_id=os.environ["SCW_S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["SCW_S3_SECRET_KEY"],
    )
    s3.head_object(Bucket=BUCKET, Key="mirador.db")
    return f"✓ lecture du journal d'audit OK (bucket {BUCKET})"


def _mnq() -> str:
    manquants = _requis("MNQ_ACCESS_KEY", "MNQ_SECRET_KEY", "SQS_QUEUE_URL")
    if manquants:
        return f"⚠️  ignoré — absent de l'env : {', '.join(manquants)}"
    import boto3

    # Les credentials MnQ sont DISTINCTES des credentials Object Storage : on ne
    # peut pas réutiliser les SCW_S3_* du bucket.
    sqs = boto3.client(
        "sqs",
        endpoint_url=SQS_ENDPOINT,
        region_name=REGION,
        aws_access_key_id=os.environ["MNQ_ACCESS_KEY"],
        aws_secret_access_key=os.environ["MNQ_SECRET_KEY"],
    )
    # Message inerte : `type` inconnu du pipeline → traitement l'ignore.
    sqs.send_message(
        QueueUrl=os.environ["SQS_QUEUE_URL"],
        MessageBody='{"type":"pre-vol-rotation"}',
    )
    return "✓ publication sur la file OK (message inerte, ignoré par le pipeline)"


def _webhook_secret() -> str:
    valeur = os.environ.get("WEBHOOK_SECRET", "")
    if not valeur:
        return "⚠️  ignoré — WEBHOOK_SECRET absent de l'env"
    if len(valeur) < 32:
        return f"✗ trop court ({len(valeur)} caractères) — attendu ≥ 32 (openssl rand -hex 32)"
    # Rien à appeler : sa validité ne se prouve qu'en le comparant à celui de la
    # GitHub App, ce qui n'est pas lisible par l'API. Seul le format est vérifiable.
    return f"✓ format plausible ({len(valeur)} caractères) — à confronter à la GitHub App"


async def main() -> int:
    verifications = [
        ("GitHub App (clé privée)", _github_app, True),
        ("Anthropic (clé API)", _anthropic, False),
        ("Object Storage (bucket)", _object_storage, False),
        ("MnQ (file de messages)", _mnq, False),
        ("Webhook secret (format)", _webhook_secret, False),
    ]
    echecs = 0
    for libelle, fonction, asynchrone in verifications:
        try:
            resultat = await fonction() if asynchrone else fonction()
        except Exception as err:  # noqa: BLE001 — on veut TOUT rapporter, pas s'arrêter
            resultat = f"✗ ÉCHEC — {type(err).__name__}: {err}"
            echecs += 1
        else:
            echecs += resultat.startswith("✗")
        print(f"  {libelle:26} {resultat}")

    print()
    if echecs:
        print(f"❌ {echecs} vérification(s) en échec — NE PAS déployer en l'état.")
        return 1
    print("✅ Tout est valide — la config peut être déployée via deploy-config.yml.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
