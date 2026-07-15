"""Tests d'intégration : orchestration du pipeline de traitement.

Câble détecteur + superviseur (réels) avec des doubles pour le correcteur, le
client GitHub Actions et le writer d'audit. Vérifie chaque branche de décision :
pas d'anomalie, intervention auto (RELANCE / PR), escalade humaine, et le retour
de validation (approuver / rejeter).
"""
import io
import zipfile
from uuid import uuid4

import pytest


def _zip_logs(texte: str) -> bytes:
    """Emballe un texte de log comme le fait l'API GitHub (archive ZIP)."""
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("build/2_Run.txt", texte)
    return tampon.getvalue()

from src.agents.correcteur import PropositionCorrection, TypeCorrection
from src.agents.detecteur import Detecteur
from src.agents.superviseur import Superviseur
from src.domaine.depot import DepotSurveille
from src.domaine.journal import TypeEvenement
from src.domaine.regle import ActionRecommandee, OrigineRegle, RegleDiagnostic
from src.gestionnaires.traitement import Traitement

RESPONSABLE = "aboigues"
DEPOT = "aboigues/k8t"
INSTALLATION_ID = 98765


def _depot(regles_ids=None) -> DepotSurveille:
    return DepotSurveille(
        identifiant_github=DEPOT,
        installation_id=INSTALLATION_ID,
        responsables=[RESPONSABLE],
        regles_ids=regles_ids or [],
    )


def _regle_relance() -> RegleDiagnostic:
    return RegleDiagnostic(
        nom="timeout-connexion-bd",
        description="Timeout de connexion à la base de données",
        pattern_log="connection timeout",
        action_recommandee=ActionRecommandee.RELANCE,
        origine=OrigineRegle.MANUELLE,
    )


def _message_workflow(*, conclusion="failure", head_branch="feature/x") -> dict:
    return {
        "type": "workflow_run",
        "delivery_id": str(uuid4()),
        "depot": DEPOT,
        "workflow_run_id": 12345678,
        "workflow_nom": "CI",
        "conclusion": conclusion,
        "head_branch": head_branch,
        "run_started_at": "2026-07-10T10:00:00Z",
        "run_completed_at": "2026-07-10T10:05:00Z",
    }


def _message_validation(commande="approuver", motif="") -> dict:
    return {
        "type": "validation_humaine",
        "delivery_id": str(uuid4()),
        "depot": DEPOT,
        "issue_number": 42,
        "anomalie_id": str(uuid4()),
        "commande": commande,
        "motif": motif,
        "acteur": RESPONSABLE,
    }


class _CorrecteurFake:
    def __init__(self, proposition: PropositionCorrection):
        self._proposition = proposition
        self.appels = 0

    async def proposer(self, anomalie, extrait_log, regle=None):
        self.appels += 1
        return self._proposition


class _ActionsFake:
    def __init__(self, logs: bytes = b"aucun pattern"):
        self._logs = logs
        self.relances: list = []
        self.prs: list = []
        self.issues_ouvertes: list = []
        self.commentaires: list = []
        self.fermetures: list = []

    async def telecharger_logs(self, depot, run_id, installation_id) -> bytes:
        return self._logs

    async def relancer_workflow(self, depot, run_id, installation_id) -> None:
        self.relances.append((depot, run_id))

    async def creer_pull_request(self, depot, installation_id, **kwargs) -> dict:
        self.prs.append(kwargs)
        return {"number": 7, "html_url": "https://github.com/x/pull/7"}

    async def ouvrir_issue(self, depot, installation_id, *, titre, corps, labels) -> dict:
        self.issues_ouvertes.append({"titre": titre, "corps": corps, "labels": labels})
        return {"number": 42, "html_url": "https://github.com/x/issues/42"}

    async def commenter_issue(self, depot, installation_id, numero, corps) -> None:
        self.commentaires.append({"numero": numero, "corps": corps})

    async def fermer_issue(self, depot, installation_id, numero, *, label=None) -> None:
        self.fermetures.append({"numero": numero, "label": label})


