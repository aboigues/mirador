"""Vue de lecture « anomalie » dérivée du journal append-only.

Les anomalies ne sont pas stockées séparément : elles sont reconstruites en
groupant les événements par correlation_id, avec un statut inféré du dernier
événement enregistré. Partagée par l'API de consultation et le serveur MCP,
pour que les deux renvoient exactement la même chose.
"""
from __future__ import annotations

from typing import Any

from src.domaine.journal import TypeEvenement

STATUT_PAR_EVENEMENT = {
    TypeEvenement.DETECTION: "OUVERTE",
    TypeEvenement.INTERVENTION: "RÉSOLUE",
    TypeEvenement.ESCALADE: "ESCALADÉE",
    TypeEvenement.VALIDATION: "EN_COURS",
    TypeEvenement.REJET: "REJETÉE",
    TypeEvenement.ERREUR_LIVRAISON: "OUVERTE",
}


def grouper_anomalies(evenements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reconstruit une vue anomalie par correlation_id à partir du journal ordonné."""
    par_correlation: dict[str, list[dict[str, Any]]] = {}
    for evt in evenements:
        par_correlation.setdefault(evt["correlation_id"], []).append(evt)

    anomalies = []
    for correlation_id, groupe in par_correlation.items():
        premier, dernier = groupe[0], groupe[-1]
        anomalies.append({
            "correlation_id": correlation_id,
            "workflow_run_id": premier.get("workflow_run_id"),
            "type": premier.get("type_anomalie"),
            "niveau_risque": dernier.get("niveau_risque"),
            "statut": STATUT_PAR_EVENEMENT.get(dernier["type_evenement"], "OUVERTE"),
            "cause_identifiee": dernier.get("resultat"),
            "detecte_le": premier.get("horodatage"),
            "mis_a_jour_le": dernier.get("horodatage"),
        })
    return anomalies
