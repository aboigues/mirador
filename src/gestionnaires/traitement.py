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
_ACTION_ECHEC = "ÉCHEC_EXÉCUTION"
_ACTION_PR_CONSTRUCTION = "PR_EN_CONSTRUCTION"
_WORKFLOW_AUTOFIX = "mirador-autofix.yml"


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
        deja_traite: Optional[Callable[[str], bool]] = None,
        lire_proposition: Optional[Callable[[str], Optional[dict[str, Any]]]] = None,
    ) -> None:
        self._detecteur = detecteur
        self._superviseur = superviseur
        self._correcteur = correcteur
        self._actions = actions
        self._writer = writer
        self._resoudre_depot = resoudre_depot
        self._deja_traite = deja_traite
        self._lire_proposition = lire_proposition
        self._delivery_courant: Optional[str] = None

    async def traiter(self, message: dict[str, Any]) -> None:
        delivery_id = message.get("delivery_id")
        # Idempotence (queue standard, livraison au moins une fois) : si ce
        # delivery_id a déjà été journalisé, on ne le retraite pas.
        if self._deja_traite is not None and delivery_id and self._deja_traite(delivery_id):
            log.info("traitement.deja_traite", delivery_id=delivery_id)
            return
        self._delivery_courant = delivery_id

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
            await self._escalader(anomalie, depot, depot_nom, extrait_log, regles)

    async def _proposer(
        self, anomalie: Anomalie, extrait_log: str, regles: list[RegleDiagnostic]
    ) -> Any:
        """Propose une correction, ou None si le correcteur est indisponible.

        La détection ne doit pas dépendre de la disponibilité de Claude : une
        panne du correcteur (crédits épuisés, 400, API down) ne doit jamais
        empêcher Mirador de remonter une anomalie qu'il a bien détectée.
        """
        regle = next(
            (r for r in regles if r.id == anomalie.regle_declenchee_id), None
        )
        try:
            return await self._correcteur.proposer(anomalie, extrait_log, regle)
        except Exception as exc:
            log.warning("correcteur.indisponible",
                        anomalie_id=str(anomalie.id), err=str(exc))
            return None

    async def _intervenir(
        self, anomalie: Anomalie, depot: Any, depot_nom: str,
        extrait_log: str, regles: list[RegleDiagnostic],
    ) -> None:
        proposition = await self._proposer(anomalie, extrait_log, regles)
        if proposition is None:
            # Rien à exécuter, mais l'anomalie est tracée plutôt que perdue en DLQ.
            self._journaliser(
                TypeEvenement.INTERVENTION, anomalie, depot_nom, _ACTION_ECHEC,
                type_action=None,
            )
            return
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

    async def _escalader(
        self, anomalie: Anomalie, depot: Any, depot_nom: str,
        extrait_log: str, regles: list[RegleDiagnostic],
    ) -> None:
        # Calcule la proposition d'intervention et la PERSISTE dans l'événement
        # d'escalade : elle sera exécutée telle quelle si un responsable /approuver.
        # None si le correcteur est en panne — l'issue s'ouvre quand même.
        proposition = await self._proposer(anomalie, extrait_log, regles)

        corps = _corps_issue(anomalie, depot_nom, proposition)
        await self._actions.ouvrir_issue(
            depot_nom, depot.installation_id,
            titre=f"[Mirador] Anomalie {anomalie.niveau_risque.name} — "
                  f"{anomalie.workflow_nom} sur {depot_nom}",
            corps=corps,
            labels=["mirador", anomalie.niveau_risque.name.lower()],
        )
        anomalie.demarrer_traitement()
        anomalie.escalader()
        self._journaliser(
            TypeEvenement.ESCALADE, anomalie, depot_nom, "EN_ATTENTE",
            details={
                "proposition": proposition.model_dump() if proposition else None,
                "workflow_run_id": anomalie.workflow_run_id,
                "head_branch": anomalie_branche(anomalie),
            },
        )

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
            action = await self._executer_proposition_approuvee(message, depot, depot_nom)
            await self._actions.commenter_issue(
                depot_nom, depot.installation_id, numero,
                _message_approbation(acteur, action),
            )
            await self._actions.fermer_issue(
                depot_nom, depot.installation_id, numero, label="resolved"
            )
            self._journaliser_validation(
                TypeEvenement.VALIDATION, message, depot_nom, f"humain:{acteur}",
                resultat=action,
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

    async def _executer_proposition_approuvee(
        self, message: dict[str, Any], depot: Any, depot_nom: str
    ) -> Optional[str]:
        """Exécute la proposition persistée à l'escalade. Retourne le type d'action.

        Retourne None si aucune proposition n'est retrouvée (anciennes issues, ou
        lecteur non câblé) : dans ce cas l'approbation est simplement enregistrée.
        """
        if self._lire_proposition is None:
            return None
        details = self._lire_proposition(message.get("anomalie_id", ""))
        if not details or not details.get("proposition"):
            return None
        proposition = details["proposition"]
        run_id = details.get("workflow_run_id")
        try:
            if proposition.get("type") == TypeCorrection.PULL_REQUEST:
                return await self._ouvrir_pull_request_correctif(
                    proposition, depot, depot_nom, run_id,
                    details.get("head_branch", "main"),
                )
            await self._actions.relancer_workflow(depot_nom, run_id, depot.installation_id)
            return TypeCorrection.RELANCE
        except Exception as exc:
            # L'approbation reste valable même si l'exécution auto échoue : on ne
            # fait pas planter le flux (le correctif reste applicable à la main).
            log.warning("validation.execution_echouee",
                        type=proposition.get("type"), err=str(exc))
            return _ACTION_ECHEC

    async def _ouvrir_pull_request_correctif(
        self, proposition: dict[str, Any], depot: Any, depot_nom: str,
        run_id: Any, branche_cible: str,
    ) -> str:
        """Matérialise le correctif et retourne le type d'action réalisé.

        Trois voies, par ordre de préférence :
        1. `mise_a_jour` (deps Go) → délègue à un workflow de build réel qui
           régénère go.mod/go.sum et ouvre la PR (→ correctif qui passe la CI).
        2. `fichiers` fournis → crée la branche, écrit les fichiers, ouvre la PR.
        3. sinon → PR documentaire (MIRADOR-FIX.md) à compléter par un humain.
        """
        inst = depot.installation_id
        branche = f"mirador/fix-{run_id}"
        titre = proposition.get("titre_pr") or "fix: correctif Mirador"

        maj = proposition.get("mise_a_jour") or {}
        if maj.get("go_version") or maj.get("modules"):
            await self._actions.declencher_workflow(
                depot_nom, _WORKFLOW_AUTOFIX, branche_cible,
                {
                    "base_branch": branche_cible,
                    "head_branch": branche,
                    "go_version": maj.get("go_version") or "",
                    "modules": " ".join(maj.get("modules") or []),
                    "titre": titre,
                    "corps": proposition.get("corps_pr") or proposition.get("justification") or "",
                },
                inst,
            )
            return _ACTION_PR_CONSTRUCTION

        sha_base = await self._actions.obtenir_sha_tete(depot_nom, branche_cible, inst)
        await self._actions.creer_branche(depot_nom, branche, sha_base, inst)

        fichiers = proposition.get("fichiers") or []
        if fichiers:
            for fichier in fichiers:
                await self._actions.televerser_fichier(
                    depot_nom, fichier["chemin"], fichier["contenu"], branche, titre, inst
                )
        else:
            await self._actions.televerser_fichier(
                depot_nom, "MIRADOR-FIX.md",
                _document_correctif(proposition), branche, titre, inst,
            )

        await self._actions.creer_pull_request(
            depot_nom, inst,
            titre=titre,
            corps=proposition.get("corps_pr") or proposition.get("justification") or "",
            branche_source=branche,
            branche_cible=branche_cible,
        )
        return TypeCorrection.PULL_REQUEST

    # --- journalisation ------------------------------------------------

    def _journaliser(
        self, type_evenement: str, anomalie: Anomalie, depot_nom: str,
        statut: str, *, type_action: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
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
            details=details,
            resultat=anomalie.cause_identifiee,
            delivery_id=self._delivery_courant,
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
            delivery_id=self._delivery_courant,
        )])


