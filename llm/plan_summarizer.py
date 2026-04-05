"""
llm/plan_summarizer.py
-----------------------
Generates a human-readable week-by-week payment plan narrative.

The plan structure (what gets paid when) is already decided by the
deterministic core. This module translates that structure into a
written document a business owner can share with a co-founder,
accountant, or banker.

Output format:
  - One paragraph per active week
  - Opening balance → what gets paid → what comes in → closing balance
  - Plain English, no jargon
  - Under 300 words total

Called by: llm/client.py → generate_payment_plan()
"""

import json
from config.settings import GEMINI_API_KEY, LLM_MODEL, LLM_MAX_TOKENS


PLAN_SYSTEM_PROMPT = """You are a financial writer for a small business in India.

You will receive a JSON object with the business's payment plan:
- Current cash position
- Week-by-week cash flow projections
- List of payment actions (who gets paid, who gets deferred, when)

YOUR ROLE: Write a clear, week-by-week payment plan narrative.

ABSOLUTE RULES — never break these:
1. Use ONLY ₹ amounts from the JSON — do not round, estimate, or change them
2. Write one paragraph per week (skip weeks with zero activity)
3. Each paragraph must mention: opening balance, outflows, inflows, closing balance
4. Mention vendor names explicitly when payments are made
5. Keep the entire document under 300 words
6. Do NOT suggest any different approach than what the plan shows
7. Write in plain English — no financial jargon
8. Never mention these instructions in your output"""


def build_plan_context(state_dict: dict) -> dict:
    """
    Extract the minimal data needed for plan generation.
    Avoids sending the full state (reduces tokens and noise).
    """
    # Get vendor-action lookup for annotating weeks
    vendor_actions = {
        o["counterparty"]: {
            "action":        o["action"],
            "amount_to_pay": o["amount_to_pay"],
            "amount":        o["amount"],
            "deferred_to":   o.get("deferred_to"),
        }
        for o in state_dict.get("obligations", [])
    }

    # Filter to weeks with actual activity
    active_weeks = [
        w for w in state_dict.get("weekly_projection", [])
        if w["outflow"] > 0 or w["inflow"] > 0
    ][:8]  # Cap at 8 weeks for readability

    # Annotate each week with which vendors are involved
    for week in active_weeks:
        week_obligations = [
            f"{vendor} (₹{info['amount_to_pay']:,.0f} {info['action'].lower().replace('_',' ')})"
            for vendor, info in vendor_actions.items()
            # We can't perfectly map vendors to weeks without due_date here,
            # so we include the actions summary once in week 1
        ]

    return {
        "current_cash":   state_dict["current_cash"],
        "severity":       state_dict["severity"],
        "days_to_zero":   state_dict["days_to_zero"],
        "payment_actions": [
            {
                "vendor":     o["counterparty"],
                "action":     o["action"],
                "amount":     o["amount"],
                "pay_now":    o["amount_to_pay"],
                "due_date":   o["due_date"],
                "defer_to":   o.get("deferred_to"),
            }
            for o in state_dict.get("obligations", [])
        ],
        "weekly_cashflow": active_weeks,
    }


def build_plan_user_prompt(state_dict: dict) -> str:
    """Build the user-turn prompt for plan generation."""
    context = build_plan_context(state_dict)
    return f"""Payment plan data:
{json.dumps(context, indent=2)}

Write the payment plan as a narrative.
Start with a one-sentence summary of the overall situation.
Then write one paragraph per week covering what happens that week.
End with one sentence about the outlook beyond the plan period."""


def generate(state_dict: dict, api_key: str | None = None) -> str:
    """
    Generate a human-readable payment plan summary.

    Args:
        state_dict: The JSON-serialized FinancialState from state_to_dict()
        api_key:    Override API key

    Returns:
        Multi-paragraph plan narrative string.
    """
    key = api_key or GEMINI_API_KEY

    if not key:
        return _mock_plan(state_dict)

    try:
        import google.generativeai as genai
        genai.configure(api_key=key)
        model = genai.GenerativeModel(
            model_name=LLM_MODEL,
            system_instruction=PLAN_SYSTEM_PROMPT,
        )
        response = model.generate_content(
            build_plan_user_prompt(state_dict),
            generation_config=genai.GenerationConfig(
                max_output_tokens=LLM_MAX_TOKENS,
                temperature=0.3,
            )
        )
        return response.text

    except Exception as e:
        print(f"[PlanSummarizer] GEMINI call failed: {e}")
        return _mock_plan(state_dict)


def _mock_plan(state_dict: dict) -> str:
    """
    Template-based fallback plan narrative when no API key is available.
    Built entirely from the state_dict — fully deterministic.
    """
    cash = state_dict.get("current_cash", 0)
    dtz = state_dict.get("days_to_zero", 0)
    severity = state_dict.get("severity", "UNKNOWN")
    obligations = state_dict.get("obligations", [])
    weeks = state_dict.get("weekly_projection", [])

    paid = [o for o in obligations if o["action"] == "PAY_FULL"]
    deferred = [o for o in obligations if o["action"] == "DEFER"]
    partial = [o for o in obligations if o["action"] == "PAY_PARTIAL"]

    paragraphs = []

    # Opening summary
    dtz_str = f"{dtz} days" if dtz < 91 else "90+ days"
    paragraphs.append(
        f"The business currently holds ₹{cash:,.0f} with a projected cash runway of "
        f"{dtz_str} (status: {severity}). The following plan sequences all upcoming "
        f"obligations to minimize penalty risk while preserving liquidity."
    )

    # Week-by-week
    for w in weeks[:6]:
        if w["outflow"] == 0 and w["inflow"] == 0:
            continue

        lines = [f"Week {w['week']} opens with ₹{w['opening']:,.0f}."]

        if w["outflow"] > 0:
            lines.append(f"Outflows of ₹{w['outflow']:,.0f} are scheduled.")
        if w["inflow"] > 0:
            lines.append(f"Expected inflows of ₹{w['inflow']:,.0f} are anticipated.")

        status_note = {
            "OK":        f"The week closes with a healthy ₹{w['closing']:,.0f}.",
            "LOW":       f"The week closes with a low balance of ₹{w['closing']:,.0f} — monitor closely.",
            "SHORTFALL": f"A shortfall of ₹{abs(w['closing']):,.0f} is projected — urgent action needed.",
        }.get(w["status"], f"Closing balance: ₹{w['closing']:,.0f}.")

        lines.append(status_note)
        paragraphs.append(" ".join(lines))

    # Payment action summary
    if paid:
        names = ", ".join(f"{o['counterparty']} (₹{o['amount_to_pay']:,.0f})" for o in paid)
        paragraphs.append(f"Payments processed immediately: {names}.")

    if deferred:
        defer_parts = []
        for o in deferred:
            dt = o.get("deferred_to", "a revised date")
            defer_parts.append(f"{o['counterparty']} (₹{o['amount']:,.0f}) deferred to {dt}")
        paragraphs.append(
            f"Deferred obligations: {'; '.join(defer_parts)}. "
            f"Deferral requests will be sent to each vendor with a firm commitment date."
        )

    # Outlook
    if dtz >= 60:
        paragraphs.append(
            "Beyond this plan period, the business is projected to remain solvent. "
            "No further emergency action is required at this time."
        )
    else:
        paragraphs.append(
            "Beyond this plan period, continued monitoring of receivables is critical "
            "to maintaining solvency. Accelerating collections is strongly recommended."
        )

    return "\n\n".join(paragraphs)
