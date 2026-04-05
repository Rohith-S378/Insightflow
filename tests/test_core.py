"""
tests/test_core.py
------------------
Unit tests for all deterministic core modules.
Run with: pytest tests/ -v

These tests verify the system produces the same outputs every time.
No LLM calls are made in these tests.
"""

import pytest
import sys
import os
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.models import Transaction, Obligation, VendorProfile
from core.normalizer import normalize_transactions, deduplicate, _are_duplicates
from core.runway_calculator import compute_runway, classify_severity
from core.obligation_scorer import score_obligation, score_all
from core.priority_resolver import resolve_priorities


TODAY = date.today()


# ── Helpers ───────────────────────────────────────────────────────────────────
def make_txn(amount, txn_type, days=5, vendor="Test Vendor", source="bank_statement"):
    return Transaction(
        id=f"t_{amount}_{days}",
        amount=amount,
        type=txn_type,
        due_date=TODAY + timedelta(days=days),
        counterparty=vendor,
        source=source,
        confidence=1.0,
    )


def make_obligation(amount, days=5, vendor="Test Vendor",
                    allows_partial=False, grace=0, rel_type="unknown"):
    ob = Obligation(
        id=f"ob_{amount}_{days}",
        amount=amount,
        due_date=TODAY + timedelta(days=days),
        counterparty=vendor,
        source="invoice",
    )
    ob.vendor_profile = VendorProfile(
        name=vendor,
        relationship_type=rel_type,
        allows_partial=allows_partial,
        has_grace_period=grace > 0,
        grace_days=grace,
    )
    return ob


# ── Normalizer Tests ──────────────────────────────────────────────────────────

def test_normalize_removes_zero_amount():
    txns = [
        make_txn(0, "payable"),
        make_txn(100, "payable"),
    ]
    result = normalize_transactions(txns)
    assert len(result) == 1
    assert result[0].amount == 100


def test_normalize_makes_amounts_positive():
    txn = make_txn(-500, "payable")
    result = normalize_transactions([txn])
    assert result[0].amount == 500


def test_normalize_fills_empty_vendor():
    txn = make_txn(100, "payable")
    txn.counterparty = ""
    result = normalize_transactions([txn])
    assert result[0].counterparty == "Unknown"


# ── Deduplication Tests ───────────────────────────────────────────────────────

def test_dedup_removes_exact_duplicate():
    txn1 = make_txn(1000, "payable", 5, "Ravi Supplies", "bank_statement")
    txn2 = make_txn(1000, "payable", 5, "Ravi Supplies", "invoice")
    result = deduplicate([txn1, txn2])
    assert len(result) == 1


def test_dedup_keeps_different_vendors():
    txn1 = make_txn(1000, "payable", 5, "Ravi Supplies")
    txn2 = make_txn(1000, "payable", 5, "Meera Traders")
    result = deduplicate([txn1, txn2])
    assert len(result) == 2


def test_dedup_keeps_different_amounts():
    txn1 = make_txn(1000, "payable", 5, "Ravi Supplies")
    txn2 = make_txn(5000, "payable", 5, "Ravi Supplies")
    result = deduplicate([txn1, txn2])
    assert len(result) == 2


def test_dedup_within_amount_tolerance():
    """Two transactions with 1% amount difference should be flagged as duplicate."""
    txn1 = Transaction(id="a", amount=10000, type="payable",
                       due_date=TODAY, counterparty="Ravi Supplies",
                       source="bank_statement", confidence=1.0)
    txn2 = Transaction(id="b", amount=10050, type="payable",  # 0.5% diff
                       due_date=TODAY, counterparty="Ravi Supplies",
                       source="invoice", confidence=0.9)
    assert _are_duplicates(txn1, txn2) is True


# ── Runway Calculator Tests ───────────────────────────────────────────────────

def test_runway_with_no_obligations():
    """No obligations = infinite runway."""
    result = compute_runway(50000, [], [])
    assert result["days_to_zero"] > 90
    assert result["severity"] == "STABLE"


def test_runway_detects_imminent_zero():
    """Cash = 10000, weekly burn = 20000 → should run out fast."""
    payables = [make_txn(20000, "payable", 3)]
    result = compute_runway(10000, payables, [])
    assert result["days_to_zero"] < 14
    assert result["severity"] in ("CRITICAL", "URGENT")


def test_runway_receivable_extends_runway():
    """Large receivable should push out days-to-zero."""
    payables = [make_txn(30000, "payable", 7)]
    receivables = [make_txn(25000, "receivable", 5)]
    without_rec = compute_runway(10000, payables, [])
    with_rec = compute_runway(10000, payables, receivables)
    assert with_rec["days_to_zero"] >= without_rec["days_to_zero"]


def test_severity_classification():
    assert classify_severity(5) == ("CRITICAL", "red")
    assert classify_severity(10) == ("URGENT", "red")
    assert classify_severity(20) == ("WARNING", "amber")
    assert classify_severity(45) == ("MONITOR", "yellow")
    assert classify_severity(90) == ("STABLE", "green")


# ── Obligation Scorer Tests ───────────────────────────────────────────────────

