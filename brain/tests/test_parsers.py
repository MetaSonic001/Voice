import pytest

from app.adapters.parsers import DocxParser, TableFileParser
from app.services.chunking import chunk_blocks
from app.services.documents import DocumentError, detect_type
from tests.conftest import KB_DIR, SAMPLE_DIR

CASA = KB_DIR / "CASA+TD KB.docx"


@pytest.mark.skipif(not CASA.exists(), reason="sample KB not present")
def test_docx_headings_and_products_are_detected():
    parsed = DocxParser().parse(CASA, {"product_prefixes": ["AU "]})
    products = [b.text for b in parsed.blocks if b.kind == "heading" and b.level == 1]
    assert "AU Royale Savings Account" in products
    assert "AU Value Savings Account" in products
    assert parsed.report["tables"] >= 5
    chunks = chunk_blocks(parsed.blocks, "CASA+TD KB")
    assert any("Royale" in c.section_path and "Rs 1 lakh" in c.text for c in chunks)
    # the Royale section appears twice in the source; its identical chunks must be stored once
    assert len({(c.section_path, c.text) for c in chunks}) == len(chunks)


@pytest.mark.skipif(not (SAMPLE_DIR / "Inbound_Voice_Agent_Dummy_Data.xlsx").exists(), reason="dummy data not present")
def test_xlsx_sheet_filter_reads_only_requested_sheet():
    parsed = TableFileParser().parse(SAMPLE_DIR / "Inbound_Voice_Agent_Dummy_Data.xlsx", {"sheets": ["Acct Debit card Mapping"]})
    assert list(parsed.report["sheets"]) == ["Acct Debit card Mapping"]
    table = [b for b in parsed.blocks if b.kind == "table"][0]
    assert table.rows[0] == ["Account Type", "Debit Card"]
    assert ["Royale", "Royale Debit Card"] in table.rows


def test_detect_type_checks_content_not_just_extension():
    assert detect_type("notes.md", b"# Title\nhello") == "md"
    with pytest.raises(DocumentError):
        detect_type("fake.pdf", b"this is not a pdf")
    with pytest.raises(DocumentError):
        detect_type("virus.exe", b"MZ\x90\x00")
