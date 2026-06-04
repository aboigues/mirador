"""Entité Anomalie : événement de déviation détecté sur un pipeline CI/CD."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import IntEnum
from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


class NiveauRisque(IntEnum):
    INFO = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


class TypeAnomalie(str):
    ECHEC = "ÉCHEC"
    TIMEOUT = "TIMEOUT"
    REGRESSION = "RÉGRESSION"
    AUTRE = "AUTRE"

    def __new__(cls, value: str):
        return str.__new__(cls, value)


# Constantes de type pour éviter les chaînes en dur
class TypeAnomalie:
    ECHEC = "ÉCHEC"
    TIMEOUT = "TIMEOUT"
    REGRESSION = "RÉGRESSION"
    AUTRE = "AUTRE"

    _valeurs = {"ÉCHEC", "TIMEOUT", "RÉGRESSION", "AUTRE"}

    def __init__(self, value: str):
        if value not in self._valeurs:
            raise ValueError(f"TypeAnomalie invalide : {value!r}")
        self._value = value

    def __str__(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return f"TypeAnomalie({self._value!r})"

    def __eq__(self, other) -> bool:
        if isinstance(other, TypeAnomalie):
            return self._value == other._value
        return self._value == other

    def __hash__(self) -> int:
        return hash(self._value)


class StatutAnomalie:
    OUVERTE = "OUVERTE"
    EN_COURS = "EN_COURS"
    RESOLUE = "RÉSOLUE"
    ESCALADEE = "ESCALADÉE"
    REJETEE = "REJETÉE"

    # Transitions autorisées : statut_actuel → {statuts_suivants_valides}
    _transitions: dict[str, set[str]] = {
        "OUVERTE": {"EN_COURS"},
        "EN_COURS": {"RÉSOLUE", "ESCALADÉE"},
        "ESCALADÉE": {"EN_COURS", "REJETÉE"},
        "RÉSOLUE": set(),
        "REJETÉE": set(),
    }

    @classmethod
    def transition_valide(cls, source: str, cible: str) -> bool:
        return cible in cls._transitions.get(source, set())


class ErreurTransitionInterdite(Exception):
    pass


class Anomalie(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    correlation_id: UUID = Field(default_factory=uuid4)
    depot_id: UUID
    workflow_run_id: int
    workflow_nom: str
    type: str
    niveau_risque: NiveauRisque
    statut: str = Field(default=StatutAnomalie.OUVERTE)
    cause_identifiee: Optional[str] = None
    regle_declenchee_id: Optional[UUID] = None
    _extrait_log_brut: Optional[str] = None
    detecte_le: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    mis_a_jour_le: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = {"arbitrary_types_allowed": True}

    @property
    def extrait_log(self) -> Optional[str]:
        return self._extrait_log_brut

    @extrait_log.setter
    def extrait_log(self, value: Optional[str]) -> None:
        if value is not None:
            self._extrait_log_brut = value[:2000]
        else:
            self._extrait_log_brut = None

    def promouvoir_risque(self, nouveau_niveau: NiveauRisque) -> None:
        if nouveau_niveau <= self.niveau_risque:
            raise ErreurTransitionInterdite(
                f"Le niveau de risque ne peut qu'augmenter : "
                f"{self.niveau_risque.name} → {nouveau_niveau.name} interdit"
            )
        self.niveau_risque = nouveau_niveau
        self.mis_a_jour_le = datetime.now(timezone.utc)

    def _transitionner(self, cible: str) -> None:
        if not StatutAnomalie.transition_valide(self.statut, cible):
            raise ErreurTransitionInterdite(
                f"Transition interdite : {self.statut} → {cible}"
            )
        self.statut = cible
        self.mis_a_jour_le = datetime.now(timezone.utc)

    def demarrer_traitement(self) -> None:
        self._transitionner(StatutAnomalie.EN_COURS)

    def marquer_resolue(self) -> None:
        self._transitionner(StatutAnomalie.RESOLUE)

    def escalader(self, notification_envoyee: bool = True) -> None:
        if not notification_envoyee:
            raise ValueError(
                "Une notification aux responsables est obligatoire avant toute escalade"
            )
        self._transitionner(StatutAnomalie.ESCALADEE)

    def reprendre_apres_validation(self) -> None:
        self._transitionner(StatutAnomalie.EN_COURS)

    def rejeter(self) -> None:
        self._transitionner(StatutAnomalie.REJETEE)
