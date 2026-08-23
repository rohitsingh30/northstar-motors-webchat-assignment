from __future__ import annotations

import logging

from fastapi import Request
from fastapi.responses import JSONResponse

from webchat.integrations.dealership import DealershipError

logger = logging.getLogger(__name__)


class ApiErrorBoundaryMiddleware:
    """Return safe JSON for unexpected API failures before CORS decorates the response."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/chat/v1"):
            await self.app(scope, receive, send)
            return
        try:
            await self.app(scope, receive, send)
        except Exception as error:
            logger.exception("unhandled chat API failure: %s", type(error).__name__)
            response = JSONResponse(
                status_code=500,
                content={
                    "error": {
                        "code": "INTERNAL_SERVER_ERROR",
                        "message": "The request could not be completed. Please retry.",
                        "retryable": True,
                    }
                },
            )
            await response(scope, receive, send)


async def dealership_error_response(request: Request, error: DealershipError) -> JSONResponse:
    """Translate integration failures at the HTTP boundary in one place."""
    return JSONResponse(
        status_code=error.status,
        content={
            "error": {
                "code": error.code,
                "message": str(error),
                "retryable": error.retryable,
                "fieldErrors": error.field_errors,
                "recovery": error.recovery,
            }
        },
    )
