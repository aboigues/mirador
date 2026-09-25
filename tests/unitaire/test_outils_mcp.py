"""Tests unitaires : logique des outils MCP, sans le protocole.

Journaux produits par le vrai WriterAudit sur un bucket mémoire ; les erreurs
attendues doivent être des ToolError au message explicite (EF-008).
"""
from uuid import uuid4

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from src.domaine.depot import DepotSurveille
from src.domaine.journal import EvenementJournal, TypeEvenement
from src.domaine.regle import ActionRecommandee, OrigineRegle, RegleDiagnostic
from src.gestionnaires.entree import ConfigDepots
from src.infrastructure.stockage.bucket import ClientBucketMemoire
from src.infrastructure.stockage.writer import WriterAudit
from src.interface_mcp.outils import TAILLE_MAX_EXTRAIT, OutilsMirador

CLE_BD = "mirador.db"
K8T = "aboigues/k8t"
VIDE = "aboigues/vide"
CORR_RESOLUE, CORR_ESCALADEE, CORR_REJETEE = uuid4(), uuid4(), uuid4()

REGLE_RESEAU = RegleDiagnostic(
    nom="reseau-transitoire",
    description="Coupure réseau pendant le téléchargement des dépendances",
    pattern_log=r"ECONNRESET",
    action_recommandee=ActionRecommandee.RELANCE,
    origine=OrigineRegle.MANUELLE,
)


def _evt(correlation_id, type_evenement, niveau="LOW", acteur="mirador-agent", statut="SUCCÈS",
         resultat=None, depot=K8T):
    return EvenementJournal(
        correlation_id=correlation_id,
        type_evenement=type_evenement,
        niveau_risque=niveau,
        depot=depot,
        acteur=acteur,
        statut=statut,
        workflow_run_id=1234,
        type_anomalie="ÉCHEC",
        resultat=resultat,
        details={"extrait": "log volumineux"},
        delivery_id="livraison-1",
    )


def _config():
    return ConfigDepots([
        (DepotSurveille(identifiant_github=K8T, installation_id=99, responsables=["alice"]),
         [REGLE_RESEAU]),
        (DepotSurveille(identifiant_github=VIDE, installation_id=98, responsables=["bob"]), []),
    ])


@pytest.fixture
def bucket():
    bucket = ClientBucketMemoire()
    WriterAudit(bucket, cle_bd=CLE_BD).ecrire([
        _evt(CORR_RESOLUE, TypeEvenement.DETECTION),
        _evt(CORR_ESCALADEE, TypeEvenement.DETECTION, niveau="HIGH"),
        _evt(CORR_RESOLUE, TypeEvenement.INTERVENTION, resultat="relance effectuée"),
        _evt(CORR_ESCALADEE, TypeEvenement.ESCALADE, niveau="HIGH", statut="EN_ATTENTE"),
        _evt(CORR_REJETEE, TypeEvenement.DETECTION, niveau="MEDIUM"),
        _evt(CORR_REJETEE, TypeEvenement.REJET, niveau="MEDIUM", acteur="alice", statut="REJETÉE"),
    ])
    return bucket


@pytest.fixture
def outils(bucket):
    return OutilsMirador(_config(), bucket, cle_bd=CLE_BD)


# --- US1 : lister_depots / lister_anomalies --------------------------------

def test_lister_depots_sans_donnees_sensibles(outils):
    resultat = outils.lister_depots()

    assert [d.identifiant_github for d in resultat.depots] == [K8T, VIDE]
    k8t = resultat.depots[0]
    assert k8t.actif is True and k8t.seuil_timeout_secondes == 3600 and k8t.nombre_regles == 1
    brut = resultat.model_dump_json()
    assert "installation_id" not in brut and "alice" not in brut


def test_lister_anomalies_sans_filtre(outils):
    resultat = outils.lister_anomalies(K8T)

    assert resultat.total == 3
    statuts = {a.correlation_id: a.statut for a in resultat.anomalies}
    assert statuts == {
        str(CORR_RESOLUE): "RÉSOLUE",
        str(CORR_ESCALADEE): "ESCALADÉE",
        str(CORR_REJETEE): "REJETÉE",
    }


def test_lister_anomalies_filtre_par_statut_et_niveau(outils):
    assert [a.correlation_id for a in outils.lister_anomalies(K8T, statut="ESCALADÉE").anomalies] \
        == [str(CORR_ESCALADEE)]
    assert [a.correlation_id for a in outils.lister_anomalies(K8T, niveau_risque="MEDIUM").anomalies] \
        == [str(CORR_REJETEE)]
    assert outils.lister_anomalies(K8T, statut="RÉSOLUE", niveau_risque="HIGH").total == 0


def test_lister_anomalies_limite(outils):
    assert outils.lister_anomalies(K8T, limite=2).total == 2


@pytest.mark.parametrize("limite", [0, 201])
def test_lister_anomalies_limite_hors_bornes(outils, limite):
    with pytest.raises(ToolError, match="limite"):
        outils.lister_anomalies(K8T, limite=limite)


