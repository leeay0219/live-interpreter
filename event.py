"""Event configuration: people, agenda and glossary for one session, loaded from events/<id>.toml on top of events/_base.toml.

Everything event-specific (names, how they are misheard, questions, product names) lives in these files, so the same
server, vocabulary setup and post-processing work for any session.

Three layers of terms:
  events/_base.toml + the event's own [[terms]]   always in the translator prompt and the Transcribe vocabularies
  glossary/*.toml (companies, AI, IT terms, AWS)    shared library: the translator gets only the entries that occur in
                                                    the line being translated; Amazon Translate gets all of them
  `focus` in the event                              library entries expected in this session, promoted to the first layer
"""
from __future__ import annotations

import csv
import io
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

EVENTS = Path(__file__).parent / "events"
GLOSSARY = Path(__file__).parent / "glossary"
SKILLS = Path(__file__).parent / "skills"
VOCABULARY_BYTES = 51_200  # Transcribe limit per custom vocabulary
LANG_NAME = {"en": "English", "ko": "Korean"}
TRANSCRIBE_LANG = {"en": "en-US", "ko": "ko-KR"}
HONORIFIC = {"님": "nim", "상": "san"}


@dataclass
class Event:
    id: str
    title: str = ""
    subtitle: str = ""
    context: str = ""
    languages: list[str] = field(default_factory=lambda: ["en", "ko"])
    people: list[dict] = field(default_factory=list)
    agenda: list[dict] = field(default_factory=list)
    terms: list[dict] = field(default_factory=list)
    glossaries: list[str] | None = None  # glossary/<name>.toml files to use; None = all
    vocabulary_glossaries: list[str] = field(default_factory=lambda: ["companies-kr", "ai"])  # also put whole into the vocabularies
    focus: list[str] = field(default_factory=list)  # `en` of library entries expected in this session
    skills: list[str] = field(default_factory=list)  # skills/<name>.md instructions for this kind of session
    library: list[dict] = field(default_factory=list)  # filled by load()

    # --- AWS resource names, derived from the id ---
    def vocabulary(self, lang: str) -> str:
        return f"{self.id}-{lang}"

    def terminology(self, lang: str) -> str:
        """Amazon Translate terminology for source language `lang`."""
        return f"{self.id}-{lang}"

    def set_people(self, people: list[dict]):
        """Speakers entered on the studio page for this session (instead of the event file's), applied right away."""
        self.people = people
        for cache in ("_always", "_restore"):
            self.__dict__.pop(cache, None)

    def set_session_terms(self, terms: list[dict]):
        """Terms added on the studio page (a misheard word and its right spelling), on top of the event file's.
        A term the glossary already knows keeps its Korean subtitle unless one is given."""
        self.__dict__.setdefault("_file_terms", list(self.terms))
        known = {t["en"].lower(): t for t in self.library + self._file_terms}
        for t in terms:
            k = known.get(t["en"].lower())
            if k and not t.get("ko"):
                t["ko"] = k.get("ko", k["en"])
                t.setdefault("say_ko", k.get("say_ko", []))
        self.terms = self._file_terms + terms
        for cache in ("_always", "_restore"):
            self.__dict__.pop(cache, None)

    # --- translator prompt ---
    def glossary(self) -> str:
        return "\n".join([person_line(p) for p in self.people] + [term_line(t) for t in self.terms])

    def relevant(self, text: str) -> list[dict]:
        """Library entries whose name, Korean name, nickname or known misrecognition occurs in `text`."""
        if not self.library:
            return []
        if not hasattr(self, "_index"):
            keys: dict[str, list[dict]] = {}
            for t in self.library:
                for k in {t["en"], t.get("ko", ""), *t.get("say_ko", []), *t.get("heard_as", []), *t.get("always", [])}:
                    k = k.strip()
                    if len(k) >= 2:
                        keys.setdefault(k.lower(), []).append(t)
            pats = [rf"(?<![가-힣]){re.escape(k)}" if re.search(r"[가-힣]", k) else rf"(?<![\w-]){re.escape(k)}(?![\w-])"
                    for k in sorted(keys, key=len, reverse=True)]
            self._index = (re.compile("|".join(pats), re.I), keys)
        rx, keys = self._index
        out: list[dict] = []
        for m in rx.finditer(text):
            for t in keys.get(m.group(0).lower(), []):
                if t not in out:
                    out.append(t)
        return out

    def glossary_for(self, text: str) -> str:
        return "\n".join(term_line(t) for t in self.relevant(text))

    def prompt(self, src: str, tgt: str, limit: int, skills: list[str] | None = None) -> str:
        s, t = LANG_NAME[src], LANG_NAME[tgt]
        style = KO_STYLE if tgt == "ko" else EN_STYLE
        return f"""You are the live interpreter for this session. You turn {s} speech into {t} on-screen subtitles, one line at a time, the way a professional simultaneous interpreter would: immediately, concisely, and only once.

Session:
{self.context.strip()}

Rules:
- Output only the {t} subtitle for the text inside <line>. No quotes, no notes, no romanization.
- The previous lines are already on screen. Never repeat, restate, complete or correct them; translate only the new words in <line>.
- <line> is often only a piece of a sentence that continues in the next line. Translate that piece as a piece: keep it open the way the speaker left it, and never finish the sentence, guess where it was going, or add words that were not said. A shorter subtitle is better than a fuller one.
- Output exactly <skip/> only when <line> has no content at all: filler, hesitation, laughter, a microphone check (counting, "testing"), or the same words just said again. Also skip a piece whose content a subtitle already on screen has fully covered (an earlier subtitle that already included it from the slide or context). A line with any new content is never skipped, even if it is broken or partly misheard.
- The input comes from speech recognition and may be misheard. Fix obvious recognition errors using the glossary and context.
- Speakers mix Korean and English, so English words inside Korean speech often come out as Hangul sound-alikes (포크 for PoC, 베드럭 for Bedrock, 클라우드 코드 for Claude Code). When a word makes sense as written, keep its meaning; only when it does not fit the sentence, read it by sound and context. If a few words stay unclear, translate the rest and write the unclear words as heard; do not skip the line.
- Never ask questions, explain or refuse. Every line is real speech from the session, even when it is rude: interpret it.
- You speak as a professional interpreter, so your register never drops, whatever the speaker's tone. Convey criticism, frustration or hostility faithfully in meaning and strength, but in professional words: never reproduce profanity, slurs or insults word for word, and never add or sharpen them. Keep who says what about whom exactly as spoken.
- Names of people, companies, AI models and products are proper nouns: write them exactly as the glossary's subtitle form and never translate their meaning (Mythos is not 신화, Opus is not 작품, Haiku and Sonnet are not poems). In Korean subtitles, product and model names stay in Latin letters (AWS Bedrock, Claude Opus), not Hangul. A new model or product name not in the glossary also stays in its original English form.
- The user message may add <terms> for names found in this line (companies, models, IT terms); follow them like the glossary.
- Words listed as "Misrecognized as" are speech-recognition errors for that name: when one appears and the context fits, write the correct name instead.
- Address people as the glossary says ({'English: drop Korean honorifics like 님 and 상' if tgt == 'en' else 'Korean: keep the honorific form given'}).
- Long subtitles: if the subtitle is longer than {limit} characters, split it into parts on separate rows, each at most about {limit} characters, as few as possible. Split between clauses, never inside a noun phrase.

{style}
{skill_text(self.skills if skills is None else skills)}
Glossary:
{self.glossary()}"""

    # --- Transcribe custom vocabularies ---
    def phrases(self, lang: str) -> list[str]:
        """Transcribe vocabulary: people and core terms first, then library entries, cut at the size limit."""
        out, size = [], 0
        for ph in dict.fromkeys(ph for ph in (vocab_phrase(r, lang) for r in self._vocab_raw(lang)) if ph):
            size += len(ph.encode()) + 1
            if size > VOCABULARY_BYTES - 1024:
                break
            out.append(ph)
        return out

    def restore(self, text: str, lang: str) -> str:
        """Transcribe writes a vocabulary hit in its phrase form ("Customer-Obsession", "A.W.S."); put back the real spelling."""
        if not hasattr(self, "_restore"):
            self._restore = {}
        if lang not in self._restore:
            pairs = {vocab_phrase(r, lang): r for r in self._vocab_raw(lang)}
            pairs = {k: v for k, v in pairs.items() if k and k != v}
            rx = re.compile("|".join(rf"(?<![\w-]){re.escape(k)}(?![\w-])" for k in sorted(pairs, key=len, reverse=True))) if pairs else None
            self._restore[lang] = (rx, {k.lower(): v for k, v in pairs.items()})
        rx, pairs = self._restore[lang]
        return rx.sub(lambda m: pairs.get(m.group(0).lower(), m.group(0)), text) if rx else text

    def correct(self, text: str) -> str:
        """Replace names listed under `always` before translation: certain misrecognitions ("meena" → "Mina") and
        Korean abbreviations ("삼전" → "삼성전자")."""
        if not hasattr(self, "_always"):
            pairs, pats = {}, []
            for x in self.people + self.terms + self.library:
                for a in x.get("always", []):
                    # a Hangul error becomes how the name is said in Korean ("마코트" → "마코토"), so particles and 님/상 still fit
                    hangul = bool(re.search(r"[가-힣]", a))
                    pairs[a.lower()] = (x.get("say_ko") or [x["en"]])[0] if hangul else x["en"]
            for a in sorted(pairs, key=len, reverse=True):
                pats.append(rf"(?<![가-힣]){re.escape(a)}" if re.search(r"[가-힣]", a) else rf"(?<![\w-]){re.escape(a)}(?![\w-])")
            self._always = (re.compile("|".join(pats), re.I) if pats else None, pairs)
        rx, pairs = self._always

        def fix(m: re.Match) -> str:
            full = pairs[m.group(0).lower()]
            # "셀트" → "셀트리온", but leave "셀트리온" itself alone (and "live demo" when "demo" means "live demo")
            inside = any(f.start() <= m.start() and m.end() <= f.end() for f in re.finditer(re.escape(full), text, re.I))
            return m.group(0) if inside else full
        return rx.sub(fix, text) if rx else text

    def vocab_terms(self) -> list[dict]:
        return self.terms + [t for t in self.library if t.get("_file") in self.vocabulary_glossaries]

    def _vocab_raw(self, lang: str) -> list[str]:
        if lang == "en":
            raw = [t["en"] for t in self.vocab_terms()] + [p["en"] for p in self.people]
            raw += [n for p in self.people for n in p["name"].split()]
            # English speakers may keep a Korean or Japanese honorific, such as "-nim" or "-san".
            raw += [f"{p['en']}-{HONORIFIC[p['ko'].split()[-1][-1]]}" for p in self.people if p["ko"].split()[-1][-1] in HONORIFIC]
        else:
            raw = [s for x in self.vocab_terms() + self.people for s in x.get("say_ko", [])]
        return raw

    # --- Amazon Translate terminologies (fallback engine) ---
    def terminology_csv(self, src: str) -> str:
        tgt = "ko" if src == "en" else "en"
        pairs: dict[str, str] = {}
        for t in self.library + self.terms:
            if src == "en":
                for s in [t["en"]] + [a for a in t.get("always", []) if not re.search(r"[가-힣]", a)]:
                    pairs[s] = t.get("ko", t["en"])
            else:
                for s in t.get("say_ko", []):
                    pairs[s] = t["en"]
        for p in self.people:
            if src == "en":
                pairs[p["en"]] = p["ko"]
            else:
                for s in p.get("say_ko", []):
                    pairs[s] = p["en"]
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow([src, tgt])
        w.writerows(pairs.items())
        return buf.getvalue()

    def topic_lines(self) -> list[str]:
        return [f"Q{i + 1}. {a['en']}" for i, a in enumerate(self.agenda)]


