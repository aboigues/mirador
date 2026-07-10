"""Composition root : lit la config, instancie les vrais clients, câble le pipeline.

C'est le chaînon entre les briques testées et un déploiement réel. La config des
dépôts surveillés est fournie via la variable d'environnement MIRADOR_DEPOTS
(JSON) — voir le format dans docs/quickstart. Les secrets (GitHub App, Anthropic,
Scaleway) sont lus depuis l'environnement chiffré Scaleway Serverless.

Trois surfaces exposées :
- creer_app_webhook() : récepteur FastAPI des webhooks GitHub (→ file).
- construire_traitement() : orchestrateur consommant la file.
- creer_app_api() : API de consultation lecture seule (EF-010).
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable, Optional

import structlog
from fastapi import FastAPI

from src.agents.detecteur import Detecteur
from src.agents.superviseur import Superviseur
from src.domaine.depot import DepotSurveille
from src.domaine.regle import RegleDiagnostic
from src.gestionnaires.traitement import Traitement

log = structlog.get_logger(__name__)

Paire = tuple[DepotSurveille, list[RegleDiagnostic]]


def charger_depots(valeur: Optional[str]) -> list[Paire]:
    """Parse la config MIRADOR_DEPOTS (JSON) en paires (dépôt, règles)."""
    if not valeur:
        return []
    try:
        brut = json.loads(valeur)
    except json.JSONDecodeError as err:
        raise ValueError(f"MIRADOR_DEPOTS : JSON invalide ({err})") from err

    paires: list[Paire] = []
    try:
        for entree in brut:
            regles = [RegleDiagnostic(**r) for r in entree.pop("regles", [])]
            depot = DepotSurveille(**entree)
            paires.append((depot, regles))
    except (TypeError, ValueError) as err:
        raise ValueError(f"MIRADOR_DEPOTS : entrée invalide ({err})") from err
    return paires


class ConfigDepots:
    """Résout les dépôts surveillés et leurs responsables depuis la config."""

    def __init__(self, paires: list[Paire]) -> None:
        self._par_nom: dict[str, Paire] = {
            depot.identifiant_github: (depot, regles) for depot, regles in paires
        }

    @classmethod
    def depuis_env(cls, env: Optional[dict[str, str]] = None) -> "ConfigDepots":
        env = env if env is not None else os.environ
        return cls(charger_depots(env.get("MIRADOR_DEPOTS")))

    def depots(self) -> list[DepotSurveille]:
        return [depot for depot, _ in self._par_nom.values()]

    def resoudre(self, nom: str) -> Optional[Paire]:
        return self._par_nom.get(nom)

    def responsables(self, nom: str) -> list[str]:
        paire = self._par_nom.get(nom)
        return list(paire[0].responsables) if paire else []


def construire_traitement(
    config: ConfigDepots,
    *,
    actions: Any,
    writer: Any,
    correcteur: Any,
    detecteur: Optional[Detecteur] = None,
    superviseur: Optional[Superviseur] = None,
) -> Traitement:
    """Assemble l'orchestrateur avec les I/O fournis et la config des dépôts."""
    return Traitement(
        detecteur=detecteur or Detecteur(),
        superviseur=superviseur or Superviseur(),
        correcteur=correcteur,
        actions=actions,
        writer=writer,
        resoudre_depot=config.resoudre,
    )


# --- Fabriques des clients réels (isolées pour rester testables) ----------

def _client_bucket() -> Any:
    from src.infrastructure.stockage.bucket import ClientBucketS3
    return ClientBucketS3()


def _client_anthropic() -> Any:
    from anthropic import AsyncAnthropic
    return AsyncAnthropic()  # ANTHROPIC_API_KEY depuis l'environnement


def _client_actions() -> Any:
    from src.infrastructure.github.actions_client import GitHubActionsClient
    from src.infrastructure.github.app_auth import GitHubAppAuth
    return GitHubActionsClient(GitHubAppAuth.depuis_env())


# --- Surfaces de déploiement ----------------------------------------------

def creer_app_webhook(config: Optional[ConfigDepots] = None) -> FastAPI:
    """Récepteur de webhooks GitHub, avec responsables résolus depuis la config."""
    from src.gestionnaires.webhook import creer_application
    config = config or ConfigDepots.depuis_env()
    return creer_application(resoudre_responsables=config.responsables)


def construire_traitement_reel(config: Optional[ConfigDepots] = None) -> Traitement:
    """Câble l'orchestrateur avec les vrais clients (échoue vite si un secret manque)."""
    from src.agents.correcteur import Correcteur
    from src.infrastructure.stockage.writer import WriterAudit

    config = config or ConfigDepots.depuis_env()
    bucket = _client_bucket()
    return construire_traitement(
        config,
        actions=_client_actions(),
        writer=WriterAudit(bucket),
        correcteur=Correcteur(_client_anthropic()),
    )


def creer_app_api(config: Optional[ConfigDepots] = None) -> FastAPI:
    """API de consultation lecture seule (EF-010) sur le journal du bucket."""
    from src.api.routes import creer_api
    from src.infrastructure.stockage.depots import DepotJournal

    config = config or ConfigDepots.depuis_env()
    journal = DepotJournal(_client_bucket())
    return creer_api(depots_provider=config.depots, journal=journal)
