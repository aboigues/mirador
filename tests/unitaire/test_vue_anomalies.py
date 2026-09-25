"""Tests unitaires : vue de lecture des anomalies, partagée par l'API et le serveur MCP."""
from src.domaine.journal import TypeEvenement
from src.domaine.vue_anomalies import STATUT_PAR_EVENEMENT, grouper_anomalies


def _evt(correlation_id, type_evenement, horodatage, niveau="LOW", resultat=None):
    return {
        "correlation_id": correlation_id,
        "type_evenement": type_evenement,
        "horodatage": horodatage,
        "niveau_risque": niveau,
        "workflow_run_id": 42,
        "type_anomalie": "ÉCHEC",
        "resultat": resultat,
    }


def test_regroupe_par_correlation_et_infere_le_statut_du_dernier_evenement():
    evenements = [
        _evt("a", TypeEvenement.DETECTION, "2026-09-01T10:00:00"),
        _evt("b", TypeEvenement.ESCALADE, "2026-09-01T11:00:00", niveau="HIGH"),
        _evt("a", TypeEvenement.INTERVENTION, "2026-09-01T12:00:00", resultat="relance"),
    ]

    anomalies = grouper_anomalies(evenements)

    assert [a["correlation_id"] for a in anomalies] == ["a", "b"]
    a, b = anomalies
    assert a["statut"] == "RÉSOLUE"
    assert a["detecte_le"] == "2026-09-01T10:00:00"
    assert a["mis_a_jour_le"] == "2026-09-01T12:00:00"
    assert a["cause_identifiee"] == "relance"
    assert b["statut"] == "ESCALADÉE"
    assert b["niveau_risque"] == "HIGH"


def test_table_des_statuts_couvre_tous_les_types_d_evenement():
    types = {v for k, v in vars(TypeEvenement).items() if not k.startswith("_")}
    assert set(STATUT_PAR_EVENEMENT) == types


def test_journal_vide_donne_aucune_anomalie():
    assert grouper_anomalies([]) == []
