from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import AnyHttpUrl

from dietary_rag.corpus.contracts import SourceDates
from dietary_rag.corpus.identity import sha256_bytes, stable_id


def test_raw_hash_changes_with_original_bytes():
    assert sha256_bytes(b"one") != sha256_bytes(b"two")


def test_identity_is_canonical_and_changes_with_evidence_or_rules():
    payload = {"document_id": "example", "text": "Retain the warning", "parser_version": "1"}
    identity = stable_id("block", payload)
    assert identity == stable_id("block", dict(reversed(list(payload.items()))))
    assert identity != stable_id("block", payload | {"text": "Changed warning"})
    assert identity != stable_id("block", payload | {"parser_version": "2"})
    assert identity != stable_id("block", payload, schema_version="2")


def test_equivalent_timezone_instants_and_dates_are_canonical():
    utc = datetime(2026, 11, 11, tzinfo=UTC)
    offset = utc.astimezone(timezone(timedelta(hours=5, minutes=30)))
    assert stable_id("docv", {"time": utc}) == stable_id("docv", {"time": offset})
    assert stable_id("docv", {"dates": SourceDates()}) == stable_id(
        "docv", {"dates": SourceDates().model_dump()}
    )
    assert stable_id("docv", {"url": AnyHttpUrl("https://example.gov/guide")}) == stable_id(
        "docv", {"url": "https://example.gov/guide"}
    )


@pytest.mark.parametrize("payload", [{"time": datetime(2026, 11, 11)}, {"value": float("nan")}])
def test_ambiguous_identity_values_fail(payload):
    with pytest.raises(ValueError):
        stable_id("block", payload)
