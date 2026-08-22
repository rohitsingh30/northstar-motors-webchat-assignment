from pathlib import Path

from fastapi.testclient import TestClient

from webchat.config import Settings
from webchat.main import create_app


def test_health_endpoints(tmp_path: Path) -> None:
    settings = Settings(environment="test", webchat_database_path=tmp_path / "webchat.sqlite3")

    with TestClient(create_app(settings)) as client:
        assert client.get("/health/live").json() == {
            "status": "ok",
            "service": "northstar-webchat",
        }
        assert client.get("/health/ready").json() == {
            "status": "ready",
            "checks": {"database": "ok"},
        }


def test_production_requires_openai_key(tmp_path: Path) -> None:
    settings = Settings(environment="production", webchat_database_path=tmp_path / "webchat.sqlite3")

    try:
        create_app(settings)
    except ValueError as error:
        assert "OPENAI_API_KEY" in str(error)
    else:
        raise AssertionError("production must reject a missing OpenAI API key")


def test_service_hosts_the_widget_bundle(tmp_path: Path) -> None:
    settings = Settings(
        environment="test",
        webchat_database_path=tmp_path / "webchat.sqlite3",
        webchat_allowed_origin="http://localhost:4173",
    )
    with TestClient(create_app(settings)) as client:
        response = client.get(
            "/widget/embed.js", headers={"Origin": "http://localhost:4173"}
        )

    assert response.status_code == 200
    assert "mountNorthstarChat" in response.text
    assert response.headers["access-control-allow-origin"] == "http://localhost:4173"
