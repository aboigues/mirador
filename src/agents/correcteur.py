"""Agent Correcteur : analyse la cause d'une anomalie et propose un correctif.

Deux chemins :
- Cause connue avec action RELANCE → proposition déterministe, sans appel LLM
  (économie de coût et de latence pour les incidents transitoires identifiés).
- Cause inconnue ou correctif de code → analyse du log par Claude, qui propose
  une RELANCE ou une PULL_REQUEST (jamais de commit direct — Principe III).

Le modèle par défaut est Claude Opus 4.8 (claude-opus-4-8), avec thinking adaptatif
et sortie structurée (json_schema) pour une proposition exploitable.
"""
from __future__ import annotations

import json
from typing import Any, Optional

import structlog
from pydantic import BaseModel

from src.domaine.anomalie import Anomalie
from src.domaine.regle import ActionRecommandee, RegleDiagnostic

log = structlog.get_logger(__name__)

_MODELE_DEFAUT = "claude-opus-4-8"
_MAX_TOKENS = 16000


class TypeCorrection:
    RELANCE = "RELANCE"
    PULL_REQUEST = "PULL_REQUEST"


class RefusModele(Exception):
    """Levée quand le modèle refuse la requête (stop_reason == refusal)."""


class FichierCorrectif(BaseModel):
    chemin: str
    contenu: str  # contenu COMPLET du fichier après correction


class PropositionCorrection(BaseModel):
    type: str
    justification: str
    titre_pr: Optional[str] = None
    corps_pr: Optional[str] = None
    correctif: Optional[str] = None
    # Fichiers à écrire sur la branche de correction (contenu complet). Optionnel :
    # si vide, Mirador ouvre quand même une PR documentant le correctif à appliquer.
    fichiers: Optional[list[FichierCorrectif]] = None


# Schéma de sortie structurée imposé au modèle
_SCHEMA_PROPOSITION: dict[str, Any] = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": [TypeCorrection.RELANCE, TypeCorrection.PULL_REQUEST]},
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
    },
    "required": ["type", "justification"],
    "additionalProperties": False,
}

_SYSTEME = (
    "Tu es Mirador, un agent de surveillance CI/CD. À partir de l'extrait de log "
    "d'un workflow GitHub Actions en échec, tu identifies la cause et proposes UNE "
    "intervention : RELANCE (si l'échec est transitoire — flaky test, timeout réseau) "
    "ou PULL_REQUEST (si un correctif de code est nécessaire). Tu ne proposes jamais "
    "de commit direct. Pour une PULL_REQUEST, fournis un titre, un corps, et — quand "
    "c'est possible et sûr — la liste `fichiers` des fichiers modifiés avec leur "
    "contenu COMPLET après correction (chemin + contenu), pour que la PR soit ouverte "
    "automatiquement. N'inclus dans `fichiers` que des fichiers que tu peux produire "
    "intégralement et correctement ; sinon laisse `fichiers` vide et décris le "
    "correctif dans le corps. Réponds en français."
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
    ) -> PropositionCorrection:
        """Retourne une proposition de correction pour l'anomalie."""
        if regle is not None and regle.action_recommandee == ActionRecommandee.RELANCE:
            log.info("correcteur.relance_deterministe",
                     anomalie_id=str(anomalie.id), regle=regle.nom)
            return PropositionCorrection(
                type=TypeCorrection.RELANCE,
                justification=f"Cause connue (règle '{regle.nom}') : relance recommandée.",
            )

        return await self._analyser_via_claude(anomalie, extrait_log)

    async def _analyser_via_claude(
        self, anomalie: Anomalie, extrait_log: str
    ) -> PropositionCorrection:
        invite = (
            f"Workflow « {anomalie.workflow_nom} » (run {anomalie.workflow_run_id}) "
            f"de type {anomalie.type}.\n\nExtrait de log :\n{extrait_log}"
        )
        reponse = await self._client.messages.create(
            model=self._modele,
            max_tokens=_MAX_TOKENS,
            thinking={"type": "adaptive"},
            output_config={
                "effort": "high",
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
