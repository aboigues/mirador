"""Gestionnaire de webhooks GitHub entrants avec validation HMAC-SHA256."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import boto3
import structlog
from fastapi import FastAPI, Request, Response

log = structlog.get_logger(__name__)

_SQS_ENDPOINT_DEFAUT = "https://sqs.mnq.fr-par.scaleway.com"


async def enqueuer_sqs(message: dict[str, Any], env: Optional[dict[str, str]] = None) -> None:
    """Publie un message dans la queue MnQ (STANDARD).

    MnQ exige ses propres credentials (MNQ_ACCESS_KEY / MNQ_SECRET_KEY), distincts
    des clés IAM du bucket Object Storage. Queue STANDARD (non-FIFO) : les triggers
    scw_sqs de Scaleway Functions ne consomment pas les files FIFO. On n'envoie donc
    ni MessageGroupId ni MessageDeduplicationId (invalides hors FIFO) ; la
    déduplication est assurée côté consommateur, par delivery_id (idempotence du
    pipeline de traitement).
    """
    env = env if env is not None else os.environ
    sqs = boto3.client(
        "sqs",
        endpoint_url=env.get("SQS_ENDPOINT_URL", _SQS_ENDPOINT_DEFAUT),
        region_name=env.get("SCALEWAY_REGION", "fr-par"),
        aws_access_key_id=env["MNQ_ACCESS_KEY"],
        aws_secret_access_key=env["MNQ_SECRET_KEY"],
    )
    sqs.send_message(
        QueueUrl=env["SQS_QUEUE_URL"],
        MessageBody=json.dumps(message),
    )

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


def _run_du_depot_surveille(payload: dict[str, Any]) -> bool:
    """Vrai si le code exécuté par le run vient du dépôt surveillé lui-même.

    Un run déclenché par une PR venant d'un fork produit des logs contrôlés par un
    inconnu : les confier au correcteur coûte une analyse et expose à une
    injection de prompt (voir SECURITY.md). Dépôt de tête absent ou nul (fork
    supprimé) : provenance inconnue, donc refusé.
    """
    depot = payload.get("repository", {}).get("full_name", "")
    tete = (payload.get("workflow_run", {}).get("head_repository") or {}).get("full_name", "")
    return bool(depot) and tete.lower() == depot.lower()


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


def traiter_webhook_brut(
    body: bytes,
    event_type: str,
    signature: Optional[str],
    delivery_id: str,
    secret: str,
    resoudre_responsables: Callable[[str], list[str]],
) -> tuple[int, Optional[dict[str, Any]]]:
    """Logique métier du webhook, indépendante du transport (FastAPI / serverless).

    Retourne (code_http, message_à_enqueuer_ou_None). Le message n'est présent
    que pour un code 202 (webhook valide à traiter).
    """
    if not _valider_signature(body, signature, secret):
        return 403, None
    if event_type not in _EVENEMENTS_TRAITES:
        return 204, None
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return 400, None

    if event_type == "workflow_run":
        # GitHub émet plusieurs events par run (requested / in_progress /
        # completed). Seul l'event terminé a des logs exploitables : ignorer
        # les autres pour ne pas faire échouer le traitement en aval.
        if payload.get("action") != "completed":
            return 204, None
        if not _run_du_depot_surveille(payload):
            log.info(
                "webhook.run_externe_ignore",
                delivery_id=delivery_id,
                depot=payload.get("repository", {}).get("full_name", ""),
                depot_tete=(payload.get("workflow_run", {}).get("head_repository") or {}).get("full_name"),
            )
            return 204, None
        return 202, _construire_message_workflow_run(payload, delivery_id)

    message = _construire_message_issue_comment(payload, delivery_id, resoudre_responsables)
    if message is None:
        return 204, None
    return 202, message


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
        await enqueuer_sqs(message)

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
        delivery_id = request.headers.get("X-GitHub-Delivery", "")

        code, message = traiter_webhook_brut(
            body,
            request.headers.get("X-GitHub-Event", ""),
            request.headers.get("X-Hub-Signature-256"),
            delivery_id,
            secret,
            _resoudre,
        )
        if code == 403:
            log.warning("webhook.signature_invalide", delivery_id=delivery_id)
        if message is not None:
            await _enqueue(message)
            log.info("webhook.accepte", delivery_id=delivery_id,
                     type=message["type"], depot=message["depot"])
        return Response(status_code=code)

    return app
