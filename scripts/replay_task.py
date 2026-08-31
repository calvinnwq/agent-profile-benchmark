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
    from release_lock import (
        ReleaseLockError,
        expected_manifest_paths,
        expected_release_artifact_paths,
        verify_task_release_artifacts,
    )
    from validate_benchmark import (
        EXPECTED_TASK_IDS,
        _reject_duplicate_json_keys,
        validate_schema_instance,
    )
except ImportError:  # pragma: no cover - package-style import
    from scripts.evaluate_task import InputError, evaluate_files
    from scripts.release_lock import (
        ReleaseLockError,
        expected_manifest_paths,
        expected_release_artifact_paths,
        verify_task_release_artifacts,
    )
    from scripts.validate_benchmark import (
        EXPECTED_TASK_IDS,
        _reject_duplicate_json_keys,
        validate_schema_instance,
    )


ROOT = Path(__file__).resolve().parents[1]
RUN_SCHEMA_PATH = ROOT / "schemas" / "task-run-record.schema.json"
INPUT_COMPOSITION_VERSION = "prompt-plus-fixture-v1"
INPUT_SEPARATOR = b"\n\nSupplied synthetic request packet (JSON):\n"
VALID_HARNESSES = {"local-replay", "hermes-oneshot"}
VALID_CONDITIONS = {"known-good-control", "known-bad-control", "model-calibration"}
VALID_EXECUTION_STATUSES = {"completed", "failed", "blocked", "timed_out"}
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


