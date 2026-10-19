"""A live interpretation session: audio to Transcribe, committing each utterance once, translating, broadcasting."""
import sys
import asyncio
import json
import logging
import time
import uuid
from pathlib import Path

import boto3
from aiohttp import web
from amazon_transcribe.auth import CredentialResolver, Credentials
from amazon_transcribe.client import TranscribeStreamingClient
from amazon_transcribe.handlers import TranscriptResultStreamHandler
from amazon_transcribe.model import TranscriptEvent

import event as event_config


sys.path.insert(0, str(Path(__file__).parent / "postprocess"))


from settings import HOLD_SECONDS, LONG_WORDS, MAX_WORDS, MIN_WORDS, SAMPLE_RATE
from translation import Translator
from captioning import JAPANESE, base_lang, collapse_repeats, content_words, norm, split_lines
from logtext import said
log = logging.getLogger("captions")

# Two-channel input (static/studio-audio.js audioSources): the operator's own microphone on channel 0, the meeting on
# channel 1. Transcribe channel identification labels each result, so every line knows which side said it.
SIDES = {"ch_0": "self", "ch_1": "other"}


class BotoCredentialResolver(CredentialResolver):
    """Resolve credentials through boto3 so every profile type (login, SSO, credential_process) works."""

    def __init__(self, session: boto3.Session):
        self._session = session

    async def get_credentials(self):
        c = self._session.get_credentials().get_frozen_credentials()
        return Credentials(c.access_key, c.secret_key, c.token)


class Hub:
    """Fan-out of caption events to all connected viewers."""

    def __init__(self):
        self.viewers: set[web.WebSocketResponse] = set()
        self.history: list[dict] = []  # finalized lines, replayed to late joiners (the phone view scrolls back over them)

    async def send(self, msg: dict):
        if msg["type"] == "caption":
            self.history = (self.history + [msg])[-60:]
        data = json.dumps(msg, ensure_ascii=False)
        async def deliver(ws):
            try:
                await asyncio.wait_for(ws.send_str(data), 1)
            except Exception:
                self.viewers.discard(ws)
                asyncio.create_task(ws.close())
        await asyncio.gather(*(deliver(ws) for ws in list(self.viewers)))


