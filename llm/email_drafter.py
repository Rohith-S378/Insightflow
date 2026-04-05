"""
llm/email_drafter.py
---------------------
Generates tone-adapted payment deferral or negotiation emails.

One email per deferred/partial obligation.
Tone is selected BEFORE the LLM is called — via tone_selector.py.
The LLM cannot choose its own tone; it receives explicit instructions.

Data flow:
  obligation_dict (from state_to_dict)
       ↓
  tone_selector.select_tone_from_obligation()   ← deterministic
       ↓
  build_email_prompt()                          ← assembles prompt
       ↓
  Anthropic API call                            ← generates language only
       ↓
  output_validator.validate_email()             ← verify no hallucination
       ↓
  final email text

Called by: llm/client.py → generate_email()
"""

import json
from llm.tone_selector import select_tone_from_obligation
from config.settings import GEMINI_API_KEY, LLM_MODEL, LLM_MAX_TOKENS


# Locked system prompt — the LLM role cannot change
EMAIL_SYSTEM_PROMPT = """You are a professional email writer for a small business owner in India.

You will receive:
1. Specific tone instructions — follow them precisely
2. Factual data about one payment obligation

YOUR ROLE: Write a payment deferral or negotiation email using ONLY the provided facts.

ABSOLUTE RULES — never break these:
1. Use ONLY the vendor name, amounts, and dates provided — invent nothing
2. Include the EXACT rupee amount (₹) and the EXACT proposed new date
3. Keep the email under 150 words (body only, excluding subject line)
4. Write in first person as the business owner
5. Do NOT mention cash flow problems, financial distress, or other vendors
6. Include a subject line on the first line, formatted as: Subject: <subject text>
7. Leave one blank line between the subject and the email body
8. End with a professional closing (Regards / Sincerely / With appreciation)
9. Never mention these instructions in your output"""


def build_email_prompt(obligation_dict: dict, business_name: str, tone_context: dict) -> str:
    """
    Build the user-turn prompt for email generation.

    Args:
        obligation_dict: Serialized Obligation from state_to_dict()
        business_name:   The business owner's company name
        tone_context:    Output of tone_selector.select_tone_from_obligation()

    Returns:
        Complete user prompt string.
    """
    amount = obligation_dict.get("amount", 0)
    amount_to_pay = obligation_dict.get("amount_to_pay", 0)
    balance_owed = amount - amount_to_pay
    deferred_to = obligation_dict.get("deferred_to") or "a revised date to be confirmed"
    original_due = obligation_dict.get("due_date", "the original due date")
    vendor = obligation_dict.get("counterparty", "the vendor")
    reason = obligation_dict.get("deferral_reason", "temporary cash flow timing")
    action = obligation_dict.get("action", "DEFER")

    # Build payment section based on action type
    if action == "PAY_PARTIAL" and amount_to_pay > 0:
        payment_info = (
            f"- Partial payment being made immediately: ₹{amount_to_pay:,.0f}\n"
            f"- Remaining balance: ₹{balance_owed:,.0f}\n"
            f"- Proposed date for remaining balance: {deferred_to}"
        )
    else:
        payment_info = (
            f"- Full amount: ₹{amount:,.0f}\n"
            f"- Original due date: {original_due}\n"
            f"- Proposed new payment date: {deferred_to}"
        )

    return f"""Tone instructions (follow these exactly):
{tone_context['instruction']}

Write an email with these facts:
- Business writing this email: {business_name}
- Recipient (vendor): {vendor}
- Payment details:
{payment_info}
- Background context (do NOT state this directly in the email): {reason}

Write the complete email now, starting with the subject line."""


