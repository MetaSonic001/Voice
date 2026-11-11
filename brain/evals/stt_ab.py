"""A/B test of the speech-recognition vocabulary hint (PHASE-4-PLAN §6.4).

  python -m evals.stt_ab            (needs GROQ_API_KEY in brain/.env and internet; ~70 small Whisper requests)

Each phrase is spoken by the configured Edge voices (so this is a clean-audio proxy, not phone audio), then transcribed with
three hints: none, base (product names, what runs today) and extended (+ banking terms). For each hint it reports
- word error rate against the phrase that was spoken,
- how many of the banking terms in the phrase were recognised,
- "new substitutions": words that are wrong with this hint but were right without any hint (a hint must not cause these).
The extended hint is kept only if it has fewer errors than base AND no more new substitutions.
"""

import asyncio
import re
import sys

from app.config import get_settings
from app.voice.providers import EdgeTTS, GroqSTT

PHRASES = [  # (language, text, banking terms that must be recognised)
    ("en", "I want to block my Zenith Plus credit card", ["zenith"]),
    ("en", "What is the interest rate on a Laksya fixed deposit", ["laksya"]),
    ("en", "Tell me about the Ananta credit card", ["ananta"]),
    ("en", "How much is the minimum balance in the Vikaas savings account", ["vikaas"]),
    ("en", "My UPI payment is pending since morning", ["upi"]),
    ("en", "Was my EMI debited through NACH today", ["emi", "nach"]),
    ("en", "I need to complete my KYC for the Royale account", ["kyc", "royale"]),
    ("en", "Is there a charge for NEFT or IMPS transfers", ["neft", "imps"]),
    ("en", "I want to do a premature withdrawal of my fixed deposit", ["premature", "withdrawal"]),
    ("en", "What are the benefits of the Swadesh savings account", ["swadesh"]),
    ("en", "Please tell me the foreclosure amount of my home loan", ["foreclosure"]),
    ("en", "Does the Digital account have zero balance", ["digital"]),
    ("hi", "मुझे अपना क्रेडिट कार्ड ब्लॉक करना है", ["क्रेडिट", "ब्लॉक"]),
    ("hi", "मेरी FD कब mature होगी", ["fd", "mature"]),
    ("hi", "UPI से पैसे कट गए लेकिन भुगतान नहीं हुआ", ["upi"]),
    ("hi", "मेरी EMI का भुगतान क्यों फेल हुआ", ["emi"]),
    ("hi", "बचत खाते में न्यूनतम बैलेंस कितना रखना होता है", ["बैलेंस"]),
    ("hi", "मुझे Zenith credit card के बारे में जानना है", ["zenith"]),
    ("hi", "सीनियर सिटीजन के लिए FD का ब्याज क्या है", ["fd"]),
    ("hi", "मेरा बैलेंस कितना है", ["बैलेंस"]),
]
HINTS = ["none", "base", "extended"]


def tokens(text: str) -> list[str]:
    return re.findall(r"[\wऀ-ॿ]+", text.lower())


def wer(ref: list[str], hyp: list[str]) -> int:
    """Word errors (edit distance) between the spoken phrase and what was heard."""
    d = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        prev, d[0] = d[0], i
        for j, h in enumerate(hyp, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (r != h))
    return d[len(hyp)]


async def main() -> None:
    s = get_settings()
    tts = EdgeTTS({"en": s.tts_voice_en, "hi": s.tts_voice_hi, "hinglish": s.tts_voice_hinglish}, s.audio_rate, 0)
    stts = {h: GroqSTT(s.groq_api_key.get_secret_value(), s.llm_base_url, s.stt_model, vocabulary=h) for h in HINTS}
    rows = []
    for lang, text, terms in PHRASES:
        pcm = b"".join([c async for c in tts.stream(text, lang)])
        heard = {}
        for h in HINTS:
            heard[h] = (await stts[h].transcribe(pcm, s.audio_rate)).text
            await asyncio.sleep(0.4)
        rows.append((lang, text, terms, heard))
        print(f"{lang} | {text}\n    " + "\n    ".join(f"{h:8} {heard[h]}" for h in HINTS))
    print()
    summary = {}
    for h in HINTS:
        errors = sum(wer(tokens(t), tokens(heard[h])) for _, t, _, heard in rows)
        words = sum(len(tokens(t)) for _, t, _, _ in rows)
        got = sum(term in " ".join(tokens(heard[h])) for _, _, terms, heard in rows for term in terms)
        total = sum(len(terms) for _, _, terms, _ in rows)
        new_wrong = sum(1 for _, t, _, heard in rows for w in tokens(t)
                        if w in tokens(heard["none"]) and w not in tokens(heard[h]))
        summary[h] = (errors, words, got, total, new_wrong)
        print(f"{h:8} word errors {errors}/{words} ({100 * errors / words:.1f}%)   banking terms right {got}/{total}   "
              f"words right without a hint but wrong with this one: {new_wrong}")
    better = summary["extended"][0] < summary["base"][0] and summary["extended"][4] <= summary["base"][4]
    print("\nVerdict:", "extended hint is better: set STT_VOCABULARY=extended" if better else "keep STT_VOCABULARY=base (extended is not better)")
    for stt in stts.values():
        await stt.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
