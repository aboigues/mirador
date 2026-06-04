"""Entité RègleDiagnostic : règle de reconnaissance d'anomalie."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class ActionRecommandee:
    RELANCE = "RELANCE"
    PULL_REQUEST = "PULL_REQUEST"


class OrigineRegle:
    MANUELLE = "MANUELLE"
    APPRISE = "APPRISE"


class StatutRegle:
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    EN_ATTENTE_VALIDATION = "EN_ATTENTE_VALIDATION"


class RegleDiagnostic(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    nom: str
    description: str
    pattern_log: Optional[str] = None
    code_sortie: Optional[int] = None
    duree_max_secondes: Optional[int] = None
    action_recommandee: str
    template_correctif: Optional[str] = None
    origine: str
    statut: str = Field(default="")
    score_confiance: float = 0.0
    utilisations: int = 0
    succes: int = 0
    cree_le: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    modifie_le: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def model_post_init(self, __context) -> None:
        # Les règles apprises automatiquement nécessitent validation humaine
        if not self.statut:
            if self.origine == OrigineRegle.APPRISE:
                self.statut = StatutRegle.EN_ATTENTE_VALIDATION
            else:
                self.statut = StatutRegle.ACTIVE

    def est_applicable(self) -> bool:
        return self.statut == StatutRegle.ACTIVE

    def correspond_au_log(self, extrait_log: str) -> bool:
        if not self.pattern_log or not self.est_applicable():
            return False
        return bool(re.search(self.pattern_log, extrait_log, re.IGNORECASE))

    def correspond_au_code_sortie(self, code: int) -> bool:
        if self.code_sortie is None or not self.est_applicable():
            return False
        return self.code_sortie == code

    def enregistrer_utilisation(self, succes: bool) -> None:
        self.utilisations += 1
        if succes:
            self.succes += 1
        self.score_confiance = self.succes / self.utilisations
        self.modifie_le = datetime.now(timezone.utc)
