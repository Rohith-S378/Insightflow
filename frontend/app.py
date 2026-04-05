"""
frontend/app.py
---------------
Streamlit dashboard for the CashFlow Engine.

Run with: streamlit run frontend/app.py

Pages:
  1. Dashboard   — runway indicator, cash position, obligation summary
  2. Upload      — import bank statements, invoices, receipts
  3. Actions     — view emails, payment plan, COT reasoning
  4. Vendors     — manage vendor relationship profiles
"""

import sys
import os

# Add project root to path so imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import pandas as pd
from datetime import date, datetime
import json

from data.db import init_db
from data.transaction_store import get_transactions, get_latest_balance, clear_all_transactions
from data.vendor_store import get_all_vendors, upsert_vendor, seed_demo_vendors
from data.models import Transaction, VendorProfile
from core.engine import run_analysis, state_to_dict
from llm.client import generate_cot_explanation, generate_email, generate_payment_plan
from ingestion.bank_statement_parser import parse_bank_statement
from ingestion.invoice_parser import parse_invoice
from ingestion.receipt_ocr import parse_receipt_image
from demo.seed_data import seed_all

import uuid
import tempfile

# ── Page Config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="InsightFlow Engine",
    page_icon="₹",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Initialize DB ─────────────────────────────────────────────────────────────
init_db()

# ── Sidebar Navigation ────────────────────────────────────────────────────────
st.sidebar.title("₹ InsightFlow Engine")
st.sidebar.markdown("*Financial clarity for small businesses*")
st.sidebar.divider()

page = st.sidebar.radio(
    "Navigate",
    ["Dashboard", "Upload Data", "Actions & Emails", "Manage Vendors"],
    index=0,
)

st.sidebar.divider()

# Demo reset button in sidebar
if st.sidebar.button("🔄 Load Demo Data", use_container_width=True):
    seed_all()
    st.sidebar.success("Demo data loaded!")
    st.rerun()

if st.sidebar.button("🗑 Clear All Data", use_container_width=True):
    clear_all_transactions()
    st.sidebar.warning("All transactions cleared.")
    st.rerun()

# ── Helper: Run Analysis ──────────────────────────────────────────────────────
@st.cache_data(ttl=30)  # Cache for 30 seconds to avoid re-running on every widget interaction
def get_analysis():
    """Run analysis and return state dict. Cached to avoid repeated computation."""
    txns = get_transactions()
    if not txns:
        return None
    state = run_analysis(txns)
    return state_to_dict(state)


