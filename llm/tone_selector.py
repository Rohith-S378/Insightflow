"""
llm/tone_selector.py
---------------------
Deterministic tone selection based on vendor relationship type.

This is a LOOKUP, not an LLM call.
The LLM never decides its own tone — this module does it,
and injects the result into the prompt before the LLM sees it.

Called by: email_drafter.py
"""

from config.tone_instructions import TONE_INSTRUCTIONS


# Human-readable labels shown in the UI next to each vendor
TONE_LABELS = {
    "long_term":  "Warm & collaborative",
    "new":        "Formal & professional",
    "critical":   "Urgent & reassuring",
    "occasional": "Polite & brief",
    "unknown":    "Professional (default)",
}

# Short rationale shown in the COT explanation
TONE_RATIONALE = {
    "long_term":  (
        "This vendor has a long-standing relationship with the business. "
        "A warm, collaborative tone acknowledges the partnership and frames "
        "the delay as temporary."
    ),
    "new":        (
        "This is a new vendor with limited relationship history. "
        "A formal, professional tone sets clear expectations and builds trust "
        "early in the relationship."
    ),
    "critical":   (
        "This vendor is operationally critical. Missing this payment could "
        "disrupt business continuity. The tone is urgent and highly reassuring, "
        "with a partial payment offered where possible."
    ),
    "occasional": (
        "This vendor is used occasionally. A polite, brief tone respects their "
        "time and communicates the revised date clearly without over-explaining."
    ),
    "unknown":    (
        "No relationship profile found for this vendor. "
        "A neutral professional tone is used as a safe default."
    ),
}


def select_tone(relationship_type: str) -> dict:
    """
    Return the full tone context for a given vendor relationship type.

    Args:
        relationship_type: "long_term" | "new" | "critical" | "occasional" | "unknown"

    Returns a dict with:
        type:        the relationship type (normalized)
        instruction: the full tone prompt text injected into the LLM
        label:       short human-readable label for the UI
        rationale:   one-sentence explanation of why this tone was chosen
    """
    # Normalize input — treat any unrecognized type as "unknown"
    key = relationship_type.lower().strip()
    if key not in TONE_INSTRUCTIONS:
        print(f"[ToneSelector] Unknown relationship type '{relationship_type}' — using 'unknown'")
        key = "unknown"

    return {
        "type":        key,
        "instruction": TONE_INSTRUCTIONS[key],
        "label":       TONE_LABELS[key],
        "rationale":   TONE_RATIONALE[key],
    }


def select_tone_from_obligation(obligation_dict: dict) -> dict:
    """
    Convenience wrapper: extract relationship type from an obligation dict
    and return the tone context.

    obligation_dict is the JSON-serialized Obligation from state_to_dict().
    """
    vendor_profile = obligation_dict.get("vendor_profile") or {}
    rel_type = vendor_profile.get("relationship_type", "unknown")
    return select_tone(rel_type)


def get_all_tones() -> dict:
    """
    Return all tone definitions.
    Used by the frontend to display a tone reference table.
    """
    return {
        key: {
            "label":     TONE_LABELS[key],
            "rationale": TONE_RATIONALE[key],
        }
        for key in TONE_INSTRUCTIONS
    }
