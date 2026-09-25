"""Vérification d'intégrité du journal d'audit.

Le writer unique téléverse `mirador.db` puis son empreinte SHA-256 dans
`mirador.db.sha256`. On recalcule l'empreinte des octets téléchargés, avant toute
ouverture SQLite, et on la compare à l'empreinte enregistrée.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
from typing import Literal, Optional

from pydantic import BaseModel

from src.infrastructure.stockage.bucket import ClientBucket


class RapportIntegrite(BaseModel):
    issue: Literal["INTÈGRE", "ALTÉRÉ", "NON_VÉRIFIABLE"]
    empreinte_attendue: Optional[str] = None
    empreinte_calculee: Optional[str] = None
    nombre_evenements: Optional[int] = None
    motif: Optional[str] = None


def _compter_evenements(donnees: bytes) -> int:
    with tempfile.TemporaryDirectory() as repertoire:
        chemin = os.path.join(repertoire, "mirador.db")
        with open(chemin, "wb") as fichier:
            fichier.write(donnees)
        conn = sqlite3.connect(f"file:{chemin}?mode=ro", uri=True)
        try:
            return conn.execute("SELECT COUNT(*) FROM journal_evenements").fetchone()[0]
        finally:
            conn.close()


def verifier_integrite(bucket: ClientBucket, cle_bd: str = "mirador.db") -> RapportIntegrite:
    donnees = bucket.telecharger(cle_bd)
    if donnees is None:
        return RapportIntegrite(issue="NON_VÉRIFIABLE", motif="Aucun journal enregistré")

    calculee = hashlib.sha256(donnees).hexdigest()
    brute = bucket.telecharger(f"{cle_bd}.sha256")
    if brute is None:
        return RapportIntegrite(
            issue="NON_VÉRIFIABLE",
            empreinte_calculee=calculee,
            motif="Aucune empreinte enregistrée pour ce journal",
        )

    attendue = brute.decode().strip()
    if attendue != calculee:
        return RapportIntegrite(
            issue="ALTÉRÉ", empreinte_attendue=attendue, empreinte_calculee=calculee,
        )
    return RapportIntegrite(
        issue="INTÈGRE",
        empreinte_attendue=attendue,
        empreinte_calculee=calculee,
        nombre_evenements=_compter_evenements(donnees),
    )
