"""Tests d'intégration : authentification GitHub App (JWT RS256 + tokens d'installation).

Le JWT est réel (vraie paire de clés RSA, vraie signature vérifiée avec la clé
publique) — conforme au Principe IV (aucun mock de la couche sécurité). Seul
l'appel HTTP vers l'API GitHub (échange JWT → token d'installation) est stubé
via respx, car c'est la frontière externe.
"""
from datetime import datetime, timedelta, timezone

import httpx
import jwt
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from src.infrastructure.github.app_auth import GitHubAppAuth

APP_ID = "123456"
BASE_URL = "https://api.github.com"
INSTALLATION_ID = 98765


@pytest.fixture(scope="module")
def paire_cles():
    cle = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem_privee = cle.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    pem_publique = cle.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return pem_privee, pem_publique


class Horloge:
    """Horloge contrôlable pour tester le cache et la rotation des tokens."""

    def __init__(self, instant: datetime):
        self.instant = instant

    def __call__(self) -> datetime:
        return self.instant

    def avancer(self, secondes: float) -> None:
        self.instant += timedelta(seconds=secondes)


@pytest.fixture
def horloge():
    # Ancrée à l'heure réelle : les JWT générés sont validés par jwt.decode
    # contre l'horloge système, donc iat/exp doivent rester cohérents avec elle.
    return Horloge(datetime.now(timezone.utc))


@pytest.fixture
def auth(paire_cles, horloge):
    pem_privee, _ = paire_cles
    return GitHubAppAuth(APP_ID, pem_privee, base_url=BASE_URL, horloge=horloge)


class TestGenerationJWT:
    def test_jwt_verifiable_avec_cle_publique(self, auth, paire_cles):
        _, pem_publique = paire_cles
        token = auth.generer_jwt_app()
        claims = jwt.decode(token, pem_publique, algorithms=["RS256"])
        assert claims["iss"] == APP_ID

    def test_jwt_claims_temporels_conformes(self, auth, paire_cles, horloge):
        _, pem_publique = paire_cles
        claims = jwt.decode(auth.generer_jwt_app(), pem_publique, algorithms=["RS256"])
        maintenant = int(horloge().timestamp())
        assert claims["iat"] <= maintenant  # iat dans le passé (tolérance d'horloge)
        assert claims["exp"] > maintenant
        assert claims["exp"] - claims["iat"] <= 600  # max 10 min imposé par GitHub

    def test_jwt_rejete_par_mauvaise_cle(self, auth):
        autre = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        autre_pub = autre.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()
        with pytest.raises(jwt.InvalidSignatureError):
            jwt.decode(auth.generer_jwt_app(), autre_pub, algorithms=["RS256"])


def _route_token(expires_at: datetime, token: str, installation_id: int = INSTALLATION_ID):
    return respx.post(
        f"{BASE_URL}/app/installations/{installation_id}/access_tokens"
    ).mock(
        return_value=httpx.Response(
            201,
            json={"token": token, "expires_at": expires_at.isoformat().replace("+00:00", "Z")},
        )
    )


class TestTokenInstallation:
    @respx.mock
    async def test_obtenir_token_retourne_le_token(self, auth, horloge):
        expire = horloge() + timedelta(hours=1)
        _route_token(expire, "ghs_token_1")
        token = await auth.obtenir_token_installation(INSTALLATION_ID)
        assert token == "ghs_token_1"

    @respx.mock
    async def test_requete_porte_le_jwt_en_bearer(self, auth, horloge, paire_cles):
        _, pem_publique = paire_cles
        route = _route_token(horloge() + timedelta(hours=1), "ghs_token_1")
        await auth.obtenir_token_installation(INSTALLATION_ID)
        entete = route.calls.last.request.headers["Authorization"]
        assert entete.startswith("Bearer ")
        # Le JWT transmis est authentique et vérifiable
        claims = jwt.decode(entete[len("Bearer "):], pem_publique, algorithms=["RS256"])
        assert claims["iss"] == APP_ID

    @respx.mock
    async def test_token_mis_en_cache(self, auth, horloge):
        route = _route_token(horloge() + timedelta(hours=1), "ghs_token_1")
        await auth.obtenir_token_installation(INSTALLATION_ID)
        await auth.obtenir_token_installation(INSTALLATION_ID)
        assert route.call_count == 1  # un seul appel HTTP grâce au cache

    @respx.mock
    async def test_rotation_avant_expiry(self, auth, horloge):
        # Token expirant dans 60s ; la marge de rotation est de 30s
        route = _route_token(horloge() + timedelta(seconds=60), "ghs_token_1")
        await auth.obtenir_token_installation(INSTALLATION_ID)
        route.mock(return_value=httpx.Response(
            201,
            json={"token": "ghs_token_2",
                  "expires_at": (horloge() + timedelta(hours=1)).isoformat().replace("+00:00", "Z")},
        ))
        horloge.avancer(40)  # 60 - 40 = 20s restants < marge 30s → rotation
        token = await auth.obtenir_token_installation(INSTALLATION_ID)
        assert token == "ghs_token_2"
        assert route.call_count == 2

    @respx.mock
    async def test_tokens_distincts_par_installation(self, auth, horloge):
        _route_token(horloge() + timedelta(hours=1), "ghs_A", installation_id=111)
        _route_token(horloge() + timedelta(hours=1), "ghs_B", installation_id=222)
        assert await auth.obtenir_token_installation(111) == "ghs_A"
        assert await auth.obtenir_token_installation(222) == "ghs_B"

    @respx.mock
    async def test_erreur_http_remonte(self, auth, horloge):
        respx.post(
            f"{BASE_URL}/app/installations/{INSTALLATION_ID}/access_tokens"
        ).mock(return_value=httpx.Response(401, json={"message": "Bad credentials"}))
        with pytest.raises(httpx.HTTPStatusError):
            await auth.obtenir_token_installation(INSTALLATION_ID)
