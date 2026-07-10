"""Gestionnaire de webhooks GitHub entrants avec validation HMAC-SHA256."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import structlog
from fastapi import FastAPI, Request, Response

log = structlog.get_logger(__name__)

_EVENEMENTS_TRAITES = {"workflow_run", "issue_comment"}

# anomalie_id encodé en commentaire HTML invisible dans le corps de l'issue Mirador
_PATTERN_ANOMALIE_ID = re.compile(
    r"<!--\s*mirador:anomalie_id:([0-9a-fA-F-]{36})\s*-->"
)
_LABEL_MIRADOR = "mirador"
_MOTIF_MIN = 10  # longueur minimale du motif de rejet (contrat api-intervention.md)


def _valider_signature(body: bytes, signature_header: Optional[str], secret: str) -> bool:
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    hash_attendu = signature_header[len("sha256="):]
    hash_calcule = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(hash_calcule, hash_attendu)


def _construire_message_workflow_run(payload: dict[str, Any], delivery_id: str) -> dict[str, Any]:
    workflow_run = payload.get("workflow_run", {})
    repository = payload.get("repository", {})
    return {
        "version": "1",
        "type": "workflow_run",
        "delivery_id": delivery_id,
        "depot": repository.get("full_name", ""),
        "workflow_run_id": workflow_run.get("id"),
        "workflow_nom": workflow_run.get("name", ""),
        "conclusion": workflow_run.get("conclusion", ""),
        "head_branch": workflow_run.get("head_branch", ""),
        "head_sha": workflow_run.get("head_sha", ""),
        "run_started_at": workflow_run.get("created_at", ""),
        "run_completed_at": workflow_run.get("updated_at", ""),
        "jobs_url": workflow_run.get("jobs_url", ""),
        "html_url": workflow_run.get("html_url", ""),
        "recu_le": datetime.now(timezone.utc).isoformat(),
    }


def _parser_commande(corps: str) -> Optional[tuple[str, str]]:
    """Extrait (commande, motif) depuis le corps d'un commentaire.

    Retourne None si aucune commande reconnue ou si le motif de rejet est
    trop court. Le motif n'est pertinent que pour /rejeter.
    """
    corps = corps.strip()
    if corps == "/approuver" or corps.startswith("/approuver "):
        return ("approuver", "")
    if corps == "/rejeter" or corps.startswith("/rejeter "):
        motif = corps[len("/rejeter"):].strip()
        if len(motif) < _MOTIF_MIN:
            return None
        return ("rejeter", motif)
    return None


def _construire_message_issue_comment(
    payload: dict[str, Any],
    delivery_id: str,
    resoudre_responsables: Callable[[str], list[str]],
) -> Optional[dict[str, Any]]:
    """Valide et construit le message de validation humaine, ou None si à ignorer.

    Suit les étapes 1-5 du contrat webhook-entrant.md pour issue_comment.
    """
    if payload.get("action") != "created":
        return None

    issue = payload.get("issue", {})
    labels = {label.get("name") for label in issue.get("labels", [])}
    if _LABEL_MIRADOR not in labels:
        return None

    depot = payload.get("repository", {}).get("full_name", "")
    auteur = payload.get("comment", {}).get("user", {}).get("login", "")
    if auteur not in resoudre_responsables(depot):
        return None

    commande_parsee = _parser_commande(payload.get("comment", {}).get("body", ""))
    if commande_parsee is None:
        return None
    commande, motif = commande_parsee

    match = _PATTERN_ANOMALIE_ID.search(issue.get("body", ""))
    if match is None:
        return None

    return {
        "version": "1",
        "type": "validation_humaine",
        "delivery_id": delivery_id,
        "depot": depot,
        "issue_number": issue.get("number"),
        "anomalie_id": match.group(1),
        "commande": commande,
        "motif": motif,
        "acteur": auteur,
        "recu_le": datetime.now(timezone.utc).isoformat(),
    }


def creer_application(
    enqueue_stub: bool = False,
    enqueue: Optional[Callable[[dict[str, Any]], Any]] = None,
    resoudre_responsables: Optional[Callable[[str], list[str]]] = None,
) -> FastAPI:
    """Crée l'application FastAPI webhook.

    enqueue_stub=True : remplace l'enqueue SQS par un no-op (tests).
    enqueue : fonction d'enqueue explicite (prioritaire, pour capture en test).
    resoudre_responsables : renvoie la liste des responsables autorisés d'un dépôt.
        Défaut fail-safe : aucun responsable (toute commande ignorée).
    """
    app = FastAPI(title="Mirador Webhook")

    async def _enqueue_reel(message: dict[str, Any]) -> None:
        import boto3
        sqs = boto3.client(
            "sqs",
            endpoint_url=os.environ.get("SQS_ENDPOINT_URL", "https://mq-sqs.scaleway.com"),
            region_name=os.environ.get("AWS_REGION", "fr-par"),
        )
        sqs.send_message(
            QueueUrl=os.environ["SQS_QUEUE_URL"],
            MessageBody=json.dumps(message),
            MessageDeduplicationId=message.get("delivery_id", ""),
            MessageGroupId="webhooks",
        )

    async def _enqueue_stub(message: dict[str, Any]) -> None:
        log.info("webhook.enqueue_stub", message=message)

    if enqueue is not None:
        _enqueue: Callable[[dict[str, Any]], Any] = enqueue
    else:
        _enqueue = _enqueue_stub if enqueue_stub else _enqueue_reel

    _resoudre = resoudre_responsables if resoudre_responsables is not None else (lambda _depot: [])

    @app.post("/webhooks/github")
    async def recevoir_webhook(request: Request) -> Response:
        secret = os.environ.get("WEBHOOK_SECRET", "")
        body = await request.body()

        signature = request.headers.get("X-Hub-Signature-256")
        if not _valider_signature(body, signature, secret):
            log.warning("webhook.signature_invalide",
                        delivery_id=request.headers.get("X-GitHub-Delivery"))
            return Response(status_code=403, content='{"erreur": "Signature invalide"}',
                            media_type="application/json")

        event_type = request.headers.get("X-GitHub-Event", "")
        if event_type not in _EVENEMENTS_TRAITES:
            return Response(status_code=204)

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return Response(status_code=400, content='{"erreur": "JSON malformé"}',
                            media_type="application/json")

        delivery_id = request.headers.get("X-GitHub-Delivery", "")

        if event_type == "workflow_run":
            message = _construire_message_workflow_run(payload, delivery_id)
        else:  # issue_comment
            message = _construire_message_issue_comment(payload, delivery_id, _resoudre)
            if message is None:
                # Commande ignorée silencieusement (non-responsable, mal formée, etc.)
                return Response(status_code=204)

        await _enqueue(message)

        log.info("webhook.accepte",
                 delivery_id=delivery_id,
                 type=message["type"],
                 depot=message["depot"])

        return Response(status_code=202)

    return app
