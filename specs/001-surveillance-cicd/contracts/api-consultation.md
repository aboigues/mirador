# Contrat : API de consultation (lecture seule)

**Type** : API REST HTTP (lecture seule)
**Émetteur** : Clients (tableau de bord, opérateurs)
**Récepteur** : `api/routes.py`

---

## GET /depots

Liste les dépôts surveillés configurés.

**Réponse 200** :
```json
{
  "depots": [
    {
      "id": "uuid",
      "identifiant_github": "aboigues/k8t",
      "actif": true,
      "seuil_timeout_secondes": 3600
    }
  ]
}
```

---

## GET /depots/{identifiant}/anomalies

Liste les anomalies d'un dépôt (30 derniers jours).

**Paramètres** :
- `identifiant` : format `proprietaire-depot` (ex: `aboigues-k8t`)
- `statut` (optionnel) : filtre par statut (`OUVERTE`, `RÉSOLUE`, etc.)
- `niveau` (optionnel) : filtre par niveau de risque
- `limite` (optionnel, défaut: 50, max: 200)

**Réponse 200** :
```json
{
  "anomalies": [
    {
      "id": "uuid",
      "correlation_id": "uuid",
      "workflow_run_id": 12345678,
      "workflow_nom": "CI",
      "type": "ÉCHEC",
      "niveau_risque": "LOW",
      "statut": "RÉSOLUE",
      "cause_identifiee": "Timeout connexion base de données",
      "detecte_le": "2026-05-29T10:05:03Z",
      "mis_a_jour_le": "2026-05-29T10:05:45Z"
    }
  ],
  "total": 1
}
```

---

## GET /anomalies/{id}/historique

Journal complet des événements pour une anomalie (via `correlation_id`).

**Réponse 200** :
```json
{
  "correlation_id": "uuid",
  "evenements": [
    {
      "id": 1,
      "horodatage": "2026-05-29T10:05:03Z",
      "type_evenement": "DÉTECTION",
      "niveau_risque": "LOW",
      "acteur": "mirador-agent",
      "statut": "SUCCÈS",
      "resultat": "Anomalie ÉCHEC détectée sur workflow CI"
    },
    {
      "id": 2,
      "horodatage": "2026-05-29T10:05:30Z",
      "type_evenement": "INTERVENTION",
      "niveau_risque": "LOW",
      "acteur": "mirador-agent",
      "statut": "SUCCÈS",
      "resultat": "Workflow relancé. Run ID: 12345679"
    }
  ]
}
```

---

## Codes de réponse communs

| Code | Condition |
|------|-----------|
| `200 OK` | Succès |
| `400 Bad Request` | Paramètre invalide |
| `404 Not Found` | Ressource introuvable |
| `500 Internal Server Error` | Erreur serveur (détails en log, pas en réponse) |