class Session:
    """One Transcribe stream fed by one audio-source browser tab."""

    def __init__(self, app_cfg, hub: Hub, translator: Translator, owner=None, record: list | None = None, channels: int = 1):
        self.cfg = app_cfg
        self.channels = 2 if channels == 2 else 1  # 2: interleaved stereo, one side of the meeting per channel
        self.hub = hub
        self.tr = translator
        self.owner = owner  # the operator tab sending the audio
        self.record = record if record is not None else []  # finished lines, for the write-up after the session
        self.audio_sent = 0.0
        self.sid = uuid.uuid4().hex[:8]  # status messages carry it, so a tab only reacts to its own stream
        self.audio: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=50)  # five seconds of 100ms frames
        self.task: asyncio.Task | None = None
        self._partial_task: asyncio.Task | None = None
        self._last_partial_tx = 0.0
        # translations run concurrently but are shown in the order they were spoken
        self._out: asyncio.Queue = asyncio.Queue(maxsize=12)
        self._emitter: asyncio.Task | None = None
        self._held: tuple | None = None  # short fragment waiting for the next one: (rid, text, lang, timer, side)
        self._sent: list[tuple[str, float]] = []  # recently translated source text, to drop re-sent duplicates
        self.last_audio = self.last_voice = self.last_result = time.monotonic()
        self.overloaded = False
        self.accepting = True
        self.jobs = set()
        self._watcher = None
        self.logical_id = getattr(translator, "logical_id", self.sid)
        self._stop_lock = asyncio.Lock()
        self._stopped = False

    @property
    def languages(self) -> list[str]:
        return self.tr.event.languages

    def start(self):
        self.task = asyncio.create_task(self._run())
        self._emitter = asyncio.create_task(self._emit())
        self._watcher = asyncio.create_task(self._watch())
        asyncio.create_task(self.tr.warm())

    async def stop(self):
        async with self._stop_lock:
            if not self._stopped:
                await self._stop()
                self._stopped = True

    async def _stop(self):
        if self._watcher:
            self._watcher.cancel()
        try:
            self.audio.put_nowait(None)
        except asyncio.QueueFull:
            if self.task:
                self.task.cancel()
        if self.task:
            try:
                await asyncio.wait_for(self.task, 5)
            except (Exception, asyncio.CancelledError):
                self.task.cancel()
        self._release_held()
        self.accepting = False
        if self._emitter:
            try:
                await asyncio.wait_for(self._out.join(), 12)
            except asyncio.TimeoutError:
                pass
            self._emitter.cancel()
        for task in list(self.jobs):
            task.cancel()
        await asyncio.gather(*(t for t in [self.task, self._emitter, self._watcher, *list(self.jobs)] if t),
                             return_exceptions=True)

    async def _watch(self):
        while True:
            await asyncio.sleep(2)
            now = time.monotonic()
            state = ("audio_missing" if now - self.last_audio > 4 else
                     "silence" if now - self.last_voice > 10 else
                     "recognition_delayed" if now - self.last_result > 18 else
                     "translation_delayed" if self._out.qsize() >= 4 else "healthy")
            if self.owner and not self.owner.closed:
                await self.owner.send_json({"type": "diagnostic", "state": state, "sid": self.sid,
                                           "audio_queue": self.audio.qsize(), "translation_queue": self._out.qsize()})

    def feed_audio(self, data):
        if not self.accepting:
            return
        self.last_audio = time.monotonic()
        # PCM16, little-endian, interleaved when stereo. A sparse peak sample is enough for the silence diagnostic.
        if len(data) % (2 * getattr(self, "channels", 1)) or len(data) > 6400:
            raise ValueError("invalid audio frame")
        import array
        samples = array.array("h", data)
        if samples and max(abs(n) for n in samples[::8]) > 260:
            self.last_voice = self.last_audio
        self.audio.put_nowait(data)

    async def _run(self):
        cfg = self.cfg
        client = TranscribeStreamingClient(region=cfg.region, credential_resolver=BotoCredentialResolver(cfg.session))
        kwargs = dict(
            media_sample_rate_hz=SAMPLE_RATE,
            media_encoding="pcm",
            enable_partial_results_stabilization=True,
            partial_results_stability=cfg.stability,
        )
        ev = self.tr.event  # the languages the translator was given for this session
        if self.channels == 2:
            kwargs.update(enable_channel_identification=True, number_of_channels=2)
        if cfg.auto_lang:
            # multiple-language mode re-identifies the language per segment, so the speakers can alternate
            kwargs.update(language_code=None, identify_multiple_languages=True,
                          language_options=[event_config.TRANSCRIBE_LANG[l] for l in ev.languages],
                          preferred_language=event_config.TRANSCRIBE_LANG[ev.languages[0]])
            # vocabularies are built for English and Korean only (setup_event.py)
            if cfg.vocabulary and any(l in event_config.RESOURCE_LANGS for l in ev.languages):
                kwargs["vocabulary_names"] = [ev.vocabulary(l) for l in ev.languages if l in event_config.RESOURCE_LANGS]
        else:
            kwargs.update(language_code=event_config.TRANSCRIBE_LANG[ev.languages[0]])
            if cfg.vocabulary and ev.languages[0] in event_config.RESOURCE_LANGS:
                kwargs["vocabulary_name"] = ev.vocabulary(ev.languages[0])
        await self.hub.send({"type": "status", "state": "connecting", "sid": self.sid})
        try:
            stream = await client.start_stream_transcription(**kwargs)
            await self.hub.send({"type": "status", "state": "live", "sid": self.sid})
            log.info("Transcribe stream started (%s%s)", "auto " + "↔".join(ev.languages) if cfg.auto_lang else ev.languages[0],
                     ", 2 channels" if self.channels == 2 else "")
            await asyncio.gather(self._pump(stream), Handler(stream.output_stream, self).handle_events())
        except Exception as e:
            log.exception("transcribe stream error")
            await self.hub.send({"type": "status", "state": "error", "detail": str(e)[:200], "sid": self.sid})
            return
        await self.hub.send({"type": "status", "state": "stopped", "sid": self.sid})

    async def _pump(self, stream):
        while True:
            chunk = await self.audio.get()
            if chunk is None:
                break
            await stream.input_stream.send_audio_event(audio_chunk=chunk)
            self.audio_sent += len(chunk) / (2 * SAMPLE_RATE * self.channels)  # seconds of audio streamed so far
        await stream.input_stream.end_stream()

    # --- result handling ---
    async def on_partial(self, rid: str, text: str, lang: str, side: str | None = None):
        self.last_result = time.monotonic()
        src = base_lang(lang, text, self.languages)
        text = self.tr.event.correct(self.tr.event.restore(text, src))
        await self.hub.send({"type": "partial", "id": rid, "text": text, "lang": src, **({"side": side} if side else {})})
        # provisional translation for long partials, throttled so we don't flood Translate
        long_enough = len(text.replace(" ", "")) >= 12 if src in ("ko", "ja") else len(text.split()) >= 6
        if self.cfg.provisional and long_enough and time.monotonic() - self._last_partial_tx > 0.8:
            if self._partial_task is None or self._partial_task.done():
                self._last_partial_tx = time.monotonic()
                self._partial_task = asyncio.create_task(self._provisional(rid, text, src))

    async def _provisional(self, rid: str, text: str, src: str):
        tgt = next(l for l in self.languages if l != src)
        try:
            tx = await self.tr.quick(text, src, tgt)
            await self.hub.send({"type": "partial_tx", "id": rid, "tx": tx, "lang": tgt})
        except Exception as e:
            log.warning("provisional translation failed: %s", e)

    def submit(self, rid: str, text: str, lang: str, side: str | None = None):
        """A finished piece of speech. Filler is dropped, short fragments wait to be joined with what follows,
        and each piece is translated once and never revised. side ("self", "other") comes from two-channel input;
        a fragment is only joined with what the same side says next."""
        self.last_result = time.monotonic()
        if not getattr(self, "accepting", True):
            return
        src = base_lang(lang, text, self.languages)
        text = self.tr.event.correct(self.tr.event.restore(text, src))
        if self._held:
            h_rid, h_text, h_src, timer, h_side = self._held
            self._held = None
            timer.cancel()
            if h_src == src and h_side == side:
                rid, text = h_rid, f"{h_text} {text}"
            else:
                self._flush_held(h_rid, h_text, h_src, h_side)
        n = content_words(text)
        if n == 0:
            log.info("[%s skip] filler: %s", src, said(text))
            return
        if n < MIN_WORDS[src] and not text.rstrip().endswith((".", "?", "!", "。", "？", "！")):
            timer = asyncio.get_running_loop().call_later(HOLD_SECONDS, self._release_held)
            self._held = (rid, text, src, timer, side)
            return
        self._dispatch(rid, text, src, side)

    def _release_held(self):
        if self._held:
            rid, text, src, _, side = self._held
            self._held = None
            self._flush_held(rid, text, src, side)

    def _flush_held(self, rid: str, text: str, src: str, side: str | None = None):
        # nothing followed: a lone word ("said", "the") is usually a recognition scrap, show it only if it is a real phrase
        if content_words(text) >= MIN_WORDS[src] - (1 if src in ("ko", "ja") else 0):
            self._dispatch(rid, text, src, side)
        else:
            log.info("[%s skip] fragment: %s", src, said(text))

    def _dispatch(self, rid: str, text: str, src: str, side: str | None = None):
        if self._out.full():
            self.overloaded = True
            self.accepting = False
            asyncio.create_task(self.hub.send({"type": "status", "state": "overloaded",
                                               "detail": "번역이 밀려 입력을 멈췄습니다. 잠시 후 이어서 하세요.",
                                               "sid": getattr(self, "sid", "")}))
            return
        text = collapse_repeats(text)
        key, now = norm(text), time.monotonic()
        # shared by both channels: the same words on the other channel within seconds are the mic hearing the meeting
        self._sent = [(k, t) for k, t in self._sent if now - t < 8]
        if any(key == k or (len(key) > 8 and key in k) for k, _ in self._sent):
            log.info("[%s skip] repeat: %s", src, said(text))
            return
        self._sent.append((key, now))
        tgt = next(l for l in self.languages if l != src)
        tag = {"side": side} if side else {}
        asyncio.create_task(self.hub.send({"type": "final", "id": rid, "text": text, "lang": src, **tag}))
        frozen = self.tr.snapshot() if hasattr(self.tr, "snapshot") else self.tr
        # Register the source before starting another line, while keeping this line's context immutable.
        if hasattr(self.tr, "recent"):
            self.tr.recent = (self.tr.recent + [(src, text)])[-8:]
        job = asyncio.create_task(frozen.final(text, src, tgt))
        if hasattr(self, "jobs"):
            self.jobs.add(job)
            job.add_done_callback(self.jobs.discard)
        context = {"topic": getattr(frozen, "topic", ""), "context_version": getattr(frozen, "context_version", 0),
                   "session_id": getattr(self, "logical_id", ""), **tag}
        self._out.put_nowait((rid, text, src, tgt, now, job, context))

    async def _emit(self):
        while True:
            item = await self._out.get()
            try:
                await self._emit_one(item)
            finally:
                self._out.task_done()

    async def _emit_one(self, item):
        rid, text, src, tgt, t0, job, context = item
        # every committed utterance is kept as heard, also when nothing reached the screen; t is when it was committed
        heard = {"t": time.time() - (time.monotonic() - t0), "src_lang": src, "src": text, **context}
        try:
            tx, engine = await asyncio.wait_for(asyncio.shield(job), 12)
        except Exception:
            log.error("translation failed for %s: %s", said(text), type(sys.exc_info()[1]).__name__)
            self.record.append({**heard, "tx": "", "status": "failed", "engine": "", "shown_at": None})
            return
        ms = int((time.monotonic() - t0) * 1000)
        if not tx:
            log.info("[%s→%s skip %dms] %s", src, tgt, ms, said(text))
            self.record.append({**heard, "tx": "", "status": "skipped", "engine": engine, "shown_at": None})
            return
        if tgt == "en":
            # Claude's English line breaks often land inside a noun phrase; break before a conjunction instead
            tx = " ".join(tx.split())
        lines = split_lines(tx, tgt) or [tx]
        log.info("[%s→%s %s %dms] %s → %s", src, tgt, engine, ms, said(text), said(" / ".join(lines)))
        self.tr._remember(text, tx)
        self.record.append({**heard, "tx": " ".join(lines), "status": "shown", "engine": engine, "shown_at": time.time()})
        # sent right away; the browser paces the lines at reading speed
        for k, line in enumerate(lines):
            await self.hub.send({"type": "caption", "id": f"{rid}#{k}", "src": text, "src_lang": src,
                                 "tx": line, "tx_lang": tgt, "engine": engine, "ms": ms if k == 0 else 0, **context})


