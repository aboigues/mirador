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


def _depot(regles_ids=None, lecture_depot=False) -> DepotSurveille:
    return DepotSurveille(
        identifiant_github=DEPOT,
        installation_id=INSTALLATION_ID,
        responsables=[RESPONSABLE],
        regles_ids=regles_ids or [],
        lecture_depot=lecture_depot,
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
        self.dernier_extrait: str | None = None
        self.dernier_contexte_depot: str | None = None

    async def proposer(self, anomalie, extrait_log, regle=None, contexte_depot=None):
        self.appels += 1
        self.dernier_extrait = extrait_log
        self.dernier_contexte_depot = contexte_depot
        return self._proposition


class _CorrecteurEnPanne:
    """Correcteur indisponible (crédits épuisés, panne API, 400…)."""

    def __init__(self, erreur: Exception | None = None):
        self._erreur = erreur or RuntimeError("credit balance is too low")

    async def proposer(self, anomalie, extrait_log, regle=None, contexte_depot=None):
        raise self._erreur


class _ActionsFake:
    def __init__(self, logs: bytes = b"aucun pattern", arbre: list | None = None,
                 contenus: dict | None = None, resultats_recherche: dict | None = None):
        self._logs = logs
        self._arbre = arbre or []
        self._contenus = contenus or {}
        self._resultats_recherche = resultats_recherche or {}
        self.relances: list = []
        self.prs: list = []
        self.issues_ouvertes: list = []
        self.commentaires: list = []
        self.fermetures: list = []
        self.branches: list = []
        self.fichiers: list = []
        self.workflows: list = []
        self.appels_lister_fichiers: list = []
        self.appels_lire_fichier: list = []
        self.appels_chercher_code: list = []

    async def telecharger_logs(self, depot, run_id, installation_id) -> bytes:
        return self._logs

    async def lister_fichiers(self, depot, ref, installation_id) -> list[dict]:
        self.appels_lister_fichiers.append((depot, ref))
        return self._arbre

    async def lire_fichier(self, depot, chemin, ref, installation_id):
        self.appels_lire_fichier.append(chemin)
        return self._contenus.get(chemin)

    async def chercher_code(self, depot, requete, installation_id) -> list[str]:
        self.appels_chercher_code.append(requete)
        return self._resultats_recherche.get(requete, [])

    async def declencher_workflow(self, depot, fichier_workflow, ref, inputs, installation_id) -> None:
        self.workflows.append({"fichier": fichier_workflow, "ref": ref, "inputs": inputs})

    async def obtenir_sha_tete(self, depot, branche, installation_id) -> str:
        return "base-sha"

    async def creer_branche(self, depot, nom_branche, sha_base, installation_id) -> None:
        self.branches.append((nom_branche, sha_base))

    async def televerser_fichier(self, depot, chemin, contenu, branche, message, installation_id) -> None:
        self.fichiers.append({"chemin": chemin, "contenu": contenu, "branche": branche})

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


class _ActionsAvecJobs(_ActionsFake):
    """Client GitHub qui expose les jobs d'un run, comme l'API réelle.

    `jobs` : liste de (id, nom, conclusion, log).
    """

    def __init__(self, jobs: list[tuple[int, str, str, str]]):
        super().__init__()
        self._jobs = jobs
        self.jobs_telecharges: list[int] = []

    async def lister_jobs_en_echec(self, depot, run_id, installation_id) -> list[dict]:
        return [
            {"id": ident, "nom": nom}
            for ident, nom, conclusion, _ in self._jobs
            if conclusion in ("failure", "timed_out")
        ]

    async def telecharger_logs_job(self, depot, job_id, installation_id) -> bytes:
        self.jobs_telecharges.append(job_id)
        texte = next(log for ident, _, _, log in self._jobs if ident == job_id)
        return _zip_logs(texte)

    async def telecharger_logs(self, depot, run_id, installation_id) -> bytes:
        raise AssertionError(
            "le log du run entier ne doit pas être téléchargé quand des jobs "
            "en échec sont identifiés"
        )


class _WriterFake:
    def __init__(self):
        self.evenements = []

    def ecrire(self, evenements) -> str:
        self.evenements.extend(evenements)
        return "checksum-factice"


def _traitement(actions, writer, correcteur, regles=None, deja_traite=None,
                lire_proposition=None, lecture_depot=False):
    depot = _depot(regles_ids=[r.id for r in (regles or [])], lecture_depot=lecture_depot)

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

    async def test_low_pull_request_avec_fichiers_materialise_la_branche(self):
        actions, writer = _ActionsFake(), _WriterFake()
        correcteur = _CorrecteurFake(PropositionCorrection(
            type=TypeCorrection.PULL_REQUEST, justification="dep obsolète",
            titre_pr="fix: dep", corps_pr="corps",
            fichiers=[{"chemin": "go.mod", "contenu": "module k8t\ngo 1.25.12\n"}],
        ))
        # échec sur branche feature sans règle → LOW → intervention auto
        traitement = _traitement(actions, writer, correcteur)
        await traitement.traiter(_message_workflow())
        assert actions.branches == [("mirador/fix-12345678", "base-sha")]
        assert actions.fichiers[0]["chemin"] == "go.mod"
        assert len(actions.prs) == 1
        assert actions.relances == []

    async def test_low_pull_request_sans_fichiers_escalade_au_lieu_de_pr_vide(self):
        # Régression : Mirador ouvrait une PR sans aucun correctif matérialisé
        # (ni branche, ni fichier) quand le correcteur ne fournissait pas
        # `fichiers`. Une PR sans diff coûte plus de temps de revue qu'elle n'en
        # fait gagner — on escalade désormais vers un humain, comme l'ABSTENTION.
        actions, writer = _ActionsFake(), _WriterFake()
        correcteur = _CorrecteurFake(PropositionCorrection(
            type=TypeCorrection.PULL_REQUEST, justification="dep obsolète",
            titre_pr="fix: dep", corps_pr="corps", correctif="diff",
        ))
        traitement = _traitement(actions, writer, correcteur)
        await traitement.traiter(_message_workflow())
        assert actions.prs == [] and actions.branches == [] and actions.relances == []
        assert len(actions.issues_ouvertes) == 1


class TestContexteDepot:
    """`depot.lecture_depot` : accès en lecture au dépôt pour le correcteur.

    Opt-in — cf. kubernetes-formation#142 : le correcteur voyait la CVE et la
    version corrigée dans le log, mais s'abstenait faute de savoir dans quel
    fichier du dépôt l'image était référencée.
    """

    async def test_lecture_desactivee_ne_consulte_pas_le_depot(self):
        # Défaut : aucun appel API supplémentaire, aucun changement de comportement
        # pour les dépôts qui n'ont pas activé la fonctionnalité.
        actions, writer = _ActionsFake(), _WriterFake()
        correcteur = _CorrecteurFake(PropositionCorrection(type=TypeCorrection.RELANCE, justification="x"))
        traitement = _traitement(actions, writer, correcteur)
        await traitement.traiter(_message_workflow())
        assert actions.appels_lister_fichiers == []
        assert actions.appels_lire_fichier == []
        assert correcteur.dernier_contexte_depot is None

    async def test_lecture_activee_transmet_le_contexte_et_materialise_le_correctif(self):
        actions = _ActionsFake(
            arbre=[
                {"path": "tp08/compose.yaml", "size": 200},
                {"path": "README.md", "size": 50},  # pas un manifeste : filtré
            ],
            contenus={"tp08/compose.yaml": "image: postgres:18-alpine\n"},
        )
        writer = _WriterFake()
        correcteur = _CorrecteurFake(PropositionCorrection(
            type=TypeCorrection.PULL_REQUEST, justification="image stagnante",
            titre_pr="fix: postgres", corps_pr="corps",
            fichiers=[{"chemin": "tp08/compose.yaml", "contenu": "image: postgres:18\n"}],
        ))
        traitement = _traitement(actions, writer, correcteur, lecture_depot=True)
        await traitement.traiter(_message_workflow())
        assert actions.appels_lister_fichiers == [(DEPOT, "main")]
        assert actions.appels_lire_fichier == ["tp08/compose.yaml"]
        assert "tp08/compose.yaml" in correcteur.dernier_contexte_depot
        assert "postgres:18-alpine" in correcteur.dernier_contexte_depot
        # Le correctif se matérialise ensuite normalement (chemin inchangé).
        assert len(actions.prs) == 1
        assert actions.fichiers[0]["chemin"] == "tp08/compose.yaml"

    async def test_recherche_ciblee_priorise_le_fichier_pertinent_meme_gros(self):
        # Régression kubernetes-formation#142 : un dépôt à centaines de petits
        # manifestes évince par la taille le fichier réellement pertinent s'il
        # est plus gros que les autres. La recherche ciblée (sur la référence
        # d'image repérée dans le log) doit le trouver et le faire passer en
        # priorité, malgré sa taille, devant les fichiers de repli.
        fillers = [{"path": f"tp0{i}/manifest.yaml", "size": 80} for i in range(5)]
        cible = {"path": "tp03/14-network-storage-examples-secure.yaml", "size": 16000}
        actions = _ActionsFake(
            logs=b"Scan amazon/aws-cli:2.36.8 : CVE-2026-44605 (rpm) HIGH",
            arbre=fillers + [cible],
            contenus={
                **{f["path"]: "contenu filler\n" for f in fillers},
                cible["path"]: "CIBLE_AWS_CLI_CONTENU\n",
            },
            resultats_recherche={"amazon/aws-cli:2.36.8": [cible["path"]]},
        )
        writer = _WriterFake()
        correcteur = _CorrecteurFake(PropositionCorrection(type=TypeCorrection.ABSTENTION, justification="x"))
        traitement = _traitement(actions, writer, correcteur, lecture_depot=True)
        await traitement.traiter(_message_workflow())
        assert actions.appels_chercher_code == ["amazon/aws-cli:2.36.8"]
        assert cible["path"] in correcteur.dernier_contexte_depot
        assert "CIBLE_AWS_CLI_CONTENU" in correcteur.dernier_contexte_depot

    async def test_panne_de_lecture_depot_n_empeche_pas_le_traitement(self):
        actions, writer = _ActionsFake(), _WriterFake()

        async def _boom(depot, ref, installation_id):
            raise RuntimeError("API indisponible")
        actions.lister_fichiers = _boom
        correcteur = _CorrecteurFake(PropositionCorrection(type=TypeCorrection.RELANCE, justification="x"))
        traitement = _traitement(actions, writer, correcteur, lecture_depot=True)
        await traitement.traiter(_message_workflow())
        assert actions.relances == [(DEPOT, 12345678)]
        assert correcteur.dernier_contexte_depot is None


class TestReconstructionImagesDurcies:
    """Une CVE de paquet OS dans une image que le dépôt CONSTRUIT lui-même
    (docker/hardened/<nom>) n'a pas de fichier à éditer : seule une
    reconstruction (rebuild-hardened-images.yml) republie le paquet corrigé.
    Détection déterministe, sans appel Claude — cf. kubernetes-formation#149 :
    15 jobs de scan en échec, tous des CVE OS avec `Fixed Version`, mais sans
    aucun fichier du dépôt à éditer pour la plupart (images officielles) —
    sauf pour les images durcies (wordpress/httpd/nginx), où Mirador
    s'abstenait à tort faute de connaître ce chemin d'action.
    """

    def _actions_image_durcie(self) -> "_ActionsFake":
        return _ActionsFake(
            logs=_zip_logs(
                "Scan telemachlearning/wordpress:7.0-php8.5-apache CVE-2026-33164 HIGH"
            ),
            arbre=[{"path": "docker/hardened/wordpress/Dockerfile", "size": 500}],
        )

    async def test_detection_court_circuite_le_correcteur(self):
        actions = self._actions_image_durcie()
        writer = _WriterFake()
        correcteur = _CorrecteurFake(
            PropositionCorrection(type=TypeCorrection.ABSTENTION, justification="x")
        )
        traitement = _traitement(actions, writer, correcteur, lecture_depot=True)
        await traitement.traiter(_message_workflow(conclusion="timed_out", head_branch="main"))
        assert correcteur.appels == 0, "décision déterministe : pas d'appel Claude"
        esc = next(e for e in writer.evenements if e.type_evenement == TypeEvenement.ESCALADE)
        assert esc.details["proposition"]["type"] == TypeCorrection.REBUILD_IMAGES
        assert esc.details["proposition"]["images"] == ["wordpress"]

    async def test_issue_affiche_la_proposition_de_reconstruction(self):
        actions = self._actions_image_durcie()
        writer = _WriterFake()
        correcteur = _CorrecteurFake(
            PropositionCorrection(type=TypeCorrection.ABSTENTION, justification="x")
        )
        traitement = _traitement(actions, writer, correcteur, lecture_depot=True)
        await traitement.traiter(_message_workflow(conclusion="timed_out", head_branch="main"))
        corps = actions.issues_ouvertes[0]["corps"]
        assert "Reconstruction d'image" in corps
        assert "wordpress" in corps

    async def test_approuver_declenche_le_rebuild(self):
        actions, writer = _ActionsFake(), _WriterFake()
        correcteur = _CorrecteurFake(PropositionCorrection(type=TypeCorrection.RELANCE, justification="x"))
        details = {
            "proposition": {
                "type": TypeCorrection.REBUILD_IMAGES,
                "justification": "j",
                "images": ["wordpress"],
            },
            "workflow_run_id": 555, "head_branch": "main",
        }
        traitement = _traitement(actions, writer, correcteur, lire_proposition=lambda _id: details)
        await traitement.traiter(_message_validation(commande="approuver"))
        assert len(actions.workflows) == 1
        w = actions.workflows[0]
        assert w["fichier"] == "rebuild-hardened-images.yml"
        assert w["ref"] == "main"
        assert w["inputs"] == {"push": "true"}
        assert actions.branches == [] and actions.prs == [] and actions.relances == []
        assert "reconstruction" in actions.commentaires[0]["corps"].lower()
        assert actions.fermetures[0]["label"] == "resolved"

    async def test_intervention_auto_low_declenche_directement_le_rebuild(self):
        # Échec sur branche feature sans règle connue → LOW → intervention auto.
        actions = self._actions_image_durcie()
        writer = _WriterFake()
        correcteur = _CorrecteurFake(
            PropositionCorrection(type=TypeCorrection.ABSTENTION, justification="x")
        )
        traitement = _traitement(actions, writer, correcteur, lecture_depot=True)
        await traitement.traiter(_message_workflow())
        assert len(actions.workflows) == 1
        assert actions.workflows[0]["fichier"] == "rebuild-hardened-images.yml"
        assert actions.relances == [] and actions.prs == []

    async def test_image_durcie_detectee_meme_hors_du_budget_de_log(self):
        # kubernetes-formation#155 (2026-09-14) : 13 jobs en échec, dont
        # wordpress (durcie) en position 12 — _MAX_JOBS_ANALYSES (5) exclut son
        # log de l'extrait envoyé au correcteur, donc `extraire_images_durcies`
        # ne voit jamais la référence à l'image et Mirador s'abstient à tort.
        # Le NOM du job ("Scan telemachlearning/wordpress:...") est lui
        # toujours disponible, sans coût de téléchargement — la détection doit
        # s'appuyer dessus, pas seulement sur les logs tronqués.
        jobs = [
            (1, "Scan postgres:17-alpine", "failure", "CVE-2026-1 FIXED in 1.2\n"),
            (2, "Scan mysql:8.4", "failure", "CVE-2026-2 FIXED in 1.2\n"),
            (3, "Scan nginx:alpine", "failure", "CVE-2026-3 FIXED in 1.2\n"),
            (4, "Scan redis:alpine", "failure", "CVE-2026-4 FIXED in 1.2\n"),
            (5, "Scan alpine/helm:3.21.3", "failure", "CVE-2026-5 FIXED in 1.2\n"),
            (6, "Scan telemachlearning/wordpress:7.0-php8.5-apache", "failure",
             "CVE-2026-33164 FIXED in 1.2\n"),
        ]
        actions = _ActionsAvecJobs(jobs)
        actions._arbre = [{"path": "docker/hardened/wordpress/Dockerfile", "size": 500}]
        writer = _WriterFake()
        correcteur = _CorrecteurFake(
            PropositionCorrection(type=TypeCorrection.ABSTENTION, justification="x")
        )
        traitement = _traitement(actions, writer, correcteur, lecture_depot=True)
        await traitement.traiter(_message_workflow(conclusion="timed_out", head_branch="main"))
        assert correcteur.appels == 0, "décision déterministe : pas d'appel Claude"
        esc = next(e for e in writer.evenements if e.type_evenement == TypeEvenement.ESCALADE)
        assert esc.details["proposition"]["type"] == TypeCorrection.REBUILD_IMAGES
        assert esc.details["proposition"]["images"] == ["wordpress"]


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


class TestEscaladeResilienteAuCorrecteur:
    """La détection ne doit pas dépendre de la disponibilité de Claude.

    Si le correcteur tombe (crédits épuisés, panne API), l'anomalie a malgré tout
    été détectée : l'issue doit être ouverte quand même, sinon Mirador devient
    aveugle exactement au moment où il sert.
    """

    async def test_correcteur_en_panne_ouvre_quand_meme_l_issue(self):
        actions, writer = _ActionsFake(), _WriterFake()
        traitement = _traitement(actions, writer, _CorrecteurEnPanne())
        await traitement.traiter(_message_workflow(conclusion="timed_out", head_branch="main"))
        assert len(actions.issues_ouvertes) == 1
        issue = actions.issues_ouvertes[0]
        assert "mirador:anomalie_id:" in issue["corps"]  # /approuver reste possible
        assert any(e.type_evenement == TypeEvenement.ESCALADE for e in writer.evenements)

    async def test_issue_signale_l_absence_de_proposition(self):
        actions, writer = _ActionsFake(), _WriterFake()
        traitement = _traitement(actions, writer, _CorrecteurEnPanne())
        await traitement.traiter(_message_workflow(conclusion="timed_out", head_branch="main"))
        corps = actions.issues_ouvertes[0]["corps"]
        # L'humain doit savoir qu'il n'y a pas de proposition ET pourquoi — un
        # silence se confondrait avec « rien à proposer ».
        assert "Correction proposée" not in corps
        assert "analyse" in corps.lower()

    async def test_escalade_persiste_une_proposition_nulle(self):
        actions, writer = _ActionsFake(), _WriterFake()
        traitement = _traitement(actions, writer, _CorrecteurEnPanne())
        await traitement.traiter(_message_workflow(conclusion="timed_out", head_branch="main"))
        esc = next(e for e in writer.evenements if e.type_evenement == TypeEvenement.ESCALADE)
        # /approuver relit ce champ : None → approbation enregistrée sans action,
        # chemin déjà géré par _executer_proposition_approuvee.
        assert esc.details["proposition"] is None
        assert esc.details["workflow_run_id"] == 12345678

    async def test_panne_correcteur_n_empeche_pas_l_intervention_auto_low(self):
        # Sur un LOW, l'intervention auto passe par le correcteur : s'il tombe,
        # on ne peut rien exécuter, mais le message ne doit pas partir en DLQ.
        actions, writer = _ActionsFake(b"connection timeout"), _WriterFake()
        traitement = _traitement(actions, writer, _CorrecteurEnPanne(),
                                 regles=[_regle_relance()])
        await traitement.traiter(_message_workflow(conclusion="failure"))
        assert actions.relances == []  # rien exécuté, mais pas de crash


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

    async def test_approuver_pr_avec_fichiers_materialise_la_branche(self):
        actions, writer = _ActionsFake(), _WriterFake()
        correcteur = _CorrecteurFake(PropositionCorrection(type=TypeCorrection.RELANCE, justification="x"))
        details = {"proposition": {"type": TypeCorrection.PULL_REQUEST, "justification": "j",
                                   "titre_pr": "fix: X", "corps_pr": "corps",
                                   "fichiers": [{"chemin": "go.mod", "contenu": "module k8t\ngo 1.25.12\n"}]},
                   "workflow_run_id": 555, "head_branch": "main"}
        traitement = _traitement(actions, writer, correcteur, lire_proposition=lambda _id: details)
        await traitement.traiter(_message_validation(commande="approuver"))
        assert actions.branches == [("mirador/fix-555", "base-sha")]
        assert actions.fichiers[0]["chemin"] == "go.mod"
        assert actions.fichiers[0]["branche"] == "mirador/fix-555"
        assert len(actions.prs) == 1
        assert actions.prs[0]["branche_source"] == "mirador/fix-555"
        assert actions.relances == []

    async def test_approuver_pr_deps_declenche_le_build_reel(self):
        # Correctif de deps → délègue au workflow de build (go mod tidy), pas de
        # branche/fichier direct ; la PR sera ouverte par le workflow.
        actions, writer = _ActionsFake(), _WriterFake()
        correcteur = _CorrecteurFake(PropositionCorrection(type=TypeCorrection.RELANCE, justification="x"))
        details = {"proposition": {"type": TypeCorrection.PULL_REQUEST, "justification": "vulns",
                                   "titre_pr": "fix: bump", "corps_pr": "corps",
                                   "mise_a_jour": {"go_version": "1.25.12",
                                                   "modules": ["golang.org/x/net@v0.55.0"]}},
                   "workflow_run_id": 555, "head_branch": "main"}
        traitement = _traitement(actions, writer, correcteur, lire_proposition=lambda _id: details)
        await traitement.traiter(_message_validation(commande="approuver"))
        assert len(actions.workflows) == 1
        w = actions.workflows[0]
        assert w["fichier"] == "mirador-autofix.yml"
        assert w["ref"] == "main"
        assert w["inputs"]["head_branch"] == "mirador/fix-555"
        assert w["inputs"]["go_version"] == "1.25.12"
        assert w["inputs"]["modules"] == "golang.org/x/net@v0.55.0"
        # pas de création directe de branche/fichier/PR sur ce chemin
        assert actions.branches == [] and actions.prs == []
        assert "construction" in actions.commentaires[0]["corps"].lower()
        assert actions.fermetures[0]["label"] == "resolved"

    async def test_approuver_pr_sans_fichiers_ne_cree_rien(self):
        # Régression : Mirador ouvrait une PR documentaire (MIRADOR-FIX.md) sans
        # aucun vrai correctif quand la proposition n'avait pas de `fichiers`.
        # Sans matérialisation possible, on n'ouvre plus de PR du tout.
        actions, writer = _ActionsFake(), _WriterFake()
        correcteur = _CorrecteurFake(PropositionCorrection(type=TypeCorrection.RELANCE, justification="x"))
        details = {"proposition": {"type": TypeCorrection.PULL_REQUEST, "justification": "j",
                                   "titre_pr": "fix: X", "corps_pr": "corps", "correctif": "diff"},
                   "workflow_run_id": 555, "head_branch": "main"}
        traitement = _traitement(actions, writer, correcteur, lire_proposition=lambda _id: details)
        await traitement.traiter(_message_validation(commande="approuver"))
        assert actions.branches == [] and actions.fichiers == [] and actions.prs == []
        assert "manuel" in actions.commentaires[0]["corps"].lower()
        assert actions.fermetures[0]["label"] == "resolved"

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
                                   "titre_pr": "t", "corps_pr": "c",
                                   "fichiers": [{"chemin": "x.txt", "contenu": "y"}]},
                   "workflow_run_id": 555, "head_branch": "main"}
        traitement = _traitement(actions, writer, correcteur, lire_proposition=lambda _id: details)
        await traitement.traiter(_message_validation(commande="approuver"))
        assert actions.fermetures[0]["label"] == "resolved"
        assert "manuel" in actions.commentaires[0]["corps"].lower()
        assert any(e.type_evenement == TypeEvenement.VALIDATION for e in writer.evenements)


