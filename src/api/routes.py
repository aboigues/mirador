"""API de consultation lecture seule (EF-010).

Expose l'état des dépôts surveillés et l'historique d'audit. Les anomalies ne
sont pas stockées séparément : elles sont un modèle de lecture dérivé du journal
append-only, groupé par correlation_id, avec un statut inféré du dernier
événement enregistré.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

import structlog
from fastapi import FastAPI, HTTPException, Query

from src.domaine.journal import TypeEvenement

log = structlog.get_logger(__name__)

_LIMITE_DEFAUT = 50
_LIMITE_MAX = 200

# Statut d'anomalie inféré du type du dernier événement journalisé
_STATUT_PAR_EVENEMENT = {
    TypeEvenement.DETECTION: "OUVERTE",
    TypeEvenement.INTERVENTION: "RÉSOLUE",
    TypeEvenement.ESCALADE: "ESCALADÉE",
    TypeEvenement.VALIDATION: "EN_COURS",
    TypeEvenement.REJET: "REJETÉE",
    TypeEvenement.ERREUR_LIVRAISON: "OUVERTE",
}


def _identifiant_vers_depot(identifiant: str) -> str:
    """`aboigues-k8t` → `aboigues/k8t` (premier tiret = séparateur)."""
    return identifiant.replace("-", "/", 1)


def _grouper_anomalies(evenements: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
            "statut": _STATUT_PAR_EVENEMENT.get(dernier["type_evenement"], "OUVERTE"),
            "cause_identifiee": dernier.get("resultat"),
            "detecte_le": premier.get("horodatage"),
            "mis_a_jour_le": dernier.get("horodatage"),
        })
    return anomalies


def creer_api(
    *,
    depots_provider: Callable[[], list[Any]],
    journal: Any,
) -> FastAPI:
    app = FastAPI(title="Mirador — API de consultation")

    @app.get("/depots")
    def lister_depots() -> dict[str, Any]:
        return {"depots": [
            {
                "id": str(d.id),
                "identifiant_github": d.identifiant_github,
                "actif": d.actif,
                "seuil_timeout_secondes": d.seuil_timeout_secondes,
            }
            for d in depots_provider()
        ]}

    @app.get("/depots/{identifiant}/anomalies")
    def lister_anomalies(
        identifiant: str,
        statut: Optional[str] = None,
        niveau: Optional[str] = None,
        limite: int = Query(_LIMITE_DEFAUT),
    ) -> dict[str, Any]:
        if not 1 <= limite <= _LIMITE_MAX:
            raise HTTPException(status_code=400, detail="limite hors bornes (1-200)")

        depot = _identifiant_vers_depot(identifiant)
        evenements = [e for e in journal.lister() if e.get("depot") == depot]
        anomalies = _grouper_anomalies(evenements)

        if statut is not None:
            anomalies = [a for a in anomalies if a["statut"] == statut]
        if niveau is not None:
            anomalies = [a for a in anomalies if a["niveau_risque"] == niveau]

        anomalies = anomalies[:limite]
        return {"anomalies": anomalies, "total": len(anomalies)}

    @app.get("/anomalies/{correlation_id}/historique")
    def historique(correlation_id: str) -> dict[str, Any]:
        from uuid import UUID
        try:
            cid = UUID(correlation_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="correlation_id invalide")

        evenements = journal.lister(correlation_id=cid)
        if not evenements:
            raise HTTPException(status_code=404, detail="Aucun événement pour cette anomalie")

        return {
            "correlation_id": correlation_id,
            "evenements": [
                {
                    "id": e["id"],
                    "horodatage": e["horodatage"],
                    "type_evenement": e["type_evenement"],
                    "niveau_risque": e["niveau_risque"],
                    "acteur": e["acteur"],
                    "statut": e["statut"],
                    "resultat": e.get("resultat"),
                }
                for e in evenements
            ],
        }

    return app
