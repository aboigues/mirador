# Contrat : Webhook entrant GitHub → Mirador

**Type** : Webhook HTTP POST entrant
**Émetteur** : GitHub (déclenché par événements `workflow_run` et `issue_comment`)
**Récepteur** : Mirador (`gestionnaires/webhook.py`)

---

## Point d'entrée

```
POST /webhooks/github
Content-Type: application/json
X-GitHub-Event: workflow_run | issue_comment
X-Hub-Signature-256: sha256=<hmac-sha256-hex>
X-GitHub-Delivery: <uuid-livraison>
```

## Validation obligatoire (avant tout traitement)

1. Vérifier présence de `X-Hub-Signature-256`
2. Calculer `hmac.new(WEBHOOK_SECRET, body, sha256).hexdigest()`
3. Comparer avec `hmac.compare_digest()` → HTTP 403 si invalide
4. Vérifier `X-GitHub-Event` dans `{"workflow_run", "issue_comment"}` → HTTP 204 sinon

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

---

## Payload attendu (événement `issue_comment`)

Déclenché quand un responsable commente sur une GitHub Issue ouverte par Mirador.

```json
{
  "action": "created",
  "issue": {
    "number": 42,
    "title": "[Mirador] Anomalie HIGH — CI sur aboigues/k8t",
    "labels": [{ "name": "mirador" }, { "name": "high" }],
    "body": "..."
  },
  "comment": {
    "id": 987654,
    "user": { "login": "aboigues" },
    "body": "/approuver"
  },
  "repository": {
    "full_name": "aboigues/k8t"
  }
}
```

## Traitement de l'événement `issue_comment`

1. Ignorer si l'issue n'a pas le label `mirador` → HTTP 204
2. Ignorer si `action != "created"` → HTTP 204
3. Ignorer si l'auteur du commentaire n'est pas dans `DépôtSurveillé.responsables` → HTTP 204
4. Parser la commande :
   - Corps commence par `/approuver` → déclencher l'approbation
   - Corps commence par `/rejeter` → extraire le motif (le reste du commentaire)
   - Autre → ignorer (HTTP 204)
5. Extraire l'`anomalie_id` depuis le corps de l'issue (champ `<!-- mirador:anomalie_id:UUID -->`)
6. Enqueuer dans `mirador-writes` + déclencher le pipeline de traitement
