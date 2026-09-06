"""Strict application-owned contracts for structured collection presentation."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CollectionDisplayItem(StrictModel):
    """One trusted item displayed inside a structured collection."""

    label: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=500)
    message: str | None = Field(default=None, min_length=1, max_length=500)


class CollectionPresentation(StrictModel):
    """Describe collection semantics without asking the model to invent layout."""

    schemaVersion: Literal[1] = 1
    layout: Literal["bullet_list", "chip_grid"]
    purpose: Literal["information", "choice", "clarification"]
    items: list[CollectionDisplayItem] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def layout_matches_semantics(self) -> CollectionPresentation:
        if self.purpose == "information" and self.layout != "bullet_list":
            raise ValueError("information collections require bullet_list")
        return self