KO_STYLE = """Korean style: write the way a Korean professional interpreter speaks and a Korean broadcast subtitle reads, not like a translation.
- Polite spoken 합니다/해요 endings for finished sentences, always, even when the speaker is casual or rude; never 반말, never 너/너희/당신. A piece that continues keeps a connective ending (~하고, ~는데, ~해서, ~때문에) or ends without a verb.
- No middle dots (·) or dashes between words; write 일본과 한국 or 일본, 한국.
- Drop subjects and pronouns Korean leaves out (저는, 우리는, 그것, 그들, 당신). Never write 그것을, 그들은.
- Make the person the subject, not a thing: "X makes it easier" → "X 덕분에 훨씬 쉬워졌습니다", "It enables customers to build" → "고객이 더 빨리 만들 수 있습니다".
- Avoid translationese: ~를 통해, ~에 있어서, ~하는 것이 중요합니다, ~하도록 해야 합니다, 일치하도록, 가지고 있습니다, 정말/매우 for every "really", 저를 초대해주셔서.
- Use the words Korean colleagues actually say at work: 맞춰 가다, 챙기다, 진행이 더디다, 고객이랑 얘기하다.
Examples (style only, not content):
"Thanks for coming all the way out here." → "멀리까지 와 주셔서 감사합니다."
"which is why the cost went down so much" → "그래서 비용이 많이 내려갔습니다"
"so what we ended up doing was" → "그래서 결국 저희가 한 건"
"we have to get the partners on the same page" → "파트너들과 생각을 맞춰야 합니다"
"""

