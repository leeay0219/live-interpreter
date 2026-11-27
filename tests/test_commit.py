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


def result(rid, spec, partial, lang="en-US", stable=True, channel=None):
    items = words(spec, stable)
    return NS(result_id=rid, is_partial=partial, language_code=lang, channel_id=channel,
              alternatives=[NS(items=items, transcript=server.join_items(items))])


class FakeSession:
    def __init__(self, channels=1):
        self.channels = channels
        self.sent, self.sides, self.partials = [], [], []

    def submit(self, rid, text, lang, side=None):
        self.sent.append(text)
        self.sides.append(side)

    async def on_partial(self, rid, text, lang, side=None):
        self.partials.append((text, side))


def run(results, session=None):
    s = session or FakeSession()
    h = server.Handler.__new__(server.Handler)
    h.s, h.until, h.chunks = s, {}, {}

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


def test_two_channels_talking_over_each_other_are_both_committed():
    # the operator (ch_0) and the meeting (ch_1) share one audio clock; one side must not mark the other's words done
    s = FakeSession(channels=2)
    sent = run([
        result("m", "We:0.0-0.3 build:0.4-0.8 agents:0.9-1.4 .", True, channel="ch_0"),
        result("o", "네:0.2-0.5 좋습니다:0.6-1.2 .", True, lang="ko-KR", channel="ch_1"),
        result("m", "We:0.0-0.3 build:0.4-0.8 agents:0.9-1.4 . Then:1.6-1.9 we:2.0-2.1 ship:2.2-2.6 .", False, channel="ch_0"),
        result("o", "네:0.2-0.5 좋습니다:0.6-1.2 . 다음:1.5-1.9 주에:2.0-2.3 해요:2.4-2.8 .", False, lang="ko-KR", channel="ch_1"),
    ], s)
    assert list(zip(sent, s.sides)) == [("We build agents.", "self"), ("네 좋습니다.", "other"),
                                        ("Then we ship.", "self"), ("다음 주에 해요.", "other")], list(zip(sent, s.sides))


def test_resent_audio_is_still_dropped_per_channel():
    s = FakeSession(channels=2)
    sent = run([
        result("a", "Claude:0.0-0.4 on:0.5-0.6 Bedrock:0.7-1.2 .", False, channel="ch_1"),
        result("b", "on:0.5-0.6 Bedrock:0.7-1.2 . Is:1.6-1.8 great:1.9-2.3 .", False, channel="ch_1"),
    ], s)
    assert sent == ["Claude on Bedrock.", "Is great."] and s.sides == ["other", "other"], (sent, s.sides)


def test_partials_carry_their_side_and_mono_has_none():
    s = FakeSession(channels=2)
    run([result("a", "We:0.0-0.2 build:0.3-0.6", True, channel="ch_0")], s)
    mono = FakeSession()
    run([result("b", "We:0.0-0.2 build:0.3-0.6", True, channel="ch_0")], mono)
    assert s.partials == [("We build", "self")] and mono.partials == [("We build", None)], (s.partials, mono.partials)


def test_mono_stream_labelled_ch_0_has_no_side():
    # Transcribe puts ChannelId ch_0 on results of a mono stream too; that is not the operator's mic
    s = FakeSession()
    sent = run([result("a", "랜딩:0.0-0.4 존은:0.5-0.9 11월에:1.0-1.5 엽니다:1.6-2.0 .", False, lang="ko-KR", channel="ch_0")], s)
    assert sent == ["랜딩 존은 11월에 엽니다."] and s.sides == [None], (sent, s.sides)


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


def dispatched(s, with_side=False):
    out = []
    while not s._out.empty():
        item = s._out.get_nowait()
        out.append((item[1], item[6].get("side")) if with_side else item[1])
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