def join_items(items) -> str:
    out = ""
    for it in items:
        # Japanese is written without spaces between words
        if it.item_type == "punctuation" or not out or (JAPANESE.match(out[-1]) and JAPANESE.match(it.content)):
            out += it.content
        else:
            out += " " + it.content
    return out.strip()


class Handler(TranscriptResultStreamHandler):
    """Commits each sentence as soon as Transcribe marks it stable, instead of waiting for the
    whole segment to end. Long answers without pauses would otherwise lag 15 s or more.

    Each stretch of audio is committed exactly once. Multiple-language mode sometimes sends audio it already returned
    again under a new result id, or re-segments a result between its partials and its final; that is why the committed
    position is audio time, not an item index per result. Words that end before it were already translated.

    With two channels both sides share one audio clock and talk over each other, so the position is kept per channel:
    one side's words never mark the other side's as already committed."""

    def __init__(self, output_stream, session: Session):
        super().__init__(output_stream)
        self.s = session
        self.until: dict[str | None, float] = {}  # per channel (None without channels): audio time (s) committed so far
        self.chunks: dict[str, int] = {}

    def _fresh(self, items, channel=None) -> int:
        """Index of the first word not yet committed on this channel."""
        until = self.until.get(channel, 0.0)
        for i, it in enumerate(items):
            if it.item_type != "punctuation" and ((it.start_time or 0) + (it.end_time or 0)) / 2 > until:
                return i
        return len(items)

    def _submit(self, rid: str, text: str, lang: str, channel=None):
        side = SIDES.get(channel)
        if side:
            self.s.submit(rid, text, lang, side)
        else:
            self.s.submit(rid, text, lang)

    def _commit(self, rid: str, items, start: int, upto: int, lang: str, channel=None):
        part = items[start:upto]
        text = join_items(part)
        ends = [it.end_time for it in part if it.item_type != "punctuation" and it.end_time]
        if ends:
            self.until[channel] = max(self.until.get(channel, 0.0), max(ends))
        if text and ends:
            n = self.chunks[rid] = self.chunks.get(rid, 0) + 1
            # how long after the last word was spoken the sentence got committed (recognition wait)
            log.info("[lag] committed %.1fs after speech: %s", getattr(self.s, "audio_sent", 0) - max(ends), said(text[:60]))
            self._submit(f"{rid}:{n}", text, lang, channel)

    async def handle_transcript_event(self, event: TranscriptEvent):
        for r in event.transcript.results:
            if not r.alternatives:
                continue
            alt = r.alternatives[0]
            items = alt.items or []
            lang = r.language_code or "en-US"
            rid = r.result_id
            # Transcribe labels mono results ch_0 too: only a two-channel stream has sides
            channel = getattr(r, "channel_id", None) if getattr(self.s, "channels", 1) == 2 else None
            start = self._fresh(items, channel)
            if r.is_partial:
                end = comma = last = 0
                words = 0
                for i, it in enumerate(items):
                    if not it.stable:
                        break
                    if i < start:
                        continue
                    if it.item_type != "punctuation":
                        words += 1
                        last = i + 1
                    elif it.content in ".?!。？！":
                        end = i + 1
                    elif it.content in ",、":
                        comma = i + 1
                # run-on speech without a period: cut at the last stable comma once it gets long
                if end <= start and words >= LONG_WORDS and comma > start:
                    end = comma
                elif end <= start and words >= MAX_WORDS[base_lang(lang, alt.transcript, getattr(self.s, "languages", ("en", "ko")))]:
                    end = last
                if end > start:
                    self._commit(rid, items, start, end, lang, channel)
                rest = join_items(items[self._fresh(items, channel):]) if items else alt.transcript.strip()
                if rest:
                    side = SIDES.get(channel)
                    await (self.s.on_partial(rid, rest, lang, side) if side else self.s.on_partial(rid, rest, lang))
            else:
                if items:
                    self._commit(rid, items, start, len(items), lang, channel)
                elif alt.transcript.strip() and rid not in self.chunks:
                    self._submit(f"{rid}:1", alt.transcript.strip(), lang, channel)
                self.chunks.pop(rid, None)
