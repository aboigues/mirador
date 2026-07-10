"""Garde-fou : les points d'entrée Scaleway sont importables et bien câblés.

Un import cassé (mauvais nom, dépendance manquante) ne se verrait qu'au
déploiement — ce test le rattrape avant.
"""
from pathlib import Path

import handler

_RACINE = Path(__file__).resolve().parents[2]


def test_points_entree_sont_callables():
    assert callable(handler.webhook)
    assert callable(handler.traitement)


def test_requirements_couvre_les_deps_runtime():
    contenu = (_RACINE / "requirements.txt").read_text(encoding="utf-8").lower()
    for dep in ("pydantic", "httpx", "pyjwt", "boto3", "anthropic", "structlog"):
        assert dep in contenu, f"dépendance runtime manquante dans requirements.txt : {dep}"
