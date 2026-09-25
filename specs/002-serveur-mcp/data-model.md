# Modèle de données : Serveur MCP en lecture seule

**Branche** : `002-serveur-mcp` | **Date** : 2026-09-25

Aucune nouvelle table, aucune écriture. Les entités ci-dessous sont des **modèles de
sortie** (Pydantic, dans `src/interface_mcp/modeles.py`) calculés à partir du journal
existant (`journal_evenements`) et de la configuration `MIRADOR_DEPOTS`. Leurs schémas
JSON sont publiés par le serveur (`output_schema`).

---

## DepotResume

Sous-ensemble non sensible de `DepotSurveille`.

| Champ | Type | Source |
|---|---|---|
| `identifiant_github` | str (`proprietaire/depot`) | config |
| `actif` | bool | config |
| `seuil_timeout_secondes` | int | config |
| `nombre_regles` | int | nombre de règles configurées pour le dépôt |

Exclus volontairement : `installation_id`, `responsables`, `id` interne.

## AnomalieVue

Vue de lecture produite par `grouper_anomalies` (identique à l'API de consultation).

| Champ | Type | Règle |
|---|---|---|
| `correlation_id` | str (UUID) | clé de regroupement |
| `workflow_run_id` | int \| null | premier événement |
| `type` | str \| null | `type_anomalie` du premier événement |
| `niveau_risque` | str | dernier événement |
| `statut` | str | inféré du type du dernier événement (table ci-dessous) |
| `cause_identifiee` | str \| null | `resultat` du dernier événement |
| `detecte_le` | str (ISO 8601) | premier événement |
| `mis_a_jour_le` | str (ISO 8601) | dernier événement |

**Statut inféré** (inchangé) : DÉTECTION → OUVERTE ; INTERVENTION → RÉSOLUE ;
ESCALADE → ESCALADÉE ; VALIDATION → EN_COURS ; REJET → REJETÉE ;
ERREUR_LIVRAISON → OUVERTE.

## ListeAnomalies

| Champ | Type |
|---|---|
| `depot` | str |
| `anomalies` | list[AnomalieVue] |
| `total` | int (nombre renvoyé après filtres et limite) |

## EvenementVue et HistoriqueAnomalie

`EvenementVue` : `id`, `horodatage`, `type_evenement`, `niveau_risque`, `acteur`,
`statut`, `resultat` (mêmes champs que `/anomalies/{id}/historique`).

`HistoriqueAnomalie` : `correlation_id`, `depot`, `evenements: list[EvenementVue]`
(ordre chronologique, par `id` croissant).

Exclus : `details` (peut contenir des extraits de logs volumineux) et `delivery_id`.

## RapportIntegrite

| Champ | Type | Règle |
|---|---|---|
| `issue` | `INTÈGRE` \| `ALTÉRÉ` \| `NON_VÉRIFIABLE` | voir transitions |
| `empreinte_attendue` | str \| null | contenu de `mirador.db.sha256` |
| `empreinte_calculee` | str \| null | SHA-256 des octets de `mirador.db` |
| `nombre_evenements` | int \| null | renseigné si `INTÈGRE` |
| `motif` | str \| null | explication si `NON_VÉRIFIABLE` |

- Journal absent → `NON_VÉRIFIABLE` (motif : aucun journal).
- Empreinte absente → `NON_VÉRIFIABLE` (motif : aucune empreinte enregistrée).
- Empreintes égales → `INTÈGRE`.
- Empreintes différentes → `ALTÉRÉ`.

## EntreeClassification (arguments de `classer_echec`)

| Champ | Type | Validation |
|---|---|---|
| `depot` | str | doit être un dépôt configuré |
| `conclusion` | str | valeur GitHub : `failure`, `timed_out`, `cancelled`, `success`, `skipped`, ... |
| `branche` | str | non vide |
| `duree_secondes` | int \| null | >= 0 |
| `extrait_log` | str \| null | tronqué aux 20 000 derniers caractères |

## ResultatClassification

| Champ | Type |
|---|---|
| `anomalie` | bool |
| `type` | str \| null (`ÉCHEC`, `TIMEOUT`, `AUTRE`) |
| `niveau_risque` | str \| null (`INFO` à `CRITICAL`) |
| `regle_declenchee` | str \| null (nom de la règle) |
| `cause_identifiee` | str \| null (description de la règle) |
| `eleve_branche_protegee` | bool |
| `extrait_tronque` | bool |
