"""Entité JournalÉvénement : entrée d'audit immuable."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class TypeEvenement:
    DETECTION = "DÉTECTION"
    INTERVENTION = "INTERVENTION"
    ESCALADE = "ESCALADE"
    VALIDATION = "VALIDATION"
    REJET = "REJET"
    ERREUR_LIVRAISON = "ERREUR_LIVRAISON"


class EvenementJournal(BaseModel):
    correlation_id: UUID
    type_evenement: str
    niveau_risque: str
    depot: str
    acteur: str
    statut: str
    workflow_run_id: Optional[int] = None
    type_anomalie: Optional[str] = None
    type_action: Optional[str] = None
    details: Optional[dict[str, Any]] = None
    resultat: Optional[str] = None
    horodatage: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = {"frozen": True}
