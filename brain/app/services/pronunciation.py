"""How the voice should say names it tends to get wrong. Applied only to the text sent to TTS: the screen,
the transcript and the call record keep the normal spelling. One reviewable list; in production the same
entries become a Sarvam pronunciation dictionary (dict_id), so this module then only keeps the cross-voice ones.

Checked by synthesising each phrase and transcribing it back, plus a listening check:
- the Hindi voice reads "AU" written in Latin letters as "आउ" (ow); written "ए.यू." it says the two letters,
  which is how the bank says its name (from Aurum, gold - not an acronym);
- "Mohd." is read as an abbreviation; product names with "+" can lose the "plus".
"""

import re

from app.services.content import SEED

LEXICON = SEED.pronunciation["rules"]  # brain/content/catalog.json "pronunciation" (locked: changed in git)
_COMPILED = {lang: [(re.compile(p), r) for p, r in rules] for lang, rules in LEXICON.items()}


# How "AU" is said, chosen by the owner by listening (6 Nov 2026) and fixed here: English and Hinglish "A.U.",
# Hindi "एयू". Applied to the text sent to TTS only.
SPELLING = SEED.pronunciation["spelling"]


def say(text: str, language: str) -> str:
    text = re.sub(r"\bAU\b", SPELLING["hi" if language == "hi" else "en"], text)
    for pattern, replacement in _COMPILED.get(language, _COMPILED["en"]):
        text = pattern.sub(replacement, text)
    return text
