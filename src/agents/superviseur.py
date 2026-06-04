"""Agent Superviseur : orchestration, évaluation du risque et escalade."""
from __future__ import annotations

from enum import Enum

from src.domaine.anomalie import Anomalie, NiveauRisque


class DecisionEscalade(str, Enum):
    JOURNALISER_SEULEMENT = "JOURNALISER_SEULEMENT"
    INTERVENTION_AUTO = "INTERVENTION_AUTO"
    NOTIFIER_ET_ATTENDRE = "NOTIFIER_ET_ATTENDRE"
    VALIDATION_HUMAINE_REQUISE = "VALIDATION_HUMAINE_REQUISE"


class NecessiteValidationHumaine(Exception):
    pass


class Superviseur:
    """Évalue le niveau de risque d'une anomalie et décide de l'action à entreprendre."""

    def evaluer(self, anomalie: Anomalie) -> DecisionEscalade:
        match anomalie.niveau_risque:
            case NiveauRisque.INFO:
                return DecisionEscalade.JOURNALISER_SEULEMENT
            case NiveauRisque.LOW:
                return DecisionEscalade.INTERVENTION_AUTO
            case NiveauRisque.MEDIUM:
                return DecisionEscalade.NOTIFIER_ET_ATTENDRE
            case NiveauRisque.HIGH | NiveauRisque.CRITICAL:
                return DecisionEscalade.VALIDATION_HUMAINE_REQUISE
