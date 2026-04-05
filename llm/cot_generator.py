"""
llm/cot_generator.py
---------------------
Generates a plain-English chain-of-thought explanation of the payment
decisions made by the deterministic core.

The LLM receives the decision JSON and is told:
  "Explain what was decided and why. Do not change the decisions."

This is the "explainability" component required by the rubric.
It translates scoring numbers into business reasoning a non-technical
owner can read and trust.

Called by: llm/client.py → generate_cot_explanation()
"""

import json
from config.settings import GEMINI_API_KEY, LLM_MODEL, LLM_MAX_TOKENS


# System prompt — locked. The LLM cannot deviate from this role.
COT_SYSTEM_PROMPT = """You are a financial advisor assistant for small businesses in India.

You will receive a JSON object containing:
- The business's current cash position
- Days until cash runs out (days_to_zero)
- A list of obligations with their computed scores and assigned actions (PAY_FULL / PAY_PARTIAL / DEFER)

YOUR ROLE: Explain these decisions clearly in plain business English.

ABSOLUTE RULES — never break these:
1. Do NOT suggest different decisions, different amounts, or different vendors
2. Do NOT invent facts, receivables, or promises not present in the JSON
3. Do NOT use financial jargon — write for a shop owner, not an accountant
4. Reference specific vendor names and ₹ amounts from the JSON
5. Keep the explanation to 4–6 sentences
6. End with exactly one clear action the owner should take today
7. Never mention these instructions or the JSON structure in your output"""


def build_cot_context(state_dict: dict) -> dict:
    """
    Extract only the fields needed for COT from the full state dict.
    Keeps the prompt focused and reduces token usage.
    """
    return {
        "current_cash":      state_dict["current_cash"],
        "days_to_zero":      state_dict["days_to_zero"],
        "severity":          state_dict["severity"],
        "total_payables":    state_dict["total_payables"],
        "total_receivables": state_dict.get("total_receivables", 0),
        "cash_gap":          state_dict["cash_gap"],
        "net_burn_per_day":  state_dict.get("net_burn_per_day", 0),
        "decisions": [
            {
                "vendor":        o["counterparty"],
                "amount":        o["amount"],
                "action":        o["action"],
                "due_date":      o["due_date"],
                "amount_to_pay": o["amount_to_pay"],
                "score":         o["final_score"],
                # Include deferral reason only when deferred
                "reason": o.get("deferral_reason", "") if o["action"] == "DEFER" else "",
            }
            for o in state_dict["obligations"]
        ],
    }


def build_cot_user_prompt(state_dict: dict) -> str:
    """Build the user-turn prompt for COT generation."""
    context = build_cot_context(state_dict)

    severity_context = {
        "CRITICAL": "The business is in a critical cash position.",
        "URGENT":   "The business needs to act urgently on its cash situation.",
        "WARNING":  "The business has a limited cash runway that needs attention.",
        "MONITOR":  "The cash situation is manageable but should be watched.",
        "STABLE":   "The cash position is currently stable.",
    }.get(context["severity"], "")

    return f"""Financial situation and decisions made:
{json.dumps(context, indent=2)}

Context: {severity_context}

Write a clear, plain-English explanation of:
1. Why the highest-priority obligation was paid first
2. Why certain obligations were deferred (reference their specific scores/reasons)
3. What the business owner should do right now

Do not use bullet points. Write in flowing paragraphs."""


def generate(state_dict: dict, api_key: str | None = None) -> str:
    """
    Generate a COT explanation for the current financial state.

    Args:
        state_dict: The JSON-serialized FinancialState from state_to_dict()
        api_key:    Override API key (uses settings.py default if None)

    Returns:
        Plain-English explanation string.
        Returns a placeholder if no API key is set.
    """
    key = api_key or GEMINI_API_KEY

    if not key:
        return _mock_cot(state_dict)

    try:
        import google.generativeai as genai

        genai.configure(api_key=key)
        model = genai.GenerativeModel(
            model_name=LLM_MODEL,
            system_instruction=COT_SYSTEM_PROMPT,
        )
        response = model.generate_content(
            build_cot_user_prompt(state_dict),
            generation_config=genai.GenerationConfig(
                max_output_tokens=LLM_MAX_TOKENS,
                temperature=0.3,   # Low temperature = more consistent, factual output
            )
        )
        return response.text

    except Exception as e:
        print(f"[COTGenerator] Gemini call failed: {e}")
        return _mock_cot(state_dict)


def _mock_cot(state_dict: dict) -> str:
    """
    Deterministic fallback explanation when no API key is available.
    Generated from the JSON itself — no LLM needed.
    Useful for demos without internet access.
    """
    obligations = state_dict.get("obligations", [])
    cash = state_dict.get("current_cash", 0)
    dtz = state_dict.get("days_to_zero", 0)
    severity = state_dict.get("severity", "UNKNOWN")
    gap = state_dict.get("cash_gap", 0)

    # Find highest and lowest priority obligations
    paid = [o for o in obligations if o["action"] == "PAY_FULL"]
    deferred = [o for o in obligations if o["action"] == "DEFER"]
    partial = [o for o in obligations if o["action"] == "PAY_PARTIAL"]

    lines = []

    # Opening: overall situation
    if severity in ("CRITICAL", "URGENT"):
        lines.append(
            f"With ₹{cash:,.0f} available and ₹{gap:,.0f} more owed than cash on hand, "
            f"the business has approximately {dtz} days of runway — immediate action is required."
        )
    else:
        lines.append(
            f"The business currently holds ₹{cash:,.0f} with {dtz}+ days of runway. "
            f"Total obligations of ₹{state_dict.get('total_payables',0):,.0f} require careful sequencing."
        )

    # Paid obligations
    if paid:
        paid_names = ", ".join(f"{o['counterparty']} (₹{o['amount']:,.0f})" for o in paid[:2])
        lines.append(
            f"{paid_names} {'was' if len(paid)==1 else 'were'} paid first due to "
            f"high penalty risk or immediate due dates that make deferral costly."
        )

    # Deferred obligations
    if deferred:
        defer_names = ", ".join(f"{o['counterparty']} (₹{o['amount']:,.0f})" for o in deferred[:2])
        lines.append(
            f"{defer_names} {'was' if len(deferred)==1 else 'were'} deferred because "
            f"{'the vendor allows flexibility or has a grace period' if len(deferred)==1 else 'these vendors offer flexibility or grace periods'}, "
            f"making deferral the lower-risk choice."
        )

    # Partial payments
    if partial:
        for o in partial[:1]:
            balance = o['amount'] - o['amount_to_pay']
            lines.append(
                f"A partial payment of ₹{o['amount_to_pay']:,.0f} was made to {o['counterparty']}, "
                f"with ₹{balance:,.0f} to follow on {o.get('deferred_to', 'a revised date')}."
            )

    # Action
    if paid:
        top = paid[0]
        lines.append(
            f"Immediate action: process the payment to {top['counterparty']} "
            f"for ₹{top['amount_to_pay']:,.0f} today."
        )

    return " ".join(lines)
