"""Tests d'intégration : writer d'audit SQLite WAL + persistance bucket.

SQLite et checksum SHA-256 sont réels ; seul le transport objet est un double
en mémoire (interface identique au client S3 boto3). Valide l'immutabilité
append-only du journal (aucun UPDATE/DELETE) et la cohérence du checksum.
"""
import hashlib
from uuid import uuid4

import pytest

from src.domaine.journal import EvenementJournal, TypeEvenement
from src.infrastructure.stockage.bucket import ClientBucketMemoire
from src.infrastructure.stockage.depots import DepotJournal
from src.infrastructure.stockage.writer import WriterAudit

CLE_BD = "mirador.db"


def _evenement(depot: str = "aboigues/k8t", statut: str = "SUCCÈS") -> EvenementJournal:
    return EvenementJournal(
        correlation_id=uuid4(),
        type_evenement=TypeEvenement.DETECTION,
        niveau_risque="HIGH",
        depot=depot,
        acteur="mirador-agent",
        statut=statut,
        workflow_run_id=12345678,
        type_anomalie="ÉCHEC",
        details={"regle": "timeout-connexion-bd"},
        resultat="anomalie détectée",
    )


@pytest.fixture
def bucket() -> ClientBucketMemoire:
    return ClientBucketMemoire()


@pytest.fixture
def writer(bucket) -> WriterAudit:
    return WriterAudit(bucket, cle_bd=CLE_BD)


@pytest.fixture
def depot(bucket) -> DepotJournal:
    return DepotJournal(bucket, cle_bd=CLE_BD)


class TestWriterEcritureLecture:
    def test_ecrire_puis_relire(self, writer, depot):
        evt = _evenement()
        writer.ecrire([evt])
        lignes = depot.lister()
        assert len(lignes) == 1
        assert lignes[0]["depot"] == "aboigues/k8t"
        assert lignes[0]["type_evenement"] == TypeEvenement.DETECTION
        assert lignes[0]["workflow_run_id"] == 12345678

    def test_details_json_round_trip(self, writer, depot):
        writer.ecrire([_evenement()])
        assert depot.lister()[0]["details"] == {"regle": "timeout-connexion-bd"}

    def test_base_absente_est_creee(self, bucket, writer, depot):
        assert bucket.telecharger(CLE_BD) is None
        writer.ecrire([_evenement()])
        assert bucket.telecharger(CLE_BD) is not None
        assert len(depot.lister()) == 1

    def test_batch_multiple(self, writer, depot):
        writer.ecrire([_evenement(), _evenement(), _evenement()])
        assert len(depot.lister()) == 3


class TestImmutabiliteAppendOnly:
    def test_ecritures_successives_sont_append(self, writer, depot):
        writer.ecrire([_evenement(statut="EN_ATTENTE")])
        writer.ecrire([_evenement(statut="SUCCÈS")])
        lignes = depot.lister()
        assert len(lignes) == 2
        # La première ligne reste inchangée après la seconde écriture
        assert lignes[0]["statut"] == "EN_ATTENTE"
        assert lignes[1]["statut"] == "SUCCÈS"

    def test_writer_nexpose_aucune_mutation(self, writer):
        # Garde-fou : le writer ne doit exposer que l'écriture append-only
        for interdit in ("update", "delete", "supprimer", "modifier", "mettre_a_jour"):
            assert not hasattr(writer, interdit)

    def test_ids_sequentiels_croissants(self, writer, depot):
        writer.ecrire([_evenement()])
        writer.ecrire([_evenement()])
        ids = [ligne["id"] for ligne in depot.lister()]
        assert ids == sorted(ids)
        assert len(set(ids)) == len(ids)


class TestChecksum:
    def test_checksum_persiste_correspond_au_fichier(self, bucket, writer):
        writer.ecrire([_evenement()])
        fichier = bucket.telecharger(CLE_BD)
        checksum_attendu = hashlib.sha256(fichier).hexdigest()
        checksum_stocke = bucket.telecharger(CLE_BD + ".sha256")
        assert checksum_stocke.decode() == checksum_attendu

    def test_ecrire_retourne_le_checksum(self, bucket, writer):
        checksum = writer.ecrire([_evenement()])
        fichier = bucket.telecharger(CLE_BD)
        assert checksum == hashlib.sha256(fichier).hexdigest()

    def test_checksum_change_entre_ecritures(self, writer):
        c1 = writer.ecrire([_evenement()])
        c2 = writer.ecrire([_evenement()])
        assert c1 != c2