EN_STYLE = """English style: natural spoken English, as a professional interpreter would say it. Keep it short and plain; no formal or written phrasing, no "regarding", "in terms of" or "it is important to" that was not said. A piece that continues stays an open piece (no period).
Examples (style only, not content):
"어 그 다음에 일정 같은 경우에는" → "Next, on the schedule,"
"예산이 좀 빠듯하고" → "the budget is tight, and"
"""


def load_skills() -> dict[str, dict]:
    """skills/<name>.md: front matter (title, description) and instructions for the translator."""
    out = {}
    for f in sorted(SKILLS.glob("*.md")):
        text = f.read_text()
        m = re.match(r"---\n(.*?)\n---\n(.*)", text, re.S)
        if not m:
            continue
        meta = dict(line.split(":", 1) for line in m.group(1).splitlines() if ":" in line)
        out[f.stem] = {"name": f.stem, "title": meta.get("title", f.stem).strip(),
                       "description": meta.get("description", "").strip(), "text": m.group(2).strip()}
    return out


def skill_text(names: list[str]) -> str:
    skills = load_skills()
    missing = [n for n in names if n not in skills]
    if missing:
        raise ValueError(f"unknown skills: {', '.join(missing)} (have: {', '.join(skills)})")
    return "".join(f"\nSituation: {skills[n]['title']}\n{skills[n]['text']}\n" for n in names)


