"""Regression checks for the public-release audit; no AWS or operator-server access."""
import asyncio
import io
import logging
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
from xml.etree import ElementTree as ET

from aiohttp import web, WSServerHandshakeError
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
from security import SafeAccessLogger, validate_bind
from postprocess.document import render_docx
from test_workflows import make_translator, config


def application():
    tr = make_translator()
    app = server.create_app(config(tr), tr)
    app["auth"] = server.Auth(None, None)
    return app


class BoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = application()
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    async def test_foreign_and_null_origins_cannot_read_websocket_or_mutate_http(self):
        sid = self.app["state"]["session_id"]
        for origin in ("https://example.invalid", "null", "file://", "http://user@localhost"):
            headers = {"Origin": origin}
            for endpoint in ("/ws", "/ws/rehearsal"):
                with self.assertRaises(WSServerHandshakeError) as error:
                    await self.client.ws_connect(endpoint, headers=headers)
                self.assertEqual(error.exception.status, 403)
            response = await self.client.post("/api/session/new", json={"session_id": sid}, headers=headers)
            self.assertEqual(response.status, 403)
        self.assertEqual(self.app["state"]["session_id"], sid)

    async def test_rebinding_host_and_browser_mutation_without_origin_are_rejected(self):
        self.assertEqual((await self.client.get("/api/session", headers={"Host": "example.invalid"})).status, 403)
        response = await self.client.post("/api/session/new", json={},
                                          headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(response.status, 403)
        response = await self.client.get("/api/session")
        self.assertEqual(response.status, 200)  # local command-line clients

    async def test_login_form_posts_with_null_origin_from_the_same_site_only(self):
        # Referrer-Policy: no-referrer makes browsers send "Origin: null" for the login form's POST
        self.app["auth"] = server.Auth("test-password", "test-view-key")
        headers = {"Host": "meeting.example.com", "Via": "1.1 cloudfront", "Origin": "null"}
        form = {"password": "test-password"}
        same = await self.client.post("/login", data=form, headers={**headers, "Sec-Fetch-Site": "same-origin"}, allow_redirects=False)
        self.assertEqual(same.status, 302)
        self.assertIn("live_interpreter", same.headers.get("Set-Cookie", ""))
        for site in ("cross-site", "same-site", None):
            extra = {"Sec-Fetch-Site": site} if site else {}
            refused = await self.client.post("/login", data=form, headers={**headers, **extra}, allow_redirects=False)
            self.assertEqual(refused.status, 403, site)

    async def test_static_files_revalidate_so_a_deploy_never_mixes_old_scripts_with_a_new_server(self):
        response = await self.client.get("/static/captions.js")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-cache")
        again = await self.client.get("/static/captions.js", headers={"If-Modified-Since": response.headers["Last-Modified"]})
        self.assertEqual(again.status, 304)
        self.assertEqual((await self.client.get("/api/session")).headers["Cache-Control"], "no-store")

    async def test_same_origin_browser_and_cloudfront_origin_work(self):
        origin = str(self.client.make_url("/")).rstrip("/")
        async with self.client.ws_connect("/ws", headers={"Origin": origin}) as ws:
            self.assertEqual((await ws.receive_json())["type"], "lifecycle")
        response = await self.client.get("/api/session", headers={"Origin": origin})
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
        self.app["auth"] = server.Auth("test-password", "test-view-key")
        headers = {"Host": "meeting.example.com", "Via": "1.1 cloudfront", "Origin": "https://meeting.example.com"}
        response = await self.client.get("/healthz", headers=headers)
        self.assertEqual(response.status, 200)
        headers["Origin"] = "http://meeting.example.com"
        self.assertEqual((await self.client.get("/healthz", headers=headers)).status, 403)

    async def test_access_log_excludes_query_and_referrer(self):
        app = application()
        app["auth"] = server.Auth("test-password", "test-view-key")
        logger = logging.getLogger("test.safe-access")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger.addHandler(handler)
        site = TestServer(app)
        try:
            await site.start_server(access_log=logger, access_log_class=SafeAccessLogger)
            async with TestClient(site) as client:
                response = await client.get("/?k=" + app["auth"].operator_key, allow_redirects=False,
                                            headers={"Referer": "https://example.invalid/?private=PRIVATE-REFERRER"})
                await response.read()
                self.assertEqual(response.status, 302)
                await asyncio.sleep(0)
            log = stream.getvalue()
            self.assertIn("GET / 302", log)
            self.assertNotIn(app["auth"].operator_key, log)
            self.assertNotIn("PRIVATE-REFERRER", log)
            self.assertNotIn("?k=", log)
        finally:
            logger.removeHandler(handler)
            await site.close()


class BindTests(unittest.TestCase):
    def test_unauthenticated_bind_is_loopback_only(self):
        for host in ("127.0.0.1", "::1", "localhost"):
            validate_bind(host, None)
        for host in ("0.0.0.0", "::", "192.0.2.1", "example.invalid"):
            with self.assertRaises(ValueError):
                validate_bind(host, None)
            validate_bind(host, "configured-password")


class DocumentTests(unittest.IsolatedAsyncioTestCase):
    async def test_docx_cannot_read_images_or_fetch_urls_and_record_stays_literal(self):
        fetched = []
        image_server = web.Application()
        async def resource(request):
            fetched.append(request.path)
            return web.Response(text="must not be fetched")
        image_server.router.add_get("/private.png", resource)
        with tempfile.TemporaryDirectory() as tmp, Image.new("RGB", (8, 8), "orange") as image:
            path = Path(tmp) / "private.png"
            image.save(path)
            async with TestServer(image_server) as remote:
                text = (f"![local secret]({path})\n\n![remote secret]({remote.make_url('/private.png')})"
                        "\n\n<img src='file:///private.png'>\n\n[unsafe](file:///private.txt)")
                app = application()
                app["record"].append({"t": 0, "src_lang": "en", "src": text, "tx": "**literal**"})
                async with TestClient(TestServer(app)) as client:
                    response = await client.get("/api/record/docx")
                    self.assertEqual(response.status, 200)
                    record_docx = await response.read()
                # Both transcript and AI-authored write-up paths are protected.
                generated_docx = await asyncio.to_thread(render_docx, "# Notes\n\n" + text)
                for document in (record_docx, generated_docx):
                    with zipfile.ZipFile(io.BytesIO(document)) as archive:
                        self.assertFalse(any(name.startswith("word/media/") for name in archive.namelist()))
                        relationships = archive.read("word/_rels/document.xml.rels").decode()
                        self.assertNotIn('Target="file:', relationships)
                with zipfile.ZipFile(io.BytesIO(record_docx)) as archive:
                    tree = ET.fromstring(archive.read("word/document.xml"))
                    content = "".join(tree.itertext())
                    self.assertIn(f"![local secret]({path})", content)
                    self.assertIn("**literal**", content)
                self.assertEqual(app["record"][0]["src"], text)
                self.assertEqual(fetched, [])

    async def test_docx_preserves_headings_tables_and_korean(self):
        data = await asyncio.to_thread(render_docx, "# 회의록\n\n설비를 확인합니다.\n\n| 할 일 | 담당 |\n|---|---|\n| 확인 | 미정 |")
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            root = ET.fromstring(archive.read("word/document.xml"))
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            self.assertEqual(len(root.findall(".//w:tbl", ns)), 1)
            self.assertIn("설비를 확인합니다.", "".join(root.itertext()))


if __name__ == "__main__":
    unittest.main()
