"""A real browser logs in with the operator password. Isolated server, no AWS calls.

Browsers send "Origin: null" for this form POST because every response sets Referrer-Policy: no-referrer; a request
check that treats null as a foreign origin locks every operator out (0.1.3 did, only on the deployed site).
"""
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
    app["auth"] = server.Auth("browser-test-password", "browser-test-view-key")
    test_server = TestServer(app)
    await test_server.start_server()
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel="chrome", headless=True)
            page = await browser.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            await page.goto(str(test_server.make_url("/")))
            assert page.url.endswith("/login"), page.url
            await page.fill("input[name=password]", "wrong")
            await page.press("input[name=password]", "Enter")
            await page.get_by_text("비밀번호가 올바르지 않습니다").wait_for()
            await page.fill("input[name=password]", "browser-test-password")
            await page.press("input[name=password]", "Enter")
            await page.locator("#start").wait_for(state="visible", timeout=10000)
            assert not errors, errors
            await browser.close()
    finally:
        await test_server.close()
    print("Login browser check passed: wrong password refused, right password reaches the studio.")


if __name__ == "__main__":
    asyncio.run(run())
