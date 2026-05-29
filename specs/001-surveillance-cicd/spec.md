# Spécification : Surveillance de pipeline CI/CD GitHub Actions

**Branche** : `001-surveillance-cicd`

**Créée** : 2026-05-29

**Statut** : Brouillon

**Description** : Surveillance en temps réel des pipelines GitHub Actions de projets externes,
détection d'anomalies, et intervention corrective automatisée ou guidée selon le niveau de risque.
Exemple de projet surveillé : `aboigues/k8t`.

---

## Clarifications

### Session 2026-05-29

- Q: Mécanisme d'écoute des événements GitHub → A: Webhooks — GitHub envoie les événements vers une file de messages (Scaleway SQS) qui déclenche le démarrage de l'instance serverless Mirador (Scaleway, scale-to-zero).
- Q: Mécanisme d'authentification GitHub → A: GitHub App — permissions granulaires par dépôt, tokens d'installation à durée limitée auto-renouvelés, non liés à un compte personnel, révocables indépendamment.
- Q: Portée des interventions automatiques niveau LOW → A: Relance de workflow + ouverture de PR de correction (pas de commit direct) — permissions `actions:write`, `contents:write`, `pull-requests:write`.
- Q: Définition d'une anomalie à cause connue → A: Hybride — catalogue de règles configurables en v1 (patterns de logs, codes d'erreur), enrichi automatiquement par l'historique des interventions passées réussies.
- Q: Nombre de dépôts surveillés simultanément (v1) → A: 1 à 5 dépôts — périmètre POC/usage personnel.

---

## Scénarios utilisateur et tests *(obligatoire)*

### Scénario 1 — Détection d'une anomalie de pipeline (Priorité : P1)

Un agent Mirador surveille en continu les workflows GitHub Actions d'un dépôt externe
(ex. : `aboigues/k8t`). Lorsqu'un job échoue, dépasse son délai maximum ou produit un
résultat inattendu, l'agent détecte l'anomalie et la classe par niveau de risque.

**Pourquoi cette priorité** : Sans détection, aucune intervention n'est possible. C'est
le cœur du système.

