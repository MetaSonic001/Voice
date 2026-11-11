"""Knowledge-base metadata and the checks that run in code before the answer model sees any passage (PHASE-4-PLAN §5).

Document-level tags, not per-figure records. The product and variant come from the chunk's own heading path
("CASA+TD KB › AU Swadesh Savings Account › Fees & Charges"); the document type and its authority come from rules on the
title and category, and a person can correct them on the KB page (stored in `documents.tags`).

Checks (all deterministic, so the model never has to choose between sources):
1. variant_gate:      a generic question about a product family ("minimum balance for a savings account") whose best
                      passages belong to particular variants is a CLARIFY decided here, naming the variants.
2. select_sources:    drops expired or superseded documents and passages that try to instruct the model, and for a rate
                      question keeps the dated rate card (highest authority) so a stale overview can never outvote it.
3. provenance:        each passage is labelled with its document type and effective date for the answer step.
"""

import re
from datetime import date

from app.domain import Hit

AUTHORITY = {"rate_card": 5, "schedule_of_charges": 4, "terms": 3, "product_guide": 2, "policy": 2, "faq": 2, "brochure": 1}
DOC_TYPES = tuple(AUTHORITY)
_FAMILY = re.compile(r"^(?P<variant>.+?) (?P<family>Savings Account|Credit Card|Current Account|Fixed Deposit|Debit Card)$", re.I)
_GENERIC = re.compile(
    r"\b(savings?|credit|debit|current) (account|acct|card)s?\b|बचत खाता|सेविंग|क्रेडिट कार्ड|डेबिट कार्ड|savings? khata", re.I)
_COMPARE = re.compile(
    r"\b(compare|comparison|difference|differ|versus|vs|which|best|suits?|suitable|recommend|options?|types?|kinds?|list|all|"
    r"offer|available|kaunsa|kaunsi|konsa|konsi|konse|sabse|fark|behtar)\b|विकल्प|कौन|कौनसा|कौन सा|फ़र्क|फर्क|सबसे", re.I)
_RATE = re.compile(r"\b(rate|rates|interest|byaaj|roi|p\.a\.)\b|%|ब्याज|दर", re.I)


def product_of(section_path: str) -> tuple[str, str]:
    """(family, variant) from the product heading in a chunk path, e.g. ("Savings Account", "Swadesh"); ("", "") when the
    passage is not about one particular product variant (overviews, comparisons, rate cards, app features)."""
    parts = [p.strip() for p in section_path.split("›")]
    if len(parts) < 3 or not parts[1].startswith("AU "):
        return "", ""
    m = _FAMILY.match(parts[1][3:].strip())
    return (m.group("family").title(), m.group("variant").strip()) if m else ("", "")


def doc_type_of(title: str, category: str | None = None, tags: dict | None = None) -> str:
    if tags and tags.get("doc_type") in AUTHORITY:
        return tags["doc_type"]
    t = f"{title} {category or ''}".lower()
    if re.search(r"rates?[- ]at[- ]a[- ]glance|rate card|\brates\b", t):
        return "rate_card"
    if re.search(r"schedule of (charges|fees)|charges|tariff", t):
        return "schedule_of_charges"
    if re.search(r"terms|conditions", t):
        return "terms"
    if re.search(r"polic", t):
        return "policy"
    if "faq" in t:
        return "faq"
    return "brochure" if "brochure" in t else "product_guide"


def doc_tags(title: str, category: str | None, stored: dict | None) -> dict:
    """The effective tags of a document: rules first, then whatever a person set on the KB page."""
    stored = stored or {}
    doc_type = doc_type_of(title, category, stored)
    return {"doc_type": doc_type, "authority": int(stored.get("authority") or AUTHORITY[doc_type]),
            "effective_to": stored.get("effective_to"), "superseded_by": stored.get("superseded_by"),
            "instruction_flag": bool(stored.get("instruction_flag"))}


def hit_type(h: Hit) -> str:
    return doc_type_of(h.document_title, h.doc_category, h.doc_tags)


