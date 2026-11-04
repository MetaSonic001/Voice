"""Structure-aware chunking.

- Text: a "unit" is one section (heading + its paragraphs/list items). Consecutive units of the same
  product are packed into a chunk up to `target_words`; a unit larger than `max_words` is split.
- Tables: every row is rendered with its own context ("Section - Header: value | Header: value"), also for
  rate sheets with stacked/merged headers, and rows are grouped into chunks up to `target_words`.
- Every chunk has a section path "Document › Product › Section(s)" used for search and citations.
- Exact duplicate chunks inside one document (e.g. a section repeated in the source) are dropped.
"""

import hashlib
import re
from dataclasses import dataclass, field

from app.domain import Block, ChunkDraft

SEP = " › "


@dataclass
class _Unit:
    product: str
    section: str
    lines: list[str] = field(default_factory=list)

    @property
    def words(self) -> int:
        return sum(len(line.split()) for line in self.lines)


def chunk_blocks(blocks: list[Block], doc_title: str, target_words: int = 180, max_words: int = 260) -> list[ChunkDraft]:
    chunks: list[ChunkDraft] = []
    units: list[_Unit] = []
    product, section = doc_title, ""

    def current() -> _Unit:
        if not units or units[-1].product != product or units[-1].section != section:
            units.append(_Unit(product, section))
        return units[-1]

    for block in blocks:
        if block.kind == "heading":
            if block.level == 1:
                product, section = block.text, ""
            else:
                section = block.text
        elif block.kind == "para":
            current().lines.append(block.text)
        elif block.kind == "list":
            current().lines.append(f"- {block.text}")
        elif block.kind == "table":
            _flush_units(units, chunks, doc_title, target_words, max_words)
            units.clear()
            chunks.extend(_table_chunks(block.rows, _path(doc_title, product, section), target_words))
    _flush_units(units, chunks, doc_title, target_words, max_words)
    return _dedupe(chunks)


def _path(doc_title: str, product: str, *sections: str) -> str:
    parts = [doc_title] + ([product] if product and product != doc_title else [])
    secs = [s for s in sections if s]
    if secs:
        parts.append("; ".join(dict.fromkeys(secs)))
    return SEP.join(parts)


def _render(unit: _Unit) -> list[str]:
    return ([f"## {unit.section}"] if unit.section else []) + unit.lines


def _flush_units(units: list[_Unit], out: list[ChunkDraft], doc_title: str, target: int, max_words: int) -> None:
    group: list[_Unit] = []

    def emit():
        if group:
            lines = [line for u in group for line in _render(u)]
            if any(u.lines for u in group):
                out.append(ChunkDraft("text", _path(doc_title, group[0].product, *(u.section for u in group)), "\n".join(lines)))
            group.clear()

    for unit in units:
        if not unit.lines:
            continue
        if unit.words > max_words:  # oversized section: split by lines
            emit()
            part = _Unit(unit.product, unit.section)
            for line in unit.lines:
                if part.lines and part.words + len(line.split()) > target:
                    out.append(ChunkDraft("text", _path(doc_title, part.product, part.section), "\n".join(_render(part))))
                    part = _Unit(unit.product, unit.section)
                part.lines.append(line)
            if part.lines:
                out.append(ChunkDraft("text", _path(doc_title, part.product, part.section), "\n".join(_render(part))))
            continue
        if group and (group[0].product != unit.product or sum(u.words for u in group) + unit.words > target):
            emit()
        group.append(unit)
    emit()


_DATA_WORDS = {"-", "–", "nil", "na", "n/a", "free", "lifetime free", "lifetime free card", "yes", "no", "zero"}
_NUMBERING = re.compile(r"\(?(\d{1,2}|[A-Za-z]|[ivxIVX]{2,4})[.)]?")  # "1", "A.", "a.", "(b)", "iii."
_MONEY = re.compile(r"%|₹|\brs\.?\s*\d|\binr\s*\d")
_ENDS_WITH_NUMBER = re.compile(r"\d[\d,]*(\.\d+)?\s*(%|p\.?\s?a\.?|aqb|amb|/-)?\s*\)?$")
_BAND = re.compile(r"[<>≤≥]|less than|above|between|\bupto\b|\bup to\b")


