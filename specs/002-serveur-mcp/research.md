# Recherche technique : Serveur MCP en lecture seule

**Branche** : `002-serveur-mcp` | **Date** : 2026-09-25 | **Spec** : [spec.md](spec.md)

Chaque décision a été vérifiée sur un prototype jetable avec le SDK réellement installé,
pas d'après la documentation seule.

---

## R1 — SDK MCP Python et API serveur

**Décision** : `mcp>=2.2.0` (dernière version stable sur PyPI au 2026-09-25, compatible
Python >= 3.10), classe `MCPServer` (`from mcp.server.mcpserver import MCPServer`),
outils déclarés par le décorateur `@serveur.tool(...)`, transport stdio (`serveur.run()`).

**Justification** : SDK officiel du protocole. En 2.x, `FastMCP` a été renommé
`MCPServer` ; l'import `mcp.server.fastmcp` lève une erreur explicite qui renvoie au guide
de migration. Le prototype confirme :
- un retour typé par un modèle Pydantic produit à la fois `structured_content` et un
  `output_schema` publié dans `list_tools` → contrat vérifiable côté client ;
- `ToolAnnotations(readOnlyHint=True, ...)` est transmis au client ;
- la validation des arguments par le schéma d'entrée est faite par le SDK (un entier
  attendu reçoit `"abc"` → erreur explicite `int_parsing`, sans exécuter l'outil).

**Alternatives écartées** :
- `mcp<2` (API `FastMCP`) : version majeure dépassée, aucune raison de démarrer dessus.
- Serveur bas niveau (`mcp.server.lowlevel`) : schémas à écrire à la main, sans gain ici.
- Bibliothèque tierce `fastmcp` : dépendance supplémentaire non justifiée (Principe V).

---

## R2 — Remontée des erreurs à l'assistant (EF-008, EF-009)

**Décision** : les erreurs attendues (identifiant invalide, dépôt non surveillé, limite
hors bornes, stockage inaccessible) sont levées sous forme de
`mcp.server.mcpserver.exceptions.ToolError` avec un message en français exploitable.
Toute autre exception reste non interceptée.

**Justification** : comportement vérifié sur le prototype :
- `ToolError("x négatif")` → `is_error=True`, texte « Error executing tool double: x négatif » ;
- `ValueError("x négatif")` → `is_error=True`, texte générique « Error executing tool
  double » : le message d'une exception imprévue reste côté serveur.

Ce second comportement est une protection utile pour EF-009 : une exception boto3 qui
contiendrait un nom de bucket ou un fragment d'identifiant n'atteint jamais l'assistant.
Les erreurs de stockage sont donc converties explicitement en `ToolError` avec un
message neutre (« journal inaccessible »), et le détail est journalisé sur stderr.

---

## R3 — Sortie standard réservée au protocole

**Décision** : le point d'entrée configure `structlog` pour écrire en JSON sur **stderr**
avant de démarrer le serveur. Un test de protocole en sous-processus vérifie que stdout
ne contient que des messages JSON-RPC après un appel à `classer_echec`.

