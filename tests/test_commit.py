"""Offline checks for the commit logic: each stretch of audio reaches the translator once, filler and scraps are dropped.

    .venv/bin/python -m pytest tests -q      (or: .venv/bin/python tests/test_commit.py)
"""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server  # noqa: E402


def words(spec, stable=True):
    """'Hello:0.0-0.4 world:0.5-0.9 .' → Transcribe-like items."""
    out = []
    for tok in spec.split():
        if ":" in tok:
            w, t = tok.split(":")
            a, b = map(float, t.split("-"))
            out.append(NS(item_type="pronunciation", content=w, start_time=a, end_time=b, stable=stable))
        else:
            out.append(NS(item_type="punctuation", content=tok, start_time=None, end_time=None, stable=stable))
    return out


def result(rid, spec, partial, lang="en-US", stable=True):
    items = words(spec, stable)
    return NS(result_id=rid, is_partial=partial, language_code=lang,
              alternatives=[NS(items=items, transcript=server.join_items(items))])


class FakeSession:
    def __init__(self):
        self.sent, self.partials = [], []

    def submit(self, rid, text, lang):
        self.sent.append(text)

    async def on_partial(self, rid, text, lang):
        self.partials.append(text)


def run(results):
    s = FakeSession()
    h = server.Handler.__new__(server.Handler)
    h.s, h.until, h.chunks = s, 0.0, {}

    async def go():
        for r in results:
            await h.handle_transcript_event(NS(transcript=NS(results=[r])))
    asyncio.run(go())
    return s.sent


def test_sentence_committed_once_from_partial_and_final():
    sent = run([
        result("a", "We:0.0-0.2 build:0.3-0.6 agents:0.7-1.1 . Then:1.5-1.7", True),
        result("a", "We:0.0-0.2 build:0.3-0.6 agents:0.7-1.1 . Then:1.5-1.7 we:1.8-1.9 ship:2.0-2.3 .", False),
    ])
    assert sent == ["We build agents.", "Then we ship."], sent


def test_new_result_id_resending_old_audio():
    # multi-language mode re-sends audio it already returned, under a new id
    sent = run([
        result("a", "Claude:0.0-0.4 on:0.5-0.6 Bedrock:0.7-1.2 .", False),
        result("b", "on:0.5-0.6 Bedrock:0.7-1.2 . Is:1.6-1.8 great:1.9-2.3 .", False),
    ])
    assert sent == ["Claude on Bedrock.", "Is great."], sent


def test_final_resegmented_differently_from_partials():
    sent = run([
        result("a", "So:0.0-0.2 the:0.3-0.4 vision:0.5-0.9 is:1.0-1.1 safety:1.2-1.7 .", True),
        # final merges words differently and changes the punctuation
        result("a", "So:0.0-0.2 the:0.3-0.4 vision:0.5-0.9 is:1.0-1.1 safety:1.2-1.7 , really:1.8-2.2 .", False),
    ])
    assert sent == ["So the vision is safety.", "really."], sent


def test_unstable_words_wait():
    sent = run([result("a", "We:0.0-0.2 build:0.3-0.6 .", True, stable=False)])
    assert sent == []


class FakeTranslator:
    event = NS(languages=["en", "ko"], restore=lambda text, lang: text, correct=lambda text: text)

    async def final(self, text, src, tgt):
        return text.upper(), "fake"


def make_session():
    hub = NS(send=lambda msg: asyncio.sleep(0))
    s = server.Session.__new__(server.Session)
    s.cfg, s.hub, s.tr = None, hub, FakeTranslator()
    s._out, s._held, s._sent = asyncio.Queue(), None, []
    return s


def dispatched(s):
    out = []
    while not s._out.empty():
        out.append(s._out.get_nowait()[1])
    return out


def test_filler_short_fragments_and_repeats():
    async def go():
        s = make_session()
        s.submit("1", "Uh, um.", "en-US")                  # filler: dropped
        s.submit("2", "One, two, three, testing.", "en-US")  # mic check: dropped
        s.submit("3", "음, 네.", "ko-KR")                    # Korean filler: dropped
        s.submit("4", "And the", "en-US")                  # short: held ...
        s.submit("5", "customer said yes.", "en-US")       # ... and joined
        s.submit("6", "The customer said yes.", "en-US")   # repeat: dropped
        s.submit("7", "said", "en-US")                     # lone scrap, nothing follows
        await asyncio.sleep(server.HOLD_SECONDS + 0.2)
        return dispatched(s)
    out = asyncio.run(go())
    assert out == ["And the customer said yes."], out


