"""Tests d'intégration : validation HMAC du webhook GitHub (sans mock)."""
import hashlib
import hmac
import json
import pytest
from fastapi.testclient import TestClient

from src.gestionnaires.webhook import creer_application


WEBHOOK_SECRET = "secret-de-test-integration"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setenv("SQS_QUEUE_URL", "http://localhost:4566/000000000000/mirador-webhooks")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    app = creer_application(enqueue_stub=True)
    return TestClient(app, raise_server_exceptions=False)


def _payload_valide() -> dict:
    return {
        "action": "completed",
        "workflow_run": {
            "id": 12345678,
            "name": "CI",
            "head_branch": "main",
            "head_sha": "abc123",
            "status": "completed",
            "conclusion": "failure",
            "created_at": "2026-05-29T10:00:00Z",
            "updated_at": "2026-05-29T10:05:00Z",
            "html_url": "https://github.com/aboigues/k8t/actions/runs/12345678",
            "jobs_url": "https://api.github.com/repos/aboigues/k8t/actions/runs/12345678/jobs",
            "head_repository": {"full_name": "aboigues/k8t", "fork": False},
        },
        "repository": {
            "id": 999,
            "full_name": "aboigues/k8t",
            "owner": {"login": "aboigues"},
        },
    }


def _signer(body: bytes, secret: str) -> str:
    mac = hmac.new(secret.encode(), body, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


class TestWebhookHMAC:
    def test_webhook_valide_retourne_202(self, client):
        body = json.dumps(_payload_valide()).encode()
        signature = _signer(body, WEBHOOK_SECRET)
        resp = client.post(
            "/webhooks/github",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "workflow_run",
                "X-Hub-Signature-256": signature,
                "X-GitHub-Delivery": "550e8400-e29b-41d4-a716-446655440000",
            },
        )
        assert resp.status_code == 202

    def test_signature_absente_retourne_403(self, client):
        body = json.dumps(_payload_valide()).encode()
        resp = client.post(
            "/webhooks/github",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "workflow_run",
                "X-GitHub-Delivery": "550e8400-e29b-41d4-a716-446655440001",
            },
        )
        assert resp.status_code == 403

    def test_signature_incorrecte_retourne_403(self, client):
        body = json.dumps(_payload_valide()).encode()
        resp = client.post(
            "/webhooks/github",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "workflow_run",
                "X-Hub-Signature-256": "sha256=deadbeefdeadbeef",
                "X-GitHub-Delivery": "550e8400-e29b-41d4-a716-446655440002",
            },
        )
        assert resp.status_code == 403
        # La réponse NE DOIT PAS révéler le secret ni le hash calculé
        assert WEBHOOK_SECRET not in resp.text
        assert "sha256=" not in resp.text.lower() or resp.text.count("sha256=") == 0

    def test_signature_mauvais_secret_retourne_403(self, client):
        body = json.dumps(_payload_valide()).encode()
        mauvaise_signature = _signer(body, "mauvais-secret")
        resp = client.post(
            "/webhooks/github",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "workflow_run",
                "X-Hub-Signature-256": mauvaise_signature,
                "X-GitHub-Delivery": "550e8400-e29b-41d4-a716-446655440003",
            },
        )
        assert resp.status_code == 403

    def test_event_ignore_retourne_204(self, client):
        body = json.dumps({"zen": "Practicality beats purity."}).encode()
        signature = _signer(body, WEBHOOK_SECRET)
        resp = client.post(
            "/webhooks/github",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "ping",
                "X-Hub-Signature-256": signature,
                "X-GitHub-Delivery": "550e8400-e29b-41d4-a716-446655440004",
            },
        )
        assert resp.status_code == 204

    @pytest.mark.parametrize("action", ["requested", "in_progress"])
    def test_workflow_run_non_termine_retourne_204(self, client, action):
        # GitHub émet plusieurs events workflow_run par run (requested /
        # in_progress / completed). Les non terminés n'ont pas encore de logs
        # disponibles : les enqueue ferait crasher le traitement en aval.
        payload = _payload_valide()
        payload["action"] = action
        payload["workflow_run"]["status"] = action
        payload["workflow_run"]["conclusion"] = None
        body = json.dumps(payload).encode()
        signature = _signer(body, WEBHOOK_SECRET)
        resp = client.post(
            "/webhooks/github",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "workflow_run",
                "X-Hub-Signature-256": signature,
                "X-GitHub-Delivery": "550e8400-e29b-41d4-a716-446655440006",
            },
        )
        assert resp.status_code == 204

    def test_json_malforme_retourne_400(self, client):
        body = b"{ invalid json }"
        signature = _signer(body, WEBHOOK_SECRET)
        resp = client.post(
            "/webhooks/github",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "workflow_run",
                "X-Hub-Signature-256": signature,
                "X-GitHub-Delivery": "550e8400-e29b-41d4-a716-446655440005",
            },
        )
        assert resp.status_code == 400
