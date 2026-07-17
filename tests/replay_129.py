"""Rejoue l'anomalie kubernetes-formation#129 contre le VRAI modèle Claude.

Pourquoi ce script existe
-------------------------
Sur le run 29509874217 (« Weekly Image Vulnerability Scan »), 6 jobs sur 35
échouaient sur de vraies vulnérabilités OS. Mirador a répondu « échec réseau
transitoire, une relance devrait résoudre le problème » — une cause inventée,
tirée du log d'un job qui avait RÉUSSI. Le responsable a rejeté la proposition.

Les tests à doubles vérifient le câblage ; ils ne peuvent pas dire si le modèle
juge correctement. Ce script ferme cet écart : il soumet au vrai correcteur
l'extrait réellement produit par `assembler_extrait_jobs` à partir des logs
GitHub authentiques de ce run, et vérifie que Mirador ne conclut plus à une
relance.

La fixture `fixtures/run-29509874217-jobs-en-echec.txt` a été produite en
téléchargeant les logs des 6 jobs en échec (942 842 caractères) via
`/actions/jobs/{id}/logs`, puis en les passant dans `assembler_extrait_jobs`.

Usage (nécessite ANTHROPIC_API_KEY — donc via le workflow `replay-129.yml`) :
    python -m tests.replay_129
Sortie : code 0 si le verdict est acceptable, 1 sinon.
"""
from __future__ import annotations

import asyncio
import pathlib
import sys
from uuid import uuid4

from anthropic import AsyncAnthropic

from src.agents.correcteur import Correcteur, TypeCorrection
from src.domaine.anomalie import Anomalie, NiveauRisque, TypeAnomalie

_FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "run-29509874217-jobs-en-echec.txt"


def _anomalie() -> Anomalie:
    return Anomalie(
        depot_id=uuid4(),
        workflow_run_id=29509874217,
        workflow_nom="Weekly Image Vulnerability Scan",
        type=TypeAnomalie.ECHEC,
        niveau_risque=NiveauRisque.MEDIUM,
    )


async def principal() -> int:
    extrait = _FIXTURE.read_text(encoding="utf-8")
    correcteur = Correcteur(AsyncAnthropic())
    proposition = await correcteur.proposer(_anomalie(), extrait, regle=None)

    print(f"Type        : {proposition.type}")
    print(f"Justification : {proposition.justification}\n")

    # Le verdict d'origine, celui que le responsable a rejeté.
    if proposition.type == TypeCorrection.RELANCE:
        print("ÉCHEC — Mirador propose encore une RELANCE : elle re-échouerait "
              "à l'identique, les vulnérabilités étant bien réelles.")
        return 1

    # ABSTENTION (« je ne sais pas ») et PULL_REQUEST (correctif d'image) sont
    # tous deux honnêtes : aucun ne fait perdre un run ni ne masque l'échec.
    print(f"OK — verdict {proposition.type} : Mirador n'invente plus de cause "
          f"transitoire sur cet échec réel.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(principal()))
