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

> Sans ce workflow, Mirador reste fonctionnel : il ouvre une PR documentaire
> (`MIRADOR-FIX.md`) à compléter manuellement.
