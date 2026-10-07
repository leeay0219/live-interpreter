"""Constants shared by the caption server modules: paths, limits, model IDs, timing and prices."""
import sys
import os
import logging
from pathlib import Path




sys.path.insert(0, str(Path(__file__).parent / "postprocess"))


log = logging.getLogger("captions")


STATIC = Path(__file__).parent / "static"
APP_VERSION = (Path(__file__).parent / "VERSION").read_text().strip()
DECKS = Path(os.environ.get("DECKS_DIR", Path(__file__).parent / "out" / "decks"))  # uploaded slides, rendered to images
MAX_DECK_PAGES = 300
SAMPLE_RATE = 16000
LONG_WORDS = 14  # stable words without a period before cutting at a comma
MAX_WORDS = {"ko": 22, "en": 34}  # run-on speech with no comma either (common in Korean): cut at the last stable word

LINE_LIMIT = {"ko": 34, "en": 72}  # characters per subtitle part, about one screen line; the browser rolls them up three lines high
MIN_WORDS = {"ko": 2, "en": 3}  # shorter fragments without a sentence end wait for the next one instead of being shown alone
HOLD_SECONDS = 2.5  # how long a short fragment waits to be joined with what follows
SKIP = "<skip/>"
DEFAULT_MODEL = "global.anthropic.claude-sonnet-5-5"
FALLBACK_MODEL = "global.anthropic.claude-haiku-4-5-20251001-v1:0"  # when the main model is slow or fails
HEDGE_AFTER = 1.8  # seconds: if the main model hasn't answered, ask the fallback model too and take the first answer
MODEL_DEADLINE = 3.6  # seconds for any model to answer; Amazon Translate after that
WRITEUP_MODEL = "global.anthropic.claude-opus-5-5"  # after the session: accuracy over speed
WRITEUP_LIMIT = 600  # seconds; a write-up still running after this is reported as failed so it can be retried
WRITEUP_CALL_TIMEOUT = 240  # seconds per model call in an app write-up, two attempts, so a stopped job ends soon after the limit
WRITEUP_KEEP = 5  # finished write-ups kept in memory; preparing a new session clears them all
# USD per million tokens (input, output, cache read, cache write), Bedrock global, ap-northeast-2, AWS Price List 2026-10-05
PRICES = {"haiku-4-5": (1.0, 5.0, 0.1, 1.25), "sonnet-5-5": (2.0, 10.0, 0.2, 2.5)}
