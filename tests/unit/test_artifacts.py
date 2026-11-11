from concurrent.futures import ThreadPoolExecutor

import pytest

from dietary_rag.corpus.artifacts import ArtifactIntegrityError, ArtifactStore


def test_store_keeps_exact_original_bytes_and_reuses_same_artifact(tmp_path):
    store = ArtifactStore(tmp_path / "raw")
    assert not store.root.exists()
    content = b"\x00\xffOriginal\r\nbytes  with   spacing\x00"
    artifact = store.put_bytes(content)
    path = store.root / artifact.relative_path
    previous_stat = path.stat()
    assert store.read_bytes(artifact.sha256) == content
    assert store.put_bytes(content) == artifact
    assert path.stat().st_mtime_ns == previous_stat.st_mtime_ns
    assert not list(store.root.rglob(".artifact-*"))
    assert len(list(store.root.rglob("*.blob"))) == 1


def test_concurrent_duplicate_writers_publish_one_complete_artifact(tmp_path):
    store = ArtifactStore(tmp_path / "raw")
    content = b"complete original bytes" * 100
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(store.put_bytes, [content] * 8))
    assert all(result == results[0] for result in results)
    assert store.read_bytes(results[0].sha256) == content
    assert len(list(store.root.rglob("*.blob"))) == 1
    assert not list(store.root.rglob(".artifact-*"))


def test_external_corruption_is_detected_and_never_replaced(tmp_path):
    store = ArtifactStore(tmp_path / "raw")
    artifact = store.put_bytes(b"original")
    path = store.root / artifact.relative_path
    path.write_bytes(b"corrupted")
    for operation in [
        lambda: store.read_bytes(artifact.sha256),
        lambda: store.put_bytes(b"original"),
    ]:
        with pytest.raises(ArtifactIntegrityError):
            operation()
    assert path.read_bytes() == b"corrupted"


@pytest.mark.parametrize("digest", ["../outside", "A" * 64, "short"])
def test_untrusted_artifact_paths_are_rejected(tmp_path, digest):
    store = ArtifactStore(tmp_path / "raw")
    with pytest.raises(ValueError):
        store.read_bytes(digest)
    assert not store.root.exists()


def test_symlink_artifact_escape_is_rejected(tmp_path):
    store = ArtifactStore(tmp_path / "raw")
    artifact = store.put_bytes(b"original")
    path = store.root / artifact.relative_path
    outside = tmp_path / "outside"
    outside.write_bytes(b"original")
    path.unlink()
    path.symlink_to(outside)
    with pytest.raises(ArtifactIntegrityError):
        store.read_bytes(artifact.sha256)


def test_empty_artifact_is_not_valid_original_content(tmp_path):
    with pytest.raises(ValueError):
        ArtifactStore(tmp_path / "raw").put_bytes(b"")
