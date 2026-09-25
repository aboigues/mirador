# Démarrage rapide : Serveur MCP de Mirador

Objectif (CS-001) : poser une première question à Mirador depuis ton assistant en moins
de 5 minutes.

## 1. Installer

```bash
git clone https://github.com/aboigues/mirador.git && cd mirador
python3.12 -m venv .venv && . .venv/bin/activate
pip install -e ".[mcp]"
```

## 2. Choisir la source du journal

**Option A — copie locale** (démonstration, analyse hors ligne) : placer `mirador.db` et
`mirador.db.sha256` dans un répertoire, puis utiliser `--journal-local <répertoire>`.

**Option B — stockage central** : créer une clé IAM Scaleway limitée à la **lecture** du
bucket Mirador, puis exporter :

```bash
export BUCKET_NAME=...            # bucket du journal
export SCALEWAY_S3_ACCESS_KEY=... # clé en lecture seule
export SCALEWAY_S3_SECRET_KEY=...
```

Dans les deux cas, la configuration des dépôts vient de `MIRADOR_DEPOTS` (même JSON que
les fonctions déployées) ou de `--config depots.json`.

## 3. Brancher sur Claude Code

```bash
claude mcp add mirador -- /chemin/vers/mirador/.venv/bin/mirador-mcp \
  --config /chemin/vers/depots.json --journal-local /chemin/vers/journal
```

Pour Claude Desktop, ajouter l'équivalent dans `claude_desktop_config.json`
(`command` = chemin de `mirador-mcp`, `args` = options ci-dessus).

## 4. Essayer

- « Quels dépôts Mirador surveille-t-il ? »
- « Quelles anomalies sont escaladées sur aboigues/k8t ? »
- « Montre-moi l'historique de la dernière anomalie. »
- « Le journal d'audit est-il intact ? »
- « Comment Mirador classerait-il cet échec sur main ? » (coller un extrait de log)

## 5. Vérifier

```bash
pytest tests/unitaire/test_outils_mcp.py tests/integration/test_serveur_mcp*.py -v
# Évaluation du choix des outils (nécessite ANTHROPIC_API_KEY) :
python -m tests.eval_outils_mcp
```
