import streamlit as st
import pdfplumber
import pytesseract
from PIL import Image
import openai
import pandas as pd
import os
import requests
from bs4 import BeautifulSoup
import json
import re
from datetime import datetime

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

    .block-container {
        padding-top: 1.5rem;
        padding-bottom: 2.5rem;
    }

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

    .metric-card h3 {
        margin: 0;
        color: var(--muted-text);
        font-size: 0.85rem;
        letter-spacing: 0.05em;
        text-transform: uppercase;
    }

    .metric-card p {
        margin: 0.25rem 0 0;
        font-size: 1.4rem;
        font-weight: 600;
        color: var(--primary-blue);
    }

    div[data-testid="stMetricValue"] {
        color: var(--primary-blue) !important;
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

# Set your OpenAI API key (from environment variable or .streamlit/secrets.toml)
openai_api_key = os.getenv("OPENAI_API_KEY", st.secrets.get("openai_api_key", ""))
if not openai_api_key:
    st.error("Please set your OpenAI API key as an environment variable (OPENAI_API_KEY).")
    st.stop()
openai.api_key = openai_api_key

def extract_text_with_ocr(uploaded_file):
    all_text = ""
    with pdfplumber.open(uploaded_file) as pdf:
        for i, page in enumerate(pdf.pages):
            text = page.extract_text()
            if text and len(text.strip()) > 30:
                all_text += text + "\n"
            else:
                st.warning(f"Page {i+1} had little or no text. Using OCR.")
                pil_image = page.to_image(resolution=300).original
                ocr_text = pytesseract.image_to_string(pil_image)
                all_text += ocr_text + "\n"
    return all_text

def ai_extract_invoice_data(pdf_text):
    prompt = f"""Extract the following from this invoice text:
    - Invoice Number
    - Invoice Date
    - Vendor
    - For each line item: item/manufacturer part number, description, brand, quantity, price, extended price.
    Present as a JSON list of line items, and include invoice number and date for each item.
    Text: {pdf_text}
    """
    response = openai.ChatCompletion.create(
        model="gpt-4o",
        messages=[{"role": "system", "content": "You are an expert at understanding invoices."},
                  {"role": "user", "content": prompt}]
    )
    return response.choices[0].message.content

def ai_predict_hts(description, part_number):
    prompt = f"""Given the following item description and part number, predict the most likely 6-digit HTS (Harmonized Tariff Schedule) code for US import, based on standard customs practices. Give ONLY the 6-digit code.
    Description: {description}
    Part Number: {part_number}
    """
    response = openai.ChatCompletion.create(
        model="gpt-4o",
        messages=[{"role": "system", "content": "You are a customs tariff specialist."},
                  {"role": "user", "content": prompt}]
    )
    raw = response.choices[0].message.content.strip()
    code_match = re.search(r"\b\d{6,10}\b", raw)
    if code_match:
        return code_match.group(0)[:6]
    return raw.split()[0]

def get_bahamas_tariff(hts_code):
    url = f'https://www.bahamascustoms.gov.bs/tariffs-and-various-taxes-collected-by-customs/tariff-search/?q={hts_code}'
    try:
        resp = requests.get(url, timeout=10)
        soup = BeautifulSoup(resp.text, 'html.parser')
        table = soup.find('table')
        if table:
            rows = table.find_all('tr')
            # Take the first non-header row
            for r in rows[1:]:
                columns = [c.get_text(strip=True) for c in r.find_all(['td', 'th'])]
                if columns and hts_code[:6] in columns[0]:
                    return " | ".join(columns)
            return "Result table found, but HTS code row missing"
        return "No result table found"
    except Exception as e:
        return f"Error: {str(e)}"

EXCEL_LOG_PATH = os.path.join("data", "invoice_tariff_log.xlsx")

if "save_confirmed" not in st.session_state:
    st.session_state.save_confirmed = False
    st.session_state.last_saved = None


def append_to_excel_log(df: pd.DataFrame, path: str) -> pd.DataFrame:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        existing = pd.read_excel(path)
        combined = pd.concat([existing, df], ignore_index=True)
    else:
        combined = df
    combined.to_excel(path, index=False)
    return combined


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

if uploaded_file:
    with st.spinner("Extracting text (using OCR if needed)..."):
        pdf_text = extract_text_with_ocr(uploaded_file)
    with st.expander("Preview extracted text", expanded=False):
        st.write(pdf_text[:2000] + ("..." if len(pdf_text) > 2000 else ""))

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
    try:
        # Some AI responses might have trailing text, try to parse JSON robustly
        data_start = invoice_data_json.find("[")
        data_end = invoice_data_json.rfind("]") + 1
        line_items = json.loads(invoice_data_json[data_start:data_end])
    except Exception as e:
        st.error("AI extraction did not return clean JSON, please check the prompt or manually correct.")
        st.write(invoice_data_json)
        line_items = []

    summary = []
    invoice_number_tracker = {}
    for item in line_items:
        desc = item.get("description", "")
        part = item.get("item/manufacturer part number", "")
        brand = item.get("brand", "")
        qty = item.get("quantity", "")
        price = item.get("price", "")
        ext_price = item.get("extended price", "")
        invoice_number = item.get("invoice number", item.get("invoice", ""))
        invoice_date = item.get("invoice date", "")
        invoice_key = (invoice_number, invoice_date)
        invoice_number_tracker.setdefault(invoice_key, 0)
        invoice_number_tracker[invoice_key] += 1
        line_index = invoice_number_tracker[invoice_key]
        hts_code = ai_predict_hts(desc, part)
        bahamas_tariff = get_bahamas_tariff(hts_code)
        summary.append({
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
    if summary:
        df = pd.DataFrame(summary)

        with st.container():
            st.markdown("<div class='invoice-card'>", unsafe_allow_html=True)
            st.subheader("3. Review & Export Findings")
            st.dataframe(
                df,
                use_container_width=True,
                hide_index=True,
            )

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
                timestamp = st.session_state.last_saved.strftime("%d %b %Y • %H:%M") if st.session_state.last_saved else ""
                st.markdown(
                    f"""
                    <div class='save-banner'>
                        ✅ Results appended to the shared Excel log.<br/>
                        <span style='font-size:0.9rem;'>Saved {timestamp}. File location: <code>{EXCEL_LOG_PATH}</code></span>
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
            st.write(
                "Use the Bahamas Customs tariff search for confirmation and duty rate checks."
            )
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
