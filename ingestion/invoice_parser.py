"""
ingestion/invoice_parser.py
----------------------------
Parses digital invoice PDFs into Obligation objects.
Uses pdfplumber for text extraction and regex for field extraction.
"""

import re
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from data.models import Transaction


DATE_FORMATS = [
    "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d",
    "%d %b %Y", "%d-%b-%Y", "%B %d, %Y",
    "%d/%m/%y", "%d-%m-%y",
]


def parse_invoice(file_path: str) -> Transaction | None:
    """
    Parse a digital invoice PDF and return a payable Transaction.
    Returns None if parsing fails.
    """
    try:
        import pdfplumber
    except ImportError:
        raise ImportError("Install pdfplumber: pip install pdfplumber")

    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Invoice not found: {file_path}")

    with pdfplumber.open(file_path) as pdf:
        # Extract all text from all pages
        full_text = "\n".join(
            page.extract_text() or "" for page in pdf.pages
        )

    return _extract_from_text(full_text, source_file=path.name)


def parse_invoice_from_text(text: str, filename: str = "invoice") -> Transaction | None:
    """Parse invoice from raw text content (for testing or API uploads)."""
    return _extract_from_text(text, source_file=filename)


def _extract_from_text(text: str, source_file: str) -> Transaction | None:
    """
    Extract amount, date, and vendor from invoice text using regex patterns.
    Returns a Transaction typed as 'payable'.
    """
    amount = _extract_amount(text)
    due_date = _extract_due_date(text)
    vendor = _extract_vendor(text)

    if not amount or not due_date:
        print(f"[InvoiceParser] Could not extract required fields from {source_file}")
        return None

    return Transaction(
        id=f"inv_{uuid.uuid4().hex[:8]}",
        amount=amount,
        type="payable",
        due_date=due_date,
        counterparty=vendor or source_file,
        source="invoice",
        description=f"Invoice from {vendor or source_file}",
        confidence=0.95,  # Digital PDF — high confidence
    )


def _extract_amount(text: str) -> float | None:
    """Extract the invoice total amount using multiple patterns."""
    patterns = [
        r"(?:total|grand total|amount due|invoice total|net amount)\s*[:\-]?\s*₹?\s*([\d,]+\.?\d*)",
        r"₹\s*([\d,]+\.?\d*)",
        r"INR\s*([\d,]+\.?\d*)",
        r"Rs\.?\s*([\d,]+\.?\d*)",
        r"(?:amount|total)\s*[:\-]\s*([\d,]+\.?\d*)",
    ]

    best_amount = None
    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for match in matches:
            val = float(match.replace(",", ""))
            # Take the largest match (usually the grand total)
            if val > 0 and (best_amount is None or val > best_amount):
                best_amount = val

    return best_amount


def _extract_due_date(text: str) -> date | None:
    """Extract due date or invoice date from invoice text."""
    patterns = [
        r"(?:due date|payment due|pay by|due on)\s*[:\-]?\s*(\d{1,2}[-/\s]\w{2,9}[-/\s]\d{2,4})",
        r"(?:invoice date|bill date|date)\s*[:\-]?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})",
        r"(\d{1,2}[-/]\d{1,2}[-/]\d{4})",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            parsed = _parse_date(match.group(1))
            if parsed:
                # If it's an invoice date (not due date), add 30 days as default
                if "due" not in pattern:
                    return parsed + timedelta(days=30)
                return parsed

    return None


def _extract_vendor(text: str) -> str | None:
    """Try to extract vendor/company name from invoice."""
    patterns = [
        r"(?:from|vendor|supplier|billed by|company)\s*[:\-]?\s*([A-Z][A-Za-z\s&.,]+(?:Pvt|Ltd|Inc|Corp|Co)?\.?)",
        r"^([A-Z][A-Za-z\s&.,]{5,50})\n",  # Company name often at top
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            name = match.group(1).strip()
            if len(name) > 3:
                return name[:60]

    return None


def _parse_date(raw: str) -> date | None:
    """Try multiple date formats."""
    raw = raw.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None
