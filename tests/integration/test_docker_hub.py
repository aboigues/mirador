"""Tests d'intégration : client du registre Docker Hub (liste des tags publiés).

Les appels HTTP sont stubés via respx ; le client réel (httpx) n'est pas mocké.
Format des réponses calqué sur l'API réelle (`/v2/repositories/<ns>/<repo>/tags`,
interrogée le 2026-10-23 pour grafana/grafana et alpine/helm).
"""
import httpx
import pytest
import respx

from src.infrastructure.registre.docker_hub import ClientDockerHub

URL_TAGS = "https://hub.docker.com/v2/repositories/grafana/grafana/tags"


def _page(noms: list[str], suivante: str | None = None) -> dict:
    return {"count": 99, "next": suivante, "results": [{"name": n} for n in noms]}


class TestListerTags:
    @respx.mock
    async def test_filtre_et_ordre_transmis_au_registre(self):
        route = respx.get(URL_TAGS).mock(
            return_value=httpx.Response(200, json=_page(["13.2.4", "13.2.3"]))
        )
        tags = await ClientDockerHub().lister_tags("grafana/grafana", "13.")
        assert tags == ["13.2.4", "13.2.3"]
        params = route.calls.last.request.url.params
        assert params["name"] == "13."
        assert params["ordering"] == "last_updated"
        assert params["page_size"] == "100"

    @respx.mock
    async def test_suit_la_pagination(self):
        page2 = f"{URL_TAGS}?page=2&page_size=100&name=13.&ordering=last_updated"
        respx.get(URL_TAGS, params={"page": "2"}).mock(
            return_value=httpx.Response(200, json=_page(["13.2.0"]))
        )
        respx.get(URL_TAGS).mock(
            return_value=httpx.Response(200, json=_page(["13.2.4"], suivante=page2))
        )
        assert await ClientDockerHub().lister_tags("grafana/grafana", "13.") == ["13.2.4", "13.2.0"]

    @respx.mock
    async def test_pagination_bornee(self):
        # Une image à milliers de tags ne doit pas coûter des dizaines d'appels :
        # les tags sont triés par date de publication, les candidats sont en tête.
        boucle = f"{URL_TAGS}?page=2"
        route = respx.get(URL_TAGS).mock(
            return_value=httpx.Response(200, json=_page(["x"], suivante=boucle))
        )
        tags = await ClientDockerHub(max_pages=3).lister_tags("grafana/grafana", "13.")
        assert route.call_count == 3
        assert tags == ["x", "x", "x"]

    @respx.mock
    async def test_erreur_http_remonte(self):
        respx.get(URL_TAGS).mock(return_value=httpx.Response(429))
        with pytest.raises(httpx.HTTPStatusError):
            await ClientDockerHub().lister_tags("grafana/grafana", "13.")