class TestCiblageDesJobsEnEchec:
    """Régression kubernetes-formation#129.

    Un run de matrice (33 jobs) dont 3 scans Trivy échouaient : Mirador
    concaténait tout le run puis n'en gardait que la fin, tombait sur le log du
    job « List Images to Scan » — qui avait RÉUSSI — et concluait « échec réseau
    transitoire, relancer ». Le correcteur ne doit voir que les jobs en échec.
    """

    def _jobs_du_run_129(self) -> list[tuple[int, str, str, str]]:
        # Le job réussi est volontairement plus verbeux que le budget entier :
        # s'il fuite dans l'extrait, il évince tout le reste (le bug d'origine).
        extraction_reussie = (
            "Found 33 images\n" + "docker.elastic.co/kibana/kibana:8.17.7\n" * 3000
        )
        echec_trivy = (
            "cassandra:4.1 (debian 11.9)\n"
            "Total: 7 (HIGH: 5, CRITICAL: 2)\n"
            "CVE-2024-45491 libexpat1 FIXED in 2.2.10-2+deb11u6\n"
            "Error: Process completed with exit code 1.\n"
        )
        return [
            (1, "List Images to Scan", "success", extraction_reussie),
            (2, "Scan cassandra:4.1", "failure", echec_trivy),
            (3, "Scan redis:alpine", "success", "Total: 0 (HIGH: 0, CRITICAL: 0)\n"),
        ]

    @pytest.mark.asyncio
    async def test_le_correcteur_ne_voit_que_le_job_en_echec(self):
        actions = _ActionsAvecJobs(self._jobs_du_run_129())
        correcteur = _CorrecteurFake(
            PropositionCorrection(type=TypeCorrection.ABSTENTION, justification="…")
        )
        traitement = _traitement(actions, _WriterFake(), correcteur)

        await traitement.traiter(_message_workflow())

        extrait = correcteur.dernier_extrait
        # La cause réelle est sous les yeux du correcteur…
        assert "CVE-2024-45491" in extrait
        assert "Scan cassandra:4.1" in extrait
        # …et le log du job réussi ne la noie pas.
        assert "Found 33 images" not in extrait
        assert actions.jobs_telecharges == [2]

    @pytest.mark.asyncio
    async def test_repli_sur_le_run_entier_sans_job_en_echec(self):
        # Échec au niveau du run lui-même : aucun job fautif à cibler.
        actions = _ActionsFake(_zip_logs("Error: connection timeout after 30s\n"))
        correcteur = _CorrecteurFake(
            PropositionCorrection(type=TypeCorrection.RELANCE, justification="…")
        )
        actions.lister_jobs_en_echec = _aucun_job
        traitement = _traitement(actions, _WriterFake(), correcteur)

        await traitement.traiter(_message_workflow())

        assert "connection timeout" in correcteur.dernier_extrait


