"""Session record downloads and write-up jobs."""
import csv
import io
import sys
import tempfile
import asyncio
import json
import logging
import time
import uuid
import threading
from pathlib import Path
from urllib.parse import quote

from aiohttp import web

from postprocess.document import render_docx


sys.path.insert(0, str(Path(__file__).parent / "postprocess"))
import process_recording as writeup  # noqa: E402  (the same pipeline that writes up recordings)
from postprocess.html_document import CSP as DOCUMENT_CSP, render_document  # noqa: E402

import settings
from settings import WRITEUP_CALL_TIMEOUT, WRITEUP_KEEP, WRITEUP_MODEL
from access import writable

log = logging.getLogger("captions")


async def record_info(request):
    """How much of the last session was recorded (DELETE clears it)."""
    if request.app["auth"].role(request) != "operator":
        raise web.HTTPForbidden()
    rec = request.app["record"]
    if request.method == "DELETE":
        writable(request)
        if request.app["state"].get("phase") in ("live", "paused"):
            raise web.HTTPConflict(text="세션을 종료한 뒤 기록을 지워 주세요.")
        rec.clear()
    minutes = round((rec[-1]["t"] - rec[0]["t"]) / 60) if len(rec) > 1 else 0
    shown = sum(1 for r in rec if r.get("status", "shown") == "shown")
    return web.json_response({"lines": shown, "heard": len(rec), "minutes": minutes}, headers={"Cache-Control": "no-store"})


