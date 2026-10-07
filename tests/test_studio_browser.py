"""Owned, isolated browser/server smoke test. No AWS calls and no connection to the live server."""
import asyncio
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from pdf_fixture import pdf_bytes
from aiohttp.test_utils import TestServer
from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import routes_live
import server
import settings
from test_workflows import make_translator, config, NoAudioSession


async def run():
    errors = []
    with tempfile.TemporaryDirectory() as tmp, patch.object(settings, "DECKS", Path(tmp) / "decks"), patch.object(routes_live, "Session", NoAudioSession):
        tr = make_translator()
        app = server.create_app(config(tr), tr)
        test_server = TestServer(app)
        await test_server.start_server()
        payload = {"name": "Northstar.pdf", "mimeType": "application/pdf",
                   "buffer": pdf_bytes("Northstar - Mina Park")}
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(channel="chrome", headless=True,
                    args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"])
                context = await browser.new_context(viewport={"width": 1280, "height": 800}, permissions=["microphone"])
                await context.add_init_script("""{
                  const get = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
                  window.testStreams = [];
                  navigator.mediaDevices.getUserMedia = async (...args) => {
                    const stream = await get(...args); window.testStreams.push(stream); return stream;
                  };
                }""")
                page = await context.new_page()
                page.on("pageerror", lambda e: errors.append(str(e)))
                await page.goto(str(test_server.make_url("/")))
                await page.locator("#input option").first.wait_for(state="attached")
                # A rejected upload remains actionable at the file control, then retry succeeds.
                async def failed_upload(route):
                    await route.abort("connectionreset")
                await page.route("**/api/deck", failed_upload)
                await page.locator("#deckFile").set_input_files(payload)
                await page.locator("#deckError").get_by_text("서버 연결이 끊겨", exact=False).wait_for()
                assert not await page.locator("#drop").is_disabled()
                assert not await page.locator("#setupError").text_content()
                await page.unroute("**/api/deck", failed_upload)
                await page.locator("#deckFile").set_input_files(payload)
                await page.locator("#analysisView").wait_for(state="visible")
                assert not await page.locator("#deckError").text_content()
                actions = [await page.locator("#" + name).bounding_box() for name in ("analysisView", "deckChange", "deckRemove")]
                assert len({a["y"] for a in actions}) == 1, "document actions should form one row"
                assert (await page.locator("#rehearse").bounding_box())["y"] < (await page.locator("#input").bounding_box())["y"]
                await page.locator("#analysisView").click()
                await page.get_by_role("button", name="근거와 보정").first.click()
                await page.locator("#evidenceImage").wait_for()
                await page.locator("#evidenceSpelling").fill("Mina Park X")
                await page.locator("#evidenceSave").click()
                await page.get_by_text("다음 발화부터 적용됩니다.", exact=True).wait_for()
                await page.locator("#sheetEvidence [data-close]").click()
                await page.locator("#referenceButton").click()
                await page.locator("#referenceFile").set_input_files(payload)
                await page.get_by_role("button", name="참고 자료 1개", exact=True).wait_for()
                await page.locator("#references").get_by_role("button", name="분석 보기").click()
                await page.locator("#evidencePage").wait_for()
                await page.locator("#sheetEvidence [data-close]").click()
                await page.screenshot(path="/tmp/li-studio-desktop.png")
                await page.locator("#rehearse").click()
                await page.locator("#rehearsalStart").click()
                await page.get_by_text("PRIVATE REHEARSAL", exact=True).wait_for()
                assert app["hub"].history == [], "rehearsal leaked to live hub"
                await page.locator("#rehearsalStop").click()
                await page.locator("#sheetRehearsal [data-close]").click()
                await page.locator("#start").click()
                await page.locator("#live").wait_for(state="visible")
                await page.locator("#pause").click()
                await page.locator("#pause").get_by_text("이어서 하기", exact=True).wait_for()
                await page.locator("#pause").click()
                await page.locator("#pause").get_by_text("잠시 멈춤", exact=True).wait_for()
                await page.evaluate("""() => window.testStreams.flatMap(s => s.getAudioTracks()).filter(t => t.readyState === 'live')
                  .forEach(t => { t.stop(); t.dispatchEvent(new Event('ended')); })""")
                await page.locator("#pause").get_by_text("이어서 하기", exact=True).wait_for()
                await page.locator("#pause").click()
                await page.locator("#pause").get_by_text("잠시 멈춤", exact=True).wait_for()
                await page.locator("#focusMode").click()
                await page.get_by_role("button", name="발표로 돌아가기", exact=True).wait_for()
                page.on("dialog", lambda dialog: dialog.accept())
                await page.locator("#stop").click()
                await page.locator("#newSession").wait_for(state="visible")
                await page.locator("#newSession").click()
                try:
                    await page.locator("#drop").wait_for(state="visible", timeout=5000)
                except Exception:
                    print("new-session error:", await page.locator("#setupError").text_content(), errors, flush=True)
                    print("phase:", app["state"]["phase"], "source:", bool(app["state"].get("source")),
                          "rehearsal:", app["state"].get("rehearsal"), flush=True)
                    raise
                await page.set_viewport_size({"width": 390, "height": 844})
                await page.screenshot(path="/tmp/li-studio-mobile-setup.png", full_page=True)
                await page.locator("#referenceButton").click()
                await page.screenshot(path="/tmp/li-studio-mobile.png")
                assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "horizontal overflow"
                await browser.close()
        finally:
            await test_server.close()
    if errors:
        raise AssertionError(errors)
    print("Browser smoke passed: upload, evidence edit, references, private rehearsal, pause/resume, Q&A, new session, mobile.")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        async def document():
            async with async_playwright() as p:
                browser = await p.chromium.launch(channel="chrome", headless=True)
                page = await browser.new_page(viewport={"width": 1440, "height": 1000})
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                await page.goto(Path(sys.argv[1]).resolve().as_uri())
                await page.screenshot(path="/tmp/li-architecture-desktop.png", full_page=True)
                await page.locator(".frame svg").screenshot(path="/tmp/li-diagram.png")
                await page.get_by_role("button", name="잠시 멈춤", exact=True).click()
                assert await page.locator("#state-title").text_content() == "쉬는 동안 같은 세션을 유지합니다"
                await page.set_viewport_size({"width": 390, "height": 844})
                await page.evaluate("window.scrollTo(0, 0)")
                await page.screenshot(path="/tmp/li-architecture-mobile.png")
                assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert not errors, errors
                await browser.close()
                print("Architecture HTML: local render, controls and responsive layout passed.")
        asyncio.run(document())
    else:
        asyncio.run(run())
