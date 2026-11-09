"""Tests d'intégration : sonde de l'état de GitHub Actions (githubstatus.com).

Sert à distinguer un run mort-né dû à une panne GitHub d'un workflow invalide.
Format réel relevé le 2026-11-09 pendant l'incident « Actions and Checks API ».
"""
import httpx
import pytest
import respx

from src.infrastructure.github.statut import URL_COMPOSANTS, actions_degrade


def _composants(statut_actions):
    composants = [
        {"name": "Git Operations", "status": "operational", "group": False},
        {"name": "Webhooks", "status": "degraded_performance", "group": False},
    ]
    if statut_actions is not None:
        composants.append({"name": "Actions", "status": statut_actions, "group": False})
    return {"components": composants}


@pytest.mark.parametrize("statut", ["major_outage", "partial_outage", "degraded_performance"])
@respx.mock
async def test_actions_degrade(statut):
    respx.get(URL_COMPOSANTS).mock(return_value=httpx.Response(200, json=_composants(statut)))
    assert await actions_degrade() is True


@respx.mock
async def test_actions_operationnel():
    # Un autre composant dégradé (Webhooks) ne compte pas.
    respx.get(URL_COMPOSANTS).mock(return_value=httpx.Response(200, json=_composants("operational")))
    assert await actions_degrade() is False


@respx.mock
async def test_composant_absent_vaut_operationnel():
    respx.get(URL_COMPOSANTS).mock(return_value=httpx.Response(200, json=_composants(None)))
    assert await actions_degrade() is False


@respx.mock
async def test_erreur_http_remonte():
    # La décision « dans le doute, chemin normal » appartient au traitement.
    respx.get(URL_COMPOSANTS).mock(return_value=httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        await actions_degrade()