class _WriterFake:
    def __init__(self):
        self.evenements = []

    def ecrire(self, evenements) -> str:
        self.evenements.extend(evenements)
        return "checksum-factice"


def _traitement(actions, writer, correcteur, regles=None, deja_traite=None,
                lire_proposition=None):
    depot = _depot(regles_ids=[r.id for r in (regles or [])])

    def resoudre(nom_depot):
        if nom_depot != DEPOT:
            return None
        return depot, (regles or [])

    return Traitement(
        detecteur=Detecteur(),
        superviseur=Superviseur(),
        correcteur=correcteur,
        actions=actions,
        writer=writer,
        resoudre_depot=resoudre,
        deja_traite=deja_traite,
        lire_proposition=lire_proposition,
    )


class TestPasDAnomalie:
    async def test_succes_ne_declenche_rien(self):
        actions, writer = _ActionsFake(), _WriterFake()
        correcteur = _CorrecteurFake(PropositionCorrection(type=TypeCorrection.RELANCE, justification="x"))
        traitement = _traitement(actions, writer, correcteur)
        await traitement.traiter(_message_workflow(conclusion="success"))
        assert actions.relances == [] and writer.evenements == []

    async def test_depot_inconnu_ignore(self):
        actions, writer = _ActionsFake(), _WriterFake()
        correcteur = _CorrecteurFake(PropositionCorrection(type=TypeCorrection.RELANCE, justification="x"))
        traitement = _traitement(actions, writer, correcteur)
        message = _message_workflow()
        message["depot"] = "inconnu/depot"
        await traitement.traiter(message)
        assert writer.evenements == []


class TestInterventionAuto:
    async def test_low_relance(self):
        regle = _regle_relance()
        actions = _ActionsFake(logs=b"Error: connection timeout after 30s")
        writer = _WriterFake()
        correcteur = _CorrecteurFake(PropositionCorrection(type=TypeCorrection.RELANCE, justification="flaky"))
        traitement = _traitement(actions, writer, correcteur, regles=[regle])
        await traitement.traiter(_message_workflow())
        assert actions.relances == [(DEPOT, 12345678)]
        assert any(e.type_evenement == TypeEvenement.INTERVENTION for e in writer.evenements)

    async def test_low_relance_sur_logs_zippes(self):
        # Cas réel : GitHub renvoie les logs en archive ZIP. Le matching de la
        # règle (pattern_log) doit fonctionner après dézippage.
        regle = _regle_relance()
        actions = _ActionsFake(logs=_zip_logs("Error: connection timeout after 30s"))
        writer = _WriterFake()
        correcteur = _CorrecteurFake(PropositionCorrection(type=TypeCorrection.RELANCE, justification="flaky"))
        traitement = _traitement(actions, writer, correcteur, regles=[regle])
        await traitement.traiter(_message_workflow())
        assert actions.relances == [(DEPOT, 12345678)]

    async def test_low_pull_request(self):
        actions, writer = _ActionsFake(), _WriterFake()
        correcteur = _CorrecteurFake(PropositionCorrection(
            type=TypeCorrection.PULL_REQUEST, justification="dep obsolète",
            titre_pr="fix: dep", corps_pr="corps", correctif="diff",
        ))
        # échec sur branche feature sans règle → LOW → intervention auto
        traitement = _traitement(actions, writer, correcteur)
        await traitement.traiter(_message_workflow())
        assert len(actions.prs) == 1
        assert actions.relances == []


