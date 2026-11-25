"""The journey catalogue: every fixed sentence the agent says, the business settings and the prompts, in one place.

The seed is `brain/content/`, kept in git: catalog.json (English, the source, plus the tree, languages and settings),
languages/<code>.json (one file per other language: message id -> text) and prompts/*.md. A product manager's edits are saved as
releases (services/releases.py): each release is a set of changes on top of the seed. A release can be live for every
call, or for a share of calls (canary), so two releases can be live at once.

Code never holds the text itself. It reads through `current()`, the catalogue of the release this call is on:
`use()` sets it at the start of each turn (a ContextVar, so it follows the call into `asyncio.to_thread`), and anything
outside a call (desk pages, the post-call judge) gets the main live release. Lookups are plain dict reads: no I/O.

Things marked `locked` (grammar word lists, the understanding prompt, the answer rules, pronunciation) always come from
the seed: only a developer changes them, in git.
"""

import copy
import json
import re
import string
import time
import uuid
from collections.abc import Iterator, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path

from app.config import ROOT

SEED_DIR = ROOT / "content"
EDITABLE_PROMPTS = ("answer_style", "judge", "summary")  # prompts/*.md a release may change
LOCKED_PROMPTS = ("answer_rules", "understand", "translate")
PIN_HOURS = 3  # a call keeps its release this long (calls are minutes long)


def placeholders(text) -> set[str]:
    """{dept}, {ref} ... in a sentence: values the code fills in, so an edit must keep exactly these."""
    if not isinstance(text, str):
        return set()
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def read_seed(folder: Path = SEED_DIR) -> dict:
    """The seed as one structure: each message's `text` becomes {lang: text}, English from catalog.json and every other
    language from its own file (a message a language file doesn't have falls back to English when spoken)."""
    data = json.loads((folder / "catalog.json").read_text(encoding="utf-8"))
    for m in data["messages"].values():
        m["text"] = {"en": m["text"]}
    for lang in data["languages"]:
        path = folder / "languages" / f"{lang['code']}.json"
        if lang.get("source") or not path.is_file():
            continue
        for mid, text in json.loads(path.read_text(encoding="utf-8")).items():
            if mid in data["messages"]:
                data["messages"][mid]["text"][lang["code"]] = text
    # \r\n -> \n: a Windows checkout must not change a prompt (the replay cache and the model see the exact text)
    data["prompts"].update({p.stem: p.read_text(encoding="utf-8").replace("\r\n", "\n").removesuffix("\n")
                            for p in sorted((folder / "prompts").glob("*.md"))})
    return data


@dataclass(frozen=True)
class Catalog:
    release: int  # 0 = the seed as shipped in git
    label: str
    messages: dict  # id -> {"text": {lang: text}, "note"?, "locked"?}
    prompts: dict
    settings: dict
    tree: list
    pronunciation: dict
    languages: dict  # code -> {code, name, style, script?, spoken?, source?, added?}; English first
    aliases: dict = field(default_factory=dict)

    def text(self, mid: str) -> dict:
        """{lang: text} of one message (an alias reads the message it points to)."""
        return self.messages[self.aliases.get(mid, mid)]["text"]

    def answer_system(self, language_instruction: str) -> str:
        return self.prompts["answer_style"] + self.prompts["answer_rules"].format(language=language_instruction)


def build(seed: dict, changes: dict | None = None, release: int = 0, label: str = "As shipped") -> Catalog:
    """The seed with a release's changes on top. Changes to something the code no longer has are ignored; anything new
    in the seed that the release doesn't mention comes from the seed (a developer added it after the release)."""
    changes = changes or {}
    languages = languages_of(seed, changes)
    messages = copy.deepcopy(seed["messages"])
    for mid, text in (changes.get("messages") or {}).items():
        if mid in messages and not messages[mid].get("locked") and isinstance(text, dict):
            messages[mid]["text"].update({lang: t for lang, t in text.items() if lang in languages and isinstance(t, str)})
    prompts = dict(seed["prompts"])
    for name, value in (changes.get("prompts") or {}).items():
        if name in EDITABLE_PROMPTS and isinstance(value, str):
            prompts[name] = value
        elif name == "strategy" and isinstance(value, dict):
            prompts["strategy"] = seed["prompts"]["strategy"] | {k: v for k, v in value.items() if k in seed["prompts"]["strategy"]}
    settings = copy.deepcopy(seed["settings"])
    for section, rows in (changes.get("settings") or {}).items():
        for key, value in (rows or {}).items():
            if key not in settings.get(section, {}):
                continue
            if isinstance(settings[section][key], dict):
                settings[section][key].update({f: v for f, v in value.items() if f in EDITABLE_SETTINGS.get(section, ())})
            elif section in EDITABLE_SETTINGS:
                settings[section][key] = value
    tree = place_all(changes.get("tree") or seed["tree"], seed["tree"], messages)
    return Catalog(release, label, messages, prompts, settings, tree, seed["pronunciation"], languages, seed.get("aliases", {}))


