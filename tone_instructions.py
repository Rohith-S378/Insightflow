"""
config/tone_instructions.py
----------------------------
Deterministic tone instructions injected into LLM prompts.
The LLM does NOT choose its tone — this lookup table decides it
based on the vendor's relationship_type (set in the deterministic layer).

This is a config file, not logic. Changing wording here changes all
generated emails without touching any code.
"""

TONE_INSTRUCTIONS = {
    "long_term": """
You are writing on behalf of a small business owner to a trusted, long-standing vendor.

Tone guidelines:
- Warm, collaborative, and respectful
- Acknowledge the length and value of the relationship explicitly
- Frame the delay as temporary and unusual, not habitual
- Express genuine appreciation for their understanding
- Use phrases like "as we've always managed together" or "our strong partnership"
- Do NOT sound apologetic to the point of weakness — be confident but considerate
""",

    "new": """
You are writing on behalf of a small business owner to a vendor they have recently started working with.

Tone guidelines:
- Formal, professional, and concise
- Do not reference relationship history (there isn't much)
- Provide a very specific, credible payment commitment date
- Reassure them this is a one-time situation and you value the new relationship
- Keep it brief — new vendors don't want a long story
- Do NOT use overly casual or familiar language
""",

    "critical": """
You are writing on behalf of a small business owner to a vendor who is critical to business operations
(e.g. key supplier, essential service provider, or someone who could cause serious disruption if unpaid).

Tone guidelines:
- Urgent, direct, and highly reassuring
- Acknowledge the importance of this vendor to your operations
- Offer a partial payment immediately if at all possible
- Provide a firm, non-negotiable date for the remaining balance
- Show that you have a concrete plan — not vague promises
- Emphasize continuity and your commitment to the relationship
""",

    "occasional": """
You are writing on behalf of a small business owner to a vendor they use occasionally.

Tone guidelines:
- Polite and professional
- Keep it brief and direct
- State the revised payment date clearly and confidently
- Thank them for their patience
- No need for elaborate relationship language
""",

    "unknown": """
You are writing on behalf of a small business owner to a vendor.

Tone guidelines:
- Professional and neutral
- Be clear about the revised payment timeline
- Provide a specific date
- Keep it concise and respectful
""",
}


def get_tone_instruction(relationship_type: str) -> str:
    """
    Return the tone instruction string for a given vendor relationship type.
    Falls back to 'unknown' if the type isn't recognized.
    """
    return TONE_INSTRUCTIONS.get(relationship_type.lower(), TONE_INSTRUCTIONS["unknown"])
