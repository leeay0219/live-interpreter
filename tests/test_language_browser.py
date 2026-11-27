"""The interface language follows the browser, can be switched on the page, and reaches server messages and the
attendee view. Isolated server, no AWS calls."""
import asyncio
import sys
from pathlib import Path

from aiohttp.test_utils import TestServer
from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import server
from test_workflows import make_translator, config


async def run():
    tr = make_translator()
    app = server.create_app(config(tr), tr)
    test_server = TestServer(app)
    await test_server.start_server()
    url = lambda path: str(test_server.make_url(path))
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel="chrome", headless=True)
            errors = []

            async def open_page(locale, path):
                page = await browser.new_page(locale=locale)
                page.on("pageerror", lambda e: errors.append(str(e)))
                await page.goto(url(path), wait_until="networkidle")
                return page

            # an English browser gets the English studio without choosing anything; markup, attributes and JS strings alike
            page = await open_page("en-US", "/")
            assert await page.get_attribute("html", "lang") == "en"
            assert await page.text_content("#start") == "Start"
            assert await page.get_attribute("#rehearse", "title") == "Check the interpretation without sending it to the audience"
            assert await page.text_content("#advValue") == "Default"
            assert await page.eval_on_selector_all("#uiLang option", "o => o.map(x => x.textContent)") == ["한국어", "English", "繁體中文"]
            # switching reloads the setup screen in the new language and tells the server through a cookie
            await page.select_option("#uiLang", "zh")
            await page.wait_for_function('() => document.documentElement.lang === "zh-Hant-TW" && document.querySelector("#start").textContent === "開始口譯"')
            assert await page.evaluate('() => fetch("/api/record/csv").then(r => r.text())') == "沒有紀錄。"
            # ?lang= wins over the saved choice, and the Korean interface is the source text itself
            await page.goto(url("/?lang=ko"), wait_until="networkidle")
            assert await page.text_content("#start") == "통역 시작"
            assert await page.evaluate('() => fetch("/api/record/csv").then(r => r.text())') == "기록이 없습니다."
            await page.close()

            # a Taiwanese attendee: Chinese chrome, caption choices from the session's two languages, switchable in place
            page = await open_page("zh-TW", "/captions?view=list")
            await page.wait_for_function('() => document.querySelectorAll("#lang button").length === 3')
            assert await page.eval_on_selector_all("#lang button", "b => b.map(x => x.textContent)") == ["English", "한국어", "兩種都看"]
            assert await page.text_content("#empty") == "正在等待字幕"
            await page.select_option("#uiLang", "en")
            await page.wait_for_function('() => document.getElementById("empty").textContent === "Waiting for captions"')
            assert await page.eval_on_selector_all("#lang button", "b => b.map(x => x.textContent)") == ["English", "한국어", "Both"]
            assert await page.get_attribute("html", "lang") == "en"
            await page.close()

            # the login page is server-rendered in the browser's language (a second server, with a password set)
            locked = server.create_app(config(make_translator()), make_translator())
            locked["auth"] = server.Auth("browser-test-password", "browser-test-view-key")
            locked_server = TestServer(locked)
            await locked_server.start_server()
            try:
                page = await browser.new_page(locale="zh-TW")
                await page.goto(str(locked_server.make_url("/login")))
                assert await page.text_content("form button") == "登入"
                assert await page.get_attribute("input[name=password]", "placeholder") == "密碼"
                await page.close()
            finally:
                await locked_server.close()
            assert not errors, errors
            await browser.close()
    finally:
        await test_server.close()
    print("Language browser check passed: browser language, in-page switch, server messages, attendee view, login page.")


if __name__ == "__main__":
    asyncio.run(run())
