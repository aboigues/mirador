"""Tests : vérification d'intégrité du journal, sur un vrai journal écrit par WriterAudit."""
from uuid import uuid4

from src.domaine.journal import EvenementJournal, TypeEvenement
from src.infrastructure.stockage.bucket import ClientBucketMemoire
from src.infrastructure.stockage.integrite import verifier_integrite
from src.infrastructure.stockage.writer import WriterAudit

CLE_BD = "mirador.db"


def _evt():
    return EvenementJournal(
        correlation_id=uuid4(),
        type_evenement=TypeEvenement.DETECTION,
        niveau_risque="LOW",
        depot="aboigues/k8t",
        acteur="mirador-agent",
        statut="SUCCÈS",
    )


def _bucket_avec_journal(nombre=3):
    bucket = ClientBucketMemoire()
    WriterAudit(bucket, cle_bd=CLE_BD).ecrire([_evt() for _ in range(nombre)])
    return bucket


def test_journal_intact():
    rapport = verifier_integrite(_bucket_avec_journal(3), CLE_BD)

    assert rapport.issue == "INTÈGRE"
    assert rapport.empreinte_attendue == rapport.empreinte_calculee
    assert rapport.nombre_evenements == 3


def test_journal_altere_apres_ecriture():
    bucket = _bucket_avec_journal()
    donnees = bytearray(bucket.telecharger(CLE_BD))
    donnees[-1] ^= 0xFF
    bucket.televerser(CLE_BD, bytes(donnees))

    rapport = verifier_integrite(bucket, CLE_BD)

    assert rapport.issue == "ALTÉRÉ"
    assert rapport.empreinte_attendue != rapport.empreinte_calculee
    assert rapport.empreinte_attendue and rapport.empreinte_calculee
    assert rapport.nombre_evenements is None


def test_sans_journal_non_verifiable():
    rapport = verifier_integrite(ClientBucketMemoire(), CLE_BD)

    assert rapport.issue == "NON_VÉRIFIABLE"
    assert rapport.motif


def test_sans_empreinte_non_verifiable():
    bucket = ClientBucketMemoire()
    bucket.televerser(CLE_BD, _bucket_avec_journal().telecharger(CLE_BD))

    rapport = verifier_integrite(bucket, CLE_BD)

    assert rapport.issue == "NON_VÉRIFIABLE"
    assert rapport.empreinte_calculee
    assert rapport.empreinte_attendue is None
