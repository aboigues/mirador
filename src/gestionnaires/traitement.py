"""Orchestrateur du pipeline : consomme la file mirador-webhooks et agit.

Deux types de messages :
- workflow_run → détection → décision (superviseur) → intervention auto (RELANCE/PR)
  ou escalade humaine (GitHub Issue). Chaque étape est journalisée (audit immuable).
- validation_humaine → applique la décision du responsable (/approuver, /rejeter) :
  commentaire + fermeture de l'issue, journalisée.

Toutes les dépendances sont injectées pour rester testable et découplé des I/O.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

import structlog

from src.agents.correcteur import TypeCorrection
from src.agents.superviseur import DecisionEscalade
from src.domaine.anomalie import Anomalie
from src.domaine.journal import EvenementJournal, TypeEvenement
from src.domaine.regle import RegleDiagnostic
from src.infrastructure.github.actions_client import extraire_texte_logs

log = structlog.get_logger(__name__)

_ACTEUR_AGENT = "mirador-agent"


class Traitement:
    def __init__(
        self,
        *,
        detecteur: Any,
        superviseur: Any,
        correcteur: Any,
        actions: Any,
        writer: Any,
        resoudre_depot: Callable[[str], Optional[tuple[Any, list[RegleDiagnostic]]]],
    ) -> None:
        self._detecteur = detecteur
        self._superviseur = superviseur
        self._correcteur = correcteur
        self._actions = actions
        self._writer = writer
        self._resoudre_depot = resoudre_depot

    async def traiter(self, message: dict[str, Any]) -> None:
        type_message = message.get("type")
        if type_message == "workflow_run":
            await self._traiter_workflow_run(message)
        elif type_message == "validation_humaine":
            await self._traiter_validation(message)
        else:
            log.warning("traitement.type_inconnu", type=type_message)

    # --- workflow_run --------------------------------------------------

    async def _traiter_workflow_run(self, message: dict[str, Any]) -> None:
        depot_nom = message.get("depot", "")
        resolu = self._resoudre_depot(depot_nom)
        if resolu is None:
            log.info("traitement.depot_non_configure", depot=depot_nom)
            return
        depot, regles = resolu
        if not depot.actif:
            return

        contenu_logs = await self._actions.telecharger_logs(
            depot_nom, message.get("workflow_run_id"), depot.installation_id
        )
        extrait_log = extraire_texte_logs(contenu_logs)

        anomalie = self._detecteur.detecter(
            message, depot.id, regles,
            seuil_timeout_secondes=depot.seuil_timeout_secondes,
            extrait_log=extrait_log,
        )
        if anomalie is None:
            return

        decision = self._superviseur.evaluer(anomalie)
        if decision == DecisionEscalade.JOURNALISER_SEULEMENT:
            self._journaliser(TypeEvenement.DETECTION, anomalie, depot_nom, "SUCCÈS")
        elif decision == DecisionEscalade.INTERVENTION_AUTO:
            await self._intervenir(anomalie, depot, depot_nom, extrait_log, regles)
        else:  # NOTIFIER_ET_ATTENDRE ou VALIDATION_HUMAINE_REQUISE
            await self._escalader(anomalie, depot, depot_nom)

    async def _intervenir(
        self, anomalie: Anomalie, depot: Any, depot_nom: str,
        extrait_log: str, regles: list[RegleDiagnostic],
    ) -> None:
        regle = next(
            (r for r in regles if r.id == anomalie.regle_declenchee_id), None
        )
        proposition = await self._correcteur.proposer(anomalie, extrait_log, regle)
        anomalie.demarrer_traitement()

        if proposition.type == TypeCorrection.PULL_REQUEST:
            await self._actions.creer_pull_request(
                depot_nom, depot.installation_id,
                titre=proposition.titre_pr or "fix: correctif Mirador",
                corps=proposition.corps_pr or proposition.justification,
                branche_source=f"mirador/fix-{anomalie.workflow_run_id}",
                branche_cible=anomalie_branche(anomalie),
            )
            action = "PULL_REQUEST"
        else:
            await self._actions.relancer_workflow(
                depot_nom, anomalie.workflow_run_id, depot.installation_id
            )
            action = "RELANCE"

        anomalie.marquer_resolue()
        self._journaliser(
            TypeEvenement.INTERVENTION, anomalie, depot_nom, "SUCCÈS", type_action=action
        )

    async def _escalader(self, anomalie: Anomalie, depot: Any, depot_nom: str) -> None:
        corps = _corps_issue(anomalie, depot_nom)
        await self._actions.ouvrir_issue(
            depot_nom, depot.installation_id,
            titre=f"[Mirador] Anomalie {anomalie.niveau_risque.name} — "
                  f"{anomalie.workflow_nom} sur {depot_nom}",
            corps=corps,
            labels=["mirador", anomalie.niveau_risque.name.lower()],
        )
        anomalie.demarrer_traitement()
        anomalie.escalader()
        self._journaliser(TypeEvenement.ESCALADE, anomalie, depot_nom, "EN_ATTENTE")

    # --- validation_humaine --------------------------------------------

    async def _traiter_validation(self, message: dict[str, Any]) -> None:
        depot_nom = message.get("depot", "")
        resolu = self._resoudre_depot(depot_nom)
        if resolu is None:
            return
        depot, _ = resolu
        numero = message.get("issue_number")
        acteur = message.get("acteur", "")
        commande = message.get("commande")

        if commande == "approuver":
            await self._actions.commenter_issue(
                depot_nom, depot.installation_id, numero,
                f"✅ Intervention approuvée par @{acteur} — exécution en cours.",
            )
            await self._actions.fermer_issue(
                depot_nom, depot.installation_id, numero, label="resolved"
            )
            self._journaliser_validation(
                TypeEvenement.VALIDATION, message, depot_nom, f"humain:{acteur}"
            )
        elif commande == "rejeter":
            motif = message.get("motif", "")
            await self._actions.commenter_issue(
                depot_nom, depot.installation_id, numero,
                f"❌ Intervention rejetée par @{acteur}.\nMotif : {motif}",
            )
            await self._actions.fermer_issue(
                depot_nom, depot.installation_id, numero, label="rejected"
            )
            self._journaliser_validation(
                TypeEvenement.REJET, message, depot_nom, f"humain:{acteur}", resultat=motif
            )

    # --- journalisation ------------------------------------------------

    def _journaliser(
        self, type_evenement: str, anomalie: Anomalie, depot_nom: str,
        statut: str, *, type_action: Optional[str] = None,
    ) -> None:
        self._writer.ecrire([EvenementJournal(
            correlation_id=anomalie.correlation_id,
            type_evenement=type_evenement,
            niveau_risque=anomalie.niveau_risque.name,
            depot=depot_nom,
            acteur=_ACTEUR_AGENT,
            statut=statut,
            workflow_run_id=anomalie.workflow_run_id,
            type_anomalie=anomalie.type,
            type_action=type_action,
            resultat=anomalie.cause_identifiee,
        )])

    def _journaliser_validation(
        self, type_evenement: str, message: dict[str, Any], depot_nom: str,
        acteur: str, *, resultat: Optional[str] = None,
    ) -> None:
        from uuid import UUID
        self._writer.ecrire([EvenementJournal(
            correlation_id=UUID(message["anomalie_id"]),
            type_evenement=type_evenement,
            niveau_risque="HIGH",
            depot=depot_nom,
            acteur=acteur,
            statut="SUCCÈS",
            resultat=resultat,
        )])


def anomalie_branche(anomalie: Anomalie) -> str:
    """Branche cible d'une PR de correction (par défaut la branche par défaut)."""
    return "main"


def _corps_issue(anomalie: Anomalie, depot_nom: str) -> str:
    return (
        f"## Anomalie détectée\n\n"
        f"| Champ | Valeur |\n|-------|--------|\n"
        f"| Dépôt | {depot_nom} |\n"
        f"| Workflow | {anomalie.workflow_nom} |\n"
        f"| Niveau de risque | {anomalie.niveau_risque.name} |\n"
        f"| Run GitHub | #{anomalie.workflow_run_id} |\n\n"
        f"## Cause identifiée\n\n{anomalie.cause_identifiee or 'Non identifiée automatiquement.'}\n\n"
        f"Pour valider ou rejeter : `/approuver` ou `/rejeter <motif>`.\n\n"
        f"<!-- mirador:anomalie_id:{anomalie.id} -->\n"
    )
