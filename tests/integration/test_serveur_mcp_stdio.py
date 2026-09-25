"""Tests d'intégration : serveur MCP lancé comme un vrai processus stdio.

- stdout ne transporte que du JSON-RPC (research R3). Le client du SDK tolère une
  ligne parasite sans erreur : on duplique donc la sortie du serveur avec `tee`
  pour inspecter chaque ligne.
- Aucune séquence d'appels ne modifie le journal (CS-005).
- Consultation d'un journal de 10 000 événements en moins de 3 s (CS-006).
"""
import hashlib
import json
import shlex
import sys
import time
from pathlib import Path
from uuid import uuid4

from mcp.client import Client
from mcp.client.client import StdioServerParameters

from src.domaine.journal import EvenementJournal, TypeEvenement
from src.infrastructure.stockage.bucket import ClientBucketMemoire
from src.infrastructure.stockage.writer import WriterAudit

RACINE = Path(__file__).resolve().parents[2]
K8T = "aboigues/k8t"
CONFIG = [{
    "identifiant_github": K8T,
    "installation_id": 1,
    "responsables": ["alice"],
    "regles": [{
        "nom": "reseau-transitoire",
        "description": "Coupure réseau",
        "pattern_log": "ECONNRESET",
        "action_recommandee": "RELANCE",
        "origine": "MANUELLE",
    }],
}]


def _preparer(tmp_path: Path, nombre_anomalies: int) -> tuple[Path, Path]:
    bucket = ClientBucketMemoire()
    evenements = []
    for i in range(nombre_anomalies):
        corr = uuid4()
        for type_evenement in (TypeEvenement.DETECTION, TypeEvenement.ESCALADE):
            evenements.append(EvenementJournal(
                correlation_id=corr, type_evenement=type_evenement, niveau_risque="HIGH",
                depot=K8T, acteur="mirador-agent", statut="SUCCÈS", workflow_run_id=i,
                type_anomalie="ÉCHEC",
            ))
    WriterAudit(bucket, cle_bd="mirador.db").ecrire(evenements)

    journal = tmp_path / "journal"
    journal.mkdir()
    for cle in ("mirador.db", "mirador.db.sha256"):
        (journal / cle).write_bytes(bucket.telecharger(cle))
    config = tmp_path / "depots.json"
    config.write_text(json.dumps(CONFIG), encoding="utf-8")
    return journal, config


def _parametres(journal: Path, config: Path, sortie: Path) -> StdioServerParameters:
    commande = " ".join(shlex.quote(p) for p in [
        sys.executable, "-m", "src.interface_mcp.serveur",
        "--config", str(config), "--journal-local", str(journal),
    ])
    return StdioServerParameters(
        command="sh",
        args=["-c", f"{commande} | tee {shlex.quote(str(sortie))}"],
        cwd=str(RACINE),
    )


def _empreinte(journal: Path) -> str:
    return hashlib.sha256((journal / "mirador.db").read_bytes()).hexdigest()


async def test_stdout_ne_contient_que_du_json_rpc_et_le_journal_est_intact(tmp_path):
    journal, config = _preparer(tmp_path, nombre_anomalies=3)
    sortie = tmp_path / "stdout.jsonl"
    avant = _empreinte(journal)

    async with Client(_parametres(journal, config, sortie)) as client:
        assert not (await client.call_tool("lister_depots", {})).is_error
        anomalies = (await client.call_tool("lister_anomalies", {"depot": K8T})).structured_content
        corr = anomalies["anomalies"][0]["correlation_id"]
        assert not (await client.call_tool("historique_anomalie", {"correlation_id": corr})).is_error
        integrite = await client.call_tool("verifier_integrite_journal", {})
        assert integrite.structured_content["issue"] == "INTÈGRE"
        # Le Détecteur journalise à chaque classification : c'est l'appel à risque.
        classement = await client.call_tool("classer_echec", {
            "depot": K8T, "conclusion": "failure", "branche": "main", "extrait_log": "ECONNRESET",
        })
        assert classement.structured_content["regle_declenchee"] == "reseau-transitoire"

    lignes = [l for l in sortie.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert lignes
    for ligne in lignes:
        assert json.loads(ligne).get("jsonrpc") == "2.0", ligne
    assert _empreinte(journal) == avant


async def test_consultation_de_10_000_evenements_en_moins_de_3_secondes(tmp_path):
    journal, config = _preparer(tmp_path, nombre_anomalies=5_000)

    async with Client(_parametres(journal, config, tmp_path / "stdout.jsonl")) as client:
        debut = time.perf_counter()
        resultat = await client.call_tool("lister_anomalies", {"depot": K8T, "limite": 200})
        duree = time.perf_counter() - debut

    assert resultat.structured_content["total"] == 200
    assert duree < 3, f"{duree:.2f} s"