def languages_of(seed: dict, changes: dict | None = None) -> dict:
    """The seed's languages plus any a release added (a name and a writing style: kept ready, but not spoken by the agent
    until a developer adds speech and understanding for it), with any writing style a release changed."""
    out = {lang["code"]: dict(lang) for lang in seed["languages"]}
    for code, value in ((changes or {}).get("languages") or {}).items():
        if not isinstance(value, dict):
            continue
        if code in out:
            if isinstance(value.get("style"), str) and not out[code].get("source"):
                out[code]["style"] = value["style"]
        elif isinstance(value.get("name"), str) and isinstance(value.get("style"), str):
            out[code] = {"code": code, "name": value["name"], "style": value["style"], "spoken": False, "added": True}
    return out


EDITABLE_SETTINGS = {"departments": ("priority", "always_open", "handles"), "tat_days": (), "issues": ("department",)}


def place_all(tree: list, seed_tree: list, messages: dict) -> list:
    """The release's tree with every message placed exactly once: unknown ids dropped, and messages it doesn't place
    (new in the seed) put where the seed has them if that journey and step still exist, else under "Not sorted yet"."""
    tree = copy.deepcopy(tree)
    seen: set = set()
    for d in tree:
        for j in d.get("journeys", []):
            for s in j.get("steps", []):
                s["messages"] = [m for m in s.get("messages", []) if m in messages and not (m in seen or seen.add(m))]
    missing = [m for m in messages if m not in seen]
    if not missing:
        return tree
    home = {m: (d["id"], j["id"], s["name"]) for d in seed_tree for j in d["journeys"] for s in j["steps"] for m in s["messages"]}
    steps = {(d["id"], j["id"], s["name"]): s for d in tree for j in d.get("journeys", []) for s in j.get("steps", [])}
    unsorted = []
    for m in missing:
        target = steps.get(home.get(m, ()))
        (target["messages"] if target is not None else unsorted).append(m)
    if unsorted:
        tree.append({"id": "unsorted", "name": "Not sorted yet", "journeys": [
            {"id": "unsorted", "name": "Not sorted yet", "steps": [{"name": "New or moved out", "messages": unsorted}]}]})
    return tree


# ───────── what is live, and which release a call is on ─────────

SEED = build(read_seed())
_live = {"stable": SEED, "canary": None, "pct": 0}
_pins: dict[str, tuple[Catalog, float]] = {}
_call: ContextVar[Catalog | None] = ContextVar("catalog", default=None)


def current() -> Catalog:
    """The catalogue for this call (set by `use`), else the main live release."""
    return _call.get() or _live["stable"]


def use(catalog: Catalog) -> None:
    _call.set(catalog)


def phrase(mid: str, lang: str) -> str:
    """One message in the caller's language (English if it has no such language), not filled in."""
    texts = current().text(mid)
    return texts.get(lang, texts["en"])


def set_live(stable: Catalog, canary: Catalog | None = None, pct: int = 0) -> None:
    _live.update(stable=stable, canary=canary if pct > 0 else None, pct=pct if canary else 0)


def live() -> dict:
    return dict(_live)


def arm_of(session_id: str | None, pct: int) -> str:
    """'canary' for about pct% of calls, decided from the call id alone (the same call always gets the same answer)."""
    if not session_id or pct <= 0:
        return "stable"
    try:
        n = uuid.UUID(str(session_id)).int
    except ValueError:
        n = int.from_bytes(str(session_id).encode()[-8:], "big")
    return "canary" if n % 100 < pct else "stable"


