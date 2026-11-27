"""Interface language for server-rendered text: error messages, the login page and download headers.

Korean source text is the key. static/locales/<code>.json holds one language each ({"meta": {...}, "strings": {...}})
and is read by both this module and static/i18n.js, so adding a language is adding one file. The browser's choice
arrives in the `ui_lang` cookie (set by i18n.js); without it the Accept-Language header decides, then Korean.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

LOCALES = Path(__file__).parent / "static" / "locales"
DEFAULT = "ko"
COOKIE = "ui_lang"


def _load() -> dict[str, dict]:
    out = {}
    for f in sorted(LOCALES.glob("*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        meta = data.get("meta", {})
        out[f.stem] = {"code": f.stem, "name": meta.get("name", f.stem), "html_lang": meta.get("html_lang", f.stem),
                       "strings": data.get("strings", {})}
    if DEFAULT not in out:
        out[DEFAULT] = {"code": DEFAULT, "name": "한국어", "html_lang": "ko", "strings": {}}
    return out


LANGUAGES = _load()


def languages() -> list[dict]:
    """[{code, name, html_lang}] for the language selector: Korean first, then the rest in code order."""
    rest = sorted(c for c in LANGUAGES if c != DEFAULT)
    return [{k: v for k, v in LANGUAGES[c].items() if k != "strings"} for c in [DEFAULT] + rest]


def normalize(code: str | None) -> str | None:
    if not code:
        return None
    c = code.strip().lower()
    if c in LANGUAGES:
        return c
    base = c.split("-")[0]
    return base if base in LANGUAGES else None


def pick(request) -> str:
    """The interface language of a request: the browser's saved choice (cookie), else Accept-Language, else Korean."""
    chosen = normalize(request.cookies.get(COOKIE)) if request is not None else None
    if chosen:
        return chosen
    header = request.headers.get("Accept-Language", "") if request is not None else ""
    for part in header.split(","):
        code = normalize(part.split(";")[0])
        if code:
            return code
    return DEFAULT


def text(lang: str, source: str, **vars) -> str:
    """`source` (Korean) in `lang`, with {name} placeholders filled. Unknown text stays Korean."""
    out = LANGUAGES.get(lang, LANGUAGES[DEFAULT])["strings"].get(source, source) if lang != DEFAULT else source
    return re.sub(r"\{(\w+)\}", lambda m: str(vars[m.group(1)]) if m.group(1) in vars else m.group(0), out) if vars else out


def msg(request, source: str, **vars) -> str:
    """`source` in the language of `request`. Use for every user-facing string a route returns."""
    return text(pick(request), source, **vars)


def html_lang(request) -> str:
    return LANGUAGES[pick(request)]["html_lang"]
