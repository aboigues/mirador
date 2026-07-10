"""Schéma SQLite du journal d'audit immuable.

Aucun UPDATE/DELETE n'est émis par le code : l'immutabilité est garantie au
niveau applicatif (writer unique append-only). Voir data-model.md § JournalÉvénement.
"""
from __future__ import annotations

import sqlite3

DDL_JOURNAL = """
CREATE TABLE IF NOT EXISTS journal_evenements (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    horodatage      TEXT    NOT NULL,
    correlation_id  TEXT    NOT NULL,
    type_evenement  TEXT    NOT NULL,
    niveau_risque   TEXT    NOT NULL,
    depot           TEXT    NOT NULL,
    workflow_run_id INTEGER,
    type_anomalie   TEXT,
    type_action     TEXT,
    acteur          TEXT    NOT NULL,
    statut          TEXT    NOT NULL,
    details         TEXT,
    resultat        TEXT
);
CREATE INDEX IF NOT EXISTS idx_journal_correlation
    ON journal_evenements (correlation_id);
"""


def initialiser_schema(conn: sqlite3.Connection) -> None:
    """Crée les tables et index si absents. Idempotent."""
    conn.executescript(DDL_JOURNAL)
