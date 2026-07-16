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

MODELE = "claude-haiku-4-5"


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
        # Économie de coût : pas de thinking ni d'effort (aussi incompatibles Haiku),
        # sortie structurée seule, max_tokens borné.
        assert "thinking" not in appel
        assert "effort" not in appel["output_config"]
        assert appel["output_config"]["format"]["type"] == "json_schema"
        assert appel["max_tokens"] <= 4000
        # Le log doit être transmis au modèle
        assert "trace de log" in json.dumps(appel["messages"], ensure_ascii=False)

    async def test_modele_par_defaut_est_haiku(self):
        # La prod n'injecte pas de modèle (entree.py) : le défaut est ce qui est
        # réellement facturé. Un changement de modèle doit être délibéré.
        client = _ClientFake(_reponse_json(type="RELANCE", justification="x"))
        await Correcteur(client).proposer(_anomalie(), extrait_log="x", regle=None)
        assert client.messages.appels[0]["model"] == "claude-haiku-4-5"

    async def test_log_volumineux_est_tronque(self):
        # Un log de plusieurs centaines de Ko ne doit pas être envoyé en entier.
        client = _ClientFake(_reponse_json(type="RELANCE", justification="x"))
        correcteur = Correcteur(client, modele=MODELE)
        gros_log = "L" * 200_000 + "\nERREUR FINALE: govulncheck exit 3"
        await correcteur.proposer(_anomalie(), extrait_log=gros_log, regle=None)
        envoye = json.dumps(client.messages.appels[0]["messages"], ensure_ascii=False)
        assert len(envoye) < 20_000  # tronqué, pas les 200 Ko
        assert "ERREUR FINALE" in envoye  # la fin (les erreurs) est conservée


class TestRefus:
    async def test_refus_leve_exception(self):
        client = _ClientFake(_Message("", stop_reason="refusal"))
        correcteur = Correcteur(client, modele=MODELE)
        with pytest.raises(RefusModele):
            await correcteur.proposer(_anomalie(), extrait_log="x", regle=None)
