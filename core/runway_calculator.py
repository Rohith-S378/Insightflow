"""
core/runway_calculator.py
--------------------------
Computes the days-to-zero liquidity indicator using week-by-week simulation.

Two methods are computed:
1. Simple formula: current_cash / net_daily_burn  (fast estimate, ignores spikes)
2. Simulation:     step through weeks until balance < 0  (accurate, catches spikes)

The simulation result is the primary number shown to users.
The simple formula is shown alongside for comparison.
"""

from datetime import date, timedelta
from data.models import Transaction, WeekProjection
from config.settings import (
    SEVERITY_CRITICAL, SEVERITY_URGENT,
    SEVERITY_WARNING, SEVERITY_MONITOR
)


def compute_runway(
    current_cash: float,
    payables: list[Transaction],
    receivables: list[Transaction],
    horizon_weeks: int = 13    # 3-month horizon
) -> dict:
    """
    Main entry point for runway calculation.

    Args:
        current_cash:   Available cash today
        payables:       Upcoming obligations (type="payable")
        receivables:    Expected inflows (type="receivable")
        horizon_weeks:  How far to project (default 13 weeks / ~3 months)

    Returns a dict with:
        days_to_zero:         int (from simulation)
        days_to_zero_simple:  float (from formula)
        net_burn_per_day:     float
        severity:             str
        severity_color:       str
        weekly_projections:   list[WeekProjection]
    """
    today = date.today()

    # ── Simple Formula ────────────────────────────────────────────────────────
    # Sum all payables and receivables over the next 30 days for burn rate
    next_30_payables = sum(
        t.amount for t in payables
        if 0 <= (t.due_date - today).days <= 30
    )
    next_30_receivables = sum(
        t.amount for t in receivables
        if 0 <= (t.due_date - today).days <= 30
    )
    net_burn_30d = next_30_payables - next_30_receivables
    net_burn_per_day = max(net_burn_30d / 30, 0)  # Never negative (can't "gain" days)

    if net_burn_per_day > 0:
        days_to_zero_simple = current_cash / net_burn_per_day
    else:
        days_to_zero_simple = float("inf")

    # ── Week-by-Week Simulation ───────────────────────────────────────────────
    days_to_zero_real, weekly_projections = _simulate(
        current_cash, payables, receivables, today, horizon_weeks
    )

    # ── Severity Classification ───────────────────────────────────────────────
    severity, severity_color = classify_severity(days_to_zero_real)

    return {
        "days_to_zero": days_to_zero_real,
        "days_to_zero_simple": round(days_to_zero_simple, 1),
        "net_burn_per_day": round(net_burn_per_day, 2),
        "severity": severity,
        "severity_color": severity_color,
        "weekly_projections": weekly_projections,
    }


def _simulate(
    cash: float,
    payables: list[Transaction],
    receivables: list[Transaction],
    start_date: date,
    horizon_weeks: int
) -> tuple[int, list[WeekProjection]]:
    """
    Simulate cash flow week by week.

    For each week:
    - Sum all payables due in that week
    - Sum all receivables expected in that week
    - Compute closing balance = opening - outflow + inflow
    - If closing < 0, interpolate the exact day within that week

    Returns (days_to_zero, list_of_WeekProjection)
    """
    balance = cash
    projections = []
    days_to_zero = None  # None means "survived the full horizon"

    for week_num in range(1, horizon_weeks + 1):
        week_start = start_date + timedelta(days=(week_num - 1) * 7)
        week_end = week_start + timedelta(days=6)

        # Sum obligations due this week
        week_payables = [
            t for t in payables
            if week_start <= t.due_date <= week_end
        ]
        week_receivables = [
            t for t in receivables
            if week_start <= t.due_date <= week_end
        ]

        outflow = sum(t.amount for t in week_payables)
        inflow = sum(t.amount for t in week_receivables)

        opening = balance
        balance = opening - outflow + inflow

        # Determine status
        if balance < 0:
            status = "SHORTFALL"
        elif balance < 5000:
            status = "LOW"
        else:
            status = "OK"

        projections.append(WeekProjection(
            week_number=week_num,
            opening_balance=round(opening, 2),
            total_outflow=round(outflow, 2),
            total_inflow=round(inflow, 2),
            closing_balance=round(balance, 2),
            status=status,
            obligations_due=[t.id for t in week_payables],
        ))

        # Detect exact day of zero crossing (only record the first time)
        if balance < 0 and days_to_zero is None:
            net_weekly_drain = outflow - inflow
            if net_weekly_drain > 0:
                # How many days into this week did we run out?
                days_into_week = max(0, int(opening / (net_weekly_drain / 7)))
            else:
                days_into_week = 7

            days_to_zero = (week_num - 1) * 7 + days_into_week

    # If we never ran out, return days beyond horizon
    if days_to_zero is None:
        days_to_zero = horizon_weeks * 7 + 1  # "90+ days"

    return days_to_zero, projections


def classify_severity(days: int) -> tuple[str, str]:
    """
    Map days-to-zero to a severity label and color.
    These thresholds are configured in settings.py.
    """
    if days < SEVERITY_CRITICAL:
        return "CRITICAL", "red"
    elif days < SEVERITY_URGENT:
        return "URGENT", "red"
    elif days < SEVERITY_WARNING:
        return "WARNING", "amber"
    elif days < SEVERITY_MONITOR:
        return "MONITOR", "yellow"
    else:
        return "STABLE", "green"
