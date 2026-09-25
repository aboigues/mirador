"""Tests d'intégration : serveur MCP interrogé par le vrai client du SDK (en mémoire).

Vérifie le contrat (specs/002-serveur-mcp/contracts/outils-mcp.md) : liste exacte
des outils, annotations de lecture seule, schémas publiés, erreurs explicites, et
équivalence avec l'API de consultation (CS-002).
"""
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from mcp.client import Client

from src.api.routes import creer_api
from src.domaine.depot import DepotSurveille
from src.domaine.journal import EvenementJournal, TypeEvenement
from src.gestionnaires.entree import ConfigDepots
from src.infrastructure.stockage.bucket import ClientBucketMemoire
from src.infrastructure.stockage.depots import DepotJournal
from src.infrastructure.stockage.writer import WriterAudit
from src.interface_mcp.serveur import creer_serveur

CLE_BD = "mirador.db"
K8T = "aboigues/k8t"
OUTILS_ATTENDUS = {
    "lister_depots",
    "lister_anomalies",
    "historique_anomalie",
    "verifier_integrite_journal",
    "classer_echec",
}


def _evt(correlation_id, type_evenement, niveau="LOW", depot=K8T):
    return EvenementJournal(
        correlation_id=correlation_id,
        type_evenement=type_evenement,
        niveau_risque=niveau,
        depot=depot,
        acteur="mirador-agent",
        statut="SUCCÈS",
        workflow_run_id=777,
        type_anomalie="ÉCHEC",
    )


@pytest.fixture
def contexte():
    bucket = ClientBucketMemoire()
    evenements = []
    for i in range(12):
        corr = uuid4()
        evenements.append(_evt(corr, TypeEvenement.DETECTION, niveau=["LOW", "HIGH", "MEDIUM"][i % 3]))
        if i % 2:
            evenements.append(_evt(corr, TypeEvenement.ESCALADE, niveau="HIGH"))
    evenements.append(_evt(uuid4(), TypeEvenement.DETECTION, depot="aboigues/autre"))
    WriterAudit(bucket, cle_bd=CLE_BD).ecrire(evenements)
    config = ConfigDepots([
        (DepotSurveille(identifiant_github=K8T, installation_id=1, responsables=["a"]), []),
        (DepotSurveille(identifiant_github="aboigues/autre", installation_id=2, responsables=["b"]), []),
    ])
    return config, bucket


@pytest.fixture
def serveur(contexte):
    config, bucket = contexte
    return creer_serveur(config, bucket, cle_bd=CLE_BD)


async def test_liste_exacte_des_outils_en_lecture_seule(serveur):
    async with Client(serveur) as client:
        outils = (await client.list_tools()).tools

    assert {o.name for o in outils} == OUTILS_ATTENDUS
    for outil in outils:
        assert outil.annotations.read_only_hint is True, outil.name
        assert outil.annotations.destructive_hint is False, outil.name
        assert outil.description, outil.name
        assert outil.output_schema, outil.name


async def test_bornes_de_la_limite_publiees_dans_le_schema(serveur):
    async with Client(serveur) as client:
        outils = {o.name: o for o in (await client.list_tools()).tools}

    limite = outils["lister_anomalies"].input_schema["properties"]["limite"]
    assert limite["minimum"] == 1 and limite["maximum"] == 200


async def test_appel_renvoie_une_sortie_structuree(serveur):
    async with Client(serveur) as client:
        resultat = await client.call_tool("lister_depots", {})

    assert resultat.is_error is False
    assert [d["identifiant_github"] for d in resultat.structured_content["depots"]] \
        == [K8T, "aboigues/autre"]


async def test_erreur_explicite_transmise_a_l_assistant(serveur):
    async with Client(serveur) as client:
        resultat = await client.call_tool("historique_anomalie", {"correlation_id": "pas-un-uuid"})

    assert resultat.is_error is True
    assert "invalide" in resultat.content[0].text


async def test_argument_hors_schema_rejete(serveur):
    async with Client(serveur) as client:
        resultat = await client.call_tool("lister_anomalies", {"depot": K8T, "statut": "INCONNU"})

    assert resultat.is_error is True


@pytest.mark.parametrize("filtres", [
    {},
    {"statut": "ESCALADÉE"},
    {"niveau_risque": "HIGH"},
    {"statut": "OUVERTE", "niveau_risque": "LOW"},
    {"limite": 3},
])
async def test_memes_anomalies_que_l_api_de_consultation(contexte, serveur, filtres):
    config, bucket = contexte
    api = TestClient(creer_api(depots_provider=config.depots, journal=DepotJournal(bucket, cle_bd=CLE_BD)))
    parametres = {"statut": filtres.get("statut"), "niveau": filtres.get("niveau_risque"),
                  "limite": filtres.get("limite", 50)}
    attendu = api.get("/depots/aboigues-k8t/anomalies",
                      params={k: v for k, v in parametres.items() if v is not None}).json()

    async with Client(serveur) as client:
        resultat = await client.call_tool("lister_anomalies", {"depot": K8T, **filtres})

    assert resultat.structured_content["anomalies"] == attendu["anomalies"]
    assert resultat.structured_content["total"] == attendu["total"]
