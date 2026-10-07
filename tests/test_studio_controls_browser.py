"""Every studio control the smoke test does not press: settings sheets, preview, inputs, the live control bar, sharing,
keyboard shortcuts, the log and clearing the record. Isolated server, fake microphone, no AWS calls.

Each step checks an effect (server state, a class or text on the page), not only that a click did not fail.
"""
import asyncio
import io
import struct
import sys
import tempfile
import wave
from pathlib import Path
from unittest.mock import patch

from pdf_fixture import pdf_bytes
from aiohttp.test_utils import TestServer
from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import logtext
import routes_live
import server
import settings
from routes_session import session_terms
from test_workflows import make_translator, config, NoAudioSession


def silent_wav(seconds=1.0, rate=16000):
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(struct.pack("<h", 0) * int(seconds * rate))
    return out.getvalue()


async def until(check, what, timeout=5.0):
    async with asyncio.timeout(timeout):
        while not await check():
            await asyncio.sleep(0.05)
    return True


async def run():
    errors, steps = [], []
    def step(name):
        steps.append(name)
        print("  -", name, flush=True)
    with tempfile.TemporaryDirectory() as tmp, patch.object(settings, "DECKS", Path(tmp) / "decks"), \
            patch.object(routes_live, "Session", NoAudioSession):
        tr = make_translator()
        app = server.create_app(config(tr), tr)
        test_server = TestServer(app)
        await test_server.start_server()
        deck = {"name": "Three.pdf", "mimeType": "application/pdf", "buffer": pdf_bytes("Northstar slide", pages=3)}
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(channel="chrome", headless=True,
                    args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"])
                context = await browser.new_context(viewport={"width": 1280, "height": 800},
                                                    permissions=["microphone", "clipboard-read", "clipboard-write"])
                page = await context.new_page()
                page.on("pageerror", lambda e: errors.append(f"{steps[-1] if steps else 'load'}: {e}"))
                page.on("dialog", lambda d: asyncio.ensure_future(d.accept()))
                base = str(test_server.make_url("/")).rstrip("/")
                await page.goto(base + "/")
                await page.locator("#input option").first.wait_for(state="attached")

                step("preview: size, background, original line")
                before = await page.text_content("#pvSize")
                await page.click("#pvBigger")
                assert await page.text_content("#pvSize") != before
                await page.click("#pvSmaller")
                assert await page.text_content("#pvSize") == before
                await page.click("#pvBg [data-bg=light]")
                assert "light" in await page.get_attribute("#pvStage", "class")
                await page.click("#pvBg [data-bg=dark]")
                assert "light" not in await page.get_attribute("#pvStage", "class")
                assert await page.is_disabled("#pvBg [data-bg=overlay]")  # needs slides
                await page.uncheck("#pvSrc")
                assert "no-en" in await page.get_attribute("#pvCap", "class")
                await page.check("#pvSrc")

                step("audio inputs: meeting tab, test file, device and sound check")
                await page.select_option("#input", "screen")
                assert "회의 탭" in await page.text_content("#hint")
                assert await page.is_hidden("#meter")
                await page.select_option("#input", "file")
                assert await page.is_visible("#fileRow")
                async with page.expect_file_chooser() as chooser:
                    await page.click("#pickAudio")
                await (await chooser.value).set_files({"name": "quiet.wav", "mimeType": "audio/wav", "buffer": silent_wav()})
                assert await page.text_content("#audioName") == "quiet.wav"
                await page.select_option("#input", index=0)
                await page.locator("#meter").wait_for(state="visible")
                await until(lambda: page.evaluate("document.getElementById('checkNote').textContent.length > 0"), "sound check note")

                step("slides: upload, remove, upload again")
                await page.locator("#deckFile").set_input_files(deck)
                await page.locator("#deckInfo").wait_for(state="visible")
                assert app["state"]["deck"]["name"] == "Three.pdf"
                assert not await page.is_disabled("#pvBg [data-bg=overlay]")
                await page.click("#deckRemove")
                await until(lambda: asyncio.sleep(0, app["state"].get("deck") is None), "deck removed")
                await page.locator("#drop").wait_for(state="visible")
                await page.locator("#deckFile").set_input_files(deck)
                await page.locator("#deckInfo").wait_for(state="visible")
                async with page.expect_file_chooser():
                    await page.click("#deckChange")  # opens the picker; nothing is chosen
                await page.click("#pvBg [data-bg=overlay]")
                assert "overlay" in await page.get_attribute("#pvStage", "class")

                step("advanced: skill, noise processing, content logging")
                await page.click("[data-sheet=adv]")
                await page.locator("#adv").wait_for(state="visible")
                await page.locator("#skills .skill").first.wait_for()
                skill_title = await page.locator("#skills .skill").first.text_content()
                await page.locator("#skills .skill").first.click()
                assert "on" in await page.locator("#skills .skill").first.get_attribute("class")
                await page.check("#dsp")
                assert await page.evaluate("JSON.parse(localStorage.studio2).dsp") is True
                await page.uncheck("#dsp")
                await page.check("#logContent")

                step("speakers: add, misheard form chip, suggestion, delete")
                await page.locator("#adv details.manual summary").first.click()
                await page.click("#addPerson")
                card = page.locator("#people .person").last
                await card.locator(".names input").first.fill("Mina Park")
                await card.locator(".names input").nth(1).fill("미나님")
                await card.locator("input.add").last.fill("meena")
                await card.locator("input.add").last.press("Enter")
                assert await page.locator("#people .person").last.locator(".chip").count() == 1
                await page.locator("#people .person").last.get_by_role("button", name="잘못 들릴 형태 추천").click()
                await until(lambda: page.evaluate("document.getElementById('peopleNote').textContent.length > 0"), "suggestion note")
                await page.click("#addPerson")
                assert await page.locator("#people .person").count() == 2
                await page.locator("#people .person").last.get_by_role("button", name="삭제").click()
                assert await page.locator("#people .person").count() == 1

                step("session terms: add, delete, add again")
                await page.locator("#adv details.manual summary").nth(1).click()
                form = page.locator("#termAdd form")
                async def add_term(heard, right):
                    await form.locator("[name=heard]").fill(heard)
                    await form.locator("[name=en]").fill(right)
                    await form.get_by_role("button", name="추가").click()
                await add_term("bed lock", "AWS Bedrock")
                await until(lambda: asyncio.sleep(0, len(session_terms(tr)) == 1), "term on server")
                await page.locator("#terms .term").first.get_by_role("button", name="삭제").click()
                await until(lambda: asyncio.sleep(0, len(session_terms(tr)) == 0), "term removed on server")
                await add_term("bed lock", "AWS Bedrock")
                await until(lambda: asyncio.sleep(0, len(session_terms(tr)) == 1), "term on server again")
                await page.locator("#adv [data-close]").click()
                assert skill_title in await page.text_content("#advValue")

                step("start: settings reach the server")
                await page.click("#start")
                await page.locator("#live").wait_for(state="visible")
                assert tr.skills, "skill not applied"
                assert [p["name"] for p in tr.event.people] == ["Mina Park"]
                assert "meena" in tr.event.people[0]["heard_as"]
                assert logtext.LOG_CONTENT["on"] is True
                assert await page.is_visible("#nav")

                step("slides: next, previous, page reaches the server")
                await page.mouse.move(640, 300)
                assert await page.text_content("#pageNo") == "1 / 3"
                await page.click("#next")
                assert await page.text_content("#pageNo") == "2 / 3"
                await until(lambda: asyncio.sleep(0, app["state"].get("page") == 1), "page on server")
                await page.click("#prev")
                assert await page.text_content("#pageNo") == "1 / 3"

                step("control bar: overlay, captions, original line, size")
                await page.mouse.move(640, 310)
                overlay = "overlay" in await page.get_attribute("#live", "class")
                await page.click("#tPos")
                assert ("overlay" in await page.get_attribute("#live", "class")) != overlay
                await page.click("#tCap")
                assert "nocap" in await page.get_attribute("#live", "class")
                await page.click("#tCap")
                await page.click("#tSrc")
                assert "no-en" in await page.get_attribute("#captions", "class")
                await page.click("#tSrc")
                scale = await page.evaluate("getComputedStyle(document.getElementById('captions')).getPropertyValue('--cap-scale')")
                await page.click("#bigger")
                assert await page.evaluate("getComputedStyle(document.getElementById('captions')).getPropertyValue('--cap-scale')") != scale
                await page.click("#smaller")

                step("captions reach the screen and the log")
                await app["hub"].send({"type": "caption", "id": "u1#0", "src": "We use bed lock for this.", "src_lang": "en",
                                       "tx": "이 일에는 bed lock을 씁니다.", "tx_lang": "ko", "engine": "test", "ms": 900})
                await page.get_by_text("이 일에는 bed lock을 씁니다.", exact=True).first.wait_for()
                await page.click("#tLog")
                await page.locator("#drawer").wait_for(state="visible")
                row = page.locator("#log > div").filter(has_text="We use bed lock for this.").first
                await row.wait_for()

                step("log: add a term from a line")
                await row.hover()
                await row.locator(".addterm").click()
                log_form = page.locator("#logTerm form")
                await log_form.locator("[name=heard]").fill("for this")
                await log_form.locator("[name=en]").fill("for the demo")
                await log_form.get_by_role("button", name="추가").click()
                await until(lambda: asyncio.sleep(0, len(session_terms(tr)) == 2), "log term on server")
                assert await page.locator("#logTerm form").count() == 0
                await page.click("#closeLog")
                assert await page.is_hidden("#drawer")

                step("share: caption window, attendee link, QR, operator link")
                await page.mouse.move(640, 320)
                await page.click("#tShare")
                await page.locator("#share").wait_for(state="visible")
                async with context.expect_page() as popup:
                    await page.locator("#share [data-open]").first.click()
                caption_page = await popup.value
                await caption_page.wait_for_load_state()
                assert "/captions" in caption_page.url
                await caption_page.close()
                await page.evaluate("navigator.clipboard.writeText('')")
                await page.click("#copyViewer")
                await until(lambda: page.evaluate("navigator.clipboard.readText().then(t => t.includes('/captions'))"), "attendee link copied")
                await page.locator("#copyViewer").get_by_text("복사했습니다").wait_for()  # the button confirms the copy
                await page.evaluate("navigator.clipboard.writeText('')")
                await page.mouse.move(640, 321)
                if await page.is_hidden("#share"):
                    await page.click("#tShare")
                await page.click("#copyOperator")
                await until(lambda: page.evaluate("navigator.clipboard.readText().then(t => t.startsWith('http'))"), "operator link copied")
                await page.click("#showQr")
                await page.locator("#qr").wait_for(state="visible")
                assert "/caplink.svg" in await page.get_attribute("#qrImg", "src")
                await page.keyboard.press("Escape")
                await page.locator("#qr").wait_for(state="hidden")

                step("keyboard: slides, captions, original line, size, blank, log")
                await page.locator("body").click(position={"x": 200, "y": 200})
                await page.keyboard.press("ArrowRight")
                assert await page.text_content("#pageNo") == "2 / 3"
                await page.keyboard.press("ArrowLeft")
                await page.keyboard.press("c")
                assert "nocap" in await page.get_attribute("#live", "class")
                await page.keyboard.press("c")
                await page.keyboard.press("e")
                assert "no-en" in await page.get_attribute("#captions", "class")
                await page.keyboard.press("e")
                await page.keyboard.press("b")
                assert "blank" in await page.get_attribute("#live", "class")
                await page.keyboard.press("b")
                await page.keyboard.press("l")
                assert await page.is_visible("#drawer")
                await page.keyboard.press("l")
                assert await page.is_hidden("#drawer")

                step("fullscreen button")
                await page.mouse.move(640, 330)
                await page.click("#tFull")
                await until(lambda: page.evaluate("!!document.fullscreenElement"), "fullscreen")
                await page.click("#tFull")
                await until(lambda: page.evaluate("!document.fullscreenElement"), "fullscreen exit")

                step("stop, then clear the record")
                app["record"].append({"t": 1, "src_lang": "en", "src": "kept until cleared", "tx": "지울 때까지 남음", "status": "shown"})
                await page.mouse.move(640, 340)
                await page.click("#stop")
                await page.locator("#newSession").wait_for(state="visible")
                await until(lambda: asyncio.sleep(0, app["state"]["phase"] == "ended"), "ended")
                await page.locator("#completedManage #recordManage summary").click()  # 기록 관리 is folded on purpose
                await page.locator("#recClear").click()
                await until(lambda: asyncio.sleep(0, app["record"] == []), "record cleared")

                step("test file: plays to its end and the session stops on its own")
                await page.click("#newSession")
                await page.locator("#drop").wait_for(state="visible")
                await page.select_option("#input", "file")
                await page.locator("#audioFile").set_input_files({"name": "quiet.wav", "mimeType": "audio/wav", "buffer": silent_wav(1.0)})
                await page.uncheck("#monitor")
                await page.click("#start")
                await page.locator("#live").wait_for(state="visible")
                await page.locator("#newSession").wait_for(state="visible", timeout=10000)

                await browser.close()
        finally:
            await test_server.close()
    if errors:
        raise AssertionError(errors)
    print(f"Studio controls passed: {len(steps)} groups.")


if __name__ == "__main__":
    asyncio.run(run())
