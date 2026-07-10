"""Crée les queues MnQ (SQS-compatible) de Mirador, idempotent.

Deux paires de queues FIFO, chacune avec sa dead-letter queue :
- mirador-webhooks(.fifo)  : événements GitHub validés (VisibilityTimeout 300s)
- mirador-writes(.fifo)    : écritures sérialisées vers le writer d'audit (60s)

Les queues sont FIFO car les producteurs fournissent MessageGroupId +
MessageDeduplicationId (déduplication par delivery_id / delivery interne).

Credentials : via l'environnement (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY),
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
REGION = os.environ.get("AWS_REGION", "fr-par")
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
    return boto3.client("sqs", endpoint_url=ENDPOINT, region_name=REGION)


def _creer(sqs, nom: str, attributs: dict[str, str]) -> tuple[str, str]:
    """Crée une queue FIFO (idempotent) et retourne (url, arn)."""
    url = sqs.create_queue(
        QueueName=f"{nom}.fifo",
        Attributes={"FifoQueue": "true", **attributs},
    )["QueueUrl"]
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
        print(f"✅ {nom}.fifo (+ DLQ) → {url}")

    print("\n# À reporter dans les variables d'environnement de la fonction :")
    print(f"SQS_QUEUE_URL={resultats['mirador-webhooks']}")
    print(f"SQS_WRITES_QUEUE_URL={resultats['mirador-writes']}")


if __name__ == "__main__":
    main()
