"""Gestionnaire de webhooks GitHub entrants avec validation HMAC-SHA256."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import structlog
from fastapi import FastAPI, Request, Response

log = structlog.get_logger(__name__)

_EVENEMENTS_TRAITES = {"workflow_run"}


def _valider_signature(body: bytes, signature_header: Optional[str], secret: str) -> bool:
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    hash_attendu = signature_header[len("sha256="):]
    hash_calcule = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(hash_calcule, hash_attendu)


def creer_application(enqueue_stub: bool = False) -> FastAPI:
    """Crée l'application FastAPI webhook.

    enqueue_stub=True : remplace l'enqueue SQS par un no-op (tests d'intégration).
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

    enqueue: Callable[[dict[str, Any]], Any] = _enqueue_stub if enqueue_stub else _enqueue_reel

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
        workflow_run = payload.get("workflow_run", {})
        repository = payload.get("repository", {})

        message = {
            "version": "1",
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

        await enqueue(message)

        log.info("webhook.accepte",
                 delivery_id=delivery_id,
                 depot=message["depot"],
                 workflow_run_id=message["workflow_run_id"],
                 conclusion=message["conclusion"])

        return Response(status_code=202)

    return app
