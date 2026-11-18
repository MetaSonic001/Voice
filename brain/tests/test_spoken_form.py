"""Every fixed reply is written the way it is said (PM review, 17 Nov 2026): no asides in brackets, semicolons, "&",
"6-digit" or markdown. The voice layer smooths these for speech, but the transcript, the screen and the review read the
written text too. A new template with a written-only mark fails here, before it reaches a call."""

import importlib

from app.services.numbers import screen_marks

MODULES = ["app.services.personal", "app.services.conversation", "app.services.journeys", "app.services.actions",
           "app.services.smalltalk", "app.voice.phrases"]


def _templates():
    for name in MODULES:
        module = importlib.import_module(name)
        for var, value in vars(module).items():
            if not isinstance(value, dict):
                continue
            for key, entry in value.items():
                if isinstance(entry, dict) and "en" in entry:
                    for lang, text in entry.items():
                        if isinstance(text, str):
                            yield f"{name}.{var}[{key}][{lang}]", text


def test_templates_are_found():
    assert sum(1 for _ in _templates()) > 300


def test_templates_read_as_spoken():
    bad = [f"{where}: {marks}" for where, text in _templates() if (marks := screen_marks(text))]
    assert not bad, "\n".join(bad)


def test_screen_marks():
    assert screen_marks("आपके रजिस्टर्ड मोबाइल (आख़िरी अंक 0101) पर") == ["brackets"]
    assert screen_marks("a 6-digit code; ok") == ["semicolon", "digit-hyphen"]
    assert screen_marks("Your balance is ₹1,240 [1].") == []
