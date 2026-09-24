# Créer la GitHub App Mirador

Mirador s'authentifie auprès de GitHub via une **GitHub App** (JWT RS256 + tokens
d'installation à durée limitée). Ce guide crée l'App, récupère les secrets et les
configure. Réglages de référence : [`infra/github-app/manifest.json`](../infra/github-app/manifest.json)
(verrouillé par `tests/unitaire/test_manifest_github_app.py`).

> ⚠️ L'URL du webhook n'est connue qu'**après** le déploiement de la fonction
> Scaleway. On crée d'abord l'App avec une URL provisoire, puis on la met à jour
> (voir § 5).

---

## 1. Créer l'App

### Option A — via le manifest (recommandé)

Le flux « manifest » pré-remplit permissions et événements depuis
`manifest.json`. Il nécessite de POSTer le manifest vers GitHub puis d'échanger le
`code` de redirection. Pour un usage perso sans serveur, l'**option B** est plus
simple ; garde le manifest comme source de vérité des réglages.

### Option B — manuellement (sans serveur)

1. Va sur **GitHub → Settings → Developer settings → GitHub Apps → New GitHub App**
   (ou `https://github.com/settings/apps/new`).
2. Renseigne, en copiant depuis `manifest.json` :
   - **GitHub App name** : `Mirador CI/CD`
   - **Homepage URL** : l'URL de ton dépôt
   - **Webhook** : coché « Active ». **Webhook URL** : mets une valeur provisoire
     (ex. `https://example.com/webhooks/github`) — on la corrigera en § 5.
   - **Webhook secret** : génère-le et garde-le → ce sera `WEBHOOK_SECRET` :
     ```bash
     openssl rand -hex 32
     ```
3. **Permissions** (Repository permissions) — exactement celles du manifest :

   | Permission | Niveau | Pourquoi |
   |------------|--------|----------|
   | Metadata | Read-only | accès de base à l'API |
   | Checks | Read-only | lecture de l'état des workflows (détection) |
   | Actions | Read & write | relance de workflow |
   | Contents | Read & write | branche de correctif pour les PR |
   | Pull requests | Read & write | ouverture des PR de correctif |
   | Issues | Read & write | escalade + validation humaine |

4. **Subscribe to events** : coche **Workflow run** et **Issue comment**.
5. **Where can this GitHub App be installed?** : *Only on this account* (App privée).
6. Clique **Create GitHub App**.

---

## 2. Récupérer les identifiants et la clé privée

Sur la page de l'App fraîchement créée :

- **App ID** (en haut) → ce sera `GITHUB_APP_ID`.
- **Private keys → Generate a private key** : télécharge le fichier `.pem`.
  Son **contenu** (multi-lignes, `-----BEGIN RSA PRIVATE KEY----- …`) sera
  `GITHUB_APP_PRIVATE_KEY`.

```bash
# Vérifier que la clé est valide
openssl rsa -in mirador.YYYY-MM-DD.private-key.pem -check -noout
```

> 🔒 Ranger le `.pem` **hors du dépôt** (`~/.config/mirador/`, fichier `600`) —
> jamais dans `infra/github-app/`.

---

## 3. Installer l'App sur le dépôt de test

1. **Install App** (menu latéral) → installe l'App sur ton compte, en la limitant
   au **dépôt de test** (crée-en un jetable si besoin).
2. Note l'**`installation_id`** : il apparaît dans l'URL après installation :
   `https://github.com/settings/installations/<INSTALLATION_ID>`.

   Ou via l'API (avec un JWT de l'App) :
   ```bash
   gh api /app/installations --jq '.[].id'   # nécessite une auth App
   ```

---

## 4. Renseigner la config des dépôts (`MIRADOR_DEPOTS`)

Mirador lit les dépôts surveillés depuis la variable d'env `MIRADOR_DEPOTS` (JSON).
Une entrée par dépôt :

```json
[
  {
    "identifiant_github": "aboigues/mon-depot-test",
    "installation_id": 98765,
    "responsables": ["aboigues"],
    "seuil_timeout_secondes": 3600,
    "regles": [
      {
        "nom": "timeout-connexion-bd",
        "description": "Timeout de connexion à la base de données",
        "pattern_log": "connection timeout",
        "action_recommandee": "RELANCE",
        "origine": "MANUELLE"
      }
    ]
  }
]
```

- `responsables` : logins GitHub autorisés à `/approuver` et `/rejeter`.
- `regles` : optionnel ; une cause connue (règle qui matche) → relance auto en LOW.
- `lecture_depot` : optionnel, défaut `false`. Autorise le correcteur à lire les
  manifestes/Dockerfiles du dépôt (recherche de la référence fautive) pour
  produire un correctif matérialisable au lieu de s'abstenir faute d'accès au
  dépôt. Coût en appels API et en tokens à chaque anomalie — à activer dépôt
  par dépôt après avoir constaté une abstention évitable, pas par défaut.

---

## 5. Après déploiement Scaleway — brancher le webhook

Une fois la fonction déployée (étape suivante du projet), tu obtiendras l'URL
publique du récepteur. Reviens sur l'App → **General → Webhook URL** et remplace la
valeur provisoire par :

```
https://<url-fonction-scaleway>/webhooks/github
```

Garde le **même Webhook secret** que celui déjà configuré côté fonction
(`WEBHOOK_SECRET`), sinon toutes les livraisons seront rejetées (HTTP 403).

Test : l'onglet **Advanced → Recent Deliveries** de l'App montre le `ping` initial
et les livraisons suivantes avec leur code de réponse.

---

## 6. Récapitulatif des secrets

À configurer comme **variables d'environnement chiffrées de la fonction Scaleway**
(pas dans le dépôt) :

| Variable | Source |
|----------|--------|
| `GITHUB_APP_ID` | § 2 |
| `GITHUB_APP_PRIVATE_KEY` | contenu du `.pem` (§ 2) |
| `WEBHOOK_SECRET` | `openssl rand -hex 32` (§ 1) |
| `ANTHROPIC_API_KEY` | console Anthropic |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | clés API Scaleway (S3 + MnQ) |
| `BUCKET_NAME` | bucket Object Storage (provisioning à venir) |
| `SQS_QUEUE_URL` | queue `mirador-webhooks` (provisioning à venir) |
| `S3_ENDPOINT_URL` | `https://s3.fr-par.scw.cloud` |
| `AWS_REGION` | `fr-par` |
| `MIRADOR_DEPOTS` | § 4 |

> 🔒 Le `.pem` et le webhook secret ne doivent **jamais** être commités. Le
> `.gitignore` couvre déjà `*.pem` ? Vérifie-le avant tout commit.
