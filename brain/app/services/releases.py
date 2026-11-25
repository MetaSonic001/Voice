"""Releases of the journey catalogue (ADR 0032): a product manager edits a draft on /journeys, code checks it, the LLM
writes the Hindi and Hinglish of new English sentences, and the draft goes live for a share of calls (canary) or for all.

1. Draft: one at a time, starting from what is live. A save sends only what changed plus the `rev` the screen last saw;
   a save from an older screen is refused, so two people editing at once never overwrite each other without knowing.
2. Check: placeholders kept, no new figures, nothing a phone voice can't say, no request for an OTP or PIN, no links.
3. Translate: people write English only. Hindi and Hinglish are written here, once, when the draft is prepared - never
   during a call, so fixed replies stay instant and the caller's bank data is never sent to an LLM to be translated.
4. Publish to 1-50% of calls (canary) or to all. Every copy of the brain reads one small row every few seconds and
   switches when it changes. A call keeps the release it started on (content.for_session).
5. Promote the canary to all calls, change its share, stop it, or roll back to any earlier release (or to git's).

Published releases never change. The draft and every release store only changes on top of brain/content/ in git.
"""

import asyncio
import copy
import json
import logging
import re
import threading

from app.db import Database
from app.logs import log
from app.services import content
from app.services.content import EDITABLE_PROMPTS, EDITABLE_SETTINGS, SEED, Catalog, build, numbers_in, placeholders
from app.services.guard import unsafe_output
from app.services.numbers import screen_marks

logger = logging.getLogger(__name__)
POLL_S = 5
CANARY_SHARES = (1, 5, 10, 25, 50)
MAX_TEXT = 600
MAX_PROMPT = 20_000
TRANSLATE_BATCH = 12
MAX_TRANSLATE = 50  # sentences per "Translate now"
MAX_LANGUAGES = 12
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_LANG_CODE = re.compile(r"^[a-z][a-z_]{1,19}$")


def translate_schema(langs: list[str]) -> dict:
    """The JSON the translation call must return: one text per asked language for each sentence."""
    item = {"id": {"type": "string"}} | {lang: {"type": "string"} for lang in langs}
    return {"type": "object", "properties": {"items": {"type": "array", "items": {
        "type": "object", "properties": item, "required": list(item), "additionalProperties": False}}},
        "required": ["items"], "additionalProperties": False}


def _languages(body: dict) -> dict:
    """The draft's languages: git's, plus any the draft added, with changed writing styles."""
    return content.languages_of({"languages": list(SEED.languages.values())}, body)


# What each prompt does, in plain words, for the prompts tab (order = order on the page).
PROMPTS = {
    "answer_style": "How the agent sounds when it answers product questions from the bank's documents: tone, length and "
                    "what to avoid. The locked rules below are added after it.",
    "strategy": "One line per kind of product question, telling the answer model how much to say (a direct answer, an "
                "overview, a comparison, a recommendation...).",
    "answer_rules": "The answer model's safety rules: only the bank's documents, figures copied exactly, and the words "
                    "CLARIFY, CONFLICT and NO_ANSWER that code acts on. Locked: code depends on them.",
    "language": "How each language is spoken (everyday Hindi with English banking words, Hinglish in Latin letters). Locked.",
    "understand": "Reads each caller turn and returns the JSON the code routes on (what they want, risk, language). "
                  "Locked: code reads its output field by field.",
    "judge": "Scores each finished call and writes the fix list. Changing it changes reviews, not what callers hear.",
    "summary": "Writes the note a person sees when a call is handed to them.",
    "translate": "Writes the other languages of an English sentence when you press Translate now or Prepare translations, "
                 "following each language's writing style (Settings > Languages). Locked.",
}


class ReleaseError(Exception):
    def __init__(self, message: str, status: int = 400, problems: list | None = None):
        super().__init__(message)
        self.status, self.problems = status, problems or []


# ───────── changes to a draft (pure functions) ─────────

def _langs(mid: str) -> list[str]:
    return list(SEED.messages[mid]["text"])


