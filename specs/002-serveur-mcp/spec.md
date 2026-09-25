# Spécification : Serveur MCP en lecture seule pour Mirador

**Branche** : `002-serveur-mcp`

**Créée** : 2026-09-25

**Statut** : Brouillon

**Description** : Permettre à un assistant IA (Claude Code, Claude Desktop ou tout client
compatible Model Context Protocol) d'interroger Mirador en langage naturel : dépôts
surveillés, anomalies détectées, historique d'audit d'une anomalie, intégrité du journal,
et classification déterministe d'un échec fourni par l'utilisateur. Le serveur est
strictement en lecture seule : aucune approbation, aucun rejet, aucune écriture dans le
journal ne passe par lui.

---

## Scénarios utilisateur et tests *(obligatoire)*

### Scénario 1 — Consulter les anomalies d'un dépôt depuis un assistant IA (Priorité : P1)

Un mainteneur travaille dans son assistant IA et demande : « Quelles anomalies Mirador
a-t-il détectées cette semaine sur `aboigues/k8t`, et lesquelles attendent encore une
décision ? ». L'assistant découvre les capacités exposées par Mirador, liste les dépôts
surveillés, puis les anomalies du dépôt filtrées par statut et niveau de risque, et
répond avec les éléments issus du journal.

**Pourquoi cette priorité** : C'est le besoin premier : rendre le journal de Mirador
consultable là où le mainteneur travaille déjà, sans ouvrir l'API ni la base.

**Test indépendant** : Avec un journal de test contenant des anomalies de statuts et de
niveaux variés, un client du protocole liste les capacités, demande les anomalies d'un
dépôt avec un filtre, et obtient exactement les anomalies attendues.

**Scénarios d'acceptation** :

1. **Étant donné** un journal contenant 3 anomalies sur `aboigues/k8t` (une RÉSOLUE, une
   ESCALADÉE, une REJETÉE), **quand** l'assistant demande les anomalies ESCALADÉES de ce
   dépôt, **alors** une seule anomalie est renvoyée avec son identifiant de corrélation,
   son type, son niveau de risque et ses horodatages.
2. **Étant donné** un dépôt configuré mais sans aucun événement, **quand** l'assistant
   demande ses anomalies, **alors** une liste vide est renvoyée, sans erreur.
3. **Étant donné** la configuration des dépôts surveillés, **quand** l'assistant demande
   la liste des dépôts, **alors** chaque dépôt est renvoyé avec son état actif et son
   seuil de timeout, sans aucun secret.

---

### Scénario 2 — Retracer l'historique complet d'une anomalie (Priorité : P1)

Le mainteneur demande : « Qu'a proposé l'agent pour l'échec d'hier, et qui l'a
approuvé ? ». L'assistant récupère la chronologie de l'anomalie : détection, analyse,
escalade, validation ou rejet humain, intervention.

**Pourquoi cette priorité** : La traçabilité est la raison d'être du journal
append-only (Principe III) ; la rendre consultable en langage naturel est la valeur
principale du serveur.

**Test indépendant** : À partir d'un identifiant de corrélation présent dans le journal
de test, la chronologie renvoyée contient tous les événements de cette anomalie, dans
l'ordre, avec l'acteur de chaque événement.

**Scénarios d'acceptation** :

1. **Étant donné** une anomalie passée par DÉTECTION, ESCALADE puis VALIDATION,
   **quand** l'assistant demande son historique, **alors** les 3 événements sont renvoyés
   dans l'ordre chronologique, avec l'acteur (agent ou mainteneur) de chacun.
2. **Étant donné** un identifiant de corrélation mal formé, **quand** l'assistant demande
   l'historique, **alors** une erreur explicite indique que l'identifiant est invalide.
3. **Étant donné** un identifiant bien formé mais inconnu, **quand** l'assistant demande
   l'historique, **alors** une erreur explicite indique qu'aucun événement n'existe.

---

### Scénario 3 — Vérifier l'intégrité du journal d'audit (Priorité : P2)

Avant de s'appuyer sur l'historique (par exemple pour un post-mortem), le mainteneur
demande : « Le journal d'audit est-il intact ? ». Le serveur recalcule l'empreinte du
journal et la compare à l'empreinte enregistrée par le writer lors de la dernière
écriture.

