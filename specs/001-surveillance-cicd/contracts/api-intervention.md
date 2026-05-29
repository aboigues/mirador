# Contrat : API d'intervention (validation humaine)

**Type** : API REST HTTP (écriture, validation humaine MEDIUM/HIGH/CRITICAL)
**Émetteur** : Responsable humain (notification reçue)
**Récepteur** : `api/routes.py`

---

## POST /anomalies/{id}/approuver

Approuver et déclencher l'intervention proposée sur une anomalie MEDIUM/HIGH/CRITICAL.

**Corps de la requête** :
```json
{
  "acteur": "alice@exemple.com",
  "commentaire": "Approuvé — correctif semble valide"
}
```

**Réponse 202** :
```json
{
  "anomalie_id": "uuid",
  "statut": "EN_COURS",
  "intervention_id": "uuid",
  "message": "Intervention approuvée et en cours d'exécution"
}
```

**Conditions d'erreur** :
- `409 Conflict` : anomalie déjà résolue ou déjà en cours
- `403 Forbidden` : `acteur` non dans la liste des responsables du dépôt
- `404 Not Found` : anomalie introuvable

---

## POST /anomalies/{id}/rejeter

Rejeter l'intervention proposée et clore l'anomalie avec statut REJETÉE.

**Corps de la requête** :
```json
{
  "acteur": "alice@exemple.com",
  "motif": "Le correctif proposé modifie un fichier sensible non lié à l'erreur"
}
```

**Réponse 200** :
```json
{
  "anomalie_id": "uuid",
  "statut": "REJETÉE",
  "message": "Intervention rejetée et enregistrée dans l'historique"
}
```

---

## Règles de sécurité

- L'`acteur` DOIT être dans `DépôtSurveillé.responsables` pour le dépôt concerné.
- Toute approbation ou rejet est enregistré dans `JournalÉvénement` avec l'identifiant
  de l'acteur et l'horodatage.
- Un `motif` est obligatoire pour les rejets (au moins 10 caractères).
