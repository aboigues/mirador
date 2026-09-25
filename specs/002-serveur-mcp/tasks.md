---
description: "Liste des tâches : serveur MCP en lecture seule"
---

# Tâches : Serveur MCP en lecture seule

**Entrée** : documents de conception de `specs/002-serveur-mcp/`

**Prérequis** : plan.md, spec.md, research.md, data-model.md, contracts/outils-mcp.md

**Tests** : obligatoires (Principe IV, TDD). Dans chaque phase, les tâches de test sont
écrites et **en échec** avant les tâches d'implémentation correspondantes.

## Format : `[ID] [P?] [Scénario] Description`

- **[P]** : parallélisable (fichiers distincts, sans dépendance)
- **[US1]…[US4]** : scénario de la spec (US1 consulter, US2 historique, US3 intégrité,
  US4 classification)

---

## Phase 1 : Mise en place

- [ ] T001 Ajouter l'extra `mcp = ["mcp>=2.2.0"]`, l'ajouter à l'extra `dev`, et le point
  d'entrée `mirador-mcp = "src.interface_mcp.serveur:main"` dans `pyproject.toml`
- [ ] T002 Créer le paquet `src/interface_mcp/__init__.py`

---

## Phase 2 : Fondations (bloquant pour tous les scénarios)

- [ ] T003 [P] Test : `grouper_anomalies` et la table des statuts dans
  `tests/unitaire/test_vue_anomalies.py` (regroupement par corrélation, statut inféré du
  dernier événement, ordre conservé)
- [ ] T004 Déplacer `_grouper_anomalies` et `_STATUT_PAR_EVENEMENT` de
  `src/api/routes.py` vers `src/domaine/vue_anomalies.py` (`grouper_anomalies`,
  `STATUT_PAR_EVENEMENT`) ; `routes.py` les importe. `tests/integration/test_api_consultation.py`
  doit rester vert sans modification
- [ ] T005 [P] Test : `ClientBucketLectureFichier` dans
  `tests/unitaire/test_lecture_fichier.py` (lecture d'une clé présente, `None` si absente,
  `televerser` lève toujours, refus d'une clé qui sort du répertoire)
- [ ] T006 Implémenter `src/infrastructure/stockage/lecture_fichier.py`
- [ ] T007 [P] Modèles de sortie Pydantic dans `src/interface_mcp/modeles.py`
  (data-model.md)
- [ ] T008 Squelette `src/interface_mcp/serveur.py` : `creer_serveur(config, bucket)`,
  décorateur de journalisation des appels sur stderr (corrélation, durée, issue,
  paramètres assainis), `main()` avec `--config` et `--journal-local`, configuration
  structlog vers stderr **avant** tout import qui journalise
- [ ] T009 [P] Test d'intégration `tests/integration/test_serveur_mcp.py::test_liste_exacte_des_outils`
  : exactement les 5 outils du contrat, tous `readOnlyHint=True`,
  `destructiveHint=False` (EF-007)

**Point de contrôle** : fondations prêtes, T009 en échec tant que les outils manquent.

---

## Phase 3 : US1 — Consulter les anomalies d'un dépôt (P1) MVP

**Objectif** : `lister_depots` et `lister_anomalies`.

**Test indépendant** : sur un journal de test, un client MCP liste les dépôts puis les
anomalies filtrées et obtient exactement l'attendu.

- [ ] T010 [P] [US1] Tests unitaires dans `tests/unitaire/test_outils_mcp.py` :
  `lister_depots` (sans `installation_id` ni `responsables`), `lister_anomalies`
  (filtres statut et niveau, limite 1 à 200, dépôt non surveillé → `ToolError`, dépôt sans
  événement → liste vide, journal inaccessible → `ToolError` neutre)
- [ ] T011 [P] [US1] Test d'intégration CS-002 dans `tests/integration/test_serveur_mcp.py` :
  mêmes anomalies via le client MCP et via l'API de consultation sur le même journal
- [ ] T012 [US1] Implémenter `lister_depots` et `lister_anomalies` dans
  `src/interface_mcp/outils.py` et les enregistrer dans `serveur.py`

---

## Phase 4 : US2 — Historique d'une anomalie (P1)

- [ ] T013 [P] [US2] Tests unitaires `historique_anomalie` : ordre chronologique,
  acteurs, `details` et `delivery_id` exclus, UUID invalide → `ToolError`, UUID inconnu →
  `ToolError`
- [ ] T014 [US2] Implémenter `historique_anomalie` dans `outils.py` et l'enregistrer

---

## Phase 5 : US3 — Intégrité du journal (P2)

- [ ] T015 [P] [US3] Tests `tests/unitaire/test_integrite.py` avec un vrai `WriterAudit` :
  INTÈGRE (+ nombre d'événements), ALTÉRÉ après modification d'un octet (deux empreintes
  renvoyées), NON_VÉRIFIABLE sans journal, NON_VÉRIFIABLE sans empreinte
- [ ] T016 [US3] Implémenter `src/infrastructure/stockage/integrite.py`
  (`verifier_integrite(bucket, cle_bd)`) puis l'outil `verifier_integrite_journal`

---

## Phase 6 : US4 — Classer un échec (P3)

- [ ] T017 [P] [US4] Tests unitaires `classer_echec` : règle active reconnue → LOW + nom
  de règle ; sans règle sur `main` → niveau de base élevé d'un cran ; conclusion
  `success` → pas d'anomalie ; extrait de 30 000 caractères → tronqué en gardant la fin ;
  durée négative et branche vide → `ToolError` ; aucun événement écrit
- [ ] T018 [US4] Implémenter `classer_echec` dans `outils.py` (message `workflow_run`
  synthétique + `Detecteur`) et l'enregistrer

---

## Phase 7 : Transversal

- [ ] T019 Test `tests/integration/test_serveur_mcp_stdio.py` : serveur lancé en
  sous-processus via `mirador-mcp --journal-local` ; après appel de `classer_echec`,
  stdout ne contient que du JSON-RPC (R3) ; empreinte du journal identique avant et après
  un appel de chaque outil (CS-005) ; `lister_anomalies` sur 10 000 événements en moins de
  3 s (CS-006)
- [ ] T020 [P] Évaluation `tests/eval_outils_mcp.py` (10 questions, seuil 9/10,
  `claude-haiku-4-5`) et workflow `.github/workflows/eval-outils-mcp.yml`
  (`workflow_dispatch`, secret `ANTHROPIC_API_KEY`)
- [ ] T021 [P] Documentation : section « Serveur MCP » dans `README.md` (anglais, noms
  d'outils expliqués) et `README.fr.md`
- [ ] T022 Lancer toute la suite (`pytest tests/unitaire tests/integration`) et la
  vérification de conformité constitutionnelle avant la PR

---

## Dépendances

- Phase 1 → Phase 2 → US1 → (US2, US3, US4 dans n'importe quel ordre) → Phase 7.
- T004 avant T011 et T012 (vue partagée). T006 avant T019. T008 avant toute tâche
  d'enregistrement d'outil.

## Parallélisme

- Phase 2 : T003, T005, T007 en parallèle.
- Après US1 : T013, T015, T017 (tests) en parallèle, puis leurs implémentations.

## Stratégie

MVP = Phases 1 à 3 (un assistant peut déjà consulter les anomalies). Puis US2 (valeur
principale d'audit), US3, US4, et la phase transversale qui porte l'évaluation.
