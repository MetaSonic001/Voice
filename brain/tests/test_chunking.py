from app.domain import Block
from app.services.chunking import SEP, _table_chunks, chunk_blocks


def _blocks():
    return [
        Block("heading", "AU Value Savings Account", 1),
        Block("heading", "Key Benefits", 2),
        Block("list", "Earn up to 6.75% p.a. on savings balances."),
        Block("heading", "Balance Requirement", 2),
        Block("para", "Urban branches: AMB Rs 10,000 or FD Rs 1,00,000."),
        Block("heading", "AU Digital Savings Account", 1),
        Block("heading", "Key Benefits", 2),
        Block("list", "Zero balance account."),
        Block("heading", "Quick Comparison", 2),
        Block("table", rows=[["Feature", "Digital", "Value"], ["Minimum Balance", "Zero", "Rs 5K-10K"], ["Lounge", "No", "No"]]),
    ]


def test_sections_are_grouped_per_product_with_paths():
    chunks = chunk_blocks(_blocks(), "CASA KB")
    text_chunks = [c for c in chunks if c.kind == "text"]
    assert text_chunks[0].section_path == f"CASA KB{SEP}AU Value Savings Account{SEP}Key Benefits; Balance Requirement"
    assert "## Balance Requirement" in text_chunks[0].text
    assert all("Digital" not in c.text for c in text_chunks[:1])  # products never mix in one chunk
    assert text_chunks[1].section_path.startswith(f"CASA KB{SEP}AU Digital Savings Account")


def test_table_rows_carry_their_headers():
    table = [c for c in chunk_blocks(_blocks(), "CASA KB") if c.kind == "table"][0]
    assert "Feature: Minimum Balance | Digital: Zero | Value: Rs 5K-10K" in table.text
    assert table.section_path.endswith("Quick Comparison")


def test_duplicate_sections_are_dropped():
    blocks = _blocks()[:5] * 2  # the whole first product repeated, as in the CASA KB
    assert len(chunk_blocks(blocks, "CASA KB")) == 1


def test_large_sections_are_split():
    blocks = [Block("heading", "AU Big", 1), Block("heading", "Details", 2)] + [Block("list", f"item{i} " * 40) for i in range(10)]
    chunks = chunk_blocks(blocks, "Doc", target_words=100, max_words=150)
    assert len(chunks) > 1
    assert all(c.word_count <= 160 for c in chunks)


def test_rate_sheet_rows_carry_section_band_and_column():
    # pdfplumber shape: numbering cells, merged cells as None, headers stacked over several rows
    rows = [["2", "Term Deposits", None, None, None],
            [None, None, None, "Less than 3 Crore (Callable)", "1 Crore to 3 Crore (Non-Callable)"],
            [None, "Tenure", None, "Interest Rate", None],
            [None, None, None, "Regular", "Regular"],
            [None, "12 Months 1 Day to 15 Months", None, "7.10%", "7.20%"],
            [None, "8", "Gold Loan", "Upto 24% p.a.", ""]]
    chunks = _table_chunks(rows, "Rates", 180)
    lines = chunks[0].text.splitlines()
    assert lines[0] == "Term Deposits › Interest Rate › Regular"  # shared context, once per chunk
    assert lines[1] == ("Tenure: 12 Months 1 Day to 15 Months | Less than 3 Crore (Callable): 7.10% | "
                        "1 Crore to 3 Crore (Non-Callable): 7.20%")
    assert any("Gold Loan" in c.text and "Upto 24% p.a." in c.text and "8" not in c.text for c in chunks)