async def record_download(request):
    """The whole live record (time, language, what was heard, subtitle shown) right away, without a model."""
    app = request.app
    if app["auth"].role(request) != "operator":
        raise web.HTTPForbidden()
    record, fmt = list(app["record"]), request.match_info["fmt"]
    if not record:
        raise web.HTTPNotFound(text="기록이 없습니다.")
    stem = time.strftime("session-%Y%m%d-%H%M", time.localtime(record[0]["t"])) + "_전체기록"
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["시각", "언어", "받아쓴 원문", "화면에 나간 자막", "상태", "슬라이드"])
        t0 = record[0]["t"]
        for r in record:
            w.writerow([writeup.ts(r["t"] - t0), r["src_lang"], r["src"], r.get("tx", ""), writeup.STATUS_KO[r.get("status", "shown")],
                        r.get("topic", "")[:80]])
        body, ctype = ("\ufeff" + buf.getvalue()).encode(), "text/csv; charset=utf-8"  # BOM so Excel reads Korean
    elif fmt in ("md", "docx", "html"):
        md = writeup.record_document(record, app["cfg"].event.title or "세션")
        if fmt == "md":
            body, ctype = md.encode(), "text/markdown; charset=utf-8"
        elif fmt == "html":
            body = (await asyncio.to_thread(render_document, md, kind="record")).encode()
            ctype = "text/html; charset=utf-8"
        else:
            body = await asyncio.to_thread(render_docx, md)
            ctype = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    else:
        raise web.HTTPNotFound()
    return web.Response(body=body, headers={"Content-Type": ctype, "Cache-Control": "no-store",
                                            "X-Content-Type-Options": "nosniff",
                                            **({"Content-Security-Policy": DOCUMENT_CSP + "; frame-ancestors 'none'"} if fmt == "html" else {}),
                                            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(stem + '.' + fmt)}"})


async def start_writeup(request):
    """Write up the last session (summary, key points, quotes, open items) as a background job: it takes minutes, longer
    than CloudFront waits for one request. Results stay in memory only."""
    app = request.app
    if app["auth"].role(request) != "operator":
        raise web.HTTPForbidden()
    writable(request)
    state = app["state"]
    if state.get("phase") in ("live", "paused"):
        raise web.HTTPConflict(text="세션을 종료한 뒤 정리해 주세요.")
    body = await request.json() if request.can_read_body else {}
    if body.get("session_id") != state["session_id"]:
        raise web.HTTPConflict(text="세션이 바뀌었습니다. 새로 고친 뒤 다시 시도해 주세요.")
    worker = app.get("writeup_worker")
    if any(j["state"] == "running" for j in app["writeups"].values()) or (worker and not worker.done()):
        raise web.HTTPConflict(text="이전 정리 작업이 아직 끝나지 않았습니다. 잠시 뒤 다시 시도해 주세요.")
    if not app["record"]:
        raise web.HTTPBadRequest(text="정리할 기록이 없습니다.")
    lang, template = body.get("lang", "ko"), body.get("template", "summary")
    if lang not in ("ko", "en") or template not in ("summary", "minutes"):
        raise web.HTTPBadRequest(text="정리 언어나 형식이 올바르지 않습니다.")
    for old in [k for k, j in app["writeups"].items() if j["state"] != "running"][:-(WRITEUP_KEEP - 1) or None]:
        del app["writeups"][old]
    job_id = uuid.uuid4().hex[:10]
    cancel = threading.Event()
    job = app["writeups"][job_id] = {"state": "running", "started": time.time(), "lines": len(app["record"]),
                                   "stage": "시작하는 중", "lang": lang, "template": template,
                                   "session_id": state["session_id"], "cancel": cancel}
    record, cfg = list(app["record"]), app["cfg"]

    def run():
        with tempfile.TemporaryDirectory() as tmp:
            md, docx = writeup.writeup_live(record, cfg.event, cfg.session, cfg.region, WRITEUP_MODEL, Path(tmp),
                                            time.strftime("session-%Y%m%d-%H%M"), progress=lambda st: job.update(stage=st),
                                            lang=lang, template=template, read_timeout=WRITEUP_CALL_TIMEOUT, attempts=2,
                                            cancelled=cancel.is_set)
            review_path = md.with_suffix(".review.json")
            review = json.loads(review_path.read_text()) if review_path.exists() else None
            return md.name, md.read_text(), docx.read_bytes(), md.with_suffix(".html").read_text(), review

    async def go():
        # the thread cannot be killed: on timeout it is told to stop before its next model call, and a new write-up
        # waits until it has really ended (app["writeup_worker"])
        worker = app["writeup_worker"] = asyncio.ensure_future(asyncio.to_thread(run))
        try:
            job["name"], job["md"], job["docx"], job["html"], job["review"] = await asyncio.wait_for(asyncio.shield(worker), settings.WRITEUP_LIMIT)
            job["state"] = "done"
            log.info("write-up %s (%s, %s): %d lines in %.0fs", job_id, template, lang, job["lines"], time.time() - job["started"])
        except asyncio.TimeoutError:
            cancel.set()
            log.warning("write-up %s gave up after %ds (stage: %s)", job_id, settings.WRITEUP_LIMIT, job["stage"])
            job["state"], job["error"] = "error", f"{settings.WRITEUP_LIMIT // 60}분이 넘어 중단했습니다 ({job['stage']}). 다시 시도해 주세요."
        except writeup.WriteupCancelled:
            job["state"], job["error"] = "error", "새 세션을 준비해서 중단했습니다."
        except Exception as e:
            log.exception("write-up failed")
            job["state"], job["error"] = "error", str(e)[:300]

    asyncio.get_running_loop().create_task(go())
    return web.json_response({"id": job_id})


async def writeup_status(request):
    app = request.app
    if app["auth"].role(request) != "operator":
        raise web.HTTPForbidden()
    job = app["writeups"].get(request.match_info["id"])
    if not job:
        raise web.HTTPNotFound()
    fmt = request.match_info.get("fmt")
    if fmt and fmt not in ("docx", "md", "html", "review"):
        raise web.HTTPNotFound()
    if fmt and job["state"] != "done":
        raise web.HTTPConflict(text="문서 작성이 끝난 뒤 받을 수 있습니다.")
    if fmt == "review":
        if job.get("review") is None:
            raise web.HTTPNotFound()
        name = job["name"].removesuffix(".md") + ".review.json"
        return web.Response(body=json.dumps(job["review"], ensure_ascii=False, indent=2).encode(),
                            headers={"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store",
                                     "X-Content-Type-Options": "nosniff",
                                     "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})
    if fmt in ("docx", "md", "html") and job["state"] == "done":
        name = job["name"].replace(".md", f".{fmt}")
        if fmt == "html" and "html" not in job:
            job["html"] = await asyncio.to_thread(render_document, job["md"],
                                                 lang=job.get("lang", "ko"), kind=job.get("template", "summary"))
        body = job["docx"] if fmt == "docx" else job[fmt].encode()
        ctype = ("application/vnd.openxmlformats-officedocument.wordprocessingml.document" if fmt == "docx"
                 else "text/html; charset=utf-8" if fmt == "html" else "text/markdown; charset=utf-8")
        disposition = "inline" if fmt == "html" and request.query.get("view") == "1" else "attachment"
        return web.Response(body=body, headers={"Content-Type": ctype, "Cache-Control": "no-store",
                                                "X-Content-Type-Options": "nosniff",
                                                **({"Content-Security-Policy": DOCUMENT_CSP + "; frame-ancestors 'none'"} if fmt == "html" else {}),
                                                "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(name)}"})
    return web.json_response({"state": job["state"], "error": job.get("error"), "stage": job.get("stage"),
                              "md": job.get("md") if job["state"] == "done" else None,
                              "formats": ["html", "docx", "md"],
                              "review_available": job.get("review") is not None,
                              "seconds": round(time.time() - job["started"])}, headers={"Cache-Control": "no-store"})