def apply_changes(body: dict, changes: dict, live: dict) -> dict:
    """The draft `body` with `changes` from the editor applied. `None` for a message, prompt or setting means "undo":
    back to what is live. A new English sentence drops its old translations (they no longer match)."""
    body = copy.deepcopy(body)
    msgs, auto = body.setdefault("messages", {}), body.setdefault("auto", {})
    _apply_languages(body, changes.get("languages") or {}, live)
    langs = _languages(body)
    for mid, value in (changes.get("messages") or {}).items():
        seed = SEED.messages.get(mid)
        if seed is None:
            raise ReleaseError(f"Unknown message '{mid}'.")
        if seed.get("locked"):
            raise ReleaseError(f"'{mid}' can't be edited here: {seed['locked']}")
        if value is None:
            _restore(msgs, (live.get("messages") or {}).get(mid), mid)
            _restore(auto, (live.get("auto") or {}).get(mid), mid)
            continue
        if not isinstance(value, dict) or not all(isinstance(v, str) or (v is None and k != "en") for k, v in value.items()):
            raise ReleaseError(f"'{mid}': send {{language: text}} (or null to remove a translation in an added language).")
        for lang in [k for k, v in value.items() if v is None]:
            if lang in seed["text"] or langs.get(lang, {}).get("spoken"):
                raise ReleaseError(f"Callers use {langs.get(lang, {}).get('name', lang)}: correct the translation or undo "
                                   "the change instead of removing it.")
            (msgs.get(mid) or {}).pop(lang, None)
            if mid in auto:
                auto[mid] = [x for x in auto[mid] if x != lang]
            value = {k: v for k, v in value.items() if k != lang}
        entry = msgs.setdefault(mid, {})
        if "en" in value and value["en"].strip() != entry.get("en", seed["text"]["en"]):
            entry.clear()
            entry["en"] = value["en"].strip()
            auto.pop(mid, None)
        for lang in value:
            if lang == "en":
                continue
            if lang not in langs:
                raise ReleaseError(f"Unknown language '{lang}': add it under Settings > Languages first.")
            entry[lang] = value[lang].strip()  # a person corrected it: no longer marked as AI-written
            auto[mid] = [x for x in auto.get(mid, []) if x != lang]
        if not auto.get(mid):
            auto.pop(mid, None)
        if set(entry) <= set(seed["text"]) and all(entry.get(lang, text) == text for lang, text in seed["text"].items()):
            msgs.pop(mid)  # back to git's wording
            auto.pop(mid, None)
    prompts = body.setdefault("prompts", {})
    for name, value in (changes.get("prompts") or {}).items():
        if name not in EDITABLE_PROMPTS and name != "strategy":
            raise ReleaseError(f"The '{name}' prompt is locked: a developer changes it in git.")
        if value is None:
            _restore(prompts, (live.get("prompts") or {}).get(name), name)
        elif name == "strategy":
            if not isinstance(value, dict) or not set(value) <= set(SEED.prompts["strategy"]) \
                    or not all(isinstance(v, str) for v in value.values()):
                raise ReleaseError("Strategy lines: send {strategy: text} for the existing strategies.")
            merged = prompts.get("strategy", {}) | {k: v.strip() for k, v in value.items()}
            merged = {k: v for k, v in merged.items() if v != SEED.prompts["strategy"][k]}
            prompts["strategy"] = merged
            if not merged:
                prompts.pop("strategy")
        elif isinstance(value, str):
            prompts[name] = value.replace("\r\n", "\n")
            if prompts[name] == SEED.prompts[name]:
                prompts.pop(name)
        else:
            raise ReleaseError(f"The '{name}' prompt must be text.")
    settings = body.setdefault("settings", {})
    for section, rows in (changes.get("settings") or {}).items():
        if section not in EDITABLE_SETTINGS or not isinstance(rows, dict):
            raise ReleaseError(f"Unknown settings '{section}'.")
        mine = settings.setdefault(section, {})
        for key, value in rows.items():
            seed = SEED.settings[section].get(key)
            if seed is None:
                raise ReleaseError(f"Unknown {section} '{key}': new ones are added in code.")
            if value is None:
                _restore(mine, ((live.get("settings") or {}).get(section) or {}).get(key), key)
            elif isinstance(seed, dict):
                if not isinstance(value, dict) or not set(value) <= set(EDITABLE_SETTINGS[section]):
                    raise ReleaseError(f"{section} '{key}': only {', '.join(EDITABLE_SETTINGS[section])} can change.")
                row = mine.get(key, {}) | value
                mine[key] = {f: v for f, v in row.items() if v != seed[f]}
                if not mine[key]:
                    mine.pop(key)
            else:
                mine[key] = value
                if value == seed:
                    mine.pop(key)
        if not mine:
            settings.pop(section)
    if "tree" in changes:
        tree = changes["tree"]
        if tree is None:
            _restore(body, live.get("tree"), "tree")
        else:
            body["tree"] = _clean_tree(tree)
            if body["tree"] == SEED.tree:
                body.pop("tree")
    return {k: v for k, v in body.items() if v or k == "messages"}


