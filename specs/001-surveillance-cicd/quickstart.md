# Guide de démarrage rapide : Mirador Surveillance CI/CD

**Date** : 2026-05-29 | **Branche** : `001-surveillance-cicd`

---

## Prérequis

- Python 3.12
- Docker (pour PostgreSQL et localstack en test local)
- Compte Scaleway (Serverless Functions + MnQ + Managed PostgreSQL)
- GitHub App créée dans votre organisation/compte GitHub

## Installation locale

```bash
# Cloner et installer
git clone <url-mirador>
cd mirador
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Variables d'environnement requises

```bash
# GitHub App
export GITHUB_APP_ID="12345"
export GITHUB_APP_PRIVATE_KEY="$(cat chemin/vers/cle-privee.pem)"
export GITHUB_WEBHOOK_SECRET="votre-secret-webhook"

# Base de données
export DATABASE_URL="postgresql://user:password@localhost:5432/mirador"

# Scaleway MnQ
export SCALEWAY_ACCESS_KEY="votre-cle-acces"
export SCALEWAY_SECRET_KEY="votre-cle-secrete"
export SCALEWAY_REGION="fr-par"
export SQS_QUEUE_URL="https://mq-sqs.scaleway.com/..."
export SQS_DLQ_URL="https://mq-sqs.scaleway.com/...-dlq"
```

## Démarrage en local

```bash
# Démarrer PostgreSQL et localstack (SQS simulé)
docker compose up -d

# Initialiser la base de données (tables + triggers)
python -m mirador.infrastructure.stockage.init_bd

# Démarrer le serveur de webhooks
uvicorn mirador.api.app:app --reload --port 8000

# Dans un second terminal : démarrer le consommateur de file
python -m mirador.gestionnaires.traitement
```

## Tester la détection d'une anomalie

```bash
# Simuler un webhook d'échec de workflow
python scripts/simuler_webhook.py \
  --depot aboigues/k8t \
  --run-id 12345 \
  --conclusion failure \
  --secret "$GITHUB_WEBHOOK_SECRET"

# Vérifier la détection
curl http://localhost:8000/depots/aboigues-k8t/anomalies
```

## Lancer les tests

```bash
# Tests unitaires (pas besoin de Docker)
pytest tests/unitaire/ -v

# Tests d'intégration (nécessite Docker)
pytest tests/integration/ -v --timeout=90

# Tous les tests avec couverture
pytest --cov=src --cov-report=term-missing
```

## Validation du guide (checklist)

- [ ] Serveur démarre sans erreur
- [ ] Webhook simulé reçu et message enqueué
- [ ] Anomalie détectée et journalisée en moins de 60s
- [ ] Entrée dans `JournalÉvénement` avec horodatage ISO 8601 et `correlation_id`
- [ ] Tests d'intégration passent (vert)
- [ ] Tentative de modification de `JournalÉvénement` lève une exception PostgreSQL

## Configuration d'un dépôt à surveiller

1. Installer la GitHub App Mirador sur le dépôt cible (ex: `aboigues/k8t`)
2. Noter l'`installation_id` affiché après installation
3. Configurer le dépôt via l'API :

```bash
curl -X POST http://localhost:8000/depots \
  -H "Content-Type: application/json" \
  -d '{
    "identifiant_github": "aboigues/k8t",
    "installation_id": 98765,
    "responsables": ["alice@exemple.com"],
    "seuil_timeout_secondes": 1800
  }'
```
