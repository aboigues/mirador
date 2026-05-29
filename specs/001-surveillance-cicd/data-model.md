# Modèle de données : Surveillance pipeline CI/CD GitHub Actions

**Date** : 2026-05-29 | **Branche** : `001-surveillance-cicd`

---

## Entités et champs

### DépôtSurveillé

Référence à un dépôt GitHub externe configuré pour être surveillé par Mirador.

| Champ | Type | Contraintes | Description |
|-------|------|-------------|-------------|
| `id` | UUID | PK, NOT NULL | Identifiant interne Mirador |
| `identifiant_github` | string | UNIQUE, NOT NULL | Format `proprietaire/depot` (ex: `aboigues/k8t`) |
| `installation_id` | integer | NOT NULL | ID d'installation de la GitHub App sur ce dépôt |
| `actif` | boolean | NOT NULL, default: true | Activer/désactiver la surveillance |
| `seuil_timeout_secondes` | integer | NOT NULL, default: 3600 | Durée max d'un workflow avant anomalie TIMEOUT |
| `responsables` | string[] | NOT NULL, default: [] | Liste des emails/identifiants à notifier (HIGH/CRITICAL) |
| `regles_ids` | UUID[] | FK vers RègleDiagnostic | Règles de diagnostic appliquées à ce dépôt |
| `cree_le` | datetime | NOT NULL, ISO 8601 | Horodatage de création |
| `modifie_le` | datetime | NOT NULL, ISO 8601 | Horodatage de dernière modification |

**Contraintes de validation** :
- `identifiant_github` : regex `^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$`
- `responsables` : au moins un responsable si `actif = true`
- Maximum 5 entrées simultanément avec `actif = true` (contrainte v1)

---

### Anomalie

Événement de déviation détecté sur un pipeline GitHub Actions.

| Champ | Type | Contraintes | Description |
|-------|------|-------------|-------------|
| `id` | UUID | PK, NOT NULL | Identifiant unique |
| `correlation_id` | UUID | NOT NULL, INDEXED | Corrélation avec les logs et interventions |
| `depot_id` | UUID | FK DépôtSurveillé, NOT NULL | Dépôt concerné |
| `workflow_run_id` | integer | NOT NULL | ID GitHub du run de workflow |
| `workflow_nom` | string | NOT NULL | Nom du workflow GitHub Actions |
| `type` | enum | NOT NULL | `ÉCHEC`, `TIMEOUT`, `RÉGRESSION`, `AUTRE` |
| `niveau_risque` | enum | NOT NULL | `INFO`, `LOW`, `MEDIUM`, `HIGH`, `CRITICAL` |
| `statut` | enum | NOT NULL | voir transitions ci-dessous |
| `cause_identifiee` | string | nullable | Description de la cause si identifiée |
| `regle_declenchee_id` | UUID | FK RègleDiagnostic, nullable | Règle qui a identifié la cause |
| `extrait_log` | text | nullable | Fragment de log pertinent (≤ 2000 chars) |
| `detecte_le` | datetime | NOT NULL, ISO 8601 | Horodatage de détection |
| `mis_a_jour_le` | datetime | NOT NULL, ISO 8601 | Horodatage dernière mise à jour statut |

**Transitions d'état du statut** :

```
OUVERTE → EN_COURS → RÉSOLUE
         ↓              ↑
      ESCALADÉE → (validation humaine) → EN_COURS
         ↓
       REJETÉE
```

- `OUVERTE` : anomalie détectée, aucune action en cours
- `EN_COURS` : intervention en cours d'exécution
- `RÉSOLUE` : correction appliquée avec succès (vérification post-intervention OK)
- `ESCALADÉE` : niveau de risque promu, en attente de validation humaine
- `REJETÉE` : responsable humain a refusé l'intervention proposée

---

### Intervention

Action corrective liée à une anomalie. Immuable après création.

| Champ | Type | Contraintes | Description |
|-------|------|-------------|-------------|
| `id` | UUID | PK, NOT NULL | Identifiant unique |
| `anomalie_id` | UUID | FK Anomalie, NOT NULL | Anomalie traitée |
| `correlation_id` | UUID | NOT NULL | Même UUID que l'anomalie parente |
| `type` | enum | NOT NULL | `RELANCE`, `PULL_REQUEST`, `NOTIFICATION` |
| `acteur` | string | NOT NULL | `mirador-agent` ou `humain:<identifiant>` |
| `statut` | enum | NOT NULL | `EN_ATTENTE`, `SUCCÈS`, `ÉCHEC` |
| `details` | JSON | nullable | Contexte spécifique au type (PR URL, run ID...) |
| `resultat` | text | nullable | Description du résultat observé |
| `cree_le` | datetime | NOT NULL, ISO 8601 | Horodatage de création (immuable) |
| `termine_le` | datetime | nullable, ISO 8601 | Horodatage de fin |

**Règles métier** :
- `type = RELANCE` : relance directe du workflow via API GitHub
- `type = PULL_REQUEST` : PR ouverte sur le dépôt avec correctif proposé ; `details.pr_url` obligatoire
- `type = NOTIFICATION` : alerte envoyée aux responsables ; `details.destinataires` obligatoire
- Une seule Intervention `EN_ATTENTE` par Anomalie à la fois

---

### JournalÉvénement

Entrée immuable d'audit. Aucun UPDATE ni DELETE autorisé (enforced par trigger SQL).

