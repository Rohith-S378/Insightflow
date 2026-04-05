"""
demo/seed_data.py
-----------------
Seeds the database with realistic demo data for presentations.
Designed to produce a compelling demo scenario:
  - Cash: ₹40,000
  - Obligations: ₹1,10,000+ (shortfall situation)
  - Mix of vendor types: GST, rent, long-term vendor, new vendor
  - Expected receivable coming in Week 2

Run: POST /demo/seed  (via API)
Or:  python -m demo.seed_data  (directly)
"""

import uuid
from datetime import date, timedelta
from data.db import init_db
from data.models import Transaction
from data.transaction_store import save_transactions, clear_all_transactions
from data.vendor_store import seed_demo_vendors

TODAY = date.today()


def make_txn(amount, txn_type, days_offset, counterparty, source="manual_entry",
             description="", is_recurring=False, confidence=1.0):
    """Helper to create a Transaction with a date relative to today."""
    return Transaction(
        id=f"demo_{uuid.uuid4().hex[:8]}",
        amount=amount,
        type=txn_type,
        due_date=TODAY + timedelta(days=days_offset),
        counterparty=counterparty,
        source=source,
        description=description,
        is_recurring=is_recurring,
        confidence=confidence,
        currency="INR",
    )


DEMO_TRANSACTIONS = [
    # ── Current balance ───────────────────────────────────────────────────────
    make_txn(40000, "balance_snapshot", 0, "balance_snapshot",
             description="Current bank balance"),

    # ── Payables (obligations) ────────────────────────────────────────────────
    # GST — overdue, highest priority
    make_txn(18500, "payable", -2, "GST Department",
             description="GST filing Q4 — overdue", source="invoice",
             is_recurring=True),

    # Electricity — due in 3 days
    make_txn(4200, "payable", 3, "City Power Co",
             description="Electricity bill March", source="invoice",
             is_recurring=True),

    # Office Rent — due in 5 days
    make_txn(25000, "payable", 5, "Office Rent",
             description="Monthly office rent April", source="invoice",
             is_recurring=True),

    # Long-term vendor — due in 8 days, has grace period
    make_txn(32000, "payable", 8, "Ravi Supplies",
             description="Raw material invoice #1042", source="invoice"),

    # New vendor — due in 12 days
    make_txn(15000, "payable", 12, "Tech Solutions Pvt",
             description="Software subscription annual", source="invoice"),

    # Occasional vendor — due in 20 days
    make_txn(8500, "payable", 20, "Meera Traders",
             description="Packaging materials", source="invoice"),

    # Loan EMI — due in 15 days
    make_txn(12000, "payable", 15, "HDFC Bank EMI",
             description="Business loan EMI", source="bank_statement",
             is_recurring=True),

    # ── Receivables (expected inflows) ────────────────────────────────────────
    # Large payment expected in week 2
    make_txn(45000, "receivable", 10, "Sunrise Exports",
             description="Payment for order #SE-889", source="invoice"),

    # Smaller payment next week
    make_txn(12000, "receivable", 6, "Local Retailer A",
             description="Outstanding balance", source="invoice"),

    # ── Historical transactions (for vendor inference) ─────────────────────────
    # Ravi Supplies — 6 months of history
    make_txn(28000, "payable", -30,  "Ravi Supplies", description="Invoice #1038"),
    make_txn(31000, "payable", -60,  "Ravi Supplies", description="Invoice #1031"),
    make_txn(29500, "payable", -90,  "Ravi Supplies", description="Invoice #1024"),
    make_txn(33000, "payable", -120, "Ravi Supplies", description="Invoice #1018"),
    make_txn(27000, "payable", -150, "Ravi Supplies", description="Invoice #1011"),

    # Office Rent history
    make_txn(25000, "payable", -30,  "Office Rent", description="March rent"),
    make_txn(25000, "payable", -60,  "Office Rent", description="February rent"),
    make_txn(25000, "payable", -90,  "Office Rent", description="January rent"),

    # GST history
    make_txn(16200, "payable", -92,  "GST Department", description="GST Q3"),
    make_txn(17800, "payable", -183, "GST Department", description="GST Q2"),
]


def seed_all():
    """Seed vendors and demo transactions."""
    init_db()
    clear_all_transactions()
    seed_demo_vendors()
    save_transactions(DEMO_TRANSACTIONS)
    print(f"[Demo] Seeded {len(DEMO_TRANSACTIONS)} demo transactions.")
    print(f"[Demo] Scenario: ₹40,000 cash | ₹1,15,200 obligations | ₹57,000 receivables")


if __name__ == "__main__":
    seed_all()
    print("[Demo] Database ready. Start the app with: streamlit run frontend/app.py")
