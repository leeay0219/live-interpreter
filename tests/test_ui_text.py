"""Interface languages: every Korean string the interface shows has a translation in each locale, and the server picks
the language the browser asked for. Run alone with `.venv/bin/python -m unittest tests.test_ui_text`."""
import io
import json
import re
import sys
import tokenize
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import ui_text  # noqa: E402

HANGUL = re.compile(r"[\uac00-\ud7a3]")
STATIC = ROOT / "static"
HTML_FILES = ["studio.html", "captions.html"]
JS_FILES = [f for f in sorted(STATIC.glob("*.js")) if f.name not in ("i18n.js", "pcm-worklet.js")]
# server modules whose Korean strings reach the browser (routes, login page, background job stages)
PY_FILES = ["routes_live.py", "routes_materials.py", "routes_session.py", "routes_records.py", "access.py", "server.py",
            "materials.py", "deck_context.py"]
ATTRS = ("title", "placeholder", "aria-label", "alt")
# Korean that is not interface text: a device-label pattern in JS, and model-facing text in deck_context.py
NOT_UI = {"내장"}
NOT_UI_KEYS = {"{}쪽: {}"}
PLACEHOLDER = re.compile(r"\{[^{}]*\}")


def generic(key: str) -> str:
    """Placeholders compared by position only: Python f-strings name them differently from the locale keys."""
    return PLACEHOLDER.sub("{}", key)


class _Html(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.keys, self.scripts, self._script = [], [], False

    def handle_starttag(self, tag, attrs):
        self._script = tag == "script"
        for name, value in attrs:
            if name in ATTRS and value and HANGUL.search(value):
                self.keys.append(value)

    def handle_endtag(self, tag):
        self._script = False

    def handle_data(self, data):
        if self._script:
            self.scripts.append(data)
        elif HANGUL.search(data):
            self.keys.append(data.strip())


JS_STRING = re.compile(r'"((?:[^"\\\n]|\\.)*?[\uac00-\ud7a3](?:[^"\\\n]|\\.)*?)"|\'((?:[^\'\\\n]|\\.)*?[\uac00-\ud7a3](?:[^\'\\\n]|\\.)*?)\'')
JS_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)


def js_keys(source: str) -> tuple[set[str], list[str]]:
    """Korean string literals in JS, and Korean left outside any literal (which no translation can reach)."""
    code = JS_COMMENT.sub("", source)
    keys = {bytes(m.group(1) or m.group(2), "utf-8").decode("unicode_escape") if "\\" in (m.group(1) or m.group(2)) else (m.group(1) or m.group(2))
            for m in JS_STRING.finditer(code)}
    rest = JS_STRING.sub("", code)
    stray = [l.strip() for l in rest.splitlines() if HANGUL.search(l) and not any(w in l for w in NOT_UI)]
    return keys, stray