def _apply_languages(body: dict, changes: dict, live: dict) -> None:
    """Add a language (a name and how it is written), change how one is written, or remove one the draft added.
    English is the source and can't change; git's languages can't be removed (callers use them)."""
    mine = body.setdefault("languages", {})
    for code, value in changes.items():
        seed = SEED.languages.get(code)
        if seed and seed.get("source"):
            raise ReleaseError("English is the language everything is written in: it can't be changed here.")
        if value is None:
            if seed:
                _restore(mine, (live.get("languages") or {}).get(code), code)
                continue
            mine.pop(code, None)  # an added language goes, with every sentence written in it
            for entry in (body.get("messages") or {}).values():
                entry.pop(code, None)
            for mid in list(body.get("auto") or {}):
                body["auto"][mid] = [x for x in body["auto"][mid] if x != code]
                if not body["auto"][mid]:
                    body["auto"].pop(mid)
            continue
        if not isinstance(value, dict):
            raise ReleaseError(f"Language '{code}': send {{name, style}}.")
        style = str(value.get("style", "")).strip()
        if not 10 <= len(style) <= 600:
            raise ReleaseError("Describe how the language should be written (10 to 600 characters).")
        if seed:
            if style == seed["style"]:
                mine.pop(code, None)
            else:
                mine[code] = {"style": style}
            continue
        name = str(value.get("name", "")).strip()
        if not _LANG_CODE.match(code) or not 2 <= len(name) <= 40:
            raise ReleaseError("A new language needs a short name (2 to 40 characters).")
        if code not in mine and len(_languages(body)) >= MAX_LANGUAGES:
            raise ReleaseError(f"At most {MAX_LANGUAGES} languages.")
        mine[code] = {"name": name, "style": style}
    if not mine:
        body.pop("languages")


def _restore(target: dict, value, key: str) -> None:
    if value is None:
        target.pop(key, None)
    else:
        target[key] = copy.deepcopy(value)


def _clean_tree(tree) -> list:
    """The editor's tree, checked for shape: domains > journeys > steps > message ids. Ids must be unique."""
    if not isinstance(tree, list):
        raise ReleaseError("The journey tree must be a list of domains.")
    out, ids = [], set()
    for d in tree:
        if not isinstance(d, dict) or not str(d.get("id", "")).strip() or not str(d.get("name", "")).strip():
            raise ReleaseError("Every domain needs an id and a name.")
        journeys = []
        for j in d.get("journeys") or []:
            if not isinstance(j, dict) or not str(j.get("id", "")).strip() or not str(j.get("name", "")).strip():
                raise ReleaseError(f"Every journey in '{d['name']}' needs an id and a name.")
            steps = [{"name": str(s.get("name", "")).strip()[:80] or "Step",
                      "messages": [m for m in s.get("messages") or [] if isinstance(m, str) and m in SEED.messages]}
                     for s in j.get("steps") or [] if isinstance(s, dict)]
            journeys.append({"id": str(j["id"])[:60], "name": str(j["name"]).strip()[:80], "steps": steps})
        out.append({"id": str(d["id"])[:60], "name": str(d["name"]).strip()[:80], "journeys": journeys})
    for d in out:
        for key in [("d", d["id"])] + [("j", j["id"]) for j in d["journeys"]]:
            if key in ids:
                raise ReleaseError(f"Two domains or journeys share the id '{key[1]}'.")
            ids.add(key)
    return out


def needs_translation(body: dict) -> list[tuple[str, list[str]]]:
    """(message id, languages missing) for every new English sentence whose other languages aren't written yet."""
    out = []
    for mid, entry in (body.get("messages") or {}).items():
        if mid in SEED.messages and "en" in entry:
            missing = [lang for lang in _langs(mid)[1:] if lang not in entry]
            if missing:
                out.append((mid, missing))
    return out


def missing_in(cat: Catalog, mid: str, languages) -> list[str]:
    """The languages (of `languages`) a sentence has no text in yet. Locked word lists don't count: code owns them."""
    entry = cat.messages[mid]
    if entry.get("locked") or not isinstance(entry["text"]["en"], str):
        return []
    return [x for x in languages if x not in entry["text"]]


