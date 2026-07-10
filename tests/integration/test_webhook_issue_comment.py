"""Tests d'intégration : événement issue_comment (validation humaine /approuver /rejeter).

Suit le contrat contracts/webhook-entrant.md (étapes 1-6) et
contracts/api-intervention.md (commandes, motif ≥ 10 caractères).
"""
import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from src.gestionnaires.webhook import creer_application


WEBHOOK_SECRET = "secret-de-test-integration"
ANOMALIE_ID = "550e8400-e29b-41d4-a716-446655440000"
RESPONSABLE = "aboigues"


def _signer(body: bytes, secret: str) -> str:
    mac = hmac.new(secret.encode(), body, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


def _corps_issue(anomalie_id: str | None = ANOMALIE_ID) -> str:
    corps = "## Anomalie détectée\n\nRelance proposée.\n"
    if anomalie_id is not None:
        corps += f"\n<!-- mirador:anomalie_id:{anomalie_id} -->\n"
    return corps


def _payload_issue_comment(
    *,
    action: str = "created",
    labels: list[str] | None = None,
    auteur: str = RESPONSABLE,
    commentaire: str = "/approuver",
    corps_issue: str | None = None,
) -> dict:
    if labels is None:
        labels = ["mirador", "high"]
    if corps_issue is None:
        corps_issue = _corps_issue()
    return {
        "action": action,
        "issue": {
            "number": 42,
            "title": "[Mirador] Anomalie HIGH — CI sur aboigues/k8t",
            "labels": [{"name": nom} for nom in labels],
            "body": corps_issue,
        },
        "comment": {
            "id": 987654,
            "user": {"login": auteur},
            "body": commentaire,
        },
        "repository": {"full_name": "aboigues/k8t"},
    }


@pytest.fixture
def messages() -> list[dict]:
    return []


@pytest.fixture
def client(monkeypatch, messages):
    monkeypatch.setenv("WEBHOOK_SECRET", WEBHOOK_SECRET)

    async def _capture(message: dict) -> None:
        messages.append(message)

    app = creer_application(
        enqueue=_capture,
        resoudre_responsables=lambda depot: [RESPONSABLE],
    )
    return TestClient(app, raise_server_exceptions=False)


def _poster(client, payload: dict):
    body = json.dumps(payload).encode()
    return client.post(
        "/webhooks/github",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "issue_comment",
            "X-Hub-Signature-256": _signer(body, WEBHOOK_SECRET),
            "X-GitHub-Delivery": "550e8400-e29b-41d4-a716-446655440099",
        },
    )


class TestIssueCommentApprouver:
    def test_approuver_par_responsable_retourne_202(self, client, messages):
        resp = _poster(client, _payload_issue_comment(commentaire="/approuver"))
        assert resp.status_code == 202
        assert len(messages) == 1
        msg = messages[0]
        assert msg["type"] == "validation_humaine"
        assert msg["commande"] == "approuver"
        assert msg["anomalie_id"] == ANOMALIE_ID
        assert msg["acteur"] == RESPONSABLE
        assert msg["depot"] == "aboigues/k8t"
        assert msg["issue_number"] == 42

    def test_approuver_avec_texte_supplementaire(self, client, messages):
        resp = _poster(client, _payload_issue_comment(commentaire="/approuver merci"))
        assert resp.status_code == 202
        assert messages[0]["commande"] == "approuver"


class TestIssueCommentRejeter:
    def test_rejeter_avec_motif_valide_retourne_202(self, client, messages):
        motif = "Le correctif cible le mauvais service, intervention manuelle"
        resp = _poster(client, _payload_issue_comment(commentaire=f"/rejeter {motif}"))
        assert resp.status_code == 202
        assert messages[0]["commande"] == "rejeter"
        assert messages[0]["motif"] == motif

    def test_rejeter_motif_trop_court_est_ignore(self, client, messages):
        resp = _poster(client, _payload_issue_comment(commentaire="/rejeter court"))
        assert resp.status_code == 204
        assert messages == []

    def test_rejeter_sans_motif_est_ignore(self, client, messages):
        resp = _poster(client, _payload_issue_comment(commentaire="/rejeter"))
        assert resp.status_code == 204
        assert messages == []


class TestIssueCommentIgnore:
    def test_action_non_created_retourne_204(self, client, messages):
        resp = _poster(client, _payload_issue_comment(action="edited"))
        assert resp.status_code == 204
        assert messages == []

    def test_sans_label_mirador_retourne_204(self, client, messages):
        resp = _poster(client, _payload_issue_comment(labels=["bug"]))
        assert resp.status_code == 204
        assert messages == []

    def test_auteur_non_responsable_retourne_204(self, client, messages):
        resp = _poster(client, _payload_issue_comment(auteur="intrus"))
        assert resp.status_code == 204
        assert messages == []

    def test_commande_inconnue_retourne_204(self, client, messages):
        resp = _poster(client, _payload_issue_comment(commentaire="bonjour"))
        assert resp.status_code == 204
        assert messages == []

    def test_anomalie_id_absent_retourne_204(self, client, messages):
        payload = _payload_issue_comment(corps_issue=_corps_issue(anomalie_id=None))
        resp = _poster(client, payload)
        assert resp.status_code == 204
        assert messages == []


class TestIssueCommentSecurite:
    def test_signature_invalide_retourne_403(self, client, messages):
        body = json.dumps(_payload_issue_comment()).encode()
        resp = client.post(
            "/webhooks/github",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "issue_comment",
                "X-Hub-Signature-256": "sha256=deadbeef",
                "X-GitHub-Delivery": "550e8400-e29b-41d4-a716-446655440098",
            },
        )
        assert resp.status_code == 403
        assert messages == []
