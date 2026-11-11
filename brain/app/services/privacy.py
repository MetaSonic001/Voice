"""Masking of personal data: applied to caller text before the LLM, traces and history, and to every log line.

One rule set, regular expressions plus two checksums (microseconds per line, no model to load). It targets what a
banking caller can actually say, in digits or spoken as words ("seven one zero seven..." or "saat ek shunya..."):

| Identifier                                         | How it is found                              | Becomes      |
|----------------------------------------------------|----------------------------------------------|--------------|
| secret after OTP / PIN / CVV / password / MPIN     | keyword followed by digits                   | [secret]     |
| card number                                        | 13-19 digits that pass the Luhn check        | [card]       |
| Aadhaar                                            | 12 digits that pass the Verhoeff check       | [aadhaar]    |
| phone number                                       | Indian mobile format (10 digits, 6-9 first)  | [phone]      |
| any other long digit run (9+): account, UTR...     | length only                                  | [number]     |
| 6+ spoken digits in a row (English / Hindi)        | digit words                                  | [number]     |
| PAN, e-mail, UPI ID                                | format                                       | [pan] [email] [upi] |
| account number said in words ("account number 4521 ...") | keyword + digits                       | [number]     |

Amounts, dates, rates and "ending 4521" survive, because answers need them (financial facts are kept, identifiers
are masked). A lone 6-digit code is never pattern-matched here: the verification state decides when a typed code
is a code (services/conversation.py), so a normal 6-digit amount is not wrongly masked.

Regex and checksums are defence in depth, not a guarantee: free speech can carry PII in forms they miss. Raw audio
is not covered by text redaction. NER (e.g. Presidio) would add names and addresses at the cost of a large model per
call, and a new dependency; the bank's own data model is the better place to keep names out of logs.
"""

import re

_DIGIT_WORDS = (r"(?:zero|oh|one|two|three|four|five|six|seven|eight|nine|shunya|ek|do|teen|char|chaar|paanch|panch|"
                r"chhe|chhah|saat|aath|nau|शून्य|एक|दो|तीन|चार|पांच|पाँच|छह|छः|सात|आठ|नौ|\d)")
_LONG_DIGITS = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?\d(?:[\s-]?\d){8,18}(?!\d)")

# Verhoeff tables (Aadhaar check digit)
_D = ((0, 1, 2, 3, 4, 5, 6, 7, 8, 9), (1, 2, 3, 4, 0, 6, 7, 8, 9, 5), (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
      (3, 4, 0, 1, 2, 8, 9, 5, 6, 7), (4, 0, 1, 2, 3, 9, 5, 6, 7, 8), (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
      (6, 5, 9, 8, 7, 1, 0, 4, 3, 2), (7, 6, 5, 9, 8, 2, 1, 0, 4, 3), (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
      (9, 8, 7, 6, 5, 4, 3, 2, 1, 0))
_P = ((0, 1, 2, 3, 4, 5, 6, 7, 8, 9), (1, 5, 7, 6, 2, 8, 3, 0, 9, 4), (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
      (8, 9, 1, 6, 0, 4, 3, 5, 2, 7), (9, 4, 5, 3, 1, 2, 6, 8, 7, 0), (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
      (2, 7, 9, 3, 8, 0, 6, 4, 1, 5), (7, 0, 4, 6, 9, 1, 3, 2, 5, 8))


def luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2:
            n *= 2
            n -= 9 if n > 9 else 0
        total += n
    return total % 10 == 0


def verhoeff_ok(digits: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(digits)):
        c = _D[c][_P[i % 8][int(ch)]]
    return c == 0


def _classify(match: re.Match) -> str:
    digits = re.sub(r"\D", "", match.group(0))
    if 13 <= len(digits) <= 19 and luhn_ok(digits):
        return "[card]"
    if len(digits) == 12 and digits[0] in "23456789" and verhoeff_ok(digits):
        return "[aadhaar]"
    national = digits[2:] if len(digits) == 12 and digits.startswith("91") else digits
    if len(national) == 10 and national[0] in "6789":
        return "[phone]"
    return "[number]"


_RULES = [
    # OTP / PIN / CVV followed by digits, in English or Hindi-ish phrasing
    (re.compile(r"\b(otp|o\.t\.p|pin|cvv|mpin|tpin|password|passcode)\b([^\d]{0,15}?)(\d[\d\s-]{2,9}\d)", re.I), r"\1\2[secret]"),
    (re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"), "[pan]"),                              # PAN
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[email]"),                           # e-mail
    (re.compile(r"\b[\w.-]{2,}@[a-z]{2,}\b", re.I), "[upi]"),                       # UPI ID (name@okaxis)
    # "account number 4521 8890 12" - the context makes even a short run an account number
    (re.compile(r"\b(account|a/c|acct|खाता)\s*(?:number|no\.?|संख्या|नंबर)\s*(?:is|hai|है)?\s*[:#-]?\s*\d[\d\s-]{3,}\d", re.I), r"\1 number [number]"),
    (_LONG_DIGITS, _classify),                                                       # card / Aadhaar / phone / account
    (re.compile(rf"(?<![\wऀ-ॿ]){_DIGIT_WORDS}(?:[\s,-]+{_DIGIT_WORDS}){{5,}}(?![\wऀ-ॿ])", re.I),
     "[number]"),                                                                   # spoken digits
    (re.compile(r"gsk_[A-Za-z0-9]{8,}"), "gsk_***"),                                # API keys (logs)
]


_UUID = re.compile(r"(\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b|\bAU\d{12}\b|\bAU-[A-Z]{2,5}-\d{8}-\d{3,6}\b)", re.I)
# (also left alone: the bank's own reference numbers, "AU" + 12 digits - a reference for a request, not personal data)


def redact(text: str) -> str:
    """Ids (UUIDs) are left alone so log lines and traces stay correlatable; everything else goes through the rules."""
    parts = _UUID.split(text)
    for i in range(0, len(parts), 2):  # odd parts are the UUIDs
        for pattern, repl in _RULES:
            parts[i] = pattern.sub(repl, parts[i])
    return "".join(parts)


_CODE = re.compile(r"\s*(\d[\s-]?){6}\s*")


def is_code_entry(text: str) -> bool:
    """True when the whole input is exactly six digits (a keypad code typed or sent as text) and nothing else."""
    digits = re.sub(r"[\s-]", "", text)
    return bool(_CODE.fullmatch(text)) and len(digits) == 6 and digits.isdigit()
