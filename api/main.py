"""
api/main.py
-----------
FastAPI application entry point.
All routes are registered here. Run with: uvicorn api.main:app --reload
"""

from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
import tempfile
import os
import json
from datetime import date

# Internal imports
from data.db import init_db
from data.transaction_store import (
    save_transactions, get_transactions, get_latest_balance,
    clear_all_transactions
)
from data.vendor_store import (
    upsert_vendor, get_all_vendors, seed_demo_vendors
)
from data.models import Transaction, VendorProfile
from core.engine import run_analysis, state_to_dict
from llm.client import generate_cot_explanation, generate_email, generate_payment_plan
from ingestion.bank_statement_parser import parse_bank_statement, parse_bank_statement_from_text
from ingestion.invoice_parser import parse_invoice
from ingestion.receipt_ocr import parse_receipt_image

# ── App Setup ─────────────────────────────────────────────────────────────────
app = FastAPI(
    title="CashFlow Engine API",
    description="Semi-autonomous financial decision engine for small businesses",
    version="1.0.0",
)

# Allow cross-origin requests (for Streamlit frontend on different port)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    """Initialize database tables on startup."""
    init_db()
    print("[API] CashFlow Engine API started.")


# ── Health Check ──────────────────────────────────────────────────────────────
@app.get("/")
def root():
    return {"status": "ok", "message": "CashFlow Engine API is running."}


@app.get("/health")
def health():
    return {"status": "healthy"}


# ── File Upload & Ingestion ───────────────────────────────────────────────────

