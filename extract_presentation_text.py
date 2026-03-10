
import pdfplumber
import sys

def extract_text(pdf_path):
    print(f"Reading: {pdf_path}")
    text_content = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for i, page in enumerate(pdf.pages):
                text = page.extract_text()
                if text:
                    text_content.append(f"--- Slide {i+1} ---\n{text}\n")
        
        full_text = "\n".join(text_content)
        with open('pdf_content.txt', 'w', encoding='utf-8') as f:
            f.write(full_text)
        print("Text saved to pdf_content.txt")
        
    except Exception as e:
        print(f"Error reading PDF: {e}")

if __name__ == "__main__":
    pdf_path = "g:/New folder/SmartCV/SmartCV - AI Career Agent .pdf"
    extract_text(pdf_path)
