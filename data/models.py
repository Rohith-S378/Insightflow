"""
data/models.py
--------------
Central dataclass definitions. Every module in the project imports from here.
Keeping all models in one place ensures consistent field names and types
across ingestion, core logic, and LLM output layers.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass
class Transaction:
    """
    A single normalized financial record.
    Created by the ingestion layer from any source (bank CSV, invoice PDF, receipt image).
    """
    id: str                          # Unique identifier e.g. "txn_001"
    amount: float                    # Always positive; type field indicates direction
    type: str                        # "payable" | "receivable" | "balance_snapshot"
    due_date: date                   # When it's due or when it occurred
    counterparty: str                # Vendor / customer name
    source: str                      # "bank_statement" | "invoice" | "receipt"
    confidence: float = 1.0          # OCR confidence (1.0 = digital, <1.0 = OCR'd)
    description: str = ""            # Raw description from source
    is_recurring: bool = False       # Whether this repeats (rent, subscriptions)
    currency: str = "INR"


@dataclass
class VendorProfile:
    """
    Relationship metadata for a counterparty.
    Used by the LLM layer to select email tone.
    Also feeds into obligation scoring (flexible vendors score lower penalty).
    """
    name: str
    relationship_type: str           # "long_term" | "new" | "critical" | "occasional"
    months_active: float = 0.0       # How long this vendor relationship has existed
    payment_history: str = "unknown" # "always_paid" | "sometimes_late" | "unknown"
    allows_partial: bool = False     # Whether vendor accepts partial payments
    has_grace_period: bool = False   # Whether vendor has a grace period
    grace_days: int = 0              # Number of grace period days
    notes: str = ""                  # Any free-text notes about the relationship


@dataclass
class Obligation:
    """
    A payable enriched with scoring, decision, and vendor profile.
    Output of the deterministic core — this is what gets fed to the LLM layer.
    """
    # Core transaction fields (duplicated for convenience)
    id: str
    amount: float
    due_date: date
    counterparty: str
    source: str
    description: str = ""
    is_recurring: bool = False
    currency: str = "INR"
    confidence: float = 1.0

    # Scoring fields (set by obligation_scorer.py)
    urgency_score: float = 0.0       # Higher = more urgent
    penalty_score: float = 0.0       # Higher = more costly to defer
    flexibility_score: float = 1.0   # Higher = easier to defer
    final_score: float = 0.0         # Composite score used for sorting

    # Decision fields (set by priority_resolver.py)
    action: str = "PENDING"          # "PAY_FULL" | "PAY_PARTIAL" | "DEFER"
    amount_to_pay: float = 0.0       # Actual amount being paid (may be partial)
    deferred_to: Optional[date] = None  # Suggested deferral date
    deferral_reason: str = ""        # Why it was deferred (for LLM context)

    # Vendor profile (set by vendor enrichment step)
    vendor_profile: Optional[VendorProfile] = None

    @classmethod
    def from_transaction(cls, txn: Transaction) -> "Obligation":
        """Convert a Transaction into an Obligation (before scoring)."""
        return cls(
            id=txn.id,
            amount=txn.amount,
            due_date=txn.due_date,
            counterparty=txn.counterparty,
            source=txn.source,
            description=txn.description,
            is_recurring=txn.is_recurring,
            currency=txn.currency,
            confidence=txn.confidence,
        )


@dataclass
class FinancialState:
    """
    Complete snapshot of the business's financial position.
    This is the main output of the deterministic core, fed into the LLM layer.
    """
    # Current position
    current_cash: float              # Available cash right now
    snapshot_date: date              # Date this snapshot was computed

    # Runway
    days_to_zero: int                # Simulated days until cash runs out
    days_to_zero_simple: float       # Simple formula (cash / daily burn)
    net_burn_per_day: float          # Average daily net outflow
    severity: str                    # "CRITICAL" | "URGENT" | "WARNING" | "MONITOR" | "STABLE"
    severity_color: str              # "red" | "amber" | "green"

    # Obligations and decisions
    obligations: list = field(default_factory=list)    # List[Obligation]
    weekly_projection: list = field(default_factory=list)  # List[WeekProjection]

    # Totals
    total_payables: float = 0.0
    total_receivables: float = 0.0
    cash_gap: float = 0.0            # total_payables - current_cash (if positive = shortfall)


@dataclass
class WeekProjection:
    """One week in the cash flow simulation."""
    week_number: int
    opening_balance: float
    total_outflow: float
    total_inflow: float
    closing_balance: float
    status: str                      # "OK" | "LOW" | "SHORTFALL"
    obligations_due: list = field(default_factory=list)  # obligation IDs due this week