async def _aucun_job(depot, run_id, installation_id) -> list[dict]:
    return []


class TestAbstention:
    """Mirador dit « je ne sais pas » plutôt que d'inventer une cause."""

    def _proposition(self) -> PropositionCorrection:
        return PropositionCorrection(
            type=TypeCorrection.ABSTENTION,
            justification="Le log ne montre aucune erreur : étapes toutes réussies.",
        )

    @pytest.mark.asyncio
    async def test_abstention_escalade_au_lieu_de_relancer(self):
        actions = _ActionsFake(_zip_logs("connection timeout"))
        correcteur = _CorrecteurFake(self._proposition())
        writer = _WriterFake()
        traitement = _traitement(actions, writer, correcteur)

        await traitement.traiter(_message_workflow(head_branch="main"))

        assert actions.relances == [], "une abstention ne doit rien relancer"
        assert len(actions.issues_ouvertes) == 1
        assert "Cause non identifiée" in actions.issues_ouvertes[0]["corps"]
        # La proposition déjà payée est réutilisée : pas de second appel Claude.
        assert correcteur.appels == 1

    @pytest.mark.asyncio
    async def test_approuver_une_abstention_ne_declenche_aucune_action(self):
        actions = _ActionsFake()
        writer = _WriterFake()

        def lire_proposition(anomalie_id):
            return {
                "proposition": {
                    "type": TypeCorrection.ABSTENTION,
                    "justification": "Cause non identifiée.",
                },
                "workflow_run_id": 12345678,
                "head_branch": "main",
            }

        traitement = _traitement(
            actions, writer, _CorrecteurFake(self._proposition()),
            lire_proposition=lire_proposition,
        )

        await traitement.traiter(_message_validation("approuver"))

        assert actions.relances == [], "approuver « je ne sais pas » ne relance rien"
        assert actions.prs == []
        assert "aucune action automatique" in actions.commentaires[0]["corps"]


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
