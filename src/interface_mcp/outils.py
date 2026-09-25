"""Logique des cinq outils MCP, indépendante du protocole.

Lecture seule : aucune méthode n'écrit dans le journal ni n'agit sur GitHub.
Les erreurs attendues sont des `ToolError` au message explicite, transmis tel
quel à l'assistant ; toute autre exception est masquée par le SDK. Les erreurs
de stockage sont converties en message neutre pour ne jamais exposer de nom de
bucket ni d'identifiant (EF-009).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

import structlog
from mcp.server.mcpserver.exceptions import ToolError

from src.agents.detecteur import _BRANCHES_PROTEGEES, Detecteur
from src.domaine.vue_anomalies import grouper_anomalies
from src.gestionnaires.entree import ConfigDepots, Paire
from src.infrastructure.stockage.bucket import ClientBucket
from src.infrastructure.stockage.depots import DepotJournal
from src.infrastructure.stockage.integrite import RapportIntegrite, verifier_integrite
from src.interface_mcp.modeles import (
    AnomalieVue,
    DepotResume,
    EvenementVue,
    HistoriqueAnomalie,
    ListeAnomalies,
    ListeDepots,
    ResultatClassification,
)

log = structlog.get_logger(__name__)

LIMITE_DEFAUT = 50
LIMITE_MAX = 200
# Les causes d'échec sont en fin de log : on garde la fin.
TAILLE_MAX_EXTRAIT = 20_000

_JOURNAL_INACCESSIBLE = "Journal d'audit inaccessible (voir les logs du serveur)"


class OutilsMirador:
    def __init__(self, config: ConfigDepots, bucket: ClientBucket, cle_bd: str = "mirador.db") -> None:
        self._config = config
        self._bucket = bucket
        self._cle_bd = cle_bd
        self._journal = DepotJournal(bucket, cle_bd=cle_bd)

    def _resoudre(self, depot: str) -> Paire:
        paire = self._config.resoudre(depot)
        if paire is None:
            surveilles = ", ".join(d.identifiant_github for d in self._config.depots()) or "aucun"
            raise ToolError(f"Dépôt non surveillé par Mirador : {depot} (surveillés : {surveilles})")
        return paire

    def _lire(self, correlation_id: Optional[UUID] = None) -> list[dict[str, Any]]:
        try:
            return self._journal.lister(correlation_id=correlation_id)
        except Exception as err:
            log.error("mcp.journal.inaccessible", erreur=type(err).__name__)
            raise ToolError(_JOURNAL_INACCESSIBLE) from err

    def lister_depots(self) -> ListeDepots:
        depots = []
        for depot in self._config.depots():
            _, regles = self._resoudre(depot.identifiant_github)
            depots.append(DepotResume(
                identifiant_github=depot.identifiant_github,
                actif=depot.actif,
                seuil_timeout_secondes=depot.seuil_timeout_secondes,
                nombre_regles=len(regles),
            ))
        return ListeDepots(depots=depots)

    def lister_anomalies(
        self,
        depot: str,
        statut: Optional[str] = None,
        niveau_risque: Optional[str] = None,
        limite: int = LIMITE_DEFAUT,
    ) -> ListeAnomalies:
        if not 1 <= limite <= LIMITE_MAX:
            raise ToolError(f"limite hors bornes : {limite} (attendu entre 1 et {LIMITE_MAX})")
        self._resoudre(depot)

        evenements = [e for e in self._lire() if e.get("depot") == depot]
        anomalies = grouper_anomalies(evenements)
        if statut is not None:
            anomalies = [a for a in anomalies if a["statut"] == statut]
        if niveau_risque is not None:
            anomalies = [a for a in anomalies if a["niveau_risque"] == niveau_risque]
        anomalies = anomalies[:limite]

        return ListeAnomalies(
            depot=depot,
            anomalies=[AnomalieVue(**a) for a in anomalies],
            total=len(anomalies),
        )

    def historique_anomalie(self, correlation_id: str) -> HistoriqueAnomalie:
        try:
            cid = UUID(correlation_id)
        except ValueError:
            raise ToolError(f"Identifiant de corrélation invalide : {correlation_id!r} (UUID attendu)")

        evenements = self._lire(correlation_id=cid)
        if not evenements:
            raise ToolError(f"Aucun événement pour l'anomalie {correlation_id}")

        return HistoriqueAnomalie(
            correlation_id=str(cid),
            depot=evenements[0]["depot"],
            evenements=[
                EvenementVue(**{champ: e.get(champ) for champ in EvenementVue.model_fields})
                for e in evenements
            ],
        )

    def verifier_integrite_journal(self) -> RapportIntegrite:
        try:
            return verifier_integrite(self._bucket, self._cle_bd)
        except Exception as err:
            log.error("mcp.journal.inaccessible", erreur=type(err).__name__)
            raise ToolError(_JOURNAL_INACCESSIBLE) from err

    def classer_echec(
        self,
        depot: str,
        conclusion: str,
        branche: str,
        duree_secondes: Optional[int] = None,
        extrait_log: Optional[str] = None,
    ) -> ResultatClassification:
        if not branche.strip():
            raise ToolError("La branche ne peut pas être vide")
        if duree_secondes is not None and duree_secondes < 0:
            raise ToolError(f"La durée ne peut pas être négative : {duree_secondes}")
        depot_surveille, regles = self._resoudre(depot)

        tronque = extrait_log is not None and len(extrait_log) > TAILLE_MAX_EXTRAIT
        if tronque:
            extrait_log = extrait_log[-TAILLE_MAX_EXTRAIT:]

        # Run fictif (identifiant 0) : l'échec est fourni par l'utilisateur, pas par GitHub.
        message: dict[str, Any] = {
            "conclusion": conclusion,
            "head_branch": branche,
            "depot": depot,
            "workflow_run_id": 0,
            "workflow_nom": "classification-manuelle",
        }
        if duree_secondes is not None:
            debut = datetime(2000, 1, 1, tzinfo=timezone.utc)
            message["run_started_at"] = debut.isoformat()
            message["run_completed_at"] = (debut + timedelta(seconds=duree_secondes)).isoformat()

        anomalie = Detecteur().detecter(
            message,
            depot_surveille.id,
            regles,
            seuil_timeout_secondes=depot_surveille.seuil_timeout_secondes,
            extrait_log=extrait_log,
        )
        if anomalie is None:
            return ResultatClassification(anomalie=False, extrait_tronque=tronque)

        regle = next((r for r in regles if r.id == anomalie.regle_declenchee_id), None)
        return ResultatClassification(
            anomalie=True,
            type=str(anomalie.type),
            niveau_risque=anomalie.niveau_risque.name,
            regle_declenchee=regle.nom if regle else None,
            cause_identifiee=anomalie.cause_identifiee,
            eleve_branche_protegee=regle is None and branche in _BRANCHES_PROTEGEES,
            extrait_tronque=tronque,
        )
