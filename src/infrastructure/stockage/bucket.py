"""Client de stockage objet (bucket Scaleway Object Storage, S3-compatible).

Le writer d'audit et les dépôts de lecture dépendent du protocole `ClientBucket`
et non d'une implémentation concrète : `ClientBucketS3` en production,
`ClientBucketMemoire` dans les tests (interface strictement identique).
"""
from __future__ import annotations

import os
from typing import Optional, Protocol, runtime_checkable


@runtime_checkable
class ClientBucket(Protocol):
    """Transport objet minimal utilisé par la couche stockage."""

    def telecharger(self, cle: str) -> Optional[bytes]:
        """Retourne le contenu de l'objet, ou None s'il n'existe pas."""
        ...

    def televerser(self, cle: str, donnees: bytes) -> None:
        """Écrit (ou remplace) l'objet identifié par `cle`."""
        ...


class ClientBucketMemoire:
    """Double en mémoire pour les tests. Interface identique à `ClientBucketS3`."""

    def __init__(self) -> None:
        self._objets: dict[str, bytes] = {}

    def telecharger(self, cle: str) -> Optional[bytes]:
        return self._objets.get(cle)

    def televerser(self, cle: str, donnees: bytes) -> None:
        self._objets[cle] = donnees


class ClientBucketS3:
    """Implémentation réelle sur Scaleway Object Storage (API S3, boto3)."""

    def __init__(
        self,
        nom_bucket: Optional[str] = None,
        endpoint_url: Optional[str] = None,
        region_name: Optional[str] = None,
    ) -> None:
        import boto3

        self._bucket = nom_bucket or os.environ["BUCKET_NAME"]
        endpoint = (
            endpoint_url
            if endpoint_url is not None
            else os.environ.get("S3_ENDPOINT_URL", "https://s3.fr-par.scw.cloud")
        )
        parametres: dict[str, str] = {
            "region_name": region_name or os.environ.get("SCW_REGION", "fr-par"),
        }
        # Clé IAM dédiée au bucket, passée explicitement. Absente (tests sous moto,
        # ou fonction pas encore reconfigurée), boto3 retombe sur sa chaîne de
        # credentials habituelle.
        cle, secret = os.environ.get("SCW_S3_ACCESS_KEY"), os.environ.get("SCW_S3_SECRET_KEY")
        if cle and secret:
            parametres["aws_access_key_id"] = cle
            parametres["aws_secret_access_key"] = secret
        # Endpoint vide → boto3 utilise l'endpoint AWS par défaut (utile sous moto).
        if endpoint:
            parametres["endpoint_url"] = endpoint
        self._s3 = boto3.client("s3", **parametres)

    def telecharger(self, cle: str) -> Optional[bytes]:
        from botocore.exceptions import ClientError

        try:
            reponse = self._s3.get_object(Bucket=self._bucket, Key=cle)
        except ClientError as err:
            if err.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                return None
            raise
        return reponse["Body"].read()

    def televerser(self, cle: str, donnees: bytes) -> None:
        self._s3.put_object(Bucket=self._bucket, Key=cle, Body=donnees)
