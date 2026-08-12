"""Rejoue l'anomalie kubernetes-formation#142 contre le VRAI modèle Claude.

Pourquoi ce script existe
-------------------------
Sur le run 31357877181 (« Weekly Image Vulnerability Scan »), le log montrait
déjà la cause exacte : CVE-2026-44605 sur `rpm` (amazon/aws-cli:2.36.8, version
corrigée 4.16.1.3-29.amzn2023.0.7 disponible) et une CVE similaire sur l'image
`telemachlearning/wordpress:6.8-php8.3-apache`. Mirador a rendu ABSTENTION avec
un diagnostic correct (barrière de sécurité, pas un bug) mais peu utile — il ne
savait pas dans quel fichier du dépôt ces images étaient référencées.

mirador#25 donne au correcteur un accès en lecture au dépôt (`lecture_depot`),
activé en prod pour kubernetes-formation. Ce script rejoue l'anomalie avec les
DEUX extraits réels que Mirador recevrait aujourd'hui : le log assemblé par
`assembler_extrait_jobs` (fixture `run-31357877181-jobs-en-echec.txt`, produite
à partir des logs authentiques des 3 jobs en échec) et le contexte dépôt
assemblé par `assembler_contexte_depot` — EXACTEMENT ce que produirait
`Traitement._recuperer_contexte_depot` en prod.

Un premier passage (budget 12 000, sélection par ordre d'arbre) évinçait les
fichiers pertinents : kubernetes-formation a 194 fichiers de manifeste, et les
3 qui référencent réellement les images fautives se classaient 98e/150e/190e
par taille. Mirador s'abstenait honnêtement (pas d'hallucination) mais sans
matérialiser de correctif. Le correcteur cible désormais la recherche (Code
Search sur les références d'image repérées dans le log) avant de compléter par
taille croissante — cf. `Traitement._recuperer_contexte_depot`. La fixture de
contexte dépôt ci-dessous reflète ce nouvel algorithme : `tp02/exercice10/
wordpress-app.yaml` et `tp03/14-network-storage-examples-secure.yaml`, les deux
fichiers qui référencent effectivement les images en cause, y sont présents.

Usage (nécessite ANTHROPIC_API_KEY — donc via le workflow `replay-142.yml`) :
    python -m tests.replay_142
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

_FIXTURE_LOG = pathlib.Path(__file__).parent / "fixtures" / "run-31357877181-jobs-en-echec.txt"
_FIXTURE_CONTEXTE = (
    pathlib.Path(__file__).parent / "fixtures" / "kubernetes-formation-contexte-depot.txt"
)


def _anomalie() -> Anomalie:
    return Anomalie(
        depot_id=uuid4(),
        workflow_run_id=31357877181,
        workflow_nom="Weekly Image Vulnerability Scan",
        type=TypeAnomalie.ECHEC,
        niveau_risque=NiveauRisque.MEDIUM,
    )


async def principal() -> int:
    extrait = _FIXTURE_LOG.read_text(encoding="utf-8")
    contexte_depot = _FIXTURE_CONTEXTE.read_text(encoding="utf-8")
    correcteur = Correcteur(AsyncAnthropic())
    proposition = await correcteur.proposer(
        _anomalie(), extrait, regle=None, contexte_depot=contexte_depot
    )

    print(f"Type          : {proposition.type}")
    print(f"Justification : {proposition.justification}\n")
    if proposition.titre_pr:
        print(f"Titre PR      : {proposition.titre_pr}")
    if proposition.fichiers:
        print(f"Fichiers proposés ({len(proposition.fichiers)}) :")
        for f in proposition.fichiers:
            print(f"  - {f.chemin}")
    else:
        print("Fichiers proposés : aucun")

    if proposition.type == TypeCorrection.RELANCE:
        print("\nÉCHEC — Mirador propose une RELANCE : elle re-échouerait à "
              "l'identique, les vulnérabilités étant bien réelles.")
        return 1

    print(f"\nOK — verdict {proposition.type}, aucune cause inventée.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(principal()))
