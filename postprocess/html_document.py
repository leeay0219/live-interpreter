"""Self-contained, passive HTML documents from the same Markdown used for Word."""
from html import escape
from html.parser import HTMLParser
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit

HERE = Path(__file__).parent
CSP = "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"


class DocumentBody(HTMLParser):
    """Keep document markup; drop active content, remote resources and arbitrary attributes."""
    TAGS = frozenset("p h1 h2 h3 h4 h5 h6 ul ol li blockquote pre code strong em b i del "
                     "table thead tbody tfoot tr th td hr br a dl dt dd sup sub".split())
    BLOCKED = frozenset("script style iframe object embed svg math".split())

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.contents, self.title_parts = [], [], []
        self.capture_title = self.found_title = False
        self.heading = None
        self.blocked = []

    def handle_starttag(self, tag, attrs):
        if tag in self.BLOCKED or self.blocked:
            self.blocked.append(tag)
            return
        if tag == "h1" and not self.found_title:
            self.capture_title = self.found_title = True
            return
        if self.capture_title or tag not in self.TAGS:
            return
        attrs = dict(attrs)
        safe = ""
        if tag == "h2":
            identity = f"section-{len(self.contents) + 1}"
            self.heading = [identity, []]
            safe = f' id="{identity}"'
        elif tag == "a":
            href = attrs.get("href", "").strip()
            try:
                allowed = href.startswith("#") or urlsplit(href).scheme.lower() in ("https", "http")
            except ValueError:
                allowed = False
            if allowed:
                safe = f' href="{escape(href, quote=True)}" rel="noopener noreferrer"'
        elif tag in ("td", "th"):
            for name in ("rowspan", "colspan"):
                value = attrs.get(name, "")
                if value.isdigit() and 1 <= int(value) <= 100:
                    safe += f' {name}="{int(value)}"'
        if tag == "table":
            self.parts.append('<div class="table-wrap">')
        self.parts.append(f"<{tag}{safe}>")

    def handle_endtag(self, tag):
        if self.blocked:
            if tag in self.blocked:
                self.blocked = self.blocked[:self.blocked.index(tag)]
            return
        if self.capture_title:
            if tag == "h1":
                self.capture_title = False
            return
        if tag not in self.TAGS or tag in ("br", "hr"):
            return
        self.parts.append(f"</{tag}>")
        if tag == "table":
            self.parts.append("</div>")
        if tag == "h2" and self.heading:
            self.contents.append((self.heading[0], "".join(self.heading[1])))
            self.heading = None

    def handle_data(self, data):
        if self.blocked:
            return
        if self.capture_title:
            self.title_parts.append(data)
            return
        if self.heading:
            self.heading[1].append(data)
        self.parts.append(escape(data))


def render_document(markdown, *, lang="ko", kind="summary"):
    fragment = subprocess.run(
        ["pandoc", "--sandbox", "--from=gfm-raw_html", "--to=html5", "--wrap=none"],
        input=markdown, text=True, capture_output=True, check=True, timeout=20).stdout
    parsed = DocumentBody()
    parsed.feed(fragment)
    parsed.close()
    labels = {"ko": {"summary": "발표 요약", "minutes": "회의록", "record": "전체 기록"},
                "en": {"summary": "Presentation summary", "minutes": "Meeting notes", "record": "Session transcript"}}
    lang = lang if lang in labels else "ko"
    label = labels[lang].get(kind, labels[lang]["summary"])
    title = "".join(parsed.title_parts).strip() or label
    contents = ""
    if len(parsed.contents) > 2:
        items = "".join(f'<li><a href="#{identity}">{escape(text)}</a></li>' for identity, text in parsed.contents)
        contents = f'<details class="contents"><summary>{"목차" if lang == "ko" else "Contents"}</summary><ol>{items}</ol></details>'
    css = (HERE / "document.css").read_text()
    footer = ("전체 기록의 원문과 자막은 수정하지 않았습니다." if kind == "record" else "실시간 받아쓰기 기록을 바탕으로 정리했습니다.")
    if lang == "en":
        footer = "Original transcript and captions are unchanged." if kind == "record" else "Prepared from the live speech recognition transcript."
    return f"""<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="{escape(CSP, quote=True)}">
<title>{escape(title)}</title>
<style>{css}</style>
</head>
<body>
<main class="document">
<header class="document-header"><p class="document-kind">{escape(label)}</p><h1>{escape(title)}</h1></header>
{contents}
<article>{"".join(parsed.parts)}</article>
<footer class="document-footer">{footer}</footer>
</main>
</body>
</html>
"""


def clean_prose(markdown):
    """Normalize editorial punctuation, preserving quotations, code, formulas and link destinations."""
    lines, fence = [], None
    for line in markdown.splitlines():
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            token = marker[1]
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            lines.append(line)
            continue
        if fence or re.match(r"^\s*>|^(?: {4}|\t)", line):
            lines.append(line)
            continue
        protected = re.split(r'(`+[^`]*`+|\$[^$]+\$|\]\([^)\n]*\)|"[^"\n]*"|“[^”\n]*”)', line)
        for i in range(0, len(protected), 2):
            protected[i] = re.sub(r"(?<=\d)[\u2013\u2014](?=\d)", "-", protected[i])
            protected[i] = re.sub(r"\s*[\u2014\u2013\u00b7\u30fb\u2027]\s*", ", ", protected[i])
        lines.append("".join(protected).rstrip())
    return "\n".join(lines) + "\n"
