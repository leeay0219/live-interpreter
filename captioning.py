"""Text rules for live captions: language of a line, filler and repeats, subtitle checks, line breaking."""
import sys
import re
import logging
import math
from pathlib import Path




sys.path.insert(0, str(Path(__file__).parent / "postprocess"))


from settings import LINE_LIMIT
log = logging.getLogger("captions")


HANGUL = re.compile(r"[\uac00-\ud7a3]")
JAPANESE = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")  # kana and kanji; Korean recognition never writes Hanja
# a word for counting: Japanese has no spaces, so each run of kanji, hiragana or katakana counts as one
WORD = re.compile(r"[\u4e00-\u9fff々]+|[\u3040-\u309fー]+|[\u30a0-\u30ff]+|(?:(?![\u3040-\u30ff\u4e00-\u9fff])[\w'])+")


def base_lang(code: str, text: str = "", languages=("en", "ko")) -> str:
    # Transcribe's per-segment tag lags a few words behind a speaker switch; the script doesn't. A line with only
    # Latin letters in a session without English ("AWS Bedrock" said in Korean) keeps Transcribe's tag.
    tagged = code.lower()[:2]
    script = ("ko" if HANGUL.search(text) else "ja" if JAPANESE.search(text) else "en") if text else ""
    return next((l for l in (script, tagged) if l in languages), languages[0])


# the model talking to us instead of subtitling; only at the start of the reply, so "Could you introduce yourself?" passes
META = re.compile(r"^\s*(?:I appreciate|I'?m ready|I am ready|I need|I don'?t see|I do not see|Let me wait|I cannot|I can'?t|"
                  r"I'?m sorry|Could you (?:provide|clarify|share|give)|Please provide|No subtitle|Note:|"
                  r"(?:The|This) (?:line|fragment|input)\b)|speech[- ]recognition|번역할|맥락이|문맥이", re.I)
NON_SPEECH = re.compile(r"^\s*[(*\[].*[)*\]]\s*$")  # "(silence)", "*laughter*", "[inaudible]"


def looks_like_subtitle(src: str, tx: str, tgt: str, names: frozenset[str] = frozenset()) -> bool:
    """Catch the model talking to us (asking for context, explaining) instead of translating."""
    # Korean is dense: "엘화랑 엘엔솔" becomes "LG Chem and LG Energy Solution"
    if not tx or len(tx) > max(60, (4 if tgt == "en" else 3) * len(src)) or tx.count("\n") > 8:
        return False
    if META.search(tx) and not META.search(src):
        return False
    script = {"ko": HANGUL, "ja": JAPANESE}.get(tgt)
    if script and not script.search(tx) and (len(src.split()) > 2 or len(WORD.findall(src)) > 2):
        return False  # left in the source language
    if script:
        # Korean and Japanese subtitles keep names in English ("Northstar Cloud과 함께"): Latin words taken from the
        # source or from glossary names (`names`, lowercase words) don't count
        kept = {w.lower() for w in re.findall(r"[A-Za-z]+", src)} | names
        tx = re.sub(r"[A-Za-z]+", lambda m: "" if m.group(0).lower() in kept else m.group(0), tx)
    letters = re.sub(r"[^A-Za-z\uac00-\ud7a3\u3040-\u30ff\u4e00-\u9fff]", "", tx)
    if not letters:
        return bool(script) or not re.search(r"[A-Za-z\uac00-\ud7a3\u3040-\u30ff\u4e00-\u9fff]", src)  # only names, or numbers only
    if script:
        # Korean subtitles legitimately carry English names and titles ("Northstar Labs의 Platform 디렉터"),
        # and META above already catches the model answering in English, so a modest share of the script is enough
        return len(script.findall(letters)) / len(letters) > 0.25
    return len(HANGUL.findall(letters) + JAPANESE.findall(letters)) / len(letters) < 0.2


FILLER = re.compile(r"^(?:u+h+|u+m+|h+m+|a+h+|o+h+|e+r+|m+|h+[ae]+(?:h+[ae]+)*|y+eah|okay|ok|testing|check|mic|"
                    r"[어음아으에흠허하헤호오]+|그|이|저|뭐|네|예|자|막|좀|테스팅|"
                    r"[えあうんー]+|えっと|えーと|あの[ー]*|その[ー]*|まあ[ー]*|うーん|はい|ええ|\d+)$", re.I)


# a microphone check is only counting and "testing" ("One, two, three, testing", "하나 둘 셋")
MIC_CHECK = re.compile(r"^(?:\W*(?:one|two|three|four|five|testing|test|check|mic|hello|하나|둘|셋|넷|마이크|테스트|テスト|マイク|いち|に|さん|\d+)\W*)+$", re.I)


