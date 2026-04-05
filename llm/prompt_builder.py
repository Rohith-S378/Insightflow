"""
llm/prompt_builder.py
----------------------
Assembles the complete prompt sent to the LLM.
The LLM only receives structured JSON + tone instructions.
It cannot alter decisions — only explain and communicate them.
"""

import json
from config.tone_instructions import get_tone_instruction


def build_cot_prompt(state_dict: dict) -> tuple[str, str]:
    """
    Build the system + user prompt for chain-of-thought explanation.
    Returns (system_prompt, user_prompt).
    """
    system = """You are a financial advisor assistant for small businesses.
You will receive a JSON object containing financial decisions already made by a deterministic engine.

YOUR ONLY JOB: Explain these decisions clearly in plain business English.

STRICT RULES:
- Do NOT suggest different decisions or different amounts
- Do NOT invent facts, projections, or numbers not in the JSON
- Do NOT use financial jargon — write for a non-technical business owner
- Reference specific vendor names and rupee amounts from the JSON
- Keep your explanation to 4–6 sentences
- End with one clear action the owner should take today"""

    # Include only the most relevant fields for COT
    cot_context = {
        "current_cash": state_dict["current_cash"],
        "days_to_zero": state_dict["days_to_zero"],
        "severity": state_dict["severity"],
        "total_payables": state_dict["total_payables"],
        "cash_gap": state_dict["cash_gap"],
        "decisions": [
            {
                "vendor": o["counterparty"],
                "amount": o["amount"],
                "action": o["action"],
                "due_date": o["due_date"],
                "reason": o.get("deferral_reason", ""),
            }
            for o in state_dict["obligations"]
        ]
    }

    user = f"""Financial state and decisions:
{json.dumps(cot_context, indent=2)}

Write a clear, plain-English explanation of why these obligations were prioritized this way.
Start with the most urgent situation, then explain the key trade-offs made."""

    return system, user


def build_email_prompt(obligation_dict: dict, business_name: str = "our business") -> tuple[str, str]:
    """
    Build the system + user prompt for a deferral/negotiation email.
    The tone instruction is injected based on vendor relationship type.
    """
    vendor_type = "unknown"
    if obligation_dict.get("vendor_profile"):
        vendor_type = obligation_dict["vendor_profile"].get("relationship_type", "unknown")

    tone_instruction = get_tone_instruction(vendor_type)

    system = f"""You are a professional email writer for a small business owner.
{tone_instruction}

STRICT RULES:
- Use ONLY the facts provided. Do NOT invent payment history, reasons, or promises.
- Include the EXACT rupee amount and EXACT proposed date from the data.
- Keep the email under 150 words.
- Write in first person as the business owner.
- Do NOT mention financial distress or cash flow problems explicitly.
- End with a specific commitment date and a polite close."""

    deferred_to = obligation_dict.get("deferred_to", "a revised date")
    amount = obligation_dict.get("amount", 0)
    amount_to_pay = obligation_dict.get("amount_to_pay", 0)
    balance = amount - amount_to_pay

    user = f"""Write a payment deferral email with these details:
- Vendor name: {obligation_dict['counterparty']}
- Original amount due: ₹{amount:,.0f}
- Original due date: {obligation_dict['due_date']}
- Proposed new date: {deferred_to}
- Partial payment being made now: ₹{amount_to_pay:,.0f} (0 means no partial payment)
- Balance remaining: ₹{balance:,.0f}
- Context: {obligation_dict.get('deferral_reason', 'temporary cash flow timing')}

Write the complete email including subject line."""

    return system, user


def build_plan_prompt(state_dict: dict) -> tuple[str, str]:
    """
    Build the system + user prompt for a payment plan summary.
    """
    system = """You are writing a payment plan summary for a small business owner.
The plan has already been computed. Your job is to express it clearly in plain English.

STRICT RULES:
- Use ONLY rupee amounts from the JSON — do not round or approximate
- Write one paragraph per week (only include weeks with activity)
- Mention vendor names explicitly
- Do NOT suggest any different approach than what the plan shows
- Keep the entire summary under 300 words"""

    # Filter to weeks with activity
    active_weeks = [
        w for w in state_dict["weekly_projection"]
        if w["outflow"] > 0 or w["inflow"] > 0
    ][:6]  # First 6 active weeks

    plan_context = {
        "current_cash": state_dict["current_cash"],
        "severity": state_dict["severity"],
        "actions": [
            {
                "vendor": o["counterparty"],
                "action": o["action"],
                "amount_to_pay": o["amount_to_pay"],
                "deferred_to": o.get("deferred_to"),
                "due_date": o["due_date"],
            }
            for o in state_dict["obligations"]
        ],
        "weekly_cashflow": active_weeks,
    }

    user = f"""Create a payment plan summary from this data:
{json.dumps(plan_context, indent=2)}

Write it as a week-by-week narrative. Each paragraph covers one week.
Include: what gets paid, what gets deferred, expected inflows, and closing balance."""

    return system, user
