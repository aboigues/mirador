# Plan d'implémentation : Serveur MCP en lecture seule

**Branche** : `002-serveur-mcp` | **Date** : 2026-09-25 | **Spec** : [spec.md](spec.md)

**Entrée** : Spécification fonctionnelle depuis `specs/002-serveur-mcp/spec.md`

---

## Résumé

Un serveur Model Context Protocol, lancé en local par l'assistant du mainteneur (stdio),
expose cinq outils en lecture seule sur le journal d'audit et la configuration de
Mirador : `lister_depots`, `lister_anomalies`, `historique_anomalie`,
`verifier_integrite_journal`, `classer_echec`. Il réutilise le domaine existant
(`DepotJournal`, `Detecteur`, `ConfigDepots`) ; la vue « anomalie » est extraite de l'API
de consultation vers le domaine pour être partagée à l'identique. Aucun outil n'écrit :
l'approbation reste dans GitHub.

---

## Contexte technique

**Langage/Version** : Python 3.12 (identique au reste du dépôt)

**Dépendances principales** :
- `mcp 2.2.0` (nouvelle, extra optionnel `mcp`) : SDK officiel, `MCPServer`, transport stdio
- existantes réutilisées : `pydantic`, `structlog`, `boto3` (lecture bucket), `anthropic`
  (évaluation uniquement)

**Stockage** : lecture seule du journal SQLite `mirador.db` et de son empreinte
`mirador.db.sha256`, depuis le bucket Scaleway (clé IAM en lecture seule) ou un
répertoire local

**Tests** : pytest 9 + pytest-asyncio ; client MCP du SDK en mémoire (`Client(serveur)`)
et en sous-processus stdio ; `ClientBucketMemoire` + `WriterAudit` réels pour produire
les journaux de test ; évaluation avec le vrai modèle via `workflow_dispatch`

**Plateforme cible** : poste du mainteneur (Linux, macOS, WSL), lancé par Claude Code,
Claude Desktop ou tout client MCP

**Type de projet** : serveur MCP local (processus stdio) dans le dépôt existant

**Objectifs de performance** : consultation < 3 s sur un journal de 10 000 événements
(CS-006)

**Contraintes** :
- stdout réservé au protocole : journalisation structlog forcée sur stderr (research R3)
- aucune écriture, ni dans le journal, ni sur GitHub (EF-007)
- aucun secret dans les réponses ni les logs (EF-009)

**Périmètre/Échelle** : 1 à 5 dépôts, un utilisateur par processus

---

## Vérification constitutionnelle

*PORTE : Obligatoire avant la Phase 0 (recherche). Revérifiée après la Phase 1 (conception).*

- [x] **Principe I — Sécurité par conception** : nouvelle surface = entrées d'un
  assistant IA (potentiellement influencé par du contenu non fiable). Mitigations :
  aucun outil d'écriture ; validation des arguments par schéma (SDK) puis par le domaine
  (UUID, bornes de limite, dépôt configuré, taille d'extrait) ; clé IAM de lecture seule
  et adaptateur local incapable d'écrire ; exceptions imprévues masquées par le SDK, erreurs
  de stockage converties en messages neutres ; pas de transport réseau en v1 (stdio).
  Les motifs de règles s'appliquent à un extrait fourni par l'utilisateur : extrait borné
  à 20 000 caractères.
- [x] **Principe II — Observabilité** : chaque appel d'outil est journalisé
  (`mcp.outil.appel`, `mcp.outil.erreur`) avec horodatage ISO 8601, identifiant de
  corrélation propre à l'appel, durée, issue et paramètres assainis (l'extrait de log est
  réduit à sa longueur). Sortie JSON sur stderr.
- [x] **Principe III — Intervention contrôlée** : aucune intervention n'est ajoutée. Le
  serveur n'expose ni approbation, ni rejet, ni relance (EF-007) ; un test vérifie que la
  liste des outils est exactement la liste attendue et que tous portent
  `readOnlyHint=True`. Aucun rollback nécessaire (aucune mutation) ; CS-005 le vérifie
  par empreinte avant/après.
- [x] **Principe IV — TDD** : chaque outil a ses tests écrits et en échec avant
  implémentation (voir `tasks.md`). La vérification d'intégrité et la validation des
  entrées (logique de sécurité) sont testées sans mock : vrai `WriterAudit`, vraie base
  SQLite, vrai client MCP.
- [x] **Principe V — Simplicité** : un seul nouveau paquet (`src/interface_mcp/`), un
  adaptateur de lecture locale, une fonction de domaine déplacée (pas dupliquée). Pas de
  cache, pas de transport HTTP, pas d'authentification réseau : non nécessaires en stdio
  local (YAGNI). Nommage et documentation en français.

**Résultat** : aucune violation. Revérifié après conception : inchangé.

---

## Structure du projet

### Documentation (cette fonctionnalité)

```text
specs/002-serveur-mcp/
├── plan.md              # Ce fichier
├── research.md          # Décisions techniques (R1 à R9)
├── data-model.md        # Modèles de sortie des outils
├── quickstart.md        # Brancher le serveur sur Claude Code / Claude Desktop
├── contracts/
│   └── outils-mcp.md    # Contrat des 5 outils (entrées, sorties, erreurs)
├── checklists/
│   └── requirements.md
└── tasks.md             # Généré par /speckit-tasks
```

### Code source (racine du dépôt)

```text
src/
├── domaine/
│   └── vue_anomalies.py         # NOUVEAU : grouper_anomalies + statuts (extrait de api/routes.py)
├── api/
│   └── routes.py                # MODIFIÉ : importe la vue depuis le domaine
├── infrastructure/stockage/
│   ├── lecture_fichier.py       # NOUVEAU : ClientBucketLectureFichier (lecture seule)
│   └── integrite.py             # NOUVEAU : verifier_integrite(bucket, cle_bd)
└── interface_mcp/               # NOUVEAU
    ├── __init__.py
    ├── modeles.py               # Modèles Pydantic des sorties d'outils
    ├── outils.py                # Logique des 5 outils (fonctions pures + ToolError)
    └── serveur.py               # MCPServer, enregistrement des outils, journalisation, main()

tests/
├── unitaire/
│   ├── test_vue_anomalies.py
│   ├── test_integrite.py
│   ├── test_lecture_fichier.py
│   └── test_outils_mcp.py       # Logique des outils, validation des entrées
├── integration/
│   ├── test_serveur_mcp.py      # Client MCP en mémoire : liste, schémas, appels, erreurs
│   └── test_serveur_mcp_stdio.py # Sous-processus : stdout = JSON-RPC uniquement, CS-005, CS-006
└── eval_outils_mcp.py           # Évaluation CS-003 avec le vrai modèle (workflow_dispatch)

.github/workflows/
└── eval-outils-mcp.yml          # NOUVEAU : lance l'évaluation à la demande
```

**Décision de structure** : un paquet `src/interface_mcp/` parallèle à `src/api/` (deux
interfaces de lecture sur le même domaine). La séparation `outils.py` / `serveur.py`
permet de tester la logique sans le protocole, puis le protocole sans dupliquer la
logique.

---

## Suivi de complexité

Aucune violation constitutionnelle à justifier.
