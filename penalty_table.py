"""
config/penalty_table.py
-----------------------
Penalty rates and risk scores by obligation category.
These are domain-knowledge constants — not computed by the LLM.

penalty_rate: daily cost as % of amount if deferred (e.g. 0.015 = 1.5%/month)
risk_level:   qualitative risk if not paid (1=low, 5=critical)
is_negotiable: whether deferral is realistically possible
"""

PENALTY_TABLE = {
    # Tax / Government — highest penalty, non-negotiable
    "gst":              {"penalty_rate": 0.018, "risk_level": 5, "is_negotiable": False},
    "income_tax":       {"penalty_rate": 0.015, "risk_level": 5, "is_negotiable": False},
    "tds":              {"penalty_rate": 0.015, "risk_level": 5, "is_negotiable": False},
    "professional_tax": {"penalty_rate": 0.010, "risk_level": 4, "is_negotiable": False},

    # Utilities — service disruption risk
    "electricity":      {"penalty_rate": 0.020, "risk_level": 4, "is_negotiable": False},
    "internet":         {"penalty_rate": 0.005, "risk_level": 3, "is_negotiable": True},
    "water":            {"penalty_rate": 0.008, "risk_level": 3, "is_negotiable": False},

    # Rent / Property — eviction risk
    "rent":             {"penalty_rate": 0.000, "risk_level": 4, "is_negotiable": True},

    # Loan / Finance — credit score damage
    "loan_emi":         {"penalty_rate": 0.020, "risk_level": 5, "is_negotiable": False},
    "credit_card":      {"penalty_rate": 0.035, "risk_level": 4, "is_negotiable": False},

    # Vendor payments — relationship risk
    "vendor_invoice":   {"penalty_rate": 0.000, "risk_level": 2, "is_negotiable": True},
    "supplier":         {"penalty_rate": 0.000, "risk_level": 3, "is_negotiable": True},

    # Payroll — legal and morale risk
    "payroll":          {"penalty_rate": 0.000, "risk_level": 5, "is_negotiable": False},

    # Insurance
    "insurance":        {"penalty_rate": 0.010, "risk_level": 3, "is_negotiable": False},

    # Default for unknown categories
    "default":          {"penalty_rate": 0.005, "risk_level": 2, "is_negotiable": True},
}


def get_penalty(category: str) -> dict:
    """Return penalty info for a category, falling back to default."""
    key = category.lower().strip().replace(" ", "_")
    return PENALTY_TABLE.get(key, PENALTY_TABLE["default"])
