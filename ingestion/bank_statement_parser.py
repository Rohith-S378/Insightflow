"""
ingestion/bank_statement_parser.py
------------------------------------
Parses bank statement files (CSV or PDF) into normalized Transaction objects.

Handles common Indian bank export formats:
- SBI, HDFC, ICICI CSV exports
- PDF statements via pdfplumber text extraction

Returns a list of Transaction objects ready for deduplication.
"""

import re
import uuid
import csv
import io
from datetime import date, datetime
from pathlib import Path
from data.models import Transaction


# Common date formats seen in Indian bank exports
DATE_FORMATS = [
    "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d",
    "%d %b %Y", "%d-%b-%Y", "%d/%b/%Y",
    "%d/%m/%y", "%d-%m-%y",
]


def parse_bank_statement(file_path: str) -> list[Transaction]:
    """
    Main entry point. Auto-detects CSV or PDF and routes accordingly.
    Returns list of Transaction objects (both payables and receivables).
    """
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"Bank statement file not found: {file_path}")

    ext = path.suffix.lower()

    if ext == ".csv":
        return _parse_csv(file_path)
    elif ext == ".pdf":
        return _parse_pdf(file_path)
    else:
        raise ValueError(f"Unsupported bank statement format: {ext}. Use .csv or .pdf")


def _parse_csv(file_path: str) -> list[Transaction]:
    """
    Parse a CSV bank statement.
    Tries to auto-detect column layout by inspecting headers.
    """
    transactions = []

    with open(file_path, "r", encoding="utf-8-sig") as f:
        # Read first line to detect format
        sample = f.read(2000)
        f.seek(0)

        dialect = csv.Sniffer().sniff(sample, delimiters=",\t|")
        reader = csv.DictReader(f, dialect=dialect)
        headers = [h.strip().lower() for h in (reader.fieldnames or [])]

        # Map header variants to standard names
        col_map = _detect_columns(headers)

        for i, row in enumerate(reader):
            # Normalize keys
            row = {k.strip().lower(): v.strip() for k, v in row.items() if k}

            try:
                txn = _row_to_transaction(row, col_map, source="bank_statement")
                if txn:
                    transactions.append(txn)
            except Exception as e:
                print(f"[BankParser] Skipping row {i+2}: {e}")
                continue

    print(f"[BankParser] Parsed {len(transactions)} transactions from {file_path}")
    return transactions


def _parse_pdf(file_path: str) -> list[Transaction]:
    """
    Parse a PDF bank statement using pdfplumber.
    Extracts text and applies regex patterns to find transactions.
    """
    try:
        import pdfplumber
    except ImportError:
        raise ImportError("Install pdfplumber: pip install pdfplumber")

    transactions = []
    # Pattern: date, description, debit/credit amount
    # Matches common Indian bank PDF table formats
    pattern = re.compile(
        r"(\d{1,2}[-/]\w{2,3}[-/]\d{2,4})"   # date
        r"\s+(.+?)\s+"                           # description
        r"(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)"    # amount
        r"\s*(Dr|Cr|DR|CR)?",                   # debit/credit indicator
        re.IGNORECASE
    )

    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            for match in pattern.finditer(text):
                try:
                    raw_date, description, amount_str, dr_cr = match.groups()
                    amount = float(amount_str.replace(",", ""))
                    txn_date = _parse_date(raw_date)

                    if not txn_date:
                        continue

                    # Determine type from Dr/Cr indicator
                    is_debit = (dr_cr or "").upper() in ("DR", "")
                    txn_type = "payable" if is_debit else "receivable"

                    txn = Transaction(
                        id=f"bank_{uuid.uuid4().hex[:8]}",
                        amount=amount,
                        type=txn_type,
                        due_date=txn_date,
                        counterparty=_clean_description(description),
                        source="bank_statement",
                        description=description,
                        confidence=1.0,  # Digital source
                    )
                    transactions.append(txn)
                except Exception as e:
                    print(f"[BankParser PDF] Skipping match: {e}")
                    continue

    print(f"[BankParser] Parsed {len(transactions)} transactions from PDF {file_path}")
    return transactions


def parse_bank_statement_from_text(text_content: str) -> list[Transaction]:
    """
    Parse bank statement from a text string (for API uploads).
    Treats it as CSV content.
    """
    transactions = []
    reader = csv.DictReader(io.StringIO(text_content))
    headers = [h.strip().lower() for h in (reader.fieldnames or [])]
    col_map = _detect_columns(headers)

    for i, row in enumerate(reader):
        row = {k.strip().lower(): v.strip() for k, v in row.items() if k}
        try:
            txn = _row_to_transaction(row, col_map, source="bank_statement")
            if txn:
                transactions.append(txn)
        except Exception as e:
            print(f"[BankParser] Skipping row {i+2}: {e}")
    return transactions


