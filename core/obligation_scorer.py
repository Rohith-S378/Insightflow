"""
core/obligation_scorer.py
--------------------------
Scores each obligation on urgency, penalty risk, and flexibility.
Returns a final_score used by the priority resolver to sort obligations.

Formula:
    final_score = (urgency_score * URGENCY_WEIGHT)
                + (penalty_score * PENALTY_WEIGHT)
                - (flexibility_score * FLEXIBILITY_WEIGHT)

Higher score = pay this first.
This is pure deterministic math — no LLM involved.
"""

from datetime import date
from data.models import Obligation, VendorProfile
from config.penalty_table import get_penalty
from config.settings import URGENCY_WEIGHT, PENALTY_WEIGHT, FLEXIBILITY_WEIGHT


# Categories to detect from obligation description
CATEGORY_KEYWORDS = {
    "gst":              ["gst", "goods and service", "tax invoice"],
    "income_tax":       ["income tax", "it return", "advance tax"],
    "tds":              ["tds", "tax deducted"],
    "professional_tax": ["professional tax", "ptax"],
    "electricity":      ["electricity", "power", "bescom", "msedcl", "tneb", "electric bill"],
    "internet":         ["internet", "broadband", "wifi", "jio", "airtel"],
    "rent":             ["rent", "rental", "office rent", "shop rent"],
    "loan_emi":         ["emi", "loan", "home loan", "vehicle loan"],
    "credit_card":      ["credit card", "cc bill", "citi", "hdfc card"],
    "payroll":          ["salary", "payroll", "wages", "staff payment"],
    "insurance":        ["insurance", "premium", "lic"],
    "water":            ["water", "water board"],
}


def score_obligation(ob: Obligation, today: date | None = None) -> Obligation:
    """
    Compute urgency, penalty, and flexibility scores for a single obligation.
    Updates the obligation in place and returns it.
    """
    if today is None:
        today = date.today()

    # ── Urgency Score (0–100) ────────────────────────────────────────────────
    # Days until due: negative means overdue (extra urgent)
    days_until_due = (ob.due_date - today).days

    if days_until_due < 0:
        # Overdue — maximum urgency, scaled by how overdue
        urgency = min(100, 80 + abs(days_until_due) * 2)
    elif days_until_due == 0:
        urgency = 80
    elif days_until_due <= 3:
        urgency = 70
    elif days_until_due <= 7:
        urgency = 60
    elif days_until_due <= 14:
        urgency = 45
    elif days_until_due <= 30:
        urgency = 30
    else:
        urgency = max(5, 30 - (days_until_due - 30) * 0.5)

    ob.urgency_score = round(urgency, 2)

    # ── Penalty Score (0–100) ────────────────────────────────────────────────
    # Based on obligation category (GST, rent, utility, vendor, etc.)
    category = _detect_category(ob.description + " " + ob.counterparty)
    penalty_info = get_penalty(category)

    # Scale: penalty_rate 0 → 0, 0.035 (credit card) → 100
    penalty = min(100, penalty_info["penalty_rate"] * 2857)
    # Add flat bonus for risk level
    penalty += penalty_info["risk_level"] * 8

    ob.penalty_score = round(min(100, penalty), 2)

    # ── Flexibility Score (0–10) ─────────────────────────────────────────────
    # Higher = easier to defer (REDUCES final score)
    flex = 1.0  # Base flexibility

    if penalty_info["is_negotiable"]:
        flex += 2.0
    if ob.vendor_profile:
        if ob.vendor_profile.has_grace_period:
            flex += 2.0 + ob.vendor_profile.grace_days * 0.1
        if ob.vendor_profile.allows_partial:
            flex += 1.0
        if ob.vendor_profile.relationship_type == "long_term":
            flex += 1.5
        if ob.vendor_profile.payment_history == "always_paid":
            flex += 1.0  # Good history → more leeway

    ob.flexibility_score = round(min(10.0, flex), 2)

    # ── Final Composite Score ────────────────────────────────────────────────
    ob.final_score = round(
        (ob.urgency_score * URGENCY_WEIGHT)
        + (ob.penalty_score * PENALTY_WEIGHT)
        - (ob.flexibility_score * FLEXIBILITY_WEIGHT * 10),  # Scale flex to similar range
        2
    )

    return ob


def score_all(obligations: list[Obligation], today: date | None = None) -> list[Obligation]:
    """Score all obligations and return them sorted highest score first."""
    if today is None:
        today = date.today()

    scored = [score_obligation(ob, today) for ob in obligations]
    return sorted(scored, key=lambda o: o.final_score, reverse=True)


def _detect_category(text: str) -> str:
    """
    Detect obligation category from description + counterparty text.
    Returns matching category key or "default".
    """
    text_lower = text.lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            return category
    return "default"