class TestDeduplicationDeliveryId:
    async def test_message_deja_traite_est_ignore(self):
        # deja_traite renvoie True → aucune action ni journalisation.
        actions, writer = _ActionsFake(logs=b"connection timeout"), _WriterFake()
        correcteur = _CorrecteurFake(PropositionCorrection(type=TypeCorrection.RELANCE, justification="x"))
        traitement = _traitement(actions, writer, correcteur, regles=[_regle_relance()],
                                 deja_traite=lambda _id: True)
        message = _message_workflow()
        message["delivery_id"] = "livr-deja-vu"
        await traitement.traiter(message)
        assert actions.relances == []
        assert writer.evenements == []

    async def test_message_neuf_est_traite_et_estampille(self):
        actions, writer = _ActionsFake(logs=b"connection timeout"), _WriterFake()
        correcteur = _CorrecteurFake(PropositionCorrection(type=TypeCorrection.RELANCE, justification="x"))
        traitement = _traitement(actions, writer, correcteur, regles=[_regle_relance()],
                                 deja_traite=lambda _id: False)
        message = _message_workflow()
        message["delivery_id"] = "livr-neuf"
        await traitement.traiter(message)
        assert actions.relances == [(DEPOT, 12345678)]
        # Le delivery_id est estampillé sur les événements journalisés (dédup future).
        assert all(e.delivery_id == "livr-neuf" for e in writer.evenements)
        assert writer.evenements


class TestEscaladeHumaine:
    async def test_high_ouvre_issue_avec_anomalie_id(self):
        actions, writer = _ActionsFake(), _WriterFake()
        correcteur = _CorrecteurFake(PropositionCorrection(type=TypeCorrection.RELANCE, justification="x"))
        # échec sur main sans règle → MEDIUM ; HIGH nécessiterait timeout sur main
        traitement = _traitement(actions, writer, correcteur)
        await traitement.traiter(_message_workflow(conclusion="timed_out", head_branch="main"))
        assert len(actions.issues_ouvertes) == 1
        issue = actions.issues_ouvertes[0]
        assert "mirador:anomalie_id:" in issue["corps"]
        assert "mirador" in issue["labels"]
        # L'escalade calcule et PERSISTE une proposition (exécutée sur /approuver),
        # mais ne l'exécute pas encore : ni relance ni PR à ce stade.
        assert correcteur.appels == 1
        assert actions.relances == [] and actions.prs == []
        assert any(e.type_evenement == TypeEvenement.ESCALADE for e in writer.evenements)

    async def test_issue_affiche_la_proposition_relance(self):
        actions, writer = _ActionsFake(), _WriterFake()
        correcteur = _CorrecteurFake(PropositionCorrection(
            type=TypeCorrection.RELANCE, justification="incident transitoire réseau"))
        traitement = _traitement(actions, writer, correcteur)
        await traitement.traiter(_message_workflow(conclusion="timed_out", head_branch="main"))
        corps = actions.issues_ouvertes[0]["corps"]
        assert "Correction proposée" in corps
        assert "incident transitoire réseau" in corps

    async def test_issue_affiche_la_proposition_pull_request(self):
        actions, writer = _ActionsFake(), _WriterFake()
        correcteur = _CorrecteurFake(PropositionCorrection(
            type=TypeCorrection.PULL_REQUEST, justification="deps obsolètes",
            titre_pr="fix: bump deps", corps_pr="## Détail\nMettre à jour X et Y."))
        traitement = _traitement(actions, writer, correcteur)
        await traitement.traiter(_message_workflow(conclusion="timed_out", head_branch="main"))
        corps = actions.issues_ouvertes[0]["corps"]
        assert "fix: bump deps" in corps
        assert "Mettre à jour X et Y" in corps


