#!/usr/bin/env sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
export ANONYMIZED_TELEMETRY=False
export TOKENIZERS_PARALLELISM=false
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-2}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-2}"
PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python}"
exec "$PYTHON_BIN" -m streamlit run streamlit_app.py --server.address "${STREAMLIT_SERVER_ADDRESS:-127.0.0.1}" --server.port "${PORT:-8501}"
