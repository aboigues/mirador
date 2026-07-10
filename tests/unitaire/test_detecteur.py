"""Tests unitaires : détection et classification des anomalies (sans I/O)."""
from uuid import UUID, uuid4

import pytest

from src.agents.detecteur import Detecteur
from src.domaine.anomalie import NiveauRisque, TypeAnomalie
from src.domaine.regle import ActionRecommandee, OrigineRegle, RegleDiagnostic, StatutRegle

DELIVERY_ID = "550e8400-e29b-41d4-a716-446655440000"


def _message(
    *,
    conclusion: str = "failure",
    head_branch: str = "feature/x",
    debut: str = "2026-07-10T10:00:00Z",
    fin: str = "2026-07-10T10:05:00Z",
) -> dict:
    return {
        "delivery_id": DELIVERY_ID,
        "depot": "aboigues/k8t",
        "workflow_run_id": 12345678,
        "workflow_nom": "CI",
        "conclusion": conclusion,
        "head_branch": head_branch,
        "run_started_at": debut,
        "run_completed_at": fin,
    }


def _regle_log(pattern: str, statut: str = StatutRegle.ACTIVE) -> RegleDiagnostic:
    regle = RegleDiagnostic(
        nom="timeout-connexion-bd",
        description="Timeout de connexion à la base de données",
        pattern_log=pattern,
        action_recommandee=ActionRecommandee.RELANCE,
        origine=OrigineRegle.MANUELLE,
    )
    if statut != StatutRegle.ACTIVE:
        regle.statut = statut
    return regle


@pytest.fixture
def detecteur():
    return Detecteur()


@pytest.fixture
def depot_id():
    return uuid4()


class TestPasDAnomalie:
    def test_succes_ne_produit_aucune_anomalie(self, detecteur, depot_id):
        assert detecteur.detecter(_message(conclusion="success"), depot_id, []) is None

    def test_skipped_ne_produit_aucune_anomalie(self, detecteur, depot_id):
        assert detecteur.detecter(_message(conclusion="skipped"), depot_id, []) is None


class TestClassificationType:
    def test_failure_sur_branche_feature(self, detecteur, depot_id):
        a = detecteur.detecter(_message(conclusion="failure"), depot_id, [])
        assert a.type == TypeAnomalie.ECHEC
        assert a.niveau_risque == NiveauRisque.LOW

    def test_timed_out(self, detecteur, depot_id):
        a = detecteur.detecter(_message(conclusion="timed_out"), depot_id, [])
        assert a.type == TypeAnomalie.TIMEOUT
        assert a.niveau_risque == NiveauRisque.MEDIUM

    def test_cancelled_est_info(self, detecteur, depot_id):
        a = detecteur.detecter(_message(conclusion="cancelled"), depot_id, [])
        assert a.type == TypeAnomalie.AUTRE
        assert a.niveau_risque == NiveauRisque.INFO


class TestElevationBrancheProtegee:
    def test_failure_sur_main_est_eleve(self, detecteur, depot_id):
        a = detecteur.detecter(_message(conclusion="failure", head_branch="main"), depot_id, [])
        assert a.niveau_risque == NiveauRisque.MEDIUM  # LOW + 1

    def test_timeout_sur_master_est_eleve(self, detecteur, depot_id):
        a = detecteur.detecter(_message(conclusion="timed_out", head_branch="master"), depot_id, [])
        assert a.niveau_risque == NiveauRisque.HIGH  # MEDIUM + 1


class TestSeuilTimeout:
    def test_failure_depassant_le_seuil_devient_timeout(self, detecteur, depot_id):
        # 5 minutes de durée, seuil à 60s → reclassé TIMEOUT
        a = detecteur.detecter(
            _message(conclusion="failure"), depot_id, [], seuil_timeout_secondes=60
        )
        assert a.type == TypeAnomalie.TIMEOUT

    def test_failure_sous_le_seuil_reste_echec(self, detecteur, depot_id):
        a = detecteur.detecter(
            _message(conclusion="failure"), depot_id, [], seuil_timeout_secondes=3600
        )
        assert a.type == TypeAnomalie.ECHEC


class TestMatchingRegles:
    def test_regle_active_identifie_la_cause_et_reste_low(self, detecteur, depot_id):
        regle = _regle_log("connection timeout")
        a = detecteur.detecter(
            _message(conclusion="failure", head_branch="main"), depot_id, [regle],
            extrait_log="Error: connection timeout after 30s",
        )
        # Cause connue → auto-réparable, niveau LOW malgré la branche protégée
        assert a.niveau_risque == NiveauRisque.LOW
        assert a.cause_identifiee == regle.description
        assert a.regle_declenchee_id == regle.id

    def test_regle_inactive_ne_matche_pas(self, detecteur, depot_id):
        regle = _regle_log("connection timeout", statut=StatutRegle.INACTIVE)
        a = detecteur.detecter(
            _message(conclusion="failure"), depot_id, [regle],
            extrait_log="Error: connection timeout after 30s",
        )
        assert a.cause_identifiee is None
        assert a.regle_declenchee_id is None

    def test_aucune_regle_ne_matche(self, detecteur, depot_id):
        regle = _regle_log("out of memory")
        a = detecteur.detecter(
            _message(conclusion="failure"), depot_id, [regle],
            extrait_log="Error: connection refused",
        )
        assert a.cause_identifiee is None


class TestTracabilite:
    def test_correlation_id_repris_du_delivery_id(self, detecteur, depot_id):
        a = detecteur.detecter(_message(), depot_id, [])
        assert a.correlation_id == UUID(DELIVERY_ID)

    def test_champs_workflow_reportes(self, detecteur, depot_id):
        a = detecteur.detecter(_message(), depot_id, [])
        assert a.workflow_run_id == 12345678
        assert a.workflow_nom == "CI"
        assert a.depot_id == depot_id