def _document_correctif(proposition: dict[str, Any]) -> str:
    """Contenu du fichier de repli quand la proposition n'a pas de fichiers exploitables."""
    parties = [
        "# Correctif proposé par Mirador",
        "",
        proposition.get("corps_pr") or proposition.get("justification") or "",
    ]
    correctif = proposition.get("correctif")
    if correctif:
        parties += ["", "## Détail du correctif", "", "```", correctif, "```"]
    parties += ["", "> Correctif à compléter/appliquer par un responsable "
                "(Mirador n'a pas pu le matérialiser automatiquement)."]
    return "\n".join(parties) + "\n"


def _message_approbation(acteur: str, action: Optional[str]) -> str:
    if action == TypeCorrection.RELANCE:
        return f"✅ Approuvé par @{acteur} — workflow relancé par Mirador."
    if action == TypeCorrection.PULL_REQUEST:
        return f"✅ Approuvé par @{acteur} — pull request de correction ouverte par Mirador."
    if action == _ACTION_PR_CONSTRUCTION:
        return (f"✅ Approuvé par @{acteur} — correctif en cours de construction "
                f"(build lancé) ; la pull request sera ouverte automatiquement.")
    if action == _ACTION_ECHEC:
        return (f"✅ Approuvé par @{acteur} — l'exécution automatique de la proposition "
                f"a échoué ; le correctif est à appliquer manuellement (voir les logs).")
    return (f"✅ Approuvé par @{acteur} — approbation enregistrée "
            f"(aucune action automatique associée à cette anomalie).")