def _text_problems(mid: str, lang: str, text: str, langs: dict | None = None) -> list[str]:
    seed = SEED.messages[mid]["text"]
    script = (langs or SEED.languages).get(lang, {}).get("script")
    out = []
    if not text.strip():
        return ["is empty"]
    if len(text) > MAX_TEXT:
        out.append(f"is longer than {MAX_TEXT} characters")
    if placeholders(text) != placeholders(seed.get(lang, seed["en"])):
        want = sorted(placeholders(seed.get(lang, seed["en"])))
        out.append("must keep exactly these blanks: " + (", ".join("{" + p + "}" for p in want) if want else "none"))
    new = numbers_in(text) - set().union(*(numbers_in(t) for t in seed.values()))
    if new:
        out.append(f"adds a figure ({', '.join(sorted(new))}): figures come from bank data or the knowledge base, "
                   "so a fixed sentence can't add one; ask a developer")
    marks = [m for m in screen_marks(text) if not (mid.startswith("dept.") and m == "ampersand")]
    if marks:
        out.append(f"has something a voice can't say naturally ({', '.join(marks)})")
    if unsafe_output(text):
        out.append("looks like it asks for a secret (OTP, PIN, CVV, password) or has a web link")
    if script == "devanagari" and not _DEVANAGARI.search(text):
        out.append("should be in Devanagari")
    if script == "latin" and lang != "en" and _DEVANAGARI.search(text):
        out.append("should be in Latin letters")
    return out


def problems(body: dict) -> list[dict]:
    """Everything that stops (error) or should make someone look again (warning) before publishing."""
    out = []
    langs = _languages(body)
    name = lambda code: langs.get(code, {}).get("name", code)  # noqa: E731
    for mid, entry in (body.get("messages") or {}).items():
        for lang, text in entry.items():
            if lang in langs:
                for p in _text_problems(mid, lang, text, langs):
                    out.append({"level": "error", "area": "message", "id": mid, "text": f"{name(lang)} {p}."})
    for mid, missing in needs_translation(body):
        out.append({"level": "error", "area": "message", "id": mid,
                    "text": f"Needs {' and '.join(name(x) for x in missing)}: press Translate now, or Prepare translations."})
    for code, lang in langs.items():
        if lang.get("added"):
            out.append({"level": "warning", "area": "language", "id": code,
                        "text": f"{lang['name']} is kept ready, but callers can't use it until a developer adds speech and "
                                "understanding for it."})
    for name, value in (body.get("prompts") or {}).items():
        texts = value.values() if isinstance(value, dict) else [value]
        if any(not t.strip() for t in texts):
            out.append({"level": "error", "area": "prompt", "id": name, "text": "Is empty."})
        elif any(len(t) > MAX_PROMPT for t in texts):
            out.append({"level": "error", "area": "prompt", "id": name, "text": f"Is longer than {MAX_PROMPT} characters."})
        else:
            out.append({"level": "warning", "area": "prompt", "id": name,
                        "text": "Prompt changes change how the AI behaves and code can't check them: start with a small "
                                "canary and watch the results."})
    s = body.get("settings") or {}
    for key, days in (s.get("tat_days") or {}).items():
        if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= 30:
            out.append({"level": "error", "area": "setting", "id": f"tat_days.{key}", "text": "Must be 1 to 30 working days."})
    for key, row in (s.get("departments") or {}).items():
        if "priority" in row and row["priority"] not in (1, 2, 3):
            out.append({"level": "error", "area": "setting", "id": f"departments.{key}", "text": "Priority must be 1, 2 or 3."})
        if "always_open" in row and not isinstance(row["always_open"], bool):
            out.append({"level": "error", "area": "setting", "id": f"departments.{key}", "text": "Open 24x7 must be yes or no."})
        if "handles" in row and not (isinstance(row["handles"], str) and 0 < len(row["handles"].strip()) <= 200):
            out.append({"level": "error", "area": "setting", "id": f"departments.{key}", "text": "Say what the team handles (up to 200 characters)."})
    for key, row in (s.get("issues") or {}).items():
        if row.get("department") not in SEED.settings["departments"]:
            out.append({"level": "error", "area": "setting", "id": f"issues.{key}", "text": "Pick one of the teams."})
    for d in body.get("tree") or []:
        if not d["journeys"]:
            out.append({"level": "warning", "area": "tree", "id": d["id"], "text": f"The domain '{d['name']}' is empty."})
    return out


def _effective_body(body: dict) -> dict:
    """What changes how the agent behaves (not who wrote the translations)."""
    return {k: body.get(k) or {} for k in ("messages", "prompts", "settings", "languages")} | {"tree": body.get("tree")}


# ───────── the store ─────────