def _detect_columns(headers: list[str]) -> dict:
    """
    Auto-detect which CSV columns contain date, description, debit, credit.
    Returns a mapping like {"date": "txn date", "debit": "withdrawal amt", ...}
    """
    mapping = {}

    date_keywords = ["date", "txn date", "transaction date", "value date", "posting date"]
    desc_keywords = ["description", "narration", "particulars", "details", "remarks", "txn description"]
    debit_keywords = ["debit", "withdrawal", "dr", "debit amount", "withdrawal amt", "amount (dr)"]
    credit_keywords = ["credit", "deposit", "cr", "credit amount", "deposit amt", "amount (cr)"]
    balance_keywords = ["balance", "closing balance", "available balance"]
    amount_keywords = ["amount", "txn amount"]

    for col in headers:
        col_clean = col.strip().lower()
        if col_clean in date_keywords and "date" not in mapping:
            mapping["date"] = col
        elif col_clean in desc_keywords and "description" not in mapping:
            mapping["description"] = col
        elif col_clean in debit_keywords and "debit" not in mapping:
            mapping["debit"] = col
        elif col_clean in credit_keywords and "credit" not in mapping:
            mapping["credit"] = col
        elif col_clean in balance_keywords and "balance" not in mapping:
            mapping["balance"] = col
        elif col_clean in amount_keywords and "amount" not in mapping:
            mapping["amount"] = col

    return mapping


def _row_to_transaction(row: dict, col_map: dict, source: str) -> Transaction | None:
    """Convert a single CSV row to a Transaction using the detected column mapping."""

    # Get date
    date_col = col_map.get("date", "date")
    raw_date = row.get(date_col, "")
    txn_date = _parse_date(raw_date)
    if not txn_date:
        return None

    # Get description
    desc_col = col_map.get("description", "description")
    description = row.get(desc_col, "unknown")

    # Handle debit/credit columns separately or combined amount column
    debit_col = col_map.get("debit", "")
    credit_col = col_map.get("credit", "")
    amount_col = col_map.get("amount", "amount")

    debit_val = _parse_amount(row.get(debit_col, "0") if debit_col else "0")
    credit_val = _parse_amount(row.get(credit_col, "0") if credit_col else "0")
    amount_val = _parse_amount(row.get(amount_col, "0"))

    if debit_val > 0:
        return Transaction(
            id=f"bank_{uuid.uuid4().hex[:8]}",
            amount=debit_val,
            type="payable",
            due_date=txn_date,
            counterparty=_clean_description(description),
            source=source,
            description=description,
            confidence=1.0,
        )
    elif credit_val > 0:
        return Transaction(
            id=f"bank_{uuid.uuid4().hex[:8]}",
            amount=credit_val,
            type="receivable",
            due_date=txn_date,
            counterparty=_clean_description(description),
            source=source,
            description=description,
            confidence=1.0,
        )
    elif amount_val > 0:
        # Guess type from description keywords
        desc_lower = description.lower()
        is_credit = any(w in desc_lower for w in ["received", "credit", "inward", "payment from", "refund"])
        return Transaction(
            id=f"bank_{uuid.uuid4().hex[:8]}",
            amount=amount_val,
            type="receivable" if is_credit else "payable",
            due_date=txn_date,
            counterparty=_clean_description(description),
            source=source,
            description=description,
            confidence=0.85,  # Slightly lower confidence since type was guessed
        )

    return None


def _parse_date(raw: str) -> date | None:
    """Try multiple date format patterns and return a date or None."""
    raw = raw.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _parse_amount(raw: str) -> float:
    """Parse an amount string, removing commas and currency symbols."""
    if not raw:
        return 0.0
    cleaned = re.sub(r"[₹,$,\s,]", "", str(raw)).replace(",", "")
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _clean_description(desc: str) -> str:
    """Extract a clean vendor/counterparty name from a raw bank description."""
    # Remove common transaction prefixes
    prefixes = [
        r"^UPI[-/]", r"^NEFT[-/]", r"^IMPS[-/]", r"^RTGS[-/]",
        r"^ATM[-/]", r"^POS[-/]", r"^CHQ[-/]", r"^ECS[-/]",
        r"\d{10,}", r"\b[A-Z0-9]{15,}\b",  # Remove long reference numbers
    ]
    cleaned = desc
    for pattern in prefixes:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)

    # Take first meaningful part (before slash or pipe)
    for sep in ["/", "|", "-"]:
        if sep in cleaned:
            cleaned = cleaned.split(sep)[0]

    return cleaned.strip()[:60] or desc[:60]
