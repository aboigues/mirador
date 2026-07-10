"""Authentification GitHub App : JWT RS256 + tokens d'installation à durée limitée.

Flux GitHub App :
  1. Signer un JWT court (RS256) avec la clé privée de l'App → prouve l'identité de l'App.
  2. Échanger ce JWT contre un token d'installation via l'API GitHub (scopé à un dépôt).
  3. Mettre en cache le token par installation ; le renouveler 30s avant expiry.

Secrets : GITHUB_APP_ID, GITHUB_APP_PRIVATE_KEY (variables d'env chiffrées Scaleway).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

import httpx
import jwt
import structlog

log = structlog.get_logger(__name__)

_MARGE_ROTATION = timedelta(seconds=30)  # renouveler avant l'expiry réel
_DUREE_JWT_SECONDES = 540                # < 10 min imposé par GitHub
_DECALAGE_IAT_SECONDES = 60              # iat dans le passé (tolérance d'horloge)
_API_VERSION = "2022-11-28"


class GitHubAppAuth:
    def __init__(
        self,
        app_id: str,
        cle_privee_pem: str,
        *,
        base_url: str = "https://api.github.com",
        horloge: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._app_id = str(app_id)
        self._cle_privee = cle_privee_pem
        self._base_url = base_url
        self._horloge = horloge or (lambda: datetime.now(timezone.utc))
        # installation_id → (token, date d'expiration)
        self._cache: dict[int, tuple[str, datetime]] = {}

    @classmethod
    def depuis_env(cls) -> "GitHubAppAuth":
        return cls(os.environ["GITHUB_APP_ID"], os.environ["GITHUB_APP_PRIVATE_KEY"])

    def generer_jwt_app(self) -> str:
        """Génère un JWT RS256 signé prouvant l'identité de la GitHub App."""
        maintenant = int(self._horloge().timestamp())
        payload = {
            "iat": maintenant - _DECALAGE_IAT_SECONDES,
            "exp": maintenant + _DUREE_JWT_SECONDES,
            "iss": self._app_id,
        }
        return jwt.encode(payload, self._cle_privee, algorithm="RS256")

    async def obtenir_token_installation(self, installation_id: int) -> str:
        """Retourne un token d'installation valide (depuis le cache ou renouvelé)."""
        en_cache = self._cache.get(installation_id)
        if en_cache is not None:
            token, expire_le = en_cache
            if expire_le - self._horloge() > _MARGE_ROTATION:
                return token

        token, expire_le = await self._demander_token(installation_id)
        self._cache[installation_id] = (token, expire_le)
        log.info("github.token_renouvele",
                 installation_id=installation_id, expire_le=expire_le.isoformat())
        return token

    async def _demander_token(self, installation_id: int) -> tuple[str, datetime]:
        jwt_app = self.generer_jwt_app()
        async with httpx.AsyncClient(base_url=self._base_url) as client:
            reponse = await client.post(
                f"/app/installations/{installation_id}/access_tokens",
                headers={
                    "Authorization": f"Bearer {jwt_app}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": _API_VERSION,
                },
            )
        reponse.raise_for_status()
        donnees = reponse.json()
        expire_le = datetime.fromisoformat(donnees["expires_at"].replace("Z", "+00:00"))
        return donnees["token"], expire_le
