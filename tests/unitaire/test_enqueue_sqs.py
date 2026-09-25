"""Tests unitaires : enqueue SQS/MnQ avec credentials dédiés.

Découvert au provisioning réel : MnQ (SQS) exige ses propres credentials,
distincts des clés IAM utilisées pour Object Storage (S3). L'enqueue doit donc
utiliser MNQ_ACCESS_KEY / MNQ_SECRET_KEY, pas les SCALEWAY_S3_* du bucket.
"""
import boto3
import pytest

from src.gestionnaires.webhook import enqueuer_sqs


@pytest.fixture
def captures(monkeypatch):
    etat: dict = {}

    class _FakeSqs:
        def send_message(self, **kw):
            etat["send"] = kw

    def _fake_client(service, **kw):
        etat["client"] = {"service": service, **kw}
        return _FakeSqs()

    monkeypatch.setattr(boto3, "client", _fake_client)
    monkeypatch.setenv("MNQ_ACCESS_KEY", "mnq-ak")
    monkeypatch.setenv("MNQ_SECRET_KEY", "mnq-sk")
    monkeypatch.setenv(
        "SQS_QUEUE_URL",
        "https://sqs.mnq.fr-par.scaleway.com/project-x/mirador-webhooks",
    )
    return etat


async def test_client_utilise_les_credentials_mnq(captures):
    await enqueuer_sqs({"delivery_id": "d1", "type": "workflow_run"})
    client = captures["client"]
    assert client["service"] == "sqs"
    assert client["aws_access_key_id"] == "mnq-ak"
    assert client["aws_secret_access_key"] == "mnq-sk"
    assert client["endpoint_url"].endswith("sqs.mnq.fr-par.scaleway.com")


async def test_message_standard_sans_params_fifo(captures):
    # Queue STANDARD (non-FIFO) : les triggers scw_sqs de Scaleway ne consomment
    # pas les files FIFO. MessageGroupId/MessageDeduplicationId sont invalides sur
    # une queue standard et provoqueraient une erreur SQS.
    await enqueuer_sqs({"delivery_id": "abc-123", "type": "validation_humaine"})
    send = captures["send"]
    assert "MessageGroupId" not in send
    assert "MessageDeduplicationId" not in send
    assert send["QueueUrl"].endswith("mirador-webhooks")
    assert '"delivery_id": "abc-123"' in send["MessageBody"]
