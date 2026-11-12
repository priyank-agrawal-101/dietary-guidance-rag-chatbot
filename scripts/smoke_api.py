"""Test the running local API, with at most two answered live Groq chat requests.

The server loads credentials; this script never reads .env or handles API keys.
Failures stop further hosted tests. Quota exhaustion is tested offline, not by spending quota.
"""

import argparse
import json
import time
from pathlib import Path

import httpx

from dietary_rag.api.contracts import ChatResponse, ServiceError

ROOT = Path(__file__).resolve().parents[1]
CASES = (
    ("refusal", {"question": "What diet treats diabetes?"}, 200, "out_of_scope"),
    (
        "structured_data",
        {"question": "How much protein is in an egg?"},
        200,
        "requires_structured_data",
    ),
    ("invalid_request", {"question": ""}, 422, "invalid_request"),
    (
        "unknown_selection",
        {
            "question": "How should I store leftovers?",
            "scope": {"mode": "selected", "document_ids": ["unknown"]},
        },
        404,
        "unknown_document",
    ),
)
LIVE_CASES = (
    (
        "storage",
        {
            "question": "How long can hard cooked eggs stay in the refrigerator or freezer?",
            "scope": {"mode": "selected", "document_ids": ["mn_storage_chart"]},
        },
    ),
    (
        "comparison",
        {
            "question": "What internal temperature should chicken be cooked to?",
            "scope": {
                "mode": "selected",
                "document_ids": ["cdc_food_safety", "mn_safe_grilling_pdf"],
            },
        },
    ),
)


def run(base_url, report_path):
    review = {
        "version": "groq_live_api_review_v1",
        "provider": "real Groq via local HTTP API",
        "model": "openai/gpt-oss-120b",
        "passed": False,
        "checks": [],
        "cases": [],
    }
    baseline = json.loads((ROOT / "docs/api-review.json").read_text())
    expected = {row["case"]: row["response"] for row in baseline["cases"]}
    with httpx.Client(base_url=base_url, timeout=75, trust_env=False) as client:

        def check(name, passed):
            review["checks"].append({"check": name, "passed": bool(passed)})
            if not passed:
                raise ValueError("smoke check failed")

        def chat(name, payload):
            started = time.monotonic()
            result = client.post("/chat", json=payload)
            body = result.json()
            review["cases"].append(
                {
                    "case": name,
                    "http_status": result.status_code,
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                    "response": body,
                }
            )
            check(
                name + "_request_id", body.get("request_id") == result.headers.get("X-Request-ID")
            )
            if "code" in body:
                ServiceError.model_validate(body)
            else:
                ChatResponse.model_validate(body)
            return result.status_code, body

        try:
            live, ready, listing = (
                client.get(url) for url in ("/health/live", "/health/ready", "/documents")
            )
            review["live"], review["ready"] = live.json(), ready.json()
            check("liveness", live.status_code == 200 and live.json()["status"] == "alive")
            check("readiness", ready.status_code == 200 and ready.json()["status"] == "ready")
            check("groq_configured", ready.json()["generation"] == "groq_evidence_selection_v1")
            check("document_listing", listing.status_code == 200)
            review["available_document_ids"] = [
                d["document_id"] for d in listing.json()["documents"] if d["available"]
            ]
            check(
                "snapshot_consistency",
                listing.json()["corpus_version"] == ready.json()["corpus_version"],
            )
            before = client.get("/usage").json()
            review["usage_before"] = before
            for name, payload, status, state in CASES:
                actual_status, body = chat(name, payload)
                check(
                    name, actual_status == status and body.get("status", body.get("code")) == state
                )
            after_gates = client.get("/usage").json()
            check(
                "pre_generation_states_spend_nothing",
                before["windows"]["day"]["requests"] == after_gates["windows"]["day"]["requests"]
                and before["windows"]["day"]["tokens"] == after_gates["windows"]["day"]["tokens"],
            )
            for name, payload in LIVE_CASES:
                status, body = chat(name, payload)
                check(name + "_answered", status == 200 and body.get("status") == "answered")
                check(name + "_has_citations", bool(body["citations"]))
                if body["corpus_version"] == baseline["ready"]["corpus_version"]:
                    for field in (
                        "document_answers",
                        "citations",
                        "searched_documents",
                        "unanswered_aspects",
                    ):
                        check(name + "_canonical_" + field, body[field] == expected[name][field])
                if name == "comparison":
                    check(
                        "comparison_source_separation",
                        len(body["document_answers"]) == 2
                        and any(
                            c["format"] == "pdf" and c["physical_pages"] for c in body["citations"]
                        ),
                    )
            review["passed"] = True
        except Exception as exc:
            # Error type only: no raw server/provider exception, credential or body logging.
            review["failure_type"] = type(exc).__name__
        finally:
            try:
                after = client.get("/usage").json()
                review["usage_after"] = after
                if "usage_before" in review:
                    before = review["usage_before"]["windows"]["day"]
                    review["usage_delta"] = {
                        key: after["windows"]["day"][key] - before[key]
                        for key in ("requests", "tokens")
                    }
            except Exception:
                review["usage_after_unavailable"] = True
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(review, indent=2) + "\n")
    print(
        json.dumps(
            {
                "passed": review["passed"],
                "cases": [
                    {
                        "case": row["case"],
                        "http_status": row["http_status"],
                        "state": row["response"].get("status", row["response"].get("code")),
                    }
                    for row in review["cases"]
                ],
                "usage_delta": review.get("usage_delta"),
                "report": str(report_path),
            }
        )
    )
    return 0 if review["passed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--report", type=Path, default=ROOT / "docs/groq-live-review.json")
    args = parser.parse_args()
    raise SystemExit(run(args.base_url, args.report))
