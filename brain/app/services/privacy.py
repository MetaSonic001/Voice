"""Masking of personal data: applied to caller text before the LLM, traces and history, and to every log line.

One rule set, regular expressions only (microseconds per line, no model to load). It targets what a banking
caller can actually say, in digits or spoken as words (speech-to-text writes "seven one zero seven..." or
"saat ek shunya..." as often as "7107..."):
- secrets after a keyword (OTP / PIN / CVV / password / MPIN ...)                       -> [secret]
- long digit runs, with or without spaces/dashes: account, card, phone, Aadhaar (9+)    -> [number]
- 6+ spoken digits in a row (English, Hindi in Latin or Devanagari)                     -> [number]
- PAN, e-mail, UPI ID                                                                    -> [pan] [email] [upi]
Short numbers (amounts, last 4 digits, dates, rates) stay, because answers need them.
NER (e.g. Microsoft Presidio) would add names/addresses at the cost of a large model per call; the bank's own
data model is the better place to keep names out of logs, so it is not used here.
"""

import re

_DIGIT_WORDS = (r"(?:zero|oh|one|two|three|four|five|six|seven|eight|nine|shunya|ek|do|teen|char|chaar|paanch|panch|"
                r"chhe|chhah|saat|aath|nau|शून्य|एक|दो|तीन|चार|पांच|पाँच|छह|छः|सात|आठ|नौ|\d)")
_RULES = [
    # OTP / PIN / CVV followed by digits, in English or Hindi-ish phrasing
    (re.compile(r"\b(otp|o\.t\.p|pin|cvv|mpin|tpin|password|passcode)\b([^\d]{0,15}?)(\d[\d\s-]{2,9}\d)", re.I), r"\1\2[secret]"),
    (re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"), "[pan]"),                              # PAN
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[email]"),                           # e-mail
    (re.compile(r"\b[\w.-]{2,}@[a-z]{2,}\b", re.I), "[upi]"),                       # UPI ID (name@okaxis)
    (re.compile(r"(?<!\d)\d(?:[\s-]?\d){8,18}(?!\d)"), "[number]"),                 # account / card / phone / Aadhaar
    (re.compile(rf"(?<![\wऀ-ॿ]){_DIGIT_WORDS}(?:[\s,-]+{_DIGIT_WORDS}){{5,}}(?![\wऀ-ॿ])", re.I),
     "[number]"),                                                                   # spoken digits
    (re.compile(r"gsk_[A-Za-z0-9]{8,}"), "gsk_***"),                                # API keys (logs)
]


def redact(text: str) -> str:
    for pattern, repl in _RULES:
        text = pattern.sub(repl, text)
    return text
