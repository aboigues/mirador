"""Tests unitaires : composition root (config des dépôts + câblage du pipeline).

Valide le parsing de la config MIRADOR_DEPOTS, les résolveurs (dépôt,
responsables) et le câblage de Traitement — sans instancier les vrais clients
réseau (I/O injectés).
"""
import json

import pytest

from src.domaine.regle import ActionRecommandee
from src.gestionnaires.entree import ConfigDepots, charger_depots, construire_traitement

DEPOT = "aboigues/k8t"

_CONFIG = [
    {
        "identifiant_github": DEPOT,
        "installation_id": 98765,
        "responsables": ["aboigues"],
        "seuil_timeout_secondes": 1800,
        "regles": [
            {
                "nom": "timeout-connexion-bd",
                "description": "Timeout de connexion à la base",
                "pattern_log": "connection timeout",
                "action_recommandee": "RELANCE",
                "origine": "MANUELLE",
            }
        ],
    }
]


class TestChargerDepots:
    def test_parse_config_valide(self):
        paires = charger_depots(json.dumps(_CONFIG))
        assert len(paires) == 1
        depot, regles = paires[0]
        assert depot.identifiant_github == DEPOT
        assert depot.installation_id == 98765
        assert depot.seuil_timeout_secondes == 1800
        assert regles[0].nom == "timeout-connexion-bd"
        assert regles[0].action_recommandee == ActionRecommandee.RELANCE

    def test_valeur_absente_donne_liste_vide(self):
        assert charger_depots(None) == []
        assert charger_depots("") == []

    def test_json_invalide_leve_erreur(self):
        with pytest.raises(ValueError):
            charger_depots("{ pas du json }")

    def test_depot_sans_responsable_leve_erreur(self):
        mauvais = [{"identifiant_github": DEPOT, "installation_id": 1}]
        with pytest.raises(ValueError):
            charger_depots(json.dumps(mauvais))


class TestConfigDepots:
    def test_resoudre_depot_connu(self):
        config = ConfigDepots(charger_depots(json.dumps(_CONFIG)))
        resolu = config.resoudre(DEPOT)
        assert resolu is not None
        depot, regles = resolu
        assert depot.identifiant_github == DEPOT
        assert len(regles) == 1

    def test_resoudre_depot_inconnu(self):
        config = ConfigDepots(charger_depots(json.dumps(_CONFIG)))
        assert config.resoudre("inconnu/x") is None

    def test_responsables(self):
        config = ConfigDepots(charger_depots(json.dumps(_CONFIG)))
        assert config.responsables(DEPOT) == ["aboigues"]
        assert config.responsables("inconnu/x") == []

    def test_depuis_env(self, monkeypatch):
        monkeypatch.setenv("MIRADOR_DEPOTS", json.dumps(_CONFIG))
        config = ConfigDepots.depuis_env()
        assert config.resoudre(DEPOT) is not None

    def test_depots_liste(self):
        config = ConfigDepots(charger_depots(json.dumps(_CONFIG)))
        assert [d.identifiant_github for d in config.depots()] == [DEPOT]


class _ActionsFake:
    def __init__(self):
        self.relances = []

    async def telecharger_logs(self, *a, **k):
        return b"aucun pattern"

    async def relancer_workflow(self, depot, run_id, installation_id):
        self.relances.append(run_id)


class _WriterFake:
    def __init__(self):
        self.evenements = []

    def ecrire(self, evenements):
        self.evenements.extend(evenements)
        return "checksum"


class _CorrecteurFake:
    async def proposer(self, anomalie, extrait_log, regle=None):
        from src.agents.correcteur import PropositionCorrection, TypeCorrection
        return PropositionCorrection(type=TypeCorrection.RELANCE, justification="x")


class TestCablage:
    async def test_construire_traitement_fonctionne(self):
        config = ConfigDepots(charger_depots(json.dumps(_CONFIG)))
        actions, writer = _ActionsFake(), _WriterFake()
        traitement = construire_traitement(
            config, actions=actions, writer=writer, correcteur=_CorrecteurFake()
        )
        # Un succès ne déclenche rien
        await traitement.traiter({
            "type": "workflow_run", "delivery_id": "550e8400-e29b-41d4-a716-446655440000",
            "depot": DEPOT, "workflow_run_id": 1, "workflow_nom": "CI",
            "conclusion": "success", "head_branch": "main",
        })
        assert writer.evenements == []

    async def test_traitement_relance_sur_echec_connu(self):
        config = ConfigDepots(charger_depots(json.dumps(_CONFIG)))
        actions, writer = _ActionsFake(), _WriterFake()
        # Les logs contiennent le pattern de la règle → LOW → relance auto
        actions.telecharger_logs = lambda *a, **k: _coro(b"Error: connection timeout after 30s")
        traitement = construire_traitement(
            config, actions=actions, writer=writer, correcteur=_CorrecteurFake()
        )
        await traitement.traiter({
            "type": "workflow_run", "delivery_id": "550e8400-e29b-41d4-a716-446655440001",
            "depot": DEPOT, "workflow_run_id": 42, "workflow_nom": "CI",
            "conclusion": "failure", "head_branch": "feature/x",
        })
        assert actions.relances == [42]


async def _coro(valeur):
    return valeur
