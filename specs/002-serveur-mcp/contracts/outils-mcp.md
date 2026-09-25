# Contrat : outils MCP de Mirador

**Transport** : stdio. **Nom du serveur** : `mirador`. **Outils** : exactement les cinq
ci-dessous, tous annotés `readOnlyHint=true`, `destructiveHint=false`,
`idempotentHint=true`, `openWorldHint=false`.

Les erreurs attendues sont renvoyées en `is_error=true` avec un message en français
explicite. Les arguments hors schéma sont rejetés par le SDK avant exécution.

---

## `lister_depots`

**Description (pour l'assistant)** : Liste les dépôts GitHub surveillés par Mirador, avec
leur état actif et leur seuil de timeout. À appeler d'abord quand l'utilisateur ne
précise pas le dépôt.

**Entrée** : aucune.
**Sortie** : `{ "depots": [DepotResume] }`.

---

## `lister_anomalies`

**Description** : Liste les anomalies CI/CD détectées par Mirador sur un dépôt,
reconstituées depuis le journal d'audit. Filtres facultatifs par statut et par niveau de
risque.

**Entrée** :

| Paramètre | Type | Défaut | Contraintes |
|---|---|---|---|
| `depot` | str | requis | `proprietaire/depot`, dépôt configuré |
| `statut` | enum \| null | null | `OUVERTE`, `EN_COURS`, `ESCALADÉE`, `RÉSOLUE`, `REJETÉE` |
| `niveau_risque` | enum \| null | null | `INFO`, `LOW`, `MEDIUM`, `HIGH`, `CRITICAL` |
| `limite` | int | 50 | 1 à 200 |

**Sortie** : `ListeAnomalies`.

**Erreurs** : dépôt non surveillé ; limite hors bornes ; journal inaccessible.

---

## `historique_anomalie`

**Description** : Donne la chronologie complète d'une anomalie (détection, analyse,
escalade, validation ou rejet humain, intervention) à partir de son identifiant de
corrélation, tel que renvoyé par `lister_anomalies`.

**Entrée** : `correlation_id` (str, UUID).
**Sortie** : `HistoriqueAnomalie`.

**Erreurs** : identifiant invalide ; aucun événement pour cet identifiant ; journal
inaccessible.

---

## `verifier_integrite_journal`

**Description** : Vérifie que le journal d'audit n'a pas été modifié depuis sa dernière
écriture par Mirador, en comparant son empreinte SHA-256 recalculée à l'empreinte
enregistrée.

**Entrée** : aucune.
**Sortie** : `RapportIntegrite`.

**Erreurs** : journal inaccessible. (Journal ou empreinte absents : pas une erreur,
issue `NON_VÉRIFIABLE`.)

---

## `classer_echec`

**Description** : Classe un échec de workflow GitHub Actions fourni par l'utilisateur avec
les règles déterministes de Mirador (type, niveau de risque, règle de diagnostic
reconnue, élévation sur branche protégée). N'appelle aucun modèle de langage et
n'enregistre rien.

**Entrée** : `EntreeClassification` (voir data-model.md).
**Sortie** : `ResultatClassification`.

**Erreurs** : dépôt non surveillé ; durée négative ; branche vide.

---

## Absences délibérées

Aucun outil pour approuver, rejeter, relancer un workflow, ouvrir une pull request ou
écrire dans le journal. Un test d'intégration échoue si un outil non listé ici apparaît.