class Releases:
    def __init__(self, db: Database, llm=None, settings=None):
        self.db, self.llm, self.s = db, llm, settings
        self.seed = content.read_seed()
        self._cache: dict[int, Catalog] = {}
        self._seen = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.on_live = None  # called with the live catalogues when they change (voice warms their fixed phrases)

    # ── what is live ──

    def rollout(self) -> dict:
        with self.db.connection() as conn:
            return conn.execute("SELECT * FROM content_rollout WHERE id = 1").fetchone()

    def catalog(self, release_id: int | None) -> Catalog:
        if not release_id:
            return content.SEED
        if release_id not in self._cache:  # published releases never change, so they are built once
            with self.db.connection() as conn:
                row = conn.execute("SELECT body FROM content_releases WHERE id = %s", (release_id,)).fetchone()
            self._cache[release_id] = build(self.seed, row["body"] if row else {}, release_id, f"Release {release_id}")
        return self._cache[release_id]

    def load(self) -> bool:
        """Make live in this copy of the brain what the rollout row says. True when it changed."""
        r = self.rollout()
        if r is None or r["changed_at"] == self._seen:
            return False
        stable, canary = self.catalog(r["stable_id"]), self.catalog(r["canary_id"]) if r["canary_id"] else None
        content.set_live(stable, canary, r["canary_pct"])
        self._seen = r["changed_at"]
        log(logger, "content live", stable=r["stable_id"] or 0, canary=r["canary_id"], share=r["canary_pct"])
        if self.on_live:
            self.on_live([c for c in (stable, canary) if c is not None])
        return True

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="content-poll", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(POLL_S):
            try:
                self.load()
            except Exception as e:  # the database is away for a moment: keep what is live
                logger.warning("content poll failed", extra={"data": {"error": f"{type(e).__name__}: {e}"}})

    # ── the draft ──

    def _live_body(self, conn) -> dict:
        """The newest live release's changes (the canary's while one runs): a new draft starts from them, and "undo"
        goes back to them."""
        row = conn.execute("SELECT r.body FROM content_rollout o JOIN content_releases r "
                           "ON r.id = COALESCE(o.canary_id, o.stable_id) WHERE o.id = 1").fetchone()
        return row["body"] if row else {}

    def draft(self) -> dict:
        with self.db.connection() as conn, conn.transaction():
            row = conn.execute("SELECT * FROM content_releases WHERE status = 'draft'").fetchone()
            if row:
                return row
            conn.execute("INSERT INTO content_releases (status, body) VALUES ('draft', %s) ON CONFLICT DO NOTHING",
                         (json.dumps(self._live_body(conn)),))
            return conn.execute("SELECT * FROM content_releases WHERE status = 'draft'").fetchone()

    def save(self, changes: dict, rev: int, author: str) -> None:
        self.draft()
        with self.db.connection() as conn, conn.transaction():
            row = conn.execute("SELECT * FROM content_releases WHERE status = 'draft' FOR UPDATE").fetchone()
            if row["rev"] != rev:
                raise ReleaseError("Someone else saved this draft after you opened it. Reload to see their changes, "
                                   "then make yours again.", 409)
            body = apply_changes(row["body"], changes, self._live_body(conn))
            conn.execute("UPDATE content_releases SET body = %s, rev = rev + 1, author = %s, updated_at = now() WHERE id = %s",
                         (json.dumps(body, ensure_ascii=False), author[:80], row["id"]))

    def discard(self) -> None:
        """Throw the draft away; the next open starts again from what is live."""
        with self.db.connection() as conn:
            conn.execute("DELETE FROM content_releases WHERE status = 'draft'")

    async def translate(self, author: str, ids: list[str] | None = None, languages: list[str] | None = None,
                        hint: str = "", fill_missing: bool = False) -> dict:
        """Write other languages of English sentences with the LLM (batches of 12) into the draft, marked AI-written.

        `ids` None: every changed sentence still missing a language (Prepare translations). `ids` given: those sentences
        (Translate now), into `languages` (default: every language but English), even if they already have one.
        `fill_missing`: every sentence that has no text yet in `languages` (a newly added language), at most
        MAX_TRANSLATE per call: the page calls again until `remaining` is 0.
        `hint`: the product manager's note on the last attempt ("more polite", "say EMI, not instalment")."""
        row = await asyncio.to_thread(self.draft)
        body, langs = row["body"], _languages(row["body"])
        cat = build(self.seed, body)
        unknown = [x for x in languages or [] if x not in langs or langs[x].get("source")]
        if unknown:
            raise ReleaseError(f"Can't translate into: {', '.join(unknown)}.")
        remaining = 0
        if fill_missing:
            if not languages:
                raise ReleaseError("Pick the languages to fill in.")
            gaps = [(mid, needs) for mid in cat.messages if (needs := missing_in(cat, mid, languages))]
            todo, remaining = gaps[:MAX_TRANSLATE], max(0, len(gaps) - MAX_TRANSLATE)
        elif ids is None:
            todo = [(mid, [x for x in missing if not languages or x in languages]) for mid, missing in needs_translation(body)]
        else:
            if len(ids) > MAX_TRANSLATE:
                raise ReleaseError(f"Translate at most {MAX_TRANSLATE} sentences at a time.")
            bad = [m for m in ids if m not in SEED.messages or SEED.messages[m].get("locked")]
            if bad:
                raise ReleaseError(f"These can't be translated here: {', '.join(bad)}.")
            targets = languages or [c for c, x in langs.items() if not x.get("source")]
            todo = [(mid, targets) for mid in dict.fromkeys(ids)]
        todo = [(mid, needs) for mid, needs in todo if needs]
        if not todo:
            return {"translated": 0, "failed": []}
        if self.llm is None:
            raise ReleaseError("No LLM is set up to translate.", 503)
        hint = hint.strip()[:300]
        items = []
        for mid, needs in todo:
            texts = cat.messages[mid]["text"]
            item = {"id": mid, "english": texts["en"], "needs": needs, "earlier": {x: texts[x] for x in needs if x in texts}}
            items.append(item | ({"feedback": hint} if hint else {}))
        done, failed = {}, []
        for i in range(0, len(items), TRANSLATE_BATCH):
            batch = items[i:i + TRANSLATE_BATCH]
            asked = sorted({x for it in batch for x in it["needs"]})
            payload = {"languages": {x: {"name": langs[x]["name"], "style": langs[x]["style"]} for x in asked}, "items": batch}
            try:
                data, _ = await self.llm.json(self.s.answer_model, SEED.prompts["translate"],
                                              json.dumps(payload, ensure_ascii=False), translate_schema(asked), effort="low")
            except Exception as e:
                failed += [{"id": it["id"], "text": f"The translation service failed ({type(e).__name__}). Try again."} for it in batch]
                continue
            got = {str(x.get("id")): x for x in data.get("items") or [] if isinstance(x, dict)}
            for it in batch:
                out = got.get(it["id"]) or {}
                texts = {x: str(out.get(x) or "").strip() for x in it["needs"]}
                bad = [f"{langs[x]['name']} {p}" for x, t in texts.items() for p in _text_problems(it["id"], x, t, langs)]
                if bad:
                    failed.append({"id": it["id"], "text": "; ".join(bad) + ". Try again, or type it yourself."})
                else:
                    done[it["id"]] = (it["english"], texts)

        def store():
            with self.db.connection() as conn, conn.transaction():
                row = conn.execute("SELECT * FROM content_releases WHERE status = 'draft' FOR UPDATE").fetchone()
                if row is None:
                    return 0
                body, n = row["body"], 0
                for mid, (english, texts) in done.items():
                    entry = body.setdefault("messages", {}).setdefault(mid, {})
                    if entry.get("en", SEED.messages[mid]["text"]["en"]) != english:
                        continue  # the English changed while translating: it gets translated again
                    entry.update(texts)
                    auto = body.setdefault("auto", {})
                    auto[mid] = sorted(set(auto.get(mid, [])) | set(texts))
                    n += 1
                conn.execute("UPDATE content_releases SET body = %s, rev = rev + 1, author = %s, updated_at = now() "
                             "WHERE id = %s", (json.dumps(body, ensure_ascii=False), author[:80], row["id"]))
                return n

        n = await asyncio.to_thread(store) if done else 0
        log(logger, "content translated", messages=n, failed=len(failed), remaining=remaining)
        return {"translated": n, "failed": failed, "remaining": remaining}

    # ── going live ──

    def publish(self, share: int, notes: str, author: str) -> None:
        if share != 100 and share not in CANARY_SHARES:
            raise ReleaseError(f"Publish to {', '.join(map(str, CANARY_SHARES))}% of calls, or to all (100).")
        self.draft()
        with self.db.connection() as conn, conn.transaction():
            r = conn.execute("SELECT * FROM content_rollout WHERE id = 1 FOR UPDATE").fetchone()
            row = conn.execute("SELECT * FROM content_releases WHERE status = 'draft' FOR UPDATE").fetchone()
            if r["canary_id"]:
                raise ReleaseError(f"Release {r['canary_id']} is still on a canary. Promote it to all calls or stop it "
                                   "first.", 409)
            errors = [p for p in problems(row["body"]) if p["level"] == "error"]
            if errors:
                raise ReleaseError("Fix these first.", 400, errors)
            if _effective_body(row["body"]) == _effective_body(self._live_body(conn)):
                raise ReleaseError("Nothing has changed from what is live.")
            conn.execute("UPDATE content_releases SET status = 'published', published_at = now(), notes = %s, author = %s "
                         "WHERE id = %s", (notes.strip()[:1000], author[:80], row["id"]))
            if share == 100:
                self._set(conn, row["id"], None, 0, author, "publish to all calls", notes)
            else:
                self._set(conn, r["stable_id"], row["id"], share, author, f"publish to {share}% of calls", notes)
        self.load()

    def promote(self, author: str) -> None:
        with self.db.connection() as conn, conn.transaction():
            r = self._locked_rollout(conn)
            if not r["canary_id"]:
                raise ReleaseError("No canary is running.", 409)
            self._set(conn, r["canary_id"], None, 0, author, "promote canary to all calls")
        self.load()

    def set_share(self, share: int, author: str) -> None:
        if share not in CANARY_SHARES:
            raise ReleaseError(f"A canary gets {', '.join(map(str, CANARY_SHARES))}% of calls.")
        with self.db.connection() as conn, conn.transaction():
            r = self._locked_rollout(conn)
            if not r["canary_id"]:
                raise ReleaseError("No canary is running.", 409)
            self._set(conn, r["stable_id"], r["canary_id"], share, author, f"canary share {share}%", keep_since=True)
        self.load()

    def stop_canary(self, author: str) -> None:
        with self.db.connection() as conn, conn.transaction():
            r = self._locked_rollout(conn)
            if not r["canary_id"]:
                raise ReleaseError("No canary is running.", 409)
            self._set(conn, r["stable_id"], None, 0, author, f"stop canary (release {r['canary_id']})")
        self.load()

    def make_live(self, release_id: int | None, author: str) -> None:
        """Roll back (or forward) to a published release for every call; None = the catalogue as shipped in git."""
        with self.db.connection() as conn, conn.transaction():
            self._locked_rollout(conn)
            if release_id is not None and not conn.execute(
                    "SELECT 1 FROM content_releases WHERE id = %s AND status = 'published'", (release_id,)).fetchone():
                raise ReleaseError("That release doesn't exist or isn't published.", 404)
            self._set(conn, release_id, None, 0, author, f"make release {release_id or 0} live for all calls")
        self.load()

    def _locked_rollout(self, conn) -> dict:
        return conn.execute("SELECT * FROM content_rollout WHERE id = 1 FOR UPDATE").fetchone()

    def _set(self, conn, stable, canary, share, who, action, notes="", keep_since=False) -> None:
        since = "canary_since" if keep_since else ("now()" if canary else "NULL")
        conn.execute(f"UPDATE content_rollout SET stable_id = %s, canary_id = %s, canary_pct = %s, canary_since = {since}, "
                     "changed_at = clock_timestamp(), changed_by = %s WHERE id = 1", (stable, canary, share, who[:80]))
        conn.execute("INSERT INTO content_rollout_log (action, stable_id, canary_id, canary_pct, who, notes) "
                     "VALUES (%s, %s, %s, %s, %s, %s)", (action, stable, canary, share, who[:80], (notes or "")[:1000]))
        log(logger, "content rollout", action=action, stable=stable or 0, canary=canary, share=share)

    # ── for the editor ──

    def editor(self) -> dict:
        """Everything /journeys shows: the draft's tree, sentences, prompts and settings, each with the live version when
        the draft differs, plus what changed, what is wrong, and what is live."""
        row, r = self.draft(), self.rollout()
        body, live = row["body"], self.catalog(r["canary_id"] or r["stable_id"])
        draft = build(self.seed, body, row["id"], "Draft")
        auto = body.get("auto") or {}
        messages = {}
        for mid, entry in draft.messages.items():
            m = {"text": entry["text"], "note": entry.get("note", ""),
                 "placeholders": sorted(placeholders(entry["text"]["en"]) if isinstance(entry["text"]["en"], str) else [])}
            if entry.get("locked"):
                m["locked"] = entry["locked"]
            if live.messages[mid]["text"] != entry["text"]:
                m["live"] = live.messages[mid]["text"]
            if mid in auto:
                m["auto"] = auto[mid]
            messages[mid] = m
        prompts = {}
        for name, about in PROMPTS.items():
            value = draft.prompts[name]
            p = {"text": value, "about": about, "locked": name not in EDITABLE_PROMPTS + ("strategy",)}
            if value != live.prompts[name]:
                p["live"] = live.prompts[name]
            prompts[name] = p
        names = {k: draft.text(f"dept.{k}")["en"] for k in draft.settings["departments"]}
        languages = [lang | ({"changed": True} if lang != live.languages.get(code) else {})
                     | ({} if lang.get("source") else {"missing": sum(1 for mid in draft.messages if missing_in(draft, mid, [code]))})
                     for code, lang in draft.languages.items()]
        return {
            "draft": {"id": row["id"], "rev": row["rev"], "author": row["author"], "updated_at": row["updated_at"]},
            "changes": changes_between(live, draft), "problems": problems(body),
            "needs_translation": [mid for mid, _ in needs_translation(body)],
            "tree": draft.tree, "messages": messages, "aliases": SEED.aliases, "prompts": prompts,
            "settings": draft.settings, "live_settings": live.settings, "team_names": names,
            "editable_settings": {k: list(v) for k, v in EDITABLE_SETTINGS.items()},
            "pronunciation": SEED.pronunciation, "rollout": self.rollout_view(r), "canary_shares": list(CANARY_SHARES),
            "languages": languages, "max_translate": MAX_TRANSLATE,
        }

    def rollout_view(self, r: dict | None = None) -> dict:
        r = r or self.rollout()
        return {"stable": r["stable_id"] or 0, "canary": r["canary_id"], "share": r["canary_pct"],
                "since": r["canary_since"], "changed_at": r["changed_at"], "changed_by": r["changed_by"]}

    def history(self) -> dict:
        with self.db.connection() as conn:
            releases = conn.execute(
                "SELECT id, notes, author, published_at, body FROM content_releases WHERE status = 'published' "
                "ORDER BY id DESC LIMIT 50").fetchall()
            events = conn.execute("SELECT * FROM content_rollout_log ORDER BY id DESC LIMIT 30").fetchall()
        for r in releases:
            r["changes"] = len(changes_between(content.SEED, build(self.seed, r.pop("body"), r["id"])))
        return {"releases": releases, "log": events}

    def stats(self) -> dict | None:
        """Main release vs canary over the same time window (since the canary started): the A/B comparison."""
        r = self.rollout()
        if not r["canary_id"] or not r["canary_since"]:
            return None
        with self.db.connection() as conn:
            rows = conn.execute("""
                SELECT data->'content'->>'arm' AS arm, count(*) AS turns, count(DISTINCT session_id) AS calls,
                       percentile_cont(0.5) WITHIN GROUP (ORDER BY (data->'timings'->>'first_reply_ms')::float) AS p50_ms,
                       count(*) FILTER (WHERE outcome IN ('answered', 'action_done', 'ticket', 'callback', 'smalltalk')) AS done,
                       count(*) FILTER (WHERE outcome IN ('refused', 'error', 'action_failed')) AS failed,
                       count(*) FILTER (WHERE outcome = 'handoff') AS handoff
                FROM traces WHERE created_at >= %s AND data ? 'content' GROUP BY 1""", (r["canary_since"],)).fetchall()
            judged = conn.execute("""
                SELECT t.arm, count(*) AS judged, avg(c.score)::float AS score
                FROM call_reviews c JOIN (SELECT DISTINCT session_id, data->'content'->>'arm' AS arm FROM traces
                                          WHERE created_at >= %s AND data ? 'content') t USING (session_id)
                WHERE c.score IS NOT NULL GROUP BY 1""", (r["canary_since"],)).fetchall()
        out = {arm: {"turns": 0, "calls": 0, "p50_ms": None, "done": 0, "failed": 0, "handoff": 0, "judged": 0, "score": None}
               for arm in ("stable", "canary")}
        for row in rows:
            if row["arm"] in out:
                out[row["arm"]].update({k: row[k] for k in ("turns", "calls", "p50_ms", "done", "failed", "handoff")})
        for row in judged:
            if row["arm"] in out:
                out[row["arm"]].update(judged=row["judged"], score=row["score"])
        return {"since": r["canary_since"], "share": r["canary_pct"], **out}


