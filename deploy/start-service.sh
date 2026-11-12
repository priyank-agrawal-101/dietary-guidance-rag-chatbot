#!/bin/sh
# One foreground process. A supervisor may restart this command; no workers/reload.
set -eu
DEPLOY_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
DEPLOY_PYTHON=${DIETARY_SERVICE_PYTHON:-"$DEPLOY_ROOT/.venv/bin/python"}
cd "$DEPLOY_ROOT"
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export ANONYMIZED_TELEMETRY=False
exec "$DEPLOY_PYTHON" -m dietary_rag serve --project-root "$DEPLOY_ROOT" "$@"