# ─────────────────────────────────────────────────────────────────────────────
# PAGE 1: DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────
if page == "Dashboard":
    st.title("Financial Dashboard")
    st.caption(f"Analysis as of {date.today().strftime('%d %B %Y')}")

    state = get_analysis()

    if not state:
        st.info("No financial data found. Upload a bank statement or load demo data from the sidebar.")
        st.stop()

    # ── Top KPI Row ───────────────────────────────────────────────────────────
    col1, col2, col3, col4 = st.columns(4)

    severity = state["severity"]
    severity_emoji = {"CRITICAL": "🔴", "URGENT": "🔴",
                      "WARNING": "🟡", "MONITOR": "🟡", "STABLE": "🟢"}.get(severity, "⚪")

    with col1:
        st.metric("Current Cash", f"₹{state['current_cash']:,.0f}")

    with col2:
        dtz = state["days_to_zero"]
        dtz_display = f"{dtz} days" if dtz < 91 else "90+ days"
        st.metric("Days to Zero", dtz_display, delta=severity, delta_color="inverse")

    with col3:
        st.metric("Total Payables", f"₹{state['total_payables']:,.0f}")

    with col4:
        gap = state["cash_gap"]
        st.metric("Cash Gap", f"₹{gap:,.0f}",
                  delta="Shortfall" if gap > 0 else "Covered",
                  delta_color="inverse" if gap > 0 else "normal")

    # ── Severity Banner ───────────────────────────────────────────────────────
    severity_colors = {
        "CRITICAL": "error", "URGENT": "error",
        "WARNING": "warning", "MONITOR": "warning", "STABLE": "success"
    }
    banner_type = severity_colors.get(severity, "info")

    if severity in ("CRITICAL", "URGENT"):
        st.error(f"{severity_emoji} **{severity}**: Cash runway is {dtz_display}. Immediate action required.")
    elif severity == "WARNING":
        st.warning(f"{severity_emoji} **{severity}**: {dtz_display} of runway. Plan your payments this week.")
    else:
        st.success(f"{severity_emoji} **{severity}**: Cash position is healthy with {dtz_display} runway.")

    st.divider()

    # ── Cash Flow Projection Chart ────────────────────────────────────────────
    st.subheader("Cash Flow Projection")

    proj = state["weekly_projection"]
    if proj:
        df_proj = pd.DataFrame([
            {
                "Week": f"Wk {w['week']}",
                "Opening (₹)": w["opening"],
                "Outflow (₹)": -w["outflow"],  # Negative for chart
                "Inflow (₹)": w["inflow"],
                "Closing (₹)": w["closing"],
            }
            for w in proj
        ])

        # Color closing balance: red if negative
        closing_vals = [w["closing"] for w in proj]
        weeks = [f"Wk {w['week']}" for w in proj]

        chart_df = pd.DataFrame({
            "Week": weeks,
            "Closing Balance": closing_vals,
        }).set_index("Week")

        st.line_chart(chart_df, color=["#1D9E75"])

        # Projection table
        with st.expander("View week-by-week breakdown"):
            display_df = pd.DataFrame([
                {
                    "Week": f"Wk {w['week']}",
                    "Opening": f"₹{w['opening']:,.0f}",
                    "Out": f"₹{w['outflow']:,.0f}",
                    "In": f"₹{w['inflow']:,.0f}",
                    "Closing": f"₹{w['closing']:,.0f}",
                    "Status": w["status"],
                }
                for w in proj
            ])
            # Highlight shortfall rows
            def highlight_status(row):
                if row["Status"] == "SHORTFALL":
                    return ["background-color: #FCEBEB"] * len(row)
                elif row["Status"] == "LOW":
                    return ["background-color: #FAEEDA"] * len(row)
                return [""] * len(row)

            st.dataframe(display_df.style.apply(highlight_status, axis=1),
                         use_container_width=True, hide_index=True)

    st.divider()

    # ── Obligations Table ─────────────────────────────────────────────────────
    st.subheader("Obligation Decisions")

    obligations = state["obligations"]
    if obligations:
        action_emoji = {"PAY_FULL": "✅", "PAY_PARTIAL": "🔶", "DEFER": "⏸"}

        for ob in obligations:
            action = ob["action"]
            emoji = action_emoji.get(action, "❓")

            with st.container(border=True):
                c1, c2, c3, c4, c5 = st.columns([3, 2, 2, 2, 2])

                with c1:
                    st.markdown(f"**{ob['counterparty']}**")
                    vendor_type = (ob.get("vendor_profile") or {}).get("relationship_type", "unknown")
                    st.caption(f"Vendor type: {vendor_type}")

                with c2:
                    st.markdown(f"₹{ob['amount']:,.0f}")
                    st.caption(f"Due: {ob['due_date']}")

                with c3:
                    st.markdown(f"{emoji} **{action}**")
                    if ob["amount_to_pay"] > 0:
                        st.caption(f"Pay: ₹{ob['amount_to_pay']:,.0f}")

                with c4:
                    score = ob["final_score"]
                    st.metric("Score", f"{score:.0f}", label_visibility="collapsed")
                    st.caption(f"Score: {score:.0f}")

                with c5:
                    if action == "DEFER" and ob.get("deferred_to"):
                        st.caption(f"Defer to: {ob['deferred_to']}")

    # ── Scoring Explanation ───────────────────────────────────────────────────
    with st.expander("How are obligations scored?"):
        st.markdown("""
**Scoring formula:**
```
final_score = (urgency × 0.50) + (penalty × 0.35) − (flexibility × 0.15)
```
- **Urgency**: How soon it's due / how overdue it is
- **Penalty**: Cost of non-payment (GST penalty > vendor invoice)
- **Flexibility**: Whether the vendor can wait (grace period, relationship type)

Higher score = pay first.
        """)


