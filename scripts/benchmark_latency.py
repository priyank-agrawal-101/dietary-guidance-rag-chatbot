"""Measure local grounded-answer overhead without Groq calls or user question caching."""

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from dietary_rag.api.runtime import build_answer_service  # noqa: E402
from dietary_rag.corpus.registry import load_registry  # noqa: E402
from dietary_rag.deployment.bootstrap import ensure_runtime  # noqa: E402
from dietary_rag.deployment.service import ChatHost  # noqa: E402
from dietary_rag.settings import load_settings  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=ROOT / ".streamlit-runtime")
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--output", type=Path, default=ROOT / ".streamlit-runtime/latency.json")
    args = parser.parse_args()
    if args.samples < 2:
        parser.error("at least two samples are required")
    settings = load_settings(ROOT / "config/settings.yaml", project_root=ROOT, environ={})
    registry = load_registry(settings.source_registry)
    start = time.perf_counter()
    active = ensure_runtime(ROOT, settings, registry, args.cache_dir)
    startup_seconds = time.perf_counter() - start
    service, ledger = build_answer_service(
        settings.model_copy(update={"data_dir": args.cache_dir / "benchmark-accounting"}),
        active,
        offline=True,
    )
    host = ChatHost(service, active, ledger, settings.api.max_in_flight)
    samples = []
    for _ in range(args.samples):
        start = time.perf_counter()
        response = host.answer(
            {"question": "How long can hard-cooked eggs be kept in the refrigerator?"}
        )
        if getattr(response, "status", None) != "answered":
            raise RuntimeError("benchmark question did not produce a validated answer")
        samples.append(time.perf_counter() - start)
    report = {
        "version": "grounded_latency_v1",
        "startup_seconds": startup_seconds,
        "answer_seconds": samples,
        "median_warm_answer_seconds": statistics.median(samples[1:]),
        "metrics": host.metrics.snapshot(),
        "corpus_version": active.store.pin().release.corpus_snapshot_id,
        "scope": "Local source-excerpt path; no Groq latency, cloud timing or network download timing claimed.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
