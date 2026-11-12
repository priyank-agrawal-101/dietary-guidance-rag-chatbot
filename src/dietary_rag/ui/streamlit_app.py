"""Native Streamlit adapter for the existing grounded answering service."""

import logging
import os
import re
import threading
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit

import streamlit as st

from dietary_rag.api.runtime import build_answer_service, load_environment
from dietary_rag.corpus.registry import load_registry
from dietary_rag.deployment.bootstrap import bootstrap_identity, ensure_runtime
from dietary_rag.deployment.service import ChatHost
from dietary_rag.operations.metrics import Metrics
from dietary_rag.settings import load_settings

ROOT = Path(__file__).resolve().parents[3]
LOGGER = logging.getLogger(__name__)
ERRORS = {
    "service_busy": "Other questions are being answered. Please try again in a moment.",
    "corpus_unavailable": "The sources are temporarily unavailable. Please try again shortly.",
    "model_unavailable": "The answer service is temporarily unavailable. Please try again shortly.",
    "answer_validation_failed": "I couldn't verify an answer against the sources. Try a more specific question.",
    "request_timeout": "This question took too long to answer. Try a shorter question or fewer sources.",
    "usage_limit_exceeded": "This learning app has reached its answer allowance. Please try again later.",
    "token_budget_exceeded": "Please narrow the question or select fewer sources.",
    "invalid_request": "Please check your question and source selection.",
    "unknown_document": "That source is no longer available. Please choose another source.",
    "source_unavailable": "A selected source is unavailable. Please choose another source.",
}


def suggested_questions():
    path = ROOT / "docs/test-questions-fot-chatbot"
    if not path.is_file():
        return []
    return re.findall(r"^\d+\.\s+(.+)$", path.read_text(encoding="utf-8"), re.MULTILINE)


def prefill_question(question):
    st.session_state["question_input"] = question


def render_suggestions(questions, *, disabled):
    if not questions:
        return
    with st.expander("Suggested questions", expanded=not st.session_state.turns):
        st.caption("Choose a question to fill the chat box. You can edit it before sending.")

        def buttons(items, offset):
            columns = st.columns(2)
            for index, question in enumerate(items, offset):
                columns[index % 2].button(
                    question,
                    key=f"suggested_question_{index}",
                    on_click=prefill_question,
                    args=(question,),
                    disabled=disabled,
                    width="stretch",
                )

        buttons(questions[:6], 0)
        if len(questions) > 6:
            with st.expander("More questions"):
                buttons(questions[6:], 6)


def safe_source_url(value, reviewed):
    try:
        link, trusted = urlsplit(str(value)), urlsplit(str(reviewed))
        if (
            link.scheme != "https"
            or trusted.scheme != "https"
            or link.username
            or link.password
            or trusted.username
            or trusted.password
            or (link.hostname, link.port, link.path, link.query)
            != (trusted.hostname, trusted.port, trusted.path, trusted.query)
        ):
            return None
        return str(value)
    except ValueError:
        return None


@st.cache_resource(show_spinner=False)
def cached_runtime(identity, cache_dir):
    settings = load_settings(ROOT / "config/settings.yaml", project_root=ROOT, environ={})
    registry = load_registry(settings.source_registry)
    active = ensure_runtime(
        ROOT,
        settings,
        registry,
        cache_dir,
        model_seed=os.environ.get("DIETARY_STREAMLIT_MODEL_SEED"),
        tokenizer_seed=os.environ.get("DIETARY_STREAMLIT_TOKENIZER_SEED"),
    )
    return active, threading.BoundedSemaphore(settings.api.max_in_flight), Metrics()


@st.cache_resource(show_spinner=False)
def cached_host(identity, cache_dir, usage_dir, key_fingerprint, offline, _api_key):
    # Offline/online sessions and credential rotation share one corpus lock and gate.
    # No visitor question/history/selection in these global resources.
    active, capacity, metrics = cached_runtime(identity, cache_dir)
    settings = active.settings.model_copy(update={"data_dir": Path(usage_dir).resolve()})
    service, ledger = build_answer_service(
        settings, active, environ={"GROQ_API_KEY": _api_key}, offline=offline
    )
    return ChatHost(
        service, active, ledger, settings.api.max_in_flight, capacity=capacity, metrics=metrics
    )


