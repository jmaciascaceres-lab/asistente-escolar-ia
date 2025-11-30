import sys
from pathlib import Path
from pypdf import PdfReader

def test_extraction(pdf_path):
    print(f"Testing extraction for: {pdf_path}")
    try:
        reader = PdfReader(str(pdf_path))
        texts = []
        for i, page in enumerate(reader.pages):
            t = page.extract_text() or ""
            texts.append(t)
            if i % 10 == 0:
                print(f"Processed page {i}, text length so far: {sum(len(x) for x in texts)}")
                sys.stdout.flush()
        
        full_text = "\n\n".join(texts)
        print(f"Total length: {len(full_text)}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_extraction("docs/2016_Orientaciones-para-la-construcción-de-comunidades-educativas-inclusivas.pdf")
