"""Garde-fou : le manifest GitHub App reste aligné avec les besoins réels du code.

Si une permission ou un événement disparaît du manifest, une capacité du pipeline
casserait en production (relance, PR, escalade, réception des commandes). Ce test
verrouille le contrat entre le manifest et les intentions du code.
"""
import json
from pathlib import Path

import pytest

_MANIFEST = Path(__file__).resolve().parents[2] / "infra" / "github-app" / "manifest.json"


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads(_MANIFEST.read_text(encoding="utf-8"))


def test_manifest_est_un_json_valide(manifest):
    assert isinstance(manifest, dict)
    assert manifest["name"]


def test_app_privee(manifest):
    # Un agent d'automatisation ne doit pas être une App publique
    assert manifest["public"] is False


def test_evenements_requis(manifest):
    # workflow_run : détection ; issue_comment : commandes /approuver /rejeter
    assert set(manifest["default_events"]) == {"workflow_run", "issue_comment"}


@pytest.mark.parametrize(
    "permission,niveau_min,raison",
    [
        ("metadata", "read", "accès de base à l'API"),
        ("checks", "read", "lecture de l'état des workflows (détection)"),
        ("actions", "write", "relance de workflow"),
        ("contents", "write", "création de branche pour les PR de correctif"),
        ("pull_requests", "write", "ouverture des PR de correctif"),
        ("issues", "write", "escalade + validation humaine via Issue"),
    ],
)
def test_permissions_minimales(manifest, permission, niveau_min, raison):
    permissions = manifest["default_permissions"]
    assert permission in permissions, f"permission manquante : {permission} ({raison})"
    if niveau_min == "write":
        assert permissions[permission] == "write", f"{permission} doit être write ({raison})"
    else:
        assert permissions[permission] in {"read", "write"}, raison


def test_webhook_configure(manifest):
    hook = manifest["hook_attributes"]
    assert hook["active"] is True
    assert hook["url"].endswith("/webhooks/github")
