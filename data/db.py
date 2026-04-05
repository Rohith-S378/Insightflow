"""
data/db.py
----------
SQLite database connection and table initialization.
Using SQLite for demo simplicity — swap to PostgreSQL for production
by changing the connection string in settings.py.
"""

import sqlite3
import json
from datetime import date
from pathlib import Path
from config.settings import DB_PATH


def get_connection() -> sqlite3.Connection:
    """Return a SQLite connection with row_factory for dict-like access."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # Allows col access by name: row["amount"]
    conn.execute("PRAGMA journal_mode=WAL")  # Better concurrent read performance
    return conn


def init_db():
    """
    Create all tables if they don't already exist.
    Safe to call multiple times (uses IF NOT EXISTS).
    """
    conn = get_connection()
    cursor = conn.cursor()

    # Transactions table — stores all normalized financial records
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id          TEXT PRIMARY KEY,
            amount      REAL NOT NULL,
            type        TEXT NOT NULL,
            due_date    TEXT NOT NULL,
            counterparty TEXT NOT NULL,
            source      TEXT NOT NULL,
            confidence  REAL DEFAULT 1.0,
            description TEXT DEFAULT '',
            is_recurring INTEGER DEFAULT 0,
            currency    TEXT DEFAULT 'INR',
            created_at  TEXT DEFAULT (datetime('now'))
        )
    """)

    # Vendors table — stores relationship profiles
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS vendors (
            name              TEXT PRIMARY KEY,
            relationship_type TEXT DEFAULT 'unknown',
            months_active     REAL DEFAULT 0.0,
            payment_history   TEXT DEFAULT 'unknown',
            allows_partial    INTEGER DEFAULT 0,
            has_grace_period  INTEGER DEFAULT 0,
            grace_days        INTEGER DEFAULT 0,
            notes             TEXT DEFAULT '',
            updated_at        TEXT DEFAULT (datetime('now'))
        )
    """)

    # Decisions table — stores the output of each analysis run
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS decisions (
            id              TEXT PRIMARY KEY,
            run_date        TEXT NOT NULL,
            financial_state TEXT NOT NULL,  -- JSON blob of FinancialState
            created_at      TEXT DEFAULT (datetime('now'))
        )
    """)

    conn.commit()
    conn.close()
    print(f"[DB] Database initialized at: {DB_PATH}")
