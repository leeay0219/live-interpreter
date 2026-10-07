"""Offline integration checks for session isolation, multimodal evidence and operator workflows."""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch, AsyncMock

from aiohttp import FormData
from aiohttp.test_utils import TestClient, TestServer
from pdf_fixture import pdf_bytes

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import deck_context as dc
import event
import materials
import routes_live
import server
import settings
from workloads import BackgroundInference, ThreadLocalAWSClient


class Model:
    def __init__(self):
        self.requests = []
        self.failed_pages = set()

    def converse(self, **kw):
        self.requests.append(kw)
        payload = json.loads(kw["messages"][0]["content"][0]["text"])
        page = payload["page"]
        if page in self.failed_pages:
            raise RuntimeError("PRIVATE-DOCUMENT-CONTENT")
        result = {"summary": "Northstar 구조", "observed_text": "Northstar Mina Park",
                  "visual": "서비스에서 저장소로 향하는 화살표", "uncertain": "",
                  "people": [{"name": "Mina Park", "role": "발표 자료 작성자", "pages": [page]}],
                  "terms": [{"term": "Northstar", "meaning": "프로젝트", "pages": [page]},
                            {"term": "Invented", "meaning": "not in evidence", "pages": [page]}]}
        return {"output": {"message": {"content": [{"text": json.dumps(result)}]}},
                "usage": {"inputTokens": 100, "outputTokens": 50}}


def make_translator():
    tr = server.Translator.__new__(server.Translator)
    tr.event = event.Event(id="general")
    tr.document_brief, tr.skills, tr.usage, tr.topic = None, [], {}, ""
    tr.model, tr.bedrock, tr.engine = server.DEFAULT_MODEL, Model(), "claude"
    tr.recent, tr.shown, tr._warmed = [], {}, set()
    tr.context_version, tr.focus = 0, "presentation"
    tr._inference_slots = asyncio.Semaphore(6)
    tr.terminology = False
    tr._rebuild()
    return tr


def config(tr):
    return NS(event=tr.event, session=None, region="ap-northeast-2", model=tr.model, engine="claude",
              vocabulary=False, auto_lang=True, stability="high", provisional=False)


class NoAudioSession(server.Session):
    def start(self):
        self.task = asyncio.create_task(asyncio.sleep(3600))
        if self.logical_id.startswith("rehearsal"):
            asyncio.create_task(self.hub.send({"type": "caption", "id": "private",
                                             "tx": "PRIVATE REHEARSAL", "tx_lang": "ko"}))

    async def stop(self):
        self.accepting = False
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)


