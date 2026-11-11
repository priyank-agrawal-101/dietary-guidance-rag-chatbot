import os
import subprocess
import sys
from pathlib import Path


def test_all_package_modules_import_without_network_files_or_heavy_models(tmp_path):
    script = """
import socket
import urllib.request
import pathlib
import pkgutil
import importlib
import sys
def forbidden(*args, **kwargs):
    raise AssertionError("import attempted network or application file I/O")
original_read_text = pathlib.Path.read_text
def guarded_read_text(path, *args, **kwargs):
    # Pydantic legitimately discovers installed plugins through distribution metadata.
    if any(part.endswith((".dist-info", ".egg-info")) for part in path.parts):
        return original_read_text(path, *args, **kwargs)
    return forbidden()
socket.socket = forbidden
urllib.request.urlopen = forbidden
pathlib.Path.read_text = guarded_read_text
pathlib.Path.mkdir = forbidden
import dietary_rag
for module in pkgutil.walk_packages(dietary_rag.__path__, dietary_rag.__name__ + "."):
    importlib.import_module(module.name)
for name in ("torch", "transformers", "sentence_transformers", "chromadb", "pytesseract", "httpx", "httpcore", "pypdf"):
    assert name not in sys.modules, name
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=os.environ | {"PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []


def test_existing_registry_baseline():
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, str(root / "scripts/validate_source_registry.py")],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "20 existing IDs retained" in result.stdout
