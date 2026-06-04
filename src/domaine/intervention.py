"""Entité Intervention : action corrective liée à une anomalie."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from src.domaine.anomalie import Anomalie


class TypeIntervention:
    RELANCE = "RELANCE"
    PULL_REQUEST = "PULL_REQUEST"
    NOTIFICATION = "NOTIFICATION"


class StatutIntervention:
    EN_ATTENTE = "EN_ATTENTE"
    SUCCES = "SUCCÈS"
    ECHEC = "ÉCHEC"


class Intervention(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    anomalie_id: UUID
    correlation_id: UUID
    type: str
    acteur: str
    statut: str = Field(default=StatutIntervention.EN_ATTENTE)
    details: Optional[dict[str, Any]] = None
    resultat: Optional[str] = None
    cree_le: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    termine_le: Optional[datetime] = None

    model_config = {"frozen": False}

    @classmethod
    def creer_si_possible(
        cls,
        anomalie: Anomalie,
        interventions_existantes: list["Intervention"],
        type: str,
        acteur: str,
        details: Optional[dict[str, Any]] = None,
    ) -> "Intervention":
        en_attente = [
            i for i in interventions_existantes
            if i.anomalie_id == anomalie.id
            and i.statut == StatutIntervention.EN_ATTENTE
        ]
        if en_attente:
            raise ValueError(
                f"Impossible de créer une intervention : "
                f"une intervention en attente existe déjà (id={en_attente[0].id})"
            )
        return cls(
            anomalie_id=anomalie.id,
            correlation_id=anomalie.correlation_id,
            type=type,
            acteur=acteur,
            details=details,
        )

    def marquer_succes(self, resultat: str) -> None:
        self.statut = StatutIntervention.SUCCES
        self.resultat = resultat
        self.termine_le = datetime.now(timezone.utc)

    def marquer_echec(self, resultat: str) -> None:
        self.statut = StatutIntervention.ECHEC
        self.resultat = resultat
        self.termine_le = datetime.now(timezone.utc)
