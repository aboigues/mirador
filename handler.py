"""Points d'entrée Scaleway Serverless Functions.

- handler.webhook     → récepteur HTTP des webhooks GitHub (→ file MnQ)
- handler.traitement  → consommateur de la file (→ pipeline)

Les dépendances sont vendorées en wheels musllinux (runtime Scaleway = Alpine/musl).
"""
from src.gestionnaires.handlers import handler_traitement, handler_webhook


def webhook(event, context=None):
    return handler_webhook(event, context)


def traitement(event, context=None):
    return handler_traitement(event, context)
