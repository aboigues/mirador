"""Crée les queues MnQ (SQS-compatible) de Mirador, idempotent.

Deux paires de queues STANDARD, chacune avec sa dead-letter queue :
- mirador-webhooks  : événements GitHub validés (VisibilityTimeout 300s)
- mirador-writes    : écritures sérialisées vers le writer d'audit (60s)

Queues STANDARD (non FIFO) : les triggers scw_sqs de Scaleway Functions ne
consomment pas les files FIFO. La déduplication est assurée côté consommateur
(idempotence par delivery_id dans le pipeline de traitement).

Credentials : via l'environnement (MNQ_ACCESS_KEY / MNQ_SECRET_KEY),
ou un fichier JSON de credentials MnQ passé en argument (clés access_key/secret_key).
Endpoint par défaut : https://sqs.mnq.fr-par.scaleway.com (surchargeable par SQS_ENDPOINT_URL).

Usage :
    python creer_queues.py [chemin-credentials.json]
"""
from __future__ import annotations

import json
import os
import sys

import boto3

ENDPOINT = os.environ.get("SQS_ENDPOINT_URL", "https://sqs.mnq.fr-par.scaleway.com")
REGION = os.environ.get("SCALEWAY_REGION", "fr-par")
MAX_RECEIVE_COUNT = 3
RETENTION = "86400"  # 24 h

# (nom logique, VisibilityTimeout secondes)
QUEUES = [
    ("mirador-webhooks", "300"),
    ("mirador-writes", "60"),
]


def _client():
    if len(sys.argv) > 1:
        creds = json.load(open(sys.argv[1]))
        return boto3.client(
            "sqs", endpoint_url=ENDPOINT, region_name=REGION,
            aws_access_key_id=creds["access_key"], aws_secret_access_key=creds["secret_key"],
        )
    return boto3.client(
        "sqs", endpoint_url=ENDPOINT, region_name=REGION,
        aws_access_key_id=os.environ["MNQ_ACCESS_KEY"],
        aws_secret_access_key=os.environ["MNQ_SECRET_KEY"],
    )


def _creer(sqs, nom: str, attributs: dict[str, str]) -> tuple[str, str]:
    """Crée une queue STANDARD (idempotent) et retourne (url, arn).

    STANDARD et non FIFO : les triggers scw_sqs de Scaleway Functions ne
    consomment pas les files FIFO. La déduplication est assurée côté consommateur
    (idempotence par delivery_id dans le pipeline de traitement).
    """
    url = sqs.create_queue(QueueName=nom, Attributes=attributs)["QueueUrl"]
    arn = sqs.get_queue_attributes(QueueUrl=url, AttributeNames=["QueueArn"])[
        "Attributes"]["QueueArn"]
    return url, arn


def main() -> None:
    sqs = _client()
    resultats: dict[str, str] = {}
    for nom, visibilite in QUEUES:
        _, dlq_arn = _creer(sqs, f"{nom}-dlq", {"MessageRetentionPeriod": RETENTION})
        url, _ = _creer(sqs, nom, {
            "VisibilityTimeout": visibilite,
            "MessageRetentionPeriod": RETENTION,
            "RedrivePolicy": json.dumps(
                {"deadLetterTargetArn": dlq_arn, "maxReceiveCount": MAX_RECEIVE_COUNT}
            ),
        })
        resultats[nom] = url
        print(f"✅ {nom} (+ DLQ) → {url}")

    print("\n# À reporter dans les variables d'environnement de la fonction :")
    print(f"SQS_QUEUE_URL={resultats['mirador-webhooks']}")
    print(f"SQS_WRITES_QUEUE_URL={resultats['mirador-writes']}")


if __name__ == "__main__":
    main()