def _is_value(cell: str) -> bool:
    """A value: a rate or amount ("7.10%", "– 4,999", "25,00,000 AQB"), a data word ("Nil", "-"), or text stating
    one ("15% of Total Amount Due..."). Labels ("1 year & above but < 2 years", "For 5 years (Maximum)") are not."""
    v = cell.strip().lower()
    if "%" not in v and _BAND.search(v):  # "For Amount < INR 3 Cr", "Upto 2 lakh": a range label, not a value
        return False
    if v in _DATA_WORDS or _MONEY.search(v) or _ENDS_WITH_NUMBER.search(v):
        return True
    chars = v.replace(" ", "")
    return bool(chars) and sum(ch.isdigit() for ch in chars) >= 0.4 * len(chars)


def _table_chunks(rows: list[list[str | None]], path: str, target: int) -> list[ChunkDraft]:
    """Render table rows so every line carries its own context ("Header: value | Header: value").

    Normal tables (first row = distinct column names) map each row against that header. Other tables (bank rate
    sheets: numbering columns, section rows, column headers stacked and merged over several rows) are read top to
    bottom: a row with one text cell is a section, a row of labels is a header row (a merged cell, None, takes the
    label on its left), and a row with values becomes "Row label: … | Column label: value" under a context line
    ("Section › Sub-section › shared header") written once per chunk. Rows that never meet a value row are kept
    as plain lines, so no text is dropped.
    """
    width = max((len(r) for r in rows), default=0)
    rows = [list(r) + [""] * (width - len(r)) for r in rows]
    first = [(c or "").strip() for c in rows[0]] if rows else []
    if width > 1 and len(rows) > 1 and all(first) and len(set(first)) == len(first) and not any(map(_is_value, first)):
        rendered = [("", " | ".join(f"{h}: {v}" for h, v in zip(first, row, strict=True) if v and v != h)) for row in rows[1:]]
    else:
        rendered = _render_stacked(rows)
    # Rows sharing a context go together; the context is written once, at the top of each chunk.
    chunks, buf, words, context = [], [], 0, None
    for prefix, line in rendered:
        if not line:
            continue
        n = len(line.split())
        if buf and (prefix != context or words + n > target):
            chunks.append(ChunkDraft("table", path, "\n".join(([context] if context else []) + buf)))
            buf, words = [], 0
        context = prefix
        buf.append(line)
        words += n
    if buf:
        chunks.append(ChunkDraft("table", path, "\n".join(([context] if context else []) + buf)))
    return chunks


def _render_stacked(rows: list[list[str | None]]) -> list[tuple[str, str]]:
    """(context, line) pairs: the context ("Term Deposits › Domestic … › Rate of Interest") is shared by many rows."""
    out: list[tuple[str, str]] = []
    sections: dict[int, str] = {}  # column -> section title (deeper columns = sub-sections)
    headers: list[list[str]] = []  # header rows of the current block
    used: set[int] = set()  # header rows that labelled at least one value row
    unused: set[str] = set()  # section titles no value row has been rendered under yet
    after_values = False

    def plain(texts) -> None:  # text kept as-is, so nothing is dropped
        out.extend(("", x) for x in texts)

    for raw in rows:
        cells = list(raw)
        if any(c and not _NUMBERING.fullmatch(c) for c in cells):  # (a row of only "A | B | C" is a header)
            for i, c in enumerate(cells):  # leading numbering cells ("2", "A.", "a.") carry no meaning
                if c and _NUMBERING.fullmatch(c):
                    cells[i] = ""
                elif c:
                    break
        filled = [(i, c) for i, c in enumerate(cells) if c]
        if not filled:
            continue
        label_col, label = filled[0]
        values = [(i, c) for i, c in filled[1:] if _is_value(c)]
        if values and _is_value(label):  # no label cell: the first cell is a value too ("< USD 5,00,000", "5.30%")
            values = filled
        if values:
            out.append(_value_line(sections, headers, cells, label_col, label, values, filled))
            used.update(range(len(headers)))
            unused.clear()
            after_values = True
        elif len(filled) == 1 and len(label) <= 80:  # a section title for the rows below
            if after_values:  # a new block starts
                plain(_leftover(headers, used))
                headers, used, after_values = [], set(), False
            for col in [c for c in sections if c >= label_col]:
                if sections[col] in unused:  # replaced before anything was listed under it: keep it as text
                    plain([sections[col]])
                del sections[col]
            sections[label_col] = label
            unused.add(label)
        else:  # a row of labels; merged cells (None) take the label on their left
            if after_values:
                plain(_leftover(headers, used))
                headers, used, after_values = [], set(), False
            row, last = [], ""
            for c in cells:
                last = c if c else (last if c is None and last else "")
                row.append(last)
            headers.append(row)
    plain(_leftover(headers, used))
    plain(s for s in sections.values() if s in unused)
    return out