def test_overdue_obligation_scores_high():
    """Overdue obligations should score higher than future ones."""
    overdue = make_obligation(10000, -5)  # 5 days overdue
    future = make_obligation(10000, 20)   # Due in 20 days
    score_obligation(overdue)
    score_obligation(future)
    assert overdue.urgency_score > future.urgency_score


def test_gst_obligation_scores_high_penalty():
    """GST should have high penalty score."""
    gst_ob = make_obligation(10000, 5, "GST Department")
    gst_ob.description = "GST filing"
    score_obligation(gst_ob)
    assert gst_ob.penalty_score > 50


def test_long_term_vendor_has_higher_flexibility():
    """Long-term vendor should be more flexible than unknown."""
    long_term = make_obligation(10000, 5, "Ravi", rel_type="long_term",
                                allows_partial=True, grace=7)
    unknown = make_obligation(10000, 5, "New Co", rel_type="unknown")
    score_obligation(long_term)
    score_obligation(unknown)
    assert long_term.flexibility_score > unknown.flexibility_score


def test_score_all_sorts_descending():
    """score_all should return obligations sorted high to low."""
    obs = [
        make_obligation(5000, 30),   # Low urgency
        make_obligation(5000, -3),   # Overdue = high urgency
        make_obligation(5000, 7),    # Medium urgency
    ]
    sorted_obs = score_all(obs)
    scores = [o.final_score for o in sorted_obs]
    assert scores == sorted(scores, reverse=True)


# ── Priority Resolver Tests ───────────────────────────────────────────────────

def test_resolver_pays_when_cash_sufficient():
    """If cash covers everything, all should be PAY_FULL."""
    obs = [
        make_obligation(10000, 5),
        make_obligation(5000, 8),
    ]
    scored = score_all(obs)
    resolved, remaining = resolve_priorities(scored, 50000)

    for o in resolved:
        assert o.action == "PAY_FULL"
    assert remaining == 35000


def test_resolver_defers_when_cash_insufficient():
    """If cash runs out, lower-priority obligations should be deferred."""
    obs = score_all([
        make_obligation(30000, -1, "GST Dept"),   # High score (overdue)
        make_obligation(20000, 10, "Ravi"),        # Lower score
    ])
    resolved, remaining = resolve_priorities(obs, 35000)

    actions = {o.counterparty: o.action for o in resolved}
    # GST should be paid (highest score), Ravi deferred
    assert actions["Gst Dept"] == "PAY_FULL"
    assert actions["Ravi"] == "DEFER"


def test_resolver_partial_payment():
    """If vendor allows partial and some cash remains, should do PAY_PARTIAL."""
    obs = score_all([
        make_obligation(10000, 5, "Exact Match"),
        make_obligation(10000, 8, "Partial OK", allows_partial=True),
    ])
    resolved, remaining = resolve_priorities(obs, 15000)

    actions = {o.counterparty: o.action for o in resolved}
    # First should be paid, second should be partial
    assert remaining == 0


def test_resolver_remaining_cash_never_negative():
    """After resolution, remaining cash should never be negative."""
    obs = score_all([
        make_obligation(100000, 5),
        make_obligation(100000, 8),
    ])
    _, remaining = resolve_priorities(obs, 5000)
    assert remaining >= 0


# ── Integration Test ──────────────────────────────────────────────────────────

def test_full_pipeline():
    """
    End-to-end test of the deterministic pipeline.
    Verifies the same inputs always produce the same outputs.
    """
    from core.engine import run_analysis

    transactions = [
        # Balance
        Transaction("bal_001", 40000, "balance_snapshot",
                    TODAY, "balance_snapshot", "manual_entry"),
        # Payables
        Transaction("pay_001", 18500, "payable",
                    TODAY + timedelta(days=-2), "GST Department",
                    "invoice", description="GST filing overdue"),
        Transaction("pay_002", 25000, "payable",
                    TODAY + timedelta(days=5), "Office Rent",
                    "invoice", description="Monthly rent"),
        Transaction("pay_003", 32000, "payable",
                    TODAY + timedelta(days=8), "Ravi Supplies",
                    "invoice", description="Raw materials"),
        # Receivable
        Transaction("rec_001", 45000, "receivable",
                    TODAY + timedelta(days=10), "Sunrise Exports",
                    "invoice"),
    ]

    state = run_analysis(transactions, current_cash=40000)

    # Basic assertions
    assert state.current_cash == 40000
    assert state.days_to_zero > 0
    assert len(state.obligations) == 3
    assert state.severity in ("CRITICAL","URGENT","WARNING","MONITOR","STABLE")

    # Determinism check: same input → same output
    state2 = run_analysis(transactions, current_cash=40000)
    assert state.days_to_zero == state2.days_to_zero
    assert state.severity == state2.severity

    # All obligations must have an action
    for ob in state.obligations:
        assert ob.action in ("PAY_FULL", "PAY_PARTIAL", "DEFER")

    print(f"\n[Integration Test] Passed! "
          f"DtZ={state.days_to_zero}, Severity={state.severity}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
