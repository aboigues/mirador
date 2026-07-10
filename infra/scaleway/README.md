# Provisioning Scaleway — Mirador

Toutes les ressources vivent dans un **projet dédié MIRADOR** (isolation propre,
séparé du reste). Accès via le profil CLI `telemach`.

| | |
|---|---|
| Profil CLI | `telemach` |
| Projet | **MIRADOR** — `4135267b-fd5a-4079-b716-8b24240deabd` |
| Application IAM | `mirador` — `fc368266-20ae-481c-a40a-d70f1580ec53` |
| Région | `fr-par` |

## Ressources provisionnées (2026-07-10)

| Ressource | Nom / ID | État |
|-----------|----------|------|
| MnQ SQS | service | ✅ activé (`https://sqs.mnq.fr-par.scaleway.com`) |
| Credentials MnQ | `mirador` — `39cdedeb-6d47-4cee-8e9a-1e37710501ba` | ✅ (access_key `qZYIi91MEs65STZbiPmn`) |
| Queue FIFO | `mirador-webhooks.fifo` (+ `-dlq`) | ✅ (VisibilityTimeout 300s, maxReceive 3) — E2E OK |
| Queue FIFO | `mirador-writes.fifo` (+ `-dlq`) | ✅ (VisibilityTimeout 60s, maxReceive 3) |
| Bucket Object Storage | `telemach-mirador-audit` | ⏳ **à créer** dans MIRADOR (voir § Bucket) |

URLs des queues : `https://sqs.mnq.fr-par.scaleway.com/project-4135267b-fd5a-4079-b716-8b24240deabd/<queue>`.

Rejouer MnQ (idempotent) : `./provision.sh`.

## Deux jeux de credentials DISTINCTS (découverte du provisioning réel)

| Usage | Variables | Source |
|-------|-----------|--------|
| **SQS / MnQ** | `MNQ_ACCESS_KEY`, `MNQ_SECRET_KEY` | `scw mnq sqs create-credentials` |
| **S3 / bucket** | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | clé API IAM de l'app, scopée MIRADOR |

Les credentials MnQ ne donnent pas accès à S3, et inversement.

## § Bucket — pourquoi il n'est pas dans `provision.sh`

`scw object bucket create` **n'a pas** de paramètre `project-id` : le bucket est
attribué au projet des **credentials S3** utilisées. La clé par défaut vise un
autre projet (`0a3e5b10`), donc elle crée toujours au mauvais endroit — vérifié.

Le bucket doit donc être créé **avec une clé dont le projet par défaut est
MIRADOR**. Deux voies :

- **Console (le plus simple)** : créer le bucket `telemach-mirador-audit` en
  sélectionnant le projet **MIRADOR** dans l'interface.
- **CLI avec la clé applicative** : après création de la clé (§ IAM), configurer
  un profil scw avec ses `access_key`/`secret_key` puis `scw object bucket create
  name=telemach-mirador-audit region=fr-par`. Nécessite temporairement le
  permission set `ObjectStorageBucketsWrite` sur la policy.

## § IAM — application, policy, clé

Application déjà créée (`fc368266-…`). Reste la policy (scope MIRADOR) et la clé.

```bash
PROJ=4135267b-fd5a-4079-b716-8b24240deabd
APP_ID=fc368266-20ae-481c-a40a-d70f1580ec53

# Policy runtime (moindre privilège) — scope projet MIRADOR
scw iam policy create name=mirador-object-storage \
  application-id=$APP_ID \
  rules.0.permission-set-names.0=ObjectStorageReadOnly \
  rules.0.permission-set-names.1=ObjectStorageObjectsWrite \
  rules.0.project-ids.0=$PROJ --profile telemach

# Clé API — IMPORTANT :
#  - default-project-id sur MIRADOR (sinon buckets/objets iraient au mauvais projet)
#  - expires-at OBLIGATOIRE (l'org impose une expiration), format RFC 3339
scw iam api-key create application-id=$APP_ID \
  default-project-id=$PROJ \
  expires-at=2027-07-10T00:00:00Z \
  description="Mirador Object Storage" --profile telemach
```

> ⏰ La clé expire à la date `expires-at` → la fonction cesse d'écrire dans le
> bucket ce jour-là. Noter l'échéance et prévoir la rotation ~1 mois avant.

- `ObjectStorageObjectsDelete` est **volontairement exclu** → le writer ne peut
  jamais supprimer, ce qui verrouille l'immutabilité au niveau IAM (Principe III).
- Pour créer le bucket via CLI avec cette clé, ajouter temporairement
  `ObjectStorageBucketsWrite` à la policy, puis le retirer.
- L'`access_key` / `secret_key` de la clé → `AWS_ACCESS_KEY_ID` /
  `AWS_SECRET_ACCESS_KEY` de la fonction.

## Variables d'environnement de la fonction

| Variable | Valeur |
|----------|--------|
| `BUCKET_NAME` | `telemach-mirador-audit` |
| `S3_ENDPOINT_URL` | `https://s3.fr-par.scw.cloud` |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | clé API app, scopée MIRADOR (§ IAM) |
| `SQS_ENDPOINT_URL` | `https://sqs.mnq.fr-par.scaleway.com` |
| `SQS_QUEUE_URL` | `…/project-4135267b-…/mirador-webhooks.fifo` |
| `MNQ_ACCESS_KEY` / `MNQ_SECRET_KEY` | credentials MnQ (dans `.secrets.env`) |
| `AWS_REGION` | `fr-par` |
| `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY`, `WEBHOOK_SECRET` | voir `docs/github-app.md` |
| `ANTHROPIC_API_KEY` | console Anthropic |
| `MIRADOR_DEPOTS` | config JSON des dépôts (voir `docs/github-app.md` §4) |

> 🔒 `.secrets.env` et tout fichier de secrets sont exclus par `.gitignore`.
