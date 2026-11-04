"""Document parsers. Each turns a file into ordered Blocks (headings, paragraphs, list items, tables).

- DOCX: native OOXML reader (keeps bold headings, lists and tables)
- PDF: pdfplumber (text PDFs; tables keep rows, cells and merged headers)
- XLSX / CSV: direct table reader (optionally only some sheets)
- MD / TXT: plain text with Markdown headings and bullets
"""

import csv
import logging
import re
import zipfile
from datetime import date, datetime
from pathlib import Path
from xml.etree import ElementTree as ET

from app.domain import Block, ParsedDoc

logger = logging.getLogger(__name__)
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

SUPPORTED_TYPES = {"pdf", "docx", "xlsx", "csv", "md", "txt"}


def heading_level(text: str, product_prefixes: tuple[str, ...]) -> int:
    return 1 if product_prefixes and text.startswith(product_prefixes) else 2


def looks_like_heading(text: str, bold: bool, is_list: bool) -> bool:
    words = text.split()
    if is_list or not bold or not words or len(text) > 80 or len(words) > 12:
        return False
    end = text.rstrip()[-1]
    return end not in ".,;" and not (end == ":" and len(words) > 4)


class DocxParser:
    def parse(self, path: Path, options: dict) -> ParsedDoc:
        prefixes = tuple(options.get("product_prefixes") or ())
        root = ET.fromstring(zipfile.ZipFile(path).read("word/document.xml"))
        blocks: list[Block] = []
        stats = {"paragraphs": 0, "headings": 0, "list_items": 0, "tables": 0}
        for el in root.find(W + "body"):
            if el.tag == W + "p":
                text = _para_text(el)
                if not text:
                    continue
                is_list = el.find(f"{W}pPr/{W}numPr") is not None
                style = el.find(f"{W}pPr/{W}pStyle")
                style = style.get(W + "val", "") if style is not None else ""
                if style.lower().startswith(("heading", "title")):
                    level = 1 if style.lower() in ("title", "heading1") else heading_level(text, prefixes)
                    blocks.append(Block("heading", text, level))
                    stats["headings"] += 1
                elif looks_like_heading(text, _mostly_bold(el), is_list):
                    blocks.append(Block("heading", text, heading_level(text, prefixes)))
                    stats["headings"] += 1
                elif is_list:
                    blocks.append(Block("list", text))
                    stats["list_items"] += 1
                else:
                    blocks.append(Block("para", text))
                    stats["paragraphs"] += 1
            elif el.tag == W + "tbl":
                rows = []
                for tr in el.iter(W + "tr"):
                    cells = [" / ".join(t for p in tc.iter(W + "p") if (t := _para_text(p))) for tc in tr.iter(W + "tc")]
                    if any(cells):
                        rows.append(cells)
                if rows:
                    blocks.append(Block("table", rows=rows))
                    stats["tables"] += 1
        return ParsedDoc(blocks, {"parser": "docx-native", **stats})


def _para_text(p) -> str:
    return re.sub(r"\s+", " ", "".join(t.text or "" for t in p.iter(W + "t"))).strip()


def _mostly_bold(p) -> bool:
    bold = total = 0
    for r in p.iter(W + "r"):
        n = sum(len(t.text or "") for t in r.iter(W + "t"))
        b = r.find(f"{W}rPr/{W}b")
        is_bold = b is not None and b.get(W + "val", "true") not in ("0", "false")
        total += n
        bold += n if is_bold else 0
    return total > 0 and bold / total >= 0.6


