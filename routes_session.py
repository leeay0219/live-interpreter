"""Session lifecycle and settings: context, speakers, terms, skills, usage."""
import sys
import asyncio
import os
import logging
import uuid
import copy
from pathlib import Path

from aiohttp import web

import event as event_config
import materials


sys.path.insert(0, str(Path(__file__).parent / "postprocess"))


from settings import APP_VERSION, PRICES
from logtext import LOG_CONTENT
from translation import Translator, model_name
from access import operator, writable
from routes_materials import remove_material
log = logging.getLogger("captions")


async def skills_list(request):
    if request.app["auth"].role(request) != "operator":
        raise web.HTTPForbidden()
    tr: Translator = request.app["translator"]
    skills = [{k: v for k, v in sk.items() if k != "text"} for sk in event_config.load_skills().values()]
    return web.json_response({"skills": skills, "selected": tr.skills, "log_content": LOG_CONTENT["on"]})


def person_from(d: dict) -> dict | None:
    """One speaker from the studio form; en and ko default to the name."""
    name = str(d.get("name", "")).strip()[:60]
    if not name:
        return None
    lst = lambda k: [str(x).strip()[:40] for x in d.get(k, []) if str(x).strip()][:12]
    return {"name": name, "en": str(d.get("en") or name).strip()[:40], "ko": str(d.get("ko") or name).strip()[:40],
            "role": str(d.get("role", "")).strip()[:80], "display": name, "say_ko": lst("say_ko"), "heard_as": lst("heard_as")}


async def suggest_people(request):
    """Likely misrecognitions of a speaker's name, for the studio's 발표자 form."""
    if request.app["auth"].role(request) != "operator":
        raise web.HTTPForbidden()
    person = person_from(await request.json())
    if not person:
        raise web.HTTPBadRequest(text="이름을 입력해 주세요.")
    try:
        return web.json_response(await asyncio.to_thread(request.app["translator"].suggest_mishearings, person))
    except Exception as e:
        log.warning("name suggestions failed: %s", type(e).__name__)
        return web.json_response({"say_ko": [], "heard_as": []})


async def set_context(request):
    """Situation skills and whether speech content goes into the log, chosen on the studio page."""
    writable(request)
    tr: Translator = request.app["translator"]
    body = await request.json()
    if body.get("session_id") != request.app["state"].get("session_id"):
        raise web.HTTPConflict(text="세션이 바뀌었습니다. 새로 고쳐 주세요.")
    if "deck_id" in body and body["deck_id"] != (request.app["state"].get("deck") or {}).get("id"):
        raise web.HTTPConflict(text="다른 창에서 발표 자료가 바뀌었습니다. 화면을 새로 고쳐 주세요.")
    try:
        tr.set_skills(body.get("skills", []))
    except ValueError as e:
        raise web.HTTPBadRequest(text=str(e))
    LOG_CONTENT["on"] = bool(body.get("log_content"))
    if "people" in body:  # speakers from the studio form replace the event file's for this session
        tr.set_people([p for p in (person_from(d) for d in body["people"][:8]) if p])
    if "terms" in body:
        tr.set_terms(terms_from(body["terms"]))
    materials.refresh(request.app)
    log.info("context: skills %s, content in log %s", tr.skills or "-", "on" if LOG_CONTENT["on"] else "off")
    return web.json_response({"skills": tr.skills, "log_content": LOG_CONTENT["on"], "people": len(tr.event.people),
                              "terms": len(session_terms(tr))})


def terms_from(items: list) -> list[dict]:
    """Session terms from the studio: a misheard word and its right spelling. `always` replaces it before translation
    (only for words that mean nothing else); otherwise the translator fixes it when the context fits."""
    out = []
    for d in items[:40]:
        heard, en = str(d.get("heard", "")).strip()[:40], str(d.get("en", "")).strip()[:60]
        if heard and en and heard.lower() != en.lower():
            t = {"en": en, ("always" if d.get("always") else "heard_as"): [heard], "_session": True}
            if str(d.get("ko", "")).strip():
                t["ko"] = str(d["ko"]).strip()[:60]
            out.append(t)
    return out


def session_terms(tr: Translator) -> list[dict]:
    return [t for t in tr.event.terms if t.get("_session")]


async def set_terms(request):
    """Replace the session terms while captioning (from the log drawer's 용어로 추가), applied to the next line."""
    writable(request)
    tr: Translator = request.app["translator"]
    tr.set_terms(terms_from((await request.json()).get("terms", [])))
    materials.refresh(request.app)
    asyncio.create_task(tr.warm())  # the prompt changed: write its cache now rather than on the next line
    log.info("session terms: %d", len(session_terms(tr)))
    return web.json_response({"terms": len(session_terms(tr))})


