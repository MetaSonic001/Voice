"""What else a caller's sentence says, read in code (any language, any length).

Used where the agent asked "which account?": the reply answers it when it names an account and nothing else. Until
20 Nov 2026 this was decided by length (6 words or fewer) and by the LLM's topic matching the waiting question. Call
a6545287 failed both ways: "मुझे दूसरे account के बारे में बताइए" has 7 words, and the LLM called it an account-status
question, so the caller's "last 5 transactions" was lost. Now the content decides: the waiting question resumes unless
the reply also names another data topic, a problem, fraud or a request for a person.

(On 20 Nov this check also replaced the other word-count limits; that caused two failures on 21 Nov and was reverted on
23 Nov. Only the account pick uses it.)
"""

import re

from app.services.guard import risk_flags

# Bank-data topics, by their own words only (never a pronoun or "account", which every "which account?" answer has).
TOPIC_WORDS = {
    "balance": re.compile(r"\b(balance|bal|kitne paise|kitna paisa)\b|बैलेंस|कितने पैसे|कितना पैसा", re.I),
    "transactions": re.compile(r"\b(transactions?|txns?|statement|passbook|len ?den)\b|ट्रांज़ैक्शन|ट्रांजैक्शन|लेनदेन|"
                               r"लेन-देन|स्टेटमेंट", re.I),
    "fd": re.compile(r"\b(fds?|fixed deposits?|recurring deposits?|rd)\b|एफडी|एफ़डी|फिक्स्ड डिपॉज़िट|फिक्स्ड डिपॉजिट", re.I),
    "loan": re.compile(r"\b(loans?|emis?)\b|लोन|ईएमआई", re.I),
    "credit_card": re.compile(r"\bcredit cards?\b|क्रेडिट कार्ड", re.I),
    "debit_card": re.compile(r"\bdebit cards?\b|डेबिट कार्ड", re.I),
    "charges": re.compile(r"\b(charges?|fees?|penalty|penalties)\b|चार्ज|शुल्क|फीस", re.I),
}
# A general product question ("what's the interest rate on Zenith+?", "how do I open an account?").
_GENERAL = re.compile(r"\b(interest|rates?|markup|forex|eligib\w*|documents?|kyc|ifsc|branch|apply|open(ing)? (an?|new) "
                      r"account|byaaj)\b|ब्याज|दर|ब्रांच|खाता खोल", re.I)
# A problem reported: a complaint, a dispute, money gone without the service.
_COMPLAINT = re.compile(r"\b(complaint|complain|shikayat|dispute|scam|cheat\w*|no cash|cash (didn'?t|did not|never) come|"
                        r"not dispensed|didn'?t dispense|not received|didn'?t (get|receive)|nahi (mila|nikla|aaya)|"
                        r"twice|double (charge|debit)|do baar)\b|शिकायत|नहीं (मिला|निकला|आया)|दो बार", re.I)
# "connect me to an agent", "kisi se baat karao", "मुझे किसी से बात करनी है".
ASK_PERSON = re.compile(r"\b(connect|transfer|put me through|talk to|speak to|speak with)\b.{0,40}\b(agent|person|human|"
                        r"someone|executive|team|manager|rm|relationship manager|officer|representative|colleague)\b|"
                        r"\b(agent|insaan|kisi) se baat\b|से बात (करा|कर)", re.I)


def topics_named(text: str) -> set[str]:
    """The bank-data topics a sentence names by their own words."""
    return {name for name, pattern in TOPIC_WORDS.items() if pattern.search(text or "")}


def new_request(text: str, keep=(), general: bool = True) -> bool:
    """True when the sentence asks or reports something besides answering the question the agent asked.
    `keep`: topics that belong to that question (answering "which account?" for transactions may say "transactions").
    `general=False` where a product word is a normal answer (an account name)."""
    text = text or ""
    return bool(topics_named(text) - set(keep)) or bool(_COMPLAINT.search(text)) or bool(ASK_PERSON.search(text)) \
        or bool(risk_flags(text)) or (general and bool(_GENERAL.search(text)))
