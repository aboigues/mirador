"""Agent Correcteur : analyse la cause d'une anomalie et propose un correctif.

Deux chemins :
- Cause connue avec action RELANCE → proposition déterministe, sans appel LLM
  (économie de coût et de latence pour les incidents transitoires identifiés).
- Cause inconnue ou correctif de code → analyse du log par Claude, qui propose
  une RELANCE ou une PULL_REQUEST (jamais de commit direct — Principe III), ou
  s'abstient (ABSTENTION) quand le log ne porte aucune erreur identifiable.

Le modèle par défaut est Claude Haiku 4.5 : la tâche est une classification
contrainte par une sortie structurée (json_schema), pour laquelle Haiku suffit à
un cinquième du coût d'Opus. Remonter à claude-sonnet-5 si la qualité des
correctifs déçoit à l'usage.
"""
from __future__ import annotations

import json
from typing import Any, Optional

import structlog
from pydantic import BaseModel

from src.domaine.anomalie import Anomalie
from src.domaine.regle import ActionRecommandee, RegleDiagnostic

log = structlog.get_logger(__name__)

_MODELE_DEFAUT = "claude-haiku-4-5"
_MAX_TOKENS = 4000
# Les logs GitHub dézippés font des centaines de Ko ; envoyer le tout à chaque
# appel Claude coûte très cher en tokens d'entrée. L'appelant est chargé de tenir
# ce budget en ciblant les jobs en échec (cf. `assembler_extrait_jobs`) ; la
# troncature ci-dessous n'est qu'un dernier filet, aveugle au découpage en jobs.
MAX_LOG_CHARS = 8000


class TypeCorrection:
    RELANCE = "RELANCE"
    PULL_REQUEST = "PULL_REQUEST"
    ABSTENTION = "ABSTENTION"
    # Reconstruction d'une image durcie du dépôt (docker/hardened/) : jamais
    # proposé par Claude (absent de `_SCHEMA_PROPOSITION`) — décision
    # déterministe prise par `Traitement._proposer`, qui seul a accès à la
    # liste des dossiers docker/hardened/ du dépôt surveillé.
    REBUILD_IMAGES = "REBUILD_IMAGES"


class RefusModele(Exception):
    """Levée quand le modèle refuse la requête (stop_reason == refusal)."""


class FichierCorrectif(BaseModel):
    chemin: str
    contenu: str  # contenu COMPLET du fichier après correction


class MiseAJourDeps(BaseModel):
    """Correctif de dépendances Go, matérialisé par un vrai build (go mod tidy)."""
    go_version: Optional[str] = None  # ex. "1.25.12" ; None si inchangée
    modules: list[str] = []  # ex. ["golang.org/x/net@v0.55.0"]


class PropositionCorrection(BaseModel):
    type: str
    justification: str
    titre_pr: Optional[str] = None
    corps_pr: Optional[str] = None
    correctif: Optional[str] = None
    # Fichiers à écrire sur la branche de correction (contenu complet). Optionnel :
    # si vide, Mirador ouvre quand même une PR documentant le correctif à appliquer.
    fichiers: Optional[list[FichierCorrectif]] = None
    # Correctif de dépendances Go → délégué à un workflow de build réel (go mod tidy)
    # qui régénère go.mod/go.sum correctement. Préféré aux `fichiers` pour les deps.
    mise_a_jour: Optional[MiseAJourDeps] = None
    # Images durcies (docker/hardened/<nom>) à reconstruire — REBUILD_IMAGES
    # uniquement (jamais rempli par Claude, cf. TypeCorrection.REBUILD_IMAGES).
    images: Optional[list[str]] = None


# Schéma de sortie structurée imposé au modèle
_SCHEMA_PROPOSITION: dict[str, Any] = {
    "type": "object",
    "properties": {
        "type": {
            "type": "string",
            "enum": [
                TypeCorrection.RELANCE,
                TypeCorrection.PULL_REQUEST,
                TypeCorrection.ABSTENTION,
            ],
        },
        "justification": {"type": "string"},
        "titre_pr": {"type": "string"},
        "corps_pr": {"type": "string"},
        "correctif": {"type": "string"},
        "fichiers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "chemin": {"type": "string"},
                    "contenu": {"type": "string"},
                },
                "required": ["chemin", "contenu"],
                "additionalProperties": False,
            },
        },
        "mise_a_jour": {
            "type": "object",
            "properties": {
                "go_version": {"type": "string"},
                "modules": {"type": "array", "items": {"type": "string"}},
            },
            "additionalProperties": False,
        },
    },
    "required": ["type", "justification"],
    "additionalProperties": False,
}

