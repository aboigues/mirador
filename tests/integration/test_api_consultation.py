"""Tests d'intégration : API de consultation lecture seule (EF-010).

L'API lit le journal d'audit réel (writer + bucket mémoire) et la config des
dépôts. Les anomalies sont un modèle de lecture dérivé du journal (groupé par
correlation_id), aucun stockage séparé.
"""
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.routes import creer_api
from src.domaine.depot import DepotSurveille
from src.domaine.journal import EvenementJournal, TypeEvenement
from src.infrastructure.stockage.bucket import ClientBucketMemoire
from src.infrastructure.stockage.depots import DepotJournal
from src.infrastructure.stockage.writer import WriterAudit

CLE_BD = "mirador.db"
CORR_A = uuid4()
CORR_B = uuid4()


def _evt(correlation_id, type_evenement, niveau="LOW", depot="aboigues/k8t", statut="SUCCÈS"):
    return EvenementJournal(
        correlation_id=correlation_id,
        type_evenement=type_evenement,
        niveau_risque=niveau,
        depot=depot,
        acteur="mirador-agent",
        statut=statut,
        workflow_run_id=12345678,
        type_anomalie="ÉCHEC",
        resultat="détail",
    )


@pytest.fixture
def client():
    bucket = ClientBucketMemoire()
    writer = WriterAudit(bucket, cle_bd=CLE_BD)
    # Anomalie A : détectée puis résolue ; Anomalie B : escaladée
    writer.ecrire([
        _evt(CORR_A, TypeEvenement.DETECTION),
        _evt(CORR_A, TypeEvenement.INTERVENTION),
        _evt(CORR_B, TypeEvenement.ESCALADE, niveau="HIGH", statut="EN_ATTENTE"),
    ])
    depots = [DepotSurveille(
        identifiant_github="aboigues/k8t", installation_id=1, responsables=["a"],
    )]
    app = creer_api(depots_provider=lambda: depots, journal=DepotJournal(bucket, cle_bd=CLE_BD))
    return TestClient(app, raise_server_exceptions=False)


class TestDepots:
    def test_liste_depots(self, client):
        resp = client.get("/depots")
        assert resp.status_code == 200
        depots = resp.json()["depots"]
        assert depots[0]["identifiant_github"] == "aboigues/k8t"
        assert depots[0]["actif"] is True


class TestAnomalies:
    def test_liste_anomalies_derivees_du_journal(self, client):
        resp = client.get("/depots/aboigues-k8t/anomalies")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        statuts = {a["statut"] for a in data["anomalies"]}
        assert statuts == {"RÉSOLUE", "ESCALADÉE"}

    def test_filtre_par_niveau(self, client):
        resp = client.get("/depots/aboigues-k8t/anomalies", params={"niveau": "HIGH"})
        assert resp.status_code == 200
        assert resp.json()["total"] == 1
        assert resp.json()["anomalies"][0]["niveau_risque"] == "HIGH"

    def test_filtre_par_statut(self, client):
        resp = client.get("/depots/aboigues-k8t/anomalies", params={"statut": "RÉSOLUE"})
        assert resp.json()["total"] == 1

    def test_depot_sans_anomalie(self, client):
        resp = client.get("/depots/autre-depot/anomalies")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_limite_invalide_retourne_400(self, client):
        assert client.get("/depots/aboigues-k8t/anomalies", params={"limite": 999}).status_code == 400
        assert client.get("/depots/aboigues-k8t/anomalies", params={"limite": 0}).status_code == 400


class TestHistorique:
    def test_historique_par_correlation_id(self, client):
        resp = client.get(f"/anomalies/{CORR_A}/historique")
        assert resp.status_code == 200
        data = resp.json()
        assert data["correlation_id"] == str(CORR_A)
        assert len(data["evenements"]) == 2
        assert data["evenements"][0]["type_evenement"] == TypeEvenement.DETECTION

    def test_historique_inconnu_retourne_404(self, client):
        resp = client.get(f"/anomalies/{uuid4()}/historique")
        assert resp.status_code == 404