# ─────────────────────────────────────────────────────────────────────────────
# PAGE 2: UPLOAD DATA
# ─────────────────────────────────────────────────────────────────────────────
elif page == "Upload Data":
    st.title("Upload Financial Data")
    st.caption("Import bank statements, invoices, and receipts.")

    tab1, tab2, tab3, tab4 = st.tabs([
        "Bank Statement", "Invoice", "Receipt Image", "Manual Entry"
    ])

    # ── Bank Statement ────────────────────────────────────────────────────────
    with tab1:
        st.subheader("Upload Bank Statement")
        st.info("Supported formats: CSV (any major Indian bank), PDF bank statements")

        uploaded = st.file_uploader("Choose file", type=["csv", "pdf"],
                                     key="bank_upload")

        if uploaded:
            suffix = ".csv" if uploaded.name.endswith(".csv") else ".pdf"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(uploaded.read())
                tmp_path = tmp.name

            with st.spinner("Parsing bank statement..."):
                try:
                    txns = parse_bank_statement(tmp_path)
                    from data.transaction_store import save_transactions
                    save_transactions(txns)
                    os.unlink(tmp_path)
                    st.success(f"✅ Saved {len(txns)} transactions from {uploaded.name}")
                    get_analysis.clear()  # Invalidate cache

                    # Show preview
                    if txns:
                        preview_df = pd.DataFrame([
                            {"Date": t.due_date, "Vendor": t.counterparty,
                             "Amount": f"₹{t.amount:,.0f}", "Type": t.type}
                            for t in txns[:10]
                        ])
                        st.dataframe(preview_df, use_container_width=True, hide_index=True)
                        if len(txns) > 10:
                            st.caption(f"... and {len(txns)-10} more")
                except Exception as e:
                    st.error(f"Parse error: {e}")

    # ── Invoice ───────────────────────────────────────────────────────────────
    with tab2:
        st.subheader("Upload Invoice PDF")
        st.info("Upload a vendor invoice. The system extracts amount, date, and vendor name.")

        uploaded_inv = st.file_uploader("Choose invoice PDF", type=["pdf"],
                                         key="inv_upload")

        if uploaded_inv:
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(uploaded_inv.read())
                tmp_path = tmp.name

            with st.spinner("Extracting invoice data..."):
                try:
                    txn = parse_invoice(tmp_path)
                    os.unlink(tmp_path)

                    if txn:
                        from data.transaction_store import save_transactions
                        save_transactions([txn])
                        get_analysis.clear()
                        st.success("✅ Invoice processed!")
                        st.json({
                            "vendor": txn.counterparty,
                            "amount": f"₹{txn.amount:,.0f}",
                            "due_date": str(txn.due_date),
                            "confidence": f"{txn.confidence:.0%}",
                        })
                    else:
                        st.error("Could not extract invoice data. Try manual entry.")
                except Exception as e:
                    st.error(f"Error: {e}")

    # ── Receipt Image ─────────────────────────────────────────────────────────
    with tab3:
        st.subheader("Upload Receipt Image")
        st.info("Upload a photo of a physical or handwritten receipt. OCR will extract the data.")

        uploaded_rcpt = st.file_uploader("Choose receipt image", type=["jpg", "jpeg", "png"],
                                          key="rcpt_upload")

        if uploaded_rcpt:
            st.image(uploaded_rcpt, caption="Uploaded receipt", width=300)

            suffix = "." + uploaded_rcpt.name.split(".")[-1]
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(uploaded_rcpt.read())
                tmp_path = tmp.name

            with st.spinner("Running OCR..."):
                try:
                    txn = parse_receipt_image(tmp_path)
                    os.unlink(tmp_path)

                    if txn:
                        confidence = txn.confidence
                        if confidence < 0.65:
                            st.warning(f"⚠️ Low OCR confidence ({confidence:.0%}). Please verify the extracted data.")
                        else:
                            st.success(f"✅ Receipt processed (confidence: {confidence:.0%})")

                        # Allow editing before saving
                        st.subheader("Verify extracted data")
                        col1, col2 = st.columns(2)
                        with col1:
                            amount = st.number_input("Amount (₹)", value=float(txn.amount), min_value=0.0)
                            vendor = st.text_input("Vendor", value=txn.counterparty)
                        with col2:
                            txn_date = st.date_input("Date", value=txn.due_date)

                        if st.button("Save Receipt", type="primary"):
                            txn.amount = amount
                            txn.counterparty = vendor
                            txn.due_date = txn_date
                            from data.transaction_store import save_transactions
                            save_transactions([txn])
                            get_analysis.clear()
                            st.success("Receipt saved!")
                    else:
                        st.error("Could not extract data from image. Please use manual entry.")
                except Exception as e:
                    st.error(f"OCR Error: {e}")

    # ── Manual Entry ──────────────────────────────────────────────────────────
    with tab4:
        st.subheader("Manual Transaction Entry")

        col1, col2 = st.columns(2)
        with col1:
            m_type = st.selectbox("Type", ["payable", "receivable", "balance_snapshot"])
            m_amount = st.number_input("Amount (₹)", min_value=0.0, step=100.0)
            m_vendor = st.text_input("Vendor / Counterparty")

        with col2:
            m_date = st.date_input("Date / Due Date", value=date.today())
            m_desc = st.text_input("Description (optional)")
            m_recurring = st.checkbox("Recurring transaction")

        if st.button("Add Transaction", type="primary"):
            if m_amount > 0 and m_vendor:
                txn = Transaction(
                    id=f"manual_{uuid.uuid4().hex[:8]}",
                    amount=m_amount,
                    type=m_type,
                    due_date=m_date,
                    counterparty=m_vendor,
                    source="manual_entry",
                    description=m_desc,
                    is_recurring=m_recurring,
                    confidence=1.0,
                )
                from data.transaction_store import save_transactions
                save_transactions([txn])
                get_analysis.clear()
                st.success(f"✅ Added: {m_vendor} ₹{m_amount:,.0f} on {m_date}")
            else:
                st.error("Please enter amount and vendor name.")

    # ── Current Data Summary ──────────────────────────────────────────────────
    st.divider()
    st.subheader("Current Data in Database")
    all_txns = get_transactions()

    if all_txns:
        df = pd.DataFrame([
            {
                "Date": t.due_date,
                "Vendor": t.counterparty,
                "Amount": f"₹{t.amount:,.0f}",
                "Type": t.type,
                "Source": t.source,
                "Confidence": f"{t.confidence:.0%}",
            }
            for t in sorted(all_txns, key=lambda x: x.due_date, reverse=True)
        ])
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.caption(f"Total: {len(all_txns)} transactions")
    else:
        st.info("No transactions yet. Upload data or load demo data.")


