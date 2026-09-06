from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from jsonschema import validate as validate_json_schema
from pydantic import BaseModel

ExecutorKind = Literal["application", "http", "mcp"]
Invocation = Literal["planner", "structured_action", "internal", "confirmation"]
Risk = Literal["read", "draft", "confirmed_write"]
ResultMode = Literal["render", "evidence", "workflow"]


@dataclass(frozen=True)
class ToolDefinition:
    """One source-independent executable operation and its runtime policy."""

    id: str
    title: str
    description: str
    input_model: type[BaseModel]
    provider_input_model: type[BaseModel] | None = None
    trusted_input_model: type[BaseModel] | None = None
    executor_kind: ExecutorKind = "application"
    executor_reference: str = ""
    invocation: Invocation = "planner"
    risk: Risk = "read"
    preconditions: tuple[str, ...] = ()
    reference_inputs: tuple[tuple[str, str], ...] = ()
    candidate_subject_field: str | None = None
    retrieval_examples: tuple[str, ...] = ()
    result_mode: ResultMode = "render"
    timeout_seconds: float = 12.0
    available: bool = True
    input_schema_override: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None

    @property
    def input_schema(self) -> dict[str, Any]:
        if self.input_schema_override is not None:
            return self.input_schema_override
        schema = self.input_model.model_json_schema()
        schema.pop("title", None)
        return schema

    @property
    def provider_input_schema(self) -> dict[str, Any]:
        if self.provider_input_model is None and self.input_schema_override is not None:
            return self.input_schema_override
        model = self.provider_input_model or self.input_model
        schema = model.model_json_schema()
        schema.pop("title", None)
        return schema

    def validate_arguments(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if self.input_schema_override is not None:
            validate_json_schema(arguments, self.input_schema_override)
            return arguments
        validated = self.input_model.model_validate(arguments)
        # Validation must not silently turn schema defaults into model-authored
        # arguments. Application handlers remain the sole owners of runtime
        # defaults such as the first result page or the default sort order.
        return validated.model_dump(include=set(arguments), exclude_none=True, exclude_unset=True)

    def validate_provider_arguments(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if self.provider_input_model is None and self.input_schema_override is not None:
            validate_json_schema(arguments, self.input_schema_override)
            return arguments
        model = self.provider_input_model or self.input_model
        validated = model.model_validate(arguments)
        return validated.model_dump(
            include=set(arguments), exclude_none=True, exclude_unset=True
        )

    def validate_trusted_arguments(self, arguments: dict[str, Any] | None) -> dict[str, Any]:
        if not arguments:
            return {}
        if self.trusted_input_model is None:
            raise ValueError(f"Tool {self.id} does not accept trusted runtime arguments")
        validated = self.trusted_input_model.model_validate(arguments)
        return validated.model_dump(exclude_none=True, exclude_unset=True)

    def provider_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "name": self.id,
            "description": self.description,
            "parameters": self.provider_input_schema,
            "strict": False,
        }
