"""Tests unitaires : agent correcteur (analyse cause + génération correctif via Claude).

Le client Anthropic est injecté (double asynchrone) — on ne consulte jamais la
vraie API en test. On vérifie que le correcteur : court-circuite l'appel LLM pour
une règle RELANCE connue ; appelle Claude avec les bons paramètres pour une cause
inconnue ; et remonte les refus.
"""
import json
from uuid import uuid4

import pytest

from src.agents.correcteur import Correcteur, RefusModele, TypeCorrection
from src.domaine.anomalie import Anomalie, NiveauRisque, TypeAnomalie
from src.domaine.regle import ActionRecommandee, OrigineRegle, RegleDiagnostic

MODELE = "claude-opus-4-8"


def _anomalie() -> Anomalie:
    return Anomalie(
        depot_id=uuid4(),
        workflow_run_id=12345678,
        workflow_nom="CI",
        type=TypeAnomalie.ECHEC,
        niveau_risque=NiveauRisque.LOW,
    )


def _regle(action: str) -> RegleDiagnostic:
    return RegleDiagnostic(
        nom="timeout-connexion-bd",
        description="Timeout de connexion à la base de données",
        pattern_log="connection timeout",
        action_recommandee=action,
        origine=OrigineRegle.MANUELLE,
    )


class _BlocTexte:
    type = "text"

    def __init__(self, text: str):
        self.text = text


class _Message:
    def __init__(self, contenu: str, stop_reason: str = "end_turn"):
        self.content = [_BlocTexte(contenu)]
        self.stop_reason = stop_reason
        self.stop_details = None


class _MessagesFake:
    def __init__(self, reponse: _Message):
        self._reponse = reponse
        self.appels: list[dict] = []

    async def create(self, **kwargs) -> _Message:
        self.appels.append(kwargs)
        return self._reponse


class _ClientFake:
    def __init__(self, reponse: _Message):
        self.messages = _MessagesFake(reponse)


def _reponse_json(**champs) -> _Message:
    return _Message(json.dumps(champs, ensure_ascii=False))


class TestCourtCircuitRelance:
    async def test_regle_relance_ne_consulte_pas_claude(self):
        client = _ClientFake(_reponse_json())  # ne devrait jamais être lu
        correcteur = Correcteur(client, modele=MODELE)
        proposition = await correcteur.proposer(
            _anomalie(), extrait_log="Error: connection timeout after 30s",
            regle=_regle(ActionRecommandee.RELANCE),
        )
        assert proposition.type == TypeCorrection.RELANCE
        assert client.messages.appels == []  # aucun appel LLM


class TestAnalyseViaClaude:
    async def test_cause_inconnue_appelle_claude_et_retourne_pr(self):
        reponse = _reponse_json(
            type="PULL_REQUEST",
            justification="Dépendance obsolète à corriger",
            titre_pr="fix: mise à jour de la dépendance",
            corps_pr="Correctif proposé par Mirador",
            correctif="--- a/requirements.txt\n+++ b/requirements.txt",
        )
        client = _ClientFake(reponse)
        correcteur = Correcteur(client, modele=MODELE)
        proposition = await correcteur.proposer(
            _anomalie(), extrait_log="ModuleNotFoundError: no module named 'x'", regle=None,
        )
        assert proposition.type == TypeCorrection.PULL_REQUEST
        assert proposition.titre_pr.startswith("fix:")
        assert proposition.correctif is not None

    async def test_claude_peut_recommander_relance(self):
        client = _ClientFake(_reponse_json(type="RELANCE", justification="Flaky test"))
        correcteur = Correcteur(client, modele=MODELE)
        proposition = await correcteur.proposer(
            _anomalie(), extrait_log="flaky", regle=None,
        )
        assert proposition.type == TypeCorrection.RELANCE

    async def test_parametres_appel_conformes(self):
        client = _ClientFake(_reponse_json(type="RELANCE", justification="x"))
        correcteur = Correcteur(client, modele=MODELE)
        await correcteur.proposer(_anomalie(), extrait_log="trace de log", regle=None)
        appel = client.messages.appels[0]
        assert appel["model"] == MODELE
        assert appel["thinking"] == {"type": "adaptive"}
        assert appel["output_config"]["effort"] == "high"
        assert appel["output_config"]["format"]["type"] == "json_schema"
        # Le log doit être transmis au modèle
        assert "trace de log" in json.dumps(appel["messages"], ensure_ascii=False)


class TestRefus:
    async def test_refus_leve_exception(self):
        client = _ClientFake(_Message("", stop_reason="refusal"))
        correcteur = Correcteur(client, modele=MODELE)
        with pytest.raises(RefusModele):
            await correcteur.proposer(_anomalie(), extrait_log="x", regle=None)