def render_response(response, documents):
    if "code" in response:
        st.info(ERRORS.get(response["code"], "Please try again shortly."))
    elif response["status"] != "answered":
        st.text(response["message"])
    else:
        citations = {c["citation_id"]: c for c in response["citations"]}
        for answer in response["document_answers"]:
            if len(response["document_answers"]) > 1:
                doc = documents.get(answer["document_id"])
                st.text(doc.document_name if doc else answer["document_id"])
            for claim in answer["claims"]:
                st.text(claim["text"])
                st.caption("Sources: " + ", ".join(claim["citation_ids"]))
        if response["unanswered_aspects"]:
            st.info(
                "Some parts of your question aren't covered by the sources. See diagnostics for details."
            )
        with st.expander("Sources and supporting passages"):
            for citation in citations.values():
                st.text(
                    f"{citation['citation_id']}: {citation['document_name']} — {citation['publisher']}"
                )
                st.text(citation["section_heading"])
                doc = documents.get(citation["document_id"])
                reviewed = str(doc.source_url) if doc else ""
                if url := safe_source_url(citation["source_url"], reviewed):
                    st.link_button("Open source", url)
                for page, page_url in zip(
                    citation["physical_pages"], citation["page_urls"], strict=False
                ):
                    if url := safe_source_url(page_url, reviewed):
                        st.link_button(f"Physical page {page}", url)
                for quote in citation["support_quotes"]:
                    st.text(quote)
    with st.expander("Diagnostics"):
        st.json(response)


def main():
    st.set_page_config(page_title="Food & dietary guidance", page_icon="🥗", layout="centered")
    st.title("Food & dietary guidance")
    st.caption(
        "Ask about healthy food choices and household food safety in the US. Answers cite reviewed sources."
    )
    env = load_environment(ROOT)
    try:
        api_key = st.secrets.get("GROQ_API_KEY", env.get("GROQ_API_KEY", ""))
    except FileNotFoundError:
        api_key = env.get("GROQ_API_KEY", "")
    offline = st.sidebar.toggle("Use offline source excerpts", value=not bool(api_key))
    st.sidebar.caption("Learning project · Human–AI assisted review")
    if offline:
        st.info("Offline mode: answers use source excerpts. A Groq key enables generated answers.")
    cache_dir = os.environ.get("DIETARY_STREAMLIT_CACHE_DIR", str(ROOT / ".streamlit-runtime"))
    usage_dir = os.environ.get("DIETARY_STREAMLIT_USAGE_DIR", str(Path(cache_dir) / "usage-state"))
    if "DIETARY_STREAMLIT_USAGE_DIR" not in os.environ:
        st.sidebar.caption(
            "The answer allowance applies to this app instance and may reset when the host restarts."
        )
    try:
        with st.spinner("Preparing reviewed sources. First startup may take a few minutes…"):
            host = cached_host(
                bootstrap_identity(ROOT),
                cache_dir,
                usage_dir,
                sha256(str(api_key).encode()).hexdigest(),
                offline,
                str(api_key),
            )
            state = host.active.corpus_status()
            documents = state["documents"]
    except Exception as exc:
        # Log only type: exception text could contain provider URLs/credentials.
        LOGGER.error("Streamlit source startup failed (%s)", type(exc).__name__)
        st.error(
            "The sources couldn't be prepared. Please retry. The app owner can check the startup log."
        )
        if st.button("Retry loading sources"):
            st.rerun()
        st.stop()
    with st.sidebar.expander("Service diagnostics"):
        st.json(
            {
                "corpus_version": state.get("corpus_version"),
                "release_id": state.get("release_id"),
                "available_sources": len(documents),
                "generation": "offline excerpts" if offline else "Groq",
                "metrics": host.metrics.snapshot(),
            }
        )
    mode = st.sidebar.radio("Search sources", ["All available sources", "Choose sources"])
    selected = st.sidebar.multiselect(
        "Sources",
        sorted(documents),
        format_func=lambda identifier: documents[identifier].document_name,
        disabled=mode == "All available sources",
    )
    official_only = st.sidebar.checkbox("Official guidance only")
    st.session_state.setdefault("turns", [])
    if st.sidebar.button("Clear conversation"):
        st.session_state.turns = []
    for turn in st.session_state.turns:
        with st.chat_message("user"):
            st.text(turn["question"])
        with st.chat_message("assistant"):
            render_response(turn["response"], documents)
    valid_scope = mode != "Choose sources" or bool(selected)
    if not valid_scope:
        st.info("Choose at least one source to ask a question.")
    render_suggestions(suggested_questions(), disabled=not valid_scope)
    prompt = st.chat_input(
        "Ask a food or food-safety question",
        key="question_input",
        max_chars=host.active.settings.runtime.question_max_characters,
        disabled=not valid_scope,
    )
    if prompt:
        scope = (
            {"mode": "selected", "document_ids": selected}
            if mode == "Choose sources"
            else {"mode": "all"}
        )
        payload = {"question": prompt, "scope": scope, "official_only": official_only}
        with st.chat_message("user"):
            st.text(prompt)
        with st.chat_message("assistant"), st.spinner("Checking the sources…"):
            try:
                response = host.answer(payload).model_dump(mode="json")
            except Exception as exc:
                LOGGER.error("Streamlit answer failed (%s)", type(exc).__name__)
                response = {
                    "request_id": "streamlit-error",
                    "code": "corpus_unavailable",
                    "message": "The sources are unavailable.",
                }
            render_response(response, documents)
        st.session_state.turns.append({"question": prompt, "response": response})
        st.session_state.turns = st.session_state.turns[-20:]


if __name__ == "__main__":
    main()
