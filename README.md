# Invoice Tariff AI Analyzer

## What it does
- Upload any PDF invoice (scanned or digital)
- Extracts all line items using OCR + AI
- Predicts the HTS code for each item using GPT-4o
- Looks up the Bahamas Customs Tariff for each code
- Outputs a downloadable summary table

## How to use

1. Install Tesseract OCR on your machine:
   - Windows: https://github.com/tesseract-ocr/tesseract
   - macOS: `brew install tesseract`
   - Ubuntu: `sudo apt-get install tesseract-ocr`

2. Install Python packages:

3. Set your OpenAI API key as an environment variable:

4. Run the app:

5. Upload an invoice PDF in the web interface.

## Notes

- Make sure you have a working internet connection for OpenAI and Bahamas Customs site lookup.
- If you want to use Google Vision or Azure for OCR, ask ChatGPT for those versions!
