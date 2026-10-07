"""Live caption server: browser mic → Amazon Transcribe → Claude on Bedrock / Amazon Translate → caption clients.

Run:  .venv/bin/python server.py            then open http://localhost:8080
"""
import argparse
import csv
import io
import sys
import tempfile
import asyncio
import hashlib
import hmac
import json
import os
import re
import logging
import math
import time
import uuid
import threading
import weakref
import copy
import shutil
from pathlib import Path
from urllib.parse import quote

import boto3
from botocore.config import Config
import qrcode
import qrcode.image.svg
from aiohttp import WSMsgType, web
from amazon_transcribe.auth import CredentialResolver, Credentials
from amazon_transcribe.client import TranscribeStreamingClient
from amazon_transcribe.handlers import TranscriptResultStreamHandler
from amazon_transcribe.model import TranscriptEvent

import event as event_config
import deck_context
import materials
from security import public_base, request_boundary, response_headers, SafeAccessLogger, validate_bind
from postprocess.document import render_docx
from workloads import BackgroundInference, ThreadLocalAWSClient


sys.path.insert(0, str(Path(__file__).parent / "postprocess"))
import process_recording as writeup  # noqa: E402  (the same pipeline that writes up recordings)
from postprocess.html_document import CSP as DOCUMENT_CSP, render_document  # noqa: E402

import settings  # noqa: F401
from settings import STATIC, APP_VERSION, DECKS, MAX_DECK_PAGES, SAMPLE_RATE, LONG_WORDS, MAX_WORDS, LINE_LIMIT, MIN_WORDS, HOLD_SECONDS, SKIP, DEFAULT_MODEL, FALLBACK_MODEL, HEDGE_AFTER, MODEL_DEADLINE, WRITEUP_MODEL, WRITEUP_LIMIT, WRITEUP_CALL_TIMEOUT, WRITEUP_KEEP, PRICES  # noqa: F401
from logtext import LOG_CONTENT, said, why  # noqa: F401
from captioning import HANGUL, base_lang, META, NON_SPEECH, looks_like_subtitle, FILLER, MIC_CHECK, collapse_repeats, TAGS, content_words, norm, EN_BREAKS, KO_BREAK_AFTER, break_points, split_balanced, split_lines  # noqa: F401
from translation import REGISTER, Translator, model_name  # noqa: F401
from live import BotoCredentialResolver, Hub, Session, join_items, Handler  # noqa: F401
from access import AUTH_COOKIE, LOGIN_PAGE, Auth, oplink, viewer_link, caplink, caplink_qr, operator, writable, captions, login_page, login  # noqa: F401
from routes_live import ws_handler, rehearsal_ws  # noqa: F401
from routes_materials import upload_deck, upload_status, remove_material, deck_analysis, current_deck, references, edit_material, deck_page  # noqa: F401
from routes_session import skills_list, person_from, suggest_people, set_context, terms_from, session_terms, set_terms, usage, session_info, new_session, claim_session  # noqa: F401
from routes_records import record_info, record_download, start_writeup, writeup_status  # noqa: F401

log = logging.getLogger("captions")

log = logging.getLogger("captions")


async def index(request):
    """The studio. /?k=<operator key> signs the browser in as operator (bookmark it once), so nobody needs to type the password."""
    auth: Auth = request.app["auth"]
    key = request.query.get("k")
    if auth.on and key is not None:
        if not hmac.compare_digest(key, auth.operator_key):
            await asyncio.sleep(1)
            raise web.HTTPForbidden(text="운영자 링크가 올바르지 않습니다. 비밀번호가 바뀌었다면 새 링크를 받아 주세요.")
        resp = web.HTTPFound(request.path)  # drop the key from the address bar
        auth.set_cookie(resp, "operator")
        raise resp
    if auth.role(request) != "operator":
        raise web.HTTPFound("/login")
    return web.FileResponse(STATIC / "studio.html")


async def studio(request):
    if request.app["auth"].role(request) != "operator":
        raise web.HTTPFound("/login")
    return web.FileResponse(STATIC / "studio.html")


async def healthz(request):
    return web.Response(text="ok")


