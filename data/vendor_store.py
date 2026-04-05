"""
data/vendor_store.py
--------------------
CRUD operations for vendor profiles.
Used by vendor_profile.py (core) to look up relationship types,
and by the API routes to let users manage their vendors.
"""

from data.db import get_connection
from data.models import VendorProfile


def upsert_vendor(profile: VendorProfile):
    """Insert or update a vendor profile."""
    conn = get_connection()
    conn.execute("""
        INSERT INTO vendors (name, relationship_type, months_active, payment_history,
                             allows_partial, has_grace_period, grace_days, notes, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(name) DO UPDATE SET
            relationship_type = excluded.relationship_type,
            months_active     = excluded.months_active,
            payment_history   = excluded.payment_history,
            allows_partial    = excluded.allows_partial,
            has_grace_period  = excluded.has_grace_period,
            grace_days        = excluded.grace_days,
            notes             = excluded.notes,
            updated_at        = datetime('now')
    """, (
        profile.name, profile.relationship_type, profile.months_active,
        profile.payment_history, int(profile.allows_partial),
        int(profile.has_grace_period), profile.grace_days, profile.notes
    ))
    conn.commit()
    conn.close()


def get_vendor(name: str) -> VendorProfile | None:
    """Fetch a vendor profile by exact name match."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM vendors WHERE name = ?", (name,)
    ).fetchone()
    conn.close()

    if not row:
        return None

    return VendorProfile(
        name=row["name"],
        relationship_type=row["relationship_type"],
        months_active=row["months_active"],
        payment_history=row["payment_history"],
        allows_partial=bool(row["allows_partial"]),
        has_grace_period=bool(row["has_grace_period"]),
        grace_days=row["grace_days"],
        notes=row["notes"],
    )


def get_all_vendors() -> list[VendorProfile]:
    """Return all vendor profiles."""
    conn = get_connection()
    rows = conn.execute("SELECT * FROM vendors ORDER BY name").fetchall()
    conn.close()

    return [
        VendorProfile(
            name=r["name"],
            relationship_type=r["relationship_type"],
            months_active=r["months_active"],
            payment_history=r["payment_history"],
            allows_partial=bool(r["allows_partial"]),
            has_grace_period=bool(r["has_grace_period"]),
            grace_days=r["grace_days"],
            notes=r["notes"],
        )
        for r in rows
    ]


def fuzzy_find_vendor(name: str) -> VendorProfile | None:
    """
    Find a vendor using fuzzy name matching.
    Handles slight variations like 'Ravi Supplies' vs 'Ravi Supply'.
    """
    try:
        from rapidfuzz import process, fuzz
    except ImportError:
        # Fallback: exact match only
        return get_vendor(name)

    all_vendors = get_all_vendors()
    if not all_vendors:
        return None

    names = [v.name for v in all_vendors]
    result = process.extractOne(name, names, scorer=fuzz.token_sort_ratio)

    if result and result[1] >= 82:  # 82% similarity threshold
        matched_name = result[0]
        return next((v for v in all_vendors if v.name == matched_name), None)

    return None


def seed_demo_vendors():
    """
    Seed the database with demo vendor profiles for presentation purposes.
    Call once when setting up the demo environment.
    """
    demo_vendors = [
        VendorProfile("Ravi Supplies", "long_term", 18.0, "always_paid",
                      allows_partial=True, has_grace_period=True, grace_days=7),
        VendorProfile("GST Department", "critical", 999.0, "always_paid",
                      allows_partial=False, has_grace_period=False),
        VendorProfile("City Power Co", "critical", 24.0, "always_paid",
                      allows_partial=False, has_grace_period=False),
        VendorProfile("Meera Traders", "occasional", 3.0, "unknown",
                      allows_partial=False, has_grace_period=False),
        VendorProfile("Office Rent", "long_term", 36.0, "always_paid",
                      allows_partial=False, has_grace_period=True, grace_days=5),
        VendorProfile("Tech Solutions Pvt", "new", 1.0, "unknown",
                      allows_partial=True, has_grace_period=False),
    ]

    for vendor in demo_vendors:
        upsert_vendor(vendor)
    print(f"[DB] Seeded {len(demo_vendors)} demo vendors.")