# ─────────────────────────────────────────────────────────────────────────────
# PAGE 3: ACTIONS & EMAILS
# ─────────────────────────────────────────────────────────────────────────────
elif page == "Actions & Emails":
    st.title("Actions & Generated Outputs")
    st.caption("AI-generated explanations and emails — grounded in deterministic decisions.")

    state = get_analysis()

    if not state:
        st.info("No data found. Upload financial data first.")
        st.stop()

    tab1, tab2, tab3 = st.tabs([
        "Reasoning & Explanation", "Negotiation Emails", "Payment Plan"
    ])

    # ── COT Explanation ───────────────────────────────────────────────────────
    with tab1:
        st.subheader("Decision Reasoning")
        st.caption("Plain-English explanation of why each obligation was prioritized.")

        if st.button("Generate Explanation", type="primary", key="gen_cot"):
            with st.spinner("Generating explanation..."):
                explanation = generate_cot_explanation(state)
                st.session_state["cot_explanation"] = explanation

        if "cot_explanation" in st.session_state:
            st.info(st.session_state["cot_explanation"])

        # Show decision summary table
        st.subheader("Decision Summary")
        deferred = [o for o in state["obligations"] if o["action"] in ("DEFER", "PAY_PARTIAL")]
        paid = [o for o in state["obligations"] if o["action"] == "PAY_FULL"]

        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**✅ Paying in Full**")
            for o in paid:
                st.markdown(f"- {o['counterparty']}: ₹{o['amount']:,.0f}")

        with col2:
            st.markdown("**⏸ Deferring**")
            for o in deferred:
                action_label = "Partial" if o["action"] == "PAY_PARTIAL" else "Defer"
                st.markdown(f"- {o['counterparty']}: ₹{o['amount']:,.0f} ({action_label})")

    # ── Negotiation Emails ────────────────────────────────────────────────────
    with tab2:
        st.subheader("Negotiation Emails")
        st.caption("Tone-adapted deferral emails. One per deferred vendor. Click to generate.")

        deferred_obs = [o for o in state["obligations"]
                        if o["action"] in ("DEFER", "PAY_PARTIAL")]

        if not deferred_obs:
            st.success("No deferrals needed! All obligations can be paid in full.")
        else:
            business_name = st.text_input("Your business name", value="ABC Enterprises")

            for ob in deferred_obs:
                vendor_type = (ob.get("vendor_profile") or {}).get("relationship_type", "unknown")
                tone_label = {
                    "long_term": "Warm & collaborative",
                    "new": "Formal & professional",
                    "critical": "Urgent & reassuring",
                    "occasional": "Polite & brief",
                    "unknown": "Professional",
                }.get(vendor_type, "Professional")

                with st.expander(
                    f"📧 {ob['counterparty']} — ₹{ob['amount']:,.0f} "
                    f"| Tone: {tone_label}"
                ):
                    col1, col2 = st.columns([2, 1])
                    with col1:
                        st.markdown(f"**Amount:** ₹{ob['amount']:,.0f}")
                        st.markdown(f"**Due:** {ob['due_date']}")
                        st.markdown(f"**Proposed new date:** {ob.get('deferred_to', 'TBD')}")
                        st.caption(f"**Reason:** {ob.get('deferral_reason', 'Cash flow timing')}")

                    with col2:
                        st.markdown(f"**Vendor type:** {vendor_type}")
                        st.markdown(f"**Tone:** {tone_label}")

                    email_key = f"email_{ob['id']}"
                    if st.button(f"Generate Email for {ob['counterparty']}", key=f"btn_{ob['id']}"):
                        with st.spinner(f"Drafting email for {ob['counterparty']}..."):
                            email_text = generate_email(ob, business_name)
                            st.session_state[email_key] = email_text

                    if email_key in st.session_state:
                        st.text_area("Generated email (edit before sending):",
                                     value=st.session_state[email_key],
                                     height=250,
                                     key=f"edit_{ob['id']}")
                        if st.button(f"Copy email", key=f"copy_{ob['id']}"):
                            st.toast("Email copied to clipboard!")

    # ── Payment Plan ──────────────────────────────────────────────────────────
    with tab3:
        st.subheader("Payment Plan Summary")
        st.caption("Week-by-week narrative of the payment schedule.")

        if st.button("Generate Payment Plan", type="primary", key="gen_plan"):
            with st.spinner("Generating payment plan..."):
                plan_text = generate_payment_plan(state)
                st.session_state["payment_plan"] = plan_text

        if "payment_plan" in st.session_state:
            st.info(st.session_state["payment_plan"])

        # Always show the structured plan table
        st.subheader("Structured Schedule")
        all_obs = sorted(state["obligations"], key=lambda o: o["due_date"])
        plan_df = pd.DataFrame([
            {
                "Vendor": o["counterparty"],
                "Due Date": o["due_date"],
                "Amount": f"₹{o['amount']:,.0f}",
                "Action": o["action"],
                "Pay": f"₹{o['amount_to_pay']:,.0f}" if o["amount_to_pay"] > 0 else "—",
                "Defer To": o.get("deferred_to") or "—",
            }
            for o in all_obs
        ])
        st.dataframe(plan_df, use_container_width=True, hide_index=True)