def collapse_repeats(text: str) -> str:
    """Stutters and recognition loops: "its accounts its its accounts its its accounts its" → "its accounts its".
    A run of 1 to 6 words repeated back to back is kept once (punctuation and case ignored when comparing)."""
    words = text.split()
    key = [re.sub(r"[^\w가-힣]", "", w).lower() for w in words]
    n = 6
    while n:
        i, changed = 0, False
        while i + 2 * n <= len(words):
            if key[i:i + n] == key[i + n:i + 2 * n] and any(key[i:i + n]):
                del words[i + n:i + 2 * n], key[i + n:i + 2 * n]
                changed = True
            else:
                i += 1
        # collapsing a short repeat can reveal a longer one ("A B A A B" → "A B A B"), so start over from the longest
        n = 6 if changed else n - 1
    return " ".join(words)


TAGS = re.compile(r"</?(?:line|terms|background|subtitle)\b[^>]*>", re.I)


def content_words(text: str) -> int:
    """Words that carry meaning: not filler, hesitation, laughter or mic-check counting."""
    if MIC_CHECK.match(text):
        return 0
    return sum(1 for w in WORD.findall(text) if not FILLER.match(w))


def norm(text: str) -> str:
    return " ".join(w for w in WORD.findall(text.lower()) if not FILLER.match(w))


# English break penalties: before a clause word is natural; "and"/"or" often join two nouns, so they rank lower
EN_BREAKS = [(re.compile(r" (?=(?:but|so|because|which|who|where|when|while|if|instead of|rather than) )"), 10),
             (re.compile(r" (?=(?:from|to|with|for|in|on|into|through|by|about|across|that) )"), 18),
             (re.compile(r" (?=(?:and|or) )"), 16),
             (re.compile(r" (?=(?:and|or) (?:the|a|an|our|their|your|its|this|these|those|[A-Z0-9]))"), 28),
             (re.compile(r" (?=(?:and|or|but) (?:then|so|also|we|they|you|I|it) )"), 8)]

KO_BREAK_AFTER = re.compile(r"(?:(?<=[면고며데만니요게록])|(?<=[^에]서))\s")  # after a connective ending; 에서 is a particle
# Japanese has no spaces: break after a comma, or after a particle that ends a phrase before the next word
JA_BREAKS = [(re.compile(r"(?<=、)"), 6), (re.compile(r"(?<=[。？！])"), 0),
             (re.compile(r"(?<=[はがをにでともへ])(?=[\u4e00-\u9fff\u30a0-\u30ffA-Za-z0-9])"), 18)]


def break_points(line: str, lang: str) -> dict[int, int]:
    """Where a line may be cut (index of the space) → penalty; lower is a more natural break."""
    pts = {m.start(): 30 for m in re.finditer(r" ", line)}
    for pattern, penalty in {"ko": [(KO_BREAK_AFTER, 14)], "ja": JA_BREAKS}.get(lang, EN_BREAKS):
        pts.update({m.start(): penalty for m in pattern.finditer(line)})
    pts.update({m.end() - 1: 6 for m in re.finditer(r"[,;:] ", line)})
    pts.update({m.end() - 1: 0 for m in re.finditer(r"[.?!] ", line)})
    return pts


def split_balanced(line: str, lang: str) -> list[str]:
    limit = LINE_LIMIT[lang]
    if len(line) <= limit * 1.3:
        return [line]
    n = math.ceil(len(line) / limit)
    target = len(line) / n
    pts = break_points(line, lang)
    # near the target length, prefer sentence ends, then commas, then conjunctions; never past 1.3× the limit
    ok = [i for i in pts if limit // 3 <= i <= limit * 1.3]
    if not ok:
        return [line]
    cut = min(ok, key=lambda i: abs(i - target) / limit * 20 + pts[i])
    return [line[:cut].strip()] + split_balanced(line[cut:].strip(), lang)


def split_lines(tx: str, lang: str) -> list[str]:
    """Subtitle-sized lines. Claude already splits long Korean subtitles; this catches Translate output and overlong lines."""
    limit = LINE_LIMIT[lang]
    # the model tends to over-split; rejoin neighbours that fit together on screen
    merged: list[str] = []
    for line in (l.strip() for l in tx.splitlines()):
        if not line:
            continue
        if merged and len(merged[-1]) + 1 + len(line) <= limit:
            merged[-1] += ("" if lang == "ja" else " ") + line
        else:
            merged.append(line)
    return [part for line in merged for part in split_balanced(line, lang)]