def for_session(session_id: str | None) -> Catalog:
    """The release this call is on. The first turn picks it; later turns keep it even if the rollout changes meanwhile
    (on this copy of the brain: a voice call stays on one copy), so a caller never hears two versions in one call."""
    if not session_id:
        return _live["stable"]
    now = time.monotonic()
    pinned = _pins.get(session_id)
    if pinned and now - pinned[1] < PIN_HOURS * 3600:
        return pinned[0]
    if len(_pins) > 5000:
        for sid in [s for s, (_c, at) in _pins.items() if now - at > PIN_HOURS * 3600]:
            del _pins[sid]
    catalog = _live["canary"] if _live["canary"] and arm_of(session_id, _live["pct"]) == "canary" else _live["stable"]
    _pins[session_id] = (catalog, now)
    return catalog


def release_of(catalog: Catalog) -> dict:
    """What a trace records: which release answered, and on which side of a canary (the A/B comparison reads this)."""
    return {"release": catalog.release, "arm": "canary" if catalog is _live["canary"] else "stable"}


class Table(Mapping):
    """A read-only view of one group of messages ("bank.", "journey."...) in the current catalogue, shaped like the
    old in-code tables: table[key] -> {lang: text}. Lets the existing lookups (fill, say, phrase_text) stay as they were."""

    def __init__(self, prefix: str):
        self.prefix = prefix + "."

    def __getitem__(self, key: str) -> dict:
        try:
            return current().text(self.prefix + key)
        except KeyError:
            raise KeyError(key) from None

    def __iter__(self) -> Iterator[str]:
        n = len(self.prefix)
        cat = current()
        return iter([m[n:] for m in list(cat.messages) + list(cat.aliases) if m.startswith(self.prefix)])

    def __len__(self) -> int:
        return sum(1 for _ in self)


class Departments(Mapping):
    """desk.DEPARTMENTS as before: key -> (name, priority, open 24x7?, what they handle). The keys are fixed in code."""

    def __getitem__(self, key: str) -> tuple:
        cat = current()
        d = cat.settings["departments"][key]
        return cat.text(f"dept.{key}")["en"], d["priority"], d["always_open"], d["handles"]

    def __iter__(self) -> Iterator[str]:
        return iter(SEED.settings["departments"])

    def __len__(self) -> int:
        return len(SEED.settings["departments"])


class Setting(Mapping):
    """One settings section (tat_days, issues) of the current catalogue; `shape` turns a row into what the code expects."""

    def __init__(self, section: str, shape=lambda row: row):
        self.section, self.shape = section, shape

    def __getitem__(self, key: str):
        return self.shape(current().settings[self.section][key])

    def __iter__(self) -> Iterator[str]:
        return iter(SEED.settings[self.section])

    def __len__(self) -> int:
        return len(SEED.settings[self.section])


_DIGITS = re.compile(r"\d+(?:[,.]\d+)*")


def numbers_in(text) -> set[str]:
    """The figures written in a sentence (1930, 0101, 7): a fixed sentence may only keep the ones it already had."""
    return set(_DIGITS.findall(text)) if isinstance(text, str) else set()


def write_seed(catalog: Catalog, folder: Path = SEED_DIR) -> int:
    """Write a release's wording, settings, tree and editable prompts back into brain/content/ (the `content-export`
    command), so what the product manager made live can be committed to git. Returns how many files changed."""
    data = json.loads((folder / "catalog.json").read_text(encoding="utf-8"))
    for mid, entry in catalog.messages.items():
        if mid in data["messages"] and not entry.get("locked"):
            data["messages"][mid]["text"] = entry["text"]["en"]
    data["languages"] = [{k: v for k, v in lang.items() if k != "added"} for lang in catalog.languages.values()]
    data["tree"] = [d for d in catalog.tree if d["id"] != "unsorted"] + [d for d in catalog.tree if d["id"] == "unsorted"]
    data["settings"] = catalog.settings
    data["prompts"]["strategy"] = catalog.prompts["strategy"]
    files = {folder / "catalog.json": json.dumps(data, ensure_ascii=False, indent=1) + "\n"}
    files |= {folder / "prompts" / f"{name}.md": catalog.prompts[name] + "\n" for name in EDITABLE_PROMPTS}
    for code, lang in catalog.languages.items():  # one file per language, English excepted (it is in catalog.json)
        if not lang.get("source"):
            texts = {mid: e["text"][code] for mid, e in catalog.messages.items() if code in e["text"]}
            files[folder / "languages" / f"{code}.json"] = json.dumps(texts, ensure_ascii=False, indent=1) + "\n"
    changed = 0
    for path, text in files.items():
        if not path.is_file() or path.read_text(encoding="utf-8").replace("\r\n", "\n") != text:
            path.write_text(text, encoding="utf-8", newline="\n")
            changed += 1
    return changed
