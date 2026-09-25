"""Modèles de sortie des outils MCP (voir specs/002-serveur-mcp/data-model.md).

Leurs schémas JSON sont publiés par le serveur : l'assistant sait exactement ce
que chaque outil renvoie.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

from src.infrastructure.stockage.integrite import RapportIntegrite  # noqa: F401  (réexporté)

Statut = Literal["OUVERTE", "EN_COURS", "ESCALADÉE", "RÉSOLUE", "REJETÉE"]
Niveau = Literal["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"]


class DepotResume(BaseModel):
    identifiant_github: str
    actif: bool
    seuil_timeout_secondes: int
    nombre_regles: int


class ListeDepots(BaseModel):
    depots: list[DepotResume]


class AnomalieVue(BaseModel):
    correlation_id: str
    workflow_run_id: Optional[int]
    type: Optional[str]
    niveau_risque: Optional[str]
    statut: str
    cause_identifiee: Optional[str]
    detecte_le: Optional[str]
    mis_a_jour_le: Optional[str]


class ListeAnomalies(BaseModel):
    depot: str
    anomalies: list[AnomalieVue]
    total: int


class EvenementVue(BaseModel):
    id: int
    horodatage: str
    type_evenement: str
    niveau_risque: str
    acteur: str
    statut: str
    resultat: Optional[str]


class HistoriqueAnomalie(BaseModel):
    correlation_id: str
    depot: str
    evenements: list[EvenementVue]


class ResultatClassification(BaseModel):
    anomalie: bool
    type: Optional[str] = None
    niveau_risque: Optional[str] = None
    regle_declenchee: Optional[str] = None
    cause_identifiee: Optional[str] = None
    eleve_branche_protegee: bool = False
    extrait_tronque: bool = False
