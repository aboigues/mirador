# Contrat : Webhook entrant GitHub → Mirador

**Type** : Webhook HTTP POST entrant
**Émetteur** : GitHub (déclenché par événements `workflow_run`)
**Récepteur** : Mirador (`gestionnaires/webhook.py`)

---

## Point d'entrée

```
POST /webhooks/github
Content-Type: application/json
X-GitHub-Event: workflow_run
X-Hub-Signature-256: sha256=<hmac-sha256-hex>
X-GitHub-Delivery: <uuid-livraison>
```

## Validation obligatoire (avant tout traitement)

1. Vérifier présence de `X-Hub-Signature-256`
2. Calculer `hmac.new(WEBHOOK_SECRET, body, sha256).hexdigest()`
3. Comparer avec `hmac.compare_digest()` → HTTP 403 si invalide
4. Vérifier `X-GitHub-Event == "workflow_run"` → HTTP 204 (ignorer) sinon

## Payload attendu (événement `workflow_run`)

```json
{
  "action": "completed",
  "workflow_run": {
    "id": 12345678,
    "name": "CI",
    "head_branch": "main",
    "head_sha": "abc123",
    "status": "completed",
    "conclusion": "failure",
    "created_at": "2026-05-29T10:00:00Z",
    "updated_at": "2026-05-29T10:05:00Z",
    "html_url": "https://github.com/aboigues/k8t/actions/runs/12345678",
    "jobs_url": "https://api.github.com/repos/aboigues/k8t/actions/runs/12345678/jobs"
  },
  "repository": {
    "id": 999,
    "full_name": "aboigues/k8t",
    "owner": { "login": "aboigues" }
  }
}
```

## Réponses

| Code | Condition |
|------|-----------|
| `202 Accepted` | Webhook valide, message enqueué dans la file MnQ |
| `204 No Content` | Webhook valide mais type d'événement ignoré |
| `400 Bad Request` | Payload JSON malformé |
| `403 Forbidden` | Signature HMAC invalide ou absente |
| `404 Not Found` | Dépôt non configuré dans Mirador (ne pas révéler l'existence) |

**Note sécurité** : Le corps de la réponse 403 NE DOIT PAS révéler le secret attendu
ni le hash calculé.
