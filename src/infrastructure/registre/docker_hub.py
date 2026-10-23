"""Client Docker Hub : liste des tags réellement publiés d'une image.

Sert à choisir les tags à mesurer pour un bump d'image, au lieu de laisser le
Correcteur en proposer un de mémoire (kubernetes-formation#216). API publique,
sans authentification : `GET /v2/repositories/<ns>/<repo>/tags`.
"""
from __future__ import annotations

import httpx
import structlog

log = structlog.get_logger(__name__)

_BASE_URL = "https://hub.docker.com"
# Tri par date de publication : les candidats (correctifs récents de la ligne
# en place) sont en tête. Au-delà, les pages ne contiennent que des tags anciens.
_MAX_PAGES = 5


class ClientDockerHub:
    def __init__(self, *, base_url: str = _BASE_URL, max_pages: int = _MAX_PAGES) -> None:
        self._base_url = base_url
        self._max_pages = max_pages

    async def lister_tags(self, nom: str, contenant: str) -> list[str]:
        """Noms des tags de `nom` (ex. `grafana/grafana`) contenant `contenant`.

        Le filtre `name` du registre est une recherche par sous-chaîne (`3.2`
        ramène aussi `3.13.2`) : le tri fin revient à l'appelant. Ne masque pas
        les erreurs HTTP : l'appelant est résilient à un registre indisponible.
        """
        url: str | None = f"{self._base_url}/v2/repositories/{nom}/tags"
        params: dict[str, str] | None = {
            "page_size": "100", "name": contenant, "ordering": "last_updated",
        }
        tags: list[str] = []
        async with httpx.AsyncClient(follow_redirects=True) as client:
            for _ in range(self._max_pages):
                if url is None:
                    break
                reponse = await client.get(url, params=params)
                reponse.raise_for_status()
                donnees = reponse.json()
                tags += [resultat["name"] for resultat in donnees.get("results", [])]
                # L'URL `next` porte déjà tous les paramètres de la requête.
                url, params = donnees.get("next"), None
        log.info("docker_hub.tags_listes", image=nom, filtre=contenant, nombre=len(tags))
        return tags
