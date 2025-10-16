import os
import re
import json
from datetime import datetime

import streamlit as st
import pdfplumber
import pytesseract
from PIL import Image
import pandas as pd
import requests
from bs4 import BeautifulSoup

# -------------- Streamlit Page Setup --------------
st.set_page_config(
    page_title="Invoice Tariff Workbench",
    layout="wide",
    page_icon="📄",
)

st.markdown(
    """
    <style>
    :root {
        --primary-blue: #0F4C81;
        --soft-blue: #E8F1FA;
        --text-dark: #0C1E34;
        --muted-text: #5A6C7D;
        --panel-border: #C6D3E0;
    }
    body, .stApp {
        background-color: var(--soft-blue);
        color: var(--text-dark);
        font-family: "Inter", "Segoe UI", sans-serif;
    }
    .stApp header {display:none;}
    .block-container { padding-top: 1.5rem; padding-bottom: 2.5rem; }
    .invoice-card {
        background: white;
        border-radius: 14px;
        border: 1px solid var(--panel-border);
        padding: 2rem 2.25rem;
        box-shadow: 0 12px 24px rgba(15, 76, 129, 0.06);
        margin-bottom: 1.5rem;
    }
    .info-note {
        background: rgba(15, 76, 129, 0.07);
        border-left: 4px solid var(--primary-blue);
        padding: 0.85rem 1.25rem;
        color: var(--text-dark);
        border-radius: 0 10px 10px 0;
        margin-bottom: 1.25rem;
        font-size: 0.95rem;
    }
    .metric-card {
        background: white;
        border: 1px solid var(--panel-border);
        border-radius: 12px;
        padding: 1.1rem 1.3rem;
        text-align: left;
        height: 100%;
    }
    .save-banner {
        background: rgba(12, 30, 52, 0.92);
        color: white;
        border-radius: 12px;
        padding: 1rem 1.5rem;
        margin-top: 1rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("Invoice Tariff Workbench")
st.caption("Internal customs operations tool for Bahamian imports")

# -------------- OpenAI Client / Secrets --------------
api_key = os.getenv("openai_API_key", st.secrets.get("openai_API_key", ""))
if not api_key:
    st.error("Set your OpenAI key as env var OPENAI_API_KEY or in Streamlit secrets as `openai_api_key`.")
    st.stop()

try:
    from openai import OpenAI
    client = OpenAI(api_key=api_key)
except Exception as e:
    st.error(f"OpenAI client import/init failed: {e}")
    st.stop()

# -------------- Helpers --------------
def extract_text_with_ocr(uploaded_file) -> str:
    """
    Extract text from a PDF; if a page has little/no text, OCR it.
    """
    all_text = ""
    with pdfplumber.open(uploaded_file) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception as e:
                text = ""

            if text.strip() and len(text.strip()) > 30:
                all_text += text + "\n"
                continue

            # Fallback to OCR
            st.warning(f"Page {i} had little/no selectable text. Using OCR.")
            try:
                # Render page raster for OCR; to_image may require wand/ImageMagick
                pil_img = page.to_image(resolution=300).original  # may raise if wand/imagemagick missing
                ocr_text = pytesseract.image_to_string(pil_img) or ""
                all_text += ocr_text + "\n"
            except Exception as e:
                st.error(
                    f"OCR render failed on page {i}. Ensure ImageMagick/Wand is installed. Error: {e}"
                )
    return all_text

def ai_extract_invoice_data(pdf_text: str) -> str:
    """
    Ask the model to extract invoice header + line items and return JSON (as text).
    """
    prompt = (
        "Extract the following from this invoice text:\n"
        "- Invoice Number\n"
        "- Invoice Date\n"
        "- Vendor\n"
        "- For each line item: item/manufacturer part number, description, brand, quantity, price, extended price.\n"
        "Return ONLY a JSON array of line item objects. Each object must include the invoice number and invoice date.\n"
        "If a field is unknown, set it to an empty string.\n\n"
        f"Text:\n{pdf_text}"
    )
    resp = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "You are an expert at understanding invoices and producing strict JSON."},
            {"role": "user", "content": prompt},
        ],
        temperature=0
    )
    return resp.choices[0].message.content or ""

def ai_predict_hts(description: str, part_number: str) -> str:
    """
    Ask the model for the likely 6-digit HTS code. Returns 6 digits when possible.
    """
    prompt = (
        "Given the following item description and part number, predict the most likely 6-digit HTS "
        "(Harmonized Tariff Schedule) code for US import, based on standard customs practices. "
        "Respond with ONLY the 6-digit code (no words).\n\n"
        f"Description: {description}\n"
        f"Part Number: {part_number}"
    )
    resp = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "You are a customs tariff specialist."},
            {"role": "user", "content": prompt},
        ],
        temperature=0
    )
    raw = (resp.choices[0].message.content or "").strip()
    m = re.search(r"\b\d{6,10}\b", raw)
    return m.group(0)[:6] if m else (raw[:6] if raw and raw[:6].isdigit() else "")

def get_bahamas_tariff(hts_code: str) -> str:
    """
    Scrape Bahamas Customs search page for the code row.
    """
    if not hts_code:
        return "No HTS code predicted"
    url = f'https://www.bahamascustoms.gov.bs/tariffs-and-various-taxes-collected-by-customs/tariff-search/?q={hts_code}'
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')
        table = soup.find('table')
        if not table:
            return "No result table found"
        rows = table.find_all('tr')
        for r in rows[1:]:
            cols = [c.get_text(strip=True) for c in r.find_all(['td', 'th'])]
            if cols and hts_code[:6] in (cols[0] or ""):
                return " | ".join(cols)
        return "Result table found, but HTS code row missing"
    except Exception as e:
        return f"Error: {str(e)}"

EXCEL_LOG_PATH = os.path.join("data", "invoice_tariff_log.xlsx")
if "save_confirmed" not in st.session_state:
    st.session_state.save_confirmed = False
    st.session_state.last_saved = None
    st.session_state.latest_log = None

def append_to_excel_log(df: pd.DataFrame, path: str) -> pd.DataFrame:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        existing = pd.read_excel(path)
        combined = pd.concat([existing, df], ignore_index=True)
    else:
        combined = df
    combined.to_excel(path, index=False)
    return combined

# -------------- UI: Upload --------------
with st.container():
    st.markdown("<div class='invoice-card'>", unsafe_allow_html=True)
    st.subheader("1. Upload & Index the Supplier Invoice")
    st.markdown(
        """
        <div class='info-note'>
        Upload the supplier PDF invoice. We will OCR any scanned pages, extract the line items, and prepare them for tariff
        classification. Ensure the document clearly shows manufacturer part numbers and product descriptions.
        </div>
        """,
        unsafe_allow_html=True,
    )
    uploaded_file = st.file_uploader("Select invoice PDF", type="pdf", label_visibility="collapsed")
    st.markdown("</div>", unsafe_allow_html=True)

# -------------- Processing --------------
if uploaded_file:
    with st.spinner("Extracting text (using OCR if needed)..."):
        pdf_text = extract_text_with_ocr(uploaded_file)

    with st.expander("Preview extracted text", expanded=False):
        preview = (pdf_text or "")
        st.write(preview[:2000] + ("..." if len(preview) > 2000 else ""))

    st.markdown(
        """
        <div class='invoice-card'>
        <h3 style='color: var(--muted-text); letter-spacing:0.08em; text-transform: uppercase;'>2. Line Item Intelligence</h3>
        <p style='color: var(--text-dark); font-size:0.95rem;'>
        Line items are parsed and enriched via GPT-4o. Each entry is cross-referenced with the Bahamas Customs tariff search
        using the suggested HS code.
        </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.info("Extracting line items and invoice fields using AI (GPT-4o)...")
    invoice_data_json = ai_extract_invoice_data(pdf_text)

    # Try to parse a JSON array anywhere in the response
    line_items = []
    try:
        start = invoice_data_json.find("[")
        end = invoice_data_json.rfind("]")
        if start != -1 and end != -1 and end > start:
            line_items = json.loads(invoice_data_json[start : end + 1])
        else:
            # If model returned object with a key, try to extract 'items' or similar
            obj = json.loads(invoice_data_json)
            if isinstance(obj, dict):
                # pick first array value
                for v in obj.values():
                    if isinstance(v, list):
                        line_items = v
                        break
    except Exception:
        st.error("AI extraction did not return clean JSON. Displaying raw model output below for troubleshooting.")
        st.code(invoice_data_json)

    summary_rows = []
    invoice_number_tracker = {}
    for item in line_items:
        # Normalize keys; model outputs can vary slightly
        desc = item.get("description", item.get("Description", ""))
        part = item.get("item/manufacturer part number", item.get("part_number", item.get("Part Number", "")))
        brand = item.get("brand", item.get("Brand", ""))
        qty = item.get("quantity", item.get("Quantity", ""))
        price = item.get("price", item.get("Price", ""))
        ext_price = item.get("extended price", item.get("extended_price", item.get("Ext. Price", "")))
        invoice_number = item.get("invoice number", item.get("invoice", item.get("Invoice", "")))
        invoice_date = item.get("invoice date", item.get("Invoice Date", ""))

        key = (invoice_number, invoice_date)
        invoice_number_tracker.setdefault(key, 0)
        invoice_number_tracker[key] += 1
        line_index = invoice_number_tracker[key]

        hts_code = ai_predict_hts(desc or "", part or "")
        bahamas_tariff = get_bahamas_tariff(hts_code)

        summary_rows.append({
            "Invoice": invoice_number,
            "Invoice Date": invoice_date,
            "Line": line_index,
            "Description": desc,
            "Part Number": part,
            "Brand": brand,
            "Qty": qty,
            "Price": price,
            "Ext. Price": ext_price,
            "HTS Code": hts_code,
            "Bahamas Tariff Result": bahamas_tariff
        })

    if summary_rows:
        df = pd.DataFrame(summary_rows)

        with st.container():
            st.markdown("<div class='invoice-card'>", unsafe_allow_html=True)
            st.subheader("3. Review & Export Findings")
            st.dataframe(df, use_container_width=True, hide_index=True)

            col_download, col_save = st.columns([1, 1])
            with col_download:
                st.download_button(
                    "Download as CSV",
                    df.to_csv(index=False),
                    "invoice_tariff_summary.csv",
                    type="primary",
                )
            with col_save:
                if st.button("Append to internal Excel log", type="secondary"):
                    combined = append_to_excel_log(df, EXCEL_LOG_PATH)
                    st.session_state.save_confirmed = True
                    st.session_state.last_saved = datetime.now()
                    st.session_state.latest_log = combined

            if st.session_state.get("save_confirmed"):
                ts = st.session_state.last_saved.strftime("%d %b %Y • %H:%M") if st.session_state.last_saved else ""
                st.markdown(
                    f"""
                    <div class='save-banner'>
                        ✅ Results appended to the shared Excel log.<br/>
                        <span style='font-size:0.9rem;'>Saved {ts}. File location: <code>{EXCEL_LOG_PATH}</code></span>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                with st.expander("View aggregated Excel log", expanded=False):
                    log_df = st.session_state.get("latest_log")
                    if log_df is not None:
                        st.dataframe(log_df, use_container_width=True, hide_index=True)
            st.markdown("</div>", unsafe_allow_html=True)

        col1, col2 = st.columns(2)
        with col1:
            st.markdown("### Manual HS Lookups")
            st.write("Use the Bahamas Customs tariff search for confirmation and duty rate checks.")
            st.link_button(
                "Open Bahamas Tariff Search",
                "https://www.bahamascustoms.gov.bs/tariffs-and-various-taxes-collected-by-customs/tariff-search/",
                type="primary",
            )
        with col2:
            st.markdown("### Research Tips")
            st.markdown(
                "- Validate descriptions using manufacturer websites.\n"
                "- Cross-check HS codes with recent rulings.\n"
                "- Capture duty rate notes in the Excel log after review."
            )
    else:
        st.warning("No line items found. Please check your invoice or try another file.")

st.markdown("---")
st.caption("For internal customs brokerage use only. Ensure compliance with Bahamas Customs regulations.")


