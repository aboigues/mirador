<!--
SYNC IMPACT REPORT
==================
Version: 0.0.0 → 1.0.0 (MAJOR — constitution initiale du projet Mirador)

Principes ajoutés:
  - I. Sécurité par conception
  - II. Observabilité des pipelines
  - III. Intervention contrôlée et traçable
  - IV. Tests obligatoires (TDD)
  - V. Simplicité et maintenabilité

Sections ajoutées:
  - Contraintes techniques MLOps
  - Flux de développement

Templates à synchroniser:
  ✅ .specify/templates/plan-template.md — Constitution Check aligné avec les 5 principes
  ✅ .specify/templates/spec-template.md — Exigences de sécurité et observabilité incluses
  ✅ .specify/templates/tasks-template.md — Phases reflètent les principes de sécurité et TDD

TODOs différés:
  - Aucun placeholder non résolu
-->

# Constitution Mirador

## Principes fondamentaux

### I. Sécurité par conception

La sécurité n'est pas une fonctionnalité ajoutée après coup : elle DOIT être intégrée dès la
conception de chaque composant. Tout code interagissant avec des pipelines CI/CD ou exécutant
des actions correctives DOIT passer par un mécanisme d'autorisation explicite.

Règles non négociables :
- Toute action d'intervention sur un pipeline DOIT être authentifiée et autorisée.
- Les secrets (tokens, clés API, credentials) ne DOIVENT JAMAIS apparaître dans les logs,
  le code source, ou les artefacts de build.
- Le principe du moindre privilège DOIT s'appliquer à tous les agents IA et services.
- Les entrées externes (webhooks, événements CI/CD) DOIVENT être validées et assainies
  avant tout traitement.
- Les communications inter-services DOIVENT être chiffrées (TLS 1.2 minimum).
- Tout accès aux APIs tierces DOIT utiliser des tokens à durée de vie limitée et révocables.

**Rationale** : Mirador agit sur des infrastructures de production via des agents IA. Une
compromission de sécurité pourrait entraîner des interruptions de service ou des modifications
non autorisées de pipelines critiques.

### II. Observabilité des pipelines

Mirador DOIT maintenir une visibilité totale et en temps réel sur l'état des pipelines CI/CD
surveillés. Chaque état, transition et anomalie DOIT être capturé et consultable.

Règles non négociables :
- Chaque événement de pipeline (démarrage, étape, échec, succès) DOIT être journalisé
  avec horodatage ISO 8601 et identifiant de corrélation.
- Les métriques de santé des pipelines DOIT être exposées via une interface standardisée
  (ex. : endpoint `/metrics` compatible Prometheus).
- Les agents IA DOIVENT journaliser chaque décision d'observation avec le contexte ayant
  conduit à cette décision.
- Les alertes DOIVENT distinguer les anomalies détectées, les interventions proposées, et
  les interventions exécutées.
- La rétention des logs DOIT couvrir au minimum 30 jours pour l'audit et la traçabilité.

**Rationale** : Sans observabilité complète, les agents IA ne peuvent pas prendre de décisions
éclairées, et les équipes ne peuvent pas auditer les actions passées ni diagnostiquer les
comportements inattendus.

### III. Intervention contrôlée et traçable

Toute intervention d'un agent IA sur un pipeline CI/CD DOIT être explicite, réversible si
possible, et intégralement tracée. Aucune action destructive ne DOIT être exécutée sans
validation humaine ou règle approuvée explicitement.

Règles non négociables :
- Chaque intervention DOIT être précédée d'une phase d'analyse documentée.
- Les interventions DOIVENT être classifiées par niveau de risque : INFO, LOW, MEDIUM,
  HIGH, CRITICAL. Les niveaux HIGH et CRITICAL exigent une validation humaine.
- Toute intervention DOIT produire un rapport d'action : contexte, décision, résultat,
  impact observé.
- Un mécanisme de rollback ou d'annulation DOIT exister pour toute intervention réversible.
- L'historique complet des interventions DOIT être conservé et non modifiable (append-only).

**Rationale** : Les agents IA opérant sur des systèmes de production présentent un risque
d'effet de bord non prévu. La traçabilité et le contrôle humain gradué garantissent la
confiance des équipes opérationnelles.

### IV. Tests obligatoires (TDD)

Le développement DOIT suivre le cycle Test-Driven Development (TDD). Aucune fonctionnalité
ne DOIT être implémentée sans que des tests aient été écrits et validés au préalable.

