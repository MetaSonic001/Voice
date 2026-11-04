"""Incremental sentence splitter for streamed LLM text.

Keeps decimals (7.10%), common abbreviations (p.a., Rs., e.g.) and citation markers ("... 7.1% [2].") intact,
so each emitted sentence can be checked and spoken on its own.
"""

import re

# A sentence ends at punctuation (+ any citation markers) only once the NEXT word has started and isn't a marker,
# so "...10,000. [1]" is never split from its citation, even when the marker arrives in a later stream chunk.
_END = re.compile(r"[.!?।]+(?:\s*\[\d+\])*(?=\s+[^\s\[])|\n")
_ABBREV = {"p.a", "rs", "e.g", "i.e", "approx", "no", "vs", "mr", "mrs", "dr", "st", "a.m", "p.m", "etc", "inr"}


class SentenceSplitter:
    def __init__(self):
        self._buf = ""

    def feed(self, delta: str) -> list[str]:
        self._buf += delta
        out, start = [], 0
        for m in _END.finditer(self._buf):
            if m.group() != "\n":
                before = self._buf[start:m.start()].split()
                last = before[-1].lower().rstrip(".") if before else ""
                if last in _ABBREV:
                    continue
            sentence = self._buf[start:m.end()].strip()
            start = m.end()
            if sentence:
                out.append(sentence)
        self._buf = self._buf[start:]
        return out

    def flush(self) -> list[str]:
        rest, self._buf = self._buf.strip(), ""
        return [rest] if rest else []