def variants_in(hits: list[Hit]) -> list[str]:
    seen: list[str] = []
    for h in hits:
        v = product_of(h.section_path)[1]
        if v and v not in seen:
            seen.append(v)
    return seen


def variant_gate(question: str, facts_text: str, context: list[Hit], fused: list[Hit]) -> list[str] | None:
    """The variants to ask the caller about, or None when the answer may go ahead.

    Fires for a generic question about a product family that names no variant, when the best passage is about one
    particular variant or the best passages mix several. Comparisons and "which suits me" questions are left to the
    answer step: they are meant to span variants."""
    if not context or not _GENERIC.search(question) or _COMPARE.search(question):
        return None
    known = {v.lower() for v in variants_in(fused + context)}
    named = f"{question} {facts_text}".lower()
    if any(re.search(rf"(?<!\w){re.escape(v)}(?!\w)", named) for v in known):
        return None
    top_is_general = not product_of(context[0].section_path)[1]
    if top_is_general and _RATE.search(question):
        return None  # a rate question is answered by the rate card itself
    if top_is_general and len(variants_in(context)) < 2:
        return None  # the passages are general (a rate card, an overview): no particular variant is being presented as "the" answer
    family = next((product_of(h.section_path)[0] for h in context if product_of(h.section_path)[1]), "")
    options = [v for v in variants_in(fused + context) if product_of(next(h for h in fused + context if
                                                                          product_of(h.section_path)[1] == v).section_path)[0] == family]
    return options[:4] if len(options) >= 2 else None  # a family with a single variant has nothing to ask about


def select_sources(question: str, context: list[Hit], today: date) -> tuple[list[Hit], list[str]]:
    """The passages the answer step may use, and what was dropped and why (kept in the trace)."""
    notes: list[str] = []
    kept = []
    for h in context:
        tags = h.doc_tags or {}
        if tags.get("superseded_by") or (tags.get("effective_to") and str(tags["effective_to"]) < today.isoformat()):
            notes.append(f"dropped {h.document_title}: expired or superseded")
        elif tags.get("instruction_flag") and _looks_like_instruction(h.text):
            notes.append(f"dropped a passage of {h.document_title}: it tries to instruct the assistant")
        else:
            kept.append(h)
    if _RATE.search(question):  # the dated rate card outranks every overview that quotes "up to" figures
        cards = [h for h in kept if hit_type(h) == "rate_card"]
        top = max((h.rerank_score or 0 for h in kept), default=0)
        if cards and (cards[0].rerank_score or 0) >= 0.5 * top and len(cards) < len(kept):
            notes.append("rate question: kept the rate card only (highest authority)")
            kept = cards
    return kept, notes


# ───────── which passages go to the answer model (relevance, identity, duplicates) ─────────
# Tuned by replaying three saved golden-set runs (evals/context_replay.py, 6 Nov 2026): the evidence each answer needs
# must survive. 0.30 lost the 2-year FD rate row; a 0.10 floor lost the online-shopping card passages (all scores were
# low, best 0.118). 0.20 + 0.05 lost nothing on any run and cut the passage text sent by about 22% (3,568 -> 2,792 chars).
REL_TO_BEST = 0.20  # keep a passage only if it scores at least this share of the best passage ...
ABS_FLOOR = 0.05  # ... and at least this much on its own
_DUP_JACCARD = 0.8


def _name_re(name: str) -> re.Pattern:
    # "Zenith" must not match "Zenith+" (two different cards); spaces and case do not matter
    return re.compile(rf"(?<![\w]){re.escape(name.strip().lower())}(?![\w+])", re.I)


def named_products(question: str, named: list[str], hits: list[Hit]) -> list[str]:
    """The product variants the caller named: the router's list when it gave one, else the variants of the retrieved
    passages that appear in the question (longest first, so "Zenith+" wins over "Zenith")."""
    names = [n.strip() for n in named or [] if n and n.strip()]
    if names:
        return list(dict.fromkeys(names))
    found, text = [], question.lower()
    for v in sorted(variants_in(hits), key=len, reverse=True):
        if _name_re(v).search(text):
            found.append(v)
            text = _name_re(v).sub(" ", text)
    return found


