from app.services.numbers import (
    allowed_numbers,
    indian_words,
    speech_text,
    unsupported_numbers,
)


def test_same_number_in_different_formats_is_allowed():
    allowed = allowed_numbers("Minimum balance Rs 1,00,000 AMB; rate 7.10% p.a.")
    assert unsupported_numbers("You need ₹1 lakh [1] and earn 7.1% [1].", allowed) == []
    assert unsupported_numbers("You need 100000 rupees [1].", allowed) == []


def test_invented_number_is_flagged():
    allowed = allowed_numbers("Senior citizen rate: 7.60%")
    assert unsupported_numbers("Seniors get 7.75% [1].", allowed) == ["7.75"]


def test_citation_markers_are_not_numbers():
    assert unsupported_numbers("Yes, it is available [3].", allowed_numbers("no numbers here")) == []


def test_months_and_years_are_equivalent():
    allowed = allowed_numbers("Tenure: 12 months to 60 months")
    assert unsupported_numbers("Tenure is 1 to 5 years [1].", allowed) == []


def test_devanagari_digits_and_hindi_multipliers():
    allowed = allowed_numbers("Loan up to Rs 15 lakh")
    assert unsupported_numbers("आपको १५ लाख तक का लोन मिल सकता है [1]।", allowed) == []


def test_k_shorthand():
    assert unsupported_numbers("Balance of Rs 5,000 [1].", allowed_numbers("AMB Rs 5K–Rs 10K")) == []


def test_indian_number_words():
    assert indian_words(124500) == "one lakh twenty four thousand five hundred"
    assert indian_words(25000000) == "two crore fifty lakh"
    assert indian_words(0) == "zero"


def test_speech_text_english_and_hindi():
    assert speech_text("Minimum balance is Rs 10,000 [1].", "en") == "Minimum balance is ten thousand rupees."
    assert speech_text("Rate is 7.1% p.a. [2]", "en") == "Rate is 7.1 percent per annum"
    assert speech_text("दर 7.1% है [1]।", "hi") == "दर 7.1% है।"


def test_figures_in_words_fail_the_check():
    from app.services.numbers import spelled_figures

    assert spelled_figures("Aapko saat point paanch percent milega [1]")
    assert spelled_figures("आपको सात दशमलव छह प्रतिशत मिलेगा")
    assert spelled_figures("Loan up to pachhattar hazaar rupaye")
    for ok in ("Senior citizens get 7.60 percent [1]", "You need Rs 1 lakh AMB [1]", "Up to 1 Crore 1 thousand [2]",
               "One of our cards has lounge access [1]", "Rate 7.60 प्रतिशत है [1]"):
        assert spelled_figures(ok) == [], ok


def test_call_facts_persist_until_changed():
    from app.services.conversation import merge_facts

    facts = merge_facts({}, {"senior_citizen": "yes", "product": "fixed deposit", "tenure": "", "amount": "", "account": ""})
    facts = merge_facts(facts, {"senior_citizen": "unknown", "product": "", "tenure": "2 years", "amount": "", "account": ""})
    assert facts == {"senior_citizen": "yes", "product": "fixed deposit", "tenure": "2 years"}  # turn 1 not forgotten
    assert merge_facts(facts, {"senior_citizen": "no"})["senior_citizen"] == "no"  # a correction replaces it
