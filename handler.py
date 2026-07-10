"""Points d'entrée Scaleway Serverless Functions.

Deux fonctions partagent ce package :
- handler.webhook     → récepteur HTTP des webhooks GitHub (→ file MnQ)
- handler.traitement  → consommateur de la file (→ pipeline)

Les handlers construisent leurs dépendances réelles depuis l'environnement
(secrets Scaleway chiffrés). Voir infra/scaleway/README.md.
"""
from src.gestionnaires.handlers import handler_traitement, handler_webhook


def webhook(event, context=None):
    return handler_webhook(event, context)


def traitement(event, context=None):
    return handler_traitement(event, context)
