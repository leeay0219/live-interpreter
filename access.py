"""Operator and viewer access: password, operator link and caption link, login page."""
import io
import sys
import asyncio
import hashlib
import hmac
import logging
from pathlib import Path

import qrcode
import qrcode.image.svg
from aiohttp import web

from security import public_base


sys.path.insert(0, str(Path(__file__).parent / "postprocess"))


from settings import STATIC
log = logging.getLogger("captions")


# --- web app ---
# Access control, on when OPERATOR_PASSWORD is set (the AWS deployment injects it from Secrets Manager).
# Operators sign in with the password and may send audio; viewers open /captions?k=<VIEW_KEY> and only receive captions.
AUTH_COOKIE = "live_interpreter"
LOGIN_PAGE = """<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>Live Interpreter 로그인</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{margin:0;height:100vh;display:grid;place-items:center;background:#0b1622;color:#e6edf3;font-family:-apple-system,"Apple SD Gothic Neo",sans-serif}
form{background:#16222f;border:1px solid #2a3b4d;border-radius:12px;padding:32px;width:320px}h1{font-size:18px;margin:0 0 20px}
input,button{width:100%;box-sizing:border-box;font-size:16px;padding:10px 12px;border-radius:8px;border:1px solid #2a3b4d;margin-top:8px}
input{background:#0b1622;color:#e6edf3}button{background:#ff9900;color:#0b1622;font-weight:700;border:0;margin-top:16px;cursor:pointer}
p{color:#ff8a80;font-size:14px;margin:12px 0 0}</style></head><body>
<form method="post" action="/login"><h1>Live Interpreter</h1><input type="password" name="password" placeholder="비밀번호" autofocus required>
<button>로그인</button>{error}<p style="color:#8f9eae">운영자 링크가 있으면 그 링크로 바로 들어갈 수 있습니다.</p></form></body></html>"""


class Auth:
    def __init__(self, password: str | None, view_key: str | None):
        self.password = password
        self.view_key = view_key
        self.key = hashlib.sha256(f"{password}\0{view_key}".encode()).digest() if password else b""

    @property
    def on(self) -> bool:
        return bool(self.password)

    @property
    def operator_key(self) -> str:
        """Key in the operator link (/?k=...). Derived from the password and view key, so changing either one
        also invalidates the link; no separate secret to manage."""
        return hmac.new(self.key, b"operator-link", hashlib.sha256).hexdigest()[:32] if self.on else ""

    def token(self, role: str) -> str:
        return f"{role}.{hmac.new(self.key, role.encode(), hashlib.sha256).hexdigest()}"

    def role(self, request: web.Request) -> str | None:
        if not self.on:
            return "operator"
        role, _, _ = request.cookies.get(AUTH_COOKIE, "").partition(".")
        if role in ("operator", "viewer") and hmac.compare_digest(request.cookies[AUTH_COOKIE], self.token(role)):
            return role
        return None

    def set_cookie(self, resp: web.StreamResponse, role: str):
        resp.set_cookie(AUTH_COOKIE, self.token(role), max_age=3 * 86400, httponly=True, secure=True, samesite="Lax", path="/")


async def oplink(request):
    """Operator link for the studio's share menu (only shown to an operator)."""
    auth: Auth = request.app["auth"]
    if auth.role(request) != "operator":
        raise web.HTTPForbidden()
    q = f"/?k={auth.operator_key}" if auth.on else "/"
    return web.json_response({"url": public_base(request) + q}, headers={"Cache-Control": "no-store"})


def viewer_link(request) -> str:
    """Attendee link: the phone view, with the view key when access control is on."""
    auth: Auth = request.app["auth"]
    q = f"k={auth.view_key}&" if auth.on and auth.view_key else ""
    return public_base(request) + f"/captions?{q}view=list"


async def caplink(request):
    """Viewer link for the operator panel."""
    if request.app["auth"].role(request) != "operator":
        raise web.HTTPForbidden()
    return web.json_response({"url": viewer_link(request)}, headers={"Cache-Control": "no-store"})


async def caplink_qr(request):
    """The attendee link as a QR code (SVG), to put up on the screen."""
    if request.app["auth"].role(request) != "operator":
        raise web.HTTPForbidden()
    img = qrcode.make(viewer_link(request), image_factory=qrcode.image.svg.SvgPathFillImage, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return web.Response(body=buf.getvalue(), content_type="image/svg+xml", headers={"Cache-Control": "no-store"})


def operator(request):
    if request.app["auth"].role(request) != "operator":
        raise web.HTTPForbidden()


def writable(request):
    operator(request)
    source = request.app["state"].get("source")
    client_id = request.headers.get("X-Operator-Id")
    state = request.app["state"]
    controller = state.get("controller")
    if controller and state.get("phase") in ("live", "paused") and client_id != controller:
        raise web.HTTPConflict(text="현재 통역 중인 운영 창에서 변경하세요.")


async def captions(request):
    auth: Auth = request.app["auth"]
    key = request.query.get("k")
    if auth.on and key is not None:
        if not (auth.view_key and hmac.compare_digest(key, auth.view_key)):
            await asyncio.sleep(1)
            raise web.HTTPForbidden(text="자막 링크가 올바르지 않습니다.")
        # keep the other display options, drop the key from the address bar
        rest = "&".join(f"{k}={v}" for k, v in request.query.items() if k != "k")
        resp = web.HTTPFound("/captions" + (f"?{rest}" if rest else ""))
        auth.set_cookie(resp, "viewer" if auth.role(request) != "operator" else "operator")
        raise resp
    if auth.role(request) is None:
        raise web.HTTPForbidden(text="자막 링크로 접속해 주세요.")
    return web.FileResponse(STATIC / "captions.html")


async def login_page(request):
    return web.Response(text=LOGIN_PAGE.replace("{error}", ""), content_type="text/html")


async def login(request):
    auth: Auth = request.app["auth"]
    form = await request.post()
    if not auth.on:
        raise web.HTTPFound("/")
    if hmac.compare_digest(str(form.get("password", "")).encode(), auth.password.encode()):
        resp = web.HTTPFound("/")
        auth.set_cookie(resp, "operator")
        raise resp
    log.warning("failed operator login from %s", request.headers.get("X-Forwarded-For", request.remote))
    await asyncio.sleep(1.5)
    return web.Response(text=LOGIN_PAGE.replace("{error}", "<p>비밀번호가 올바르지 않습니다.</p>"), content_type="text/html", status=401)
