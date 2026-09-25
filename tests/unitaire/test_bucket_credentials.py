"""Tests unitaires : credentials du client bucket (Scaleway Object Storage).

La clé IAM du bucket est lue sous SCALEWAY_S3_ACCESS_KEY / SCALEWAY_S3_SECRET_KEY et passée
explicitement à boto3. Absente, le client laisse boto3 appliquer sa chaîne de
credentials habituelle : la transition depuis les anciens noms AWS_* se fait
ainsi sans coupure, quel que soit l'ordre de déploiement du code et de la config.
"""
import boto3
import pytest

from src.infrastructure.stockage.bucket import ClientBucketS3


@pytest.fixture
def client_capture(monkeypatch):
    etat: dict = {}

    def _fake_client(service, **kw):
        etat.update(service=service, **kw)
        return object()

    monkeypatch.setattr(boto3, "client", _fake_client)
    monkeypatch.setenv("BUCKET_NAME", "mirador-audit")
    for nom in ("SCALEWAY_S3_ACCESS_KEY", "SCALEWAY_S3_SECRET_KEY", "SCALEWAY_REGION"):
        monkeypatch.delenv(nom, raising=False)
    return etat


def test_cles_scw_s3_passees_explicitement(client_capture, monkeypatch):
    monkeypatch.setenv("SCALEWAY_S3_ACCESS_KEY", "s3-ak")
    monkeypatch.setenv("SCALEWAY_S3_SECRET_KEY", "s3-sk")
    ClientBucketS3()
    assert client_capture["service"] == "s3"
    assert client_capture["aws_access_key_id"] == "s3-ak"
    assert client_capture["aws_secret_access_key"] == "s3-sk"


def test_sans_cles_scw_s3_boto3_garde_sa_chaine_par_defaut(client_capture):
    ClientBucketS3()
    assert "aws_access_key_id" not in client_capture
    assert "aws_secret_access_key" not in client_capture


def test_cle_incomplete_ignoree(client_capture, monkeypatch):
    # Une paire à moitié posée ne doit pas produire un client à moitié authentifié.
    monkeypatch.setenv("SCALEWAY_S3_ACCESS_KEY", "s3-ak")
    ClientBucketS3()
    assert "aws_access_key_id" not in client_capture


def test_region_lue_sous_scw_region(client_capture, monkeypatch):
    monkeypatch.setenv("SCALEWAY_REGION", "nl-ams")
    ClientBucketS3()
    assert client_capture["region_name"] == "nl-ams"


def test_region_par_defaut_fr_par(client_capture):
    ClientBucketS3()
    assert client_capture["region_name"] == "fr-par"
