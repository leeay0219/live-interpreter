"""Session-scoped documents and authoritative, versioned interpretation context."""
import asyncio
from contextlib import closing
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

from aiohttp import web

import deck_context as dc

MAX_REFERENCES = 4
MAX_UPLOAD_BYTES = 40 * 1024 * 1024


def documents(app):
    state = app["state"]
    return ([state["deck"]] if state.get("deck") else []) + list(state.get("references", {}).values())


def find_document(app, identity):
    return next((d for d in documents(app) if d["id"] == identity), None)


def public_document(doc):
    return {k: v for k, v in doc.items() if not k.startswith("_")}


def entity_id(field, item):
    return hashlib.sha256(json.dumps([field, item.get("name", item.get("term")), item.get("pages")],
                                     ensure_ascii=False).encode()).hexdigest()[:16]


def effective_brief(doc):
    analysis = doc.get("analysis", {})
    result = {"summary": analysis.get("summary", ""), "people": [], "terms": []}
    for field in ("people", "terms"):
        for original in analysis.get(field, []):
            identity = entity_id(field, original)
            edit = doc.get("_edits", {}).get(identity, {})
            key = "name" if field == "people" else "term"
            if edit.get("excluded") or key in edit:
                result["summary"] = replace_spelling(result["summary"], original[key],
                                                     "" if edit.get("excluded") else edit[key])
            if edit.get("excluded"):
                continue
            item = {**original, **edit, "id": identity, "document_id": doc["id"], "document_name": doc["name"]}
            if edit:
                item["evidence"] = "manual"
            result[field].append(item)
    return result


def replace_spelling(text, old, new):
    return re.sub(r"(?<![A-Za-z0-9_])" + re.escape(old) + r"(?![A-Za-z0-9_])",
                  lambda _: new, text, flags=re.I)


def refresh(app):
    tr, state = app["translator"], app["state"]
    briefs = [effective_brief(d) for d in documents(app) if d.get("analysis", {}).get("state") in ("done", "partial")]
    combined = {"summary": dc.clean_text(" ".join(b["summary"] for b in briefs), 1200), "people": [], "terms": []}
    for field, cap in (("people", 12), ("terms", 40)):
        # Do not merge page numbers from different documents into the first document's evidence.
        combined[field] = [item for brief in briefs for item in brief[field]][:cap]
    while len(json.dumps(combined, ensure_ascii=False)) > dc.MAX_BRIEF_CHARS:
        combined["terms" if combined["terms"] else "people"].pop()
    tr.set_document_brief(combined if briefs else None)
    state["context_version"] = state.get("context_version", 0) + 1
    tr.context_version = state["context_version"]
    update_topic(app)


def update_topic(app):
    state, tr = app["state"], app["translator"]
    deck = state.get("deck")
    focus = state.get("focus", "presentation")
    tr.focus = focus
    if focus == "qa":
        tr.set_topic("Q&A: prioritize the current question and recent speech. Document reference is optional background. "
                     "Do not force the question to match the last slide.")
    elif deck:
        page = min(state.get("page", 0), len(deck["pages"]) - 1)
        context = next((p for p in deck.get("analysis", {}).get("page_contexts", []) if p["page"] == page + 1), {})
        # Entity edits are applied to the current page context as well as the global brief.
        text = " ".join([deck["pages"][page].get("text", ""), context.get("summary", ""),
                         context.get("visual", ""), context.get("observed_text", "")])
        for field in ("people", "terms"):
            for original in deck.get("analysis", {}).get(field, []):
                edit = deck.get("_edits", {}).get(entity_id(field, original), {})
                key = "name" if field == "people" else "term"
                if edit.get("excluded") or key in edit:
                    text = replace_spelling(text, original[key], edit.get(key, "") if not edit.get("excluded") else "")
        tr.set_topic(f"Slide {page + 1} of {len(deck['pages'])}: {text[:2000]}")
    else:
        tr.set_topic("")


def select_page(app, body):
    state = app["state"]
    deck = state.get("deck")
    if body.get("deck_id") != (deck or {}).get("id"):
        raise web.HTTPConflict(text="발표 자료가 바뀌었습니다. 새로 고쳐 주세요.")
    page = body.get("page", 0)
    if type(page) is not int or page < 0 or (deck and page >= len(deck["pages"])) or (not deck and page != 0):
        raise web.HTTPBadRequest(text="페이지 번호가 올바르지 않습니다.")
    focus = body.get("focus", state.get("focus", "presentation"))
    if focus not in ("presentation", "qa"):
        raise web.HTTPBadRequest(text="발표 또는 질의응답을 선택하세요.")
    state.update(page=page, focus=focus, context_version=state.get("context_version", 0) + 1)
    app["translator"].context_version = state["context_version"]
    update_topic(app)
    return {"type": "context", "deck_id": (deck or {}).get("id"), "page": page, "focus": focus,
            "version": state["context_version"]}


