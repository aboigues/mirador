# Provisioning Scaleway — Mirador

Ressources nécessaires au fonctionnement réel de Mirador dans le projet Scaleway
cible (profil `telemach` → projet **LMS**, région `fr-par`).

## Ressources provisionnées (2026-07-10)

| Ressource | Nom | État |
|-----------|-----|------|
| Bucket Object Storage | `telemach-mirador-audit` | ✅ créé |
| MnQ SQS | service | ✅ activé (`https://sqs.mnq.fr-par.scaleway.com`) |
| Queue FIFO | `mirador-webhooks.fifo` (+ `-dlq`) | ✅ créée (VisibilityTimeout 300s, maxReceive 3) |
| Queue FIFO | `mirador-writes.fifo` (+ `-dlq`) | ✅ créée (VisibilityTimeout 60s, maxReceive 3) |
| Credentials MnQ | `mirador` | ✅ créées |

Rejouer (idempotent) : `./provision.sh` (utilise `--profile telemach`).

## ⚠️ Credentials — deux jeux DISTINCTS

Découverte au provisioning réel (invisible aux tests à doubles) :

| Usage | Variables | Source | Vérifié |
|-------|-----------|--------|---------|
| **SQS / MnQ** (enqueue webhooks) | `MNQ_ACCESS_KEY`, `MNQ_SECRET_KEY` | `scw mnq sqs create-credentials` | ✅ envoi/réception FIFO OK |
| **S3 / bucket** (writer d'audit) | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | clé IAM avec droit **Object Storage sur le projet LMS** | ❌ **bloqué** |

- Les credentials MnQ **ne** donnent **pas** accès à S3 (et inversement).
- La clé IAM du profil par défaut voit un autre projet (`strava-synchro-journal`)
  et reçoit **403 / AccessDenied** sur `telemach-mirador-audit`.
- **Action requise (Alexandre)** : fournir/créer une clé API IAM ayant la
  permission Object Storage (lecture + écriture) sur le projet LMS
  (`0a3e5b10-4caa-4048-a607-f53080c01569`), puis la placer dans
  `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` de la fonction.

  ```bash
  # Exemple (à adapter aux policies IAM du projet) :
  scw iam api-key create --profile telemach \
      description="Mirador Object Storage" \
      default-project-id=0a3e5b10-4caa-4048-a607-f53080c01569
  ```

## Variables d'environnement de la fonction

| Variable | Valeur |
|----------|--------|
| `BUCKET_NAME` | `telemach-mirador-audit` |
| `S3_ENDPOINT_URL` | `https://s3.fr-par.scw.cloud` |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | clé IAM Object Storage projet LMS (à fournir) |
| `SQS_ENDPOINT_URL` | `https://sqs.mnq.fr-par.scaleway.com` |
| `SQS_QUEUE_URL` | `…/mirador-webhooks.fifo` (sortie de `creer_queues.py`) |
| `MNQ_ACCESS_KEY` / `MNQ_SECRET_KEY` | credentials MnQ (dans `.secrets.env`) |
| `AWS_REGION` | `fr-par` |
| `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY`, `WEBHOOK_SECRET` | voir `docs/github-app.md` |
| `ANTHROPIC_API_KEY` | console Anthropic |
| `MIRADOR_DEPOTS` | config JSON des dépôts (voir `docs/github-app.md` §4) |

> 🔒 `.secrets.env` et tout fichier de secrets sont exclus par `.gitignore`.