def person_line(p: dict) -> str:
    alias = ", ".join(p.get("always", []) + p.get("heard_as", []))
    return (f"- {p['name']} ({p.get('role', '')}): English subtitle \"{p['en']}\", Korean subtitle \"{p['ko']}\"."
            + (f" Korean speakers say {' / '.join(p['say_ko'])}." if p.get("say_ko") else "")
            + (f" Misrecognized as: {alias}." if alias else ""))


def term_line(t: dict) -> str:
    ko = t.get("ko", t["en"])
    line = f"- {t['en']}: Korean subtitle \"{ko}\""
    if ko == t["en"] and t.get("say_ko"):
        line += f" in Latin letters, never Hangul ({' / '.join(t['say_ko'])} is only how Korean speakers say it)"
    elif t.get("say_ko"):
        line += f"; Korean speakers say {' / '.join(t['say_ko'])}"
    if t.get("always") or t.get("heard_as"):
        line += f". Misrecognized as: {', '.join(t.get('always', []) + t.get('heard_as', []))}"
    if t.get("note"):
        line += f". {t['note']}"
    return line + ("" if line.endswith(".") else ".")


def vocab_phrase(text: str, lang: str) -> str:
    """Transcribe list-format phrase: words joined by hyphens, acronyms dotted, only allowed characters."""
    if lang == "en" and re.search(r"\d", text):
        return ""  # en-US vocabularies cannot hold digits; GPT-5 and the like are left to the translator
    words = []
    for w in re.split(r"[\s/]+", text.strip()):
        if lang == "en":
            w = re.sub(r"[^A-Za-z'.-]", "", w)
            if not re.fullmatch(r"(?:[A-Za-z]\.)+", w):
                w = w.rstrip(".")  # "Inc."
                if "." in w:
                    return ""  # periods only mark acronyms: AGENTS.md, Z.ai and SSG.COM cannot be vocabulary phrases
                # spelled-out acronyms (AWS, LG, CNS) get dots; brands read as a word (NAVER, HYBE, POSCO) do not
                if w.isupper() and 1 < len(w) <= 5 and (len(w) <= 3 or not re.search(r"[AEIOU]", w)):
                    w = ".".join(w) + "."
        else:
            w = re.sub(r"[^가-힣]", "", w)
        if w:
            words.append(w)
    return "-".join(words)[:256]


def load(name: str | None = None) -> Event:
    """Load events/<name>.toml (or a path) on top of events/_base.toml. Default: $EVENT, else events/general.toml
    (no people or agenda), so one session's names never leak into another."""
    name = name or os.environ.get("EVENT") or "general"
    path = Path(name) if name.endswith(".toml") else EVENTS / f"{name}.toml"
    base = tomllib.loads((EVENTS / "_base.toml").read_text())
    data = tomllib.loads(path.read_text())
    own = {t["en"].lower() for t in data.get("terms", [])}
    data["terms"] = [t for t in base.get("terms", []) if t["en"].lower() not in own] + data.get("terms", [])
    data["library"] = load_library(data.get("glossaries"), {t["en"].lower() for t in data["terms"]})
    focus = {f.lower() for f in data.get("focus", [])}
    missing = focus - {t["en"].lower() for t in data["library"]}
    if missing:
        raise SystemExit(f"focus entries not in the glossary: {', '.join(sorted(missing))}")
    data["terms"] += [t for t in data["library"] if t["en"].lower() in focus]
    data["library"] = [t for t in data["library"] if t["en"].lower() not in focus]
    return Event(**{k: v for k, v in data.items() if k in Event.__dataclass_fields__})


def load_library(names: list[str] | None, skip: set[str]) -> list[dict]:
    """glossary/<name>.toml entries (all files if names is None), minus those already defined as core or event terms."""
    files = [GLOSSARY / f"{n}.toml" for n in names] if names is not None else sorted(GLOSSARY.glob("*.toml"))
    out, seen = [], set(skip)
    for f in files:
        for t in tomllib.loads(f.read_text()).get("terms", []):
            if t["en"].lower() not in seen:
                seen.add(t["en"].lower())
                out.append({**t, "_file": f.stem})
    return out
