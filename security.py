"""Request boundaries and logs that never record bearer links."""
import ipaddress
import logging
from urllib.parse import urlsplit

from aiohttp import web
from aiohttp.abc import AbstractAccessLogger

log = logging.getLogger("captions")
# The studio page loads only its own module scripts; other pages (captions, login) keep inline code.
STUDIO_CSP = "script-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"


def is_loopback(host):
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_bind(host, password):
    if not password and not is_loopback(host):
        raise ValueError("외부 주소에서 실행하려면 OPERATOR_PASSWORD를 설정하세요.")
    if not is_loopback(host):
        # cookies are Secure, so browsers drop them on plain HTTP and sign-in never sticks
        log.warning("listening on %s over plain HTTP: put a TLS-terminating proxy (HTTPS) in front of it", host)


def public_base(request):
    via_cloudfront = "cloudfront" in request.headers.get("Via", "").lower()
    scheme = request.headers.get("CloudFront-Forwarded-Proto") or ("https" if via_cloudfront else request.scheme)
    return f"{scheme}://{request.host}"


def origin_tuple(url):
    parsed = urlsplit(url)
    if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.path or parsed.query or parsed.fragment):
        raise ValueError("invalid origin")
    return parsed.scheme, parsed.hostname.lower(), parsed.port or (443 if parsed.scheme == "https" else 80)


@web.middleware
async def request_boundary(request, handler):
    auth = request.app["auth"]
    try:
        expected = origin_tuple(public_base(request))
        # Reject DNS rebinding hostnames even on unauthenticated GET requests.
        if not auth.on and not is_loopback(expected[1]):
            raise ValueError("untrusted local host")
        origin = request.headers.get("Origin")
        # With Referrer-Policy: no-referrer a browser sends "Origin: null" for a same-origin form POST (the login page).
        # Sec-Fetch-Site is set by the browser and cannot be forged by a page, so it decides those requests.
        same_origin_form = origin == "null" and request.headers.get("Sec-Fetch-Site") == "same-origin"
        if origin is not None and not same_origin_form and origin_tuple(origin) != expected:
            raise ValueError("foreign origin")
        # Browser requests cannot opt out by suppressing Origin.
        if request.headers.get("Sec-Fetch-Site") in ("cross-site", "same-site"):
            if request.method not in ("GET", "HEAD") or request.headers.get("Upgrade", "").lower() == "websocket":
                raise ValueError("cross-site mutation")
    except ValueError:
        raise web.HTTPForbidden(text="이 출처에서는 요청할 수 없습니다.") from None
    return await handler(request)


async def response_headers(request, response):
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    # write-up exports bring their own stricter policy (with frame-ancestors)
    response.headers.setdefault("Content-Security-Policy", STUDIO_CSP if request.path in ("/", "/studio") else "frame-ancestors 'none'")
    if public_base(request).startswith("https:") or request.headers.get("X-Forwarded-Proto", "").lower() == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    if not request.path.startswith("/static/"):
        response.headers.setdefault("Cache-Control", "no-store")
    else:
        # revalidate every time (304 when unchanged): after a deploy the page, its scripts and the server change together
        # instead of CloudFront serving day-old JavaScript against a new API
        response.headers.setdefault("Cache-Control", "no-cache")


class SafeAccessLogger(AbstractAccessLogger):
    def log(self, request, response, time):
        resource = request.match_info.route.resource
        path = resource.canonical if resource is not None else "<unmatched>"
        # The route template contains no query, Referer, headers or user-supplied IDs.
        self.logger.info("%s %s %s %.3fs", request.method, path, response.status, time)