def test_fragment_is_not_joined_with_the_other_side():
    async def go():
        s = make_session()
        s.submit("1", "And the", "en-US", "self")              # the operator's short fragment is held ...
        s.submit("2", "customer said yes.", "en-US", "other")  # ... the other side speaks: not glued on
        s.submit("3", "We ship next week.", "en-US", "self")
        await asyncio.sleep(server.HOLD_SECONDS + 0.2)
        return dispatched(s, with_side=True)
    out = asyncio.run(go())
    assert ("customer said yes.", "other") in out and ("We ship next week.", "self") in out, out
    assert not any("And the customer" in text for text, _ in out), out


def test_echo_of_the_meeting_on_the_mic_is_dropped():
    # without earphones the mic hears the meeting: the same words arrive on both channels within seconds
    async def go():
        s = make_session()
        s.submit("1", "The landing zone launches in November.", "en-US", "other")
        s.submit("2", "The landing zone launches in November.", "en-US", "self")
        return dispatched(s, with_side=True)
    assert asyncio.run(go()) == [("The landing zone launches in November.", "other")]


def test_stereo_frames_are_validated_by_channel_count():
    s = server.Session.__new__(server.Session)
    s.accepting, s.channels, s.audio = True, 2, asyncio.Queue(maxsize=50)
    s.feed_audio(bytes(6400))  # 100 ms of 16 kHz stereo
    try:
        s.feed_audio(bytes(6402))
        raise AssertionError("oversized frame accepted")
    except ValueError:
        pass
    try:
        s.feed_audio(bytes(3202))  # half a stereo sample
        raise AssertionError("misaligned stereo frame accepted")
    except ValueError:
        pass
    assert s.audio.qsize() == 1


def test_vocabulary_spelling_restored():
    import event
    ev = event.load("example")
    assert ev.restore("Customer-Obsession and A.W.S.-Bedrock at A.W.S.", "en") == \
        "Customer Obsession and AWS Bedrock at AWS"
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
    assert ok("with Northstar Cloud,", "Northstar Cloud과 함께,", "ko")
    assert ok("Anthropic is", "Anthropic에서는", "ko")
    assert ok("Claude Code", "Claude Code", "ko")
    assert not ok("with Northstar Cloud,", "with Northstar Cloud,", "ko")
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


def test_japanese_is_committed_without_spaces_at_its_sentence_end():
    sent = run([
        result("j", "今日:0.0-0.3 は:0.3-0.4 晴れ:0.5-0.8 です:0.8-1.0 。 明日:1.2-1.5", True, lang="ja-JP"),
        result("j", "今日:0.0-0.3 は:0.3-0.4 晴れ:0.5-0.8 です:0.8-1.0 。 明日:1.2-1.5 も:1.5-1.6 AWS:1.7-2.0 です:2.0-2.2 。", False, lang="ja-JP"),
    ])
    assert sent == ["今日は晴れです。", "明日も AWS です。"], sent


def test_language_of_a_line_within_the_session_pair():
    assert server.base_lang("ja-JP", "今日はAWSの話です", ("ja", "ko")) == "ja"
    assert server.base_lang("ko-KR", "네 좋습니다", ("ja", "ko")) == "ko"
    assert server.base_lang("ko-KR", "AWS Bedrock", ("ja", "ko")) == "ko"  # Latin only, no English in the pair: Transcribe's tag
    assert server.base_lang("ja-JP", "AWS Bedrock", ("en", "ja")) == "en"
    assert server.base_lang("en-US", "네 좋습니다") == "ko"


def test_japanese_filler_and_subtitles():
    assert server.content_words("えーと、あのー") == 0
    assert server.content_words("テスト、テスト、いち、に、さん") == 0
    assert server.content_words("今日はクラウドの話をします。") >= 3
    ok = server.looks_like_subtitle
    assert ok("오늘은 클라우드 이야기를 하겠습니다", "今日はクラウドの話をします。", "ja")
    assert ok("We use AWS Bedrock for this today.", "今日はAWS Bedrockを使います。", "ja")
    assert ok("今日はクラウドの話をします。", "Today I'll talk about the cloud.", "en")
    assert not ok("今日はクラウドの話をします。", "今日はクラウドの話をします。", "ko")  # left untranslated
    assert not ok("We will ship it next week.", "We will ship it next week.", "ja")
    lines = server.split_lines("本日はお忙しいところお集まりいただき、誠にありがとうございます。これからAWSの生成AIサービスについてご紹介します。", "ja")
    assert lines == ["本日はお忙しいところお集まりいただき、誠にありがとうございます。", "これからAWSの生成AIサービスについてご紹介します。"], lines


