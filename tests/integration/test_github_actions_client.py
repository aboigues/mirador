"""Tests d'intégration : client GitHub Actions (relance, logs, PR, Issues).

Le client obtient un token d'installation via un fournisseur (GitHubAppAuth en
prod, stub ici puisque l'auth est testée séparément), puis appelle l'API GitHub.
Les appels HTTP sont stubés via respx ; le client réel (httpx) n'est pas mocké.
"""
import httpx
import pytest
import respx

from src.infrastructure.github.actions_client import GitHubActionsClient

BASE_URL = "https://api.github.com"
DEPOT = "aboigues/k8t"
INSTALLATION_ID = 98765
RUN_ID = 12345678
TOKEN = "ghs_test_token"


class _AuthStub:
    """Fournisseur de token minimal (l'auth réelle est testée dans test_github_app_auth)."""

    def __init__(self):
        self.appels: list[int] = []

    async def obtenir_token_installation(self, installation_id: int) -> str:
        self.appels.append(installation_id)
        return TOKEN


@pytest.fixture
def auth():
    return _AuthStub()


@pytest.fixture
def client(auth):
    return GitHubActionsClient(auth, base_url=BASE_URL)


def _url(chemin: str) -> str:
    return f"{BASE_URL}/repos/{DEPOT}{chemin}"


class TestRelanceWorkflow:
    @respx.mock
    async def test_relance_appelle_endpoint_rerun(self, client, auth):
        route = respx.post(_url(f"/actions/runs/{RUN_ID}/rerun")).mock(
            return_value=httpx.Response(201)
        )
        await client.relancer_workflow(DEPOT, RUN_ID, INSTALLATION_ID)
        assert route.called
        assert auth.appels == [INSTALLATION_ID]
        assert route.calls.last.request.headers["Authorization"] == f"Bearer {TOKEN}"

    @respx.mock
    async def test_erreur_http_remonte(self, client):
        respx.post(_url(f"/actions/runs/{RUN_ID}/rerun")).mock(
            return_value=httpx.Response(404, json={"message": "Not Found"})
        )
        with pytest.raises(httpx.HTTPStatusError):
            await client.relancer_workflow(DEPOT, RUN_ID, INSTALLATION_ID)


class TestTelechargementLogs:
    @respx.mock
    async def test_logs_suit_la_redirection_et_retourne_les_octets(self, client):
        respx.get(_url(f"/actions/runs/{RUN_ID}/logs")).mock(
            return_value=httpx.Response(302, headers={"Location": "https://dl.github.com/logs.zip"})
        )
        respx.get("https://dl.github.com/logs.zip").mock(
            return_value=httpx.Response(200, content=b"PK\x03\x04zip-des-logs")
        )
        contenu = await client.telecharger_logs(DEPOT, RUN_ID, INSTALLATION_ID)
        assert contenu == b"PK\x03\x04zip-des-logs"


class TestIssues:
    @respx.mock
    async def test_ouvrir_issue_retourne_numero_et_url(self, client):
        route = respx.post(_url("/issues")).mock(
            return_value=httpx.Response(
                201, json={"number": 42, "html_url": f"https://github.com/{DEPOT}/issues/42"}
            )
        )
        resultat = await client.ouvrir_issue(
            DEPOT, INSTALLATION_ID,
            titre="[Mirador] Anomalie HIGH — CI",
            corps="détails",
            labels=["mirador", "high"],
        )
        assert resultat["number"] == 42
        assert resultat["html_url"].endswith("/issues/42")
        import json
        envoye = json.loads(route.calls.last.request.content)
        assert envoye["title"].startswith("[Mirador]")
        assert envoye["labels"] == ["mirador", "high"]

    @respx.mock
    async def test_commenter_issue(self, client):
        route = respx.post(_url("/issues/42/comments")).mock(
            return_value=httpx.Response(201, json={"id": 1})
        )
        await client.commenter_issue(DEPOT, INSTALLATION_ID, 42, "✅ approuvé")
        import json
        assert json.loads(route.calls.last.request.content)["body"] == "✅ approuvé"

    @respx.mock
    async def test_fermer_issue_ajoute_label_et_ferme(self, client):
        route_label = respx.post(_url("/issues/42/labels")).mock(
            return_value=httpx.Response(200, json=[])
        )
        route_patch = respx.patch(_url("/issues/42")).mock(
            return_value=httpx.Response(200, json={"number": 42, "state": "closed"})
        )
        await client.fermer_issue(DEPOT, INSTALLATION_ID, 42, label="resolved")
        assert route_label.called and route_patch.called
        import json
        assert json.loads(route_label.calls.last.request.content)["labels"] == ["resolved"]
        assert json.loads(route_patch.calls.last.request.content)["state"] == "closed"


