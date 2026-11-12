import pytest
from pydantic import ValidationError

from dietary_rag.api.contracts import ChatRequest, ChatResponse, ServiceError, service_error
from dietary_rag.settings import RuntimeSettings


@pytest.mark.parametrize(
    "payload",
    [
        {"question": " "},
        {"question": 123},
        {"question": "x" * 2001},
        {"question": "store food", "scope": {"mode": "all", "document_ids": ["doc"]}},
        {"question": "store food", "scope": {"mode": "selected", "document_ids": []}},
        {"question": "store food", "scope": {"mode": "selected", "document_ids": ["bad id"]}},
        {"question": "store food", "document_ids": ["doc"]},
        {"question": "store food", "source_tiers": []},
        {"question": "store food", "source_tiers": ["editorial_reference"], "official_only": True},
        {"question": "store food", "limit": 21},
        {"question": "store food", "limit": True},
    ],
)
def test_invalid_requests(payload):
    with pytest.raises(ValidationError):
        ChatRequest.model_validate(payload)


def test_selection_deduplication_and_runtime_limits():
    payload = {
        "question": "store food",
        "scope": {"mode": "selected", "document_ids": ["doc", "doc"]},
    }
    assert ChatRequest.model_validate(payload).search_request().selected_document_ids == ("doc",)
    with pytest.raises(ValidationError):
        ChatRequest.model_validate(
            payload, context={"runtime": RuntimeSettings(question_max_characters=5)}
        )
    request = ChatRequest.model_validate(
        {"question": "x" * 2001}, context={"runtime": RuntimeSettings(question_max_characters=3000)}
    )
    assert len(request.question) == 2001


@pytest.mark.parametrize(
    "status", ["out_of_scope", "requires_structured_data", "needs_clarification"]
)
def test_preretrieval_messages_have_no_evidence(status):
    response = ChatResponse(request_id="req", status=status, message="Fixed message")
    assert response.citations == response.searched_documents == ()


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "not_covered", "message": "No evidence"},
        {"status": "answered"},
        {
            "status": "out_of_scope",
            "message": "referral",
            "corpus_version": "corpus",
            "searched_documents": ["doc"],
        },
        {"status": "model_unavailable", "message": "error"},
        {"status": "needs_clarification"},
    ],
)
def test_response_state_invariants(payload):
    with pytest.raises(ValidationError):
        ChatResponse(request_id="req", **payload)


def test_evidence_gap_and_errors_are_distinct():
    ChatResponse(
        request_id="req",
        status="not_covered",
        message="Not in searched evidence",
        corpus_version="corpus",
        searched_documents=("doc",),
    )
    for code, status in [
        ("invalid_request", 422),
        ("unknown_document", 404),
        ("source_unavailable", 409),
        ("corpus_unavailable", 503),
        ("model_unavailable", 503),
        ("answer_validation_failed", 502),
        ("request_timeout", 504),
    ]:
        error = service_error("req", code)
        assert isinstance(error, ServiceError) and error.http_status == status
        assert "status" not in error.model_dump()


def answer_payload():
    return {
        "request_id": "req",
        "status": "answered",
        "corpus_version": "corpus",
        "searched_documents": ["doc"],
        "document_answers": [
            {
                "document_id": "doc",
                "claims": [{"text": "Refrigerate leftovers.", "citation_ids": ["cite"]}],
            }
        ],
        "citations": [
            {
                "citation_id": "cite",
                "document_id": "doc",
                "document_name": "Guidance",
                "publisher": "Agency",
                "source_tier": "official_guidance",
                "format": "pdf",
                "physical_pages": [1],
                "source_url": "https://example.gov/guide.pdf#page=1",
                "section_heading": "Storage",
                "chunk_ids": ["chunk"],
                "support_quotes": ["Refrigerate leftovers."],
            }
        ],
    }


def test_answer_structure_and_policy_hook():
    from dietary_rag.policy.runtime import PolicyRuntime
    from dietary_rag.policy.service import PolicyViolation

    payload = answer_payload()
    response = ChatResponse.model_validate(payload)
    assert response.citations[0].publication_year is None
    runtime = PolicyRuntime(lambda: None)
    assert runtime.check_response_policy(response) == response
    payload["document_answers"][0]["claims"][0]["text"] = "Eat 1800 calories per day."
    with pytest.raises(PolicyViolation):
        runtime.check_response_policy(payload)


@pytest.mark.parametrize(
    "mutation",
    ["wrong_document", "missing_page", "missing_quote", "unknown_citation", "unsearched_document"],
)
def test_invalid_answer_structure(mutation):
    payload = answer_payload()
    if mutation == "wrong_document":
        payload["citations"][0]["document_id"] = "another_doc"
    elif mutation == "missing_page":
        payload["citations"][0]["physical_pages"] = []
    elif mutation == "missing_quote":
        payload["citations"][0]["support_quotes"] = []
    elif mutation == "unknown_citation":
        payload["document_answers"][0]["claims"][0]["citation_ids"] = ["unknown"]
    else:
        payload["searched_documents"] = ["another_doc"]
    with pytest.raises(ValidationError):
        ChatResponse.model_validate(payload)
