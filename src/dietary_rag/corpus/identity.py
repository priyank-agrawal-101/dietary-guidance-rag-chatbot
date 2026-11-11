"""Deterministic evidence identities without I/O or machine-specific defaults."""

import hashlib
import json
import re
from datetime import UTC, date, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import AnyUrl, BaseModel


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _canonical(value.model_dump(mode="python"))
    if isinstance(value, AnyUrl):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("identity timestamps require a timezone")
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, Enum):
        return _canonical(value.value)
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("identity mapping keys must be strings")
        return {key: _canonical(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported identity payload type: {type(value).__name__}")


def stable_id(namespace: str, payload: dict[str, Any], *, schema_version: str = "1") -> str:
    """Hash canonical UTF-8 JSON of the namespace, schema and explicit identity payload."""
    if not re.fullmatch(r"[a-z][a-z0-9_]*", namespace) or not schema_version.strip():
        raise ValueError("identity namespace/schema version must be valid and nonempty")
    if not isinstance(payload, dict):
        raise ValueError("identity payload must be a mapping")
    content = json.dumps(
        {"namespace": namespace, "schema_version": schema_version, "payload": _canonical(payload)},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return f"{namespace}_{sha256_bytes(content)}"
