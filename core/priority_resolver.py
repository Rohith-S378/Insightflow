"""
core/priority_resolver.py
--------------------------
Greedy allocation algorithm: given sorted obligations and available cash,
assigns PAY_FULL / PAY_PARTIAL / DEFER to each obligation.

Obligations are processed in score order (highest first).
Cash is allocated greedily until exhausted.

Also provides scenario_projector: runs multiple allocation simulations
with different assumptions (early collection, deferring specific vendors).
"""

from datetime import date, timedelta
from copy import deepcopy
from data.models import Obligation


def resolve_priorities(
    obligations: list[Obligation],   # Must be pre-sorted by final_score desc
    available_cash: float,
    today: date | None = None,
) -> tuple[list[Obligation], float]:
    """
    Greedy cash allocator.

    For each obligation (highest score first):
    - If enough cash remains → PAY_FULL
    - If partial is allowed and some cash remains → PAY_PARTIAL
    - Otherwise → DEFER

    Returns (updated_obligations, remaining_cash)
    """
    if today is None:
        today = date.today()

    remaining = available_cash

    for ob in obligations:
        if remaining >= ob.amount:
            # Full payment possible
            ob.action = "PAY_FULL"
            ob.amount_to_pay = ob.amount
            remaining -= ob.amount

        elif (remaining > 0
              and ob.vendor_profile
              and ob.vendor_profile.allows_partial
              and remaining >= ob.amount * 0.2):
            # Partial payment: only if vendor allows it and we can pay at least 20%
            ob.action = "PAY_PARTIAL"
            ob.amount_to_pay = round(remaining, 2)
            remaining = 0

            # Suggest deferring the balance
            ob.deferred_to = ob.due_date + timedelta(days=14)
            balance_owed = ob.amount - ob.amount_to_pay
            ob.deferral_reason = (
                f"Partial payment of ₹{ob.amount_to_pay:,.0f} now. "
                f"Remaining ₹{balance_owed:,.0f} deferred to {ob.deferred_to.strftime('%d %b %Y')} "
                f"pending incoming receivables."
            )

        else:
            # Cannot pay — defer
            ob.action = "DEFER"
            ob.amount_to_pay = 0.0

            # Suggest a realistic deferral date based on urgency
            grace = ob.vendor_profile.grace_days if ob.vendor_profile else 0
            defer_by = max(7, grace + 3)
            ob.deferred_to = today + timedelta(days=defer_by)
            ob.deferral_reason = _build_deferral_reason(ob, available_cash)

    return obligations, round(remaining, 2)


def project_scenarios(
    obligations: list[Obligation],
    base_cash: float,
    early_receivables: float = 0.0,
    force_defer_vendors: list[str] | None = None,
    today: date | None = None,
) -> list[dict]:
    """
    Run multiple allocation scenarios and return a comparison.

    Scenarios:
    1. Base case: current cash, no changes
    2. With early collections: add expected early receivables
    3. Defer specific vendors: remove their obligations before allocation

    Returns list of scenario result dicts.
    """
    if today is None:
        today = date.today()

    results = []

    # ── Scenario 1: Base case ─────────────────────────────────────────────────
    base_obs = deepcopy(obligations)
    resolved_base, remaining_base = resolve_priorities(base_obs, base_cash, today)
    deferred_base = [o for o in resolved_base if o.action == "DEFER"]
    results.append({
        "name": "Base case",
        "description": "Current cash, no changes",
        "cash_used": base_cash - remaining_base,
        "remaining_cash": remaining_base,
        "paid_count": sum(1 for o in resolved_base if o.action in ("PAY_FULL", "PAY_PARTIAL")),
        "deferred_count": len(deferred_base),
        "deferred_amount": sum(o.amount for o in deferred_base),
        "obligations": resolved_base,
    })

    # ── Scenario 2: With early collections ───────────────────────────────────
    if early_receivables > 0:
        early_obs = deepcopy(obligations)
        adjusted_cash = base_cash + early_receivables
        resolved_early, remaining_early = resolve_priorities(early_obs, adjusted_cash, today)
        deferred_early = [o for o in resolved_early if o.action == "DEFER"]
        results.append({
            "name": "Collect early",
            "description": f"If ₹{early_receivables:,.0f} receivable collected early",
            "cash_used": adjusted_cash - remaining_early,
            "remaining_cash": remaining_early,
            "paid_count": sum(1 for o in resolved_early if o.action in ("PAY_FULL", "PAY_PARTIAL")),
            "deferred_count": len(deferred_early),
            "deferred_amount": sum(o.amount for o in deferred_early),
            "obligations": resolved_early,
        })

    # ── Scenario 3: Defer selected vendors ───────────────────────────────────
    if force_defer_vendors:
        defer_obs = deepcopy(obligations)
        # Remove the specified vendors from allocation (they'll all be DEFER)
        allocatable = [o for o in defer_obs
                       if o.counterparty not in force_defer_vendors]
        forced_deferred = [o for o in defer_obs
                           if o.counterparty in force_defer_vendors]

        for o in forced_deferred:
            o.action = "DEFER"
            o.amount_to_pay = 0.0
            o.deferred_to = today + timedelta(days=14)
            o.deferral_reason = "Strategically deferred to preserve cash for critical obligations."

        resolved_defer, remaining_defer = resolve_priorities(allocatable, base_cash, today)
        all_obs = resolved_defer + forced_deferred
        deferred_all = [o for o in all_obs if o.action == "DEFER"]

        results.append({
            "name": f"Defer {', '.join(force_defer_vendors)}",
            "description": f"Force-defer {len(force_defer_vendors)} vendor(s) to free up cash",
            "cash_used": base_cash - remaining_defer,
            "remaining_cash": remaining_defer,
            "paid_count": sum(1 for o in all_obs if o.action in ("PAY_FULL", "PAY_PARTIAL")),
            "deferred_count": len(deferred_all),
            "deferred_amount": sum(o.amount for o in deferred_all),
            "obligations": all_obs,
        })

    return results


def _build_deferral_reason(ob: Obligation, available_cash: float) -> str:
    """
    Build a human-readable reason string explaining why an obligation was deferred.
    This string is passed to the LLM for email generation context.
    """
    shortfall = ob.amount - available_cash
    parts = []

    if available_cash <= 0:
        parts.append("No cash available after higher-priority obligations are paid.")
    else:
        parts.append(f"Cash shortfall of ₹{shortfall:,.0f} after paying higher-priority obligations.")

    if ob.vendor_profile:
        rel = ob.vendor_profile.relationship_type
        if rel == "long_term":
            parts.append("Vendor has a long payment history — deferral request is appropriate.")
        elif rel == "occasional":
            parts.append("Occasional vendor — lower operational risk if deferred.")
        if ob.vendor_profile.has_grace_period:
            parts.append(f"Vendor offers {ob.vendor_profile.grace_days}-day grace period.")

    return " ".join(parts)
