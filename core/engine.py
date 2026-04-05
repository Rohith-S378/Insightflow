"""
core/engine.py
--------------
Main orchestrator for the deterministic layer.

Call run_analysis() with raw transactions and current cash balance.
Returns a fully populated FinancialState object ready for the LLM layer
and the frontend dashboard.

This is the single entry point the API and frontend call.
"""

from datetime import date
from data.models import (
    Transaction, Obligation, FinancialState, WeekProjection
)
from data.transaction_store import get_latest_balance
from core.normalizer import normalize_transactions, deduplicate
from core.runway_calculator import compute_runway
from core.obligation_scorer import score_all
from core.priority_resolver import resolve_priorities, project_scenarios
from core.vendor_profile import enrich_obligations


def run_analysis(
    transactions: list[Transaction],
    current_cash: float | None = None,
    today: date | None = None,
) -> FinancialState:
    """
    Full analysis pipeline. Steps:

    1. Normalize all transactions
    2. Deduplicate across sources
    3. Separate payables / receivables
    4. Get current cash (from balance_snapshot or parameter)
    5. Compute runway (days-to-zero simulation)
    6. Build obligation objects and enrich with vendor profiles
    7. Score each obligation
    8. Resolve priorities (greedy allocation)
    9. Package into FinancialState

    Args:
        transactions:  All raw transactions from all sources
        current_cash:  Override cash balance (if None, uses latest balance_snapshot)
        today:         Override today's date (for testing)

    Returns:
        FinancialState with all decisions made, ready for LLM output layer
    """
    if today is None:
        today = date.today()

    print(f"\n[Engine] Starting analysis for {today} with {len(transactions)} transactions")

    # ── Step 1 & 2: Normalize and Deduplicate ─────────────────────────────────
    normalized = normalize_transactions(transactions)
    deduped = deduplicate(normalized)
    print(f"[Engine] After normalize+dedup: {len(deduped)} transactions")

    # ── Step 3: Separate by type ──────────────────────────────────────────────
    payables = [t for t in deduped if t.type == "payable"]
    receivables = [t for t in deduped if t.type == "receivable"]
    balance_snapshots = [t for t in deduped if t.type == "balance_snapshot"]

    # ── Step 4: Determine current cash ────────────────────────────────────────
    if current_cash is None:
        if balance_snapshots:
            # Use the most recent balance snapshot
            current_cash = sorted(
                balance_snapshots, key=lambda t: t.due_date
            )[-1].amount
        else:
            current_cash = get_latest_balance()

    print(f"[Engine] Current cash: ₹{current_cash:,.2f}")
    print(f"[Engine] Payables: {len(payables)}, Receivables: {len(receivables)}")

    # ── Step 5: Compute runway ────────────────────────────────────────────────
    runway = compute_runway(current_cash, payables, receivables)
    print(f"[Engine] Days to zero: {runway['days_to_zero']} ({runway['severity']})")

    # ── Step 6: Build and enrich obligations ──────────────────────────────────
    obligations = [Obligation.from_transaction(t) for t in payables]
    obligations = enrich_obligations(obligations, all_transactions=deduped)

    # ── Step 7: Score obligations ─────────────────────────────────────────────
    scored_obligations = score_all(obligations, today=today)

    # ── Step 8: Resolve priorities ────────────────────────────────────────────
    resolved_obligations, remaining_cash = resolve_priorities(
        scored_obligations, current_cash, today=today
    )

    # ── Step 9: Build FinancialState ──────────────────────────────────────────
    total_payables = sum(o.amount for o in resolved_obligations)
    total_receivables = sum(t.amount for t in receivables)

    state = FinancialState(
        current_cash=current_cash,
        snapshot_date=today,
        days_to_zero=runway["days_to_zero"],
        days_to_zero_simple=runway["days_to_zero_simple"],
        net_burn_per_day=runway["net_burn_per_day"],
        severity=runway["severity"],
        severity_color=runway["severity_color"],
        obligations=resolved_obligations,
        weekly_projection=runway["weekly_projections"],
        total_payables=total_payables,
        total_receivables=total_receivables,
        cash_gap=max(0, total_payables - current_cash),
    )

    print(f"[Engine] Analysis complete. "
          f"Pay: {sum(1 for o in resolved_obligations if o.action=='PAY_FULL')}, "
          f"Defer: {sum(1 for o in resolved_obligations if o.action=='DEFER')}, "
          f"Partial: {sum(1 for o in resolved_obligations if o.action=='PAY_PARTIAL')}")

    return state


def state_to_dict(state: FinancialState) -> dict:
    """
    Convert FinancialState to a JSON-serializable dict.
    This is what gets passed to the LLM layer.
    """
    return {
        "snapshot_date": state.snapshot_date.isoformat(),
        "current_cash": state.current_cash,
        "days_to_zero": state.days_to_zero,
        "days_to_zero_simple": state.days_to_zero_simple,
        "net_burn_per_day": state.net_burn_per_day,
        "severity": state.severity,
        "total_payables": state.total_payables,
        "total_receivables": state.total_receivables,
        "cash_gap": state.cash_gap,
        "obligations": [
            {
                "id": o.id,
                "counterparty": o.counterparty,
                "amount": o.amount,
                "due_date": o.due_date.isoformat(),
                "action": o.action,
                "amount_to_pay": o.amount_to_pay,
                "deferred_to": o.deferred_to.isoformat() if o.deferred_to else None,
                "deferral_reason": o.deferral_reason,
                "final_score": o.final_score,
                "urgency_score": o.urgency_score,
                "penalty_score": o.penalty_score,
                "flexibility_score": o.flexibility_score,
                "vendor_profile": {
                    "relationship_type": o.vendor_profile.relationship_type,
                    "months_active": o.vendor_profile.months_active,
                    "payment_history": o.vendor_profile.payment_history,
                    "allows_partial": o.vendor_profile.allows_partial,
                    "has_grace_period": o.vendor_profile.has_grace_period,
                    "grace_days": o.vendor_profile.grace_days,
                } if o.vendor_profile else None,
            }
            for o in state.obligations
        ],
        "weekly_projection": [
            {
                "week": w.week_number,
                "opening": w.opening_balance,
                "outflow": w.total_outflow,
                "inflow": w.total_inflow,
                "closing": w.closing_balance,
                "status": w.status,
            }
            for w in state.weekly_projection
        ],
    }
