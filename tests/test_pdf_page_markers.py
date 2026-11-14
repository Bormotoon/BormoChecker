from __future__ import annotations

from pathlib import Path

from bormochecker.evaluate import _annotate_quote_with_page
from bormochecker.scripts.build_mirrors_fts import extract_pdf_content
from bormochecker.sources import Doc


TWO_PAGE_PDF = b"""%PDF-1.4
1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj
2 0 obj << /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >> endobj
3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 5 0 R /Resources << /Font << /F1 7 0 R >> >> >> endobj
4 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 6 0 R /Resources << /Font << /F1 7 0 R >> >> >> endobj
5 0 obj << /Length 44 >> stream
BT /F1 12 Tf 100 700 Td (First page text) Tj ET
endstream
endobj
6 0 obj << /Length 45 >> stream
BT /F1 12 Tf 100 700 Td (Second page text) Tj ET
endstream
endobj
7 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj
xref
0 8
0000000000 65535 f 
0000000009 00000 n 
0000000058 00000 n 
0000000121 00000 n 
0000000242 00000 n 
0000000363 00000 n 
0000000457 00000 n 
0000000552 00000 n 
trailer << /Size 8 /Root 1 0 R >>
startxref
628
%%EOF"""


def test_extract_pdf_content_two_page_markers(tmp_path: Path):
    pdf_path = tmp_path / "two_page_document.pdf"
    pdf_path.write_bytes(TWO_PAGE_PDF)

    title, text = extract_pdf_content(pdf_path)
    assert "[page: 1]" in text
    assert "[page: 2]" in text
    assert "First page text" in text
    assert "Second page text" in text


def test_quote_evidence_annotated_with_page():
    doc = Doc(
        id="doc_pdf",
        url="https://example.edu/doc.pdf",
        title="Тестовый приказ",
        text="[page: 1]\nВводная часть приказа.\n\n[page: 2]\nУтвердить норматив в размере 5000 рублей.",
    )
    quote = "Утвердить норматив в размере 5000 рублей."
    annotated = _annotate_quote_with_page(doc, quote)
    assert annotated == "Утвердить норматив в размере 5000 рублей. (стр. 2)"