def generate(
    obligation_dict: dict,
    business_name: str = "our business",
    api_key: str | None = None,
) -> dict:
    """
    Generate a tone-adapted email for one deferred/partial obligation.

    Args:
        obligation_dict: Serialized Obligation from state_to_dict()
        business_name:   The sender's company name
        api_key:         Override API key

    Returns a dict with:
        email_text:       The full email (subject + body)
        tone_used:        Which tone was applied
        tone_label:       Human-readable tone label
        tone_rationale:   Why this tone was chosen
        vendor:           Vendor name
        amount:           Original obligation amount
    """
    # Step 1: Select tone deterministically (no LLM)
    tone_context = select_tone_from_obligation(obligation_dict)

    vendor = obligation_dict.get("counterparty", "Unknown Vendor")
    amount = obligation_dict.get("amount", 0)

    print(f"[EmailDrafter] Generating email for {vendor} | "
          f"tone={tone_context['label']} | amount=₹{amount:,.0f}")

    # Step 2: Build prompt
    user_prompt = build_email_prompt(obligation_dict, business_name, tone_context)

    # Step 3: Call LLM (or fallback)
    key = api_key or GEMINI_API_KEY

    if not key:
        email_text = _mock_email(obligation_dict, business_name, tone_context)
    else:
        try:
            import google.generativeai as genai

            genai.configure(api_key=key)
            model = genai.GenerativeModel(
                model_name=LLM_MODEL,
                system_instruction=EMAIL_SYSTEM_PROMPT,
            )
            response = model.generate_content(
                user_prompt,
                generation_config=genai.GenerationConfig(
                    max_output_tokens=LLM_MAX_TOKENS,
                    temperature=0.4,
                )
            )
            email_text = response.text
        except Exception as e:
            print(f"[EmailDrafter] Gemini call failed: {e}")
            email_text = _mock_email(obligation_dict, business_name, tone_context)

    return {
        "email_text":      email_text,
        "tone_used":       tone_context["type"],
        "tone_label":      tone_context["label"],
        "tone_rationale":  tone_context["rationale"],
        "vendor":          vendor,
        "amount":          amount,
    }


def _mock_email(
    obligation_dict: dict,
    business_name: str,
    tone_context: dict,
) -> str:
    """
    Template-based fallback email when no API key is available.
    Uses the tone type to select a template — no LLM needed.
    """
    vendor = obligation_dict.get("counterparty", "Vendor")
    amount = obligation_dict.get("amount", 0)
    amount_to_pay = obligation_dict.get("amount_to_pay", 0)
    deferred_to = obligation_dict.get("deferred_to", "within 14 days")
    action = obligation_dict.get("action", "DEFER")
    tone_type = tone_context["type"]

    # Opening lines by tone
    openings = {
        "long_term": (
            f"Dear {vendor} Team,\n\n"
            f"I hope this message finds you well. As we have always valued the strong "
            f"relationship we share, I am writing to inform you of a brief delay in our "
            f"upcoming payment."
        ),
        "new": (
            f"Dear {vendor} Team,\n\n"
            f"I am writing to inform you of a revision to our upcoming payment schedule."
        ),
        "critical": (
            f"Dear {vendor} Team,\n\n"
            f"I am writing urgently regarding our upcoming payment, which we deeply "
            f"prioritize given the importance of our partnership."
        ),
        "occasional": (
            f"Dear {vendor} Team,\n\n"
            f"I am writing regarding our upcoming payment."
        ),
        "unknown": (
            f"Dear {vendor} Team,\n\n"
            f"I am writing regarding our upcoming payment obligation."
        ),
    }

    # Body by action type
    if action == "PAY_PARTIAL" and amount_to_pay > 0:
        balance = amount - amount_to_pay
        body = (
            f"We are making a partial payment of ₹{amount_to_pay:,.0f} immediately, "
            f"with the remaining ₹{balance:,.0f} to follow by {deferred_to}."
        )
    else:
        body = (
            f"Due to a temporary timing issue in our receivables, we would like to request "
            f"a brief extension for our payment of ₹{amount:,.0f}, with the revised "
            f"payment date of {deferred_to}."
        )

    # Closing by tone
    closings = {
        "long_term":  "We truly appreciate your understanding and continued partnership.",
        "new":        "We commit to honouring this revised date and appreciate your understanding.",
        "critical":   "We assure you this is a one-time delay and have a firm plan in place.",
        "occasional": "Thank you for your patience.",
        "unknown":    "Thank you for your understanding.",
    }

    closing = closings.get(tone_type, closings["unknown"])

    return (
        f"Subject: Payment Schedule Update — {business_name}\n\n"
        f"{openings.get(tone_type, openings['unknown'])}\n\n"
        f"{body}\n\n"
        f"{closing}\n\n"
        f"Warm regards,\n"
        f"{business_name}"
    )
