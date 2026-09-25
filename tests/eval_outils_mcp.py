"""Évalue si un vrai modèle choisit le bon outil MCP de Mirador (CS-003).

Pourquoi ce script existe
-------------------------
Les tests d'intégration vérifient le câblage du serveur ; ils ne disent pas si un
assistant comprend les descriptions des outils. Seul un vrai modèle peut le dire
(même leçon que le rejeu #129). Ce script présente 10 questions de mainteneur au
modèle avec les définitions d'outils publiées par le serveur, et compare son
premier appel d'outil à l'attendu : bon outil, et bons arguments pour les clés
listées.

Modèle : celui du correcteur (`claude-haiku-4-5`), le plus petit. Si lui choisit
correctement, un modèle plus grand le fera aussi.

Usage (nécessite ANTHROPIC_API_KEY — donc via le workflow `eval-outils-mcp.yml`) :
    python -m tests.eval_outils_mcp
Sortie : code 0 si au moins 9 questions sur 10 sont correctes, 1 sinon.
"""
from __future__ import annotations

import asyncio
import sys
from typing import Any
from uuid import uuid4

from anthropic import AsyncAnthropic
from mcp.client import Client

from src.domaine.depot import DepotSurveille
from src.domaine.journal import EvenementJournal, TypeEvenement
from src.gestionnaires.entree import ConfigDepots
from src.infrastructure.stockage.bucket import ClientBucketMemoire
from src.infrastructure.stockage.writer import WriterAudit
from src.interface_mcp.serveur import creer_serveur

_MODELE = "claude-haiku-4-5"
_SEUIL = 9
K8T = "aboigues/k8t"
CORR_REJETEE = uuid4()
CORR_ESCALADEE = uuid4()

_SYSTEME = (
    "Tu es l'assistant d'un mainteneur de dépôts GitHub. Tu as accès aux outils de "
    "Mirador, l'agent qui surveille leurs pipelines CI/CD. Réponds en appelant l'outil "
    "adapté."
)

# (question, outil attendu, arguments attendus — seules ces clés sont comparées)
_CAS: list[tuple[str, str, dict[str, Any]]] = [
    ("Quels dépôts sont surveillés par Mirador ?", "lister_depots", {}),
    (f"Sur {K8T}, quelles anomalies attendent encore une décision humaine ?",
     "lister_anomalies", {"depot": K8T, "statut": "ESCALADÉE"}),
    (f"Montre-moi les anomalies de niveau HIGH sur {K8T}.",
     "lister_anomalies", {"depot": K8T, "niveau_risque": "HIGH"}),
    (f"Donne-moi au plus 5 anomalies du dépôt {K8T}.",
     "lister_anomalies", {"depot": K8T, "limite": 5}),
    (f"Que s'est-il passé, étape par étape, pour l'anomalie {CORR_ESCALADEE} ?",
     "historique_anomalie", {"correlation_id": str(CORR_ESCALADEE)}),
    (f"Qui a rejeté la correction proposée pour l'anomalie {CORR_REJETEE} ?",
     "historique_anomalie", {"correlation_id": str(CORR_REJETEE)}),
    ("Le journal d'audit de Mirador a-t-il été modifié depuis sa dernière écriture ?",
     "verifier_integrite_journal", {}),
    ("Avant le post-mortem, je veux être sûr que personne n'a trafiqué l'historique "
     "des incidents. Tu peux vérifier ?", "verifier_integrite_journal", {}),
    (f"Sur {K8T}, un run sur main a échoué avec ce log : « npm ERR! network ECONNRESET ». "
     "Comment Mirador le classerait-il ?",
     "classer_echec", {"depot": K8T, "conclusion": "failure", "branche": "main"}),
    (f"Le workflow de {K8T} sur la branche dev a été interrompu pour dépassement de délai "
     "(conclusion timed_out). Quel niveau de risque Mirador lui donnerait-il ?",
     "classer_echec", {"depot": K8T, "conclusion": "timed_out", "branche": "dev"}),
]


def _serveur():
    bucket = ClientBucketMemoire()

    def evt(corr, type_evenement, niveau, acteur="mirador-agent"):
        return EvenementJournal(correlation_id=corr, type_evenement=type_evenement,
                                niveau_risque=niveau, depot=K8T, acteur=acteur,
                                statut="SUCCÈS", workflow_run_id=1, type_anomalie="ÉCHEC")

    WriterAudit(bucket).ecrire([
        evt(CORR_REJETEE, TypeEvenement.DETECTION, "MEDIUM"),
        evt(CORR_REJETEE, TypeEvenement.REJET, "MEDIUM", acteur="aboigues"),
        evt(CORR_ESCALADEE, TypeEvenement.DETECTION, "HIGH"),
        evt(CORR_ESCALADEE, TypeEvenement.ESCALADE, "HIGH"),
    ])
    config = ConfigDepots([(
        DepotSurveille(identifiant_github=K8T, installation_id=1, responsables=["aboigues"]), [],
    )])
    return creer_serveur(config, bucket)


def _conforme(appel: dict[str, Any], outil: str, attendus: dict[str, Any]) -> bool:
    if appel["name"] != outil:
        return False
    return all(appel["input"].get(cle) == valeur for cle, valeur in attendus.items())


async def principal() -> int:
    async with Client(_serveur()) as client:
        outils = [
            {"name": o.name, "description": o.description, "input_schema": o.input_schema}
            for o in (await client.list_tools()).tools
        ]

    anthropic = AsyncAnthropic()
    reussis = 0
    for question, outil, attendus in _CAS:
        reponse = await anthropic.messages.create(
            model=_MODELE,
            max_tokens=1024,
            system=_SYSTEME,
            tools=outils,
            tool_choice={"type": "any"},
            messages=[{"role": "user", "content": question}],
        )
        appel = next(
            ({"name": b.name, "input": b.input} for b in reponse.content if b.type == "tool_use"),
            {"name": None, "input": {}},
        )
        ok = _conforme(appel, outil, attendus)
        reussis += ok
        print(f"{'OK   ' if ok else 'ÉCHEC'} | {question}")
        if not ok:
            print(f"        attendu : {outil} {attendus}")
            print(f"        obtenu  : {appel['name']} {appel['input']}")

    print(f"\n{reussis}/{len(_CAS)} correctes (seuil : {_SEUIL}) — modèle {_MODELE}")
    return 0 if reussis >= _SEUIL else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(principal()))
