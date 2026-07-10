"""Tests d'intégration : implémentation boto3 réelle du client bucket (ClientBucketS3).

Exerce le vrai chemin boto3 (get_object / put_object / gestion NoSuchKey) contre
un S3 simulé in-process par moto — sans mocker boto3 lui-même. Valide aussi que le
writer d'audit fonctionne de bout en bout sur ce client S3.

Note perf : sous WSL avec le venv sur un disque Windows (drvfs), la création d'un
client boto3 charge lentement les données botocore. La fixture est donc à portée
module (un seul client créé) ; chaque test utilise une clé distincte pour rester
isolé. En CI Linux native, ce coût disparaît.
"""
import boto3
import pytest

moto = pytest.importorskip("moto")
from moto import mock_aws  # noqa: E402

from src.domaine.journal import EvenementJournal, TypeEvenement  # noqa: E402
from src.infrastructure.stockage.bucket import ClientBucketS3  # noqa: E402
from src.infrastructure.stockage.depots import DepotJournal  # noqa: E402
from src.infrastructure.stockage.writer import WriterAudit  # noqa: E402

NOM_BUCKET = "mirador-audit-test"
REGION = "fr-par"


@pytest.fixture(scope="module")
def bucket_s3():
    mp = pytest.MonkeyPatch()
    mp.setenv("AWS_ACCESS_KEY_ID", "test")
    mp.setenv("AWS_SECRET_ACCESS_KEY", "test")
    mp.setenv("AWS_DEFAULT_REGION", REGION)
    # Endpoint vide → boto3/moto utilise l'endpoint AWS par défaut, intercepté
    # de façon fiable par moto (un endpoint réel s'échapperait vers le réseau).
    mp.setenv("S3_ENDPOINT_URL", "")
    with mock_aws():
        boto3.client("s3", region_name=REGION).create_bucket(
            Bucket=NOM_BUCKET,
            CreateBucketConfiguration={"LocationConstraint": REGION},
        )
        yield ClientBucketS3(nom_bucket=NOM_BUCKET, region_name=REGION)
    mp.undo()


class TestClientBucketS3:
    def test_objet_absent_retourne_none(self, bucket_s3):
        assert bucket_s3.telecharger("inexistant.bin") is None

    def test_round_trip_televerser_telecharger(self, bucket_s3):
        bucket_s3.televerser("round.bin", b"contenu-binaire-\x00\x01")
        assert bucket_s3.telecharger("round.bin") == b"contenu-binaire-\x00\x01"

    def test_televerser_ecrase(self, bucket_s3):
        bucket_s3.televerser("ecrase.bin", b"v1")
        bucket_s3.televerser("ecrase.bin", b"v2")
        assert bucket_s3.telecharger("ecrase.bin") == b"v2"


class TestWriterSurS3:
    def test_ecrire_et_relire_via_s3(self, bucket_s3):
        writer = WriterAudit(bucket_s3, cle_bd="mirador.db")
        evt = EvenementJournal(
            correlation_id=__import__("uuid").uuid4(),
            type_evenement=TypeEvenement.INTERVENTION,
            niveau_risque="LOW",
            depot="aboigues/k8t",
            acteur="mirador-agent",
            statut="SUCCÈS",
        )
        checksum = writer.ecrire([evt])
        assert bucket_s3.telecharger("mirador.db.sha256").decode() == checksum

        lignes = DepotJournal(bucket_s3, cle_bd="mirador.db").lister()
        assert len(lignes) == 1
        assert lignes[0]["type_evenement"] == TypeEvenement.INTERVENTION
