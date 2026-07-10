"""Agent Détecteur : détection et classification des anomalies CI/CD.

À partir d'un message `workflow_run`, décide s'il y a anomalie, en détermine le
type (ÉCHEC/TIMEOUT/AUTRE) et le niveau de risque, puis tente d'identifier la
cause via le catalogue de règles de diagnostic.

Philosophie de classification :
- Une cause connue (règle ACTIVE qui matche) → anomalie auto-réparable → niveau LOW.
- Sinon, heuristique par type, élevée d'un cran sur une branche protégée (main/master).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

import structlog

from src.domaine.anomalie import Anomalie, NiveauRisque, TypeAnomalie
from src.domaine.regle import RegleDiagnostic

log = structlog.get_logger(__name__)

_BRANCHES_PROTEGEES = ("main", "master")

# Conclusions GitHub ne constituant pas une anomalie
_CONCLUSIONS_SAINES = {"success", "skipped"}

# Niveau de base par type d'anomalie (avant élévation)
_NIVEAU_BASE = {
    TypeAnomalie.AUTRE: NiveauRisque.INFO,
    TypeAnomalie.ECHEC: NiveauRisque.LOW,
    TypeAnomalie.TIMEOUT: NiveauRisque.MEDIUM,
    TypeAnomalie.REGRESSION: NiveauRisque.HIGH,
}


def _elever(niveau: NiveauRisque) -> NiveauRisque:
    return NiveauRisque(min(niveau + 1, NiveauRisque.CRITICAL))


def _duree_secondes(message: dict) -> Optional[float]:
    debut, fin = message.get("run_started_at"), message.get("run_completed_at")
    if not debut or not fin:
        return None
    try:
        d = datetime.fromisoformat(debut.replace("Z", "+00:00"))
        f = datetime.fromisoformat(fin.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (f - d).total_seconds()


class Detecteur:
    def __init__(self, branches_protegees: tuple[str, ...] = _BRANCHES_PROTEGEES) -> None:
        self._branches_protegees = branches_protegees

    def detecter(
        self,
        message: dict,
        depot_id: UUID,
        regles: list[RegleDiagnostic],
        *,
        seuil_timeout_secondes: Optional[int] = None,
        extrait_log: Optional[str] = None,
    ) -> Optional[Anomalie]:
        conclusion = (message.get("conclusion") or "").lower()
        if conclusion in _CONCLUSIONS_SAINES:
            return None

        type_anomalie = self._classer_type(conclusion, message, seuil_timeout_secondes)
        regle = self._trouver_regle(regles, extrait_log)

        if regle is not None:
            niveau = NiveauRisque.LOW  # cause connue → auto-réparable
        else:
            niveau = _NIVEAU_BASE.get(type_anomalie, NiveauRisque.INFO)
            if message.get("head_branch") in self._branches_protegees:
                niveau = _elever(niveau)

        anomalie = Anomalie(
            correlation_id=self._correlation_id(message),
            depot_id=depot_id,
            workflow_run_id=message.get("workflow_run_id"),
            workflow_nom=message.get("workflow_nom", ""),
            type=type_anomalie,
            niveau_risque=niveau,
            cause_identifiee=regle.description if regle else None,
            regle_declenchee_id=regle.id if regle else None,
        )
        if extrait_log is not None:
            anomalie.extrait_log = extrait_log

        log.info("anomalie.detectee",
                 depot=message.get("depot"),
                 workflow_run_id=anomalie.workflow_run_id,
                 type=type_anomalie,
                 niveau=niveau.name,
                 cause=anomalie.cause_identifiee)
        return anomalie

    def _classer_type(
        self, conclusion: str, message: dict, seuil_timeout_secondes: Optional[int]
    ) -> str:
        if conclusion == "timed_out":
            return TypeAnomalie.TIMEOUT
        if conclusion == "cancelled":
            return TypeAnomalie.AUTRE
        if conclusion == "failure":
            duree = _duree_secondes(message)
            if seuil_timeout_secondes and duree is not None and duree > seuil_timeout_secondes:
                return TypeAnomalie.TIMEOUT
            return TypeAnomalie.ECHEC
        return TypeAnomalie.AUTRE

    def _trouver_regle(
        self, regles: list[RegleDiagnostic], extrait_log: Optional[str]
    ) -> Optional[RegleDiagnostic]:
        if not extrait_log:
            return None
        for regle in regles:
            if regle.correspond_au_log(extrait_log):
                return regle
        return None

    @staticmethod
    def _correlation_id(message: dict) -> UUID:
        delivery_id = message.get("delivery_id")
        if delivery_id:
            try:
                return UUID(delivery_id)
            except (ValueError, AttributeError):
                pass
        from uuid import uuid4
        return uuid4()
