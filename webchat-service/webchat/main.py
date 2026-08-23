from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api.conversations import router as conversation_router
from .api.errors import dealership_error_response
from .api.restoration import ConversationRestorer
from .api.security import SecurityMiddleware
from .config import Settings, get_settings
from .domain.workflows import WorkflowService
from .integrations.contracts import LlmProvider
from .integrations.dealership import DealershipClient, DealershipError
from .integrations.fake_llm import FakeLlmProvider
from .integrations.openai_provider import OpenAIProvider
from .observability.logging import configure_logging
from .orchestration.orchestrator import Orchestrator
from .orchestration.planning.plan_policy import SemanticPlanPolicy
from .orchestration.routing import DeterministicPlanRouter
from .orchestration.tools.registry import ToolRegistry
from .persistence.database import Database
from .persistence.repositories import (
    ConversationRepository,
    MessageRepository,
    TurnRepository,
    WorkflowRepository,
)


def create_app(settings: Settings | None = None, provider: LlmProvider | None = None) -> FastAPI:
    runtime_settings = settings or get_settings()
    runtime_settings.validate_runtime()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging(runtime_settings.log_level)
        database = Database(runtime_settings.webchat_database_path)
        database.migrate()
        app.state.settings = runtime_settings
        app.state.database = database
        app.state.conversations = ConversationRepository(database)
        app.state.conversations.expire_old()
        app.state.messages = MessageRepository(database)
        app.state.turns = TurnRepository(database)
        dealership = DealershipClient(
            runtime_settings.northstar_base_url,
            runtime_settings.northstar_api_key.get_secret_value(),
        )
        app.state.dealership = dealership
        app.state.workflow_repository = WorkflowRepository(database)
        app.state.restorer = ConversationRestorer(
            app.state.messages, app.state.workflow_repository
        )
        app.state.workflows = WorkflowService(app.state.workflow_repository, dealership)
        app.state.tools = ToolRegistry(dealership, app.state.workflows)
        selected_provider = provider
        if selected_provider is None and runtime_settings.llm_provider == "azure":
            selected_provider = OpenAIProvider(
                runtime_settings.azure_openai_api_key.get_secret_value(),
                runtime_settings.azure_openai_deployment,
                endpoint=runtime_settings.azure_openai_endpoint,
                azure=True,
            )
        if selected_provider is None and runtime_settings.openai_api_key is not None:
            selected_provider = OpenAIProvider(
                runtime_settings.openai_api_key.get_secret_value(), runtime_settings.openai_model
            )
        selected_provider = selected_provider or FakeLlmProvider()
        # Natural language always reaches the configured semantic provider first.
        # Deterministic routing is only a safety net for rejected general responses.
        semantic_plan_policy = SemanticPlanPolicy(
            runtime_settings.semantic_plan_policy_mode,
            DeterministicPlanRouter(),
        )
        app.state.provider = selected_provider
        app.state.orchestrator = Orchestrator(
            app.state.messages,
            app.state.turns,
            selected_provider,
            app.state.tools,
            app.state.conversations,
            semantic_plan_policy=semantic_plan_policy,
        )
        app.state.database_ready = True
        yield
        await dealership.close()
        close_provider = getattr(selected_provider, "close", None)
        if close_provider:
            await close_provider()

    application = FastAPI(title="Northstar Motors Webchat", lifespan=lifespan)
    application.add_exception_handler(DealershipError, dealership_error_response)
    application.add_middleware(SecurityMiddleware, settings=runtime_settings)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[runtime_settings.webchat_allowed_origin],
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Accept", "Content-Type"],
    )
    application.mount(
        "/widget",
        StaticFiles(directory=Path(__file__).resolve().parent / "widget"),
        name="northstar-chat-widget",
    )
    application.include_router(conversation_router)

    @application.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok", "service": "northstar-webchat"}

    @application.get("/health/ready")
    async def ready() -> dict[str, object]:
        database_ready = bool(getattr(application.state, "database_ready", False))
        return {
            "status": "ready" if database_ready else "not_ready",
            "checks": {"database": "ok" if database_ready else "unavailable"},
        }

    return application


app = create_app()