async def usage(request):
    """Tokens and estimated Bedrock cost since the server started."""
    if request.app["auth"].role(request) != "operator":
        raise web.HTTPForbidden()
    out, total = {}, 0.0
    usage_by_model = copy.deepcopy(request.app["translator"].usage)
    analysis = request.app["state"].get("analysis_usage", {})
    if analysis:
        target = usage_by_model.setdefault(model_name(request.app["translator"].model), {})
        for key, value in analysis.items():
            target[key] = target.get(key, 0) + value
    for model, u in usage_by_model.items():
        price = PRICES.get(model)
        cost = None
        if price:
            fresh = u.get("inputTokens", 0)
            cost = (fresh * price[0] + u.get("outputTokens", 0) * price[1] + u.get("cacheReadInputTokens", 0) * price[2]
                    + u.get("cacheWriteInputTokens", 0) * price[3]) / 1e6
            total += cost
        out[model] = {**u, "usd": round(cost, 4) if cost is not None else None}
    return web.json_response({"models": out, "usd": round(total, 4), "analysis": analysis}, headers={"Cache-Control": "no-store"})


async def session_info(request):
    """The loaded event, for the studio page."""
    if request.app["auth"].role(request) != "operator":
        raise web.HTTPForbidden()
    cfg = request.app["cfg"]
    ev = request.app["translator"].event
    state = request.app["state"]
    return web.json_response({
        "version": APP_VERSION,
        "document_formats": ["html", "docx", "md", "csv"],
        "id": ev.id, "title": ev.title, "subtitle": ev.subtitle, "languages": ev.languages,
        "people": ev.people,
        "session_id": state["session_id"], "phase": state["phase"], "controller": state.get("controller"),
        "page": state.get("page", 0), "focus": state.get("focus", "presentation"),
        "context_version": state.get("context_version", 0),
        "session_terms": [{"heard": (t.get("heard_as") or t.get("always") or [""])[0],
                           "en": t["en"], "ko": t.get("ko", ""), "always": bool(t.get("always"))}
                          for t in session_terms(request.app["translator"])],
        "agenda": [{"title": a["title"], "en": a["en"], "ko": a.get("ko", a["en"])} for a in ev.agenda],
        "terms": len(ev.terms), "library": len(ev.library),
        "engine": cfg.engine, "vocabulary": cfg.vocabulary, "auto_lang": cfg.auto_lang,
        "hosted": bool(os.environ.get("ECS_CONTAINER_METADATA_URI_V4")),  # on AWS: the studio shows the running cost
    }, headers={"Cache-Control": "no-store"})


async def new_session(request):
    writable(request)
    app, state = request.app, request.app["state"]
    async with app["session_lock"]:
        if state.get("source") or state.get("rehearsal") or state.get("phase") == "paused":
            raise web.HTTPConflict(text="진행 중인 세션을 종료한 뒤 새 세션을 준비하세요.")
        body = await request.json()
        if body.get("session_id") != state["session_id"]:
            raise web.HTTPConflict(text="세션이 바뀌었습니다. 새로 고쳐 주세요.")
        for doc in materials.documents(app):
            await remove_material(app, doc)
        state.update(deck=None, references={}, page=0, focus="presentation", controller=None,
                     session_id=uuid.uuid4().hex, phase="prepared")
        app["translator"].event = copy.deepcopy(app["base_event"])
        app["cfg"].event = app["translator"].event
        app["translator"].reset_conversation()
        app["translator"].skills = list(app["base_event"].skills)
        materials.refresh(app)
        app["record"].clear()
        for job in app["writeups"].values():
            job["cancel"].set()
        app["writeups"].clear()
        app["hub"].history.clear()
        LOG_CONTENT["on"] = False
        await app["hub"].send({"type": "clear"})
        await app["hub"].send({"type": "lifecycle", "phase": "prepared", "session_id": state["session_id"]})
    return await session_info(request)


async def claim_session(request):
    operator(request)
    app, state = request.app, request.app["state"]
    body = await request.json()
    identity = request.headers.get("X-Operator-Id", "")
    async with app["session_lock"]:
        if not identity or body.get("session_id") != state["session_id"]:
            raise web.HTTPConflict(text="세션이 바뀌었습니다. 새로 고쳐 주세요.")
        if state.get("source") or state.get("phase") != "paused":
            raise web.HTTPConflict(text="현재 입력 창이 연결되어 있습니다.")
        state["controller"] = identity
    return await session_info(request)
