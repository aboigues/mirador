"""Tests unitaires : catalogue de règles de diagnostic."""
import pytest
from uuid import uuid4

from src.domaine.regle import RegleDiagnostic, ActionRecommandee, OrigineRegle, StatutRegle


class TestRegleDiagnostic:
    def test_creation_regle_manuelle(self):
        regle = RegleDiagnostic(
            nom="timeout-connexion-bd",
            description="Détecte les timeouts de connexion base de données",
            pattern_log="connection timeout",
            action_recommandee=ActionRecommandee.RELANCE,
            origine=OrigineRegle.MANUELLE,
        )
        assert regle.statut == StatutRegle.ACTIVE
        assert regle.score_confiance == 0.0
        assert regle.utilisations == 0

    def test_regle_apprise_en_attente_validation(self):
        regle = RegleDiagnostic(
            nom="pattern-detecte",
            description="Règle détectée automatiquement",
            pattern_log="OOM killed",
            action_recommandee=ActionRecommandee.RELANCE,
            origine=OrigineRegle.APPRISE,
        )
        assert regle.statut == StatutRegle.EN_ATTENTE_VALIDATION

    def test_score_confiance_calcule(self):
        regle = RegleDiagnostic(
            nom="test-regle",
            description="Règle de test",
            action_recommandee=ActionRecommandee.PULL_REQUEST,
            origine=OrigineRegle.MANUELLE,
        )
        regle.enregistrer_utilisation(succes=True)
        regle.enregistrer_utilisation(succes=True)
        regle.enregistrer_utilisation(succes=False)
        assert regle.utilisations == 3
        assert regle.succes == 2
        assert abs(regle.score_confiance - 2 / 3) < 0.001

    def test_score_confiance_zero_sans_utilisations(self):
        regle = RegleDiagnostic(
            nom="test-regle",
            description="Règle de test",
            action_recommandee=ActionRecommandee.RELANCE,
            origine=OrigineRegle.MANUELLE,
        )
        assert regle.score_confiance == 0.0

    def test_match_par_pattern_log(self):
        regle = RegleDiagnostic(
            nom="oom",
            description="Out of memory",
            pattern_log="OOM|out of memory",
            action_recommandee=ActionRecommandee.RELANCE,
            origine=OrigineRegle.MANUELLE,
        )
        assert regle.correspond_au_log("Process killed: OOM")
        assert regle.correspond_au_log("Error: out of memory at step 3")
        assert not regle.correspond_au_log("Connection refused")

    def test_match_par_code_sortie(self):
        regle = RegleDiagnostic(
            nom="exit-137",
            description="Code de sortie 137 (OOM)",
            code_sortie=137,
            action_recommandee=ActionRecommandee.RELANCE,
            origine=OrigineRegle.MANUELLE,
        )
        assert regle.correspond_au_code_sortie(137)
        assert not regle.correspond_au_code_sortie(1)

    def test_seules_regles_actives_applicables(self):
        regle = RegleDiagnostic(
            nom="inactive",
            description="Règle inactive",
            action_recommandee=ActionRecommandee.RELANCE,
            origine=OrigineRegle.APPRISE,
        )
        assert not regle.est_applicable()

        regle_active = RegleDiagnostic(
            nom="active",
            description="Règle active",
            action_recommandee=ActionRecommandee.RELANCE,
            origine=OrigineRegle.MANUELLE,
        )
        assert regle_active.est_applicable()
