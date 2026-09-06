from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api.errors import ApiErrorBoundaryMiddleware, dealership_error_response
from .api.restoration import ConversationRestorer
from .api.router import router as conversation_router
from .api.security import SecurityMiddleware
from .api.turn_admission import TurnAdmission
from .config import Settings, get_settings
from .domain.workflows import WorkflowService
from .integrations.contracts import LlmProvider
from .integrations.dealership import DealershipClient, DealershipError
from .integrations.fake_llm import FakeLlmProvider
from .integrations.hosted_llm import HostedLlmProvider
from .observability.logging import configure_logging
from .orchestration.catalogue import UnifiedToolCatalog
from .orchestration.catalogue.mcp import McpServerConfig, McpToolSource
from .orchestration.orchestrator import Orchestrator
from .orchestration.retrieval import FastEmbedCandidateRetriever
from .orchestration.tools.executor import ApplicationToolExecutor
from .persistence.database import Database
from .persistence.repositories import (
    ConversationRepository,
    MessageRepository,
    ProtectedInteractionRepository,
    TurnCommitRepository,
    TurnRepository,
    WorkflowRepository,
)


class RevalidatedWidgetFiles(StaticFiles):
    async def get_response(self, path: str, scope: dict):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


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
        app.state.protected_interactions = ProtectedInteractionRepository(database)
        app.state.turn_commit = TurnCommitRepository(database)
        app.state.turns = TurnRepository(database)
        app.state.turn_admission = TurnAdmission(
            app.state.turns,
            daily_limit=runtime_settings.webchat_daily_turn_limit,
            max_concurrent=runtime_settings.webchat_max_concurrent_turns,
        )
        dealership = DealershipClient(
            runtime_settings.northstar_base_url,
            runtime_settings.northstar_api_key.get_secret_value(),
        )
        app.state.dealership = dealership
        app.state.workflow_repository = WorkflowRepository(database)
        app.state.restorer = ConversationRestorer(app.state.messages, app.state.workflow_repository)
        app.state.workflows = WorkflowService(app.state.workflow_repository, dealership)
        application_executor = ApplicationToolExecutor(dealership, app.state.workflows)
        app.state.tools = UnifiedToolCatalog(application_executor)
        mcp_configs = tuple(McpServerConfig(**server) for server in runtime_settings.mcp_servers())
        if mcp_configs:
            app.state.mcp_source = McpToolSource(mcp_configs)
            for definition in await app.state.mcp_source.discover():
                app.state.tools.register(definition, app.state.mcp_source)
        selected_provider = provider
        if selected_provider is None and runtime_settings.hosted_llm_configured:
            assert runtime_settings.llm_provider_url is not None
            assert runtime_settings.llm_api_key is not None
            assert runtime_settings.llm_model is not None
            selected_provider = HostedLlmProvider(
                provider_url=runtime_settings.llm_provider_url,
                api_key=runtime_settings.llm_api_key.get_secret_value(),
                model=runtime_settings.llm_model,
                catalogue=app.state.tools,
                retriever=FastEmbedCandidateRetriever(app.state.tools),
                request_timeout_seconds=max(
                    5.0,
                    runtime_settings.llm_turn_timeout_seconds - 1.0,
                ),
            )
        if selected_provider is None:
            if runtime_settings.environment != "test":
                # validate_runtime() rejects this state before application startup. Keep this
                # guard here as a second boundary so a production/development process can never
                # silently impersonate the conversational agent with the deterministic test fake.
                raise ValueError("Hosted LLM configuration is required outside tests")
            selected_provider = FakeLlmProvider()
        app.state.provider = selected_provider
        app.state.assistant_mode = (
            "limited_demo" if isinstance(selected_provider, FakeLlmProvider) else "hosted"
        )
        app.state.orchestrator = Orchestrator(
            app.state.messages,
            app.state.turns,
            selected_provider,
            app.state.tools,
            app.state.conversations,
            timeout_seconds=runtime_settings.llm_turn_timeout_seconds,
            workflows=app.state.workflows,
            workflow_repository=app.state.workflow_repository,
            protected_interactions=app.state.protected_interactions,
            turn_commit=app.state.turn_commit,
        )
        app.state.database_ready = True
        yield
        await dealership.close()
        close_provider = getattr(selected_provider, "close", None)
        if close_provider:
            await close_provider()

    application = FastAPI(title="Northstar Motors Webchat", lifespan=lifespan)
    application.add_exception_handler(DealershipError, dealership_error_response)
    application.add_middleware(ApiErrorBoundaryMiddleware)
    application.add_middleware(
        SecurityMiddleware,
        settings=runtime_settings,
        requests_per_minute=runtime_settings.webchat_requests_per_minute,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[runtime_settings.webchat_allowed_origin],
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Accept", "Content-Type"],
    )
    application.mount(
        "/widget",
        RevalidatedWidgetFiles(directory=Path(__file__).resolve().parent / "widget"),
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
            "assistantMode": getattr(application.state, "assistant_mode", "unavailable"),
        }

    return application


app = create_app()
