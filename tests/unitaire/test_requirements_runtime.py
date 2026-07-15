"""Garde-fou : les dépendances runtime vendorées couvrent les besoins réels.

Certaines dépendances sont invisibles aux tests (le venv de dev les fournit déjà)
mais indispensables au runtime des fonctions Scaleway, où seules les libs listées
dans requirements.txt sont vendorées dans le zip. Ce test verrouille les pièges
déjà rencontrés en production.
"""
import re
from pathlib import Path

import pytest

_REQUIREMENTS = Path(__file__).resolve().parents[2] / "requirements.txt"


@pytest.fixture(scope="module")
def lignes() -> list[str]:
    txt = _REQUIREMENTS.read_text(encoding="utf-8")
    return [l.strip() for l in txt.splitlines() if l.strip() and not l.lstrip().startswith("#")]


def test_signature_rs256_disponible(lignes):
    """L'auth GitHub App signe un JWT RS256 → cryptography DOIT être vendorée.

    Soit explicitement (`cryptography`), soit via l'extra `PyJWT[crypto]`. Sans
    elle, PyJWT lève NotImplementedError au runtime et la fonction traitement
    plante avant tout traitement.
    """
    joint = "\n".join(lignes)
    a_cryptography = bool(re.search(r"(?im)^\s*cryptography\b", joint))
    a_extra_crypto = bool(re.search(r"(?i)pyjwt\[[^\]]*\bcrypto\b[^\]]*\]", joint))
    assert a_cryptography or a_extra_crypto, (
        "requirements.txt doit fournir cryptography (via `PyJWT[crypto]` ou "
        "`cryptography`) pour la signature JWT RS256 de l'auth GitHub App."
    )