| Champ | Type | Contraintes | Description |
|-------|------|-------------|-------------|
| `id` | integer | PK, autoincrement | Identifiant séquentiel immuable |
| `horodatage` | datetime | NOT NULL, server_default, ISO 8601 | Horodatage de création |
| `correlation_id` | UUID | NOT NULL, INDEXED | Identifiant de corrélation |
| `type_evenement` | enum | NOT NULL | `DÉTECTION`, `INTERVENTION`, `ESCALADE`, `VALIDATION`, `REJET`, `ERREUR_LIVRAISON` |
| `niveau_risque` | enum | NOT NULL | `INFO`, `LOW`, `MEDIUM`, `HIGH`, `CRITICAL` |
| `depot` | string | NOT NULL | Format `proprietaire/depot` |
| `workflow_run_id` | integer | nullable | ID GitHub du run |
| `type_anomalie` | enum | nullable | `ÉCHEC`, `TIMEOUT`, `RÉGRESSION`, `AUTRE` |
| `type_action` | enum | nullable | `RELANCE`, `PULL_REQUEST`, `NOTIFICATION` |
| `acteur` | string | NOT NULL | `mirador-agent` ou `humain:<id>` |
| `statut` | enum | NOT NULL | `SUCCÈS`, `ÉCHEC`, `EN_ATTENTE` |
| `details` | JSON | nullable | Contexte additionnel |
| `resultat` | text | nullable | Description du résultat |

**Immutabilité** : trigger PostgreSQL `BEFORE UPDATE OR DELETE → RAISE EXCEPTION` +
rôle applicatif avec permissions `INSERT, SELECT` uniquement (pas `UPDATE, DELETE`).

---

### RègleDiagnostic

Règle de reconnaissance d'une anomalie, alimentant le moteur de diagnostic hybride.

| Champ | Type | Contraintes | Description |
|-------|------|-------------|-------------|
| `id` | UUID | PK, NOT NULL | Identifiant unique |
| `nom` | string | NOT NULL | Nom court de la règle (ex: "timeout-connexion-bd") |
| `description` | text | NOT NULL | Description lisible de la règle |
| `pattern_log` | string | nullable | Regex ou sous-chaîne à chercher dans les logs |
| `code_sortie` | integer | nullable | Code de sortie du job (0 = succès) |
| `duree_max_secondes` | integer | nullable | Durée max du job avant déclenchement |
| `action_recommandee` | enum | NOT NULL | `RELANCE` ou `PULL_REQUEST` |
| `template_correctif` | text | nullable | Template du correctif pour les PR |
| `origine` | enum | NOT NULL | `MANUELLE` ou `APPRISE` |
| `statut` | enum | NOT NULL | `ACTIVE`, `INACTIVE`, `EN_ATTENTE_VALIDATION` |
| `score_confiance` | float | NOT NULL, 0.0-1.0 | Score basé sur l'historique des succès |
| `utilisations` | integer | NOT NULL, default: 0 | Nombre d'utilisations |
| `succès` | integer | NOT NULL, default: 0 | Nombre de succès confirmés |
| `cree_le` | datetime | NOT NULL, ISO 8601 | |
| `modifie_le` | datetime | NOT NULL, ISO 8601 | |

**Règles métier** :
- `statut = EN_ATTENTE_VALIDATION` : toute règle avec `origine = APPRISE` est inactive
  jusqu'à validation humaine explicite (conforme Principe III)
- `score_confiance = succès / utilisations` (calculé ; 0.0 si jamais utilisée)
- Seules les règles avec `statut = ACTIVE` sont utilisées par le moteur de diagnostic

---

### MessageFile *(entité transitoire, non persistée en base)*

Représentation d'un événement webhook en transit dans la file Scaleway MnQ.

| Champ | Type | Description |
|-------|------|-------------|
| `message_id` | string | ID SQS du message |
| `reception_le` | datetime | Horodatage de réception dans la file |
| `tentatives` | integer | Nombre de tentatives de traitement (max 3 avant DLQ) |
| `statut` | enum | `EN_ATTENTE`, `EN_TRAITEMENT`, `TRAITÉ`, `ERREUR`, `LETTRE_MORTE` |
| `payload` | JSON | Payload brut du webhook GitHub |
| `signature` | string | Header `X-Hub-Signature-256` pour validation HMAC |
| `delivery_id` | string | Header `X-GitHub-Delivery` (UUID de livraison GitHub) |

---

## Relations entre entités

```
DépôtSurveillé ──< Anomalie ──< Intervention
      │                │
      └──< RègleDiagnostic ──< (référencée par)─── Anomalie.regle_declenchee_id
                        │
                 JournalÉvénement (append-only, référence logique via correlation_id)
```

---

## Règles de validation croisées

1. Une `Anomalie` ne peut avoir qu'une `Intervention` avec `statut = EN_ATTENTE` à la fois.
2. Une `Intervention` de `type = NOTIFICATION` est obligatoire avant tout escalade vers
   `statut = ESCALADÉE` sur une `Anomalie`.
3. Une `Intervention` ne peut être créée que si `DépôtSurveillé.actif = true`.
4. Le `niveau_risque` d'une `Anomalie` ne peut qu'augmenter (INFO → LOW → MEDIUM →
   HIGH → CRITICAL) ; la dégradation est interdite.
5. Toute création ou modification d'entité doit produire une entrée dans `JournalÉvénement`.
