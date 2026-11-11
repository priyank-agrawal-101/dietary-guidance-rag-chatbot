"""Phase 0 administrative configuration check; later commands are not exposed yet."""

import argparse
import json
import os
from pathlib import Path

from pydantic import ValidationError

from dietary_rag.settings import load_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dietary-rag")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check-settings", help="validate and display nonsecret settings")
    check.add_argument("--config", type=Path, default=Path("config/settings.yaml"))
    check.add_argument("--project-root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    try:
        settings = load_settings(args.config, project_root=args.project_root, environ=os.environ)
    except ValidationError as exc:
        for error in exc.errors(include_input=False, include_url=False):
            print(f"Invalid setting {'.'.join(map(str, error['loc']))}: {error['msg']}")
        return 2
    except (OSError, ValueError) as exc:
        print(f"Cannot load settings: {exc}")
        return 2
    print(json.dumps(settings.model_dump(mode="json"), indent=2))
    return 0
