# InsightFlow Engine
### Semi-autonomous financial decision system for small businesses

Transforms fragmented financial data into clear, actionable cash flow decisions.
Built for the Fintech Track — National Project Presentation Contest.

---

## What It Does

1. **Ingests** bank statements (CSV/PDF), invoices (PDF), and receipt images (OCR)
2. **Models** your financial state: cash balance, payables, receivables
3. **Computes** days-to-zero runway with week-by-week simulation
4. **Scores** and **prioritizes** every obligation using a deterministic formula
5. **Generates** tone-adapted deferral emails and payment plan summaries via LLM

---

## Architecture

```
Layer 1 — Deterministic Core (pure Python, no LLM)
  ingestion/      ← Parse all data sources
  core/           ← Normalize, dedup, score, resolve, simulate

Layer 2 — LLM Output Layer (Anthropic Claude)
  llm/            ← Generate explanations, emails, summaries
                     (grounded in Layer 1 JSON — cannot change decisions)

Frontend
  frontend/app.py ← Streamlit dashboard (4 pages)
  api/main.py     ← FastAPI REST backend
```

---

## Step-by-Step Setup

### Prerequisites
- Python 3.10 or higher
- pip
- (Optional) Tesseract OCR for receipt scanning

---

### Step 1 — Clone / Download the project

```bash
cd ~/Desktop
# If using git:
git clone <your-repo-url> cashflow-engine
cd cashflow-engine

# Or just place the cashflow-engine/ folder on your Desktop and:
cd cashflow-engine
```

---

### Step 2 — Create a virtual environment (recommended)

```bash
python3 -m venv venv

# Activate it:
# On Mac/Linux:
source venv/bin/activate

# On Windows:
venv\Scripts\activate
```

You should see `(venv)` in your terminal prompt.

---

### Step 3 — Install dependencies

```bash
pip install -r requirements.txt
```

This installs: FastAPI, Streamlit, Anthropic SDK, pdfplumber, pandas, rapidfuzz, etc.

If you get errors on individual packages, install the critical ones manually:
```bash
pip install streamlit anthropic pandas rapidfuzz python-dotenv pdfplumber pillow
```

---

### Step 4 — Install Tesseract OCR (for receipt image scanning)

Tesseract is needed to OCR handwritten/physical receipts.

**Ubuntu/Debian:**
```bash
sudo apt-get install tesseract-ocr
```

**Mac (Homebrew):**
```bash
brew install tesseract
```

**Windows:**
Download from: https://github.com/UB-Mannheim/tesseract/wiki

---

### Step 5 — Set up API keys

```bash
# Copy the template
cp .env.example .env

# Edit .env and add your keys:
nano .env   # or open in any text editor
```

Your `.env` file should look like:
```
ANTHROPIC_API_KEY=sk-ant-api03-xxxxx...
```

> **Note:** The app works without API keys — the dashboard, scoring, and
> all deterministic logic runs fine. Only email/explanation generation
> requires the Anthropic key.

Get your Anthropic API key at: https://console.anthropic.com

---

### Step 6 — Initialize the database

```bash
python3 -c "from data.db import init_db; init_db()"
```

You should see:
```
[DB] Database initialized at: cashflow.db
```

---

### Step 7 — Seed demo data (for presentation)

```bash
python3 -m demo.seed_data
```

This loads a realistic scenario:
- **Cash:** ₹40,000
- **Obligations:** ₹1,15,200 (GST overdue, rent, vendor invoices, loan EMI)
- **Expected receivables:** ₹57,000
- **Result:** System prioritizes GST first, defers low-priority vendors

---

### Step 8 — Run the application

#### Option A: Streamlit Dashboard (recommended for demo)

```bash
streamlit run frontend/app.py
```

Open your browser at: **http://localhost:8501**

#### Option B: FastAPI Backend only

```bash
uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
```

Open your browser at: **http://localhost:8000/docs** for the interactive API docs.

#### Option C: Run both simultaneously

```bash
# Terminal 1 — API backend
uvicorn api.main:app --reload --port 8000

# Terminal 2 — Streamlit frontend
streamlit run frontend/app.py
```

---

### Step 9 — Run tests

```bash
pytest tests/test_core.py -v
```

All 15 tests should pass. These verify:
- Deduplication logic
- Runway calculation accuracy
- Obligation scoring formula
- Priority resolver (greedy allocation)
- Full pipeline determinism

---

## Demo Flow (for presentation)

1. Open Streamlit at http://localhost:8501
2. Click **"Load Demo Data"** in the sidebar
3. Go to **Dashboard** — see the days-to-zero, severity, and obligation decisions
4. Go to **Actions & Emails** → click "Generate Explanation"
5. Click "Generate Email" for any deferred vendor
6. Show the **Manage Vendors** page to explain tone adaptation
7. Go to **Upload Data** → upload `sample_data/bank_statement.csv`

**Winning demo moment:** Show GST being paid first (score 72.5) while Ravi Supplies is deferred (score 28.6) — then explain the exact formula that made that decision.

---

## Key Files Reference

| File | Purpose |
|------|---------|
| `core/engine.py` | Main orchestrator — call `run_analysis()` |
| `core/obligation_scorer.py` | The scoring formula |
| `core/runway_calculator.py` | Days-to-zero simulation |
| `core/priority_resolver.py` | Greedy cash allocator |
| `llm/client.py` | All LLM calls |
| `config/penalty_table.py` | Domain rules (GST > rent > vendor) |
| `config/tone_instructions.py` | Email tone by vendor type |
| `frontend/app.py` | Complete Streamlit UI |
| `demo/seed_data.py` | Demo scenario |
| `tests/test_core.py` | All unit tests |

---

## API Endpoints (Quick Reference)

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/dashboard` | Run analysis, get full state |
| POST | `/upload/bank-statement` | Upload bank CSV or PDF |
| POST | `/upload/invoice` | Upload invoice PDF |
| POST | `/upload/receipt` | Upload receipt image |
| POST | `/balance` | Set current cash balance |
| GET | `/generate/explanation` | Generate COT reasoning |
| POST | `/generate/email` | Generate vendor email |
| GET | `/generate/payment-plan` | Generate payment plan |
| POST | `/vendors` | Add/update vendor profile |
| POST | `/demo/seed` | Load demo data |

---

## Troubleshooting

**"No module named 'rapidfuzz'"**
```bash
pip install rapidfuzz
```

**"tesseract is not installed"**
Receipt OCR will fail. Install tesseract (Step 4) or use manual entry instead.

**"ANTHROPIC_API_KEY not set"**
LLM outputs will show a placeholder. Add the key to `.env` file.

**"No such table: vendors"**
Run Step 6 (init_db) again.

**Streamlit shows "No transactions found"**
Run Step 7 (seed demo data) or upload a file in the Upload page.

---

## For the Judges

**Decision integrity:** Every obligation decision traces back to a deterministic formula in `obligation_scorer.py`. No LLM involvement in scoring or allocation.

**Separation of concerns:** `core/` never imports from `llm/`. The LLM receives a JSON object and generates language only.

**Verifiable outputs:** `llm/client.py` → `_validate_output()` checks the LLM's response against input amounts. Any hallucinated number triggers a warning.

**Data robustness:** Deduplication handles the same payment appearing in both bank statement and invoice. OCR confidence scoring flags uncertain extractions for manual review.