class PdfParser:
    """Text PDFs via pdfplumber (reads the PDF's own text and ruling lines; no ML models).

    Tables keep their rows and cells: a merged cell comes back as None, an empty cell as "". Text outside
    tables becomes headings (short bold lines) and paragraphs. Scanned PDFs have no text layer and are rejected.
    """

    def parse(self, path: Path, options: dict) -> ParsedDoc:
        import pdfplumber

        prefixes = tuple(options.get("product_prefixes") or ())
        blocks: list[Block] = []
        stats = {"pages": 0, "paragraphs": 0, "headings": 0, "tables": 0}
        with pdfplumber.open(path) as pdf:
            stats["pages"] = len(pdf.pages)
            for page in pdf.pages:
                tables = page.find_tables()
                boxes = [t.bbox for t in tables]
                items = [(t.bbox[1], t) for t in tables]
                items += [(line["top"], line) for line in page.filter(lambda o: not _inside(o, boxes)).extract_text_lines()]
                prev_bottom = None
                for _, item in sorted(items, key=lambda x: x[0]):
                    if not isinstance(item, dict):  # a table
                        rows = [[None if c is None else re.sub(r"\s+", " ", c).strip() for c in row] for row in item.extract()]
                        rows = [r for r in rows if any(r)]
                        if rows:
                            blocks.append(Block("table", rows=rows))
                            stats["tables"] += 1
                        prev_bottom = None
                        continue
                    text = re.sub(r"\s+", " ", item["text"]).strip()
                    if not text or re.fullmatch(r"(page\s*)?\d+(\s*(of|/)\s*\d+)?", text, re.I):  # page numbers
                        continue
                    chars = item["chars"]
                    bold = sum("bold" in c["fontname"].lower() for c in chars) >= 0.6 * len(chars)
                    size = chars[0]["size"]
                    if looks_like_heading(text, bold, False):
                        blocks.append(Block("heading", text, heading_level(text, prefixes)))
                        stats["headings"] += 1
                    elif blocks and blocks[-1].kind == "para" and prev_bottom is not None                             and item["top"] - prev_bottom < size * 0.8:  # next line of the same paragraph
                        blocks[-1].text += " " + text
                    else:
                        blocks.append(Block("para", text))
                        stats["paragraphs"] += 1
                    prev_bottom = item["bottom"]
        if not blocks:
            raise ValueError("no text found in the PDF (scanned image?) - only text PDFs are supported")
        return ParsedDoc(blocks, {"parser": "pdfplumber", **stats})


def _inside(obj: dict, boxes: list) -> bool:
    x, y = (obj.get("x0", 0) + obj.get("x1", 0)) / 2, (obj.get("top", 0) + obj.get("bottom", 0)) / 2
    return any(x0 <= x <= x1 and top <= y <= bottom for x0, top, x1, bottom in boxes)


def _cell(v) -> str:
    if v is None or (isinstance(v, float) and v != v):
        return ""
    if isinstance(v, (datetime, date)):
        return v.strftime("%d-%b-%Y")
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return re.sub(r"\s+", " ", str(v)).strip()


class TableFileParser:
    """XLSX (all sheets, or only `options.sheets`) and CSV. First non-empty row is the header."""

    def parse(self, path: Path, options: dict) -> ParsedDoc:
        blocks: list[Block] = []
        if path.suffix.lower() == ".csv":
            with open(path, newline="", encoding="utf-8-sig") as f:
                rows = [[_cell(c) for c in r] for r in csv.reader(f)]
            sheets = {path.stem: rows}
        else:
            from openpyxl import load_workbook

            wanted = {s.strip().lower() for s in options.get("sheets") or []}
            wb = load_workbook(path, read_only=True, data_only=True)
            sheets = {
                ws.title: [[_cell(c) for c in r] for r in ws.iter_rows(values_only=True)]
                for ws in wb.worksheets
                if not wanted or ws.title.strip().lower() in wanted
            }
            wb.close()
        report = {"parser": "table", "sheets": {}}
        for name, rows in sheets.items():
            rows = [r for r in rows if any(c for c in r)]
            if not rows:
                continue
            width = max(i + 1 for r in rows for i, c in enumerate(r) if c)
            rows = [r[:width] + [""] * (width - len(r[:width])) for r in rows]
            blocks.append(Block("heading", name, 1))
            blocks.append(Block("table", rows=rows))
            report["sheets"][name] = len(rows) - 1
        report["tables"] = len(report["sheets"])
        return ParsedDoc(blocks, report)


class TextParser:
    def parse(self, path: Path, options: dict) -> ParsedDoc:
        blocks: list[Block] = []
        para: list[str] = []

        def flush():
            if para:
                blocks.append(Block("para", " ".join(para)))
                para.clear()

        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line:
                flush()
            elif m := re.match(r"^(#{1,6})\s+(.*)", line):
                flush()
                blocks.append(Block("heading", m.group(2).strip(), 1 if len(m.group(1)) == 1 else 2))
            elif m := re.match(r"^[-*•]\s+(.*)", line):
                flush()
                blocks.append(Block("list", m.group(1).strip()))
            else:
                para.append(line)
        flush()
        return ParsedDoc(blocks, {"parser": "text", "blocks": len(blocks)})


class ParserRouter:
    def __init__(self):
        table, text = TableFileParser(), TextParser()
        self._by_type = {"docx": DocxParser(), "pdf": PdfParser(), "xlsx": table, "csv": table, "md": text, "txt": text}

    def parse(self, path: Path, file_type: str, options: dict) -> ParsedDoc:
        return self._by_type[file_type].parse(path, options)
