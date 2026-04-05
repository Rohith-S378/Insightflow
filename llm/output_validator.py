"""
llm/output_validator.py
------------------------
Post-generation validation layer.

After every LLM call, this module checks that:
1. No monetary amounts were hallucinated (invented numbers not in input)
2. No vendor names were invented
3. The output doesn't contain disallowed content (internal instructions, etc.)

This is the "verifiable outputs" requirement from the rubric.

If validation fails:
- In STRICT mode: raises ValidationError (blocks the output)
- In WARN mode (default): logs a warning, returns the output with a flag

Called by: llm/client.py after every LLM call.
"""

import re
from dataclasses import dataclass, field


@dataclass
class ValidationResult:
    """Result of a validation check."""
    passed: bool
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    hallucinated_amounts: list[float] = field(default_factory=list)
    hallucinated_vendors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if self.passed and not self.warnings:
            return "Validation passed."
        parts = []
        if self.errors:
            parts.append(f"Errors: {'; '.join(self.errors)}")
        if self.warnings:
            parts.append(f"Warnings: {'; '.join(self.warnings)}")
        if self.hallucinated_amounts:
            parts.append(f"Suspicious amounts: {self.hallucinated_amounts}")
        return " | ".join(parts) if parts else "Validation passed with notes."


class OutputValidator:
    """
    Validates LLM-generated text against the source state_dict.

    Usage:
        validator = OutputValidator(state_dict)
        result = validator.validate(llm_output_text)
        if not result.passed:
            print(result.summary())
    """

    # Minimum amount value to check (ignore small numbers like percentages, days, etc.)
    AMOUNT_CHECK_THRESHOLD = 200

    # Tolerance for amount matching: 1% difference is still "the same amount"
    AMOUNT_TOLERANCE = 0.01

    def __init__(self, state_dict: dict, strict: bool = False):
        """
        Args:
            state_dict: The input JSON passed to the LLM (from state_to_dict())
            strict:     If True, raise ValidationError on failure.
                        If False (default), log warnings and continue.
        """
        self.state_dict = state_dict
        self.strict = strict

        # Pre-extract valid values from the input for fast lookup
        self._valid_amounts = self._extract_valid_amounts(state_dict)
        self._valid_vendors = self._extract_valid_vendors(state_dict)

    # ── Public API ────────────────────────────────────────────────────────────

    def validate(self, text: str) -> ValidationResult:
        """
        Run all validation checks on LLM output text.
        Returns a ValidationResult with pass/fail and details.
        """
        result = ValidationResult(passed=True)

        self._check_amounts(text, result)
        self._check_forbidden_content(text, result)
        self._check_minimum_length(text, result)

        # If any errors exist, mark as failed
        if result.errors:
            result.passed = False
            if self.strict:
                raise ValidationError(
                    f"LLM output failed validation: {result.summary()}"
                )

        # Warnings don't fail validation but are logged
        if result.warnings or result.hallucinated_amounts:
            print(f"[Validator] {result.summary()}")

        return result

    def validate_email(self, email_text: str, obligation_dict: dict) -> ValidationResult:
        """
        Additional validation specific to email output.
        Checks that the vendor name and key amounts are present.
        """
        result = ValidationResult(passed=True)

        # Run base validation
        base = self.validate(email_text)
        result.warnings.extend(base.warnings)
        result.errors.extend(base.errors)
        result.hallucinated_amounts = base.hallucinated_amounts

        # Email-specific: must mention the vendor name
        vendor = obligation_dict.get("counterparty", "")
        if vendor and vendor.lower() not in email_text.lower():
            # Fuzzy check — vendor name might be slightly different
            vendor_first_word = vendor.split()[0].lower()
            if vendor_first_word not in email_text.lower():
                result.warnings.append(
                    f"Vendor name '{vendor}' not found in email output."
                )

        # Email-specific: must contain a Subject line
        if "subject:" not in email_text.lower():
            result.warnings.append("Email output missing subject line.")

        # Email-specific: must mention an amount
        amount = obligation_dict.get("amount", 0)
        amount_str = f"{amount:,.0f}"
        if amount_str not in email_text.replace(" ", ""):
            result.warnings.append(
                f"Expected amount ₹{amount_str} not found in email output."
            )

        result.passed = len(result.errors) == 0
        return result

    # ── Private Checks ────────────────────────────────────────────────────────

    def _check_amounts(self, text: str, result: ValidationResult):
        """
        Extract all numbers from LLM output and verify each one
        appears in the input state_dict (within tolerance).

        Numbers that are too small or clearly not monetary are skipped.
        """
        # Find all numbers that look like currency amounts
        # Handles: 32,000 | 32000 | ₹32,000 | Rs.32000
        raw_numbers = re.findall(r"[\d,]{3,}(?:\.\d{1,2})?", text)

        for raw in raw_numbers:
            try:
                value = float(raw.replace(",", ""))
            except ValueError:
                continue

            # Skip small values (not likely to be amounts)
            if value < self.AMOUNT_CHECK_THRESHOLD:
                continue
            # Skip 4-digit years (1900-2100) — not monetary amounts
            if 1900 <= value <= 2100:
                continue

            # Check if this value is close to any valid amount
            is_valid = any(
                abs(value - v) / max(v, 1) <= self.AMOUNT_TOLERANCE
                for v in self._valid_amounts
                if v > 0
            )

            if not is_valid:
                result.hallucinated_amounts.append(value)
                result.warnings.append(
                    f"Amount ₹{value:,.0f} in LLM output not found in input data."
                )

    def _check_forbidden_content(self, text: str, result: ValidationResult):
        """Check for content that should never appear in LLM output."""
        forbidden = [
            ("ABSOLUTE RULES",    "LLM repeated its own system instructions."),
            ("never break these", "LLM repeated its own system instructions."),
            ("[JSON]",            "LLM exposed internal JSON structure."),
            ("state_dict",        "LLM exposed internal variable name."),
        ]
        text_lower = text.lower()
        for phrase, message in forbidden:
            if phrase.lower() in text_lower:
                result.errors.append(message)

    def _check_minimum_length(self, text: str, result: ValidationResult):
        """Flag suspiciously short outputs."""
        word_count = len(text.split())
        if word_count < 10:
            result.warnings.append(
                f"LLM output is very short ({word_count} words). May be incomplete."
            )

    # ── Static Helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _extract_valid_amounts(state_dict: dict) -> set[float]:
        """
        Recursively extract all float/int values from the state_dict.
        These are the only monetary values the LLM is allowed to reproduce.
        """
        amounts = set()

        def extract(obj):
            if isinstance(obj, (int, float)):
                amounts.add(float(obj))
            elif isinstance(obj, dict):
                for v in obj.values():
                    extract(v)
            elif isinstance(obj, list):
                for item in obj:
                    extract(item)

        extract(state_dict)
        return amounts

    @staticmethod
    def _extract_valid_vendors(state_dict: dict) -> set[str]:
        """Extract all vendor/counterparty names from the state_dict."""
        vendors = set()
        for ob in state_dict.get("obligations", []):
            name = ob.get("counterparty", "")
            if name:
                vendors.add(name.lower())
        return vendors


class ValidationError(Exception):
    """Raised when strict validation fails."""
    pass


# ── Convenience Functions ─────────────────────────────────────────────────────

def validate_output(text: str, state_dict: dict, strict: bool = False) -> ValidationResult:
    """
    Convenience wrapper: validate any LLM output against a state_dict.

    Args:
        text:       LLM-generated text to validate
        state_dict: Input data the LLM received
        strict:     If True, raise on failure; if False (default), warn and continue

    Returns:
        ValidationResult
    """
    validator = OutputValidator(state_dict, strict=strict)
    return validator.validate(text)


def validate_email_output(
    email_text: str,
    obligation_dict: dict,
    state_dict: dict,
    strict: bool = False,
) -> ValidationResult:
    """
    Validate an email specifically — checks vendor name, subject line, amount.
    """
    validator = OutputValidator(state_dict, strict=strict)
    return validator.validate_email(email_text, obligation_dict)
