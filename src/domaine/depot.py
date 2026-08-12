"""Entité DépôtSurveillé : dépôt GitHub configuré pour la surveillance."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator

_PATTERN_IDENTIFIANT = re.compile(r"^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$")
_MAX_DEPOTS_ACTIFS = 5


class DepotSurveille(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    identifiant_github: str
    installation_id: int
    actif: bool = True
    seuil_timeout_secondes: int = 3600
    # Opt-in : autorise le correcteur à lire les manifestes/Dockerfiles du dépôt
    # pour matérialiser un correctif (bump d'image...) plutôt que de s'abstenir
    # faute de savoir quel fichier éditer. Coût en appels API et en tokens à
    # chaque anomalie — à activer dépôt par dépôt, pas par défaut.
    lecture_depot: bool = False
    responsables: list[str] = Field(default_factory=list)
    regles_ids: list[UUID] = Field(default_factory=list)
    cree_le: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    modifie_le: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("identifiant_github")
    @classmethod
    def valider_identifiant(cls, v: str) -> str:
        if not _PATTERN_IDENTIFIANT.match(v):
            raise ValueError(
                f"Format invalide pour identifiant_github : {v!r}. "
                "Format attendu : 'proprietaire/depot'"
            )
        return v

    @field_validator("responsables")
    @classmethod
    def valider_responsables(cls, v: list[str]) -> list[str]:
        return v

    def model_post_init(self, __context) -> None:
        if self.actif and len(self.responsables) == 0:
            raise ValueError(
                "Au moins un responsable est requis lorsque le dépôt est actif"
            )

    def desactiver(self) -> None:
        self.actif = False
        self.modifie_le = datetime.now(timezone.utc)