def test_lister_anomalies_depot_non_surveille(outils):
    with pytest.raises(ToolError, match="aboigues/inconnu"):
        outils.lister_anomalies("aboigues/inconnu")


def test_lister_anomalies_depot_sans_evenement(outils):
    resultat = outils.lister_anomalies(VIDE)
    assert resultat.anomalies == [] and resultat.total == 0


def test_journal_inaccessible_donne_un_message_neutre(bucket):
    class BucketEnPanne:
        def telecharger(self, cle):
            raise RuntimeError("AccessDenied: cle SCWXXXXXXXXXXXXXXXXX bucket secret-bucket")

        def televerser(self, cle, donnees):
            raise AssertionError("aucune écriture attendue")

    outils = OutilsMirador(_config(), BucketEnPanne(), cle_bd=CLE_BD)

    with pytest.raises(ToolError) as erreur:
        outils.lister_anomalies(K8T)
    message = str(erreur.value)
    assert "inaccessible" in message
    assert "SCW" not in message and "secret-bucket" not in message


# --- US2 : historique_anomalie ---------------------------------------------

def test_historique_dans_l_ordre_avec_les_acteurs(outils):
    resultat = outils.historique_anomalie(str(CORR_REJETEE))

    assert resultat.depot == K8T
    assert [e.type_evenement for e in resultat.evenements] == ["DÉTECTION", "REJET"]
    assert [e.acteur for e in resultat.evenements] == ["mirador-agent", "alice"]
    brut = resultat.model_dump_json()
    assert "log volumineux" not in brut and "livraison-1" not in brut


def test_historique_identifiant_invalide(outils):
    with pytest.raises(ToolError, match="invalide"):
        outils.historique_anomalie("pas-un-uuid")


def test_historique_identifiant_inconnu(outils):
    with pytest.raises(ToolError, match="Aucun événement"):
        outils.historique_anomalie(str(uuid4()))


# --- US3 : verifier_integrite_journal --------------------------------------

def test_verifier_integrite_journal(outils):
    rapport = outils.verifier_integrite_journal()
    assert rapport.issue == "INTÈGRE" and rapport.nombre_evenements == 6


# --- US4 : classer_echec ---------------------------------------------------

def test_classer_echec_regle_reconnue(outils):
    resultat = outils.classer_echec(K8T, conclusion="failure", branche="main",
                                    extrait_log="npm ERR! network ECONNRESET")

    assert resultat.anomalie is True
    assert resultat.niveau_risque == "LOW"
    assert resultat.regle_declenchee == "reseau-transitoire"
    assert resultat.cause_identifiee == REGLE_RESEAU.description
    assert resultat.eleve_branche_protegee is False


def test_classer_echec_sans_regle_sur_main_est_eleve(outils):
    sur_branche = outils.classer_echec(K8T, conclusion="failure", branche="feature/x")
    sur_main = outils.classer_echec(K8T, conclusion="failure", branche="main")

    assert sur_branche.type == "ÉCHEC" and sur_branche.niveau_risque == "LOW"
    assert sur_main.niveau_risque == "MEDIUM"
    assert sur_main.eleve_branche_protegee is True
    assert sur_main.regle_declenchee is None


def test_classer_echec_duree_au_dela_du_seuil_est_un_timeout(outils):
    resultat = outils.classer_echec(K8T, conclusion="failure", branche="dev", duree_secondes=7200)
    assert resultat.type == "TIMEOUT"


def test_classer_echec_conclusion_saine(outils):
    resultat = outils.classer_echec(K8T, conclusion="success", branche="main")
    assert resultat.anomalie is False and resultat.niveau_risque is None


def test_classer_echec_tronque_en_gardant_la_fin(outils):
    extrait = "x" * (TAILLE_MAX_EXTRAIT + 10_000) + " ECONNRESET"

    resultat = outils.classer_echec(K8T, conclusion="failure", branche="dev", extrait_log=extrait)

    assert resultat.extrait_tronque is True
    assert resultat.regle_declenchee == "reseau-transitoire"


@pytest.mark.parametrize("arguments, motif", [
    ({"branche": "", "duree_secondes": None}, "branche"),
    ({"branche": "main", "duree_secondes": -1}, "durée"),
])
def test_classer_echec_entrees_invalides(outils, arguments, motif):
    with pytest.raises(ToolError, match=motif):
        outils.classer_echec(K8T, conclusion="failure", **arguments)


def test_classer_echec_depot_non_surveille(outils):
    with pytest.raises(ToolError, match="aboigues/inconnu"):
        outils.classer_echec("aboigues/inconnu", conclusion="failure", branche="main")


def test_classer_echec_n_ecrit_rien(outils, bucket):
    avant = bucket.telecharger(CLE_BD)
    outils.classer_echec(K8T, conclusion="failure", branche="main", extrait_log="ECONNRESET")
    assert bucket.telecharger(CLE_BD) == avant
