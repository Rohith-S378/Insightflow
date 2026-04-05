"""
core/normalizer.py
------------------
Normalizes all ingested transactions into a consistent schema,
then deduplicates across sources.

The deduplication step is critical because the same payment often appears in:
- Bank statement (as a debit)
- Vendor invoice (as a payable)
These must be recognized as the same obligation, not counted twice.
"""

import uuid
from datetime import date
from data.models import Transaction
from config.settings import DEDUP_AMOUNT_TOLERANCE, DEDUP_DATE_TOLERANCE_DAYS, DEDUP_NAME_SIMILARITY


def normalize_transactions(raw_transactions: list[Transaction]) -> list[Transaction]:
    """
    Apply normalization rules to a list of raw transactions:
    - Standardize counterparty names (title case, strip noise)
    - Ensure all amounts are positive
    - Remove records with missing critical fields
    Returns cleaned list.
    """
    normalized = []

    for txn in raw_transactions:
        # Ensure amount is always positive before the zero-check
        txn.amount = abs(txn.amount)

        # Skip if missing critical fields
        if not txn.amount or txn.amount <= 0:
            continue
        if not txn.due_date:
            continue
        if not txn.counterparty or txn.counterparty.strip() == "":
            txn.counterparty = "Unknown"

        # Normalize counterparty name
        txn.counterparty = _normalize_name(txn.counterparty)

        normalized.append(txn)

    return normalized


def deduplicate(transactions: list[Transaction]) -> list[Transaction]:
    """
    Remove duplicate transactions using fuzzy matching on:
    - Amount (within 2% tolerance)
    - Date (within 2 days)
    - Vendor name (82% similarity)

    When a duplicate is found:
    - Keep the one with higher confidence (digital > OCR)
    - If same confidence, keep the bank statement version

    Returns deduplicated list.
    """
    if not transactions:
        return []

    # Sort by confidence descending so we keep higher-quality records
    sorted_txns = sorted(transactions, key=lambda t: t.confidence, reverse=True)

    kept = []
    for candidate in sorted_txns:
        if not _is_duplicate_of_any(candidate, kept):
            kept.append(candidate)
        else:
            print(f"[Dedup] Removed duplicate: {candidate.counterparty} ₹{candidate.amount} on {candidate.due_date}")

    print(f"[Dedup] {len(transactions)} → {len(kept)} transactions after deduplication")
    return kept


def _is_duplicate_of_any(candidate: Transaction, existing: list[Transaction]) -> bool:
    """Check if a candidate transaction is a duplicate of any in the existing list."""
    for txn in existing:
        if _are_duplicates(candidate, txn):
            return True
    return False


def _are_duplicates(a: Transaction, b: Transaction) -> bool:
    """
    Determine if two transactions are likely the same real-world event.
    All three conditions must be True.
    """
    # 1. Amount must be within tolerance
    if a.amount == 0 or b.amount == 0:
        return False
    amount_diff = abs(a.amount - b.amount) / max(a.amount, b.amount)
    if amount_diff > DEDUP_AMOUNT_TOLERANCE:
        return False

    # 2. Date must be within tolerance
    date_diff = abs((a.due_date - b.due_date).days)
    if date_diff > DEDUP_DATE_TOLERANCE_DAYS:
        return False

    # 3. Vendor name must be similar
    name_sim = _name_similarity(a.counterparty, b.counterparty)
    if name_sim < DEDUP_NAME_SIMILARITY:
        return False

    return True


def _name_similarity(name_a: str, name_b: str) -> float:
    """
    Compute similarity between two vendor names.
    Uses rapidfuzz if available, falls back to simple prefix match.
    """
    a = name_a.lower().strip()
    b = name_b.lower().strip()

    if a == b:
        return 1.0

    try:
        from rapidfuzz import fuzz
        return fuzz.token_sort_ratio(a, b) / 100.0
    except ImportError:
        # Simple fallback: check if one name starts with or contains the other
        if a in b or b in a:
            return 0.9
        # Count common words
        words_a = set(a.split())
        words_b = set(b.split())
        if not words_a or not words_b:
            return 0.0
        common = words_a & words_b
        return len(common) / max(len(words_a), len(words_b))


def _normalize_name(name: str) -> str:
    """
    Standardize a vendor name:
    - Title case
    - Remove special characters
    - Truncate to 60 chars
    """
    import re
    # Remove non-alphanumeric except spaces and common punctuation
    cleaned = re.sub(r"[^\w\s&.,'-]", " ", name)
    # Collapse multiple spaces
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    # Title case
    cleaned = cleaned.title()
    return cleaned[:60]
