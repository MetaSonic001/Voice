"""Number handling: the figure check (no number may appear in an answer unless the sources contain it)
and speech-ready text (Indian number words).

Normalisation makes "1,00,000", "100000", "1 lakh" and "Rs 1L" compare equal, and "7.10%" equal "7.1".
"""

import re

from app.services.pronunciation import say

_DEV_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_NUM = re.compile(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)(?:\s*(lakh|lakhs|lac|lacs|l|crore|crores|cr|k|thousand|hazaar|hazar|लाख|करोड़|करोड|हज़ार|हजार))?(?![\w])", re.I)
_MULT = {"lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "lacs": 1e5, "l": 1e5, "crore": 1e7, "crores": 1e7, "cr": 1e7,
         "k": 1e3, "thousand": 1e3, "hazaar": 1e3, "hazar": 1e3, "लाख": 1e5, "करोड़": 1e7, "करोड": 1e7, "हज़ार": 1e3, "हजार": 1e3}
_UNIT_AFTER = re.compile(r"\s*(months?|years?|yrs?|days?)", re.I)


def _canon(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _mentions(text: str) -> list[tuple[set[str], str]]:
    """Each number in the text as (equivalent canonical forms, following unit word)."""
    text = text.translate(_DEV_DIGITS)
    out = []
    for m in _NUM.finditer(text):
        try:
            base = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        forms = {_canon(base)}
        if m.group(2):
            forms.add(_canon(base * _MULT[m.group(2).lower()]))
        unit = _UNIT_AFTER.match(text, m.end())
        out.append((forms, unit.group(1).lower() if unit else ""))
    return out


def allowed_numbers(*texts: str) -> set[str]:
    """Every number form present in the source texts, including month<->year equivalents."""
    allowed: set[str] = set()
    for text in texts:
        for forms, unit in _mentions(text):
            allowed |= forms
            for f in forms:
                v = float(f)
                if unit.startswith("month") and v % 12 == 0:
                    allowed.add(_canon(v / 12))
                elif unit.startswith(("year", "yr")):
                    allowed.add(_canon(v * 12))
    return allowed


def unsupported_numbers(answer: str, allowed: set[str]) -> list[str]:
    """Numbers in the answer with no equivalent in `allowed`. Citation markers like [2] are ignored."""
    answer = re.sub(r"\[\d+\]", " ", answer)
    bad = []
    for forms, _ in _mentions(answer):
        if not forms & allowed:
            bad.append(sorted(forms)[0])
    return bad


# ---------- speech text ----------

_ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def _words_below_1000(n: int) -> str:
    parts = []
    if n >= 100:
        parts.append(f"{_ONES[n // 100]} hundred")
        n %= 100
    if n >= 20:
        parts.append(_TENS[n // 10] + (f" {_ONES[n % 10]}" if n % 10 else ""))
    elif n or not parts:
        parts.append(_ONES[n])
    return " ".join(parts)


def indian_words(n: int) -> str:
    """123456 -> 'one lakh twenty three thousand four hundred fifty six' (Indian grouping)."""
    if n == 0:
        return "zero"
    parts = []
    for size, name in ((10**7, "crore"), (10**5, "lakh"), (10**3, "thousand")):
        if n >= size:
            parts.append(f"{indian_words(n // size) if n // size >= 1000 else _words_below_1000(n // size)} {name}")
            n %= size
    if n:
        parts.append(_words_below_1000(n))
    return " ".join(parts)


# ───────── voice-safe text ─────────
# Answers are spoken: markdown and chat formatting must never reach the screen or the voice ("star star", "dash").
# Amounts, decimals, percentages, dates, "ending 4521" and citation markers are left alone.
_MD_LINE_START = re.compile(r"^\s*(?:[-*•▪◦·]+|#{1,6}|>|\d+[.)](?=\s))\s*", re.M)
_MD_EMPHASIS = re.compile(r"(\*{1,3}|_{2,3}|`+)(?=\S)(.+?)(?<=\S)\1")
_MD_LEFTOVER = re.compile(r"\*+|`+|#{2,}|(?<!\w)_{2,}|\s[-–—•]\s(?=[A-Z\u0900-\u097F*])")
_EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF]")


def clean_markup(text: str) -> str:
    """Plain spoken text: no bullets, headings, bold / italic marks, code ticks or emojis."""
    text = _MD_EMPHASIS.sub(r"\2", text)
    text = _MD_LINE_START.sub("", text)
    text = re.sub(r"([:.;])\s+[-–•*]\s+", r"\1 ", text)  # an inline list after a colon or a full stop
    text = _MD_LEFTOVER.sub(" ", text)
    text = _EMOJI.sub("", text)
    text = re.sub(r"\s+([,.;:?!])", r"\1", text)
    return re.sub(r"\s+", " ", text).strip(" -–—:")


def speech_text(answer: str, language: str) -> str:
    """Text ready for TTS: no citation markers, currency spoken, English numbers as Indian words.

    Hindi/Hinglish keep digits (Indian TTS voices read them natively in Hindi).
    """
    text = say(clean_markup(re.sub(r"\s*\[\d+\]", "", answer)), language)
    text = re.sub(r"\s*;\s+", ". ", text)  # a semicolon joins two ideas: spoken, a full stop gives the caller a pause
    text = re.sub(r"\b(Rs\.?|INR)\s*", "₹", text)
    if language != "en":
        return re.sub(r"\s+", " ", text).strip()

    def money(m: re.Match) -> str:
        value = m.group(1).replace(",", "")
        if "." in value:
            return f"{value} rupees"
        return f"{indian_words(int(value))} rupees"

    text = re.sub(r"₹\s*(\d[\d,]*(?:\.\d+)?)(?!\s*(?:lakh|crore|k)\b)", money, text)
    text = text.replace("₹", "rupees ")
    text = re.sub(r"(\d+(?:\.\d+)?)\s*%", r"\1 percent", text)
    text = re.sub(r"\bp\.a\.", "per annum", text)
    return re.sub(r"\s+", " ", text).strip()


# ───────── figures written in words ─────────
# Answers must state rates and amounts in digits, so the check above can verify them against the sources.
# A unit word (percent, lakh, crore, rupees, प्रतिशत, ...) must therefore follow a digit, and "<number word> point
# <number word>" is never allowed. This catches any spelling of the number ("saat point paanch", "pachhattar
# hazaar", "सात दशमलव छह") without having to list every Hindi number.
_MONEY_UNIT = (r"percent|per\s+cent|pratishat|lakhs?|lacs?|crores?|hazaa?r|thousand|rupees?|rupaye|rupaiye|"
         r"प्रतिशत|लाख|करोड़|करोड|हज़ार|हजार|रुपये|रुपए")
_MONEY_UNIT_AFTER = re.compile(rf"(?:^|(\S+)\s+)({_MONEY_UNIT})(?![\wऀ-ॿ])", re.I)
_SMALL = (r"zero|one|two|three|four|five|six|seven|eight|nine|ek|do|teen|char|chaar|paanch|panch|chhe|chhah|saat|"
          r"aath|nau|शून्य|एक|दो|तीन|चार|पांच|पाँच|छह|सात|आठ|नौ")
_WORD_POINT = re.compile(rf"(?<![\wऀ-ॿ])({_SMALL})\s+(point|dashamlav|दशमलव)\s+({_SMALL})(?![\wऀ-ॿ])", re.I)


def spelled_figures(sentence: str) -> list[str]:
    """Rates/amounts written in words in an answer sentence (each one fails the figure check)."""
    found = [m.group(0) for m in _WORD_POINT.finditer(sentence)]
    for m in _MONEY_UNIT_AFTER.finditer(sentence):
        before = m.group(1) or ""
        if not re.search(r"\d", before) and before not in ("₹", "Rs", "Rs."):
            found.append(m.group(0).strip())
    return found
