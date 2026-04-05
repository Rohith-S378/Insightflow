"""
core/vendor_profile.py
-----------------------
Enriches obligations with vendor relationship profiles.

Profile lookup order:
1. Exact match in vendor store (database)
2. Fuzzy match in vendor store
3. Infer from transaction history (if enough data)
4. Default to "unknown" profile

This module is deterministic — no LLM.
The profile it returns directly controls email tone in the LLM layer.
"""

from datetime import date
from data.models import Obligation, VendorProfile, Transaction
from data.vendor_store import get_vendor, fuzzy_find_vendor, upsert_vendor


def enrich_obligations(
    obligations: list[Obligation],
    all_transactions: list[Transaction] | None = None,
) -> list[Obligation]:
    """
    Attach VendorProfile to each obligation.
    Modifies obligations in place. Returns the same list.
    """
    for ob in obligations:
        profile = _lookup_profile(ob.counterparty, all_transactions)
        ob.vendor_profile = profile

    return obligations


def _lookup_profile(
    vendor_name: str,
    history: list[Transaction] | None = None,
) -> VendorProfile:
    """
    Lookup or infer a vendor profile.
    Returns a VendorProfile (never None — falls back to default).
    """
    # 1. Try exact match
    profile = get_vendor(vendor_name)
    if profile:
        return profile

    # 2. Try fuzzy match
    profile = fuzzy_find_vendor(vendor_name)
    if profile:
        return profile

    # 3. Try to infer from transaction history
    if history:
        profile = _infer_from_history(vendor_name, history)
        if profile:
            # Save inferred profile so future lookups are faster
            upsert_vendor(profile)
            return profile

    # 4. Return default unknown profile
    return VendorProfile(
        name=vendor_name,
        relationship_type="unknown",
        months_active=0.0,
        payment_history="unknown",
        allows_partial=False,
        has_grace_period=False,
    )


def _infer_from_history(
    vendor_name: str,
    transactions: list[Transaction],
) -> VendorProfile | None:
    """
    Infer relationship type from transaction history.

    Rules:
    - > 12 months + > 1.5 txns/month = long_term
    - < 2 months = new
    - > 3 txns/month = frequent (treat as long_term with less grace)
    - else = occasional
    """
    # Find transactions with this vendor (fuzzy match)
    vendor_txns = []
    vendor_lower = vendor_name.lower()
    for t in transactions:
        if (vendor_lower in t.counterparty.lower() or
                t.counterparty.lower() in vendor_lower):
            vendor_txns.append(t)

    if len(vendor_txns) < 2:
        return None  # Not enough history to infer

    # Compute relationship age
    dates = sorted(t.due_date for t in vendor_txns)
    months_active = max(0.5, (dates[-1] - dates[0]).days / 30)
    frequency = len(vendor_txns) / months_active  # Transactions per month

    # Classify
    if months_active > 12 and frequency > 1.5:
        rel_type = "long_term"
        allows_partial = True
        has_grace = True
        grace_days = 7
    elif months_active < 2:
        rel_type = "new"
        allows_partial = False
        has_grace = False
        grace_days = 0
    elif frequency > 3:
        rel_type = "long_term"
        allows_partial = True
        has_grace = True
        grace_days = 5
    else:
        rel_type = "occasional"
        allows_partial = False
        has_grace = False
        grace_days = 0

    print(f"[VendorProfile] Inferred '{rel_type}' for {vendor_name} "
          f"({months_active:.1f} months, {frequency:.1f} txns/mo)")

    return VendorProfile(
        name=vendor_name,
        relationship_type=rel_type,
        months_active=round(months_active, 1),
        payment_history="always_paid" if len(vendor_txns) > 5 else "unknown",
        allows_partial=allows_partial,
        has_grace_period=has_grace,
        grace_days=grace_days,
    )
