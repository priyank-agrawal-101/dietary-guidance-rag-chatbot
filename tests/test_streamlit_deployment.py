"""Deployment integrity, threaded capacity and Streamlit session regressions."""

import gzip
import io
import json
import tarfile
import threading
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from dietary_rag.api.contracts import ChatResponse
from dietary_rag.deployment.bootstrap import MEMBERS, extract_verified
from dietary_rag.deployment.service import ChatHost

ROOT = Path(__file__).resolve().parents[1]


def test_packaged_corpus_is_complete_and_verified(tmp_path):
    receipt = extract_verified(ROOT, tmp_path)
    assert {
        p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()
    } == MEMBERS
    assert (
        json.loads((tmp_path / "manifest.json").read_text())["chunk_snapshot"]["snapshot_id"]
        == receipt["chunk_snapshot_id"]
    )


def archive_fixture(root, members):
    config = root / "config"
    config.mkdir()
    archive = root / "deployment/artifacts/corpus.tar.gz"
    archive.parent.mkdir(parents=True)
    with (
        gzip.open(archive, "wb") as compressed,
        tarfile.open(fileobj=compressed, mode="w") as stream,
    ):
        for name, kind in members:
            info = tarfile.TarInfo(name)
            info.type = kind
            info.size = 1 if kind == tarfile.REGTYPE else 0
            info.linkname = "../../outside"
            stream.addfile(info, io.BytesIO(b"x") if info.size else None)
    config.joinpath("deployment-artifacts.json").write_text(
        json.dumps(
            {
                "version": "streamlit_corpus_v1",
                "sha256": sha256(archive.read_bytes()).hexdigest(),
                "bytes": archive.stat().st_size,
                "members": {n: {"bytes": 1, "sha256": sha256(b"x").hexdigest()} for n in MEMBERS},
            }
        )
    )


@pytest.mark.parametrize(
    "name,kind",
    [
        ("../outside", tarfile.REGTYPE),
        ("/outside", tarfile.REGTYPE),
        ("evidence.sqlite", tarfile.SYMTYPE),
        ("evidence.sqlite", tarfile.LNKTYPE),
        ("unexpected", tarfile.REGTYPE),
    ],
)
def test_archive_rejects_unsafe_members(tmp_path, name, kind):
    archive_fixture(tmp_path, [(name, kind)])
    with pytest.raises(ValueError, match="unsafe"):
        extract_verified(tmp_path, tmp_path / "out")
    assert not (tmp_path / "outside").exists()


def test_archive_rejects_duplicates_and_missing_members(tmp_path):
    archive_fixture(tmp_path, [("evidence.sqlite", tarfile.REGTYPE)] * 2)
    with pytest.raises(ValueError, match="duplicate"):
        extract_verified(tmp_path, tmp_path / "out")
    with pytest.raises(ValueError):
        extract_verified(tmp_path, tmp_path / "other")


