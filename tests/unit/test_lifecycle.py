"""Publication mechanics with labeled synthetic validation, not retrieval-quality claims."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from dietary_rag.cli import main
from dietary_rag.corpus.identity import stable_id
from dietary_rag.corpus.lifecycle import CASE_FILES, LifecycleStore
from dietary_rag.retrieval.contracts import RetrievalConfig


@pytest.fixture
def publications(tmp_path):
    files = {}
    for name in CASE_FILES:
        path = tmp_path / name
        path.write_text('{"kind":"synthetic lifecycle fixture"}')
        files[name] = path
    publications = []
    for index, strategy in enumerate(("bm25", "hybrid", "dense")):
        directory = tmp_path / f"snapshot-{index}"
        directory.mkdir()
        manifest = {
            "snapshot_id": f"corpus_fixture_{index}",
            "embedding_configuration_id": "embedding_fixture",
        }
        (directory / "manifest.json").write_text(json.dumps(manifest))
        config = RetrievalConfig(strategy=strategy, resolution_configuration_id="resolver_fixture")
        artifact = {
            "corpus_snapshot_id": manifest["snapshot_id"],
            "retrieval_configuration_id": config.configuration_id,
            "configuration": config.model_dump(),
        }
        proof = {
            "selection_report_id": stable_id("test_selection", {"index": index}),
            "corpus": manifest["snapshot_id"],
            "config": config.configuration_id,
        }
        publications.append((directory, artifact, proof, files))
    return publications


def fixture_validation(directory, config, proof, cases):
    """Only test the publication boundary; production uses validate_activation."""
    assert set(p.name for p in cases.iterdir()) == {"selection.json", *CASE_FILES}
    assert proof["config"] == config.configuration_id
    manifest = json.loads((directory / "manifest.json").read_text())
    assert proof["corpus"] == manifest["snapshot_id"]
    return {"status": "test_only", **manifest}


def publish(store, publication, **kwargs):
    return store.activate(*publication, fixture_validation, **kwargs)


def test_activation_is_one_pair_idempotent_and_rolls_back_both_versions(tmp_path, publications):
    store = LifecycleStore(tmp_path / "active")
    assert store.read_state() is None
    first = publish(store, publications[0], expected_generation=0)
    pinned = store.pin()
    assert first.generation == 1 and first.previous_release_id is None
    assert publish(store, publications[0]) == first
    second = publish(store, publications[1], expected_generation=1)
    active = store.pin()
    assert second.generation == 2 and second.previous_release_id == first.active_release_id
    assert pinned.release.corpus_snapshot_id != active.release.corpus_snapshot_id
    assert pinned.release.retrieval_configuration_id != active.release.retrieval_configuration_id
    assert pinned.generation == 1  # Existing request remains on its original pair.
    # Changes to current evaluation inputs cannot affect retained rollback proof.
    publications[0][3]["freeze.json"].write_text("changed external receipt")
    rolled = store.rollback(
        lambda d, c, p, cases: {
            "status": "test_only",
            **json.loads((d / "manifest.json").read_text()),
        }
    )
    assert rolled.generation == 3 and rolled.active_release_id == first.active_release_id
    assert rolled.previous_release_id == second.active_release_id
    assert store.pin().release == pinned.release
    redone = store.rollback(
        lambda d, c, p, cases: {
            "status": "test_only",
            **json.loads((d / "manifest.json").read_text()),
        }
    )
    assert redone.active_release_id == second.active_release_id
    assert all(
        (store.releases / identifier).exists()
        for identifier in (first.active_release_id, second.active_release_id)
    )


def test_failed_validation_or_pointer_replace_preserves_active_pair(
    tmp_path, publications, monkeypatch
):
    store = LifecycleStore(tmp_path / "active")
    first = publish(store, publications[0])
    before = store.pointer.read_bytes()

    def reject(*args):
        raise ValueError("incompatible candidate")

    with pytest.raises(ValueError, match="incompatible"):
        store.activate(*publications[1], reject)
    assert store.pointer.read_bytes() == before
    assert not list(store.releases.glob(".release-*"))
    original = __import__("os").replace

    def fail_pointer(source, target):
        if target == store.pointer:
            raise OSError("injected publication failure")
        return original(source, target)

    monkeypatch.setattr("dietary_rag.corpus.lifecycle.os.replace", fail_pointer)
    with pytest.raises(OSError, match="publication failure"):
        publish(store, publications[1])
    assert store.read_state() == first and store.pointer.read_bytes() == before
    assert store.pin().release.corpus_snapshot_id == publications[0][1]["corpus_snapshot_id"]


def test_failed_rollback_preserves_current_pair(tmp_path, publications):
    store = LifecycleStore(tmp_path / "active")
    publish(store, publications[0])
    current = publish(store, publications[1])

    def reject(*args):
        raise ValueError("rollback incompatible")

    with pytest.raises(ValueError, match="rollback incompatible"):
        store.rollback(reject)
    assert store.read_state() == current
    with pytest.raises(ValueError, match="validation differs"):
        store.rollback(lambda *args: {"changed": True})
    assert store.read_state() == current


def test_no_previous_or_active_pair_is_explicit(tmp_path, publications):
    store = LifecycleStore(tmp_path / "active")
    with pytest.raises(ValueError, match="no active"):
        store.pin()
    with pytest.raises(ValueError, match="no previous"):
        store.rollback(fixture_validation)
    publish(store, publications[0])
    with pytest.raises(ValueError, match="no previous"):
        store.rollback(fixture_validation)


def test_rollback_recovers_damaged_current_proof_and_reports_bad_previous(
    tmp_path, publications, capsys
):
    store = LifecycleStore(tmp_path / "active")
    first = publish(store, publications[0])
    second = publish(store, publications[1])
    (store.releases / second.active_release_id / "selection.json").write_text("{}")
    with pytest.raises(ValueError, match="artifact changed"):
        publish(store, publications[2])
    assert store.read_state() == second
    restored = store.rollback(
        lambda d, c, p, cases: {
            "status": "test_only",
            **json.loads((d / "manifest.json").read_text()),
        }
    )
    assert restored.active_release_id == first.active_release_id
    assert store.pin().release.release_id == first.active_release_id
    assert main(["corpus-status", "--lifecycle-dir", str(store.directory)]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["active"] and status["previous_release"] is None
    assert "previous_release_error" in status


def test_concurrent_publishers_use_generation_compare_and_swap(tmp_path, publications):
    store = LifecycleStore(tmp_path / "active")
    publish(store, publications[0])
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(publish, store, p, expected_generation=1) for p in publications[1:]
        ]
        successes, rejected = [], []
        for future in futures:
            try:
                successes.append(future.result(timeout=10))
            except ValueError as exc:
                rejected.append(str(exc))
    assert len(successes) == len(rejected) == 1
    assert "generation changed" in rejected[0]
    assert store.read_state().generation == 2


def test_readers_see_old_pair_while_new_pair_is_validating(tmp_path, publications):
    store = LifecycleStore(tmp_path / "active")
    first = publish(store, publications[0])
    entered, finish = threading.Event(), threading.Event()

    def delayed(*args):
        entered.set()
        assert finish.wait(timeout=5)
        return fixture_validation(*args)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(store.activate, *publications[1], delayed)
        assert entered.wait(timeout=5)
        for _ in range(10):
            assert store.read_state() == first
            assert (
                store.pin().release.corpus_snapshot_id == publications[0][1]["corpus_snapshot_id"]
            )
        finish.set()
        second = future.result(timeout=5)
    assert store.read_state() == second


@pytest.mark.parametrize(
    "artifact", ["release.json", "selection.json", "freeze.json", "manifest.json", "active.json"]
)
def test_corrupt_pinned_artifacts_fail_without_fallback(tmp_path, publications, artifact):
    store = LifecycleStore(tmp_path / "active")
    first = publish(store, publications[0])
    if artifact == "manifest.json":
        path = publications[0][0] / artifact
    elif artifact == "active.json":
        path = store.pointer
    else:
        path = store.releases / first.active_release_id / artifact
    path.write_text("{}")
    with pytest.raises((ValueError, KeyError)):
        store.pin()


def test_pin_reads_pointer_once_and_rejects_stale_writers(tmp_path, publications, monkeypatch):
    store = LifecycleStore(tmp_path / "active")
    publish(store, publications[0])
    original = store.read_state
    calls = []

    def read():
        calls.append(True)
        return original()

    monkeypatch.setattr(store, "read_state", read)
    pinned = store.pin()
    assert len(calls) == 1
    with pytest.raises(ValueError, match="generation changed"):
        publish(store, publications[1], expected_generation=0)
    assert pinned.release == store.pin().release
    with pytest.raises(ValueError, match="invalid release identifier"):
        store.read_release("../other")


def test_active_cli_cannot_override_pair(tmp_path, capsys):
    assert main(["search-corpus", "food", "--active", "--strategy", "bm25"]) == 2
    assert "cannot override" in capsys.readouterr().out
    assert (
        main(["search-corpus", "food", "--active", "--lifecycle-dir", str(tmp_path / "absent")])
        == 2
    )
    assert "no active" in capsys.readouterr().out
    assert main(["corpus-status", "--lifecycle-dir", str(tmp_path / "absent")]) == 0
    assert not (tmp_path / "absent").exists()
