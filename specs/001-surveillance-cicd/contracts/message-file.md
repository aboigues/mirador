# Contrat : Message interne file Scaleway MnQ

**Type** : Message SQS JSON (format interne Mirador)
**Producteur** : `gestionnaires/webhook.py` (après validation HMAC)
**Consommateur** : `gestionnaires/traitement.py` (déclenché par SQS)

---

## Format du message

```json
{
  "version": "1",
  "delivery_id": "550e8400-e29b-41d4-a716-446655440000",
  "depot": "aboigues/k8t",
  "workflow_run_id": 12345678,
  "workflow_nom": "CI",
  "conclusion": "failure",
  "head_branch": "main",
  "head_sha": "abc123def456",
  "run_started_at": "2026-05-29T10:00:00Z",
  "run_completed_at": "2026-05-29T10:05:00Z",
  "jobs_url": "https://api.github.com/repos/aboigues/k8t/actions/runs/12345678/jobs",
  "html_url": "https://github.com/aboigues/k8t/actions/runs/12345678",
  "recu_le": "2026-05-29T10:05:03Z"
}
```

## Champs obligatoires

| Champ | Type | Description |
|-------|------|-------------|
| `version` | string | Version du format de message (actuellement `"1"`) |
| `delivery_id` | UUID | Repris de `X-GitHub-Delivery` pour déduplication |
| `depot` | string | Format `proprietaire/depot` |
| `workflow_run_id` | integer | ID GitHub du run |
| `conclusion` | string | `failure`, `success`, `cancelled`, `timed_out`, `skipped` |
| `recu_le` | datetime ISO 8601 | Horodatage de réception du webhook |

## Comportement de la file

- **Queue principale** : `mirador-webhooks`
- **VisibilityTimeout** : 300s (temps max de traitement)
- **RetentionPeriod** : 86400s (24h)
- **RedrivePolicy** : maxReceiveCount = 3 → DLQ après 3 échecs
- **Dead-letter queue** : `mirador-webhooks-dlq`

## Déduplication

Le `delivery_id` (UUID fourni par GitHub par livraison) est utilisé pour détecter
et ignorer les doublons. Si un message avec le même `delivery_id` a déjà été traité
avec succès, le traitement est skippé et le message supprimé de la file.
