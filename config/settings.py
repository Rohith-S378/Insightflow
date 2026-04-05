import os
from dotenv import load_dotenv

load_dotenv()

# ── API Keys ──────────────────────────────────────────────────────────────────
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GOOGLE_VISION_API_KEY = os.getenv("GOOGLE_VISION_API_KEY", "")

# LLM model — gemini-1.5-flash is fast and free-tier friendly
# Other options: "gemini-1.5-pro" (smarter), "gemini-2.0-flash" (latest)
LLM_MODEL = "gemini-1.5-flash"
LLM_MAX_TOKENS = 1000

# ── Obligation Scoring Weights ────────────────────────────────────────────────
URGENCY_WEIGHT = 0.5
PENALTY_WEIGHT = 0.35
FLEXIBILITY_WEIGHT = 0.15

# ── Runway Severity Thresholds (days) ────────────────────────────────────────
SEVERITY_CRITICAL = 7
SEVERITY_URGENT = 14
SEVERITY_WARNING = 30
SEVERITY_MONITOR = 60

# ── Deduplication Thresholds ──────────────────────────────────────────────────
DEDUP_AMOUNT_TOLERANCE = 0.02
DEDUP_DATE_TOLERANCE_DAYS = 2
DEDUP_NAME_SIMILARITY = 0.82

# ── OCR Confidence ────────────────────────────────────────────────────────────
OCR_MIN_CONFIDENCE = 0.65

# ── Database ──────────────────────────────────────────────────────────────────
DB_PATH = "cashflow.db"