def anomalie_branche(anomalie: Anomalie) -> str:
    """Branche cible d'une PR de correction (par défaut la branche par défaut)."""
    return "main"


def _section_proposition(proposition: Any) -> str:
    """Rend la proposition d'intervention en Markdown pour le corps de l'issue.

    L'humain voit ainsi ce que Mirador exécuterait sur /approuver — utile aussi
    quand l'exécution auto échoue (le correctif reste actionnable manuellement).

    `None` = l'analyse n'a pas pu aboutir (correcteur indisponible). On le dit
    explicitement : un silence se confondrait avec « rien à proposer ».
    """
    if proposition is None:
        return ("## Analyse indisponible\n\n"
                "⚠️ Mirador n'a pas pu analyser cet échec (correcteur "
                "indisponible) : aucune correction n'est proposée. L'anomalie "
                "ci-dessus a bien été détectée et reste à traiter manuellement.\n\n")
    if proposition.type == TypeCorrection.PULL_REQUEST:
        detail = ""
        if proposition.corps_pr:
            detail = (f"\n\n<details><summary>Détail de la PR proposée</summary>\n\n"
                      f"### {proposition.titre_pr or 'Correctif Mirador'}\n\n"
                      f"{proposition.corps_pr}\n\n</details>")
        return (f"## Correction proposée par Mirador\n\n"
                f"**Pull request** — {proposition.justification}{detail}\n\n")
    return (f"## Correction proposée par Mirador\n\n"
            f"**Relance du workflow** — {proposition.justification}\n\n")


def _corps_issue(anomalie: Anomalie, depot_nom: str, proposition: Any = None) -> str:
    return (
        f"## Anomalie détectée\n\n"
        f"| Champ | Valeur |\n|-------|--------|\n"
        f"| Dépôt | {depot_nom} |\n"
        f"| Workflow | {anomalie.workflow_nom} |\n"
        f"| Niveau de risque | {anomalie.niveau_risque.name} |\n"
        f"| Run GitHub | #{anomalie.workflow_run_id} |\n\n"
        f"## Cause identifiée\n\n{anomalie.cause_identifiee or 'Non identifiée automatiquement.'}\n\n"
        f"{_section_proposition(proposition)}"
        f"Pour valider ou rejeter : `/approuver` ou `/rejeter <motif>`.\n\n"
        f"<!-- mirador:anomalie_id:{anomalie.correlation_id} -->\n"
    )
