"""Client GitHub Actions : relance de workflow, logs, pull requests, issues.

S'authentifie via un fournisseur de token d'installation (`GitHubAppAuth`). Les
interventions restent volontairement limitées à RELANCE et PULL_REQUEST (jamais
de commit direct) ; les Issues portent le flux de validation humaine.
"""
from __future__ import annotations

import base64
import io
import zipfile
from typing import Any, Optional, Protocol

import httpx
import structlog

log = structlog.get_logger(__name__)

_API_VERSION = "2022-11-28"


def extraire_texte_logs(contenu: bytes) -> str:
    """Extrait le texte des logs GitHub à partir des octets renvoyés par l'API.

    L'endpoint `/actions/runs/{id}/logs` renvoie une archive ZIP (un .txt par
    étape). On concatène le texte de toutes les entrées. Si les octets ne sont
    pas une archive ZIP valide (logs déjà textuels, doubles de test), on retombe
    sur un décodage UTF-8 tolérant.
    """
    if not contenu:
        return ""
    try:
        with zipfile.ZipFile(io.BytesIO(contenu)) as archive:
            morceaux = [
                archive.read(info).decode("utf-8", errors="ignore")
                for info in archive.infolist()
                if not info.is_dir()
            ]
        return "\n".join(morceaux)
    except zipfile.BadZipFile:
        return contenu.decode("utf-8", errors="ignore")


class FournisseurToken(Protocol):
    async def obtenir_token_installation(self, installation_id: int) -> str: ...


class GitHubActionsClient:
    def __init__(
        self,
        auth: FournisseurToken,
        *,
        base_url: str = "https://api.github.com",
    ) -> None:
        self._auth = auth
        self._base_url = base_url

    async def _entetes(self, installation_id: int) -> dict[str, str]:
        token = await self._auth.obtenir_token_installation(installation_id)
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": _API_VERSION,
        }

    async def _requete(
        self,
        methode: str,
        chemin: str,
        installation_id: int,
        *,
        json: Optional[dict[str, Any]] = None,
    ) -> httpx.Response:
        entetes = await self._entetes(installation_id)
        async with httpx.AsyncClient(base_url=self._base_url, follow_redirects=True) as client:
            reponse = await client.request(methode, chemin, headers=entetes, json=json)
        reponse.raise_for_status()
        return reponse

    # --- Workflows -------------------------------------------------------

    async def relancer_workflow(self, depot: str, run_id: int, installation_id: int) -> None:
        await self._requete(
            "POST", f"/repos/{depot}/actions/runs/{run_id}/rerun", installation_id
        )
        log.info("github.workflow_relance", depot=depot, run_id=run_id)

    async def telecharger_logs(self, depot: str, run_id: int, installation_id: int) -> bytes:
        """Télécharge l'archive ZIP des logs d'un run (suit la redirection GitHub)."""
        reponse = await self._requete(
            "GET", f"/repos/{depot}/actions/runs/{run_id}/logs", installation_id
        )
        return reponse.content

    # --- Issues (validation humaine) ------------------------------------

    async def ouvrir_issue(
        self,
        depot: str,
        installation_id: int,
        *,
        titre: str,
        corps: str,
        labels: list[str],
    ) -> dict[str, Any]:
        reponse = await self._requete(
            "POST", f"/repos/{depot}/issues", installation_id,
            json={"title": titre, "body": corps, "labels": labels},
        )
        donnees = reponse.json()
        log.info("github.issue_ouverte", depot=depot, numero=donnees.get("number"))
        return donnees

    async def commenter_issue(
        self, depot: str, installation_id: int, numero: int, corps: str
    ) -> None:
        await self._requete(
            "POST", f"/repos/{depot}/issues/{numero}/comments", installation_id,
            json={"body": corps},
        )

    async def fermer_issue(
        self, depot: str, installation_id: int, numero: int, *, label: Optional[str] = None
    ) -> None:
        if label is not None:
            await self._requete(
                "POST", f"/repos/{depot}/issues/{numero}/labels", installation_id,
                json={"labels": [label]},
            )
        await self._requete(
            "PATCH", f"/repos/{depot}/issues/{numero}", installation_id,
            json={"state": "closed"},
        )
        log.info("github.issue_fermee", depot=depot, numero=numero, label=label)

    # --- Pull requests (correctifs) -------------------------------------

    async def creer_pull_request(
        self,
        depot: str,
        installation_id: int,
        *,
        titre: str,
        corps: str,
        branche_source: str,
        branche_cible: str,
    ) -> dict[str, Any]:
        reponse = await self._requete(
            "POST", f"/repos/{depot}/pulls", installation_id,
            json={
                "title": titre,
                "body": corps,
                "head": branche_source,
                "base": branche_cible,
            },
        )
        donnees = reponse.json()
        log.info("github.pr_creee", depot=depot, numero=donnees.get("number"))
        return donnees

    async def declencher_workflow(
        self, depot: str, fichier_workflow: str, ref: str,
        inputs: dict[str, str], installation_id: int,
    ) -> None:
        """Déclenche un workflow (workflow_dispatch) avec des entrées.

        Utilisé pour déléguer la matérialisation d'un correctif à un vrai
        environnement de build (le workflow tourne dans le dépôt surveillé).
        """
        await self._requete(
            "POST", f"/repos/{depot}/actions/workflows/{fichier_workflow}/dispatches",
            installation_id, json={"ref": ref, "inputs": inputs},
        )
        log.info("github.workflow_declenche", depot=depot, workflow=fichier_workflow, ref=ref)

    # --- Matérialisation d'un correctif (branche + fichiers) ------------

    async def obtenir_sha_tete(self, depot: str, branche: str, installation_id: int) -> str:
        """SHA du dernier commit d'une branche (pour créer une branche dérivée)."""
        reponse = await self._requete(
            "GET", f"/repos/{depot}/git/ref/heads/{branche}", installation_id
        )
        return reponse.json()["object"]["sha"]

    async def creer_branche(
        self, depot: str, nom_branche: str, sha_base: str, installation_id: int
    ) -> None:
        """Crée une branche à partir d'un SHA de base."""
        await self._requete(
            "POST", f"/repos/{depot}/git/refs", installation_id,
            json={"ref": f"refs/heads/{nom_branche}", "sha": sha_base},
        )
        log.info("github.branche_creee", depot=depot, branche=nom_branche)

    async def televerser_fichier(
        self, depot: str, chemin: str, contenu: str, branche: str,
        message: str, installation_id: int,
    ) -> None:
        """Crée ou met à jour un fichier sur une branche (Contents API)."""
        sha_existant: Optional[str] = None
        try:
            actuel = await self._requete(
                "GET", f"/repos/{depot}/contents/{chemin}?ref={branche}", installation_id
            )
            sha_existant = actuel.json().get("sha")
        except httpx.HTTPStatusError:
            sha_existant = None  # fichier absent → création
        corps: dict[str, Any] = {
            "message": message,
            "content": base64.b64encode(contenu.encode("utf-8")).decode("ascii"),
            "branch": branche,
        }
        if sha_existant is not None:
            corps["sha"] = sha_existant
        await self._requete(
            "PUT", f"/repos/{depot}/contents/{chemin}", installation_id, json=corps
        )
        log.info("github.fichier_ecrit", depot=depot, chemin=chemin, branche=branche)
