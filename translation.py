"""Translator: prompts, terminology and document context, and the model calls with their fallbacks."""
import sys
import asyncio
import json
import re
import logging
import copy
from pathlib import Path

import boto3
from botocore.config import Config

import event as event_config
import deck_context
from workloads import ThreadLocalAWSClient


sys.path.insert(0, str(Path(__file__).parent / "postprocess"))


from settings import FALLBACK_MODEL, HEDGE_AFTER, LINE_LIMIT, MIN_WORDS, MODEL_DEADLINE, SKIP
from captioning import NON_SPEECH, TAGS, collapse_repeats, content_words, looks_like_subtitle
from logtext import said, why
log = logging.getLogger("captions")


# repeated right next to every line: the model follows a reminder here far more reliably than a rule in the system prompt
REGISTER = {
    "ko": "\n(Interpreter's register: natural spoken Korean, polite 합니다/해요, never 반말 or 너/너희/당신 (say 여러분 or leave it out), "
          "no 그것/그들, no translationese (a thing that 'makes X easier' → X가 쉬워집니다), no profanity or insults; "
          "keep the meaning, its strength and who says what about whom.)",
    "en": "\n(Interpreter's register: plain professional English, no profanity or insults; keep the meaning, its strength and who says what about whom.)",
}


class Translator:
    def __init__(self, session: boto3.Session, region: str, model: str, engine: str, ev: event_config.Event, terminology: bool):
        live_config = Config(connect_timeout=3, read_timeout=8, retries={"total_max_attempts": 1})
        self.translate = ThreadLocalAWSClient(session, "translate", region_name=region, config=live_config)
        self.bedrock = ThreadLocalAWSClient(session, "bedrock-runtime", region_name=region, config=live_config)
        self.session = session
        self.model = model
        self.engine = engine
        self.event = ev
        self.terminology = terminology
        self.document_brief = None
        self.skills: list[str] = list(ev.skills)
        self.set_skills(self.skills)
        self.usage: dict[str, dict[str, int]] = {}  # per model: calls and token counts, for the cost readout
        self._warmed: set[tuple[str, int]] = set()
        self.recent: list[tuple[str, str]] = []  # (lang, text) of previous source lines, both speakers
        self.shown: dict[str, str] = {}  # source line → subtitle shown for it (recent lines only)
        self.names = frozenset(w.lower() for t in ev.people + ev.terms + ev.library for w in re.findall(r"[A-Za-z]+", t["en"]))
        self.topic = ""
        self.context_version = 0
        self.focus = "presentation"
        self._inference_slots = asyncio.Semaphore(6)
        # the glossary index is one large regex (about 0.6 s to compile on a laptop, more on the task's one vCPU, holding
        # the GIL); built lazily it landed on the first line of the first session and pushed it past the model deadline
        ev.relevant("")

    def reset_conversation(self):
        self.recent = []
        self.shown = {}

    def snapshot(self):
        frozen = copy.copy(self)
        frozen.event = copy.copy(self.event)
        frozen.prompts = dict(self.prompts)
        frozen.recent = list(self.recent)
        frozen.shown = dict(self.shown)
        return frozen

    def set_people(self, people: list[dict]):
        self.event.set_people(people)
        self._rebuild()

    def set_terms(self, terms: list[dict]):
        self.event.set_session_terms(terms)
        self._rebuild()

    def _rebuild(self):
        ev = self.event
        self.names = frozenset(w.lower() for t in ev.people + ev.terms + ev.library for w in re.findall(r"[A-Za-z]+", t["en"]))
        if self.document_brief:
            self.names |= frozenset(w.lower() for field, key in (("people", "name"), ("terms", "term"))
                                   for t in self.document_brief[field] for w in re.findall(r"[A-Za-z]+", t[key]))
        self.set_skills(self.skills)  # rebuilds the prompts, which list the people

    def set_document_brief(self, brief):
        self.document_brief = brief
        self._rebuild()

    def suggest_mishearings(self, person: dict) -> dict:
        """How speech recognition is likely to mishear a name, in English and Korean speech, for the operator to keep or drop."""
        prompt = (f"A speaker at an English-Korean interpreted session: {person['name']} (English subtitle \"{person['en']}\", "
                  f"Korean subtitle \"{person['ko']}\"). Real-time speech recognition (Amazon Transcribe, en-US and ko-KR) often "
                  "mishears names. List how it would likely write this name when English speakers say it and when Korean speakers "
                  "say it, including the name with honorifics (님, 상, -nim, -san). Also give the Korean pronunciations. "
                  'Return only JSON: {"say_ko": ["..."], "heard_as": ["..."]}, at most 4 say_ko and 10 heard_as, no duplicates of the name itself.')
        r = self.bedrock.converse(modelId=self.model, messages=[{"role": "user", "content": [{"text": prompt}]}],
                                  inferenceConfig={"maxTokens": 400},
                                  additionalModelRequestFields={"output_config": {"effort": "low"}})
        text = next(b["text"] for b in r["output"]["message"]["content"] if "text" in b)
        m = re.search(r"\{.*\}", text, re.S)
        out = json.loads(m.group(0)) if m else {}
        names = {person["name"].lower(), person["en"].lower(), person["ko"].lower()}
        clean = lambda xs, n: list(dict.fromkeys(x.strip() for x in xs if isinstance(x, str) and x.strip() and x.strip().lower() not in names))[:n]
        return {"say_ko": clean(out.get("say_ko", []), 4), "heard_as": clean(out.get("heard_as", []), 10)}

    def set_skills(self, names: list[str]):
        ev = self.event
        reference = deck_context.reference_text(self.document_brief)
        self.prompts = {(a, b): ev.prompt(a, b, LINE_LIMIT[b], names) + reference
                        for a in ev.languages for b in ev.languages if a != b}
        self.skills = list(names)

    async def warm(self):
        """Prime each translation direction once per prompt (writes the prompt cache, opens connections) while the
        speaker is still getting started; without it the first real lines after a deploy took 3-4 s."""
        jobs = []
        for (src, tgt), prompt in self.prompts.items():
            for model in dict.fromkeys([self.model, FALLBACK_MODEL]):
                key = (model, hash(prompt))
                if key not in self._warmed:
                    self._warmed.add(key)
                    jobs.append(asyncio.to_thread(self._converse, model, src, tgt, "Translate only this new line:\n<line>Hello.</line>"))
        for r in await asyncio.gather(*jobs, return_exceptions=True):
            if isinstance(r, Exception):
                log.warning("warm-up failed: %s", r)
        if jobs:
            log.info("warm-up: %d calls", len(jobs))

    def set_topic(self, text: str):
        self.topic = text

    def _translate_api(self, text: str, src: str, tgt: str) -> str:
        kw = {"TerminologyNames": [self.event.terminology(src)]} if self.terminology else {}
        # the interpreter's voice: polite Korean (never 반말), and profanity masked rather than put on a big screen
        settings = {"Profanity": "MASK", **({"Formality": "FORMAL"} if tgt == "ko" else {})}
        r = self.translate.translate_text(Text=text, SourceLanguageCode=src, TargetLanguageCode=tgt, Settings=settings, **kw)
        return collapse_repeats(re.sub(r"\s{2,}", " ", r["TranslatedText"].replace("?$#@$", "")).strip())

    def _claude(self, text: str, src: str, tgt: str, recent: list[tuple[str, str]], model: str | None = None) -> str:
        model = model or self.model
        # each previous line with the subtitle actually shown for it, so the model knows what the audience already read
        context = "\n".join(f"[{l.upper()}] {t}" + (f"\n   shown: {self.shown[t]}" if self.shown.get(t) else "")
                            for l, t in recent) or "(start of conversation)"
        terms = self.event.glossary_for(text)  # only the library entries that occur in this line
        user = (
            f"Current question on screen: {self.topic or '(none)'}\n"
            f"Previous lines and the subtitles already on screen (context only; never repeat what a subtitle already said):\n{context}\n\n"
            + (f"<terms>\n{terms}\n</terms>\n\n" if terms else "")
            + f"Translate only this new line:\n<line>{text}</line>"
            + REGISTER[tgt]
        )
        tx = self._converse(model, src, tgt, user)
        if SKIP in tx and content_words(text) >= MIN_WORDS[src]:
            # filler, mic checks and repeats were already dropped before this point; when unsure about a broken line
            # the model tends to skip it, so ask once more
            tx = self._converse(model, src, tgt, user + "\n\nThis line has new content. Translate it as a piece; do not skip it.")
        if SKIP in tx or NON_SPEECH.match(tx):
            return ""
        tx = TAGS.sub("", tx).strip()  # the model sometimes echoes the request's markup ("…어떻게</line>")
        if tgt == "ko":
            tx = re.sub(r"\s*[·•]\s*", ", ", tx)  # 일본·한국 → 일본, 한국
        if not looks_like_subtitle(text, tx, tgt, self.names):
            raise ValueError(f"not a subtitle: {tx[:80]!r}")
        return tx

    def _converse(self, model: str, src: str, tgt: str, user: str) -> str:
        r = self.bedrock.converse(
            modelId=model,
            # the system prompt (rules, glossary, skills) is the same for every line: cached, so later lines pay the
            # cache-read rate for it (models below their minimum cacheable size simply don't cache)
            system=[{"text": self.prompts[(src, tgt)]}, {"cachePoint": {"type": "default"}}],
            messages=[{"role": "user", "content": [{"text": user}]}],
            # newer models (Sonnet 5.5, Opus 5.5) reject temperature
            inferenceConfig={"maxTokens": 400, **({"temperature": 0} if "haiku-4-5" in model else {})},
            # a subtitle line needs no deliberation: low effort keeps Sonnet/Opus close to Haiku's latency
            **({} if "haiku-4-5" in model else {"additionalModelRequestFields": {"output_config": {"effort": "low"}}}),
        )
        u, stats = r.get("usage", {}), self.usage.setdefault(model_name(model), {})
        for k in ("inputTokens", "outputTokens", "cacheReadInputTokens", "cacheWriteInputTokens"):
            stats[k] = stats.get(k, 0) + u.get(k, 0)
        stats["calls"] = stats.get("calls", 0) + 1
        # newer models may put a reasoning block before the text
        return next(b["text"] for b in r["output"]["message"]["content"] if "text" in b).strip()

    async def _hedged(self, text: str, src: str, tgt: str, recent) -> tuple[str, str] | None:
        """Ask the main model; if it hasn't answered after HEDGE_AFTER (or fails), ask the fallback model too and use
        whichever good answer comes first. None if no model answers within MODEL_DEADLINE."""
        loop = asyncio.get_running_loop()

        async def bounded(model):
            async with self._inference_slots:
                return await asyncio.to_thread(self._claude, text, src, tgt, recent, model)

        def call(model: str) -> asyncio.Future:
            task = asyncio.create_task(bounded(model))
            # A losing SDK call may finish later. Consume its exception and keep the concurrency
            # slot until it really exits instead of spawning unbounded detached calls.
            task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
            return task

        tasks = {call(self.model): self.model}
        deadline = loop.time() + MODEL_DEADLINE
        hedge_at = loop.time() + HEDGE_AFTER if self.model != FALLBACK_MODEL else None
        while tasks and loop.time() < deadline:
            until = min(deadline, hedge_at) if hedge_at else deadline
            done, _ = await asyncio.wait(tasks, timeout=until - loop.time(), return_when=asyncio.FIRST_COMPLETED)
            for t in sorted(done, key=lambda t: tasks[t] != self.model):  # the main model's answer first
                model = tasks.pop(t)
                try:
                    tx = t.result()
                except Exception as e:
                    log.warning("final translation via %s failed (%s)", model_name(model), why(e))
                    continue
                return tx, model_name(model) if tx else "skip"  # a slower call still running is simply ignored
            if hedge_at and (loop.time() >= hedge_at or not tasks):
                tasks[call(FALLBACK_MODEL)] = FALLBACK_MODEL
                hedge_at = None
        log.warning("final translation: no model answered within %.1fs", MODEL_DEADLINE)
        return None

    async def quick(self, text: str, src: str, tgt: str) -> str:
        return await asyncio.to_thread(self._translate_api, text, src, tgt)

    async def final(self, text: str, src: str, tgt: str) -> tuple[str, str]:
        """Translate a finalized segment once. Returns ("", "skip") for filler. Falls back to Amazon Translate if Claude
        is slow or fails, except for short fragments, which are dropped rather than shown as a literal guess."""
        engine = self.engine
        recent = self.recent[-4:]  # lines submitted before this one, even if their translation is still running
        self.recent = (self.recent + [(src, text)])[-8:]
        if engine == "claude":
            tx = await self._hedged(text, src, tgt, recent)
            if tx is not None:
                self._remember(text, tx[0])
                return tx
        else:
            try:
                return await self.quick(text, src, tgt), engine
            except Exception as e:
                log.warning("final translation via translate failed (%s)", e)
        if content_words(text) < MIN_WORDS[src] + 1:
            log.warning("short fragment dropped: %s", said(text))
            return "", "skip"
        tx = await self.quick(text, src, tgt)
        self._remember(text, tx)
        return tx, "translate"

    def _remember(self, text: str, tx: str):
        self.shown[text] = " ".join(tx.split())
        keep = {t for _, t in self.recent}
        self.shown = {k: v for k, v in self.shown.items() if k in keep}


def model_name(model: str) -> str:
    """global.anthropic.claude-sonnet-5-5 → sonnet-5-5"""
    m = re.search(r"claude-([a-z]+-[\d-]+?)(?:-\d{8})?(?:-v\d.*)?$", model)
    return m.group(1) if m else model
