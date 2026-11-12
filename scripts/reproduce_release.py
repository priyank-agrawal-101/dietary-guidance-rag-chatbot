"""Build a wheel, install hash locks in an isolated venv, and run the full suite.

Supported release target: macOS ARM64 CPython 3.13. Network only installs pinned
packages; corpus/tokenizer/model caches remain explicit local prerequisites.
"""

import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from hashlib import sha256
from pathlib import Path

from dietary_rag.operations.release import implementation_hash

LOCKS = ("core", "dev", "acquisition", "html", "pdf", "ocr", "chunking", "embeddings", "api")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("docs/reproduction-review.json"))
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument(
        "--wheelhouse", type=Path, help="install offline using predownloaded hash-locked wheels"
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    work = args.work_dir or Path(tempfile.mkdtemp(prefix="dietary-release-"))
    work.mkdir(parents=True, exist_ok=True)
    report = {
        "version": "clean_reproduction_v1",
        "isolated_environment": True,
        "implementation_sha256": implementation_hash(root),
        "status": "failed",
        "tests_passed": False,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "work_directory": str(work),
        "checks": [],
        "lock_hashes": {
            name: sha256((root / f"requirements/{name}.lock").read_bytes()).hexdigest()
            for name in LOCKS
        },
    }
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("PYTHON", "DIETARY_RAG_", "GROQ_"))
        and k not in ("VIRTUAL_ENV", "PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL")
    }
    env.update(
        PIP_CONFIG_FILE=os.devnull,
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
        ANONYMIZED_TELEMETRY="False",
    )

    def run(name, command, cwd=root):
        started = time.monotonic()
        result = subprocess.run(
            command, cwd=cwd, env=env, capture_output=True, text=True, timeout=900
        )
        (work / f"{name}.log").write_text(result.stdout + result.stderr)
        report["checks"].append(
            {"check": name, "passed": result.returncode == 0, "seconds": time.monotonic() - started}
        )
        print(name, result.returncode, flush=True)
        if result.returncode:
            raise RuntimeError(f"{name} failed; see {work / (name + '.log')}")
        return result.stdout

    try:
        if (
            platform.system() != "Darwin"
            or platform.machine() != "arm64"
            or sys.version_info[:2] != (3, 13)
        ):
            raise ValueError("embedding wheel lock supports macOS ARM64 CPython 3.13 only")
        run(
            "wheel_build",
            [
                sys.executable,
                "-m",
                "build",
                "--wheel",
                "--no-isolation",
                "--outdir",
                str(work / "dist"),
            ],
        )
        wheel = next((work / "dist").glob("*.whl"))
        report["wheel_sha256"] = sha256(wheel.read_bytes()).hexdigest()
        run("venv", [sys.executable, "-m", "venv", str(work / "venv")])
        python = str(work / "venv/bin/python")
        install = [
            python,
            "-m",
            "pip",
            "--isolated",
            "--disable-pip-version-check",
            "install",
            "--require-hashes",
            "--only-binary=:all:",
            "--retries",
            "1",
            "--timeout",
            "20",
            "--cache-dir",
            str(work / "pip-cache"),
        ]
        if args.wheelhouse:
            install += ["--no-index", "--find-links", str(args.wheelhouse.resolve())]
        else:
            install += ["--index-url", "https://pypi.org/simple"]
        for name in LOCKS:
            install += ["-r", str(root / f"requirements/{name}.lock")]
        run("locked_install", install)
        run(
            "wheel_install",
            [
                python,
                "-m",
                "pip",
                "--isolated",
                "install",
                "--no-deps",
                "--force-reinstall",
                str(wheel),
            ],
            cwd=work,
        )
        run("dependency_check", [python, "-m", "pip", "check"], cwd=work)
        run(
            "settings",
            [python, "-m", "dietary_rag", "check-settings", "--project-root", str(root)],
            cwd=work,
        )
        run(
            "registry",
            [python, "-m", "dietary_rag", "check-registry", "--project-root", str(root)],
            cwd=work,
        )
        run(
            "installed_import",
            [
                python,
                "-c",
                "import dietary_rag; from pathlib import Path; p=Path(dietary_rag.__file__).resolve(); assert 'site-packages' in p.parts; print(p)",
            ],
            cwd=work,
        )
        output = run("tests", [python, "-m", "pytest", str(root / "tests"), "-q"])
        # A complete release reproduction cannot silently skip local model/OCR gates.
        report["tests_passed"] = "passed" in output and "skipped" not in output
        if not report["tests_passed"]:
            raise RuntimeError(
                "full suite has skips; install reviewed model/tokenizer/Tesseract prerequisites"
            )
        smoke = """
import json
from pathlib import Path
from fastapi.testclient import TestClient
from dietary_rag.api.app import create_app
root = Path.cwd()
with TestClient(create_app(project_root=root, environ={}, offline=True)) as client:
    assert client.get('/health/live').status_code == 200
    ready = client.get('/health/ready')
    assert ready.status_code == 200, ready.text
    assert client.get('/documents').status_code == 200
    for question, document in [('What internal temperature should chicken reach?', 'cdc_food_safety'), ('How many days can fresh beef be refrigerated and how many months frozen?', 'mn_storage_chart')]:
        response = client.post('/chat', json={'question': question, 'scope': {'mode': 'selected', 'document_ids': [document]}}).json()
        assert response['status'] == 'answered', response
        assert response['citations']
    refusal = client.post('/chat', json={'question': 'What diet treats diabetes?'}).json()
    assert refusal['status'] == 'out_of_scope'
    assert client.get('/metrics').status_code == 200
    print(json.dumps({'status': 'passed', 'readiness': ready.json(), 'offline': True}))
"""
        run("active_api", [python, "-c", smoke])
        report["status"] = "passed"
    except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as exc:
        report["error"] = str(exc)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
