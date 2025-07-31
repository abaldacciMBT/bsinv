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

st.set_page_config(layout="wide")
st.title("AI Invoice-to-Bahamas Customs Tariff Analyzer")

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
    # Try to only return the code, strip any extra text
    return response.choices[0].message.content.strip().split()[0]

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

uploaded_file = st.file_uploader("Upload an Invoice PDF", type="pdf")

if uploaded_file:
    with st.spinner("Extracting text (using OCR if needed)..."):
        pdf_text = extract_text_with_ocr(uploaded_file)
    st.subheader("Extracted Invoice Text (first 1000 characters):")
    st.text(pdf_text[:1000] + ("..." if len(pdf_text) > 1000 else ""))

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
    for item in line_items:
        desc = item.get("description", "")
        part = item.get("item/manufacturer part number", "")
        brand = item.get("brand", "")
        qty = item.get("quantity", "")
        price = item.get("price", "")
        ext_price = item.get("extended price", "")
        hts_code = ai_predict_hts(desc, part)
        bahamas_tariff = get_bahamas_tariff(hts_code)
        summary.append({
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
        st.subheader("Summary Table")
        st.dataframe(df)
        st.download_button("Download as CSV", df.to_csv(index=False), "summary.csv")
    else:
        st.warning("No line items found. Please check your invoice or try another file.")

st.markdown("---")
st.caption("Created by ChatGPT – For production, ask for authentication & more features!")
