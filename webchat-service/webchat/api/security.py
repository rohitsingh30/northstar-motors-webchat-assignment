from __future__ import annotations

import time
from collections import defaultdict, deque
from ipaddress import ip_address

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

MAX_BODY = 64 * 1024


def problem(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "retryable": False}},
    )


class SecurityMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, settings, requests_per_minute: int = 60):
        super().__init__(app)
        self.settings = settings
        self.requests_per_minute = requests_per_minute
        self.requests: defaultdict[str, deque[float]] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith("/api/chat/v1"):
            rejected = self._validate(request)
            if rejected:
                return rejected
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        return response

    def _validate(self, request: Request) -> JSONResponse | None:
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > MAX_BODY:
                    return problem(413, "REQUEST_TOO_LARGE", "Chat request is too large.")
            except ValueError:
                return problem(400, "INVALID_REQUEST", "Content-Length is invalid.")

        if request.method in {"POST", "PATCH"} and not request.headers.get(
            "content-type", ""
        ).lower().startswith("application/json"):
            return problem(415, "JSON_REQUIRED", "Use application/json for this request.")
        if request.method in {"POST", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            missing_allowed_for_tests = self.settings.environment == "test" and origin is None
            if not missing_allowed_for_tests and origin != self.settings.webchat_allowed_origin:
                return problem(403, "ORIGIN_REJECTED", "The request origin is not allowed.")

        client = self._client_address(request)
        now = time.monotonic()
        recent = self.requests[client]
        while recent and recent[0] < now - 60:
            recent.popleft()
        if len(recent) >= self.requests_per_minute:
            return problem(429, "RATE_LIMITED", "Too many chat requests. Please wait and retry.")
        recent.append(now)
        return None

    def _client_address(self, request: Request) -> str:
        if self.settings.webchat_trust_proxy_headers:
            forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
            try:
                return str(ip_address(forwarded))
            except ValueError:
                return "unknown"
        return request.client.host if request.client else "unknown"
