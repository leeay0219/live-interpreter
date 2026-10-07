"""PDF uploads, rendering, analysis, corrections and slide images."""
import sys
import asyncio
import re
import logging
import time
import uuid
import shutil
from pathlib import Path

from aiohttp import web

import materials


sys.path.insert(0, str(Path(__file__).parent / "postprocess"))

import settings
from settings import MAX_DECK_PAGES
from access import operator, writable

log = logging.getLogger("captions")


async def upload_deck(request):
    """Receive a PDF and render it in the background. The request returns as soon as the file is in (202 and an upload
    id); a large PDF can take longer to render than CloudFront waits for one request. GET /api/uploads/{id} reports it."""
    writable(request)
    app, state = request.app, request.app["state"]
    session_id = state["session_id"]
    client_id = request.headers.get("X-Operator-Id")
    reference = request.path == "/api/references"
    if reference and len(state.get("references", {})) >= materials.MAX_REFERENCES:
        raise web.HTTPBadRequest(text="참고 자료는 4개까지 올릴 수 있습니다.")
    # one upload at a time, for memory bounds and so concurrent uploads cannot exhaust disk
    if app["upload_lock"].locked():
        raise web.HTTPConflict(text="다른 자료를 읽는 중입니다. 끝난 뒤 다시 올려 주세요.")
    await app["upload_lock"].acquire()
    try:
        name, data = await materials.read_upload(request)
    except BaseException:
        app["upload_lock"].release()
        raise
    identity = uuid.uuid4().hex[:10]
    job = {"state": "rendering", "name": name, "kind": "reference" if reference else "presentation",
           "session_id": session_id, "error": None, "document": None, "started": time.time()}
    uploads = app["uploads"]
    for old in [k for k, j in uploads.items() if j["state"] != "rendering"][:-9 or None]:
        del uploads[old]  # keep the last few results for polling
    uploads[identity] = job

    def still_allowed():
        if state["session_id"] != session_id:
            return "세션이 바뀌어 업로드를 취소했습니다. 다시 올려 주세요."
        controller = state.get("controller")
        if controller and state.get("phase") in ("live", "paused") and client_id != controller:
            return "현재 통역 중인 운영 창에서 올려 주세요."
        if reference and len(state.get("references", {})) >= materials.MAX_REFERENCES:
            return "참고 자료는 4개까지 올릴 수 있습니다."
        return None

    async def render():
        out = settings.DECKS / identity
        try:
            try:
                pages, texts, images = await materials.render_upload(data, out, MAX_DECK_PAGES)
            except Exception as exc:
                log.warning("PDF render failed: %s", type(exc).__name__)
                job.update(state="error", error="PDF를 읽지 못했습니다. 암호, 페이지 수 또는 파일 크기를 확인하세요.")
                return
            refused = still_allowed()
            if refused:
                await asyncio.to_thread(shutil.rmtree, out, True)
                job.update(state="error", error=refused)
                return
            doc = {"id": identity, "name": name, "kind": job["kind"],
                   "pages": pages, "revision": 0, "_texts": texts, "_images": images, "_edits": {}}
            if reference:
                state.setdefault("references", {})[identity] = doc
            else:
                old = state.get("deck")
                state.update(deck=doc, page=0)
                if old:
                    await remove_material(app, old)
            materials.refresh(app)
            materials.start_analysis(app, doc)
            job.update(state="done", document=materials.public_document(doc))
        finally:
            app["upload_lock"].release()

    task = asyncio.create_task(render())
    app.setdefault("cleanup_tasks", set()).add(task)
    task.add_done_callback(app["cleanup_tasks"].discard)
    return web.json_response({"upload_id": identity, "name": name, "kind": job["kind"], "state": "rendering"}, status=202)