def _sha256_reference_from_bytes(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _reject_nonfinite_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number {value!r} is not supported")


def _load_json(path: Path, label: str) -> Any:
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_nonfinite_json_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise InputError(f"unable to read {label} ({type(exc).__name__})") from exc


def _resolve_manifest_path(package: Path, relative_path: Any, label: str) -> Path:
    if not isinstance(relative_path, str) or not relative_path or Path(relative_path).is_absolute():
        raise InputError(f"{label} must be a relative path")
    resolved = (package / relative_path).resolve()
    try:
        resolved.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise InputError(f"{label} escapes the repository root") from exc
    if not resolved.is_file():
        raise InputError(f"{label} does not name a file")
    return resolved


def compose_model_input(prompt_path: Path, fixture_path: Path) -> bytes:
    """Return the exact UTF-8 bytes sent to a model calibration command."""
    try:
        prompt = prompt_path.read_bytes()
        fixture = fixture_path.read_bytes()
        prompt.decode("utf-8")
        fixture.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise InputError(f"unable to read model input ({type(exc).__name__})") from exc
    return prompt + INPUT_SEPARATOR + fixture


def _load_task_binding(
    task_id: str,
    fixture_path: Path,
    prompt_path: Path,
    *,
    harness: str = "hermes-oneshot",
) -> dict[str, Any]:
    """Resolve and verify the frozen manifest before any model process starts."""
    if not isinstance(task_id, str) or not task_id:
        raise InputError("task ID must be a non-empty string")
    if task_id not in EXPECTED_TASK_IDS:
        raise InputError(f"unknown benchmark task ID {task_id!r}")
    package = ROOT / "fixtures" / task_id.lower()
    manifest_path = package / "manifest.json"
    manifest = _load_json(manifest_path, f"{task_id} manifest")
    if not isinstance(manifest, dict) or manifest.get("task_id") != task_id:
        raise InputError("task manifest identity is not bound to the requested task")
    if (
        manifest.get("benchmark_version") != "0.2.0"
        or manifest.get("status") != "benchmark-ready"
        or manifest.get("benchmark_ready") is not True
    ):
        raise InputError("task manifest is not benchmark-ready")

    fixture_meta = manifest.get("fixture")
    prompt_meta = manifest.get("prompt")
    if not isinstance(fixture_meta, dict) or not isinstance(prompt_meta, dict):
        raise InputError("task manifest has incomplete frozen input metadata")
    def _metadata_path(name: str) -> Any:
        metadata = manifest.get(name)
        return metadata.get("path") if isinstance(metadata, dict) else None

    expected_paths = expected_manifest_paths(task_id)
    actual_paths = {
        "fixture": fixture_meta.get("path"),
        "prompt": prompt_meta.get("path"),
        "oracle": _metadata_path("oracle"),
        "output_schema": _metadata_path("output_schema"),
        "run_record_schema": _metadata_path("run_record_schema"),
        "evaluator": _metadata_path("evaluator"),
        "release_gate": manifest.get("release_gate"),
    }
    for artifact, expected_path in expected_paths.items():
        if actual_paths.get(artifact) != expected_path:
            raise InputError(
                f"task manifest {artifact} path must be the canonical {expected_path!r}"
            )
    expected_profile = task_id.split("-", 1)[0].lower()
    if manifest.get("profile_id") != expected_profile:
        raise InputError("task manifest profile is not bound to the requested task")
    try:
        lock_binding = verify_task_release_artifacts(task_id, harness)
    except ReleaseLockError as exc:
        raise InputError(str(exc)) from exc
    expected_fixture = _resolve_manifest_path(package, fixture_meta.get("path"), f"{task_id} fixture path")
    expected_prompt = _resolve_manifest_path(package, prompt_meta.get("path"), f"{task_id} prompt path")
    if fixture_path.resolve() != expected_fixture or prompt_path.resolve() != expected_prompt:
        raise InputError("provided input path does not match the frozen task package")
    if fixture_meta.get("status") != "frozen" or prompt_meta.get("status") != "frozen":
        raise InputError("task inputs are not frozen")
    if fixture_meta.get("sha256") != _sha256_reference(expected_fixture):
        raise InputError("frozen fixture fingerprint does not match its manifest")
    if prompt_meta.get("sha256") != _sha256_reference(expected_prompt):
        raise InputError("frozen prompt fingerprint does not match its manifest")
    if "allowed_tools" not in manifest or manifest["allowed_tools"] != []:
        raise InputError("task manifest does not declare an empty tool surface")
    fixture = _load_json(expected_fixture, f"{task_id} fixture")
    if (
        not isinstance(fixture, dict)
        or fixture.get("fixture_id") != fixture_meta.get("id")
        or fixture.get("fixture_version") != fixture_meta.get("version")
        or fixture.get("fixture_id") != lock_binding["fixture_id"]
        or fixture.get("fixture_version") != lock_binding["fixture_version"]
    ):
        raise InputError("fixture identity does not match its manifest")

    output_schema = manifest.get("output_schema")
    evaluator = manifest.get("evaluator")
    run_schema = manifest.get("run_record_schema")
    if not isinstance(output_schema, dict) or not isinstance(evaluator, dict) or not isinstance(run_schema, dict):
        raise InputError("task manifest has incomplete evaluator metadata")
    output_schema_path = _resolve_manifest_path(package, output_schema.get("path"), f"{task_id} output schema path")
    evaluator_path = _resolve_manifest_path(package, evaluator.get("path"), f"{task_id} evaluator path")
    run_schema_path = _resolve_manifest_path(package, run_schema.get("path"), f"{task_id} run-record schema path")
    if run_schema_path != RUN_SCHEMA_PATH:
        raise InputError("task manifest does not use the shared run-record schema")
    for metadata, path, label in (
        (evaluator, evaluator_path, f"{task_id} evaluator"),
        (output_schema, output_schema_path, f"{task_id} output schema"),
        (run_schema, run_schema_path, f"{task_id} run-record schema"),
    ):
        if metadata.get("sha256") != _sha256_reference(path):
            raise InputError(f"{label} fingerprint does not match its manifest")
    if not isinstance(evaluator.get("version"), str) or not evaluator["version"].strip():
        raise InputError("task manifest evaluator version is missing")
    return {
        **lock_binding,
        "profile_id": manifest.get("profile_id"),
        "evaluator_version": evaluator.get("version"),
    }


def validate_task_inputs(task_id: str, fixture_path: Path, prompt_path: Path) -> dict[str, Any]:
    """Public preflight used by model runners before creating evidence."""
    return _load_task_binding(task_id, fixture_path, prompt_path, harness="hermes-oneshot")


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _raw_output_reference(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return "external-candidate"


def _load_run_schema() -> dict[str, Any]:
    try:
        schema = json.loads(
            RUN_SCHEMA_PATH.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_nonfinite_json_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
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
    """Evaluate one candidate and atomically write an audit-ready record."""

    if harness not in VALID_HARNESSES:
        raise InputError(f"unsupported replay harness {harness!r}")
    if condition not in VALID_CONDITIONS:
        raise InputError(f"unsupported replay condition {condition!r}")
    if condition not in HARNESS_CONDITIONS[harness]:
        raise InputError(f"replay harness {harness!r} does not support {condition!r}")
    if status_override is not None and status_override not in {"passed", "failed", "blocked"}:
        raise InputError(f"unsupported status override {status_override!r}")
    if execution_status not in VALID_EXECUTION_STATUSES:
        raise InputError(f"unsupported execution status {execution_status!r}")
    if not isinstance(provider_requested, str) or not provider_requested.strip():
        raise InputError("provider_requested must be non-empty")
    if not run_id or not run_id[0].isalnum() or any(
        char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-" for char in run_id
    ):
        raise InputError("run_id contains unsupported characters")
    if output_path.exists() or output_path.is_symlink():
        raise InputError(f"run record output already exists for run ID {run_id!r}")

    binding = _load_task_binding(task_id, fixture_path, prompt_path, harness=harness)
    if harness == "local-replay":
        expected_control_key = {
            "known-good-control": "known_good",
            "known-bad-control": "known_bad",
        }[condition]
        expected_control = ROOT / expected_release_artifact_paths(task_id)[expected_control_key]
        if candidate_path.resolve() != expected_control.resolve():
            raise InputError("control candidate path does not match its declared replay condition")
    input_fingerprint = _sha256_reference_from_bytes(compose_model_input(prompt_path, fixture_path))
    raw_path = raw_output_path or candidate_path
    record_started_at = started_at or _utc_timestamp()
    start = time.perf_counter()
    evaluation = evaluate_files(
        task_id,
        fixture_path,
        candidate_path,
        model_output=model_output or harness == "hermes-oneshot",
    )
    if evaluation.get("evaluator_version") != binding["evaluator_version"]:
        raise InputError("evaluator output version does not match the task manifest")
    fixture_fingerprint = _sha256_reference(fixture_path)
    prompt_fingerprint = _sha256_reference(prompt_path)
    raw_output_fingerprint = _sha256_reference(raw_path)
    candidate_fingerprint = _sha256_reference(candidate_path)
    if raw_path.resolve() != candidate_path.resolve() and raw_output_fingerprint != candidate_fingerprint:
        raise InputError("raw output bytes do not match the evaluated candidate")
    record_completed_at = completed_at or _utc_timestamp()
    record_latency_ms = (
        max(0, int((time.perf_counter() - start) * 1000))
        if latency_ms is None
        else latency_ms
    )
    human_review = evaluation.get("human_review", {})
    dimensions = human_review.get("dimensions", []) if isinstance(human_review, dict) else []
    resolution_status = (
        "resolved"
        if all(
            isinstance(value, str)
            and value.strip()
            and value.casefold() not in {"none", "unresolved"}
            for value in (model_resolved, provider_resolved)
        )
        else "unresolved"
    )
    effective_failure_class = failure_class or "none"
    if status_override == "passed" and (
        evaluation["status"] != "passed"
        or execution_status != "completed"
        or effective_failure_class != "none"
        or (condition == "model-calibration" and resolution_status != "resolved")
    ):
        raise InputError("a passed status override cannot mask failed execution, unresolved identity, or evaluation")
    if (
        evaluation["status"] == "failed"
        or execution_status in {"failed", "timed_out"}
        or effective_failure_class
        not in {"none", "unverified-isolation", "usage-resolution", "git-provenance"}
    ):
        record_status = "failed"
    elif execution_status == "blocked" or effective_failure_class in {
        "unverified-isolation",
        "usage-resolution",
        "git-provenance",
    }:
        record_status = "blocked"
    elif condition == "model-calibration" and resolution_status != "resolved":
        record_status = "blocked"
    else:
        record_status = status_override or evaluation["status"]
    record = {
        "run_id": run_id,
        "benchmark_id": "agent-profile-benchmark",
        "benchmark_version": "0.2.0",
        "release_lock_fingerprint": binding["release_lock_fingerprint"],
        "ledger_fingerprint": binding["ledger_fingerprint"],
        "task_id": task_id,
        "profile_id": binding["profile_id"],
        "harness": harness,
        "model_requested": model_requested,
        "model_resolved": model_resolved or "unresolved",
        "provider_requested": provider_requested,
        "provider_resolved": provider_resolved or "unresolved",
        "resolution_status": resolution_status,
        "condition": condition,
        "evaluator_version": str(evaluation.get("evaluator_version", "unknown")),
        "task_manifest_fingerprint": binding["manifest_fingerprint"],
        "oracle_fingerprint": binding["oracle_fingerprint"],
        "output_schema_fingerprint": binding["output_schema_fingerprint"],
        "evaluator_fingerprint": binding["evaluator_fingerprint"],
        "run_record_schema_fingerprint": binding["run_record_schema_fingerprint"],
        "harness_fingerprint": binding["harness_fingerprint"],
        "prompt_fingerprint": prompt_fingerprint,
        "fixture_fingerprint": fixture_fingerprint,
        "input_fingerprint": input_fingerprint,
        "input_composition_version": INPUT_COMPOSITION_VERSION,
        "started_at": record_started_at,
        "completed_at": record_completed_at,
        "status": record_status,
        "execution_status": execution_status,
        "failure_class": effective_failure_class,
        "raw_output_reference": _raw_output_reference(raw_path),
        "raw_output_fingerprint": raw_output_fingerprint,
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
    except ValueError as exc:
        print(f"replay failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"run_id": record["run_id"], "status": record["status"]}, sort_keys=True))
    return 0 if record["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
