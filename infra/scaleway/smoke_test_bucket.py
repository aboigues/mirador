"""Smoke-test réel de la chaîne Object Storage (writer d'audit ↔ bucket Scaleway).

Valide, contre le VRAI bucket, ce que les tests à doubles ne peuvent pas :
authentification, permissions IAM, endpoint, format SQLite persisté, checksum.

Le bucket doit préexister (créé dans le projet MIRADOR). Credentials + config
lus depuis l'environnement — source `infra/scaleway/.secrets.env` avant de lancer :

    set -a; . infra/scaleway/.secrets.env; set +a
    export BUCKET_NAME=telemach-mirador-audit \
           S3_ENDPOINT_URL=https://s3.fr-par.scw.cloud AWS_REGION=fr-par
    python infra/scaleway/smoke_test_bucket.py

Note : la clé runtime n'ayant pas ObjectStorageObjectsDelete (immutabilité),
les objets de test sous `smoke-test/` ne sont pas supprimés — c'est attendu.
"""
from __future__ import annotations

import hashlib
import sys
from uuid import uuid4

from src.domaine.journal import EvenementJournal, TypeEvenement
from src.infrastructure.stockage.bucket import ClientBucketS3
from src.infrastructure.stockage.depots import DepotJournal
from src.infrastructure.stockage.writer import WriterAudit

CLE = "smoke-test/audit.db"


def _evenement(correlation_id, statut: str) -> EvenementJournal:
    return EvenementJournal(
        correlation_id=correlation_id,
        type_evenement=TypeEvenement.DETECTION,
        niveau_risque="LOW",
        depot="aboigues/smoke",
        acteur="mirador-agent",
        statut=statut,
        workflow_run_id=1,
        type_anomalie="ÉCHEC",
        resultat="smoke-test",
    )


def main() -> int:
    bucket = ClientBucketS3()  # BUCKET_NAME + AWS_* depuis l'environnement
    writer = WriterAudit(bucket, cle_bd=CLE)
    depot = DepotJournal(bucket, cle_bd=CLE)
    correlation_id = uuid4()

    checksum = writer.ecrire([_evenement(correlation_id, "SUCCÈS")])
    lignes = depot.lister(correlation_id=correlation_id)
    assert lignes and lignes[-1]["depot"] == "aboigues/smoke", "relecture invalide"

    fichier = bucket.telecharger(CLE)
    attendu = hashlib.sha256(fichier).hexdigest()
    stocke = bucket.telecharger(CLE + ".sha256").decode()
    assert attendu == stocke == checksum, "checksum non concordant"

    avant = len(depot.lister(correlation_id=correlation_id))
    writer.ecrire([_evenement(correlation_id, "EN_ATTENTE")])
    apres = len(depot.lister(correlation_id=correlation_id))
    assert apres == avant + 1, "append-only non préservé"

    print(f"✅ chaîne Object Storage validée (checksum {checksum[:16]}…, append {avant}→{apres})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
