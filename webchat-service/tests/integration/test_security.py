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
