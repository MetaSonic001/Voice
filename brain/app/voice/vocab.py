"""Speech-recognition vocabulary hints (Whisper `prompt`). A hint biases recognition toward these words, so it is kept
only if the A/B test on our own audio (evals/stt_ab.py) shows fewer errors AND no new wrong substitutions.
"""

BASE = ("AU Small Finance Bank. FD, RD, Zenith+, Ananta, Laksya, BSBDA, Royale, Vikaas, Kisan, Swadesh, "
        "SMARTLoan, Xpress Loan, senior citizen, minimum balance, interest rate.")
EXTENDED = (BASE + " UPI, NEFT, IMPS, RTGS, EMI, KYC, OTP, ATM, debit card, credit card, Digital, Value, Platinum, Kids, Women, "
            "foreclosure, premature withdrawal, fixed deposit, savings account, AU 0101 app, block my card, balance, transaction.")
HINTS = {"base": BASE, "extended": EXTENDED, "none": ""}


def hint_for(name: str) -> str:
    return HINTS.get(name, BASE)


def terms_for(name: str) -> list[str]:
    """The same vocabulary as a list of terms (Sarvam saaras:v4 `keyterms`, at most 50)."""
    return [t.strip() for t in hint_for(name).replace(".", ",").split(",") if t.strip()]