**Pourquoi cette priorité** : Utile pour la confiance et l'audit, mais la consultation
(P1) apporte déjà de la valeur sans elle.

**Test indépendant** : Sur un journal de test et son empreinte, la vérification réussit ;
après altération d'un octet du journal, elle échoue en indiquant les deux empreintes.

**Scénarios d'acceptation** :

1. **Étant donné** un journal et son empreinte cohérente, **quand** l'assistant demande
   la vérification, **alors** le résultat indique « intègre » avec l'empreinte et le
   nombre d'événements.
2. **Étant donné** un journal modifié après écriture, **quand** l'assistant demande la
   vérification, **alors** le résultat indique « altéré » avec l'empreinte attendue et
   l'empreinte calculée.
3. **Étant donné** un journal sans empreinte enregistrée, **quand** l'assistant demande la
   vérification, **alors** le résultat indique « non vérifiable » plutôt qu'un succès.

---

### Scénario 4 — Classer un échec sans l'envoyer à un modèle (Priorité : P3)

Le mainteneur colle un extrait de log d'un échec de CI et demande : « Comment Mirador
classerait-il cet échec ? ». Le serveur applique les règles déterministes de Mirador
(type d'anomalie, niveau de risque, règle de diagnostic reconnue, élévation sur branche
protégée) et renvoie le résultat, sans appel à un modèle de langage et sans rien écrire
dans le journal.

**Pourquoi cette priorité** : Montre et rend testable la logique de décision de Mirador,
mais ce n'est pas un besoin de consultation.

**Test indépendant** : Pour un extrait de log qui correspond à une règle active d'un
dépôt, la classification renvoie la règle et le niveau LOW ; sans correspondance sur
`main`, le niveau est élevé d'un cran.

**Scénarios d'acceptation** :

1. **Étant donné** une règle active dont le motif apparaît dans l'extrait fourni,
   **quand** l'assistant demande la classification, **alors** le niveau est LOW et la
   règle déclenchée est nommée.
2. **Étant donné** un échec sans règle correspondante sur la branche `main`, **quand**
   l'assistant demande la classification, **alors** le niveau est celui du type d'anomalie
   élevé d'un cran.
3. **Étant donné** une conclusion saine (succès ou ignoré), **quand** l'assistant demande
   la classification, **alors** le résultat indique qu'il n'y a pas d'anomalie.

---

### Cas limites

- Le journal n'existe pas encore (aucune écriture) : les consultations renvoient des
  listes vides, la vérification d'intégrité renvoie « non vérifiable ».
- Le stockage du journal est inaccessible (identifiants invalides, réseau) : une erreur
  explicite est renvoyée à l'assistant, sans exposer d'identifiant ni de secret.
- La limite demandée est hors bornes (inférieure à 1 ou supérieure au maximum) : erreur
  explicite, comme pour l'API de consultation existante.
- Un dépôt demandé n'est pas surveillé : erreur explicite qui le dit, plutôt qu'une liste
  vide trompeuse.
- Un extrait de log très volumineux est fourni à la classification : il est tronqué à une
  taille maximale documentée avant l'application des règles.
- Un motif de règle de diagnostic est coûteux à évaluer : la classification applique les
  règles telles que configurées ; la protection contre les motifs abusifs relève de la
  configuration (hors périmètre).

## Exigences *(obligatoire)*

### Exigences fonctionnelles

- **EF-001** : Le système DOIT exposer ses capacités via le Model Context Protocol, de
  sorte qu'un client compatible les découvre et les appelle sans configuration propre à
  Mirador au-delà du lancement du serveur.
- **EF-002** : Le système DOIT permettre de lister les dépôts surveillés avec leur état
  actif et leur seuil de timeout.
- **EF-003** : Le système DOIT permettre de lister les anomalies d'un dépôt, filtrables
  par statut et par niveau de risque, avec une limite bornée, en appliquant exactement les
  mêmes règles de regroupement et de statut que l'API de consultation (EF-010 de la
  spécification 001).
- **EF-004** : Le système DOIT permettre d'obtenir la chronologie complète d'une anomalie
  à partir de son identifiant de corrélation.
- **EF-005** : Le système DOIT permettre de vérifier l'intégrité du journal en comparant
  son empreinte recalculée à l'empreinte enregistrée, avec trois issues distinctes :
  intègre, altéré, non vérifiable.
