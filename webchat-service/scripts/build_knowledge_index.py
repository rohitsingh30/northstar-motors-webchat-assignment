"""Build the packaged runtime knowledge index from authoritative Markdown sections."""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]
OUTPUT = (
    REPOSITORY
    / "webchat-service/webchat/orchestration/retrieval/knowledge.json"
)


@dataclass(frozen=True)
class Source:
    path: str
    audience: str
    prefix: str


SOURCES = (
    Source("PRODUCT-BRIEF.md", "planner", "product"),
    Source("docs/BUSINESS-SEMANTICS.md", "planner", "business"),
    Source("docs/CUSTOMER-KNOWLEDGE.md", "customer", "customer"),
)

PLANNER_ONLY_HEADINGS = {
    "Overview",
    "General rules",
    "Error handling",
    "Bolton workshop availability",
    "Running the product",
    "Technical scope",
}

CUSTOMER_GUIDANCE_PATTERN = re.compile(
    r"\b(?:assistant|model|planner|reviewer|system prompt)\b",
    re.IGNORECASE,
)
FOLLOW_UP_ACTION_PATTERN = re.compile(
    r"^<!--\s*follow-up-action:\s*([a-z][a-z0-9_]{0,79})\s*-->$"
)


def main() -> None:
    rendered = json.dumps(build_entries(), indent=2, ensure_ascii=False) + "\n"
    if "--check" in sys.argv[1:]:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != rendered:
            raise SystemExit(
                "knowledge.json is stale; run scripts/build_knowledge_index.py"
            )
        return
    OUTPUT.write_text(rendered, encoding="utf-8")


def build_entries() -> list[dict[str, object]]:
    return [
        entry
        for source in SOURCES
        for entry in _document_entries(source)
    ]


def _document_entries(source: Source) -> list[dict[str, object]]:
    sections = _sections((REPOSITORY / source.path).read_text(encoding="utf-8"))
    entries: list[dict[str, object]] = []
    used_ids: set[str] = set()
    for heading, lines in sections:
        text = _plain_text(lines)
        if not text:
            continue
        audience = "planner" if heading in PLANNER_ONLY_HEADINGS else source.audience
        if audience == "customer" and CUSTOMER_GUIDANCE_PATTERN.search(text):
            raise ValueError(
                f"customer knowledge must be final customer-facing copy: {source.path}#{heading}"
            )
        identifier = f"{source.prefix}.{_slug(heading)}"
        suffix = 2
        while identifier in used_ids:
            identifier = f"{source.prefix}.{_slug(heading)}_{suffix}"
            suffix += 1
        used_ids.add(identifier)
        follow_up_actions = [
            match.group(1)
            for line in lines
            if (match := FOLLOW_UP_ACTION_PATTERN.fullmatch(line.strip()))
        ]
        if len(follow_up_actions) > 1:
            raise ValueError(f"knowledge section has multiple follow-up actions: {heading}")
        entry: dict[str, object] = {
            "id": identifier,
            "title": heading,
            "text": text,
            "source": f"{source.path}#{heading}",
            "audience": audience,
        }
        if follow_up_actions:
            entry["follow_up_action"] = {"type": follow_up_actions[0]}
        entries.append(entry)
    return entries


def _sections(markdown: str) -> list[tuple[str, list[str]]]:
    sections: list[tuple[str, list[str]]] = []
    heading = "Overview"
    body: list[str] = []
    in_code = False
    for line in markdown.splitlines():
        if line.startswith("```"):
            in_code = not in_code
            continue
        match = re.match(r"^#{2,3}\s+(.+?)\s*$", line)
        if match and not in_code:
            if body:
                sections.append((heading, body))
            heading = match.group(1).strip()
            body = []
        elif not in_code:
            body.append(line)
    if body:
        sections.append((heading, body))
    return sections


def _plain_text(lines: list[str]) -> str:
    kept: list[str] = []
    for line in lines:
        value = line.strip()
        if not value or value.startswith(("|", "<!--")):
            continue
        value = re.sub(r"^[-*]\s+", "", value)
        value = re.sub(r"\[([^]]+)]\([^)]+\)", r"\1", value)
        value = value.replace("`", "")
        kept.append(value)
    return " ".join(kept)


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


if __name__ == "__main__":
    main()
