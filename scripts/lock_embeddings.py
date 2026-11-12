"""Resolve platform-specific embedding wheels and pin their supplied SHA-256 hashes."""

import argparse
import json
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--resolution-report", type=Path, help="reuse an explicit pip dry-run report offline"
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = root / "requirements/embeddings.lock"
    constraints = {}
    for lock in (root / "requirements").glob("*.lock"):
        if lock == output:
            continue
        for name, value in re.findall(r"^([a-zA-Z0-9_.-]+)==([^\s\\]+)", lock.read_text(), re.M):
            name = name.lower().replace("_", "-")
            if name in constraints and constraints[name] != value:
                raise ValueError(f"shared lock conflict for {name}")
            constraints[name] = value
    if args.resolution_report:
        report = json.loads(args.resolution_report.read_text())
    else:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            constraint_file = directory / "constraints.txt"
            constraint_file.write_text(
                "\n".join(f"{k}=={v}" for k, v in sorted(constraints.items())) + "\n"
            )
            report_file = directory / "resolution.json"
            dependencies = tomllib.loads((root / "pyproject.toml").read_text())["project"][
                "optional-dependencies"
            ]["embeddings"]
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--dry-run",
                    "--ignore-installed",
                    "--only-binary=:all:",
                    "--quiet",
                    "--constraint",
                    str(constraint_file),
                    "--report",
                    str(report_file),
                    *dependencies,
                ],
                check=True,
            )
            report = json.loads(report_file.read_text())
    environment = report["environment"]
    lines = [
        "# Platform-specific wheel lock; regenerate with scripts/lock_embeddings.py.",
        f"# Resolved for {environment['platform_system']} {environment['platform_machine']}, Python {environment['python_version']}.",
        "# Other Python/platform combinations require a separately resolved and tested lock.",
        "--only-binary=:all:",
        "",
    ]
    packages = {}
    for item in report["install"]:
        name = item["metadata"]["name"].lower().replace("_", "-")
        value = item["metadata"]["version"]
        digest = item["download_info"]["archive_info"]["hashes"]["sha256"]
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("resolution report lacks a wheel SHA-256")
        if name in constraints and constraints[name] != value:
            raise ValueError(f"resolution changed shared locked dependency {name}")
        packages[name] = f"{name}=={value} \\\n    --hash=sha256:{digest}"
    lines.extend(packages[name] for name in sorted(packages))
    output.write_text("\n".join(lines) + "\n")
    print(f"Wrote {len(packages)} platform-specific hashed requirements to {output}")


if __name__ == "__main__":
    main()
