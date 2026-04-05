"""
ingestion/receipt_ocr.py
-------------------------
OCR processing for physical/handwritten receipt images.
Primary: Google Cloud Vision API (better accuracy)
Fallback: Tesseract OCR (offline, free)

Returns a Transaction with confidence score reflecting OCR quality.
Low-confidence results are flagged for manual review.
"""

import re
import uuid
import base64
from datetime import date, datetime, timedelta
from pathlib import Path
from data.models import Transaction
from config.settings import GOOGLE_VISION_API_KEY, OCR_MIN_CONFIDENCE


def parse_receipt_image(file_path: str) -> Transaction | None:
    """
    Main entry point for receipt image OCR.
    Tries Google Vision first, falls back to Tesseract.
    Returns a Transaction or None if extraction fails.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Receipt image not found: {file_path}")

    # Try Google Vision if API key is available
    if GOOGLE_VISION_API_KEY:
        try:
            text, confidence = _ocr_google_vision(file_path)
            print(f"[OCR] Google Vision confidence: {confidence:.2f}")
        except Exception as e:
            print(f"[OCR] Google Vision failed ({e}), falling back to Tesseract")
            text, confidence = _ocr_tesseract(file_path)
    else:
        print("[OCR] No Google Vision key, using Tesseract")
        text, confidence = _ocr_tesseract(file_path)

    if not text:
        print(f"[OCR] No text extracted from {file_path}")
        return None

    if confidence < OCR_MIN_CONFIDENCE:
        print(f"[OCR] Low confidence ({confidence:.2f}) — flagging for manual review")

    return _extract_from_ocr_text(text, confidence, source_file=path.name)


def _ocr_google_vision(file_path: str) -> tuple[str, float]:
    """
    Use Google Cloud Vision API for OCR.
    Returns (text, confidence) tuple.
    """
    try:
        from google.cloud import vision
    except ImportError:
        raise ImportError("Install: pip install google-cloud-vision")

    client = vision.ImageAnnotatorClient()

    with open(file_path, "rb") as f:
        content = f.read()

    image = vision.Image(content=content)
    response = client.document_text_detection(image=image)

    if response.error.message:
        raise RuntimeError(f"Vision API error: {response.error.message}")

    full_text = response.full_text_annotation.text

    # Compute average confidence from word-level annotations
    confidences = []
    for page in response.full_text_annotation.pages:
        for block in page.blocks:
            for paragraph in block.paragraphs:
                for word in paragraph.words:
                    confidences.append(word.confidence)

    avg_confidence = sum(confidences) / len(confidences) if confidences else 0.5

    return full_text, avg_confidence


def _ocr_tesseract(file_path: str) -> tuple[str, float]:
    """
    Use Tesseract OCR as fallback.
    Returns (text, confidence) tuple.
    """
    try:
        import pytesseract
        from PIL import Image, ImageFilter, ImageEnhance
    except ImportError:
        raise ImportError("Install: pip install pytesseract pillow")

    img = Image.open(file_path)

    # Preprocessing to improve OCR accuracy:
    # 1. Convert to grayscale
    img = img.convert("L")
    # 2. Enhance contrast
    img = ImageEnhance.Contrast(img).enhance(2.0)
    # 3. Sharpen
    img = img.filter(ImageFilter.SHARPEN)

    # Run OCR with confidence data
    data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
    text = pytesseract.image_to_string(img)

    # Compute average confidence (Tesseract returns -1 for non-word regions)
    valid_confs = [int(c) for c in data["conf"] if int(c) > 0]
    avg_confidence = (sum(valid_confs) / len(valid_confs) / 100.0) if valid_confs else 0.4

    return text, avg_confidence


def _extract_from_ocr_text(text: str, confidence: float, source_file: str) -> Transaction | None:
    """
    Extract amount, date, and vendor from OCR'd receipt text.
    Uses regex patterns designed to handle noisy OCR output.
    """
    amount = _extract_amount_ocr(text)
    txn_date = _extract_date_ocr(text)
    vendor = _extract_vendor_ocr(text, source_file)

    if not amount:
        print(f"[OCR] Could not extract amount from {source_file}")
        return None

    # If no date found, default to today
    if not txn_date:
        txn_date = date.today()
        confidence *= 0.9  # Reduce confidence since date was not found

    return Transaction(
        id=f"rcpt_{uuid.uuid4().hex[:8]}",
        amount=amount,
        type="payable",           # Receipts are almost always expenses
        due_date=txn_date,
        counterparty=vendor,
        source="receipt",
        description=f"Receipt: {source_file}",
        confidence=round(confidence, 2),
    )


def _extract_amount_ocr(text: str) -> float | None:
    """
    Extract amount from OCR'd text.
    Handles common OCR errors like '0' vs 'O', '1' vs 'I'.
    """
    # Fix common OCR substitutions in numbers
    fixed = text.replace("O", "0").replace("l", "1").replace("I", "1")

    patterns = [
        r"(?:total|amount|grand total|net|subtotal|to pay)\s*[:\-]?\s*[₹Rs\.]?\s*([\d,]+\.?\d*)",
        r"[₹]\s*([\d,]+\.?\d*)",
        r"(?:Rs|INR)\.?\s*([\d,]+\.?\d*)",
        r"\b(\d{3,6}\.?\d{0,2})\b",  # Any number 100-999999
    ]

    candidates = []
    for pattern in patterns:
        matches = re.findall(pattern, fixed, re.IGNORECASE)
        for match in matches:
            try:
                val = float(str(match).replace(",", ""))
                if 10 <= val <= 9_999_999:  # Reasonable receipt range
                    candidates.append(val)
            except ValueError:
                continue

    if not candidates:
        return None

    # Return the largest value (most likely to be the total)
    return max(candidates)


def _extract_date_ocr(text: str) -> date | None:
    """Extract date from receipt, handling OCR noise."""
    patterns = [
        r"(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4})",
        r"(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{2,4})",
        r"((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},?\s+\d{4})",
    ]

    formats = [
        "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y",
        "%d/%m/%y", "%d-%m-%y",
        "%d %b %Y", "%b %d, %Y", "%b %d %Y",
    ]

    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for match in matches:
            for fmt in formats:
                try:
                    return datetime.strptime(match.strip(), fmt).date()
                except ValueError:
                    continue

    return None


def _extract_vendor_ocr(text: str, fallback: str) -> str:
    """Extract vendor/shop name from receipt — usually near the top."""
    lines = [l.strip() for l in text.split("\n") if l.strip()]

    # First non-empty line is often the shop/vendor name
    for line in lines[:4]:
        if len(line) > 3 and not re.match(r"^\d", line):
            return line[:60]

    return fallback[:60]
