"""HTML export, preserved source content and passive document rendering."""
import asyncio
import json
from html.parser import HTMLParser
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiohttp.test_utils import TestClient, TestServer

import server
import event
from postprocess.html_document import clean_prose, render_document
from test_workflows import make_translator, config

SAMPLE = Path(__file__).parent / "fixtures" / "writeup-sample.md"


class Elements(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.tags = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def fake_writeup(record, ev, session, region, model, out_dir, stem, **options):
    md = out_dir / (stem + ".md")
    md.write_text(SAMPLE.read_text())
    md.with_suffix(".docx").write_bytes(b"test Word")
    md.with_suffix(".html").write_text(render_document(md.read_text(), lang=options["lang"], kind=options["template"]))
    md.with_suffix(".review.json").write_text(json.dumps({"review_notes": [{"issue": "private audit detail"}]}))
    return md, md.with_suffix(".docx")


class HtmlDocumentTests(unittest.TestCase):
    def test_short_session_is_one_document_without_repeated_overview_or_review_appendix(self):
        ev = event.Event(id="sample", title="검색 점검")
        server.writeup.configure(ev, [("1부", "", "")], "ko", "minutes")
        doc = server.writeup.build_doc(None, "test-model", [{"end": 20}], {}, {
            1: "### 논의 내용\n문서가 늦게 갱신됩니다.\n### 결정 사항\n매시간 갱신합니다.\n### 할 일\n기한은 미정입니다."
        }, {1: [{"where": "a", "problem": "internal audit", "fix": "b"}]}, "", {}, live=True)
        self.assertEqual(doc.count("## 결정 사항"), 1)
        self.assertNotIn("1부", doc)
        self.assertNotIn("internal audit", doc)
        self.assertIn("작성 기준", doc)

    def test_document_has_embedded_styles_print_rules_and_working_contents(self):
        doc = render_document(SAMPLE.read_text(), kind="minutes")
        tags = Elements(doc).tags
        self.assertEqual(sum(tag == "h1" for tag, _ in tags), 1)
        self.assertIn("@media print", doc)
        self.assertIn('lang="ko"', doc)
        ids = {a["id"] for _, a in tags if "id" in a}
        anchors = [a["href"][1:] for tag, a in tags if tag == "a" and a.get("href", "").startswith("#")]
        self.assertTrue(anchors)
        self.assertTrue(all(a in ids for a in anchors))
        self.assertIn("<table>", doc)
        self.assertFalse(any(tag in ("script", "link", "img", "iframe") for tag, _ in tags))

    def test_untrusted_markdown_cannot_load_resources_or_execute_markup(self):
        md = '# <img src=x onerror="alert(1)">\n\n<script>alert(2)</script>\n\n' \
             '![private image](https://example.com/leak)\n\n[click](javascript:alert(3))\n\n' \
             '[safe](https://example.com)\n\n## A & B\n\nplain <iframe src="https://example.com"></iframe>'
        tags = Elements(render_document(md)).tags
        self.assertFalse(any(tag in ("script", "img", "iframe", "object") for tag, _ in tags))
        self.assertFalse(any(k.startswith("on") or k in ("src", "style") for _, attrs in tags for k in attrs))
        self.assertFalse(any(a.get("href", "").startswith("javascript:") for _, a in tags))
        self.assertTrue(any(a.get("href") == "https://example.com" for _, a in tags))

    def test_editorial_punctuation_preserves_quotes_code_urls_and_ranges(self):
        md = "회의·발표 — 검토합니다.\n10–20개\n> 원문·인용 — 그대로\n`a·b—c`\n" \
             '```\nx — y\n```\n[문서·보기](https://example.com/a—b)\n$x·y$\n인용: "A — B"'
        result = clean_prose(md)
        self.assertIn("회의, 발표, 검토합니다.", result)
        self.assertIn("10-20개", result)
        for exact in ("> 원문·인용 — 그대로", "`a·b—c`", "x — y", "https://example.com/a—b", "$x·y$", '"A — B"'):
            self.assertIn(exact, result)


class HtmlApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tr = make_translator()
        self.app = server.create_app(config(tr), tr)
        self.app["record"].append({"t": 1, "src_lang": "ko", "src": "원문·표기 — 유지", "tx": "Original — text", "topic": ""})
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    async def test_record_html_preserves_transcript_without_calling_a_model(self):
        response = await self.client.get("/api/record/html")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.content_type, "text/html")
        self.assertIn("원문·표기 — 유지", await response.text())
        self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assertIn("default-src 'none'", response.headers["Content-Security-Policy"])
        self.assertEqual(self.app["translator"].bedrock.requests, [])

    async def test_writeup_download_and_view_use_same_generated_document(self):
        with patch.object(server.writeup, "writeup_live", side_effect=fake_writeup):
            response = await self.client.post("/api/writeup", json={"lang": "ko", "template": "minutes",
                                                                    "session_id": self.app["state"]["session_id"]})
            identity = (await response.json())["id"]
            async with asyncio.timeout(5):
                while self.app["writeups"][identity]["state"] == "running":
                    await asyncio.sleep(.02)
        status = await (await self.client.get(f"/api/writeup/{identity}")).json()
        self.assertIn("html", status["formats"])
        self.assertTrue(status["review_available"])
        self.assertNotIn("private audit detail", status["md"])
        download = await self.client.get(f"/api/writeup/{identity}/html")
        view = await self.client.get(f"/api/writeup/{identity}/html?view=1")
        self.assertEqual(download.status, 200)
        self.assertEqual(await download.text(), await view.text())
        self.assertTrue(view.headers["Content-Disposition"].startswith("inline"))
        self.assertTrue(download.headers["Content-Disposition"].startswith("attachment"))
        self.assertEqual((await self.client.get(f"/api/writeup/{identity}/exe")).status, 404)
        review = await self.client.get(f"/api/writeup/{identity}/review")
        self.assertIn("private audit detail", await review.text())
        self.assertIn("attachment", review.headers["Content-Disposition"])

    async def test_viewer_cannot_download_session_documents(self):
        self.app["writeups"]["private"] = {"state": "done", "started": time.time(), "md": "private"}
        with patch.object(self.app["auth"], "role", return_value="viewer"):
            for path in ("/api/record/html", "/api/writeup/private/html", "/api/writeup/private/html?view=1", "/api/writeup/private/review"):
                self.assertEqual((await self.client.get(path)).status, 403)


if __name__ == "__main__":
    unittest.main()
