# Contrat : Validation humaine via GitHub Issue

**Type** : GitHub Issue + commentaires de commande
**Émetteur** : Mirador (ouverture de l'issue) ↔ Responsable humain (commentaires)
**Récepteur** : Mirador via webhook `issue_comment`

---

## Flux de validation

```
Anomalie HIGH/CRITICAL détectée
        ↓
Mirador ouvre une GitHub Issue sur le dépôt surveillé
        ↓
Responsable reçoit la notification GitHub habituelle
        ↓
Responsable poste /approuver ou /rejeter <motif>
        ↓
GitHub envoie un webhook issue_comment → Mirador
        ↓
Mirador exécute ou rejette l'intervention + ferme l'issue
```

---

## Format de la GitHub Issue ouverte par Mirador

**Titre** :
```
[Mirador] Anomalie {NIVEAU} — {workflow_nom} sur {depot}
```

**Labels** : `mirador`, `{niveau_en_minuscules}` (ex : `high`, `critical`)

**Corps** :
```markdown
## Anomalie détectée

| Champ | Valeur |
|-------|--------|
| Dépôt | aboigues/k8t |
| Workflow | CI |
| Niveau de risque | HIGH |
| Détectée le | 2026-05-29T10:05:00Z |
| Run GitHub | [#12345678](https://github.com/aboigues/k8t/actions/runs/12345678) |

## Cause identifiée

Timeout de connexion à la base de données détecté dans les logs (règle : `timeout-connexion-bd`).

## Extrait de log

```
Error: Connection timeout after 30s — postgresql://...
```

## Intervention proposée

**RELANCE** du workflow `CI` (run #12345678).

---

Pour valider ou rejeter cette intervention, postez l'une des commandes suivantes :

- ✅ `/approuver` — déclenche l'intervention immédiatement
- ❌ `/rejeter <motif>` — rejette et clôt l'anomalie (motif obligatoire)

> ⚠️ Seuls les responsables désignés peuvent valider : @aboigues

<!-- mirador:anomalie_id:550e8400-e29b-41d4-a716-446655440000 -->
```

---

## Commandes de commentaire

### `/approuver`

Déclenche l'intervention proposée.

**Conditions** :
- L'auteur du commentaire doit être dans `DépôtSurveillé.responsables`
- L'anomalie doit être en statut `ESCALADÉE`

**Effet** :
1. Anomalie passe en `EN_COURS`
2. Intervention exécutée (RELANCE ou PULL_REQUEST)
3. Mirador poste un commentaire de confirmation sur l'issue
4. Issue fermée avec label `resolved`

**Commentaire de confirmation de Mirador** :
```
✅ Intervention approuvée par @aboigues — relance du workflow en cours.
Résultat : [consulter l'historique](/etat)
```

---

### `/rejeter <motif>`

Rejette l'intervention et clôt l'anomalie.

**Conditions** :
- L'auteur doit être dans `DépôtSurveillé.responsables`
- Le motif doit faire au moins 10 caractères

**Exemple** :
```
/rejeter Le correctif proposé cible le mauvais service, intervention manuelle en cours
```

**Effet** :
1. Anomalie passe en `REJETÉE`
2. Motif enregistré dans `JournalÉvénement`
3. Mirador poste un commentaire d'accusé de réception
4. Issue fermée avec label `rejected`

**Commentaire de Mirador** :
```
❌ Intervention rejetée par @aboigues.
Motif : Le correctif proposé cible le mauvais service, intervention manuelle en cours
```

---

## Règles de sécurité

- L'`anomalie_id` est encodé en commentaire HTML invisible dans le corps de l'issue
  (`<!-- mirador:anomalie_id:UUID -->`) — non modifiable par un tiers sans droits d'édition.
- Seuls les comptes listés dans `DépôtSurveillé.responsables` peuvent émettre des commandes ;
  tout autre commentaire est ignoré silencieusement (pas de message d'erreur public).
- La validation HMAC du webhook `issue_comment` est identique à celle de `workflow_run`.
- Un commentaire de commande mal formé ou d'un non-responsable est ignoré sans effet.
