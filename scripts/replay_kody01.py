"""Compatibility CLI for replaying KODY-01 through the generic runner."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    from replay_task import InputError, replay as _replay
except ImportError:  # pragma: no cover - exercised when imported as scripts.replay_kody01
    from scripts.replay_task import InputError, replay as _replay


def replay(
    fixture_path: Path,
    prompt_path: Path,
    candidate_path: Path,
    condition: str,
    run_id: str,
    model_requested: str,
    model_resolved: str | None,
    output_path: Path,
    *,
    harness: str = "local-replay",
    raw_output_path: Path | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
    latency_ms: int | None = None,
    usage: dict[str, Any] | None = None,
    notes: str | None = None,
    model_output: bool = False,
    status_override: str | None = None,
    provider_requested: str = "none",
    provider_resolved: str | None = "none",
    execution_status: str = "completed",
    failure_class: str | None = None,
) -> dict[str, Any]:
    """Preserve the historical function signature while using one runner."""
    return _replay(
        "KODY-01",
        fixture_path,
        prompt_path,
        candidate_path,
        condition,
        run_id,
        model_requested,
        model_resolved,
        output_path,
        harness=harness,
        raw_output_path=raw_output_path,
        started_at=started_at,
        completed_at=completed_at,
        latency_ms=latency_ms,
        usage=usage,
        notes=notes,
        model_output=model_output,
        status_override=status_override,
        provider_requested=provider_requested,
        provider_resolved=provider_resolved,
        execution_status=execution_status,
        failure_class=failure_class,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True, help="path to the KODY-01 fixture")
    parser.add_argument("--prompt", type=Path, required=True, help="path to the exact prompt packet")
    parser.add_argument("--candidate", type=Path, required=True, help="path to a candidate JSON output")
    parser.add_argument("--harness", choices=["local-replay", "hermes-oneshot"], default="local-replay")
    parser.add_argument("--condition", choices=["known-good-control", "known-bad-control", "model-calibration"], required=True)
    parser.add_argument("--run-id", required=True, help="stable identifier for this replay record")
    parser.add_argument("--model-requested", required=True, help="requested model or control identity")
    parser.add_argument("--model-resolved", required=True, help="resolved model or control identity")
    parser.add_argument("--output", type=Path, required=True, help="run-record JSON output path")
    parser.add_argument("--model-output", action="store_true")
    args = parser.parse_args(argv)
    try:
        record = replay(
            args.fixture,
            args.prompt,
            args.candidate,
            args.condition,
            args.run_id,
            args.model_requested,
            args.model_resolved,
            args.output,
            harness=args.harness,
            model_output=args.model_output,
        )
    except (ValueError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"replay failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"run_id": record["run_id"], "status": record["status"]}, sort_keys=True))
    return 0 if record["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