_SYSTEME = (
    "Tu es Mirador, un agent de surveillance CI/CD. À partir de l'extrait de log "
    "d'un workflow GitHub Actions en échec, tu identifies la cause et proposes UNE "
    "intervention : RELANCE, PULL_REQUEST ou ABSTENTION.\n\n"
    "Règle absolue : ta proposition doit s'appuyer sur un message d'erreur que tu "
    "LIS dans l'extrait. Tu n'inventes jamais une cause plausible. Si l'extrait ne "
    "montre aucune erreur — parce qu'il est tronqué, incomplet, ou ne contient que "
    "des étapes réussies —, réponds ABSTENTION et dis dans `justification` ce que tu "
    "as vu et ce qui te manque. Une hypothèse présentée comme une cause fait perdre "
    "plus de temps qu'un « je ne sais pas » : un humain prendra le relais.\n\n"
    "RELANCE seulement si le log montre explicitement un échec transitoire (timeout "
    "réseau, flaky test, erreur 5xx d'un registre) ; cite le message dans la "
    "justification. L'absence d'erreur visible n'est PAS une preuve que l'échec est "
    "transitoire : c'est une ABSTENTION. PULL_REQUEST si un correctif de code est "
    "nécessaire. Tu ne proposes jamais de commit direct.\n\n"
    "Pour une PULL_REQUEST, fournis un titre, un corps, et — quand "
    "c'est possible et sûr — la liste `fichiers` des fichiers modifiés avec leur "
    "contenu COMPLET après correction (chemin + contenu), pour que la PR soit ouverte "
    "automatiquement. N'inclus dans `fichiers` que des fichiers que tu peux produire "
    "intégralement et correctement ; sinon laisse `fichiers` vide et décris le "
    "correctif dans le corps.\n\n"
    "Si un extrait du dépôt (fichiers de manifeste/configuration) est fourni "
    "après le log, cherches-y la référence exacte à l'image ou la dépendance en "
    "cause. Si tu la trouves dans un fichier fourni, mets ce fichier dans "
    "`fichiers` avec son contenu ENTIER, à l'identique, en ne changeant que la "
    "référence fautive — jamais un extrait partiel : ce contenu écrase le fichier "
    "réel tel quel. Si l'image/dépendance n'apparaît dans AUCUN fichier fourni, ne "
    "conclus PAS qu'elle est absente du dépôt : l'extrait est volontairement "
    "partiel (budget limité), pas le dépôt entier. Dis-le explicitement dans "
    "`justification` et pars sur ABSTENTION plutôt que de deviner un chemin de "
    "fichier.\n\n"
    "Pour un correctif de DÉPENDANCES Go (mise à jour du "
    "toolchain et/ou de modules), n'édite PAS go.mod/go.sum toi-même (go.sum n'est "
    "pas calculable sans build) : renseigne plutôt `mise_a_jour` (go_version cible "
    "et/ou modules « chemin@version »), et Mirador régénérera go.mod/go.sum via un "
    "vrai build. Réponds en français."
)


class Correcteur:
    def __init__(self, client: Any, *, modele: str = _MODELE_DEFAUT) -> None:
        self._client = client
        self._modele = modele

    async def proposer(
        self,
        anomalie: Anomalie,
        extrait_log: str,
        regle: Optional[RegleDiagnostic] = None,
        contexte_depot: Optional[str] = None,
    ) -> PropositionCorrection:
        """Retourne une proposition de correction pour l'anomalie.

        `contexte_depot` : extrait optionnel de fichiers du dépôt surveillé
        (manifestes, Dockerfiles...), déjà budgeté par l'appelant — voir
        `assembler_contexte_depot`. Permet au correcteur de localiser une
        référence fautive et de produire un correctif matérialisable au lieu
        de s'abstenir faute d'accès au dépôt.
        """
        if regle is not None and regle.action_recommandee == ActionRecommandee.RELANCE:
            log.info("correcteur.relance_deterministe",
                     anomalie_id=str(anomalie.id), regle=regle.nom)
            return PropositionCorrection(
                type=TypeCorrection.RELANCE,
                justification=f"Cause connue (règle '{regle.nom}') : relance recommandée.",
            )

        return await self._analyser_via_claude(anomalie, extrait_log, contexte_depot)

    async def _analyser_via_claude(
        self, anomalie: Anomalie, extrait_log: str, contexte_depot: Optional[str] = None
    ) -> PropositionCorrection:
        log_tronque = extrait_log[-MAX_LOG_CHARS:] if extrait_log else ""
        if extrait_log and len(extrait_log) > MAX_LOG_CHARS:
            log_tronque = "[…début du log tronqué…]\n" + log_tronque
        invite = (
            f"Workflow « {anomalie.workflow_nom} » (run {anomalie.workflow_run_id}) "
            f"de type {anomalie.type}.\n\nExtrait de log :\n{log_tronque}"
        )
        if contexte_depot:
            # Non retronqué ici : la troncature dangereuse (couper un fichier en
            # cours de route) a déjà été évitée en amont par
            # `assembler_contexte_depot`, qui n'inclut jamais un fichier partiel.
            invite += (
                "\n\nExtrait du dépôt (fichiers de manifeste/configuration "
                "susceptibles de référencer l'image ou la dépendance en cause ; "
                "liste PARTIELLE, pas nécessairement exhaustive) :\n\n"
                + contexte_depot
            )
        # Ni `thinking` ni `output_config.effort` : coûteux en tokens, non nécessaires
        # pour cette classification, et surtout incompatibles avec Haiku 4.5 (erreur).
        # La sortie structurée (json_schema) suffit et marche sur tous les modèles.
        reponse = await self._client.messages.create(
            model=self._modele,
            max_tokens=_MAX_TOKENS,
            output_config={
                "format": {"type": "json_schema", "schema": _SCHEMA_PROPOSITION},
            },
            system=_SYSTEME,
            messages=[{"role": "user", "content": invite}],
        )

        if getattr(reponse, "stop_reason", None) == "refusal":
            raise RefusModele("Le modèle a refusé d'analyser ce log")

        texte = next((b.text for b in reponse.content if b.type == "text"), "")
        proposition = PropositionCorrection(**json.loads(texte))
        log.info("correcteur.proposition",
                 anomalie_id=str(anomalie.id), type=proposition.type)
        return proposition