# ─────────────────────────────────────────────────────────────────────────────
# PAGE 4: MANAGE VENDORS
# ─────────────────────────────────────────────────────────────────────────────
elif page == "Manage Vendors":
    st.title("Vendor Relationship Profiles")
    st.caption("Define vendor relationships. This controls email tone and flexibility scoring.")

    # ── Add / Edit Vendor ─────────────────────────────────────────────────────
    with st.expander("➕ Add or Update Vendor", expanded=False):
        col1, col2 = st.columns(2)
        with col1:
            v_name = st.text_input("Vendor Name")
            v_type = st.selectbox("Relationship Type",
                ["long_term", "new", "critical", "occasional", "unknown"])
            v_months = st.number_input("Months Active", min_value=0.0, step=1.0)
            v_history = st.selectbox("Payment History",
                ["always_paid", "sometimes_late", "unknown"])

        with col2:
            v_partial = st.checkbox("Accepts Partial Payments")
            v_grace = st.checkbox("Has Grace Period")
            v_grace_days = st.number_input("Grace Days", min_value=0, step=1,
                                            disabled=not v_grace)
            v_notes = st.text_area("Notes", height=80)

        if st.button("Save Vendor", type="primary"):
            if v_name:
                profile = VendorProfile(
                    name=v_name,
                    relationship_type=v_type,
                    months_active=v_months,
                    payment_history=v_history,
                    allows_partial=v_partial,
                    has_grace_period=v_grace,
                    grace_days=int(v_grace_days) if v_grace else 0,
                    notes=v_notes,
                )
                upsert_vendor(profile)
                get_analysis.clear()
                st.success(f"✅ Vendor '{v_name}' saved.")
                st.rerun()
            else:
                st.error("Vendor name is required.")

    # ── Vendor List ───────────────────────────────────────────────────────────
    st.subheader("All Vendors")
    vendors = get_all_vendors()

    if vendors:
        type_emoji = {
            "long_term": "🤝", "new": "🆕",
            "critical": "⚠️", "occasional": "📋", "unknown": "❓"
        }
        tone_map = {
            "long_term": "Warm & collaborative",
            "new": "Formal & professional",
            "critical": "Urgent & reassuring",
            "occasional": "Polite & brief",
            "unknown": "Professional (default)",
        }

        for v in vendors:
            with st.container(border=True):
                c1, c2, c3, c4 = st.columns([3, 2, 2, 3])
                with c1:
                    st.markdown(
                        f"{type_emoji.get(v.relationship_type,'❓')} **{v.name}**"
                    )
                    st.caption(f"Type: {v.relationship_type}")
                with c2:
                    st.markdown(f"**{v.months_active:.0f}** months")
                    st.caption("relationship age")
                with c3:
                    features = []
                    if v.allows_partial:
                        features.append("Partial OK")
                    if v.has_grace_period:
                        features.append(f"{v.grace_days}d grace")
                    st.markdown(", ".join(features) if features else "Standard terms")
                    st.caption(v.payment_history)
                with c4:
                    st.caption(f"Email tone: {tone_map.get(v.relationship_type, 'Professional')}")
    else:
        st.info("No vendor profiles yet. Add vendors above or load demo data.")

    if st.button("Seed Default Demo Vendors"):
        seed_demo_vendors()
        st.success("Demo vendors added!")
        st.rerun()