def test_chinese_is_committed_without_spaces_at_its_sentence_end():
    sent = run([
        result("c", "今天:0.0-0.3 我們:0.3-0.5 介紹:0.5-0.8 AWS:0.9-1.2 。 明天:1.4-1.7", True, lang="zh-TW"),
        result("c", "今天:0.0-0.3 我們:0.3-0.5 介紹:0.5-0.8 AWS:0.9-1.2 。 明天:1.4-1.7 再:1.7-1.8 見:1.8-2.0 。", False, lang="zh-TW"),
    ])
    assert sent == ["今天我們介紹 AWS。", "明天再見。"], sent


def test_chinese_is_told_from_japanese_by_kana():
    # Han characters without kana are Chinese only when the session has Chinese; a kanji-only Japanese line stays Japanese
    assert server.base_lang("zh-TW", "今天我們介紹HealthOmics", ("en", "zh")) == "zh"
    assert server.base_lang("en-US", "今天我們介紹HealthOmics", ("en", "zh")) == "zh"  # Transcribe's tag lags, the script does not
    assert server.base_lang("zh-TW", "AWS HealthOmics", ("en", "zh")) == "en"
    assert server.base_lang("ja-JP", "今日", ("ja", "ko")) == "ja"
    assert server.base_lang("ko-KR", "네 좋습니다", ("ko", "zh")) == "ko"


def test_chinese_filler_and_subtitles():
    assert server.content_words("嗯那個") == 0
    assert server.content_words("對對") == 0
    assert server.content_words("測試測試一二三") == 0
    assert server.content_words("今天我們來介紹 HealthOmics") >= 6  # characters count: a two-character word is two
    assert server.norm("嗯那個我們今天") == server.norm("我們今天")
    ok = server.looks_like_subtitle
    assert ok("We use AWS Bedrock for this today.", "今天我們用AWS Bedrock。", "zh")
    assert ok("오늘은 클라우드 이야기를 하겠습니다", "今天來談雲端。", "zh")
    assert ok("今天我們來談雲端運算。", "Today we'll talk about cloud computing.", "en")
    assert not ok("We will ship it next week.", "We will ship it next week.", "zh")  # left untranslated
    assert not ok("We use it.", "今日はAWSを使います。", "zh")  # Japanese instead of Chinese
    lines = server.split_lines("謝謝各位今天遠道而來參加這場工作坊，接下來我們會介紹AWS HealthOmics和Nextflow的執行方式。", "zh")
    assert lines == ["謝謝各位今天遠道而來參加這場工作坊，", "接下來我們會介紹AWS HealthOmics", "和Nextflow的執行方式。"], lines


def test_chinese_prompt_and_translate_codes():
    ev = server.event_config.load("example")
    ev.set_languages(["en", "zh"])
    prompt = ev.prompt("en", "zh", 26)
    assert "Traditional Chinese" in prompt and "軟體 not 软件" in prompt and "Chinese sound-alikes" in prompt
    assert server.event_config.TRANSLATE_LANG["zh"] == "zh-TW" and server.event_config.TRANSCRIBE_LANG["zh"] == "zh-TW"
    assert "Chinese subtitle \"米娜\"" in server.event_config.person_line({"name": "Mina Park", "en": "Mina", "ko": "미나", "zh": "米娜"})
    assert "Chinese subtitle \"雲端\"" in server.event_config.term_line({"en": "cloud", "ko": "클라우드", "zh": "雲端"})


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
