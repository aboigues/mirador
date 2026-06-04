"""Tests unitaires : classification des anomalies et règles de risque."""
import pytest
from datetime import datetime, timezone
from uuid import uuid4

from src.domaine.anomalie import (
    Anomalie,
    NiveauRisque,
    StatutAnomalie,
    TypeAnomalie,
    ErreurTransitionInterdite,
)


class TestNiveauRisqueOrdre:
    def test_ordre_croissant(self):
        assert NiveauRisque.INFO < NiveauRisque.LOW
        assert NiveauRisque.LOW < NiveauRisque.MEDIUM
        assert NiveauRisque.MEDIUM < NiveauRisque.HIGH
        assert NiveauRisque.HIGH < NiveauRisque.CRITICAL

    def test_promotion_valide(self):
        anomalie = _anomalie_de_base(NiveauRisque.LOW)
        anomalie.promouvoir_risque(NiveauRisque.MEDIUM)
        assert anomalie.niveau_risque == NiveauRisque.MEDIUM

    def test_degradation_interdite(self):
        anomalie = _anomalie_de_base(NiveauRisque.HIGH)
        with pytest.raises(ErreurTransitionInterdite):
            anomalie.promouvoir_risque(NiveauRisque.LOW)

    def test_meme_niveau_interdit(self):
        anomalie = _anomalie_de_base(NiveauRisque.MEDIUM)
        with pytest.raises(ErreurTransitionInterdite):
            anomalie.promouvoir_risque(NiveauRisque.MEDIUM)


class TestTransitionsStatut:
    def test_ouverte_vers_en_cours(self):
        anomalie = _anomalie_de_base()
        anomalie.demarrer_traitement()
        assert anomalie.statut == StatutAnomalie.EN_COURS

    def test_en_cours_vers_resolue(self):
        anomalie = _anomalie_de_base()
        anomalie.demarrer_traitement()
        anomalie.marquer_resolue()
        assert anomalie.statut == StatutAnomalie.RESOLUE

    def test_en_cours_vers_escaladee(self):
        anomalie = _anomalie_de_base()
        anomalie.demarrer_traitement()
        anomalie.escalader()
        assert anomalie.statut == StatutAnomalie.ESCALADEE

    def test_escaladee_vers_en_cours(self):
        anomalie = _anomalie_de_base()
        anomalie.demarrer_traitement()
        anomalie.escalader()
        anomalie.reprendre_apres_validation()
        assert anomalie.statut == StatutAnomalie.EN_COURS

    def test_escaladee_vers_rejetee(self):
        anomalie = _anomalie_de_base()
        anomalie.demarrer_traitement()
        anomalie.escalader()
        anomalie.rejeter()
        assert anomalie.statut == StatutAnomalie.REJETEE

    def test_transition_invalide_resolue_vers_en_cours(self):
        anomalie = _anomalie_de_base()
        anomalie.demarrer_traitement()
        anomalie.marquer_resolue()
        with pytest.raises(ErreurTransitionInterdite):
            anomalie.demarrer_traitement()

    def test_ouverte_ne_peut_pas_etre_resolue_directement(self):
        anomalie = _anomalie_de_base()
        with pytest.raises(ErreurTransitionInterdite):
            anomalie.marquer_resolue()


class TestAnomalieValidation:
    def test_extrait_log_tronque_a_2000_chars(self):
        anomalie = _anomalie_de_base()
        long_log = "x" * 3000
        anomalie.extrait_log = long_log
        assert len(anomalie.extrait_log) <= 2000

    def test_identifiant_github_format_valide(self):
        from src.domaine.depot import DepotSurveille
        depot = DepotSurveille(
            identifiant_github="owner/repo",
            installation_id=123,
            responsables=["admin@example.com"],
        )
        assert depot.identifiant_github == "owner/repo"

    def test_identifiant_github_format_invalide(self):
        from src.domaine.depot import DepotSurveille
        with pytest.raises(ValueError):
            DepotSurveille(
                identifiant_github="invalide-sans-slash",
                installation_id=123,
                responsables=["admin@example.com"],
            )


def _anomalie_de_base(niveau: NiveauRisque = NiveauRisque.LOW) -> Anomalie:
    return Anomalie(
        depot_id=uuid4(),
        workflow_run_id=12345678,
        workflow_nom="CI",
        type=TypeAnomalie.ECHEC,
        niveau_risque=niveau,
    )
