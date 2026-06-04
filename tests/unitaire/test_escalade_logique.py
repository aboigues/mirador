"""Tests unitaires : logique d'escalade sans I/O."""
import pytest
from uuid import uuid4

from src.domaine.anomalie import Anomalie, NiveauRisque, TypeAnomalie
from src.domaine.intervention import Intervention, TypeIntervention, StatutIntervention
from src.domaine.journal import TypeEvenement
from src.agents.superviseur import (
    Superviseur,
    DecisionEscalade,
    NecessiteValidationHumaine,
)


class TestDecisionEscalade:
    def test_low_ne_necessite_pas_validation(self):
        superviseur = Superviseur()
        anomalie = _anomalie(NiveauRisque.LOW)
        decision = superviseur.evaluer(anomalie)
        assert decision == DecisionEscalade.INTERVENTION_AUTO

    def test_medium_necessite_notification_avant_escalade(self):
        superviseur = Superviseur()
        anomalie = _anomalie(NiveauRisque.MEDIUM)
        decision = superviseur.evaluer(anomalie)
        assert decision == DecisionEscalade.NOTIFIER_ET_ATTENDRE

    def test_high_necessite_validation_humaine(self):
        superviseur = Superviseur()
        anomalie = _anomalie(NiveauRisque.HIGH)
        decision = superviseur.evaluer(anomalie)
        assert decision == DecisionEscalade.VALIDATION_HUMAINE_REQUISE

    def test_critical_necessite_validation_humaine(self):
        superviseur = Superviseur()
        anomalie = _anomalie(NiveauRisque.CRITICAL)
        decision = superviseur.evaluer(anomalie)
        assert decision == DecisionEscalade.VALIDATION_HUMAINE_REQUISE

    def test_info_pas_d_action(self):
        superviseur = Superviseur()
        anomalie = _anomalie(NiveauRisque.INFO)
        decision = superviseur.evaluer(anomalie)
        assert decision == DecisionEscalade.JOURNALISER_SEULEMENT


class TestContrainteIntervention:
    def test_une_seule_intervention_en_attente_par_anomalie(self):
        anomalie = _anomalie(NiveauRisque.LOW)
        anomalie.demarrer_traitement()
        intervention1 = Intervention(
            anomalie_id=anomalie.id,
            correlation_id=anomalie.correlation_id,
            type=TypeIntervention.RELANCE,
            acteur="mirador-agent",
        )
        # La deuxième intervention ne doit pas être créée si une EN_ATTENTE existe
        with pytest.raises(ValueError, match="intervention en attente"):
            Intervention.creer_si_possible(
                anomalie=anomalie,
                interventions_existantes=[intervention1],
                type=TypeIntervention.RELANCE,
                acteur="mirador-agent",
            )

    def test_notification_obligatoire_avant_escalade(self):
        anomalie = _anomalie(NiveauRisque.MEDIUM)
        anomalie.demarrer_traitement()
        # Escalader sans notification préalable doit lever une exception
        with pytest.raises(ValueError, match="notification"):
            anomalie.escalader(notification_envoyee=False)

    def test_escalade_possible_apres_notification(self):
        anomalie = _anomalie(NiveauRisque.MEDIUM)
        anomalie.demarrer_traitement()
        anomalie.escalader(notification_envoyee=True)
        from src.domaine.anomalie import StatutAnomalie
        assert anomalie.statut == StatutAnomalie.ESCALADEE


def _anomalie(niveau: NiveauRisque) -> Anomalie:
    return Anomalie(
        depot_id=uuid4(),
        workflow_run_id=98765,
        workflow_nom="Deploy",
        type=TypeAnomalie.ECHEC,
        niveau_risque=niveau,
    )