async def upload_status(request):
    """Progress of a PDF upload started with POST /api/deck or /api/references."""
    operator(request)
    job = request.app["uploads"].get(request.match_info["id"])
    if not job or job["session_id"] != request.app["state"]["session_id"]:
        raise web.HTTPNotFound()
    return web.json_response({"state": job["state"], "error": job["error"], "document": job["document"],
                              "seconds": round(time.time() - job["started"])}, headers={"Cache-Control": "no-store"})


async def remove_material(app, doc):
    materials.cancel(doc)
    # Keep paths alive until an in-flight SDK call has settled. Clean up only this known upload directory.
    async def cleanup():
        task = doc.get("_task")
        if task:
            await asyncio.gather(task, return_exceptions=True)
        await asyncio.to_thread(shutil.rmtree, settings.DECKS / doc["id"], True)
    task = asyncio.create_task(cleanup())
    app.setdefault("cleanup_tasks", set()).add(task)
    task.add_done_callback(app["cleanup_tasks"].discard)


async def deck_analysis(request):
    operator(request)
    app = request.app
    identity = request.match_info.get("id")
    doc = materials.find_document(app, identity) if identity else app["state"].get("deck")
    if not doc:
        return web.json_response(None, headers={"Cache-Control": "no-store"})
    if request.method == "POST":
        writable(request)
        body = await request.json()
        if body.get("deck_id") != doc["id"]:
            raise web.HTTPConflict(text="자료가 바뀌었습니다. 새로 고쳐 주세요.")
        if doc.get("analysis", {}).get("state") != "running":
            materials.start_analysis(app, doc)
    return web.json_response(materials.analysis_view(doc), headers={"Cache-Control": "no-store"})


async def current_deck(request):
    operator(request)
    if request.method == "DELETE":
        writable(request)
        doc = request.app["state"].pop("deck", None)
        if doc:
            await remove_material(request.app, doc)
        materials.refresh(request.app)
    doc = request.app["state"].get("deck")
    return web.json_response(materials.public_document(doc) if doc else None)


async def references(request):
    operator(request)
    if request.method == "DELETE":
        writable(request)
        doc = request.app["state"].get("references", {}).pop(request.match_info["id"], None)
        if not doc:
            raise web.HTTPNotFound()
        await remove_material(request.app, doc)
        materials.refresh(request.app)
    return web.json_response([materials.public_document(d) for d in request.app["state"].get("references", {}).values()])


async def edit_material(request):
    writable(request)
    doc = materials.find_document(request.app, request.match_info["id"])
    if not doc:
        raise web.HTTPNotFound()
    body = await request.json()
    if body.get("revision") != doc.get("revision") or doc.get("analysis", {}).get("state") == "running":
        raise web.HTTPConflict(text="분석 결과가 바뀌었습니다. 다시 열어 주세요.")
    identity = body.get("id")
    match = next(((field, item) for field in ("people", "terms") for item in doc.get("analysis", {}).get(field, [])
                  if materials.entity_id(field, item) == identity), None)
    if not match:
        raise web.HTTPNotFound()
    field, _ = match
    key = "name" if field == "people" else "term"
    if body.get("restore"):
        doc["_edits"].pop(identity, None)
    else:
        edit = {"excluded": bool(body.get("excluded"))}
        if key in body:
            value = materials.dc.clean_text(body[key], 100)
            if not value:
                raise web.HTTPBadRequest(text="표기를 입력하세요.")
            edit[key] = value
        doc["_edits"][identity] = edit
    doc["revision"] += 1
    materials.refresh(request.app)
    return web.json_response(materials.analysis_view(doc))


async def deck_page(request):
    doc = materials.find_document(request.app, request.match_info["deck"])
    if request.app["auth"].role(request) is None:
        raise web.HTTPForbidden()
    if not doc or (doc["kind"] == "reference" and request.app["auth"].role(request) != "operator"):
        raise web.HTTPNotFound()
    page = request.match_info["page"]
    if not re.fullmatch(r"[1-9]\d*\.jpg", page) or not (settings.DECKS / doc["id"] / page).is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(settings.DECKS / doc["id"] / page, headers={"Cache-Control": "private, no-store"})
