"""Client GitHub Actions : relance de workflow, logs, pull requests, issues.

S'authentifie via un fournisseur de token d'installation (`GitHubAppAuth`). Les
interventions restent volontairement limitées à RELANCE et PULL_REQUEST (jamais
de commit direct) ; les Issues portent le flux de validation humaine.
"""
from __future__ import annotations

from typing import Any, Optional, Protocol

import httpx
import structlog

log = structlog.get_logger(__name__)

_API_VERSION = "2022-11-28"


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
