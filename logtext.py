"""Whether speech content goes into the server log, and helpers that respect it."""
import sys
import logging
from pathlib import Path




sys.path.insert(0, str(Path(__file__).parent / "postprocess"))


log = logging.getLogger("captions")


# Whether speech content (what was said, the subtitles) goes into the server log. Off by default: the log keeps time,
# direction, model, latency and length only. The studio turns it on per session for troubleshooting (/api/context).
LOG_CONTENT = {"on": False}


def said(text: str) -> str:
    """Speech content for a log line, or just its length when content logging is off."""
    return text if LOG_CONTENT["on"] else f"[{len(text)}자]"


def why(e: Exception) -> str:
    """An error for a log line; "not a subtitle: '...'" carries the model's output, so only its kind when content is off."""
    msg = str(e)[:120] or type(e).__name__
    return msg if LOG_CONTENT["on"] or not msg.startswith("not a subtitle") else "not a subtitle"
