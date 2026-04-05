"""
llm/client.py
--------------
Thin orchestrator for the LLM output layer.

This module is the single entry point called by the API and frontend.
It delegates to specialist modules:

  tone_selector.py    → selects email tone (deterministic)
  cot_generator.py    → generates COT explanation
  email_drafter.py    → generates negotiation email
  plan_summarizer.py  → generates payment plan narrative
  output_validator.py → validates all LLM outputs

No prompt-building or LLM calls happen directly in this file.
Each specialist module handles its own prompt and API call.
"""

from llm.cot_generator import generate as _generate_cot
from llm.email_drafter import generate as _generate_email
from llm.plan_summarizer import generate as _generate_plan
from llm.output_validator import validate_output, validate_email_output
from llm.tone_selector import select_tone_from_obligation


def generate_cot_explanation(state_dict: dict) -> str:
    """
    Generate a chain-of-thought explanation of the payment decisions.

    Args:
        state_dict: JSON-serialized FinancialState from core/engine.state_to_dict()

    Returns:
        Plain-English explanation string (4-6 sentences).
    """
    text = _generate_cot(state_dict)

    # Validate: check for hallucinated amounts
    result = validate_output(text, state_dict, strict=False)
    if result.hallucinated_amounts:
        print(f"[Client] COT validation warning: {result.summary()}")

    return text


def generate_email(
    obligation_dict: dict,
    business_name: str = "our business",
) -> str:
    """
    Generate a tone-adapted deferral/negotiation email for one obligation.

    The tone is selected deterministically based on vendor relationship type
    BEFORE the LLM is called — the LLM cannot choose its own tone.

    Args:
        obligation_dict: Single obligation from state_dict["obligations"]
        business_name:   Sender's company name (shown in email)

    Returns:
        Complete email text including subject line.
    """
    result = _generate_email(obligation_dict, business_name)
    email_text = result["email_text"]

    # Validate email output
    validation = validate_email_output(
        email_text,
        obligation_dict,
        {"obligations": [obligation_dict]},
        strict=False,
    )
    if not validation.passed or validation.warnings:
        print(f"[Client] Email validation: {validation.summary()}")

    # Log tone selection for transparency
    print(
        f"[Client] Email for {result['vendor']} | "
        f"Tone: {result['tone_label']} | "
        f"Rationale: {result['tone_rationale']}"
    )

    return email_text


def generate_payment_plan(state_dict: dict) -> str:
    """
    Generate a human-readable week-by-week payment plan summary.

    Args:
        state_dict: JSON-serialized FinancialState from core/engine.state_to_dict()

    Returns:
        Multi-paragraph plan narrative string.
    """
    text = _generate_plan(state_dict)

    # Validate plan output
    result = validate_output(text, state_dict, strict=False)
    if result.hallucinated_amounts:
        print(f"[Client] Plan validation warning: {result.summary()}")

    return text


def get_tone_preview(obligation_dict: dict) -> dict:
    """
    Preview which tone would be used for an obligation's email
    without generating the email. Useful for the frontend to display
    tone labels before the user clicks Generate.

    Returns dict with: tone_type, tone_label, tone_rationale
    """
    tone = select_tone_from_obligation(obligation_dict)
    return {
        "tone_type":      tone["type"],
        "tone_label":     tone["label"],
        "tone_rationale": tone["rationale"],
    }
