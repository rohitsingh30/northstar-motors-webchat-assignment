"""Small shared persistence primitives."""

from datetime import UTC, datetime
from hashlib import sha256


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def token_hash(token: str) -> bytes:
    return sha256(token.encode("utf-8")).digest()
