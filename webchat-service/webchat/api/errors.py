from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from webchat.integrations.dealership import DealershipError


async def dealership_error_response(
    request: Request, error: DealershipError
) -> JSONResponse:
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