class TestPullRequest:
    @respx.mock
    async def test_creer_pr_retourne_numero_et_url(self, client):
        route = respx.post(_url("/pulls")).mock(
            return_value=httpx.Response(
                201, json={"number": 7, "html_url": f"https://github.com/{DEPOT}/pull/7"}
            )
        )
        resultat = await client.creer_pull_request(
            DEPOT, INSTALLATION_ID,
            titre="fix: correctif proposé",
            corps="correctif automatique Mirador",
            branche_source="mirador/fix-timeout",
            branche_cible="main",
        )
        assert resultat["number"] == 7
        import json
        envoye = json.loads(route.calls.last.request.content)
        assert envoye["head"] == "mirador/fix-timeout"
        assert envoye["base"] == "main"


class TestDeclencherWorkflow:
    @respx.mock
    async def test_declencher_workflow_envoie_ref_et_inputs(self, client):
        route = respx.post(_url("/actions/workflows/mirador-autofix.yml/dispatches")).mock(
            return_value=httpx.Response(204)
        )
        await client.declencher_workflow(
            DEPOT, "mirador-autofix.yml", "main",
            {"head_branch": "mirador/fix-1", "modules": "golang.org/x/net@v0.55.0"},
            INSTALLATION_ID,
        )
        import json
        envoye = json.loads(route.calls.last.request.content)
        assert envoye["ref"] == "main"
        assert envoye["inputs"]["head_branch"] == "mirador/fix-1"


class TestMaterialisationCorrectif:
    @respx.mock
    async def test_obtenir_sha_tete(self, client):
        respx.get(_url("/git/ref/heads/main")).mock(
            return_value=httpx.Response(200, json={"object": {"sha": "abc123"}})
        )
        assert await client.obtenir_sha_tete(DEPOT, "main", INSTALLATION_ID) == "abc123"

    @respx.mock
    async def test_creer_branche(self, client):
        route = respx.post(_url("/git/refs")).mock(
            return_value=httpx.Response(201, json={"ref": "refs/heads/mirador/fix-1"})
        )
        await client.creer_branche(DEPOT, "mirador/fix-1", "abc123", INSTALLATION_ID)
        import json
        envoye = json.loads(route.calls.last.request.content)
        assert envoye["ref"] == "refs/heads/mirador/fix-1"
        assert envoye["sha"] == "abc123"

    @respx.mock
    async def test_televerser_fichier_nouveau(self, client):
        # GET du sha existant → 404 (fichier absent) ; PUT sans sha (création).
        respx.get(_url("/contents/go.mod")).mock(return_value=httpx.Response(404))
        route = respx.put(_url("/contents/go.mod")).mock(
            return_value=httpx.Response(201, json={"content": {"path": "go.mod"}})
        )
        await client.televerser_fichier(
            DEPOT, "go.mod", "module k8t\n", "mirador/fix-1", "fix", INSTALLATION_ID
        )
        import base64, json
        envoye = json.loads(route.calls.last.request.content)
        assert base64.b64decode(envoye["content"]).decode() == "module k8t\n"
        assert envoye["branch"] == "mirador/fix-1"
        assert "sha" not in envoye

    @respx.mock
    async def test_televerser_fichier_existant_passe_le_sha(self, client):
        respx.get(_url("/contents/go.mod")).mock(
            return_value=httpx.Response(200, json={"sha": "old-sha"})
        )
        route = respx.put(_url("/contents/go.mod")).mock(
            return_value=httpx.Response(200, json={})
        )
        await client.televerser_fichier(
            DEPOT, "go.mod", "nouveau", "mirador/fix-1", "fix", INSTALLATION_ID
        )
        import json
        assert json.loads(route.calls.last.request.content)["sha"] == "old-sha"


class TestLectureDepot:
    @respx.mock
    async def test_lister_fichiers_ne_garde_que_les_blobs(self, client):
        respx.get(_url("/git/trees/main?recursive=1")).mock(
            return_value=httpx.Response(200, json={
                "tree": [
                    {"path": "tp08", "type": "tree", "sha": "d1"},
                    {"path": "tp08/compose.yaml", "type": "blob", "sha": "b1", "size": 512},
                    {"path": "README.md", "type": "blob", "sha": "b2", "size": 100},
                ],
                "truncated": False,
            })
        )
        fichiers = await client.lister_fichiers(DEPOT, "main", INSTALLATION_ID)
        assert fichiers == [
            {"path": "tp08/compose.yaml", "size": 512},
            {"path": "README.md", "size": 100},
        ]

    @respx.mock
    async def test_lire_fichier_decode_le_base64(self, client):
        import base64
        contenu = base64.b64encode("image: postgres:18-alpine\n".encode()).decode()
        respx.get(_url("/contents/tp08/compose.yaml?ref=main")).mock(
            return_value=httpx.Response(200, json={"encoding": "base64", "content": contenu})
        )
        resultat = await client.lire_fichier(DEPOT, "tp08/compose.yaml", "main", INSTALLATION_ID)
        assert resultat == "image: postgres:18-alpine\n"

    @respx.mock
    async def test_lire_fichier_absent_retourne_none(self, client):
        respx.get(_url("/contents/inexistant.yaml?ref=main")).mock(
            return_value=httpx.Response(404, json={"message": "Not Found"})
        )
        assert await client.lire_fichier(DEPOT, "inexistant.yaml", "main", INSTALLATION_ID) is None