def create_app(cfg, translator=None):
    app = web.Application(middlewares=[request_boundary], client_max_size=materials.MAX_UPLOAD_BYTES + 1024 ** 2)
    app.on_response_prepare.append(response_headers)
    app["cfg"] = cfg
    app["hub"] = Hub()
    app["translator"] = translator or Translator(cfg.session, cfg.region, cfg.model, cfg.engine, cfg.event, cfg.vocabulary)
    app["record"] = []  # finished lines of the current or last session
    app["writeups"] = {}  # write-up jobs, in memory only
    app["uploads"] = {}  # PDF uploads being rendered (or just finished), polled at /api/uploads/{id}
    app["replaced"] = weakref.WeakSet()  # operator tabs another tab took over from
    app["state"] = {"source": None, "session_id": uuid.uuid4().hex, "phase": "prepared", "references": {}}
    app["base_event"] = copy.deepcopy(cfg.event)
    app["session_lock"] = asyncio.Lock()
    app["upload_lock"] = asyncio.Lock()
    app["cleanup_tasks"] = set()
    app["background"] = BackgroundInference(lambda: app["state"].get("phase") == "live")
    app["analysis_client"] = (translator.bedrock if translator else ThreadLocalAWSClient(
        cfg.session, "bedrock-runtime", region_name=cfg.region,
        config=Config(connect_timeout=5, read_timeout=45, retries={"total_max_attempts": 1})))
    app["auth"] = Auth(os.environ.get("OPERATOR_PASSWORD"), os.environ.get("VIEW_KEY"))
    app.router.add_get("/", index)
    app.router.add_get("/login", login_page)
    app.router.add_post("/login", login)
    app.router.add_get("/healthz", healthz)
    app.router.add_get("/captions", captions)
    app.router.add_get("/caplink", caplink)
    app.router.add_get("/caplink.svg", caplink_qr)
    app.router.add_get("/api/oplink", oplink)
    app.router.add_get("/studio", studio)
    app.router.add_get("/api/session", session_info)
    app.router.add_post("/api/session/new", new_session)
    app.router.add_post("/api/session/claim", claim_session)
    app.router.add_get("/api/references", references)
    app.router.add_post("/api/references", upload_deck)
    app.router.add_delete("/api/references/{id}", references)
    app.router.add_get("/api/materials/{id}/analysis", deck_analysis)
    app.router.add_post("/api/materials/{id}/analysis", deck_analysis)
    app.router.add_post("/api/materials/{id}/edit", edit_material)
    app.router.add_get("/ws/rehearsal", rehearsal_ws)
    app.router.add_get("/api/skills", skills_list)
    app.router.add_post("/api/context", set_context)
    app.router.add_post("/api/people/suggest", suggest_people)
    app.router.add_post("/api/terms", set_terms)
    app.router.add_get("/api/usage", usage)
    app.router.add_get("/api/record", record_info)
    app.router.add_delete("/api/record", record_info)
    app.router.add_get("/api/record/{fmt}", record_download)
    app.router.add_post("/api/writeup", start_writeup)
    app.router.add_get("/api/writeup/{id}", writeup_status)
    app.router.add_get("/api/writeup/{id}/{fmt}", writeup_status)
    app.router.add_post("/api/deck", upload_deck)
    app.router.add_get("/api/uploads/{id}", upload_status)
    app.router.add_get("/api/deck", current_deck)
    app.router.add_delete("/api/deck", current_deck)
    app.router.add_get("/api/deck/analysis", deck_analysis)
    app.router.add_post("/api/deck/analysis", deck_analysis)
    app.router.add_get("/decks/{deck}/{page}", deck_page)
    app.router.add_get("/ws", ws_handler)
    app.router.add_static("/static", STATIC)
    async def cancel_analysis(app):
        docs = materials.documents(app)
        for doc in docs:
            materials.cancel(doc)
        await asyncio.gather(*(d["_task"] for d in docs if d.get("_task")), return_exceptions=True)
        await asyncio.gather(*list(app["cleanup_tasks"]), return_exceptions=True)
        source = app["state"].get("source")
        if source:
            await source.stop()
        app["background"].close()
    app.on_cleanup.append(cancel_analysis)
    return app


def main():
    ap = argparse.ArgumentParser(description="Live EN↔KO caption server")
    ap.add_argument("--host", default="127.0.0.1", help="external addresses require OPERATOR_PASSWORD")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--region", default="ap-northeast-2")
    ap.add_argument("--profile", default=None)
    ap.add_argument("--engine", choices=["claude", "translate"], default="claude",
                    help="engine for finalized lines (provisional lines always use Amazon Translate)")
    ap.add_argument("--model", default=DEFAULT_MODEL, help=f"Bedrock model for translation (falls back to {FALLBACK_MODEL} when slow)")
    ap.add_argument("--event", default=None, help="events/<name>.toml with people, agenda and glossary (default: $EVENT or the only event)")
    ap.add_argument("--no-vocabulary", dest="vocabulary", action="store_false",
                    help="don't use the event's Transcribe vocabularies and Translate terminologies (before setup_event.py has run)")
    ap.add_argument("--provisional", action="store_true",
                    help="also show quick Amazon Translate drafts while a sentence is still being spoken (off: only finished translations)")
    ap.add_argument("--stability", choices=["high", "medium", "low"], default="high",
                    help="Transcribe partial-result stability: lower marks words final sooner (faster captions) but they change more often")
    ap.add_argument("--auto-lang", action="store_true", help="identify English/Korean per segment and translate each way (EN→KO, KO→EN)")
    cfg = ap.parse_args()
    try:
        validate_bind(cfg.host, os.environ.get("OPERATOR_PASSWORD"))
    except ValueError as exc:
        ap.error(str(exc))
    cfg.event = event_config.load(cfg.event)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg.session = boto3.Session(profile_name=cfg.profile, region_name=cfg.region)

    app = create_app(cfg)
    log.info("event %s: %d people, %d terms, vocabularies %s", cfg.event.id, len(cfg.event.people), len(cfg.event.terms),
             "on" if cfg.vocabulary else "off")
    log.info("access control %s", "on (operator password)" if app["auth"].on else "off (local)")
    print(f"\n  Studio   http://localhost:{cfg.port}/\n  Captions http://localhost:{cfg.port}/captions\n")
    web.run_app(app, host=cfg.host, port=cfg.port, print=None, access_log_class=SafeAccessLogger)


if __name__ == "__main__":
    main()
