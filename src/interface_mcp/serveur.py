"""Serveur MCP de Mirador (stdio, lecture seule).

Point d'entrée `mirador-mcp`. stdout est réservé au protocole JSON-RPC : la
journalisation structlog est redirigée sur stderr avant le démarrage, sans quoi
chaque log (dont `anomalie.detectee` émis par le Détecteur) corromprait le flux.

Aucun outil n'écrit : l'approbation d'une correction reste un geste humain dans
GitHub (`/approuver`, `/rejeter`).
"""
from __future__ import annotations

import argparse
import functools
import logging
import sys
import time
from pathlib import Path
from typing import Annotated, Any, Callable, Optional
from uuid import uuid4

import structlog
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from src.gestionnaires.entree import ConfigDepots, charger_depots
from src.infrastructure.stockage.bucket import ClientBucket
from src.interface_mcp.modeles import (
    HistoriqueAnomalie,
    ListeAnomalies,
    ListeDepots,
    Niveau,
    RapportIntegrite,
    ResultatClassification,
    Statut,
)
from src.interface_mcp.outils import LIMITE_DEFAUT, LIMITE_MAX, OutilsMirador

log = structlog.get_logger(__name__)

_INSTRUCTIONS = (
    "Mirador surveille les workflows GitHub Actions de quelques dépôts, classe chaque "
    "échec par niveau de risque et consigne tout dans un journal d'audit append-only. "
    "Ces outils sont en lecture seule : ils consultent le journal et appliquent les "
    "règles de classification, mais ne peuvent ni approuver, ni rejeter, ni relancer. "
    "Pour agir, le mainteneur commente `/approuver` ou `/rejeter` sur l'Issue GitHub."
)

_LECTURE_SEULE = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)

_TAILLE_MAX_PARAMETRE_JOURNALISE = 200


def _assainir(parametres: dict[str, Any]) -> dict[str, Any]:
    """Réduit les longues chaînes (extraits de log) à leur longueur."""
    return {
        cle: f"<{len(valeur)} caractères>"
        if isinstance(valeur, str) and len(valeur) > _TAILLE_MAX_PARAMETRE_JOURNALISE
        else valeur
        for cle, valeur in parametres.items()
    }


def _journalise(fonction: Callable[..., Any]) -> Callable[..., Any]:
    """Journalise chaque appel d'outil : corrélation, durée, issue (Principe II)."""

    @functools.wraps(fonction)
    def enveloppe(**parametres: Any) -> Any:
        journal = log.bind(outil=fonction.__name__, correlation_id=str(uuid4()))
        debut = time.perf_counter()
        journal.info("mcp.outil.appel", parametres=_assainir(parametres))
        try:
            resultat = fonction(**parametres)
        except Exception as err:
            journal.warning("mcp.outil.erreur", erreur=type(err).__name__, message=str(err),
                            duree_ms=round((time.perf_counter() - debut) * 1000))
            raise
        journal.info("mcp.outil.succes", duree_ms=round((time.perf_counter() - debut) * 1000))
        return resultat

    return enveloppe


