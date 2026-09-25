🇬🇧 **English** | [🇫🇷 Français](#politique-de-sécurité)

# Security Policy

## Supported versions

Mirador has no numbered releases: the `master` branch is what runs in production,
deployed continuously by GitHub Actions. Only `master` receives security fixes.

| Version | Supported |
| ------- | --------- |
| `master` (deployed) | ✅ |
| Any other branch or older commit | ❌ |

## Reporting a vulnerability

**Please do not open a public issue, pull request or discussion for a security problem.**

Report it privately through GitHub:
**Security** tab → **Report a vulnerability**
([direct link](https://github.com/aboigues/mirador/security/advisories/new)).

Please include:

- the affected component (webhook function, processing function, GitHub App,
  workflows, `mirador-autofix.yml`…);
- the steps to reproduce, or a proof of concept;
- the impact you expect (what an attacker could read, write or trigger).

Mirador is maintained by a single person, on a best-effort basis:

| Step | Target |
| ---- | ------ |
| Acknowledgement | within 7 days |
| First assessment (accepted / declined, with reasons) | within 14 days |
| Fix for an accepted vulnerability | as fast as severity requires; you will be kept informed |

Once fixed, the vulnerability is disclosed in a GitHub Security Advisory. You will be
credited, unless you prefer to remain anonymous.

## Scope

In scope — anything that lets someone act through Mirador or read what it protects:

- forging or replaying webhooks (HMAC-SHA256 signature check);
- triggering `/approuver` or `/rejeter` without being a declared owner;
- making Mirador open a pull request, rerun a workflow or dispatch
  `mirador-autofix.yml` with attacker-controlled content — including through
  **prompt injection** in CI logs analysed by the LLM;
- tampering with the append-only audit log;
- leaking a secret (GitHub App key, API keys, storage credentials) through logs,
  issues, pull requests or workflow runs.

Out of scope:

- vulnerabilities in GitHub, Scaleway or Anthropic themselves (report them to the
  vendor);
- denial of service or load testing against the production endpoints;
- findings that require an already-compromised maintainer account or secret;
- repositories monitored by Mirador (report to their owners).

Please test only against your own deployment or with the maintainer's agreement.
Good-faith research that follows this policy will not be pursued.

---

# Politique de sécurité

## Versions supportées

Mirador n'a pas de versions numérotées : la branche `master` est ce qui tourne en
production, déployée en continu par GitHub Actions. Seule `master` reçoit des correctifs
de sécurité.

| Version | Supportée |
| ------- | --------- |
| `master` (déployée) | ✅ |
| Toute autre branche ou commit antérieur | ❌ |

## Signaler une vulnérabilité

**Merci de ne pas ouvrir d'issue, de pull request ni de discussion publique pour un
problème de sécurité.**

Signale-le en privé via GitHub :
onglet **Security** → **Report a vulnerability**
([lien direct](https://github.com/aboigues/mirador/security/advisories/new)).

Précise si possible :

- le composant concerné (fonction webhook, fonction de traitement, GitHub App,
  workflows, `mirador-autofix.yml`…) ;
- les étapes pour reproduire, ou une preuve de concept ;
- l'impact attendu (ce qu'un attaquant pourrait lire, écrire ou déclencher).

Mirador est maintenu par une seule personne, au mieux de ses disponibilités :

| Étape | Objectif |
| ----- | -------- |
| Accusé de réception | sous 7 jours |
| Première évaluation (acceptée / refusée, avec justification) | sous 14 jours |
| Correctif d'une vulnérabilité acceptée | aussi vite que la gravité l'exige ; tu seras tenu informé |

Une fois corrigée, la vulnérabilité est publiée dans une GitHub Security Advisory. Tu
seras crédité, sauf si tu préfères rester anonyme.

## Périmètre

Dans le périmètre — tout ce qui permet d'agir à travers Mirador ou de lire ce qu'il
protège :

- forger ou rejouer des webhooks (vérification de signature HMAC-SHA256) ;
- déclencher `/approuver` ou `/rejeter` sans être un responsable déclaré ;
- faire ouvrir une pull request, relancer un workflow ou déclencher
  `mirador-autofix.yml` avec un contenu contrôlé par un attaquant — y compris par
  **injection de prompt** dans les logs de CI analysés par le LLM ;
- altérer le journal d'audit append-only ;
- faire fuiter un secret (clé de la GitHub App, clés d'API, identifiants de stockage)
  via des logs, des issues, des pull requests ou des runs de workflow.

Hors périmètre :

- les vulnérabilités de GitHub, Scaleway ou Anthropic eux-mêmes (à signaler à
  l'éditeur) ;
- le déni de service ou les tests de charge contre les endpoints de production ;
- les constats qui supposent un compte mainteneur ou un secret déjà compromis ;
- les dépôts surveillés par Mirador (à signaler à leurs propriétaires).

Merci de ne tester que sur ton propre déploiement, ou avec l'accord du mainteneur. Une
recherche de bonne foi qui respecte cette politique ne fera l'objet d'aucune poursuite.
