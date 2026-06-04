# Recherche technique : Surveillance pipeline CI/CD GitHub Actions

**Date** : 2026-05-29 | **Branche** : `001-surveillance-cicd`

---

## 1. Stack Python et versions stables (2026-05-29)

**Décision** : Python 3.12 sur Scaleway Serverless Functions.

**Rationale** : Python 3.12 est la version de production recommandée pour Scaleway
Serverless Functions (support confirmé). Python 3.13 est disponible mais l'écosystème
MLOps n'est pas encore pleinement stabilisé sur cette version. Python est le standard
de fait pour les projets MLOps et les agents IA.

**Alternatives considérées** : TypeScript/Node.js (bon pour serverless mais bibliothèques
IA moins matures) ; Go (performance maximale mais surcoût développement pour un POC).

**Versions retenues** (vérifiées via PyPI 2026-05-29) :

| Package | Version | Rôle |
|---------|---------|------|
| pydantic | 2.13.4 | Validation des modèles de données |
| httpx | 0.28.1 | Appels HTTP asynchrones (GitHub API) |
| PyGitHub | 2.9.1 | Client GitHub App (actions, PR, runs) |
| PyJWT | 2.x | Génération JWT pour GitHub App |
| boto3 | 1.43.17 | Client Scaleway MnQ (SQS-compatible) |
| anthropic | 0.105.2 | SDK agents IA Claude |
| structlog | 25.5.0 | Journalisation structurée JSON |
| aiosqlite | 0.20.x | SQLite async (writer) |
| FastAPI | 0.115.x | Récepteur de webhooks / API consultation |
| pytest | 9.0.3 | Tests unitaires et d'intégration |
| pytest-asyncio | 0.24.x | Support async dans les tests |

---

## 2. Architecture d'écoute : Webhooks GitHub → Scaleway MnQ → Serverless

**Décision** : Webhook GitHub → Scaleway Message Queue (SQS-compatible) → Fonction
serverless Mirador (scale-to-zero).