def test_vocabulary_spelling_restored():
    import event
    ev = event.load("example")
    assert ev.restore("Customer-Obsession with Claude on A.W.S.-Bedrock at A.W.S.", "en") == \
        "Customer Obsession with Claude on AWS Bedrock at AWS"
    assert ev.restore("Mina-nim and co-sell", "en") == "Mina-nim and co-sell"


def test_certain_misrecognitions_corrected():
    import event
    ev = event.load("example")
    assert ev.correct("Thanks, meena. As allex said, we connect the records.") == \
        "Thanks, Mina. As Alex said, we connect the records."
    assert ev.correct("the allexes and meenas") == "the allexes and meenas"  # whole words only
    # AWS services now use the AWS prefix; the company stays Amazon
    assert ev.correct("Amazon S3 Vectors, Amazon EC2 and Amazon Bedrock AgentCore at Amazon") == \
        "AWS S3 Vectors, AWS EC2 and AWS Bedrock AgentCore at Amazon"
    # Korean company abbreviations from glossary/companies-kr.toml
    assert ev.correct("삼전이랑 하닉, 엘전과 엘화는") == "삼성전자이랑 에스케이하이닉스, 엘지전자과 엘지화학는"
    assert ev.correct("셀트리온과 셀트, 삼전기") == "셀트리온과 셀트리온, 삼성전기"  # full names stay as they are


def test_stutters_and_echoed_markup_removed():
    # a recognition loop shows up once, not three times ("그 계정, 그 계정, 그 계정")
    assert server.collapse_repeats("walk through its accounts its its accounts its its accounts its") == "walk through its accounts its"
    assert server.collapse_repeats("We need to we need to move faster") == "We need to move faster"
    assert server.collapse_repeats("one, two, three, testing") == "one, two, three, testing"
    assert server.collapse_repeats("정하는 건데 저희는 보통 저희는 저희는 보통 품질 검사") == "정하는 건데 저희는 보통 품질 검사"
    assert server.collapse_repeats("Q1, Q2, Q3") == "Q1, Q2, Q3"
    assert server.TAGS.sub("", "예산은 어떻게 관리하고, 실제로 어떻게</line>") == "예산은 어떻게 관리하고, 실제로 어떻게"


def test_operator_link_follows_the_secrets():
    a = server.Auth("pw-1", "vk-1")
    assert a.operator_key and a.operator_key == server.Auth("pw-1", "vk-1").operator_key
    assert a.operator_key != server.Auth("pw-2", "vk-1").operator_key  # new password: old link stops working
    assert a.operator_key != server.Auth("pw-1", "vk-2").operator_key
    assert server.Auth(None, None).operator_key == ""  # local mode: no access control


def test_subtitle_check():
    ok = server.looks_like_subtitle
    assert ok("with Claude on Amazon Bedrock,", "Claude on Amazon Bedrock과 함께,", "ko")
    assert ok("Anthropic is", "Anthropic에서는", "ko")
    assert ok("Claude Code", "Claude Code", "ko")
    assert not ok("with Claude on Amazon Bedrock,", "with Claude on Amazon Bedrock,", "ko")
    assert not ok("I think so.", "I think so.", "ko")          # left untranslated
    assert not ok("고객이 중요합니다", "고객이 중요합니다", "en")


def test_library_terms_found_per_line():
    import event
    lib = [{"en": "Samsung Electronics", "ko": "삼성전자", "say_ko": ["삼성전자", "삼전"]},
           {"en": "SK hynix", "ko": "SK하이닉스", "say_ko": ["SK하이닉스", "하이닉스", "하닉"]},
           {"en": "LG Chem", "ko": "LG화학", "say_ko": ["LG화학", "엘화"]},
           {"en": "retrieval-augmented generation", "ko": "RAG", "heard_as": ["rag"]}]
    ev = event.Event(id="t", library=lib)
    names = lambda text: [t["en"] for t in ev.relevant(text)]
    assert names("삼전이랑 하닉은 메모리를 하고, 엘화는요") == ["Samsung Electronics", "SK hynix", "LG Chem"]
    assert names("We built RAG on Bedrock for SK hynix.") == ["retrieval-augmented generation", "SK hynix"]
    assert names("dragon") == []  # English matches whole words only (Korean allows particles: 삼전이랑)
    assert "en,ko" in ev.terminology_csv("en") and "삼전,Samsung Electronics" in ev.terminology_csv("ko")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