@app.post("/upload/bank-statement")
async def upload_bank_statement(file: UploadFile = File(...)):
    """
    Upload a bank statement (CSV or PDF).
    Parses it, normalizes, saves to DB.
    Returns count of transactions saved.
    """
    allowed_types = ["text/csv", "application/pdf",
                     "application/octet-stream", "text/plain"]

    content = await file.read()
    suffix = os.path.splitext(file.filename or "file.csv")[1].lower()

    # Write to temp file for parsers that need file paths
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        transactions = parse_bank_statement(tmp_path)
        save_transactions(transactions)
        return {
            "status": "success",
            "filename": file.filename,
            "transactions_saved": len(transactions),
            "message": f"Parsed and saved {len(transactions)} transactions."
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Parse error: {str(e)}")
    finally:
        os.unlink(tmp_path)  # Always clean up temp file


@app.post("/upload/invoice")
async def upload_invoice(file: UploadFile = File(...)):
    """Upload a digital invoice PDF. Returns the parsed payable."""
    content = await file.read()

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        txn = parse_invoice(tmp_path)
        if not txn:
            raise HTTPException(status_code=422, detail="Could not extract invoice data.")

        save_transactions([txn])
        return {
            "status": "success",
            "transaction": {
                "id": txn.id,
                "amount": txn.amount,
                "counterparty": txn.counterparty,
                "due_date": txn.due_date.isoformat(),
                "confidence": txn.confidence,
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        os.unlink(tmp_path)


@app.post("/upload/receipt")
async def upload_receipt(file: UploadFile = File(...)):
    """Upload a receipt image (JPG/PNG). OCR extracts amount and vendor."""
    content = await file.read()
    suffix = os.path.splitext(file.filename or "receipt.jpg")[1].lower()

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        txn = parse_receipt_image(tmp_path)
        if not txn:
            raise HTTPException(status_code=422,
                detail="Could not extract receipt data. Please check image quality.")

        save_transactions([txn])

        needs_review = txn.confidence < 0.65
        return {
            "status": "success",
            "needs_manual_review": needs_review,
            "confidence": txn.confidence,
            "transaction": {
                "id": txn.id,
                "amount": txn.amount,
                "counterparty": txn.counterparty,
                "due_date": txn.due_date.isoformat(),
                "confidence": txn.confidence,
            },
            "message": (
                "Low confidence — please verify the extracted data."
                if needs_review else "Receipt processed successfully."
            )
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        os.unlink(tmp_path)


# ── Cash Balance ──────────────────────────────────────────────────────────────

class BalanceInput(BaseModel):
    amount: float
    date: Optional[str] = None  # ISO date string, defaults to today


@app.post("/balance")
def set_balance(data: BalanceInput):
    """Set the current cash balance (creates a balance_snapshot transaction)."""
    bal_date = date.fromisoformat(data.date) if data.date else date.today()

    import uuid
    txn = Transaction(
        id=f"bal_{uuid.uuid4().hex[:8]}",
        amount=data.amount,
        type="balance_snapshot",
        due_date=bal_date,
        counterparty="balance_snapshot",
        source="manual_entry",
        confidence=1.0,
    )
    save_transactions([txn])
    return {"status": "success", "balance": data.amount, "date": bal_date.isoformat()}


@app.get("/balance")
def get_balance():
    """Get the latest recorded cash balance."""
    balance = get_latest_balance()
    return {"current_cash": balance}


# ── Analysis / Dashboard ──────────────────────────────────────────────────────

@app.get("/dashboard")
def get_dashboard():
    """
    Run the full analysis pipeline and return the financial state.
    This is the main endpoint the dashboard calls.
    """
    transactions = get_transactions()

    if not transactions:
        return {
            "status": "no_data",
            "message": "No transactions found. Please upload a bank statement first."
        }

    state = run_analysis(transactions)
    return {"status": "success", "data": state_to_dict(state)}


@app.post("/analyze")
def analyze_with_cash(data: BalanceInput):
    """
    Run analysis with a specific cash balance override.
    Useful for what-if scenarios.
    """
    transactions = get_transactions()

    if not transactions:
        raise HTTPException(status_code=400,
            detail="No transactions found. Upload data first.")

    state = run_analysis(transactions, current_cash=data.amount)
    return {"status": "success", "data": state_to_dict(state)}


# ── LLM Output Generation ──────────────────────────────────────────────────────

@app.get("/generate/explanation")
def generate_explanation():
    """Generate a COT explanation of the current financial decisions."""
    transactions = get_transactions()
    if not transactions:
        raise HTTPException(status_code=400, detail="No data to explain.")

    state = run_analysis(transactions)
    state_dict = state_to_dict(state)
    explanation = generate_cot_explanation(state_dict)

    return {"status": "success", "explanation": explanation}


class EmailRequest(BaseModel):
    obligation_id: str
    business_name: Optional[str] = "our business"


@app.post("/generate/email")
def generate_email_for_obligation(req: EmailRequest):
    """Generate a tone-adapted email for a specific deferred obligation."""
    transactions = get_transactions()
    if not transactions:
        raise HTTPException(status_code=400, detail="No data found.")

    state = run_analysis(transactions)
    state_dict = state_to_dict(state)

    # Find the specific obligation
    obligation = next(
        (o for o in state_dict["obligations"] if o["id"] == req.obligation_id),
        None
    )
    if not obligation:
        raise HTTPException(status_code=404,
            detail=f"Obligation {req.obligation_id} not found.")

    if obligation["action"] not in ("DEFER", "PAY_PARTIAL"):
        return {
            "status": "not_needed",
            "message": "This obligation is being paid in full — no email needed."
        }

    email = generate_email(obligation, req.business_name)
    return {
        "status": "success",
        "obligation_id": req.obligation_id,
        "vendor": obligation["counterparty"],
        "email": email
    }


@app.get("/generate/payment-plan")
def get_payment_plan():
    """Generate a human-readable payment plan summary."""
    transactions = get_transactions()
    if not transactions:
        raise HTTPException(status_code=400, detail="No data found.")

    state = run_analysis(transactions)
    state_dict = state_to_dict(state)
    plan = generate_payment_plan(state_dict)

    return {"status": "success", "plan": plan}


# ── Vendor Management ──────────────────────────────────────────────────────────

class VendorInput(BaseModel):
    name: str
    relationship_type: str  # long_term | new | critical | occasional
    months_active: float = 0.0
    payment_history: str = "unknown"
    allows_partial: bool = False
    has_grace_period: bool = False
    grace_days: int = 0
    notes: str = ""


@app.get("/vendors")
def list_vendors():
    """List all vendor profiles."""
    vendors = get_all_vendors()
    return {
        "count": len(vendors),
        "vendors": [
            {
                "name": v.name,
                "relationship_type": v.relationship_type,
                "months_active": v.months_active,
                "payment_history": v.payment_history,
                "allows_partial": v.allows_partial,
                "has_grace_period": v.has_grace_period,
                "grace_days": v.grace_days,
            }
            for v in vendors
        ]
    }


@app.post("/vendors")
def create_or_update_vendor(vendor: VendorInput):
    """Create or update a vendor profile."""
    profile = VendorProfile(
        name=vendor.name,
        relationship_type=vendor.relationship_type,
        months_active=vendor.months_active,
        payment_history=vendor.payment_history,
        allows_partial=vendor.allows_partial,
        has_grace_period=vendor.has_grace_period,
        grace_days=vendor.grace_days,
        notes=vendor.notes,
    )
    upsert_vendor(profile)
    return {"status": "success", "message": f"Vendor '{vendor.name}' saved."}


# ── Demo / Reset ──────────────────────────────────────────────────────────────

@app.post("/demo/seed")
def seed_demo_data():
    """
    Seed the database with demo data for presentations.
    WARNING: Clears existing transactions first.
    """
    from demo.seed_data import seed_all
    seed_all()
    return {"status": "success", "message": "Demo data seeded. Ready for presentation."}


@app.post("/reset")
def reset_transactions():
    """Clear all transactions (vendors are preserved)."""
    clear_all_transactions()
    return {"status": "success", "message": "All transactions cleared."}