def test_archive_rejects_tampering_before_extracting(tmp_path):
    archive_fixture(tmp_path, [])
    path = tmp_path / "deployment/artifacts/corpus.tar.gz"
    path.write_bytes(path.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="archive checksum"):
        extract_verified(tmp_path, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_capacity_is_shared_and_released_after_failure():
    entered, release = threading.Event(), threading.Event()

    def answer(payload):
        entered.set()
        assert release.wait(3)
        raise RuntimeError("fault")

    host = ChatHost(SimpleNamespace(answer=answer), None, None, 1)
    errors = []

    def worker():
        try:
            host.answer({})
        except RuntimeError:
            errors.append(True)

    thread = threading.Thread(target=worker)
    thread.start()
    try:
        assert entered.wait(3)
        assert host.answer({}).code == "service_busy"
    finally:
        release.set()
        thread.join(3)
    assert errors and host.capacity.acquire(blocking=False)
    host.capacity.release()
    assert host.metrics.snapshot()["chat_states"] == {"service_busy": 1}


def test_safe_citation_urls():
    pytest.importorskip("streamlit")
    from dietary_rag.ui.streamlit_app import safe_source_url

    reviewed = "https://www.fda.gov/food/safety?q=1"
    assert safe_source_url(reviewed + "#page=2", reviewed)
    for bad in (
        "javascript:alert(1)",
        "http://www.fda.gov/food/safety?q=1",
        "https://www.fda.gov/other?q=1",
        "https://evil.example/food/safety?q=1",
        "https://user@www.fda.gov/food/safety?q=1",
        reviewed + "&evil=1",
    ):
        assert safe_source_url(bad, reviewed) is None


@pytest.fixture
def app_host(monkeypatch):
    pytest.importorskip("streamlit")
    from dietary_rag.ui import streamlit_app as ui

    documents = {
        "food": SimpleNamespace(document_name="Food safety", source_url="https://www.fda.gov/food")
    }
    calls = []

    def answer(payload):
        calls.append(payload)
        return ChatResponse(
            request_id="test-request",
            status="out_of_scope",
            message="I can help with food choices and food safety. Please ask a food question.",
        )

    host = SimpleNamespace(
        metrics=SimpleNamespace(snapshot=lambda: {}),
        active=SimpleNamespace(
            store=SimpleNamespace(
                pin=lambda: SimpleNamespace(
                    release=SimpleNamespace(
                        corpus_snapshot_id="test-corpus", release_id="test-release"
                    )
                )
            ),
            corpus_status=lambda: {"documents": documents},
            settings=SimpleNamespace(runtime=SimpleNamespace(question_max_characters=2000)),
        ),
        answer=answer,
    )
    monkeypatch.setattr(ui, "cached_host", lambda *args: host)
    monkeypatch.setattr(ui, "bootstrap_identity", lambda root: "test")
    monkeypatch.setattr(ui, "load_environment", lambda root: {})
    return calls


def test_streamlit_submission_rerun_and_session_isolation(app_host):
    from streamlit.testing.v1 import AppTest

    first = AppTest.from_file(str(ROOT / "streamlit_app.py")).run()
    assert not first.exception
    first.chat_input[0].set_value("Who is the US president?").run()
    assert not first.exception
    assert len(app_host) == 1 and len(first.session_state["turns"]) == 1
    first.checkbox[0].set_value(True).run()
    assert len(app_host) == 1  # Widget rerun does not repeat a paid request.
    second = AppTest.from_file(str(ROOT / "streamlit_app.py")).run()
    assert not second.exception and second.session_state["turns"] == []


def test_streamlit_empty_selection_is_disabled_and_exact_scope_preserved(app_host):
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(str(ROOT / "streamlit_app.py")).run()
    app.radio[0].set_value("Choose sources").run()
    assert app.chat_input[0].disabled
    app.multiselect[0].set_value(["food"]).run()
    app.chat_input[0].set_value("How do I store food?").run()
    assert not app.exception
    assert app_host[-1]["scope"] == {"mode": "selected", "document_ids": ["food"]}


def test_streamlit_startup_failure_is_friendly(monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    from dietary_rag.ui import streamlit_app as ui

    monkeypatch.setattr(ui, "bootstrap_identity", lambda root: "test")
    monkeypatch.setattr(ui, "load_environment", lambda root: {})

    def failed(*args):
        raise ValueError("private-path-and-secret")

    monkeypatch.setattr(ui, "cached_host", failed)
    app = AppTest.from_file(str(ROOT / "streamlit_app.py")).run()
    assert not app.exception
    assert app.error and "private-path-and-secret" not in app.error[0].value
    assert not app.chat_input


def test_offline_and_online_hosts_use_shared_capacity():
    capacity = threading.BoundedSemaphore(1)
    service = SimpleNamespace(
        answer=lambda payload: ChatResponse(
            request_id="test", status="out_of_scope", message="Food questions only."
        )
    )
    first = ChatHost(service, None, None, 1, capacity=capacity)
    second = ChatHost(service, None, None, 1, capacity=capacity)
    assert first.capacity is second.capacity
    assert first.capacity.acquire(blocking=False)
    try:
        assert second.answer({}).code == "service_busy"
    finally:
        first.capacity.release()
    assert second.answer({}).status == "out_of_scope"


def test_archive_rejects_missing_members(tmp_path):
    archive_fixture(tmp_path, [("evidence.sqlite", tarfile.REGTYPE)])
    with pytest.raises(ValueError, match="incomplete"):
        extract_verified(tmp_path, tmp_path / "out")


def test_archive_rejects_member_checksum_mismatch(tmp_path):
    archive_fixture(tmp_path, [("evidence.sqlite", tarfile.REGTYPE)])
    receipt = tmp_path / "config/deployment-artifacts.json"
    values = json.loads(receipt.read_text())
    values["members"]["evidence.sqlite"]["sha256"] = "0" * 64
    receipt.write_text(json.dumps(values))
    with pytest.raises(ValueError, match="member checksum"):
        extract_verified(tmp_path, tmp_path / "out")
