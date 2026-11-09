"""Sonde de l'état de GitHub Actions, d'après la page de statut publique.

Un run terminé en échec sans avoir lancé un seul job a deux causes possibles :
une panne de GitHub Actions (rien à faire côté dépôt) ou un fichier de workflow
invalide (à signaler). Cette sonde permet de les distinguer.
Cas réel : incident « Actions and Checks API » du 2026-11-09, où chaque run des
dépôts surveillés échouait au démarrage (docker-formation#50-52).
"""
from __future__ import annotations

import httpx

URL_COMPOSANTS = "https://www.githubstatus.com/api/v2/components.json"
_COMPOSANT_ACTIONS = "Actions"
_DELAI_SECONDES = 5.0


async def actions_degrade() -> bool:
    """Vrai si GitHub déclare le composant Actions autre qu'opérationnel.

    Les erreurs HTTP remontent : la décision « dans le doute, traiter le run
    normalement » appartient à l'appelant.
    """
    async with httpx.AsyncClient(timeout=_DELAI_SECONDES) as client:
        reponse = await client.get(URL_COMPOSANTS)
    reponse.raise_for_status()
    for composant in reponse.json().get("components", []):
        if composant.get("name") == _COMPOSANT_ACTIONS:
            return composant.get("status") != "operational"
    return False
