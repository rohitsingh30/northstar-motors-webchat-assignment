"""Server-owned wording and allowlists that an AI may not paraphrase or invent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ApprovedNotice:
    id: str
    version: int
    kind: Literal["privacy", "finance", "part_exchange"]
    text: str


COLLECTOR_PRIVACY_NOTICE = ApprovedNotice(
    id="collector-privacy",
    version=1,
    kind="privacy",
    text=(
        "Your secure answers stay in this browser tab until the request is ready. "
        "They are sent only to the validated Northstar request endpoint and can be read by "
        "trusted scripts running on this website."
    ),
)

PART_EXCHANGE_NOTICE = ApprovedNotice(
    id="part-exchange-indicative",
    version=1,
    kind="part_exchange",
    text=(
        "Any part-exchange value is indicative and subject to inspection, vehicle history "
        "and final appraisal."
    ),
)
