"""Replay one artifact-backed benchmark task and write its run record."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from evaluate_task import InputError, evaluate_files
    from validate_benchmark import validate_schema_instance
except ImportError:  # pragma: no cover - package-style import
    from scripts.evaluate_task import InputError, evaluate_files
    from scripts.validate_benchmark import validate_schema_instance


ROOT = Path(__file__).resolve().parents[1]
RUN_SCHEMA_PATH = ROOT / "schemas" / "task-run-record.schema.json"
VALID_HARNESSES = {"local-replay", "hermes-oneshot"}
VALID_CONDITIONS = {"known-good-control", "known-bad-control", "model-calibration"}
HARNESS_CONDITIONS = {
    "local-replay": {"known-good-control", "known-bad-control"},
    "hermes-oneshot": {"model-calibration"},
}


def _sha256_reference(path: Path) -> str:
    try:
        content = path.read_bytes()
    except (OSError, UnicodeError) as exc:
        raise InputError(f"unable to read {path.name} ({type(exc).__name__})") from exc
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _raw_output_reference(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return "external-candidate"


def _load_run_schema() -> dict[str, Any]:
    try:
        schema = json.loads(RUN_SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InputError(f"unable to read run-record schema ({type(exc).__name__})") from exc
    if not isinstance(schema, dict):
        raise InputError("run-record schema must be a JSON object")
    return schema


def replay(
    task_id: str,
    fixture_path: Path,
    prompt_path: Path,
    candidate_path: Path,
    condition: str,
    run_id: str,
    model_requested: str,
    model_resolved: str,
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
) -> dict[str, Any]:
    """Evaluate one candidate and atomically write an audit-ready record."""

    if harness not in VALID_HARNESSES:
        raise InputError(f"unsupported replay harness {harness!r}")
    if condition not in VALID_CONDITIONS:
        raise InputError(f"unsupported replay condition {condition!r}")
    if condition not in HARNESS_CONDITIONS[harness]:
        raise InputError(f"replay harness {harness!r} does not support {condition!r}")
    if not run_id or not run_id[0].isalnum() or any(
        char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-" for char in run_id
    ):
        raise InputError("run_id contains unsupported characters")

    record_started_at = started_at or _utc_timestamp()
    start = time.perf_counter()
    evaluation = evaluate_files(task_id, fixture_path, candidate_path, model_output=model_output)
    fixture_fingerprint = _sha256_reference(fixture_path)
    prompt_fingerprint = _sha256_reference(prompt_path)
    record_completed_at = completed_at or _utc_timestamp()
    record_latency_ms = (
        max(0, int((time.perf_counter() - start) * 1000))
        if latency_ms is None
        else latency_ms
    )
    human_review = evaluation.get("human_review", {})
    dimensions = human_review.get("dimensions", []) if isinstance(human_review, dict) else []
    record = {
        "run_id": run_id,
        "benchmark_id": "agent-profile-benchmark",
        "benchmark_version": "0.2.0",
        "task_id": task_id,
        "profile_id": task_id.split("-", 1)[0].lower(),
        "harness": harness,
        "model_requested": model_requested,
        "model_resolved": model_resolved,
        "condition": condition,
        "evaluator_version": str(evaluation.get("evaluator_version", "unknown")),
        "prompt_fingerprint": prompt_fingerprint,
        "fixture_fingerprint": fixture_fingerprint,
        "started_at": record_started_at,
        "completed_at": record_completed_at,
        "status": status_override or evaluation["status"],
        "raw_output_reference": _raw_output_reference(raw_output_path or candidate_path),
        "automatic_checks": evaluation["automatic_checks"],
        "hard_failures": evaluation["hard_failures"],
        "human_scores": {},
        "latency_ms": record_latency_ms,
        "usage": usage if usage is not None else {},
        "notes": notes
        or (
            f"Hermes one-shot model calibration for {task_id}; no tools were exposed and no external action was performed."
            if harness == "hermes-oneshot"
            else f"Deterministic {task_id} control replay; no model was called and no external action was performed."
        ),
    }
    if not isinstance(dimensions, list):
        raise InputError("evaluator human-review dimensions must be a list")
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
    parser.add_argument("--task", required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--prompt", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--harness", choices=sorted(VALID_HARNESSES), default="local-replay")
    parser.add_argument("--condition", choices=sorted(VALID_CONDITIONS), required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--model-requested", required=True)
    parser.add_argument("--model-resolved", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-output", action="store_true")
    args = parser.parse_args(argv)
    try:
        record = replay(
            args.task,
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
    except InputError as exc:
        print(f"replay failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"run_id": record["run_id"], "status": record["status"]}, sort_keys=True))
    return 0 if record["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
