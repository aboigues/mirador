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
| Queue standard | `mirador-webhooks` (+ `-dlq`) | ✅ (VisibilityTimeout 300s, maxReceive 3) — trigger scw_sqs OK |
| Queue standard | `mirador-writes` (+ `-dlq`) | ✅ (VisibilityTimeout 60s, maxReceive 3) |

> ⚠️ **Queues STANDARD, pas FIFO** : les triggers `scw_sqs` de Scaleway Functions
> ne consomment pas les files FIFO (messages non délivrés au handler → DLQ). La
> déduplication est faite côté consommateur (idempotence par `delivery_id` dans le
> pipeline de traitement).
| Bucket Object Storage | `telemach-mirador-audit` | ⏳ **à créer** dans MIRADOR (voir § Bucket) |

URLs des queues : `https://sqs.mnq.fr-par.scaleway.com/project-4135267b-fd5a-4079-b716-8b24240deabd/<queue>`.

Rejouer MnQ (idempotent) : `./provision.sh`.

## Trois jeux de credentials DISTINCTS

| Usage | Variables | Utilisées par | Source |
|-------|-----------|---------------|--------|
| **Déploiement** | `SCW_DEPLOY_ACCESS_KEY`, `SCW_DEPLOY_SECRET_KEY` | GitHub Actions (CLI `scw`) | clé API IAM de l'app `mirador-deploy` (`FunctionsFullAccess`, projet MIRADOR) |
| **S3 / bucket** | `SCALEWAY_S3_ACCESS_KEY`, `SCALEWAY_S3_SECRET_KEY` | fonctions (writer d'audit) | clé API IAM de l'app `mirador`, scopée MIRADOR |
| **SQS / MnQ** | `MNQ_ACCESS_KEY`, `MNQ_SECRET_KEY` | fonctions (webhook) | `scw mnq sqs create-credentials` |

Aucun jeu ne donne accès au périmètre d'un autre. Tout est chez Scaleway : aucun de
ces noms ne désigne AWS, même si boto3 (client S3/SQS) est utilisé.

> ⚠️ Scaleway **réserve le préfixe `SCW_`** aux variables d'environnement des fonctions
> (erreur « A reserved environment variable is used »). Les variables posées sur les
> fonctions utilisent donc `SCALEWAY_` ; seule la clé de déploiement, qui reste dans la
> CI, garde les noms `SCW_*` attendus par la CLI. `deploy-config.yml` refuse tout `SCW_`.

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
- L'`access_key` / `secret_key` de la clé → `SCALEWAY_S3_ACCESS_KEY` /
  `SCALEWAY_S3_SECRET_KEY` de la fonction.

## Variables d'environnement de la fonction

| Variable | Valeur |
|----------|--------|
| `BUCKET_NAME` | `telemach-mirador-audit` |
| `S3_ENDPOINT_URL` | `https://s3.fr-par.scw.cloud` |
| `SCALEWAY_S3_ACCESS_KEY` / `SCALEWAY_S3_SECRET_KEY` | clé API app, scopée MIRADOR (§ IAM) |
| `SQS_ENDPOINT_URL` | `https://sqs.mnq.fr-par.scaleway.com` |
| `SQS_QUEUE_URL` | `…/project-4135267b-…/mirador-webhooks` (queue standard) |
| `MNQ_ACCESS_KEY` / `MNQ_SECRET_KEY` | credentials MnQ (dans le fichier de secrets local) |
| `SCALEWAY_REGION` | `fr-par` |
| `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY`, `WEBHOOK_SECRET` | voir `docs/github-app.md` |
| `ANTHROPIC_API_KEY` | console Anthropic |
| `MIRADOR_DEPOTS` | config JSON des dépôts (voir `docs/github-app.md` §4) |

> 🔒 Le fichier de secrets local vit **hors du dépôt** : `~/.config/mirador/secrets.env`
> (répertoire `700`, fichier `600`, hors dossier synchronisé). Les scripts le lisent
> à cet emplacement, ou à celui de `MIRADOR_SECRETS` s'il est défini. Ne jamais le
> recréer dans l'arbre de travail : un fichier swap ou un `git add` de trop suffit à le
> publier (incident du 2026-07-15, voir `docs/rotation-secrets.md`).

## Déploiement des fonctions

Deux fonctions Scaleway dans le namespace `mirador` (projet MIRADOR) :

| Fonction | Handler | Privacy | Scale | Rôle |
|----------|---------|---------|-------|------|
| `webhook` | `handler.webhook` | public | 0→5 | reçoit les webhooks GitHub → enqueue MnQ |
| `traitement` | `handler.traitement` | private | 0→**1** | consomme la file → pipeline |

> `traitement` est plafonné à **max-scale 1** : le writer d'audit est unique
> (sérialisation des écritures SQLite → immutabilité). Un trigger SQS le déclenche
> sur `mirador-webhooks` (queue standard).

### Déploiement initial (une fois)

Remplir `~/.config/mirador/secrets.env` avec **toutes** les variables du tableau
ci-dessus (env + secrets), puis :

```bash
./infra/scaleway/deploy.sh
```

Le script crée le namespace, les 2 fonctions (env + secrets), déploie le code,
branche le trigger SQS, et affiche l'**URL du webhook** à reporter dans la
GitHub App (`docs/github-app.md` § 5).

### Mises à jour de code (continu)

À chaque push sur `master` touchant `src/`, `handler.py` ou `requirements.txt`,
le workflow [`.github/workflows/deploy.yml`](../../.github/workflows/deploy.yml)
met à jour **uniquement le code** des fonctions (build zip → upload → deploy),
sans toucher aux secrets. Manuellement :

```bash
./infra/scaleway/update_code.sh <function-id>
```

**Secrets GitHub Actions requis** (Settings → Secrets) : `SCW_DEPLOY_ACCESS_KEY`,
`SCW_DEPLOY_SECRET_KEY`, `SCW_PROJECT_ID` (= projet MIRADOR). Le CD ne connaît pas les
secrets applicatifs — ils restent sur les fonctions.

### Re-déploiement après un correctif (`redeploy.sh`)

Tant que les secrets `SCW_*` ne sont pas configurés, le CD automatique échoue :
après le merge d'un fix, le redéploiement est **manuel**. `redeploy.sh` enchaîne
les étapes et affiche la marche à suivre pour le re-test E2E :

```bash
./infra/scaleway/redeploy.sh            # redéploie les 2 fonctions, puis propose de purger
./infra/scaleway/redeploy.sh --sans-purge   # redéploie seulement
./infra/scaleway/redeploy.sh --oui      # redéploie + purge sans confirmation
```

Ce qu'il fait :

1. Résout les IDs des fonctions `webhook` / `traitement` par leur nom (pas d'ID
   en dur) dans le namespace `mirador`.
2. Redéploie le **code** des deux fonctions via `update_code.sh` — `traitement`
   d'abord (le consommateur corrigé doit précéder tout nouvel événement), puis
   `webhook`. Ne touche pas aux secrets.
3. Propose de **purger** les messages poison restés bloqués d'un test précédent
   (queue `mirador-webhooks` + sa DLQ), via `purger_queues.py`.
4. Affiche l'URL du webhook, les étapes du re-test E2E réel et les commandes de
   vérification (logs de la fonction, profondeur des queues, issues ouvertes).

> Prérequis : `~/.config/mirador/secrets.env` rempli (les credentials MnQ servent à la purge) et
> le profil scw `telemach`.

#### Purge des queues (`purger_queues.py`)

Vide une paire de queues (principale + DLQ) — utile avant un re-test pour ne pas
rejouer d'anciens messages. **Irréversible**, et limité à un appel par minute et
par queue (`PurgeQueue`). Utilise les credentials MnQ (`MNQ_ACCESS_KEY` /
`MNQ_SECRET_KEY`) chargés depuis `~/.config/mirador/secrets.env` :

```bash
set -a && . ~/.config/mirador/secrets.env && set +a
python infra/scaleway/purger_queues.py                # mirador-webhooks + DLQ
python infra/scaleway/purger_queues.py mirador-writes # autre paire
```

### Contraintes du runtime (découvertes au 1er déploiement réel)

- **Runtime = Alpine Linux / musl** (`EXT_SUFFIX = …-linux-musl.so`). Les wheels
  `manylinux`/glibc produisent un `.so` introuvable au runtime → il faut des
  **wheels `musllinux`**. `update_code.sh` les installe via
  `pip install --platform musllinux_1_2_x86_64 --only-binary=:all: --abi cp312`.
- **Le déploiement bas-niveau (upload zip) ne build pas `requirements.txt`** → les
  dépendances sont **vendorées** dans le zip (~20 Mo). `requirements.txt` est
  volontairement exclu du zip pour éviter tout rebuild côté Scaleway.
- **Namespace** : après création il est brièvement « blocking » → `deploy.sh`
  attend l'état `ready` avant de créer les fonctions.
- **URL du webhook déployée** :
  `https://miradorjghejyxc-webhook.functions.fnc.fr-par.scw.cloud/webhooks/github`
  (à reporter dans la GitHub App). Validée en réel : 202 (signé), 403 (mauvaise
  signature), 204 (événement ignoré).