def creer_serveur(config: ConfigDepots, bucket: ClientBucket, cle_bd: str = "mirador.db") -> MCPServer:
    outils = OutilsMirador(config, bucket, cle_bd=cle_bd)
    serveur = MCPServer("mirador", instructions=_INSTRUCTIONS)
    outil = functools.partial(serveur.tool, annotations=_LECTURE_SEULE)

    @outil()
    @_journalise
    def lister_depots() -> ListeDepots:
        """Liste les dépôts GitHub surveillés par Mirador, avec leur état actif et leur
        seuil de timeout. À appeler d'abord si l'utilisateur ne précise pas le dépôt."""
        return outils.lister_depots()

    @outil()
    @_journalise
    def lister_anomalies(
        depot: Annotated[str, Field(description="Dépôt surveillé, au format proprietaire/depot")],
        statut: Annotated[Optional[Statut], Field(description="Filtre par statut")] = None,
        niveau_risque: Annotated[Optional[Niveau], Field(description="Filtre par niveau de risque")] = None,
        limite: Annotated[int, Field(ge=1, le=LIMITE_MAX, description="Nombre maximal d'anomalies")] = LIMITE_DEFAUT,
    ) -> ListeAnomalies:
        """Liste les anomalies CI/CD (échecs, timeouts) détectées par Mirador sur un dépôt,
        reconstituées depuis le journal d'audit. Statuts : OUVERTE, EN_COURS (validée par
        un humain), ESCALADÉE (en attente de décision humaine), RÉSOLUE, REJETÉE.
        Chaque anomalie porte un correlation_id à passer à historique_anomalie."""
        return outils.lister_anomalies(depot, statut=statut, niveau_risque=niveau_risque, limite=limite)

    @outil()
    @_journalise
    def historique_anomalie(
        correlation_id: Annotated[str, Field(description="UUID renvoyé par lister_anomalies")],
    ) -> HistoriqueAnomalie:
        """Donne la chronologie complète d'une anomalie : détection, analyse, escalade,
        validation ou rejet par un humain, intervention. Chaque événement indique son
        acteur (l'agent Mirador ou le mainteneur)."""
        return outils.historique_anomalie(correlation_id)

    @outil()
    @_journalise
    def verifier_integrite_journal() -> RapportIntegrite:
        """Vérifie que le journal d'audit n'a pas été modifié depuis sa dernière écriture
        par Mirador (empreinte SHA-256 recalculée comparée à l'empreinte enregistrée).
        Issues : INTÈGRE, ALTÉRÉ, NON_VÉRIFIABLE."""
        return outils.verifier_integrite_journal()

    @outil()
    @_journalise
    def classer_echec(
        depot: Annotated[str, Field(description="Dépôt surveillé dont on applique les règles")],
        conclusion: Annotated[str, Field(description="Conclusion GitHub du run : failure, timed_out, cancelled, success...")],
        branche: Annotated[str, Field(description="Branche du run, par exemple main")],
        duree_secondes: Annotated[Optional[int], Field(ge=0, description="Durée du run, si connue")] = None,
        extrait_log: Annotated[Optional[str], Field(description="Extrait du log d'échec (la fin est conservée)")] = None,
    ) -> ResultatClassification:
        """Classe un échec de workflow GitHub Actions fourni par l'utilisateur avec les
        règles déterministes de Mirador : type, niveau de risque, règle de diagnostic
        reconnue, élévation sur branche protégée. N'appelle aucun modèle de langage et
        n'enregistre rien dans le journal."""
        return outils.classer_echec(depot, conclusion=conclusion, branche=branche,
                                    duree_secondes=duree_secondes, extrait_log=extrait_log)

    return serveur


def configurer_journalisation() -> None:
    """JSON sur stderr, horodatage ISO 8601 : stdout appartient au protocole."""
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        logger_factory=structlog.PrintLoggerFactory(sys.stderr),
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    )


def main(arguments: Optional[list[str]] = None) -> None:
    configurer_journalisation()
    analyseur = argparse.ArgumentParser(prog="mirador-mcp", description=__doc__.splitlines()[0])
    analyseur.add_argument("--config", type=Path,
                           help="Fichier JSON des dépôts (format MIRADOR_DEPOTS) ; défaut : variable d'environnement")
    analyseur.add_argument("--journal-local", type=Path,
                           help="Répertoire contenant mirador.db et mirador.db.sha256 ; défaut : bucket Scaleway")
    analyseur.add_argument("--cle-bd", default="mirador.db", help="Nom du fichier du journal")
    options = analyseur.parse_args(arguments)

    if options.config:
        config = ConfigDepots(charger_depots(options.config.read_text(encoding="utf-8")))
    else:
        config = ConfigDepots.depuis_env()

    if options.journal_local:
        from src.infrastructure.stockage.lecture_fichier import ClientBucketLectureFichier
        bucket: ClientBucket = ClientBucketLectureFichier(options.journal_local)
    else:
        from src.gestionnaires.entree import _client_bucket
        bucket = _client_bucket()

    log.info("mcp.serveur.demarrage", depots=len(config.depots()),
             source="locale" if options.journal_local else "bucket")
    creer_serveur(config, bucket, cle_bd=options.cle_bd).run()


if __name__ == "__main__":
    main()
