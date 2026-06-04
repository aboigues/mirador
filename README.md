# Mirador

Surveillance autonome de pipelines CI/CD GitHub Actions avec agents IA.

Mirador écoute les événements GitHub Actions, détecte les anomalies, les classe par niveau de risque et applique des corrections automatiques — ou escalade vers validation humaine pour les cas critiques.

---

## Architecture

```mermaid
flowchart TD
    GH["GitHub Actions\n(dépôts surveillés)"]
    WH["POST /webhooks/github\nvalidation HMAC-SHA256"]
    MNQ["Scaleway MnQ\nmirador-webhooks"]
    DLQ1["mirador-webhooks-dlq\n(3 tentatives max)"]
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

    WQUEUE["Scaleway MnQ\nmirador-writes"]
    DLQ2["mirador-writes-dlq\n(3 tentatives max)"]
    WRITER["Writer unique\nINSERT-only · WAL"]
    BUCKET[("Scaleway Object Storage\nmirador.db · SQLite WAL\n~€0.02/mois")]
    API["GET /etat · /historique\nAPI consultation"]
    HUMAIN["Validation humaine\nPOST /interventions/:id/approuver"]

    GH -->|"webhook workflow_run"| WH
    WH -->|"202 — message enqueué"| MNQ
    MNQ -->|"échec × 3"| DLQ1
    MNQ --> TRAIT
    TRAIT --> DET
    DET --> SUP
    SUP -->|"INFO"| WQUEUE
    SUP -->|"LOW"| CORR
    SUP -->|"MEDIUM"| NOTIF
    SUP -->|"HIGH / CRITICAL"| HUMAIN
    CORR --> RELANCE
    CORR --> PR
    RELANCE --> WQUEUE
    PR --> WQUEUE
    NOTIF --> WQUEUE
    HUMAIN -->|"approuvé"| CORR
    HUMAIN -->|"rejeté"| WQUEUE
    WQUEUE -->|"échec × 3"| DLQ2
    WQUEUE --> WRITER
    WRITER -->|"download → INSERT → upload + checksum"| BUCKET
    BUCKET --> API
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
- **Files de messages** : Scaleway MnQ (SQS-compatible, boto3) — `mirador-webhooks` (événements) + `mirador-writes` (écritures sérialisées)
- **Stockage** : SQLite en mode WAL, persisté dans un bucket Scaleway Object Storage (~€0,02/mois) — audit append-only enforced par writer unique (INSERT-only)
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
