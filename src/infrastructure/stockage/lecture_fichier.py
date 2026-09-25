"""Lecture du journal depuis un répertoire local, sans aucune écriture possible.

Même interface que `ClientBucket` : `DepotJournal` et la vérification
d'intégrité fonctionnent sans modification sur une copie locale du bucket
(`mirador.db` + `mirador.db.sha256`). Sert au serveur MCP hors ligne, aux
tests et aux démonstrations.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional


class EcritureInterdite(RuntimeError):
    """Levée à toute tentative d'écriture : cet adaptateur est en lecture seule."""


class ClientBucketLectureFichier:
    def __init__(self, repertoire: str | Path) -> None:
        self._racine = Path(repertoire).resolve()
        if not self._racine.is_dir():
            raise ValueError(f"Répertoire du journal introuvable : {repertoire}")

    def _chemin(self, cle: str) -> Path:
        chemin = (self._racine / cle).resolve()
        if not chemin.is_relative_to(self._racine):
            raise ValueError(f"Clé hors du répertoire du journal : {cle!r}")
        return chemin

    def telecharger(self, cle: str) -> Optional[bytes]:
        chemin = self._chemin(cle)
        if not chemin.is_file():
            return None
        return chemin.read_bytes()

    def televerser(self, cle: str, donnees: bytes) -> None:
        raise EcritureInterdite("Journal local ouvert en lecture seule")
