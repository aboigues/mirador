"""Client GitHub Actions : relance de workflow, logs, pull requests, issues.

S'authentifie via un fournisseur de token d'installation (`GitHubAppAuth`). Les
interventions restent volontairement limitées à RELANCE et PULL_REQUEST (jamais
de commit direct) ; les Issues portent le flux de validation humaine.
"""
from __future__ import annotations

import base64
import io
import re
import zipfile
from typing import Any, Optional, Protocol

import httpx
import structlog

log = structlog.get_logger(__name__)

_API_VERSION = "2022-11-28"

# Conclusions d'un job qui valent la peine d'être analysées. `cancelled` et
# `skipped` en sont exclus : leur log ne contient pas de cause.
_CONCLUSIONS_ECHEC = frozenset({"failure", "timed_out"})

# Au-delà, les parts de budget deviennent trop courtes pour porter une erreur.
# Les jobs surnuméraires sont nommés en tête d'extrait, sans leur log.
_MAX_JOBS_ANALYSES = 5

_MARQUEUR_TRONCATURE = "[…début du log tronqué…]\n"

# GitHub annote l'étape fautive par `##[error]…`. C'est le seul repère fiable
# de l'endroit où regarder : la fin brute du log d'un job en échec, elle, ne
# contient que le « Post job cleanup » exécuté APRÈS l'erreur.
_MARQUEUR_ERREUR = "##[error]"

# Chaque ligne de log GitHub est préfixée d'un horodatage ISO (~29 caractères)
# sans valeur pour le diagnostic — le retirer rend ~25 % du budget au contexte.
_HORODATAGE = re.compile(r"^\d{4}-\d{2}-\d{2}T[\d:.]+Z ", re.MULTILINE)


def _fenetre_utile(texte: str, taille: int) -> str:
    """Extrait `taille` caractères de `texte`, centrés sur ce qui a échoué.

    On se cale sur la DERNIÈRE annotation `##[error]` et on remonte : le
    contexte qui la précède (sortie de l'outil, trace) porte la cause. À défaut
    d'annotation, on retombe sur la fin du log.
    """
    coupe = texte.rfind(_MARQUEUR_ERREUR)
    if coupe == -1:
        return texte[-taille:]
    fin = texte.find("\n", coupe)
    fin = len(texte) if fin == -1 else fin + 1
    return texte[max(0, fin - taille):fin]


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


def assembler_extrait_jobs(jobs: list[tuple[str, str]], budget: int) -> str:
    """Assemble les logs de plusieurs jobs en échec dans un budget de caractères.

    Chaque job retenu reçoit une part égale du budget, dont on garde la FIN
    (l'erreur y est). Le partage est le point clé : concaténer les jobs puis
    tronquer globalement laisserait un seul job verbeux évincer tous les autres.
    Le total retourné ne dépasse pas `budget`.
    """
    if not jobs:
        return ""

    entete_globale = ""
    retenus = jobs
    if len(jobs) > _MAX_JOBS_ANALYSES:
        retenus = jobs[:_MAX_JOBS_ANALYSES]
        noms = ", ".join(nom for nom, _ in jobs)
        entete_globale = (
            f"[{len(jobs)} jobs en échec : {noms}. "
            f"Log des {_MAX_JOBS_ANALYSES} premiers seulement.]\n"
        )

    part = max((budget - len(entete_globale)) // len(retenus), 0)
    morceaux = []
    for nom, texte in retenus:
        texte = _HORODATAGE.sub("", texte)
        entete = f"===== Job en échec : {nom} =====\n"
        disponible = part - len(entete) - len(_MARQUEUR_TRONCATURE) - 1
        if disponible <= 0:
            morceaux.append(entete.rstrip("\n")[:part])
            continue
        if len(texte) > disponible:
            corps = _MARQUEUR_TRONCATURE + _fenetre_utile(texte, disponible)
        else:
            corps = texte
        morceaux.append(entete + corps)
    return entete_globale + "\n".join(morceaux)


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

    async def lister_jobs_en_echec(
        self, depot: str, run_id: int, installation_id: int
    ) -> list[dict[str, Any]]:
        """Jobs du run dont la conclusion est un échec : [{"id", "nom"}, …].

        Pagine jusqu'au bout : un run de matrice dépasse couramment les 30 jobs
        de la page par défaut, et le job fautif peut être sur n'importe laquelle.
        """
        jobs: list[dict[str, Any]] = []
        page = 1
        while True:
            reponse = await self._requete(
                "GET",
                f"/repos/{depot}/actions/runs/{run_id}/jobs"
                f"?filter=latest&per_page=100&page={page}",
                installation_id,
            )
            donnees = reponse.json()
            lot = donnees.get("jobs", [])
            jobs.extend(lot)
            if not lot or len(jobs) >= donnees.get("total_count", 0):
                break
            page += 1

        en_echec = [
            {"id": job["id"], "nom": job.get("name", "")}
            for job in jobs
            if job.get("conclusion") in _CONCLUSIONS_ECHEC
        ]
        log.info("github.jobs_en_echec", depot=depot, run_id=run_id,
                 total=len(jobs), en_echec=len(en_echec))
        return en_echec

    async def telecharger_logs_job(
        self, depot: str, job_id: int, installation_id: int
    ) -> bytes:
        """Log d'un job précis (texte brut, pas une archive)."""
        reponse = await self._requete(
            "GET", f"/repos/{depot}/actions/jobs/{job_id}/logs", installation_id
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