**Test indépendant** : On peut simuler un échec de workflow sur un dépôt de test et vérifier
que Mirador détecte l'anomalie et produit une entrée journalisée en moins de 60 secondes
(incluant le temps de démarrage de l'instance serverless).

**Scénarios d'acceptation** :

1. **Étant donné** un dépôt externe configuré dans Mirador avec un webhook GitHub actif,
   **quand** un job GitHub Actions échoue,
   **alors** GitHub envoie l'événement à la file de messages, l'instance Mirador démarre,
   détecte l'échec, le classe par niveau de risque et enregistre un événement journalisé
   avec horodatage ISO 8601 et identifiant de corrélation — le tout en moins de 60 secondes.

2. **Étant donné** un workflow dont la durée dépasse le seuil configuré,
   **quand** le dépassement est constaté,
   **alors** Mirador génère une anomalie de type TIMEOUT et l'enregistre dans l'historique.

3. **Étant donné** un dépôt non configuré dans Mirador,
   **quand** un événement se produit sur ce dépôt,
   **alors** Mirador l'ignore sans erreur ni journalisation.

4. **Étant donné** un message en échec dans la file (livraison impossible à Mirador),
   **quand** le délai de retry est dépassé,
   **alors** le message est placé en file de lettres mortes et une alerte est émise.

---

### Scénario 2 — Intervention corrective automatique (Priorité : P2)

Lorsqu'une anomalie de niveau LOW ou MEDIUM est détectée, l'agent analyse le contexte
(logs, type d'erreur, historique récent) et tente une correction : relance du workflow,
correction d'un paramètre de configuration trivial, ou ouverture d'une pull request de
correction sur le dépôt surveillé.

**Pourquoi cette priorité** : La valeur principale de Mirador est de corriger automatiquement
les erreurs récurrentes sans intervention humaine pour les cas simples.

**Test indépendant** : On peut injecter un échec connu (ex. : variable d'environnement
manquante) et vérifier que Mirador produit et applique la correction appropriée, puis
enregistre le résultat dans l'historique.

**Scénarios d'acceptation** :

1. **Étant donné** une anomalie de niveau LOW détectée sur `aboigues/k8t`,
   **quand** l'agent analyse les logs et trouve une correspondance dans le catalogue de règles
   ou dans l'historique des interventions passées réussies,
   **alors** l'agent applique la correction associée (relance ou PR), et enregistre l'action
   dans l'historique avec le résultat observé et la règle déclenchée.

2. **Étant donné** une anomalie de niveau MEDIUM,
   **quand** l'agent propose une correction,
   **alors** la correction est soumise à validation de l'agent superviseur avant exécution.

3. **Étant donné** une correction automatique qui échoue à résoudre l'anomalie,
   **quand** le résultat est constaté,
   **alors** l'anomalie est promue au niveau de risque supérieur et une alerte est émise.

---

### Scénario 3 — Escalade vers validation humaine (Priorité : P3)

Pour les anomalies de niveau HIGH ou CRITICAL, l'agent Mirador génère un rapport d'analyse
détaillé, le consigne dans l'historique et notifie les responsables humains désignés.
Aucune action corrective n'est exécutée sans validation humaine explicite.

**Pourquoi cette priorité** : La sécurité opérationnelle exige que les actions à fort impact
restent sous contrôle humain, conformément au Principe III de la constitution Mirador.

**Test indépendant** : On peut déclencher une anomalie critique simulée et vérifier qu'aucune
action automatique n'est prise, qu'un rapport est généré et qu'une notification est émise.

**Scénarios d'acceptation** :

1. **Étant donné** une anomalie de niveau HIGH ou CRITICAL,
   **quand** elle est détectée,
   **alors** aucune intervention automatique n'est exécutée ; un rapport est créé et les
   responsables humains désignés sont notifiés.

2. **Étant donné** une notification envoyée aux responsables,
   **quand** un responsable approuve l'intervention proposée,
   **alors** l'action est exécutée et le résultat enregistré dans l'historique immuable.

3. **Étant donné** un responsable qui rejette l'intervention proposée,
   **quand** le rejet est enregistré,
   **alors** l'anomalie reste ouverte dans l'historique avec le motif du rejet.

---

### Cas limites

- Que se passe-t-il si le dépôt externe devient inaccessible (révocation de token, dépôt privé) ?
- Comment le système gère-t-il plusieurs anomalies simultanées sur le même dépôt ?
- Que se passe-t-il si la correction automatique introduit une régression sur le dépôt surveillé ?
- Comment le système réagit-il si GitHub Actions est momentanément indisponible ?
- Que se passe-t-il si aucun responsable ne valide une anomalie HIGH dans un délai raisonnable ?
- Que se passe-t-il si la file de messages est saturée ou indisponible (panne Scaleway SQS) ?
- Comment le démarrage à froid de l'instance serverless impacte-t-il le délai de détection de 60 secondes ?

---

## Exigences *(obligatoire)*

### Exigences fonctionnelles

- **EF-001** : Le système DOIT permettre de configurer entre 1 et 5 dépôts GitHub externes
  à surveiller simultanément (ajout, suppression, activation/désactivation). Cette limite
  définit le périmètre de la version 1.

- **EF-002** : Le système DOIT s'authentifier auprès de GitHub via une GitHub App installée
  sur chaque dépôt surveillé, en utilisant des tokens d'installation à durée de vie limitée,
  renouvelés automatiquement, sans lien à un compte utilisateur humain.

- **EF-002b** : Le système DOIT recevoir les événements de workflow via des webhooks GitHub
  transitant par une file de messages asynchrone, déclenchant le traitement à la demande
  (architecture événementielle, sans serveur en veille permanente).

- **EF-003** : Le système DOIT détecter les événements de workflow et journaliser l'anomalie
  dans un délai maximum de 60 secondes après l'événement déclencheur, temps de démarrage
  de l'instance de traitement inclus.

- **EF-004** : Le système DOIT classifier chaque anomalie détectée selon cinq niveaux de risque :
  INFO, LOW, MEDIUM, HIGH, CRITICAL — selon des règles de classification configurables par dépôt.

- **EF-005** : Pour les niveaux INFO et LOW, le système DOIT être capable d'exécuter
  automatiquement sans validation humaine, en moins de 30 secondes après la détection,
  l'une des actions suivantes selon l'anomalie identifiée :
  - Relancer le workflow échoué (ré-exécution directe via l'API GitHub).
  - Ouvrir une pull request de correction sur le dépôt surveillé avec le correctif proposé.
  Aucun commit direct sur une branche existante n'est autorisé en intervention automatique.

- **EF-006** : Pour le niveau MEDIUM, le système DOIT proposer une correction et attendre
  la validation de l'agent superviseur avant exécution.

- **EF-007** : Pour les niveaux HIGH et CRITICAL, le système DOIT bloquer toute intervention
  automatique et notifier les responsables humains désignés.

- **EF-008** : Chaque événement, décision et action DOIT être journalisé avec horodatage
  ISO 8601, identifiant de corrélation, niveau de risque, et résultat observé.

- **EF-009** : L'historique des interventions DOIT être conservé de façon immuable (append-only)
  pendant au minimum 30 jours.

- **EF-010** : Le système DOIT exposer l'état courant des pipelines surveillés et l'historique
  des interventions via une interface consultable.

- **EF-011** : En cas d'échec d'une correction automatique, le système DOIT escalader
  l'anomalie au niveau de risque immédiatement supérieur.

- **EF-013** : Le système DOIT maintenir un catalogue de règles de reconnaissance d'anomalies,
  configurable par l'opérateur (ajout, modification, désactivation de règles).

- **EF-014** : Le système DOIT enrichir automatiquement le catalogue en extrayant les patterns
  des interventions passées dont le résultat a été validé comme succès, sous réserve de
  validation humaine avant activation de toute règle apprise automatiquement.

- **EF-012** : Le système DOIT gérer les échecs de livraison de messages (file de lettres
  mortes) et émettre une alerte en cas de messages non traités.

### Entités clés

- **DépôtSurveillé** : Référence à un dépôt GitHub externe ; identifiant unique, statut
  actif/inactif, configuration des seuils d'anomalie, liste des responsables désignés,
  référence au webhook configuré, identifiant d'installation GitHub App associé.

- **Anomalie** : Événement de déviation détecté sur un pipeline ; niveau de risque, type
  (ÉCHEC, TIMEOUT, RÉGRESSION, AUTRE), horodatage, identifiant de corrélation, statut
  (ouverte, en cours, résolue, escaladée, rejetée).

- **Intervention** : Action corrective liée à une anomalie ; type (RELANCE, PULL_REQUEST,
  NOTIFICATION), résultat (succès, échec, en attente), acteur (agent automatique ou humain),
  horodatage. Note : les interventions RELANCE et PULL_REQUEST sont automatiques (niveaux
  INFO/LOW) ; NOTIFICATION est utilisée pour les niveaux MEDIUM/HIGH/CRITICAL.

- **JournalÉvénement** : Entrée immuable enregistrant tout événement du système ; ne peut
  être ni modifiée ni supprimée.

- **MessageFile** : Représentation d'un événement webhook en transit dans la file de messages ;
  statut (en attente, traité, en erreur, lettre morte), horodatage de réception, nombre de
  tentatives.

- **RègleDiagnostic** : Règle de reconnaissance d'une anomalie ; pattern de détection (expression
  sur logs/codes d'erreur/durée), action corrective associée (RELANCE ou PULL_REQUEST), origine
  (manuelle ou apprise), statut (active, inactive, en attente de validation), score de confiance
  issu de l'historique.

---

## Critères de succès *(obligatoire)*

### Résultats mesurables

- **CS-001** : 100 % des échecs de workflow des dépôts configurés sont détectés dans les
  60 secondes suivant l'événement GitHub, démarrage serverless inclus.

- **CS-002** : Au moins 70 % des anomalies de niveau LOW sont résolues automatiquement
  sans intervention humaine.

- **CS-003** : Aucune intervention sur anomalie HIGH ou CRITICAL n'est exécutée sans
  validation humaine explicite (zéro exception tolérée).

- **CS-004** : L'historique de toutes les interventions est consultable et non altérable,
  couvrant au minimum les 30 derniers jours.

- **CS-005** : Le temps d'exécution d'une intervention automatique LOW est inférieur à
  30 secondes après la détection.

- **CS-006** : Le taux de faux positifs (anomalies signalées à tort) ne dépasse pas 5 %
  sur une fenêtre glissante de 7 jours.

- **CS-007** : Aucun message webhook n'est perdu définitivement sans alerte émise ; le taux
  de messages en lettre morte détectés et alertés est de 100 %.

---

## Hypothèses

- Les dépôts externes à surveiller (ex. : `aboigues/k8t`) autorisent l'installation de la
  GitHub App Mirador, ce qui configure automatiquement le webhook et accorde les permissions
  nécessaires (lecture des workflows/logs, écriture si intervention activée).
- Les équipes opérationnelles disposent d'au moins un responsable humain désigné par dépôt
  pour valider les interventions HIGH/CRITICAL.
- Le temps de démarrage à froid de l'instance serverless est compatible avec le délai de
  détection de 60 secondes (hypothèse : démarrage ≤ 10 secondes).
- La file de messages est configurée avec un mécanisme de retry et une file de lettres mortes
  pour garantir qu'aucun événement n'est perdu silencieusement.
- Les règles de classification des anomalies (seuils, patterns) sont configurables par
  dépôt et disposent de valeurs par défaut raisonnables à l'initialisation.
- L'accès en écriture au dépôt surveillé (relance de workflow, création de PR) est accordé
  explicitement et révocable à tout moment par l'opérateur.
- La portée initiale couvre uniquement GitHub Actions ; d'autres plateformes CI/CD
  (GitLab CI, Jenkins) sont hors périmètre pour cette version.
- La version 1 est dimensionnée pour 1 à 5 dépôts simultanés ; la montée en charge
  au-delà est hors périmètre et fera l'objet d'une version ultérieure.
- L'infrastructure de réception des webhooks et de la file de messages est hébergée sur
  Scaleway ; les pannes Scaleway sont hors périmètre de résilience pour cette version.