- **EF-006** : Le système DOIT permettre de classer un échec fourni (conclusion, branche,
  durée facultative, extrait de log facultatif, dépôt) avec les règles déterministes de
  Mirador, sans appel à un modèle de langage.
- **EF-007** : Le système NE DOIT exposer AUCUNE capacité d'écriture : ni approbation, ni
  rejet, ni relance, ni ouverture de pull request, ni écriture dans le journal.
  L'approbation reste un geste humain dans GitHub (Principe III).
- **EF-008** : Le système DOIT valider toutes les entrées reçues de l'assistant
  (identifiants, filtres, limites, tailles d'extrait) et renvoyer des erreurs explicites
  et exploitables par l'assistant.
- **EF-009** : Le système NE DOIT JAMAIS renvoyer de secret (clés, tokens, identifiants de
  stockage) dans ses réponses ni dans ses logs.
- **EF-010** : Le système DOIT journaliser chaque appel reçu (capacité, paramètres
  assainis, durée, issue) avec horodatage ISO 8601 et identifiant de corrélation
  (Principe II), sur la sortie d'erreur pour ne pas perturber le protocole.
- **EF-011** : Le système DOIT pouvoir lire le journal soit depuis le stockage central de
  Mirador avec des identifiants en lecture seule, soit depuis un fichier local (tests,
  démonstration, analyse hors ligne).
- **EF-012** : Les descriptions des capacités exposées DOIVENT être suffisamment précises
  pour qu'un assistant choisisse la bonne capacité et les bons paramètres à partir d'une
  question en langage naturel (voir CS-003).

### Entités clés

- **Dépôt surveillé** : dépôt GitHub suivi par Mirador, avec son état actif, son seuil de
  timeout et ses règles de diagnostic. Issu de la configuration existante.
- **Anomalie (vue de lecture)** : regroupement des événements du journal par identifiant
  de corrélation ; type, niveau de risque, statut inféré du dernier événement, cause
  identifiée, dates de détection et de mise à jour. Aucun stockage propre.
- **Événement du journal** : entrée append-only existante (type, acteur, statut, niveau,
  résultat, horodatage).
- **Rapport d'intégrité** : issue (intègre / altéré / non vérifiable), empreinte attendue,
  empreinte calculée, nombre d'événements.
- **Résultat de classification** : anomalie ou non, type, niveau de risque, règle
  déclenchée éventuelle, élévation appliquée ou non.

## Critères de succès *(obligatoire)*

### Résultats mesurables

- **CS-001** : Un mainteneur connecte le serveur à son assistant et obtient une première
  réponse fondée sur le journal en moins de 5 minutes, en suivant uniquement le guide de
  démarrage.
- **CS-002** : Pour un même journal, les anomalies renvoyées par le serveur et par l'API de
  consultation sont identiques dans 100 % des cas de test.
- **CS-003** : Sur un jeu de 10 questions types en langage naturel, l'assistant choisit la
  bonne capacité avec les bons paramètres dans au moins 9 cas sur 10.
- **CS-004** : Toute altération d'un journal de test est détectée par la vérification
  d'intégrité (100 % des cas de test).
- **CS-005** : Aucune séquence d'appels au serveur ne modifie le journal ni l'état d'un
  dépôt surveillé (vérifié par comparaison d'empreinte avant et après la suite de tests).
- **CS-006** : Une consultation sur un journal de 10 000 événements répond en moins de
  3 secondes.

## Hypothèses

- Les utilisateurs sont les mainteneurs des dépôts surveillés ; le serveur tourne sur leur
  poste, lancé par leur assistant, et n'est pas exposé sur le réseau en v1.
- La configuration des dépôts et des règles est celle déjà utilisée par Mirador ; aucune
  nouvelle source de configuration n'est introduite.
- Le volume reste celui du périmètre POC (1 à 5 dépôts) ; le journal complet peut être
  chargé à chaque consultation.
- Les identifiants de stockage fournis au serveur sont restreints à la lecture ; la
  création de cette clé relève de l'exploitation, documentée dans le guide de démarrage.
- L'évaluation du choix des capacités par un assistant (CS-003) nécessite une clé d'API
  de modèle et s'exécute à la demande, comme les rejeux existants, pas à chaque commit.
- Les rejets, approbations et relances restent exclusivement dans GitHub, via les
  commandes existantes.