def about(h: Hit, name: str) -> bool:
    """Is this passage about the named product? Its product heading names it, or its text does (a rate card, an
    overview, or another product's comparison table that covers it). Found by replaying the golden set: "Which debit card
    comes with a Digital account?" is answered by the Kids account's comparison table."""
    variant = product_of(h.section_path)[1]
    if variant and _name_re(name).search(variant.lower()):
        return True
    return bool(_name_re(name).search(f"{h.section_path} {h.text}"))


_FOREIGN = re.compile(r"\b(fcnr|nri|nre|nro|foreign currency|usd|eur|gbp|dollar|euro|pound)\b", re.I)


def trim_context(context: list[Hit], names: list[str], scope: str, question: str = "") -> tuple[list[Hit], list[str]]:
    """Relevance (absolute floor + share of the best score), identity (a passage about a different named product is
    dropped; general passages stay) and duplicates. The best passage always stays.
    For a rate question, rate-card passages need only the absolute floor (the right row of the table can score low), and
    NRI foreign-currency deposit tables are dropped unless the caller asked about them."""
    if not context:
        return context, []
    notes: list[str] = []
    if _RATE.search(question) and not _FOREIGN.search(question):
        domestic = [h for h in context if not re.search(r"\bFCNR\b|\bRFC\b", f"{h.section_path} {h.text[:120]}")]
        if domestic and len(domestic) < len(context):
            notes.append(f"rate question: dropped {len(context) - len(domestic)} foreign-currency deposit passage(s)")
            context = domestic
    rate_q = bool(_RATE.search(question))
    best = context[0].rerank_score or 0
    floor = max(ABS_FLOOR, REL_TO_BEST * best)
    kept = [context[0]] + [h for h in context[1:] if (h.rerank_score or 0) >= floor
                           or (rate_q and hit_type(h) == "rate_card")]  # a 1-year FD rate row scored 0.049 in a live test
    if len(kept) < len(context):
        notes.append(f"dropped {len(context) - len(kept)} weak passage(s) (below {floor:.2f})")
    if names and scope not in ("overview", "recommend", "guided"):  # named products: no other product's passages
        before = len(kept)
        kept = [h for i, h in enumerate(kept) if i == 0 or not product_of(h.section_path)[1]
                or any(about(h, n) for n in names)]
        if len(kept) < before:
            notes.append(f"dropped {before - len(kept)} passage(s) about a different product than {', '.join(names)}")
    out: list[Hit] = []
    for h in kept:
        words = set(h.text.lower().split())
        if any(len(words & (s := set(o.text.lower().split()))) / (len(words | s) or 1) >= _DUP_JACCARD for o in out):
            notes.append("dropped a duplicate passage")
            continue
        out.append(h)
    return out, notes


def mention_note(h: Hit, names: list[str]) -> str:
    """For a passage about another product that only mentions the named one (a comparison table): tell the answer model
    to use only what it says about the named product, so the other product's benefits are never attributed to it."""
    variant = product_of(h.section_path)[1]
    if not variant or not names:
        return ""
    if any(_name_re(n).search(variant.lower()) for n in names):
        return ""
    mentioned = [n for n in names if _name_re(n).search(h.text)]
    return (f" [about {variant}; use only what it says about {', '.join(mentioned)}]") if mentioned else ""


def missing_products(context: list[Hit], names: list[str]) -> list[str]:
    """For a comparison: the named products that no passage supports (never compare against a side we can't see)."""
    return [n for n in names if not any(about(h, n) for h in context)]


def _looks_like_instruction(text: str) -> bool:
    from app.services.guard import instruction_like

    return instruction_like(text)


def provenance(h: Hit) -> str:
    kind = hit_type(h).replace("_", " ")
    return f"{kind}, effective {h.effective_from}" if h.effective_from else kind
