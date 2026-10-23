# Fichiers à installer dans un dépôt surveillé

Pour que Mirador puisse **matérialiser un correctif de dépendances** (et pas
seulement le documenter), le dépôt surveillé doit fournir un environnement de
build. Mirador n'a pas de toolchain Go ; il délègue donc à un workflow GitHub
Actions qui tourne dans le dépôt.

## `mirador-autofix.yml`

À copier dans `.github/workflows/mirador-autofix.yml` du dépôt surveillé.

Déclenché par Mirador (`workflow_dispatch`) sur `/approuver` d'une proposition de
mise à jour de dépendances. Il :

1. crée la branche de correction depuis la base ;
2. applique la version Go cible et/ou les modules (`go get`), régénère
   `go.mod`/`go.sum` via `go mod tidy` (dans un vrai runner) ;
3. commite, pousse, et ouvre la pull request.

Entrées **structurées** (`go_version`, `modules` en `chemin@version`) — jamais de
commande arbitraire, pour éviter toute injection.

### Prérequis côté dépôt surveillé

- Réglage **« Allow GitHub Actions to create and approve pull requests »** activé
  (Settings → Actions → General → Workflow permissions), sinon l'étape d'ouverture
  de PR échoue (`GitHub Actions is not permitted to create ... pull requests`).
- La GitHub App Mirador doit avoir la permission **Actions: write** (déclenchement)
  — déjà requise pour la relance de workflow.

> Sans ce workflow, Mirador reste fonctionnel : le déclenchement échoue,
> l'approbation est enregistrée et le correctif reste à appliquer à la main.

## `mirador-mesure-images.yml`

À copier dans `.github/workflows/mirador-mesure-images.yml` du dépôt surveillé.

Déclenché par Mirador quand un correctif **change le tag d'une image** (`image:`
d'un manifeste, `FROM` d'un Dockerfile). Mirador ne laisse plus Claude choisir le
tag : celui-ci le tirait de ses données d'entraînement (kubernetes-formation#216 :
`grafana/grafana:13.2.1 → 11.4.0`, `alpine/helm:3.22.0 → 3.14.0`, soit 7 → 43 CVE
bloquantes). Mirador liste lui-même les tags publiés sur Docker Hub (dernier
correctif de la même mineure, puis de la mineure suivante, jamais en arrière) et
les passe en entrée. Le workflow :

1. mesure le tag en place et chaque candidat avec `trivy image --image-src remote`
   et **les filtres de la barrière du dépôt** ;
2. retient, par image, le candidat qui fait **strictement baisser** le nombre de
   CVE bloquantes ;
3. remplace la référence dans les fichiers suivis, ouvre la PR avec le tableau des
   mesures pour corps (le corps ne peut plus contredire le diff) ;
4. sinon, publie le tableau dans l'Issue Mirador (rouverte) ou en ouvre une :
   aucun tag publié ne corrige, correctif amont attendu.

Une exception datée (`.trivyignore.yaml`) reste une décision humaine : elle n'est
pas appliquée à la mesure, et Mirador ne l'écrit jamais.

### À régler à l'installation

Le bloc `env:` en tête du fichier **doit reproduire la barrière** du dépôt, sans
quoi la mesure ne prédit pas ce que la barrière bloquera :

| Variable | Exemple (kubernetes-formation, `scan-images.yml`) |
|---|---|
| `SEVERITE` | `HIGH,CRITICAL` |
| `PKG_TYPES` | `os` |
| `IGNORE_UNFIXED` | `true` |
| `TRIVY_VERSION` | la même que la barrière (`v0.72.0`) |

Prérequis identiques à `mirador-autofix.yml`, plus la permission `issues: write`
(déclarée dans le workflow) pour publier une abstention.
