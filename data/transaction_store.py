"""
data/transaction_store.py
--------------------------
CRUD operations for normalized transactions.
"""

from datetime import date
from data.db import get_connection
from data.models import Transaction


def save_transaction(txn: Transaction):
    """Insert a transaction. Ignores duplicates (same id)."""
    conn = get_connection()
    conn.execute("""
        INSERT OR IGNORE INTO transactions
        (id, amount, type, due_date, counterparty, source, confidence,
         description, is_recurring, currency)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        txn.id, txn.amount, txn.type,
        txn.due_date.isoformat(),
        txn.counterparty, txn.source,
        txn.confidence, txn.description,
        int(txn.is_recurring), txn.currency
    ))
    conn.commit()
    conn.close()


def save_transactions(txns: list[Transaction]):
    """Bulk save a list of transactions."""
    for txn in txns:
        save_transaction(txn)


def get_transactions(type_filter: str | None = None,
                     from_date: date | None = None,
                     to_date: date | None = None) -> list[Transaction]:
    """
    Fetch transactions with optional filters.
    type_filter: "payable" | "receivable" | "balance_snapshot" | None (all)
    """
    conn = get_connection()
    query = "SELECT * FROM transactions WHERE 1=1"
    params = []

    if type_filter:
        query += " AND type = ?"
        params.append(type_filter)
    if from_date:
        query += " AND due_date >= ?"
        params.append(from_date.isoformat())
    if to_date:
        query += " AND due_date <= ?"
        params.append(to_date.isoformat())

    query += " ORDER BY due_date ASC"
    rows = conn.execute(query, params).fetchall()
    conn.close()

    return [_row_to_txn(r) for r in rows]


def get_latest_balance() -> float:
    """
    Return the most recent balance snapshot amount.
    Falls back to 0.0 if no balance record exists.
    """
    conn = get_connection()
    row = conn.execute("""
        SELECT amount FROM transactions
        WHERE type = 'balance_snapshot'
        ORDER BY due_date DESC, created_at DESC
        LIMIT 1
    """).fetchone()
    conn.close()
    return row["amount"] if row else 0.0


def clear_all_transactions():
    """
    Wipe all transactions. Useful for demo reset.
    Does NOT clear vendors or decisions.
    """
    conn = get_connection()
    conn.execute("DELETE FROM transactions")
    conn.commit()
    conn.close()


def _row_to_txn(row) -> Transaction:
    """Convert a SQLite row to a Transaction dataclass."""
    return Transaction(
        id=row["id"],
        amount=row["amount"],
        type=row["type"],
        due_date=date.fromisoformat(row["due_date"]),
        counterparty=row["counterparty"],
        source=row["source"],
        confidence=row["confidence"],
        description=row["description"],
        is_recurring=bool(row["is_recurring"]),
        currency=row["currency"],
    )
