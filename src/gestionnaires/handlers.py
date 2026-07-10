"""Handlers Scaleway Serverless Functions : adaptateurs entre le format d'événement
Scaleway et la logique métier de Mirador.

- handler_webhook : fonction HTTP (récepteur des webhooks GitHub → file MnQ).
- handler_traitement : fonction déclenchée par la file (→ pipeline de traitement).

Le format exact du trigger de queue Scaleway est tolérant (body / Records /
message direct) pour absorber les variations ; à confirmer au premier déploiement.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
from typing import Any, Callable, Optional

import structlog

from src.gestionnaires.webhook import enqueuer_sqs, traiter_webhook_brut

log = structlog.get_logger(__name__)


def _entetes_minuscules(event: dict[str, Any]) -> dict[str, str]:
    return {str(k).lower(): v for k, v in (event.get("headers") or {}).items()}


def _corps_bytes(event: dict[str, Any]) -> bytes:
    corps = event.get("body") or ""
    if event.get("isBase64Encoded"):
        return base64.b64decode(corps)
    return corps.encode("utf-8") if isinstance(corps, str) else bytes(corps)


def handler_webhook(
    event: dict[str, Any],
    context: Any = None,
    *,
    enqueue: Optional[Callable[[dict[str, Any]], Any]] = None,
    resoudre_responsables: Optional[Callable[[str], list[str]]] = None,
    secret: Optional[str] = None,
) -> dict[str, Any]:
    """Récepteur HTTP des webhooks GitHub. Valide, puis enqueue dans MnQ."""
    if resoudre_responsables is None:
        from src.gestionnaires.entree import ConfigDepots
        resoudre_responsables = ConfigDepots.depuis_env().responsables
    enqueue = enqueue or enqueuer_sqs
    secret = secret if secret is not None else os.environ.get("WEBHOOK_SECRET", "")

    entetes = _entetes_minuscules(event)
    delivery_id = entetes.get("x-github-delivery", "")

    code, message = traiter_webhook_brut(
        _corps_bytes(event),
        entetes.get("x-github-event", ""),
        entetes.get("x-hub-signature-256"),
        delivery_id,
        secret,
        resoudre_responsables,
    )
    if message is not None:
        asyncio.run(enqueue(message))
        log.info("handler_webhook.accepte", delivery_id=delivery_id, type=message["type"])
    return {"statusCode": code, "body": ""}


def _extraire_messages(event: Any) -> list[dict[str, Any]]:
    """Extrait les messages métier d'un événement de trigger de queue (tolérant)."""
    if isinstance(event, dict):
        if "Records" in event:
            return [json.loads(r["body"]) for r in event["Records"]]
        if "body" in event:
            corps = event["body"]
            return [corps if isinstance(corps, dict) else json.loads(corps)]
        if "type" in event:  # message métier passé directement
            return [event]
    if isinstance(event, str):
        return [json.loads(event)]
    return []


def handler_traitement(
    event: Any,
    context: Any = None,
    *,
    traitement: Any = None,
) -> dict[str, Any]:
    """Consomme un (ou des) message(s) de la file et exécute le pipeline."""
    if traitement is None:
        from src.gestionnaires.entree import construire_traitement_reel
        traitement = construire_traitement_reel()

    messages = _extraire_messages(event)
    for message in messages:
        asyncio.run(traitement.traiter(message))
    log.info("handler_traitement.traite", nombre=len(messages))
    return {"statusCode": 200}
