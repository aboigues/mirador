"""Tests d'intégration : handlers Scaleway Serverless Functions.

Adaptent le format d'événement Scaleway (HTTP proxy / trigger de queue) à la
logique métier (traiter_webhook_brut, Traitement). I/O injectés (pas de réseau).
"""
import base64
import hashlib
import hmac
import json

import pytest

from src.gestionnaires.handlers import handler_traitement, handler_webhook

SECRET = "secret-handler-test"
DEPOT = "aboigues/k8t"


def _signer(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def _payload_workflow() -> bytes:
    return json.dumps({
        "action": "completed",
        "workflow_run": {"id": 12345678, "name": "CI", "conclusion": "failure",
                         "head_branch": "main", "head_repository": {"full_name": DEPOT}},
        "repository": {"full_name": DEPOT},
    }).encode()


def _event_http(body: bytes, *, event_type="workflow_run", signer=True, b64=False,
                headers_minuscules=False) -> dict:
    sig = _signer(body) if signer else "sha256=faux"
    entetes = {
        "X-GitHub-Event": event_type,
        "X-Hub-Signature-256": sig,
        "X-GitHub-Delivery": "550e8400-e29b-41d4-a716-446655440000",
    }
    if headers_minuscules:
        entetes = {k.lower(): v for k, v in entetes.items()}
    corps = base64.b64encode(body).decode() if b64 else body.decode()
    return {"httpMethod": "POST", "path": "/webhooks/github",
            "headers": entetes, "body": corps, "isBase64Encoded": b64}


class TestHandlerWebhook:
    def _enqueue_capture(self):
        msgs = []
        async def _cap(message):
            msgs.append(message)
        return msgs, _cap

    def test_workflow_run_valide_202(self):
        msgs, cap = self._enqueue_capture()
        resp = handler_webhook(_event_http(_payload_workflow()),
                               enqueue=cap, resoudre_responsables=lambda d: [], secret=SECRET)
        assert resp["statusCode"] == 202
        assert msgs[0]["type"] == "workflow_run" and msgs[0]["depot"] == DEPOT

    def test_signature_invalide_403(self):
        msgs, cap = self._enqueue_capture()
        resp = handler_webhook(_event_http(_payload_workflow(), signer=False),
                               enqueue=cap, resoudre_responsables=lambda d: [], secret=SECRET)
        assert resp["statusCode"] == 403 and msgs == []

    def test_event_ignore_204(self):
        msgs, cap = self._enqueue_capture()
        resp = handler_webhook(_event_http(_payload_workflow(), event_type="ping"),
                               enqueue=cap, resoudre_responsables=lambda d: [], secret=SECRET)
        assert resp["statusCode"] == 204 and msgs == []

    def test_body_base64(self):
        msgs, cap = self._enqueue_capture()
        resp = handler_webhook(_event_http(_payload_workflow(), b64=True),
                               enqueue=cap, resoudre_responsables=lambda d: [], secret=SECRET)
        assert resp["statusCode"] == 202 and msgs[0]["depot"] == DEPOT

    def test_headers_minuscules(self):
        # Scaleway peut normaliser les en-têtes en minuscules
        msgs, cap = self._enqueue_capture()
        resp = handler_webhook(_event_http(_payload_workflow(), headers_minuscules=True),
                               enqueue=cap, resoudre_responsables=lambda d: [], secret=SECRET)
        assert resp["statusCode"] == 202 and msgs[0]["depot"] == DEPOT


class _TraitementFake:
    def __init__(self):
        self.messages = []
    async def traiter(self, message):
        self.messages.append(message)


class TestHandlerTraitement:
    def test_message_via_body(self):
        t = _TraitementFake()
        msg = {"type": "workflow_run", "depot": DEPOT, "workflow_run_id": 1}
        handler_traitement({"body": json.dumps(msg)}, traitement=t)
        assert t.messages == [msg]

    def test_format_records(self):
        t = _TraitementFake()
        m1 = {"type": "workflow_run", "depot": DEPOT}
        m2 = {"type": "validation_humaine", "depot": DEPOT}
        handler_traitement({"Records": [{"body": json.dumps(m1)}, {"body": json.dumps(m2)}]},
                           traitement=t)
        assert t.messages == [m1, m2]

    def test_message_direct(self):
        t = _TraitementFake()
        msg = {"type": "workflow_run", "depot": DEPOT}
        handler_traitement(msg, traitement=t)
        assert t.messages == [msg]

    def test_retourne_200(self):
        t = _TraitementFake()
        resp = handler_traitement({"body": json.dumps({"type": "workflow_run"})}, traitement=t)
        assert resp["statusCode"] == 200