**Rationale** : Ce pattern événementiel garantit qu'aucun compute n'est consommé en
l'absence d'événements (coût minimal), tout en assurant la durabilité des messages
(SQS stocke l'événement même si l'instance n'est pas démarrée). Le démarrage à froid
estimé à ≤ 10 secondes reste compatible avec le délai de détection de 60 secondes.

**Alternatives considérées** : Polling périodique (plus simple mais latence ≥ 60s non
garantie et coût compute continu) ; webhooks directs sans file (risque de perte si
l'instance ne répond pas au moment de la livraison).

**Configuration SQS retenue** :
- Queue principale : `mirador-webhooks`, VisibilityTimeout = 300s, RetentionPeriod = 86400s
- Dead-letter queue : `mirador-webhooks-dlq`, RedrivePolicy maxReceiveCount = 3
- Long polling : WaitTimeSeconds = 20 (réduit la latence de réception)
- Endpoint boto3 : `https://mq-sqs.scaleway.com`

---

## 3. Authentification GitHub App

**Décision** : GitHub App avec JWT RSA-256 + tokens d'installation à durée limitée,
gérés par la classe `GitHubAppAuth` (cache + rotation automatique 30s avant expiry).

**Rationale** : Une GitHub App offre des permissions granulaires par dépôt, des tokens
non liés à un compte personnel, et une révocabilité indépendante par installation.
C'est le standard recommandé par GitHub pour les outils automatisés.

**Alternatives considérées** : Personal Access Token (lié à un humain, non révocable
par dépôt) ; OAuth App (requiert une interaction humaine pour le flux de consentement,
inadapté aux agents autonomes).

**Permissions minimales requises par niveau d'intervention** :
- Surveillance seule (lecture) : `actions: read`, `metadata: read`, `checks: read`
- Relance de workflow : + `actions: write`
- Ouverture de PR de correction : + `contents: write`, `pull-requests: write`

**Secrets** : `GITHUB_APP_ID` et `GITHUB_APP_PRIVATE_KEY` stockés en variables
d'environnement Scaleway Serverless (chiffrées au repos).

---

## 4. Validation HMAC des webhooks GitHub

**Décision** : Validation avec `hmac.compare_digest()` (résistance aux timing attacks)
avant tout parsing ou traitement du payload.

**Rationale** : La comparaison directe de chaînes est vulnérable aux timing attacks.
`hmac.compare_digest()` garantit un temps de comparaison constant O(n), conformément
aux recommandations GitHub (header `X-Hub-Signature-256`, algo SHA-256).

**Pattern retenu** :
1. Extraire `X-Hub-Signature-256` du header
2. Calculer HMAC-SHA256 du body brut avec le secret webhook
3. Comparer via `hmac.compare_digest()` → rejeter avec HTTP 403 si invalide
4. Parser le JSON uniquement après validation réussie

---

## 5. Journalisation immuable (audit append-only)

**Décision** : Immutabilité enforced au niveau applicatif via un writer unique :
seules des instructions `INSERT` sont émises ; aucun `UPDATE` ni `DELETE` n'existe
dans le code du writer. Le mode WAL de SQLite garantit la cohérence en cas de crash.

**Rationale** : Le single-writer sérialisé par la queue `mirador-writes` remplace le
trigger PostgreSQL. Pour un POC à 1-5 dépôts avec un volume d'événements faible, cette
garantie applicative est suffisante et supprime la dépendance à une base de données
managée. Le risque de contournement accidentel (connexion directe hors writer) est
inexistant dans ce contexte d'usage personnel.

**Alternatives considérées** : Trigger PostgreSQL `BEFORE UPDATE/DELETE → RAISE EXCEPTION`
(plus robuste, mais coût ~€11/mois pour un POC) ; event sourcing complet (surcoût
architectural injustifié à cette échelle).

---

## 6. Stockage principal

**Décision** : SQLite (mode WAL) persisté dans un bucket Scaleway Object Storage,
avec sérialisation des écritures via une queue dédiée `mirador-writes`.

**Architecture** :
- Les fonctions serverless (webhook, traitement) n'écrivent jamais directement en base.
  Elles enqueued des messages dans `mirador-writes`.
- Un writer unique consomme `mirador-writes` : il télécharge le fichier SQLite depuis
  le bucket, exécute les `INSERT` en batch, puis réupload le fichier avec vérification
  de checksum (SHA-256) avant de valider les messages.
- SQLite en mode WAL : les lectures peuvent se faire en parallèle des écritures ; en
  cas de crash avant l'upload, les messages restent dans la queue et sont retraités.

**Configuration des queues** :
- `mirador-webhooks` (existante) : réception des événements GitHub
- `mirador-writes` (nouvelle) : sérialisation des écritures SQLite, VisibilityTimeout = 60s
- `mirador-writes-dlq` : lettres mortes après 3 tentatives

**Coût** : Scaleway Object Storage ~€0,02/GB/mois — pratiquement gratuit pour un fichier
SQLite de quelques MB (30 jours d'audit, 1-5 dépôts).

**Rationale** : La concurrence multi-writer est le seul vrai problème de SQLite en
contexte serverless. La queue `mirador-writes` le résout sans infrastructure managée.
La latence d'écriture additionnelle (download + upload bucket, ~100-300ms) est
acceptable pour un journal d'audit non critique au chemin de traitement principal.

**Alternatives considérées** : PostgreSQL managé Scaleway DB-DEV-S (~€11/mois, justifié
pour de la production) ; Neon serverless PostgreSQL (gratuit, mais hors écosystème
Scaleway) ; Litestream (réplication WAL continue vers S3, complexité supérieure).

---

## 7. Agent IA de diagnostic

**Décision** : Agent IA basé sur Claude (SDK `anthropic 0.105.2`) pour l'analyse
des logs et la génération de correctifs. L'agent utilise les logs GitHub Actions
comme contexte et le catalogue de règles comme référence.

**Rationale** : Claude Sonnet offre un excellent ratio performance/coût pour l'analyse
de logs et la génération de code de correction. Le SDK Anthropic supporte le prompt
caching, ce qui réduit les coûts pour les patterns d'erreur récurrents.

**Alternatives considérées** : Règles pures sans IA (insuffisant pour les cas non
prévus dans le catalogue) ; GPT-4 (moins intégré dans l'écosystème Mirador).

---

## 8. Tests d'intégration (conformité Principe IV)

**Décision** : Tests d'intégration avec FastAPI TestClient + SQLite en mémoire (`:memory:`)
+ Scaleway MnQ simulé (localstack). Aucun mock de la couche sécurité (HMAC, JWT).

**Rationale** : Conforme au Principe IV de la constitution : les tests de la couche
sécurité utilisent des interfaces réelles. SQLite en mémoire est identique à SQLite sur
fichier pour les tests fonctionnels — la différence (upload bucket) est testée séparément
via localstack S3. Plus rapide et sans Docker pour les tests unitaires.

**Packages additionnels tests** :
- `localstack` (via Docker) : SQS + S3 compatibles pour les tests d'intégration
- `pytest-asyncio` : tests des handlers async
- `respx` : mock réseau uniquement pour l'API GitHub externe (acceptable car ce n'est
  pas une couche de sécurité interne)
