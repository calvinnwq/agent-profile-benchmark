"""Replay KODY-01 controls and write an audit-ready run record."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evaluate_kody01 import (
    EVALUATOR_VERSION,
    DuplicateJSONKeyError,
    InputError,
    _reject_duplicate_json_keys,
    evaluate_files,
)
from validate_benchmark import validate_schema_instance


ROOT = Path(__file__).resolve().parents[1]
RUN_SCHEMA_PATH = ROOT / "schemas" / "kody-01-run-record.schema.json"


def _sha256_reference(path: Path) -> str:
    try:
        content = path.read_bytes()
    except (OSError, UnicodeError) as exc:
        raise InputError(f"unable to read {path.name} ({type(exc).__name__})") from exc
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _raw_output_reference(candidate_path: Path) -> str:
    try:
        return candidate_path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return "external-candidate"


def _load_run_schema() -> dict[str, Any]:
    try:
        schema = json.loads(
            RUN_SCHEMA_PATH.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except DuplicateJSONKeyError as exc:
        raise InputError(f"unable to read run-record schema ({exc})") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InputError(f"unable to read run-record schema ({type(exc).__name__})") from exc
    if not isinstance(schema, dict):
        raise InputError("run-record schema must be a JSON object")
    return schema


def replay(
    fixture_path: Path,
    prompt_path: Path,
    candidate_path: Path,
    condition: str,
    run_id: str,
    model_requested: str,
    model_resolved: str,
    output_path: Path,
) -> dict[str, Any]:
    """Evaluate one candidate and atomically write its run record."""

    started_at = _utc_timestamp()
    start = time.perf_counter()
    evaluation = evaluate_files(fixture_path, candidate_path)
    fixture_fingerprint = _sha256_reference(fixture_path)
    prompt_fingerprint = _sha256_reference(prompt_path)
    completed_at = _utc_timestamp()
    latency_ms = max(0, int((time.perf_counter() - start) * 1000))
    record = {
        "run_id": run_id,
        "benchmark_id": "agent-profile-benchmark",
        "benchmark_version": "0.1.0",
        "task_id": "KODY-01",
        "profile_id": "kody",
        "harness": "local-replay",
        "model_requested": model_requested,
        "model_resolved": model_resolved,
        "condition": condition,
        "evaluator_version": EVALUATOR_VERSION,
        "prompt_fingerprint": prompt_fingerprint,
        "fixture_fingerprint": fixture_fingerprint,
        "started_at": started_at,
        "completed_at": completed_at,
        "status": evaluation["status"],
        "raw_output_reference": _raw_output_reference(candidate_path),
        "automatic_checks": evaluation["automatic_checks"],
        "hard_failures": evaluation["hard_failures"],
        "human_scores": {},
        "latency_ms": latency_ms,
        "usage": {},
        "notes": "Deterministic control replay; no model was called and no external action was performed.",
    }
    schema_errors = validate_schema_instance(record, _load_run_schema())
    if schema_errors:
        raise InputError("generated run record failed schema validation: " + "; ".join(schema_errors))
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = output_path.with_name(output_path.name + ".tmp")
        temporary_path.write_text(
            json.dumps(record, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(output_path)
    except OSError as exc:
        raise InputError(f"unable to write run record ({type(exc).__name__})") from exc
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True, help="path to the KODY-01 fixture")
    parser.add_argument("--prompt", type=Path, required=True, help="path to the exact prompt packet")
    parser.add_argument("--candidate", type=Path, required=True, help="path to a candidate JSON output")
    parser.add_argument(
        "--condition",
        choices=("known-good-control", "known-bad-control"),
        required=True,
        help="control condition represented by this replay",
    )
    parser.add_argument("--run-id", required=True, help="stable identifier for this replay record")
    parser.add_argument("--model-requested", required=True, help="requested model or control identity")
    parser.add_argument("--model-resolved", required=True, help="resolved model or control identity")
    parser.add_argument("--output", type=Path, required=True, help="run-record JSON output path")
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
        )
    except InputError as exc:
        print(f"replay failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"run_id": record["run_id"], "status": record["status"]}, sort_keys=True))
    return 0 if record["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
