"""Every fixed reply is written the way it is said (PM review, 17 Nov 2026): no asides in brackets, semicolons, "&",
"6-digit" or markdown. The voice layer smooths these for speech, but the transcript, the screen and the review read the
written text too. A new template with a written-only mark fails here, before it reaches a call."""

from app.services.content import SEED
from app.services.numbers import screen_marks


def _templates():
    """Every sentence in the journey catalogue (brain/content/catalog.json), in every language it has."""
    for mid, entry in SEED.messages.items():
        for lang, text in entry["text"].items():
            if isinstance(text, str):
                yield f"{mid}[{lang}]", text


def test_templates_are_found():
    assert sum(1 for _ in _templates()) > 300


def test_templates_read_as_spoken():
    # team names keep their "&" for the desk screen; desk.department_name says it as "and"
    bad = [f"{where}: {marks}" for where, text in _templates()
           if (marks := [m for m in screen_marks(text) if not (where.startswith("dept.") and m == "ampersand")])]
    assert not bad, "\n".join(bad)


def test_screen_marks():
    assert screen_marks("आपके रजिस्टर्ड मोबाइल (आख़िरी अंक 0101) पर") == ["brackets"]
    assert screen_marks("a 6-digit code; ok") == ["semicolon", "digit-hyphen"]
    assert screen_marks("Your balance is ₹1,240 [1].") == []