def _value_line(sections: dict[int, str], headers: list[list[str]], cells: list[str | None], label_col: int,
                label: str, values: list[tuple[int, str]], filled: list[tuple[int, str]]) -> tuple[str, str]:
    # Header text between the label column and the first value is a title for the row ("NRE (All Maturities)").
    data_cols = {label_col} | {i for i, _ in values}
    titles = [t for h in headers for j, t in enumerate(h) if t and label_col < j < values[0][0] and j not in data_cols
              and t not in sections.values() and not any(h[k] == t for k in data_cols if k < len(h))]
    skip = set(sections.values()) | set(titles)

    def levels(i: int) -> list[str]:
        """Header levels over column i, top to bottom. A merged value (None to its right) covers those columns too."""
        end = i + 1
        while end < len(cells) and cells[end] is None:
            end += 1
        per_col = [list(dict.fromkeys(h[j] for h in headers if j < len(h) and h[j] and h[j] not in skip))
                   for j in range(i, end)]
        if len(per_col) == 1:
            return per_col[0]
        shared = [x for x in per_col[0] if all(x in p for p in per_col)]
        own = " & ".join(dict.fromkeys(" / ".join(x for x in p if x not in shared) for p in per_col if p))
        return shared + ([own] if own else [])

    shown = [(i, v) for i, v in values if v not in ("-", "–")]
    value_levels = [levels(i) for i, _ in shown]
    # Levels every value column shares ("Rate of Interest", "Interest Rate") move to the context, once.
    common = [x for x in value_levels[0] if all(x in lv for lv in value_levels)] if len(value_levels) > 1 else []
    has_label = values[0][0] != label_col
    label_levels = levels(label_col) if has_label else []
    context = [sections[c] for c in sorted(sections)] + titles + label_levels[:-1] + common
    parts = []
    if has_label:
        parts.append(f"{label_levels[-1]}: {label}" if label_levels else label)
        parts += [c for i, c in filled[1:] if (i, c) not in values]  # other text cells (e.g. a variant name)
    for (_, v), lv in zip(shown, value_levels, strict=True):
        head = " / ".join(x for x in lv if x not in common)
        parts.append(f"{head}: {v}" if head else v)
    return " › ".join(dict.fromkeys(context)), " | ".join(dict.fromkeys(parts))


def _leftover(headers: list[list[str]], used: set[int]) -> list[str]:
    """Header rows that never labelled a value row are content (e.g. notes): keep them as plain lines."""
    return [" | ".join(dict.fromkeys(c for c in h if c)) for k, h in enumerate(headers) if k not in used]


def content_hash(chunk: ChunkDraft) -> str:
    # Ignore the document title so the same section repeated within one document is caught.
    key = chunk.section_path.split(SEP, 1)[-1] + "\n" + chunk.text
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _dedupe(chunks: list[ChunkDraft]) -> list[ChunkDraft]:
    seen, out = set(), []
    for chunk in chunks:
        h = content_hash(chunk)
        if h not in seen:
            seen.add(h)
            out.append(chunk)
    return out