class TestExecutionSurApprobation:
    async def test_escalade_calcule_et_persiste_la_proposition(self):
        actions, writer = _ActionsFake(), _WriterFake()
        correcteur = _CorrecteurFake(PropositionCorrection(type=TypeCorrection.RELANCE, justification="flaky"))
        traitement = _traitement(actions, writer, correcteur)
        await traitement.traiter(_message_workflow(conclusion="timed_out", head_branch="main"))
        esc = next(e for e in writer.evenements if e.type_evenement == TypeEvenement.ESCALADE)
        assert esc.details["proposition"]["type"] == TypeCorrection.RELANCE
        assert esc.details["workflow_run_id"] == 12345678

    async def test_approuver_execute_relance(self):
        actions, writer = _ActionsFake(), _WriterFake()
        correcteur = _CorrecteurFake(PropositionCorrection(type=TypeCorrection.RELANCE, justification="x"))
        details = {"proposition": {"type": TypeCorrection.RELANCE, "justification": "x"},
                   "workflow_run_id": 555, "head_branch": "main"}
        traitement = _traitement(actions, writer, correcteur, lire_proposition=lambda _id: details)
        await traitement.traiter(_message_validation(commande="approuver"))
        assert actions.relances == [(DEPOT, 555)]
        assert actions.fermetures[0]["label"] == "resolved"
        assert "relanc" in actions.commentaires[0]["corps"].lower()

    async def test_approuver_execute_pull_request(self):
        actions, writer = _ActionsFake(), _WriterFake()
        correcteur = _CorrecteurFake(PropositionCorrection(type=TypeCorrection.RELANCE, justification="x"))
        details = {"proposition": {"type": TypeCorrection.PULL_REQUEST, "justification": "j",
                                   "titre_pr": "fix: X", "corps_pr": "corps", "correctif": "diff"},
                   "workflow_run_id": 555, "head_branch": "main"}
        traitement = _traitement(actions, writer, correcteur, lire_proposition=lambda _id: details)
        await traitement.traiter(_message_validation(commande="approuver"))
        assert len(actions.prs) == 1 and actions.relances == []

    async def test_approuver_sans_proposition_ferme_sans_action(self):
        actions, writer = _ActionsFake(), _WriterFake()
        correcteur = _CorrecteurFake(PropositionCorrection(type=TypeCorrection.RELANCE, justification="x"))
        traitement = _traitement(actions, writer, correcteur, lire_proposition=lambda _id: None)
        await traitement.traiter(_message_validation(commande="approuver"))
        assert actions.relances == [] and actions.prs == []
        assert actions.fermetures[0]["label"] == "resolved"

    async def test_approuver_execution_qui_echoue_ferme_proprement(self):
        # Si l'exécution de la proposition échoue (ex. PR sur branche inexistante),
        # l'approbation ne doit PAS planter : commentaire honnête + fermeture.
        actions, writer = _ActionsFake(), _WriterFake()

        async def _boom(*a, **k):
            raise RuntimeError("422 no such branch")
        actions.creer_pull_request = _boom
        correcteur = _CorrecteurFake(PropositionCorrection(type=TypeCorrection.RELANCE, justification="x"))
        details = {"proposition": {"type": TypeCorrection.PULL_REQUEST, "justification": "j",
                                   "titre_pr": "t", "corps_pr": "c"},
                   "workflow_run_id": 555, "head_branch": "main"}
        traitement = _traitement(actions, writer, correcteur, lire_proposition=lambda _id: details)
        await traitement.traiter(_message_validation(commande="approuver"))
        assert actions.fermetures[0]["label"] == "resolved"
        assert "manuel" in actions.commentaires[0]["corps"].lower()
        assert any(e.type_evenement == TypeEvenement.VALIDATION for e in writer.evenements)


class TestValidationHumaine:
    async def test_approuver_commente_et_ferme_resolved(self):
        actions, writer = _ActionsFake(), _WriterFake()
        correcteur = _CorrecteurFake(PropositionCorrection(type=TypeCorrection.RELANCE, justification="x"))
        traitement = _traitement(actions, writer, correcteur)
        await traitement.traiter(_message_validation(commande="approuver"))
        assert actions.commentaires and actions.fermetures[0]["label"] == "resolved"
        assert any(e.type_evenement == TypeEvenement.VALIDATION for e in writer.evenements)

    async def test_rejeter_commente_et_ferme_rejected(self):
        actions, writer = _ActionsFake(), _WriterFake()
        correcteur = _CorrecteurFake(PropositionCorrection(type=TypeCorrection.RELANCE, justification="x"))
        traitement = _traitement(actions, writer, correcteur)
        motif = "Le correctif cible le mauvais service, intervention manuelle"
        await traitement.traiter(_message_validation(commande="rejeter", motif=motif))
        assert actions.fermetures[0]["label"] == "rejected"
        assert any(e.type_evenement == TypeEvenement.REJET for e in writer.evenements)
