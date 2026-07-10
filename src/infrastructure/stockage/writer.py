"""Writer d'audit unique : sérialise les écritures SQLite vers le bucket.

Flux (consommateur de la queue `mirador-writes`) :
  télécharger la base → INSERT batch (WAL) → checkpoint → téléverser + checksum SHA-256.

Writer *unique* : une seule instance consomme la queue à la fois, ce qui évite
toute concurrence multi-writer sur SQLite. Seuls des INSERT sont émis → journal
append-only immuable.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sqlite3
import tempfile

import structlog

from src.domaine.journal import EvenementJournal
from src.infrastructure.stockage.bucket import ClientBucket
from src.infrastructure.stockage.modeles_bd import initialiser_schema

log = structlog.get_logger(__name__)

_COLONNES = (
    "horodatage", "correlation_id", "type_evenement", "niveau_risque", "depot",
    "workflow_run_id", "type_anomalie", "type_action", "acteur", "statut",
    "details", "resultat",
)


def _ligne_depuis_evenement(evt: EvenementJournal) -> tuple:
    return (
        evt.horodatage.isoformat(),
        str(evt.correlation_id),
        evt.type_evenement,
        evt.niveau_risque,
        evt.depot,
        evt.workflow_run_id,
        evt.type_anomalie,
        evt.type_action,
        evt.acteur,
        evt.statut,
        json.dumps(evt.details, ensure_ascii=False) if evt.details is not None else None,
        evt.resultat,
    )


class WriterAudit:
    def __init__(self, bucket: ClientBucket, cle_bd: str = "mirador.db") -> None:
        self._bucket = bucket
        self._cle_bd = cle_bd

    def ecrire(self, evenements: list[EvenementJournal]) -> str:
        """Ajoute les événements au journal et retourne le checksum SHA-256 du fichier.

        Opération append-only : les lignes existantes ne sont jamais modifiées.
        """
        if not evenements:
            raise ValueError("Aucun événement à écrire")

        with tempfile.TemporaryDirectory() as repertoire:
            chemin = os.path.join(repertoire, "mirador.db")

            existant = self._bucket.telecharger(self._cle_bd)
            if existant is not None:
                with open(chemin, "wb") as fichier:
                    fichier.write(existant)

            conn = sqlite3.connect(chemin)
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                initialiser_schema(conn)
                conn.executemany(
                    f"INSERT INTO journal_evenements ({', '.join(_COLONNES)}) "
                    f"VALUES ({', '.join('?' * len(_COLONNES))})",
                    [_ligne_depuis_evenement(evt) for evt in evenements],
                )
                conn.commit()
                # Fusionne le WAL dans le fichier principal → objet unique cohérent
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                conn.commit()
            finally:
                conn.close()

            with open(chemin, "rb") as fichier:
                donnees = fichier.read()

        checksum = hashlib.sha256(donnees).hexdigest()
        self._bucket.televerser(self._cle_bd, donnees)
        self._bucket.televerser(f"{self._cle_bd}.sha256", checksum.encode())

        log.info(
            "audit.ecrit",
            cle_bd=self._cle_bd,
            evenements=len(evenements),
            checksum=checksum,
        )
        return checksum


@contextlib.contextmanager
def _ouvrir_lecture(bucket: ClientBucket, cle_bd: str):
    """Ouvre une connexion SQLite en lecture sur la copie téléchargée du bucket."""
    donnees = bucket.telecharger(cle_bd)
    with tempfile.TemporaryDirectory() as repertoire:
        chemin = os.path.join(repertoire, "mirador.db")
        if donnees is not None:
            with open(chemin, "wb") as fichier:
                fichier.write(donnees)
        conn = sqlite3.connect(chemin)
        conn.row_factory = sqlite3.Row
        try:
            initialiser_schema(conn)
            yield conn
        finally:
            conn.close()