**Justification** : Mirador n'appelle jamais `structlog.configure` ; la fabrique par défaut
est `PrintLoggerFactory`, qui écrit sur **stdout** (vérifié : `structlog 25.5.0`,
`get_config()['logger_factory']`). Or `Detecteur.detecter` journalise
`anomalie.detectee` à chaque classification : sans reconfiguration, chaque appel de
`classer_echec` injecterait une ligne de texte dans le flux stdio et casserait la session
MCP. Les fonctions serverless ne sont pas concernées (le point d'entrée MCP est séparé).

**Alternatives écartées** : configurer `structlog` globalement dans `src/__init__.py`
(modifierait le comportement des fonctions déployées, hors périmètre).

---

## R4 — Partage de la logique avec l'API de consultation (EF-003, CS-002)

**Décision** : déplacer `_grouper_anomalies` et la table des statuts de
`src/api/routes.py` vers un module de domaine `src/domaine/vue_anomalies.py`
(`grouper_anomalies`, `STATUT_PAR_EVENEMENT`). L'API et le serveur MCP l'importent tous
deux. Les tests existants de l'API servent de filet de non-régression.

**Justification** : CS-002 exige des résultats identiques ; une seule implémentation le
garantit par construction. Importer depuis `src/api/routes.py` aurait fait dépendre le
serveur MCP de FastAPI pour une fonction pure.

**Alternatives écartées** : dupliquer la fonction (dérive assurée) ; appeler l'API HTTP
depuis le serveur MCP (déploiement et authentification supplémentaires).

---

## R5 — Source du journal : stockage central ou fichier local (EF-011)

**Décision** : réutiliser le protocole `ClientBucket` existant.
- Stockage central : `ClientBucketS3` existant, avec une **clé IAM dédiée en lecture
  seule** sur le bucket (variables `SCALEWAY_S3_ACCESS_KEY` / `SCALEWAY_S3_SECRET_KEY`
  déjà lues par le client).
- Fichier local : nouvel adaptateur `ClientBucketLectureFichier` qui lit
  `<répertoire>/mirador.db` et `mirador.db.sha256`, et dont `televerser` lève
  systématiquement une erreur.

Sélection par option de ligne de commande : `--journal-local <répertoire>` ; sinon
stockage central.

**Justification** : `DepotJournal` et la vérification d'intégrité fonctionnent sans
modification sur les deux sources. La lecture seule est garantie deux fois : par les
droits de la clé IAM, et par un adaptateur local incapable d'écrire.

**Point d'attention** : `_ouvrir_lecture` appelle `initialiser_schema` sur une **copie
temporaire** téléchargée ; aucune écriture ne revient au stockage. La vérification
d'intégrité travaille sur les octets téléchargés, avant toute ouverture SQLite.

---

## R6 — Configuration des dépôts et des règles

**Décision** : réutiliser `charger_depots` / `ConfigDepots`, alimentés par la variable
`MIRADOR_DEPOTS` (même format JSON que les fonctions déployées) ou par
`--config <fichier.json>`.

**Justification** : aucune nouvelle source de configuration (hypothèse de la spec).
`DepotSurveille` ne contient aucun secret (identifiant, installation, seuils,
responsables) ; `lister_depots` n'en expose qu'un sous-ensemble.

---

## R7 — Classification d'un échec fourni (EF-006)

**Décision** : `classer_echec` construit un message `workflow_run` synthétique
(`conclusion`, `head_branch`, dates facultatives) et appelle `Detecteur.detecter` avec
les règles du dépôt demandé. L'extrait de log est tronqué à **20 000 caractères en
conservant la fin** (les causes d'échec sont en fin de log). Aucun événement n'est écrit.

**Justification** : réutilise exactement la logique de production (pas de
réimplémentation divergente). L'élévation sur branche protégée est rapportée en
comparant le niveau obtenu au niveau de base du type.

**Alternatives écartées** : réimplémenter les règles dans le module MCP (divergence).

---

## R8 — Évaluation du choix des outils par un assistant (CS-003)

**Décision** : script `tests/eval_outils_mcp.py`, sur le modèle de `replay_129.py` :
10 questions en langage naturel, chacune avec l'outil et les arguments attendus. Le
script démarre le serveur en mémoire sur un journal de test, convertit `list_tools` en
définitions d'outils pour l'API Messages, envoie chaque question au modèle et compare le
premier appel d'outil à l'attendu. Seuil : 9/10. Lancé par `workflow_dispatch`
(`eval-outils-mcp.yml`), comme les rejeux, car il nécessite `ANTHROPIC_API_KEY`.

**Modèle** : `claude-haiku-4-5` (celui du correcteur de Mirador). Évaluer les
descriptions sur le modèle le plus petit est le cas le plus exigeant : si Haiku choisit
correctement, un modèle plus grand le fera aussi.

**Justification** : les tests à doubles vérifient le câblage, pas la qualité des
descriptions ; seul un vrai modèle peut dire si un assistant comprend les outils
(même leçon que le post-mortem du 2026-07-17).

---

## R9 — Emballage et dépendances

**Décision** : extra optionnel `mcp = ["mcp>=2.2.0"]` dans `pyproject.toml`, ajouté aussi
à l'extra `dev` (la CI installe `.[dev]`). Point d'entrée `mirador-mcp =
"src.interface_mcp.serveur:main"`. `requirements.txt` (fonctions serverless) inchangé.

**Justification** : les fonctions déployées n'ont pas besoin du SDK MCP ; ne pas alourdir
leur paquet vendorisé. Le paquet s'appelle `interface_mcp` et non `mcp` pour éviter
toute ambiguïté avec le paquet `mcp` du SDK.