Règles non négociables :
- Cycle obligatoire : Tests écrits → Tests approuvés → Tests en échec → Implémentation.
- Les tests d'intégration DOIT couvrir les interactions entre agents IA et les APIs CI/CD.
- Les tests de contrat DOIVENT valider les interfaces entre Mirador et les systèmes surveillés.
- Toute logique de sécurité (authentification, autorisation, validation d'entrée) DOIT avoir
  une couverture de tests unitaires à 100 %.
- Les tests NE DOIVENT PAS utiliser de mocks pour les couches de sécurité critiques ;
  les tests d'intégration DOIVENT cibler les vraies interfaces.

**Rationale** : Dans un système MLOps où des agents prennent des décisions autonomes, les
régressions non détectées peuvent avoir des conséquences immédiates sur les pipelines de
production.

### V. Simplicité et maintenabilité

Mirador DOIT privilégier les solutions simples et lisibles. La complexité DOIT être justifiée
par un besoin réel et documenté, jamais par anticipation de besoins hypothétiques.

Règles non négociables :
- Principe YAGNI (You Aren't Gonna Need It) : aucune abstraction sans usage avéré.
- Chaque module ou agent DOIT avoir une responsabilité unique et clairement définie.
- La documentation DOIT être rédigée en français et maintenue synchronisée avec le code.
- Le code DOIT être lisible sans commentaires excessifs : les noms explicites priment.
- Toute dépendance externe DOIT être justifiée ; les dépendances transitives DOIVENT
  être auditées régulièrement.

**Rationale** : Un système MLOps complexe sans simplicité structurelle devient rapidement
ingérable. La maintenabilité garantit que les agents IA et les humains peuvent collaborer
efficacement sur la durée.

## Contraintes techniques MLOps

Ce projet évolue dans un contexte MLOps où les agents IA interagissent avec des systèmes
CI/CD tiers. Les contraintes suivantes s'appliquent à toute implémentation :

**Langages et outils** : Tout le code source, la documentation, les commentaires, les messages
de commit, et les interfaces utilisateur DOIVENT être rédigés en français.

**Intégrations CI/CD** : Les connecteurs vers des plateformes CI/CD (GitHub Actions, GitLab CI,
Jenkins, etc.) DOIVENT être isolés dans des modules d'adaptation dédiés et testés de façon
indépendante.

**Agents IA** : Les agents DOIVENT être sans état (stateless) entre les cycles d'observation.
L'état partagé entre agents DOIT transiter exclusivement par le système de stockage central,
jamais par des variables globales ou des effets de bord implicites.

**Gestion des erreurs** : Tout échec d'un agent DOIT être capturé, journalisé avec contexte
complet, et déclencher une alerte. Les erreurs silencieuses sont interdites.

**Performance** : Le temps de détection d'une anomalie de pipeline NE DOIT PAS dépasser
60 secondes après l'événement déclencheur. Les interventions automatiques LOW DOIVENT
s'exécuter en moins de 30 secondes.

## Flux de développement

Le développement de Mirador suit un processus structuré garantissant la cohérence entre
les principes constitutionnels et le code produit :

**Flux de développement standard** :
1. Chaque fonctionnalité DOIT débuter par une spécification (`/speckit-specify`).
2. Une branche dédiée DOIT être créée pour chaque fonctionnalité (`/speckit-git-feature`).
3. Le plan d'implémentation DOIT référencer les principes constitutionnels applicables.
4. Les tests DOIVENT être écrits et en échec avant toute implémentation (Principe IV).
5. Chaque pull request DOIT inclure une vérification de conformité constitutionnelle.
6. Les merges DOIVENT être approuvés par au moins un réviseur humain.

**Vérifications de conformité obligatoires (Constitution Check)** :
- [ ] Principe I : Les nouvelles surfaces d'attaque sont-elles identifiées et mitigées ?
- [ ] Principe II : Les nouveaux événements sont-ils journalisés avec corrélation ?
- [ ] Principe III : Les nouvelles interventions sont-elles classifiées par niveau de risque ?
- [ ] Principe IV : Les tests sont-ils écrits avant l'implémentation ?
- [ ] Principe V : La complexité ajoutée est-elle justifiée par un besoin réel ?

## Gouvernance

Cette constitution constitue la référence normative de Mirador. Elle prime sur toute pratique
informelle, convention d'équipe, ou préférence individuelle.

**Procédure d'amendement** :
- Toute proposition d'amendement DOIT être soumise sous forme de pull request documentée.
- Les amendements de type MAJOR (suppression ou redéfinition de principe) requièrent
  l'approbation de l'ensemble des contributeurs actifs.
- Les amendements de type MINOR (ajout de principe ou section) requièrent l'approbation
  d'au moins deux contributeurs.
- Les amendements de type PATCH (clarification, formulation) peuvent être approuvés par
  un seul contributeur.

**Politique de versionnement** :
- MAJOR : Suppression ou redéfinition incompatible d'un principe existant.
- MINOR : Ajout d'un nouveau principe ou d'une nouvelle section substantielle.
- PATCH : Clarifications, corrections orthographiques, précisions non sémantiques.

**Révision de conformité** : La conformité constitutionnelle DOIT être vérifiée à chaque
revue de code. Toute violation identifiée DOIT être documentée et résolue avant merge.

**Fichier de référence runtime** : Pour les directives opérationnelles détaillées, consulter
`CLAUDE.md` à la racine du projet.

**Version**: 1.0.0 | **Ratifiée**: 2026-05-29 | **Dernière révision**: 2026-05-29
