"""Tests unitaires : les runs issus d'un fork ne sont jamais traités.

Sur un dépôt surveillé public, n'importe qui peut ouvrir une PR depuis un fork :
le run qu'elle déclenche produit des logs contrôlés par un inconnu. Les confier
au correcteur, c'est payer l'analyse et s'exposer à une injection de prompt
(voir SECURITY.md). Seuls les runs dont le code vient du dépôt surveillé
lui-même — `head_repository` identique à `repository` — sont traités.
"""
import hashlib
import hmac
import json

from src.gestionnaires.webhook import traiter_webhook_brut

SECRET = "secret-forks"
DEPOT = "aboigues/kubernetes-formation"

_ABSENT = object()


def _payload(head_repository=_ABSENT) -> dict:
    run = {
        "id": 42,
        "name": "CI",
        "conclusion": "failure",
        "head_branch": "main",
        "head_sha": "abc123",
    }
    if head_repository is not _ABSENT:
        run["head_repository"] = head_repository
    return {"action": "completed", "workflow_run": run, "repository": {"full_name": DEPOT}}


def _traiter(payload: dict):
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return traiter_webhook_brut(body, "workflow_run", signature, "d-1", SECRET, lambda _d: [])


def test_run_du_depot_lui_meme_est_traite():
    code, message = _traiter(_payload({"full_name": DEPOT, "fork": False}))
    assert code == 202
    assert message["depot"] == DEPOT


def test_run_venant_d_un_fork_est_ignore():
    code, message = _traiter(_payload({"full_name": "inconnu/kubernetes-formation", "fork": True}))
    assert code == 204
    assert message is None


def test_fork_se_reclamant_non_fork_reste_ignore():
    # Seule l'identité du dépôt de tête compte, pas le drapeau `fork`.
    code, message = _traiter(_payload({"full_name": "inconnu/kubernetes-formation", "fork": False}))
    assert code == 204
    assert message is None


def test_depot_de_tete_nul_est_ignore():
    # GitHub envoie `null` quand le fork à l'origine du run a été supprimé.
    code, message = _traiter(_payload(None))
    assert code == 204
    assert message is None


def test_depot_de_tete_absent_est_ignore():
    # Provenance inconnue : on ne traite pas (fail-safe).
    code, message = _traiter(_payload())
    assert code == 204
    assert message is None


def test_comparaison_insensible_a_la_casse():
    # Les noms de dépôts GitHub ne sont pas sensibles à la casse.
    code, _ = _traiter(_payload({"full_name": "Aboigues/Kubernetes-Formation"}))
    assert code == 202


def test_run_non_termine_d_un_fork_reste_ignore_sans_erreur():
    payload = _payload({"full_name": "inconnu/kubernetes-formation"})
    payload["action"] = "requested"
    code, message = _traiter(payload)
    assert code == 204
    assert message is None
