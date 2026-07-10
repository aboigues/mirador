"""Dépôts de lecture du journal d'audit (lecture seule, aucune mutation).

Les écritures passent exclusivement par `WriterAudit` (writer unique). Ces dépôts
ne font que télécharger la base depuis le bucket et exécuter des SELECT.
"""
from __future__ import annotations

import json
from typing import Any, Optional
from uuid import UUID

from src.infrastructure.stockage.bucket import ClientBucket
from src.infrastructure.stockage.writer import _ouvrir_lecture


def _ligne_en_dict(ligne) -> dict[str, Any]:
    resultat = dict(ligne)
    if resultat.get("details") is not None:
        resultat["details"] = json.loads(resultat["details"])
    return resultat


class DepotJournal:
    def __init__(self, bucket: ClientBucket, cle_bd: str = "mirador.db") -> None:
        self._bucket = bucket
        self._cle_bd = cle_bd

    def lister(self, correlation_id: Optional[UUID] = None) -> list[dict[str, Any]]:
        """Retourne les événements du journal, ordonnés par id croissant."""
        with _ouvrir_lecture(self._bucket, self._cle_bd) as conn:
            if correlation_id is not None:
                curseur = conn.execute(
                    "SELECT * FROM journal_evenements "
                    "WHERE correlation_id = ? ORDER BY id",
                    (str(correlation_id),),
                )
            else:
                curseur = conn.execute(
                    "SELECT * FROM journal_evenements ORDER BY id"
                )
            return [_ligne_en_dict(ligne) for ligne in curseur.fetchall()]