async def receive(ws, kind):
    async with asyncio.timeout(2):
        while True:
            message = await ws.receive_json()
            if message.get("type") == kind:
                return message


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.paths = patch.object(settings, "DECKS", Path(self.temp.name))
        self.paths.start()
        self.tr = make_translator()
        self.app = server.create_app(config(self.tr), self.tr)
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        self.session_patch = patch.object(routes_live, "Session", NoAudioSession)
        self.session_patch.start()
        self.headers = {"X-Operator-Id": "operator-a"}
        self.sid = self.app["state"]["session_id"]

    async def asyncTearDown(self):
        await self.client.close()
        self.session_patch.stop()
        self.paths.stop()
        self.temp.cleanup()

    async def upload(self, reference=False, text="Northstar", pages=1):
        data = FormData()
        data.add_field("file", pdf_bytes(text, pages), filename="source.pdf", content_type="application/pdf")
        response = await self.client.post("/api/references" if reference else "/api/deck",
                                          data=data, headers=self.headers)
        status = await self.finish(response)
        self.assertEqual(status["state"], "done", status)
        internal = materials.find_document(self.app, status["document"]["id"])
        if internal.get("_task"):
            await internal["_task"]
        return internal

    async def finish(self, response):
        """An upload answers 202 at once and renders in the background; wait for the background result."""
        self.assertEqual(response.status, 202, await response.text())
        identity = (await response.json())["upload_id"]
        async with asyncio.timeout(10):
            while self.app["uploads"][identity]["state"] == "rendering":
                await asyncio.sleep(0.02)
        return self.app["uploads"][identity]

    async def start(self, client="operator-a", intent="new"):
        ws = await self.client.ws_connect(f"/ws?client={client}")
        await receive(ws, "lifecycle")
        await ws.send_json({"type": "start", "intent": intent, "session_id": self.sid, "take": True})
        await receive(ws, "session")
        await receive(ws, "lifecycle")
        return ws

    async def test_image_only_pdf_is_analyzed_and_evidence_is_distinguished(self):
        doc = await self.upload(text="")
        self.assertEqual(doc["analysis"]["state"], "done")
        self.assertEqual(doc["analysis"]["terms"][0]["evidence"], "image")
        self.assertEqual([t["term"] for t in doc["analysis"]["terms"]], ["Northstar"])
        self.assertIn("image", self.tr.bedrock.requests[0]["messages"][0]["content"][1])
        self.assertIn("Northstar", self.tr.prompts[("en", "ko")])

    async def test_upload_answers_before_rendering_ends_and_takes_one_at_a_time(self):
        release = asyncio.Event()
        original = materials.render_upload
        async def slow(*args):
            await asyncio.wait_for(release.wait(), 5)
            return await original(*args)
        def form():
            data = FormData()
            data.add_field("file", pdf_bytes(), filename="large.pdf", content_type="application/pdf")
            return data
        with patch.object(materials, "render_upload", side_effect=slow):
            response = await asyncio.wait_for(self.client.post("/api/deck", data=form(), headers=self.headers), 2)
            self.assertEqual(response.status, 202)  # answered while rendering is still blocked
            busy = await self.client.post("/api/references", data=form(), headers=self.headers)
            self.assertEqual(busy.status, 409)
            identity = (await response.json())["upload_id"]
            polled = await (await self.client.get(f"/api/uploads/{identity}")).json()
            self.assertEqual(polled["state"], "rendering")
            release.set()
            status = await self.finish(response)
        self.assertEqual(status["state"], "done")
        self.assertEqual(self.app["state"]["deck"]["id"], status["document"]["id"])

    async def test_invalid_replacement_keeps_previous_document_and_server_available(self):
        original = await self.upload()
        data = FormData()
        data.add_field("file", b"invalid PDF", filename="broken.pdf", content_type="application/pdf")
        response = await self.client.post("/api/deck", data=data, headers=self.headers)
        status = await self.finish(response)
        self.assertEqual(status["state"], "error")
        self.assertIn("PDF를 읽지 못했습니다", status["error"])
        self.assertIs(self.app["state"]["deck"], original)
        health = await self.client.get("/healthz")
        self.assertEqual(await health.text(), "ok")
        self.assertEqual([p.name for p in Path(self.temp.name).iterdir()], [original["id"]])

    async def test_full_native_text_not_only_first_800_characters(self):
        text = "Northstar reference. " * 48 + "TAIL_REFERENCE"
        await self.upload(text=text)
        payload = json.loads(self.tr.bedrock.requests[0]["messages"][0]["content"][0]["text"])
        self.assertIn("TAIL_REFERENCE", payload["text"])
        self.assertGreater(len(payload["text"]), 800)

    async def test_partial_failure_keeps_successful_page_context(self):
        self.tr.bedrock.failed_pages = {2}
        doc = await self.upload(pages=2)
        self.assertEqual(doc["analysis"]["state"], "partial")
        self.assertEqual(doc["analysis"]["failed_pages"], [2])
        self.assertEqual(doc["analysis"]["analyzed_pages"], 1)
        self.assertNotIn("PRIVATE-DOCUMENT", json.dumps(materials.public_document(doc)))

    async def test_reference_is_context_only_and_not_served_to_viewers(self):
        deck = await self.upload()
        ref = await self.upload(reference=True)
        self.assertEqual(self.app["state"]["deck"]["id"], deck["id"])
        self.assertIn(ref["id"], self.app["state"]["references"])
        with patch.object(self.app["auth"], "role", return_value="viewer"):
            response = await self.client.get(ref["pages"][0]["url"])
            self.assertEqual(response.status, 404)
            response = await self.client.get("/api/references")
            self.assertEqual(response.status, 403)

    async def test_edit_exclude_restore_and_stale_revision(self):
        doc = await self.upload()
        view = materials.analysis_view(doc)
        item = view["terms"][0]
        url = f"/api/materials/{doc['id']}/edit"
        body = {"id": item["id"], "revision": view["revision"], "term": "Northstar X"}
        response = await self.client.post(url, json=body, headers=self.headers)
        self.assertEqual(response.status, 200)
        self.assertIn("Northstar X", self.tr.topic)
        self.assertIn("Northstar X", self.tr.prompts[("en", "ko")])
        self.assertEqual((await self.client.post(url, json=body, headers=self.headers)).status, 409)
        response = await self.client.post(url, json={**body, "revision": doc["revision"], "excluded": True},
                                          headers=self.headers)
        self.assertEqual(response.status, 200)
        self.assertEqual(self.tr.document_brief["terms"], [])
        await self.client.post(url, json={"id": item["id"], "revision": doc["revision"], "restore": True},
                               headers=self.headers)
        self.assertEqual(self.tr.document_brief["terms"][0]["term"], "Northstar")

    async def test_removing_and_replacing_document_clear_current_page(self):
        first = await self.upload()
        second = await self.upload(text="Replacement")
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual((await self.client.get(first["pages"][0]["url"])).status, 404)
        await self.client.delete("/api/deck", headers=self.headers)
        self.assertEqual(self.tr.topic, "")
        self.assertIsNone(self.tr.document_brief)

    async def test_analysis_retry_is_idempotent_while_running(self):
        doc = await self.upload()
        doc["analysis"]["state"] = "running"
        with patch.object(materials, "start_analysis") as start:
            r = await self.client.post("/api/deck/analysis", json={"deck_id": doc["id"]}, headers=self.headers)
            self.assertEqual(r.status, 200)
            start.assert_not_called()

    async def test_stale_deck_and_non_owner_cannot_change_context(self):
        doc = await self.upload()
        owner = await self.start()
        other = await self.client.ws_connect("/ws?client=operator-b")
        await receive(other, "lifecycle")
        await other.send_json({"type": "context", "session_id": self.sid, "deck_id": doc["id"], "page": 0, "focus": "qa"})
        self.assertIn("운영 창", (await receive(other, "control_error"))["detail"])
        await owner.send_json({"type": "context", "session_id": self.sid, "deck_id": "stale", "page": 0})
        self.assertIn("자료", (await receive(owner, "control_error"))["detail"])
        response = await self.client.post("/api/context", json={"session_id": self.sid, "deck_id": doc["id"]},
                                          headers={"X-Operator-Id": "operator-b"})
        self.assertEqual(response.status, 409)
        await other.close(); await owner.close()

    async def test_qa_preserves_global_reference_but_not_slide_priority(self):
        doc = await self.upload()
        materials.select_page(self.app, {"deck_id": doc["id"], "page": 0, "focus": "qa"})
        self.assertIn("Q&A", self.tr.topic)
        self.assertNotIn("Slide 1", self.tr.topic)
        self.assertIn("Northstar", self.tr.prompts[("en", "ko")])

    async def test_new_start_resets_recent_speech_pause_resume_preserves_it(self):
        self.tr.recent = [("en", "PREVIOUS SESSION")]
        self.tr.shown = {"old": "OLD"}
        ws = await self.start()
        self.assertEqual(self.tr.recent, [])
        self.assertEqual(self.tr.shown, {})
        self.tr.recent = [("en", "THIS SESSION")]
        self.app["record"].append({"tx": "kept"})
        await ws.send_json({"type": "pause"})
        self.assertEqual((await receive(ws, "lifecycle"))["phase"], "paused")
        await ws.send_json({"type": "start", "intent": "resume", "session_id": self.sid})
        await receive(ws, "session"); await receive(ws, "lifecycle")
        self.assertEqual(self.tr.recent, [("en", "THIS SESSION")])
        self.assertEqual(self.app["record"], [{"tx": "kept"}])
        await ws.close()

    async def test_new_session_resets_manual_settings_and_documents(self):
        await self.upload()
        self.tr.set_people([{"name": "Old person", "en": "Old person", "ko": "이전 인물"}])
        self.tr.recent = [("en", "old")]
        self.app["state"]["phase"] = "ended"
        response = await self.client.post("/api/session/new", json={"session_id": self.sid}, headers=self.headers)
        self.assertEqual(response.status, 200)
        self.assertNotEqual((await response.json())["session_id"], self.sid)
        self.assertEqual(self.tr.event.people, [])
        self.assertEqual(materials.documents(self.app), [])
        self.assertEqual(self.tr.recent, [])

    async def test_rehearsal_does_not_broadcast_or_change_record(self):
        viewer = await self.client.ws_connect("/ws")
        await receive(viewer, "lifecycle")
        self.app["record"].append({"tx": "public"})
        self.tr.recent = [("en", "public conversation")]
        rehearsal = await self.client.ws_connect("/ws/rehearsal")
        message = await receive(rehearsal, "caption")
        self.assertEqual(message["tx"], "PRIVATE REHEARSAL")
        self.assertEqual(self.app["hub"].history, [])
        self.assertEqual(self.app["record"], [{"tx": "public"}])
        self.assertEqual(self.tr.recent, [("en", "public conversation")])
        with self.assertRaises(asyncio.TimeoutError):
            await viewer.receive(timeout=0.05)
        await rehearsal.close(); await viewer.close()

    async def test_analysis_and_rehearsal_require_operator(self):
        with patch.object(self.app["auth"], "role", return_value="viewer"):
            for url in ("/api/deck/analysis", "/api/references", "/ws/rehearsal"):
                self.assertEqual((await self.client.get(url)).status, 403)

    async def test_translate_engine_does_not_analyze(self):
        self.tr.engine = "translate"
        doc = await self.upload()
        self.assertEqual(doc["analysis"]["state"], "unavailable")
        self.assertEqual(self.tr.bedrock.requests, [])

    async def test_upload_from_previous_session_is_discarded(self):
        entered, release = asyncio.Event(), asyncio.Event()
        original = materials.render_upload
        async def slow(*args):
            result = await original(*args)
            entered.set()
            await asyncio.wait_for(release.wait(), 3)
            return result
        data = FormData()
        data.add_field("file", pdf_bytes(), filename="old-session.pdf")
        with patch.object(materials, "render_upload", side_effect=slow):
            upload = asyncio.create_task(self.client.post("/api/deck", data=data, headers=self.headers))
            await asyncio.wait_for(entered.wait(), 3)
            try:
                reset = await self.client.post("/api/session/new", json={"session_id": self.sid}, headers=self.headers)
                self.assertEqual(reset.status, 200)
            finally:
                release.set()
            response = await upload
            status = await self.finish(response)
        self.assertEqual(status["state"], "error")
        self.assertIn("세션이 바뀌어", status["error"])
        polled = await self.client.get(f"/api/uploads/{(await response.json())['upload_id']}")
        self.assertEqual(polled.status, 404)  # the status of an upload belongs to its session
        self.assertEqual(materials.documents(self.app), [])


class BoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_aws_workers_use_distinct_clients_and_reuse_their_own(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        barrier = threading.Barrier(2)
        clients = []
        class SDKSession:
            def client(self, service, **options):
                client = NS(converse=lambda: (threading.get_ident(), len(clients)))
                clients.append(client)
                return client
        wrapper = ThreadLocalAWSClient(SDKSession(), "bedrock-runtime")
        # Passing a bound method on the main thread must not create the SDK client there.
        operation = wrapper.converse
        self.assertEqual(clients, [])
        def work():
            first = operation()
            own = wrapper.local.client
            barrier.wait(timeout=2)
            operation()
            return own, wrapper.local.client, first[0]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(await asyncio.gather(*(asyncio.get_running_loop().run_in_executor(pool, work) for _ in range(2))))
        self.assertEqual(len(clients), 2)
        self.assertIsNot(results[0][0], results[1][0])
        self.assertNotEqual(results[0][2], results[1][2])
        for first, second, _ in results:
            self.assertIs(first, second)

    async def test_bad_pdf_worker_cleans_up_without_crashing_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "broken"
            with self.assertRaises(ValueError):
                await materials.render_upload(b"not a PDF", out, 10)
            self.assertFalse(out.exists())

    async def test_old_analysis_completion_cannot_overwrite_replacement(self):
        tr = make_translator()
        first = {"id": "first", "name": "first", "pages": [{}], "_texts": ["first"], "_images": ["x"]}
        second = {"id": "second", "name": "second", "pages": [{}]}
        app = {"translator": tr, "state": {"deck": first}}
        entered, release = asyncio.Event(), asyncio.Event()
        async def slow(*args, **kwargs):
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            return {"summary": "OLD", "people": [], "terms": [], "failed_pages": [], "page_contexts": [{}]}
        with patch.object(dc, "analyze", side_effect=slow):
            materials.start_analysis(app, first)
            await entered.wait()
            app["state"]["deck"] = second
            materials.cancel(first); release.set()
            await first["_task"]
        self.assertIsNone(tr.document_brief)

    async def test_snapshot_freezes_context_version_and_names(self):
        tr = make_translator()
        tr.topic, tr.context_version = "page one", 4
        frozen = tr.snapshot()
        tr.topic, tr.context_version = "page two", 5
        tr.set_people([{"name": "New", "en": "New", "ko": "새 이름"}])
        self.assertEqual((frozen.topic, frozen.context_version), ("page one", 4))
        self.assertEqual(frozen.event.people, [])
        self.assertNotIn("새 이름", frozen.prompts[("en", "ko")])

    async def test_audio_queue_is_bounded_and_reports_overflow(self):
        tr = make_translator()
        session = server.Session(config(tr), server.Hub(), tr)
        for _ in range(50):
            session.feed_audio(bytes(3200))
        with self.assertRaises(asyncio.QueueFull):
            session.feed_audio(bytes(3200))
        self.assertEqual(session.audio.qsize(), 50)

    async def test_background_requests_are_serial_while_live(self):
        import threading
        active, peak = 0, 0
        lock = threading.Lock()
        def call():
            nonlocal active, peak
            import time
            with lock:
                active += 1; peak = max(peak, active)
            time.sleep(0.02)
            with lock:
                active -= 1
        pool = BackgroundInference(live=lambda: True)
        await asyncio.gather(*(pool.invoke(call, {}) for _ in range(4)))
        pool.close()
        self.assertEqual(peak, 1)

    async def test_stop_drains_last_caption_and_keeps_original_context(self):
        tr = make_translator()
        tr.topic, tr.context_version = "page one", 7
        tr.final = AsyncMock(return_value=("문맥을 유지한 마지막 자막입니다.", "test"))
        session = server.Session(config(tr), server.Hub(), tr)
        session._emitter = asyncio.create_task(session._emit())
        session._dispatch("last", "This is the final complete sentence.", "en")
        tr.topic, tr.context_version = "page two", 8
        await session.stop()
        self.assertEqual(len(session.record), 1)
        self.assertEqual(session.record[0]["topic"], "page one")
        self.assertEqual(session.record[0]["context_version"], 7)

    async def test_record_keeps_every_committed_utterance_with_its_status(self):
        tr = make_translator()
        replies = iter([("자막으로 나간 줄입니다.", "test"), ("", "skip"), RuntimeError("model down")])
        async def final(*_):
            reply = next(replies)
            if isinstance(reply, Exception):
                raise reply
            return reply
        tr.final = final
        session = server.Session(config(tr), server.Hub(), tr)
        session._emitter = asyncio.create_task(session._emit())
        for rid, text in (("a", "This line reaches the screen."), ("b", "Uh, so, the same again."), ("c", "This one fails to translate.")):
            session._dispatch(rid, text, "en")
        await session.stop()
        self.assertEqual([r["status"] for r in session.record], ["shown", "skipped", "failed"])
        self.assertEqual([r["src"] for r in session.record][2], "This one fails to translate.")
        self.assertIsNotNone(session.record[0]["shown_at"])
        self.assertIsNone(session.record[2]["shown_at"])
        self.assertLessEqual(session.record[0]["t"], session.record[0]["shown_at"])
        # the write-up reads what was said; filler the translator skipped stays only in the full record
        import process_recording as writeup
        self.assertEqual([s["text"] for s in writeup.live_segments(session.record, skipped=False)],
                         ["This line reaches the screen.", "This one fails to translate."])
        document = writeup.record_document(session.record, "세션")
        self.assertIn("나가지 않음 (번역 실패)", document)
        self.assertIn("나가지 않음 (건너뜀)", document)

    def test_record_entries_without_status_read_as_shown(self):
        import process_recording as writeup
        segs = writeup.live_segments([{"t": 1, "src_lang": "en", "src": "old", "tx": "옛 기록"}], skipped=False)
        self.assertEqual(segs[0]["status"], "shown")


class WriteupGuardTests(unittest.IsolatedAsyncioTestCase):
    """POST /api/writeup only for the ended session it was asked for, one at a time, and never while a stopped job's
    thread is still running."""

    async def asyncSetUp(self):
        tr = make_translator()
        self.app = server.create_app(config(tr), tr)
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        self.app["record"].append({"t": 1, "src_lang": "en", "src": "Hello.", "tx": "안녕하세요.", "status": "shown"})

    async def asyncTearDown(self):
        await self.client.close()

    def body(self, **kw):
        return {"lang": "ko", "template": "summary", "session_id": self.app["state"]["session_id"], **kw}

    async def test_refused_while_live_or_for_another_session(self):
        self.app["state"]["phase"] = "live"
        self.assertEqual((await self.client.post("/api/writeup", json=self.body())).status, 409)
        self.app["state"]["phase"] = "ended"
        self.assertEqual((await self.client.post("/api/writeup", json=self.body(session_id="old"))).status, 409)

    async def test_timed_out_job_stops_and_blocks_a_new_one_until_its_thread_ends(self):
        import threading
        release = threading.Event()
        seen = {}
        def slow(record, ev, session, region, model, out_dir, stem, cancelled, **options):
            seen["options"] = options
            release.wait(5)
            seen["cancelled"] = cancelled()
            raise server.writeup.WriteupCancelled()
        self.app["state"]["phase"] = "ended"
        with patch.object(settings, "WRITEUP_LIMIT", 0.2), patch.object(server.writeup, "writeup_live", side_effect=slow):
            first = (await (await self.client.post("/api/writeup", json=self.body())).json())["id"]
            async with asyncio.timeout(5):
                while self.app["writeups"][first]["state"] == "running":
                    await asyncio.sleep(0.05)
            self.assertEqual(self.app["writeups"][first]["state"], "error")
            self.assertEqual((await self.client.post("/api/writeup", json=self.body())).status, 409)  # thread still alive
            release.set()
            async with asyncio.timeout(5):
                while not self.app["writeup_worker"].done():
                    await asyncio.sleep(0.05)
        self.assertTrue(seen["cancelled"])
        self.assertEqual(seen["options"]["attempts"], 2)
        self.assertLessEqual(seen["options"]["read_timeout"], server.WRITEUP_CALL_TIMEOUT)


if __name__ == "__main__":
    unittest.main()
