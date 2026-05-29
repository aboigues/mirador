# Plan d'implémentation : Surveillance pipeline CI/CD GitHub Actions

**Branche** : `001-surveillance-cicd` | **Date** : 2026-05-29 | **Spec** : [spec.md](spec.md)

**Entrée** : Spécification fonctionnelle depuis `specs/001-surveillance-cicd/spec.md`

---

## Résumé

Mirador surveille les workflows GitHub Actions de dépôts externes (1 à 5 en v1) via
des webhooks GitHub acheminés par une file de messages Scaleway MnQ vers une fonction
serverless Python 3.12 scale-to-zero. L'agent IA analyse les anomalies détectées,
les classe par niveau de risque (INFO→CRITICAL) et applique des corrections
automatiques (RELANCE ou PULL_REQUEST) pour les niveaux bas, avec escalade vers
validation humaine pour les niveaux HIGH/CRITICAL. Tout est journalisé de façon
immuable (PostgreSQL append-only, 30 jours).

---

## Contexte technique

**Langage/Version** : Python 3.12 (Scaleway Serverless Functions)

**Dépendances principales** :
- `pydantic 2.13.4` — validation des modèles de données
- `httpx 0.28.1` — appels HTTP asynchrones vers GitHub API
- `PyGitHub 2.9.1` — client GitHub App (relance workflow, création PR)
- `PyJWT 2.x` — génération JWT RSA-256 pour authentification GitHub App
- `boto3 1.43.17` — client Scaleway MnQ (SQS-compatible)
- `anthropic 0.105.2` — SDK agent IA Claude (analyse logs, génération correctifs)
- `structlog 25.5.0` — journalisation structurée JSON avec corrélation UUID
- `SQLAlchemy 2.x` — ORM PostgreSQL (modèles + triggers append-only)
- `FastAPI 0.115.x` — récepteur de webhooks + API de consultation (EF-010)

**Stockage** : PostgreSQL managé Scaleway (via PgBouncer pour pooling serverless)

**Tests** : pytest 9.0.3 + pytest-asyncio + testcontainers (PostgreSQL + localstack SQS)

**Plateforme cible** : Scaleway Serverless Functions, Python 3.12, Linux

**Type de projet** : Service cloud événementiel (serverless + API REST)

**Objectifs de performance** :
- Détection d'anomalie : ≤ 60s après événement GitHub (démarrage à froid inclus)
- Intervention automatique LOW : ≤ 30s après détection
- Cold start : ≤ 10s (hypothèse budgétaire)

**Contraintes** :
- Stateless entre invocations (serverless scale-to-zero)
- 1 à 5 dépôts surveillés simultanément (périmètre v1)
- Secrets (GITHUB_APP_PRIVATE_KEY, WEBHOOK_SECRET, DB_URL) en variables d'env chiffrées

**Périmètre/Échelle** : 1-5 dépôts, POC/usage personnel, GitHub Actions uniquement

---

## Vérification constitutionnelle

*PORTE : Obligatoire avant l'implémentation. Revérifiée post-conception.*

