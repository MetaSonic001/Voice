# ADR-0005: Structure-aware chunking with native parsers; no OCR

- **Status:** Accepted
- **Date:** 2026-11-03
- **Deciders:** project owner

## Context

The bank's documents are Word files (product overviews with headings and lists), PDFs (rate cards full of tables with merged headers) and spreadsheets. Splitting them into fixed-size slices breaks tables and loses which product a sentence belongs to. That causes wrong-product answers.

## Decision

- **Parsers:**
  - DOCX is read directly from its XML (stdlib), keeping bold headings, lists and tables;
  - PDF uses **pdfplumber**, keeping table rows, cells and merged headers;
  - XLSX/CSV are read as tables;
  - MD/TXT as text.
- **Chunks follow the document's structure:**
  - one section (heading + its paragraphs) is the unit;
  - units of the same product are packed up to **~180 words** (hard cap 260);
  - every chunk carries a **section path** ("Document › Product › Section") used in search and citations.
- **Table rows** are written out with their headers ("Tenure: 13 months | Senior citizen: 7.60%"), so each row makes sense on its own.
- Exact duplicate chunks within a document are dropped.
- Documents are **versioned**. A new upload makes a new version active; the old one stays on record.
- **No OCR.** Scanned (image-only) PDFs aren't supported.

## Options considered

| Option | Pros | Cons |
|---|---|---|
| **Structure-aware + native parsers** | Product context kept; tables readable row by row; tiny dependency footprint | Heuristics for headings |
| Fixed-size token windows | Trivial | Cuts tables and sections; loses product context |
| Docling / ML layout parsing | Handles odd layouts | 103 packages; lost ~5% of the rates PDF |
| OCR (Textract, Tesseract) | Scanned docs | Not needed for the supplied KB; cloud OCR is a data-handling decision |

## Consequences

- **Easier:** answers cite a precise section; rate tables answer exact tenure questions.
- **Harder:** long PDFs bring repeated page headers and footers, multi-column pages and tables split across pages; page numbers aren't kept yet. See [0022](0022-rag-at-large-scale.md).
- **Revisit when:** scanned documents arrive (add OCR), or when AU's own document formats are known.

## Links

`brain/app/adapters/parsers.py`, `brain/app/services/chunking.py`, `PROJECT-GUIDE.md` §6.
