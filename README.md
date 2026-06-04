# Mirador

Surveillance autonome de pipelines CI/CD GitHub Actions avec agents IA.

Mirador écoute les événements GitHub Actions, détecte les anomalies, les classe par niveau de risque et applique des corrections automatiques — ou escalade vers validation humaine pour les cas critiques.

---

## Architecture

```mermaid
flowchart TD
    GH["GitHub Actions\n(dépôts surveillés)"]
    WH["POST /webhooks/github\nvalidation HMAC-SHA256"]
    MNQ["Scaleway MnQ\nfile mirador-webhooks"]
    DLQ["Dead-Letter Queue\n(3 tentatives max)"]
    TRAIT["gestionnaires/traitement.py\nConsommateur SQS"]

    subgraph Agents["Agents IA"]
        DET["Détecteur\nclassification + niveau de risque"]
        CORR["Correcteur\nanalyse cause · Claude SDK"]
        SUP["Superviseur\norchestrateur · décision escalade"]
    end

    subgraph Actions["Interventions"]
        RELANCE["RELANCE\nrestart workflow GitHub"]
        PR["PULL_REQUEST\ncorrectif proposé"]
        NOTIF["NOTIFICATION\nalerte responsables"]
    end

    PG[("PostgreSQL\naudit append-only\n30 jours")]
    API["GET /etat · /historique\nAPI consultation"]
    HUMAIN["Validation humaine\nPOST /interventions/:id/approuver"]

    GH -->|"webhook workflow_run"| WH
    WH -->|"202 — message enqueué"| MNQ
    MNQ -->|"échec × 3"| DLQ
    MNQ --> TRAIT
    TRAIT --> DET
    DET --> SUP
    SUP -->|"INFO"| PG
    SUP -->|"LOW"| CORR
    SUP -->|"MEDIUM"| NOTIF
    SUP -->|"HIGH / CRITICAL"| HUMAIN
    CORR --> RELANCE
    CORR --> PR
    RELANCE --> PG
    PR --> PG
    NOTIF --> PG
    HUMAIN -->|"approuvé"| CORR
    HUMAIN -->|"rejeté"| PG
    PG --> API
```

### Niveaux de risque et actions

| Niveau | Déclencheur | Action automatique |
|--------|-------------|-------------------|
| `INFO` | Événement normal | Journalisation uniquement |
| `LOW` | Échec isolé, pattern connu | Relance automatique |
| `MEDIUM` | Échec répété ou inattendu | Notification + attente |
| `HIGH` | Régression ou impact étendu | Validation humaine requise |
| `CRITICAL` | Incident de production | Validation humaine requise |

---

## Stack technique

- **Runtime** : Python 3.12 — Scaleway Serverless Functions (scale-to-zero)
- **File de messages** : Scaleway MnQ (SQS-compatible, boto3)
- **Stockage** : PostgreSQL managé Scaleway — audit append-only enforced par trigger SQL
- **Auth GitHub** : GitHub App — JWT RSA-256, tokens d'installation à durée limitée
- **Agent IA** : Claude SDK (`anthropic`) — analyse logs, génération correctifs
- **API** : FastAPI — webhooks entrants + consultation état/historique
- **Observabilité** : `structlog` JSON + `correlation_id` UUID sur chaque événement

---

## Lancement local

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest tests/ -v
```

---

## Périmètre v1

1 à 5 dépôts GitHub surveillés simultanément. Interventions limitées à la relance de workflow et l'ouverture de PR — aucun commit direct.
