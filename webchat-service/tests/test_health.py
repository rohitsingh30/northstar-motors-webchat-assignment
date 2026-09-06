from pathlib import Path

from fastapi.testclient import TestClient

from webchat.config import Settings
from webchat.main import create_app


def test_blank_hosted_llm_values_are_unconfigured() -> None:
    settings = Settings(
        _env_file=None,
        llm_provider_url="   ",
        llm_api_key="",
        llm_model="",
    )

    assert settings.llm_provider_url is None
    assert settings.llm_api_key is None
    assert settings.llm_model is None


def test_health_endpoints(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        environment="test",
        webchat_database_path=tmp_path / "webchat.sqlite3",
    )

    with TestClient(create_app(settings)) as client:
        assert client.get("/health/live").json() == {
            "status": "ok",
            "service": "northstar-webchat",
        }
        assert client.get("/health/ready").json() == {
            "status": "ready",
            "checks": {"database": "ok"},
            "assistantMode": "limited_demo",
        }


def test_production_requires_complete_hosted_llm_configuration(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        environment="production", webchat_database_path=tmp_path / "webchat.sqlite3"
    )

    try:
        create_app(settings)
    except ValueError as error:
        assert "LLM_PROVIDER_URL" in str(error)
        assert "LLM_API_KEY" in str(error)
        assert "LLM_MODEL" in str(error)
    else:
        raise AssertionError("production must reject missing hosted LLM configuration")


def test_development_requires_complete_hosted_llm_configuration(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        environment="development", webchat_database_path=tmp_path / "webchat.sqlite3"
    )

    try:
        create_app(settings)
    except ValueError as error:
        assert "LLM_PROVIDER_URL" in str(error)
        assert "LLM_API_KEY" in str(error)
        assert "LLM_MODEL" in str(error)
    else:
        raise AssertionError("development must not silently start the deterministic test provider")


def test_partial_hosted_llm_configuration_is_rejected(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        environment="development",
        webchat_database_path=tmp_path / "webchat.sqlite3",
        llm_provider_url="https://provider.example/v1",
        llm_api_key="shared-key",
    )

    try:
        create_app(settings)
    except ValueError as error:
        assert "LLM_MODEL" in str(error)
    else:
        raise AssertionError("partial hosted LLM configuration must not silently use defaults")


def test_service_hosts_the_widget_bundle(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        environment="test",
        webchat_database_path=tmp_path / "webchat.sqlite3",
        webchat_allowed_origin="http://localhost:4173",
    )
    with TestClient(create_app(settings)) as client:
        embed_response = client.get("/widget/embed.js", headers={"Origin": "http://localhost:4173"})
        api_module_response = client.get("/widget/core/api.js")
        collector_response = client.get("/widget/core/workflow-conversation.js")
        message_view_response = client.get("/widget/views/message.js")
        appointment_view_response = client.get("/widget/views/appointments.js")

    assert embed_response.status_code == 200
    assert "mountNorthstarChat" in embed_response.text
    assert embed_response.headers["cache-control"] == "no-cache"
    assert embed_response.headers["access-control-allow-origin"] == "http://localhost:4173"
    assert api_module_response.status_code == 200
    assert "createChatApi" in api_module_response.text
    assert collector_response.status_code == 200
    assert "WORKFLOW_CONVERSATION_SPECS" in collector_response.text
    assert message_view_response.status_code == 200
    assert "renderMessage" in message_view_response.text
    assert appointment_view_response.status_code == 200
    assert "confirmedBookingDisclosure" in appointment_view_response.text
