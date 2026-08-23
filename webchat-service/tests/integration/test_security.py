from pathlib import Path

from fastapi.testclient import TestClient

from webchat.config import Settings
from webchat.main import create_app

CONTEXT = {"path": "/", "section": "home", "vehicleId": None, "title": "Used Cars"}


def test_state_change_requires_same_origin_and_json(tmp_path: Path) -> None:
    settings = Settings(
        environment="development",
        webchat_database_path=tmp_path / "webchat.sqlite3",
        webchat_allowed_origin="http://localhost:4173",
    )
    with TestClient(create_app(settings)) as client:
        missing_origin = client.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT})
        wrong_origin = client.post(
            "/api/chat/v1/conversations",
            json={"pageContext": CONTEXT},
            headers={"Origin": "https://attacker.example"},
        )
        wrong_type = client.post(
            "/api/chat/v1/conversations",
            content="{}",
            headers={"Origin": "http://localhost:4173", "Content-Type": "text/plain"},
        )
        valid = client.post(
            "/api/chat/v1/conversations",
            json={"pageContext": CONTEXT},
            headers={"Origin": "http://localhost:4173"},
        )

    assert missing_origin.status_code == 403
    assert wrong_origin.status_code == 403
    assert wrong_type.status_code == 415
    assert valid.status_code == 201
    assert valid.headers["x-content-type-options"] == "nosniff"


def test_configured_widget_origin_gets_credentialed_cors(tmp_path: Path) -> None:
    settings = Settings(
        environment="development",
        webchat_database_path=tmp_path / "webchat.sqlite3",
        webchat_allowed_origin="http://localhost:4173",
    )
    with TestClient(create_app(settings)) as client:
        response = client.options(
            "/api/chat/v1/conversations",
            headers={
                "Origin": "http://localhost:4173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:4173"
    assert response.headers["access-control-allow-credentials"] == "true"


def test_unhandled_api_failure_returns_safe_json_with_cors(tmp_path: Path) -> None:
    settings = Settings(
        environment="development",
        webchat_database_path=tmp_path / "webchat.sqlite3",
        webchat_allowed_origin="http://localhost:4173",
    )
    application = create_app(settings)

    @application.get("/api/chat/v1/test-unhandled-error")
    async def unhandled_error():
        raise RuntimeError("private failure details")

    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.get(
            "/api/chat/v1/test-unhandled-error",
            headers={"Origin": "http://localhost:4173"},
        )

    assert response.status_code == 500
    assert response.headers["access-control-allow-origin"] == "http://localhost:4173"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.json() == {
        "error": {
            "code": "INTERNAL_SERVER_ERROR",
            "message": "The request could not be completed. Please retry.",
            "retryable": True,
        }
    }
    assert "private failure details" not in response.text


def test_trusted_gateway_addresses_have_independent_rate_limits(tmp_path: Path) -> None:
    settings = Settings(
        environment="test",
        webchat_database_path=tmp_path / "webchat.sqlite3",
        webchat_requests_per_minute=1,
        webchat_trust_proxy_headers=True,
    )
    application = create_app(settings)

    @application.get("/api/chat/v1/rate-test")
    async def rate_test():
        return {"ok": True}

    with TestClient(application) as client:
        first = client.get(
            "/api/chat/v1/rate-test", headers={"X-Forwarded-For": "203.0.113.10"}
        )
        repeated = client.get(
            "/api/chat/v1/rate-test", headers={"X-Forwarded-For": "203.0.113.10"}
        )
        other_reviewer = client.get(
            "/api/chat/v1/rate-test", headers={"X-Forwarded-For": "203.0.113.11"}
        )

    assert first.status_code == 200
    assert repeated.status_code == 429
    assert other_reviewer.status_code == 200