- [x] **Principe I — Sécurité par conception** : Validation HMAC-SHA256 (`hmac.compare_digest`)
  avant tout parsing ; GitHub App JWT RSA-256 ; secrets en variables d'env chiffrées ;
  principe du moindre privilège (permissions GitHub App minimales par niveau d'intervention).

- [x] **Principe II — Observabilité** : `structlog` JSON avec `correlation_id` UUID,
  horodatage ISO 8601 sur chaque événement ; métriques DLQ monitorées ; table
  `audit_evenements` couvrant 30 jours ; endpoint `/metrics` prévu (EF-010).

- [x] **Principe III — Intervention contrôlée** : 5 niveaux de risque
  (INFO/LOW/MEDIUM/HIGH/CRITICAL) ; RELANCE et PULL_REQUEST uniquement (pas de commit
  direct) ; validation humaine obligatoire pour HIGH/CRITICAL ; historique append-only
  immuable (trigger PostgreSQL BEFORE UPDATE/DELETE → EXCEPTION).

- [x] **Principe IV — TDD** : Tests écrits avant implémentation ; couche HMAC et JWT
  testées sans mock (testcontainers) ; PostgreSQL réelle pour tester les triggers
  d'immutabilité ; cycle rouge-vert obligatoire.

- [x] **Principe V — Simplicité** : Architecture à 4 couches (gestionnaires, agents,
  domaine, infrastructure) ; 1-5 dépôts en v1 ; aucune abstraction anticipée ;
  tout en français.

**Résultat** : ✅ Aucune violation — toutes les portes franchies.

---

## Structure du projet

### Documentation (cette fonctionnalité)

```text
specs/001-surveillance-cicd/
├── plan.md              # Ce fichier
├── research.md          # Recherche technique
├── data-model.md        # Modèle de données
├── quickstart.md        # Guide de démarrage rapide
├── contracts/           # Contrats d'interface
│   ├── webhook-entrant.md
│   ├── api-consultation.md
│   ├── api-intervention.md
│   └── message-file.md
└── tasks.md             # Généré par /speckit-tasks
```

### Code source (racine du dépôt)

```text
src/
├── gestionnaires/           # Points d'entrée serverless
│   ├── webhook.py           # Réception + validation HMAC webhook GitHub
│   └── traitement.py        # Traitement des messages de la file
├── agents/                  # Logique des agents IA
│   ├── detecteur.py         # Détection et classification des anomalies
│   ├── correcteur.py        # Analyse des causes et génération de correctifs
│   └── superviseur.py       # Orchestration et escalade
├── domaine/                 # Modèles métier (entités de la spec)
│   ├── anomalie.py          # Entité Anomalie + états + transitions
│   ├── depot.py             # Entité DépôtSurveillé
│   ├── intervention.py      # Entité Intervention
│   ├── regle.py             # Entité RègleDiagnostic
│   └── journal.py           # Entité JournalÉvénement
├── infrastructure/          # Adaptateurs externes
│   ├── github/
│   │   ├── app_auth.py      # GitHub App JWT + tokens d'installation
│   │   └── actions_client.py # Actions API (relance, logs, PR)
│   ├── file/
│   │   └── sqs_client.py    # Scaleway MnQ (SQS boto3) + DLQ
│   └── stockage/
│       ├── modeles_bd.py    # Modèles SQLAlchemy + triggers append-only
│       └── depots.py        # Accès données (lecture + insertion uniquement)
└── api/
    └── routes.py            # API consultation état/historique (EF-010)

tests/
├── integration/             # Tests réels (testcontainers)
│   ├── test_webhook_hmac.py        # Validation signature réelle
│   ├── test_detection_anomalie.py  # Détection + journalisation
│   ├── test_intervention_low.py    # Relance + PR automatiques
│   ├── test_escalade.py            # Escalade MEDIUM → validation
│   └── test_audit_immuable.py      # Triggers append-only PostgreSQL
└── unitaire/
    ├── test_classification.py  # Règles de classification des anomalies
    ├── test_regles.py          # Matching catalogue de règles
    └── test_escalade_logique.py # Logique d'escalade sans I/O

pyproject.toml               # Dépendances + configuration pytest
```

**Décision de structure** : Architecture en couches unique (Option 1 adaptée) avec
séparation nette entre agents (logique IA), domaine (modèles métier), infrastructure
(I/O externes), gestionnaires (points d'entrée serverless). La couche domaine ne
dépend d'aucune infrastructure.

---

## Suivi de complexité

> Aucune violation constitutionnelle justifiant ce tableau.

---

## Artéfacts générés (Phase 1)

- [x] `research.md` — Stack technique validée, versions confirmées
- [x] `data-model.md` — Entités, champs, transitions d'état, règles de validation
- [x] `contracts/webhook-entrant.md` — Format webhook GitHub → Mirador
- [x] `contracts/api-consultation.md` — API REST lecture état/historique
- [x] `contracts/api-intervention.md` — API approbation/rejet MEDIUM/HIGH
- [x] `contracts/message-file.md` — Format message interne SQS
- [x] `quickstart.md` — Guide d'installation et de test local
- [x] `CLAUDE.md` — Contexte agent mis à jour
