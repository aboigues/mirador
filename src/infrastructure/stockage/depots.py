"""Dépôts de lecture du journal d'audit (lecture seule, aucune mutation).

Les écritures passent exclusivement par `WriterAudit` (writer unique). Ces dépôts
ne font que télécharger la base depuis le bucket et exécuter des SELECT.
"""
from __future__ import annotations

import json
from typing import Any, Optional
from uuid import UUID

from src.domaine.journal import TypeEvenement
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

    def delivery_deja_traite(self, delivery_id: str) -> bool:
        """Indique si un événement a déjà été journalisé pour ce delivery_id.

        Support de l'idempotence : la queue standard peut livrer un message plus
        d'une fois. Si le pipeline a déjà journalisé ce delivery_id, on ne le
        retraite pas (pas de double issue / double intervention).
        """
        if not delivery_id:
            return False
        with _ouvrir_lecture(self._bucket, self._cle_bd) as conn:
            curseur = conn.execute(
                "SELECT 1 FROM journal_evenements WHERE delivery_id = ? LIMIT 1",
                (delivery_id,),
            )
            return curseur.fetchone() is not None

    def proposition_escalade(self, anomalie_id: str) -> Optional[dict[str, Any]]:
        """Retourne les `details` du dernier événement ESCALADE pour cet anomalie_id.

        `anomalie_id` correspond au `correlation_id` embarqué dans l'issue Mirador.
        Permet d'exécuter, sur /approuver, la proposition d'intervention persistée
        au moment de l'escalade. Retourne None si introuvable ou id invalide.
        """
        try:
            cid = UUID(anomalie_id)
        except (ValueError, TypeError, AttributeError):
            return None
        for evt in reversed(self.lister(correlation_id=cid)):
            if evt.get("type_evenement") == TypeEvenement.ESCALADE:
                return evt.get("details")
        return None