def py_keys(path: Path) -> set[str]:
    """Korean string literals in a module; f-string expressions become positional placeholders. Docstrings are skipped."""
    source = path.read_text()
    lines = source.splitlines(keepends=True)
    offsets = [0]
    for l in lines:
        offsets.append(offsets[-1] + len(l))
    keys, fstart = set(), None
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.FSTRING_START:
            fstart = tok.start
        elif tok.type == tokenize.FSTRING_END and fstart:
            raw = source[offsets[fstart[0] - 1] + fstart[1]:offsets[tok.end[0] - 1] + tok.end[1]]
            fstart = None
            if HANGUL.search(raw):
                body = raw[len(re.match(r"[a-zA-Z]*", raw).group(0)):][1:-1]
                keys.add(re.sub(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", "{}", body))
        elif tok.type == tokenize.STRING and HANGUL.search(tok.string):
            text = tok.string
            if text.lstrip("rbuRBU").startswith(('"""', "'''")):
                continue
            body = text[len(re.match(r"[a-zA-Z]*", text).group(0)):][1:-1]
            keys.add(body.replace('\\"', '"').replace("\\'", "'"))
    return keys


def interface_keys() -> dict[str, set[str]]:
    """Korean interface text → the files it appears in."""
    found: dict[str, set[str]] = {}
    for name in HTML_FILES:
        p = _Html()
        p.feed((STATIC / name).read_text())
        for k in p.keys:
            found.setdefault(k, set()).add(name)
        for script in p.scripts:
            keys, _ = js_keys(script)
            for k in keys:
                found.setdefault(k, set()).add(name)
    for f in JS_FILES:
        keys, _ = js_keys(f.read_text())
        for k in keys:
            found.setdefault(k, set()).add(f.name)
    for name in PY_FILES:
        for k in py_keys(ROOT / name):
            if generic(k) not in NOT_UI_KEYS:
                found.setdefault(k, set()).add(name)
    for f in sorted((ROOT / "skills").glob("*.md")):  # chip title and tooltip in 고급 설정 (README.md has no front matter)
        front = re.match(r"---\n(.*?)\n---", f.read_text(), re.S)
        for m in re.finditer(r"^(?:title|description):\s*(.+)$", front.group(1) if front else "", re.M):
            found.setdefault(m.group(1).strip(), set()).add(f"skills/{f.name}")
    return found


class LocaleCompleteness(unittest.TestCase):
    def test_every_interface_string_is_translated_in_every_locale(self):
        wanted = {generic(k): k for k in interface_keys()}
        for code, locale in ui_text.LANGUAGES.items():
            if code == ui_text.DEFAULT:
                continue
            have = {generic(k) for k in locale["strings"]}
            missing = sorted(wanted[k] for k in wanted if k not in have)
            self.assertEqual(missing, [], f"{code}.json lacks {len(missing)} strings:\n" + "\n".join(missing))
            stale = sorted(k for k in locale["strings"] if generic(k) not in wanted)
            self.assertEqual(stale, [], f"{code}.json has strings no longer used:\n" + "\n".join(stale))
            empty = sorted(k for k, v in locale["strings"].items() if not v.strip())
            self.assertEqual(empty, [], f"{code}.json has empty translations: {empty}")

    def test_korean_in_scripts_is_inside_string_literals(self):
        for f in JS_FILES:
            _, stray = js_keys(f.read_text())
            self.assertEqual(stray, [], f"{f.name}: Korean outside a string literal (template text?):\n" + "\n".join(stray))

    def test_locales_declare_name_and_html_lang(self):
        for code, locale in ui_text.LANGUAGES.items():
            self.assertTrue(locale["name"] and locale["html_lang"], code)
        self.assertEqual(ui_text.languages()[0]["code"], "ko")
        self.assertGreaterEqual(len(ui_text.languages()), 3)
        self.assertEqual([l["code"] for l in ui_text.languages()], ["ko", "en", "zh"])


class _Req:
    def __init__(self, cookie=None, accept=""):
        self.cookies = {ui_text.COOKIE: cookie} if cookie else {}
        self.headers = {"Accept-Language": accept} if accept else {}


class ServerMessages(unittest.TestCase):
    def test_language_from_cookie_then_header_then_korean(self):
        self.assertEqual(ui_text.pick(_Req()), "ko")
        self.assertEqual(ui_text.pick(_Req(accept="en-US,en;q=0.9")), "en")
        self.assertEqual(ui_text.pick(_Req(accept="zh-TW,zh;q=0.9,en;q=0.8")), "zh")
        self.assertEqual(ui_text.pick(_Req(accept="fr-FR,fr;q=0.9")), "ko")
        self.assertEqual(ui_text.pick(_Req(cookie="en", accept="zh-TW")), "en")
        self.assertEqual(ui_text.pick(_Req(cookie="xx", accept="zh-TW")), "zh")
        self.assertEqual(ui_text.pick(None), "ko")

    def test_messages_and_placeholders(self):
        self.assertEqual(ui_text.msg(_Req(), "기록이 없습니다."), "기록이 없습니다.")
        self.assertEqual(ui_text.msg(_Req(cookie="en"), "기록이 없습니다."), ui_text.LANGUAGES["en"]["strings"]["기록이 없습니다."])
        self.assertNotEqual(ui_text.msg(_Req(cookie="zh"), "기록이 없습니다."), "기록이 없습니다.")
        self.assertEqual(ui_text.msg(_Req(cookie="en"), "이 글은 없는 키 {n}", n=3), "이 글은 없는 키 3")  # unknown text stays Korean, filled
        self.assertEqual(ui_text.html_lang(_Req(cookie="zh")), "zh-Hant-TW")


if __name__ == "__main__":
    for key, files in sorted(interface_keys().items(), key=lambda kv: sorted(kv[1])):
        print(f"{'|'.join(sorted(files))}\t{key}")
