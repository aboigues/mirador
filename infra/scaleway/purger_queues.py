"""Purge les queues MnQ (SQS-compatible) de Mirador.

À utiliser avant un re-test E2E, pour vider les messages « poison » restés
bloqués d'un test précédent (ex. events workflow_run non terminés qui faisaient
crasher le traitement → retry → DLQ). Purge la queue principale ET sa DLQ.

⚠️ `PurgeQueue` est IRRÉVERSIBLE et limité à un appel par minute et par queue
(sinon `PurgeQueueInProgress`).

Credentials MnQ : `MNQ_ACCESS_KEY` / `MNQ_SECRET_KEY` (distincts des clés S3).
Chargés depuis l'environnement — typiquement `source ~/.config/mirador/secrets.env`.
Endpoint par défaut : https://sqs.mnq.fr-par.scaleway.com (SQS_ENDPOINT_URL).

Usage :
    python purger_queues.py                 # purge mirador-webhooks + sa DLQ
    python purger_queues.py mirador-writes  # purge une autre paire (+ DLQ)
"""
from __future__ import annotations

import os
import sys

import boto3
from botocore.exceptions import ClientError

ENDPOINT = os.environ.get("SQS_ENDPOINT_URL", "https://sqs.mnq.fr-par.scaleway.com")
REGION = os.environ.get("SCALEWAY_REGION", "fr-par")


def _client():
    try:
        cle, secret = os.environ["MNQ_ACCESS_KEY"], os.environ["MNQ_SECRET_KEY"]
    except KeyError as exc:
        sys.exit(
            f"❌ {exc.args[0]} manquant. Charge les credentials MnQ d'abord :\n"
            f"   set -a && . ~/.config/mirador/secrets.env && set +a"
        )
    return boto3.client(
        "sqs", endpoint_url=ENDPOINT, region_name=REGION,
        aws_access_key_id=cle, aws_secret_access_key=secret,
    )


def _profondeur(sqs, url: str) -> str:
    """Nombre approximatif de messages (visibles + en vol), pour le rapport."""
    attrs = sqs.get_queue_attributes(
        QueueUrl=url,
        AttributeNames=["ApproximateNumberOfMessages",
                        "ApproximateNumberOfMessagesNotVisible"],
    )["Attributes"]
    return (f"{attrs['ApproximateNumberOfMessages']} visibles, "
            f"{attrs['ApproximateNumberOfMessagesNotVisible']} en vol")


def _purger(sqs, nom: str) -> None:
    try:
        url = sqs.get_queue_url(QueueName=nom)["QueueUrl"]
    except ClientError as exc:
        if exc.response["Error"]["Code"].endswith("NonExistentQueue"):
            print(f"⏭️  {nom} : inexistante, ignorée")
            return
        raise
    print(f"   {nom} avant purge : {_profondeur(sqs, url)}")
    sqs.purge_queue(QueueUrl=url)
    print(f"🧹 {nom} : purge demandée (effacement asynchrone jusqu'à 60 s)")


def main() -> None:
    base = sys.argv[1] if len(sys.argv) > 1 else "mirador-webhooks"
    sqs = _client()
    for nom in (base, f"{base}-dlq"):
        _purger(sqs, nom)


if __name__ == "__main__":
    main()
