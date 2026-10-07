"""Owned browser check for write-up HTML, downloads, narrow screens and print layout."""
import asyncio
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aiohttp.test_utils import TestServer
from playwright.async_api import async_playwright
import server
from test_workflows import make_translator, config
from test_html_document import SAMPLE, fake_writeup
from postprocess.html_document import render_document


async def main():
    tr = make_translator()
    app = server.create_app(config(tr), tr)
    app["state"]["phase"] = "ended"
    app["record"].append({"t": 1, "src_lang": "ko", "src": "문서 갱신 주기를 줄입니다.", "tx": "Update documents more often.", "topic": ""})
    errors = []
    with patch.object(server.writeup, "writeup_live", side_effect=fake_writeup), tempfile.TemporaryDirectory() as tmp:
        async with TestServer(app) as site, async_playwright() as p:
            browser = await p.chromium.launch(channel="chrome", headless=True,
                args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"])
            context = await browser.new_context(viewport={"width": 1280, "height": 900}, permissions=["microphone"])
            page = await context.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            await page.goto(str(site.make_url("/")))
            await page.locator("#completed").wait_for(state="visible")
            assert not await page.locator("#preparation").is_visible()
            assert not await page.locator("#rehearse").is_visible()
            assert not await page.locator("#recent").is_visible()
            assert await page.locator("#recDl").is_visible()
            assert await page.locator("#sheetRecent").get_attribute("role") == "region"
            await page.screenshot(path="/tmp/li-completed-empty.png")
            await page.set_viewport_size({"width": 390, "height": 844})
            assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            record_box = await page.locator("#completed").bounding_box()
            document_box = await page.locator("#sheetRecent").bounding_box()
            assert document_box["y"] >= record_box["y"] + record_box["height"]
            await page.screenshot(path="/tmp/li-completed-mobile.png", full_page=True)
            await page.set_viewport_size({"width": 1280, "height": 900})
            await page.locator("#wuTemplate").select_option("minutes")
            await page.locator("#wuStart").click()
            await page.locator("#wuHtml").wait_for(state="visible")
            assert await page.locator("#wuReview").is_visible()
            assert await page.locator("#wuReview").get_attribute("open") is None
            await page.locator("#wuReview summary").click()
            async with page.expect_download() as review_pending:
                await page.locator("#wuReviewDownload").click()
            assert (await review_pending.value).suggested_filename.endswith(".review.json")
            await page.locator("#wuReview summary").click()
            async with page.expect_download() as pending:
                await page.locator("#wuHtml").click()
            download = await pending.value
            saved = Path(tmp) / download.suggested_filename
            await download.save_as(saved)
            assert saved.suffix == ".html"
            await page.screenshot(path="/tmp/li-html-options.png")
            async with page.expect_popup() as popup:
                await page.locator("#wuOpen").click()
            document = await popup.value
            await document.wait_for_load_state()
            assert await document.locator("h1").count() == 1
            assert await document.locator("table").count() == 1
            await document.screenshot(path="/tmp/li-html-desktop.png", full_page=True)
            await document.locator(".contents summary").click()
            await document.locator('.contents a').first.click()
            assert "#section-" in document.url
            await document.goto(saved.as_uri())
            await document.set_viewport_size({"width": 390, "height": 844})
            await document.screenshot(path="/tmp/li-html-mobile.png", full_page=True)
            assert await document.evaluate("document.documentElement.scrollWidth <= innerWidth")
            await document.emulate_media(media="print")
            await document.set_viewport_size({"width": 794, "height": 1123})
            await document.screenshot(path="/tmp/li-html-print.png", full_page=True)
            assert not await document.locator(".contents").is_visible()
            assert not errors, errors
            # Preparing another session is the only route back to microphone setup.
            page.on("dialog", lambda dialog: dialog.accept())
            await page.locator("#newSession").click()
            await page.locator("#preparation").wait_for(state="visible")
            assert not await page.locator("#completed").is_visible()
            assert await page.locator("#rehearse").is_visible()
            assert not await page.locator("#recent").is_visible()
            await browser.close()
    examples = Path(__file__).resolve().parents[1] / "out" / "examples"
    examples.mkdir(parents=True, exist_ok=True)
    (examples / "meeting-notes.html").write_text(render_document(SAMPLE.read_text(), kind="minutes"))
    print("HTML browser check passed: generate, download, open, contents, offline file, mobile and print.")


if __name__ == "__main__":
    asyncio.run(main())