def changes_between(live: Catalog, draft: Catalog) -> list[dict]:
    """What a draft (or release) changes compared with what is live, for the review list."""
    out = []
    for mid, entry in draft.messages.items():
        before, after = live.messages[mid]["text"], entry["text"]
        if before != after:
            out.append({"area": "message", "id": mid, "before": before.get("en"), "after": after.get("en"),
                        "translation_only": before.get("en") == after.get("en")})
    for name in EDITABLE_PROMPTS + ("strategy",):
        if draft.prompts[name] != live.prompts[name]:
            out.append({"area": "prompt", "id": name})
    for section in EDITABLE_SETTINGS:
        for key, value in draft.settings[section].items():
            if value != live.settings[section][key]:
                out.append({"area": "setting", "id": f"{section}.{key}", "before": live.settings[section][key], "after": value})
    for code in dict.fromkeys(list(live.languages) + list(draft.languages)):
        if live.languages.get(code) != draft.languages.get(code):
            lang = draft.languages.get(code) or live.languages[code]
            what = "added" if code not in live.languages else "removed" if code not in draft.languages else "writing style changed"
            out.append({"area": "language", "id": code, "before": lang["name"], "after": what})
    if draft.tree != live.tree:
        out.append({"area": "tree", "id": "tree"})
    return out