async def read_upload(request):
    reader = await request.multipart()
    field = await reader.next()
    if field is None or not field.filename:
        raise web.HTTPBadRequest(text="PDF 파일을 선택하세요.")
    name = Path(field.filename).name[:160]
    if not name.lower().endswith(".pdf"):
        raise web.HTTPBadRequest(text="PDF만 올릴 수 있습니다. PowerPoint에서 PDF로 저장해서 올려 주세요.")
    data = bytearray()
    while chunk := await field.read_chunk(64 * 1024):
        data.extend(chunk)
        if len(data) > MAX_UPLOAD_BYTES:
            raise web.HTTPRequestEntityTooLarge(max_size=MAX_UPLOAD_BYTES, actual_size=len(data))
    return name, bytes(data)


def render_pdf(data, out, max_pages):
    import pypdfium2 as pdfium
    out.mkdir(parents=True)
    pages, texts, images = [], [], []
    total = 0
    try:
        with pdfium.PdfDocument(data) as pdf:
            if not 1 <= len(pdf) <= max_pages:
                raise ValueError(f"PDF는 1쪽부터 {max_pages}쪽까지 올릴 수 있습니다.")
            for number in range(1, len(pdf) + 1):
                path = out / f"{number}.jpg"
                with closing(pdf[number - 1]) as page:
                    width, height = page.get_size()
                    scale = min(1920 / max(width, 1), 2400 / max(height, 1))
                    bitmap = page.render(scale=scale)
                    try:
                        with bitmap.to_pil() as image, image.convert("RGB") as rgb:
                            rgb.save(path, format="JPEG", quality=85)
                    finally:
                        bitmap.close()
                    with closing(page.get_textpage()) as textpage:
                        text = " ".join(textpage.get_text_bounded().split())
                total += path.stat().st_size
                if total > 160 * 1024 * 1024:
                    raise ValueError("렌더링된 자료가 너무 큽니다. PDF를 나눠 주세요.")
                texts.append(text)
                pages.append({"url": f"/decks/{out.name}/{number}.jpg", "text": text[:800]})
                images.append(str(path))
        dc.chunks(texts)
        return pages, texts, images
    except Exception:
        shutil.rmtree(out, ignore_errors=True)
        raise


async def render_upload(data, out, max_pages):
    """Keep PDF native code outside the process serving audio and TLS connections."""
    process = await asyncio.create_subprocess_exec(
        sys.executable, str(Path(__file__).with_name("pdf_worker.py")), str(out), str(max_pages),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(data), 120)
        result = json.loads(stdout) if process.returncode == 0 else {}
        if "pages" not in result:
            raise ValueError("PDF를 읽지 못했습니다. 암호, 페이지 수 또는 파일 크기를 확인하세요.")
        return result["pages"], result["texts"], result["images"]
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        shutil.rmtree(out, ignore_errors=True)
        raise


def cancel(doc):
    task = doc.get("_task")
    if task and not task.done():
        task.cancel()


def start_analysis(app, doc):
    cancel(doc)
    doc["revision"] = doc.get("revision", 0) + 1
    job = doc["analysis"] = {
        "state": "running", "stage": "분석 대기 중", "total_pages": len(doc["pages"]),
        "text_pages": sum(bool(t) for t in doc["_texts"]),
        "note": "텍스트와 페이지 이미지를 함께 분석합니다. 이미지에서 읽은 표기와 시각적 해석은 원본에서 확인할 수 있습니다.",
    }
    if app["translator"].engine != "claude":
        job.update(state="unavailable", stage="Translate 모드에서는 자료 분석을 사용하지 않습니다.")
        return
    # Keep the analysis client's timeouts separate from latency-sensitive translation.
    client = app.get("analysis_client", app["translator"].bedrock)
    pool = app.get("background")
    def progress(stage):
        job["stage"] = stage
    def usage(u):
        stats = app["state"].setdefault("analysis_usage", {"calls": 0, "inputTokens": 0, "outputTokens": 0})
        stats["calls"] += 1
        for key in ("inputTokens", "outputTokens"):
            stats[key] += u.get(key, 0)
    async def run():
        try:
            result = await asyncio.wait_for(dc.analyze(
                client, app["translator"].model, doc["_texts"], progress, usage,
                images=doc["_images"], invoke=pool.invoke if pool else None), dc.ANALYSIS_TIMEOUT)
            if find_document(app, doc["id"]) is not doc or doc.get("analysis") is not job:
                return
            failed = result.get("failed_pages", [])
            count = len(result.get("page_contexts", []))
            job.update(result, state="done" if not failed else "partial" if count else "error",
                       stage="통역에 반영됨" if not failed else f"{count}/{len(doc['pages'])}쪽 반영됨",
                       analyzed_pages=count)
            doc["revision"] += 1
            refresh(app)
        except asyncio.CancelledError:
            raise
        except Exception:
            if find_document(app, doc["id"]) is doc and doc.get("analysis") is job:
                job.update(state="error", stage="분석을 마치지 못했습니다. 다시 시도하세요.")
    doc["_task"] = asyncio.create_task(run())


def analysis_view(doc):
    brief = effective_brief(doc)
    excluded = []
    for field in ("people", "terms"):
        for item in doc.get("analysis", {}).get(field, []):
            identity = entity_id(field, item)
            if doc.get("_edits", {}).get(identity, {}).get("excluded"):
                excluded.append({"id": identity, "label": item.get("name", item.get("term"))})
    return {**doc.get("analysis", {}), **brief, "deck_id": doc["id"], "revision": doc.get("revision", 0),
            "excluded": excluded}
