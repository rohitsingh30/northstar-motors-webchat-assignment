"""Resolve customer questions against authoritative public business facts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

BusinessTopic = Literal["finance", "privacy", "part_exchange", "general"]
ResolutionOutcome = Literal["matched", "ambiguous", "unavailable"]


@dataclass(frozen=True)
class FactSpec:
    """Describe the meaning of one platform field, independent of user wording."""

    key: str
    topic: BusinessTopic
    label: str
    path: tuple[str, ...]
    description: str


@dataclass(frozen=True)
class BusinessFact:
    key: str
    topic: BusinessTopic
    label: str
    value: str

    def as_view(self) -> dict[str, str]:
        return {
            "key": self.key,
            "topic": self.topic,
            "label": self.label,
            "value": self.value,
        }


@dataclass(frozen=True)
class BusinessInformationResolution:
    outcome: ResolutionOutcome
    topic: BusinessTopic
    facts: tuple[BusinessFact, ...] = ()


FACT_SPECS: tuple[FactSpec, ...] = (
    FactSpec(
        "organisation.name",
        "general",
        "Organisation",
        ("organisation",),
        "Name of the Northstar Motors business and organisation",
    ),
    FactSpec(
        "organisation.market",
        "general",
        "Market",
        ("market",),
        "Country and market where Northstar Motors operates",
    ),
    FactSpec(
        "organisation.currency",
        "general",
        "Currency",
        ("currency",),
        "Currency used for Northstar Motors prices and payments",
    ),
    FactSpec(
        "finance.notice",
        "finance",
        "Finance",
        ("finance", "notice"),
        "Finance qualification, credit-broker status, terms and approval notice",
    ),
    FactSpec(
        "finance.minimum_age",
        "finance",
        "Minimum customer age",
        ("finance", "minimumAge"),
        "Minimum customer eligibility age",
    ),
    FactSpec(
        "part_exchange.estimate_notice",
        "part_exchange",
        "Part-exchange estimates",
        ("partExchange", "estimateNotice"),
        "Qualification and calculation basis for indicative part-exchange valuations",
    ),
    FactSpec(
        "privacy.contact",
        "privacy",
        "Privacy contact",
        ("privacyContact",),
        "Contact address for privacy and personal-data questions",
    ),
)

# Some public facts are qualifications of an entire topic, rather than optional answers to one
# wording.  Once a question has genuinely matched that topic, the qualification must accompany a
# more specific fact (for example, minimum age) so relevance ranking cannot silently remove a
# customer-protection notice.
_REQUIRED_TOPIC_FACTS: dict[BusinessTopic, tuple[str, ...]] = {
    "finance": ("finance.notice",),
    "part_exchange": ("part_exchange.estimate_notice",),
}

_QUERY_NOISE = frozenset(
    {
        "about",
        "and",
        "are",
        "business",
        "can",
        "car",
        "could",
        "did",
        "does",
        "for",
        "from",
        "have",
        "how",
        "information",
        "into",
        "motor",
        "motors",
        "northstar",
        "our",
        "please",
        "policy",
        "question",
        "that",
        "the",
        "their",
        "there",
        "they",
        "this",
        "vehicle",
        "was",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
    }
)


class BusinessInformationResolver:
    """Return only platform facts relevant to the exact customer question."""

    def resolve(
        self,
        data: dict[str, Any],
        *,
        topic: BusinessTopic,
        question: str,
    ) -> BusinessInformationResolution:
        query_terms = _tokens(question)
        candidates: list[tuple[int, BusinessFact]] = []
        for spec in FACT_SPECS:
            if topic != "general" and spec.topic != topic:
                continue
            value = _path_value(data, spec.path)
            if value in (None, ""):
                continue
            document_terms = _tokens(" ".join((spec.label, spec.description, str(value))))
            score = len(query_terms.intersection(document_terms))
            if score:
                candidates.append(
                    (
                        score,
                        BusinessFact(
                            spec.key,
                            spec.topic,
                            spec.label,
                            str(value),
                        ),
                    )
                )

        if not candidates:
            return BusinessInformationResolution("unavailable", topic)

        highest_score = max(score for score, _ in candidates)
        strongest = tuple(fact for score, fact in candidates if score == highest_score)
        strongest_topics = {fact.topic for fact in strongest}
        if topic == "general" and len(strongest_topics) > 1:
            return BusinessInformationResolution("ambiguous", topic, strongest)
        strongest_keys = {fact.key for fact in strongest}
        required_keys = tuple(
            dict.fromkeys(
                key
                for matched_topic in strongest_topics
                for key in _REQUIRED_TOPIC_FACTS.get(matched_topic, ())
            )
        )
        specs_by_key = {spec.key: spec for spec in FACT_SPECS}
        required = tuple(
            BusinessFact(
                specs_by_key[key].key,
                specs_by_key[key].topic,
                specs_by_key[key].label,
                str(_path_value(data, specs_by_key[key].path)),
            )
            for key in required_keys
            if key in specs_by_key
            and _path_value(data, specs_by_key[key].path) not in (None, "")
            and key not in strongest_keys
        )
        return BusinessInformationResolution("matched", topic, (*required, *strongest))


def _tokens(value: str) -> frozenset[str]:
    return frozenset(
        token
        for token in re.findall(r"[a-z0-9]+", value.casefold())
        if len(token) >= 3 and token not in _QUERY_NOISE
    )


def _path_value(data: dict[str, Any], path: tuple[str, ...]) -> Any | None:
    value: Any = data
    for component in path:
        if not isinstance(value, dict):
            return None
        value = value.get(component)
    return value
