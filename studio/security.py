"""Small ASGI boundary: authentication, same-origin writes and body limits."""

import secrets

from starlette.datastructures import Headers
from starlette.responses import JSONResponse


class SecurityMiddleware:
    def __init__(self, app, token="", max_body=262144):
        self.app = app
        self.token = token
        self.max_body = max_body

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = Headers(scope=scope)

        async def protected_send(message):
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + [
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"cache-control", b"no-store"),
                    (
                        b"content-security-policy",
                        b"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
                    ),
                ]
            await send(message)

        async def reject(code, detail):
            await JSONResponse({"detail": detail}, status_code=code)(scope, receive, protected_send)

        if scope["path"].startswith("/api/") and scope["path"] != "/api/health" and self.token:
            received = headers.get("authorization", "").encode("utf-8")
            expected = ("Bearer " + self.token).encode("utf-8")
            if not secrets.compare_digest(received, expected):
                return await reject(401, "A valid studio API token is required.")
        if scope["method"] in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = headers.get("origin")
            expected_origin = scope["scheme"] + "://" + headers.get("host", "")
            if origin is not None and origin != expected_origin:
                return await reject(403, "Cross-origin writes are not allowed.")
            raw_length = headers.get("content-length")
            if raw_length is not None:
                try:
                    size = int(raw_length)
                    if size < 0:
                        return await reject(400, "Invalid content length.")
                    if size > self.max_body:
                        return await reject(413, "Request exceeds the 256 KiB limit.")
                except ValueError:
                    return await reject(400, "Invalid content length.")
            chunks = []
            size = 0
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                chunks.append(message)
                size += len(message.get("body", b""))
                if size > self.max_body:
                    return await reject(413, "Request exceeds the 256 KiB limit.")
                if not message.get("more_body", False):
                    break
            index = 0

            async def bounded_receive():
                nonlocal index
                if index < len(chunks):
                    message = chunks[index]
                    index += 1
                    return message
                return await receive()

            return await self.app(scope, bounded_receive, protected_send)
        return await self.app(scope, receive, protected_send)
