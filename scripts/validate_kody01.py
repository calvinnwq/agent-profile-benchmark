"""Validate the KODY-01 control slice without calling a model."""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any, NoReturn

from evaluate_kody01 import (
    EXPECTED_FIXTURE_ID,
    EXPECTED_FIXTURE_VERSION,
    InputError,
    _load_json,
    evaluate_files,
)
from replay_task import replay as replay_task
from validate_benchmark import validate_schema_instance


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "fixtures" / "kody-01"
MANIFEST_PATH = FIXTURE_DIR / "manifest.json"
EXPECTED_EVALUATOR = ROOT / "scripts" / "evaluate_kody01.py"
EXPECTED_OUTPUT_SCHEMA = ROOT / "schemas" / "kody-01-output.schema.json"
EXPECTED_COMMON_RUN_SCHEMA = ROOT / "schemas" / "task-run-record.schema.json"
EXPECTED_RELEASE_GATE = ROOT / "scripts" / "validate_benchmark_ready.py"
EXPECTED_CONTROL_STATUSES = {
    "known-good-control": "passed",
    "known-bad-control": "failed",
}
EXPECTED_BAD_FAILURES = {
    "dropped-hard-constraint",
    "invented-authority",
    "unsafe-external-action",
}


def _fail(message: str) -> NoReturn:
    raise InputError(message)


def _require(condition: bool, message: str) -> None:
    if not condition:
        _fail(message)


def _resolve_manifest_path(raw_path: Any, label: str) -> Path:
    if not isinstance(raw_path, str) or not raw_path:
        _fail(f"manifest {label} path must be a non-empty string")
    path = (FIXTURE_DIR / raw_path).resolve()
    try:
        path.relative_to(ROOT.resolve())
    except ValueError:
        _fail(f"manifest {label} path escapes the repository")
    if not path.is_file():
        _fail(f"manifest {label} path does not exist")
    return path


def _sha256_reference(path: Path) -> str:
    try:
        content = path.read_bytes()
    except OSError as exc:
        _fail(f"unable to read {path.name} ({type(exc).__name__})")
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _load_manifest() -> dict[str, Any]:
    manifest = _load_json(MANIFEST_PATH, "KODY-01 manifest")
    if not isinstance(manifest, dict):
        _fail("KODY-01 manifest must be a JSON object")
    return manifest


def _bound_paths(manifest: dict[str, Any]) -> tuple[Path, Path, Path, Path, Path]:
    _require(manifest.get("task_id") == "KODY-01", "manifest task binding is not KODY-01")
    _require(manifest.get("profile_id") == "kody", "manifest profile binding is not kody")
    _require(manifest.get("slice_status") == "benchmark-ready", "manifest slice status must be benchmark-ready")
    _require(manifest.get("benchmark_version") == "0.2.0", "manifest benchmark version must be 0.2.0")
    _require(manifest.get("status") == "benchmark-ready", "manifest status must be benchmark-ready")
    _require(manifest.get("benchmark_ready") is True, "benchmark-ready slice must claim benchmark readiness")
    _require(manifest.get("allowed_tools") == [], "KODY-01 must declare an empty tool surface")

    fixture_metadata = manifest.get("fixture")
    prompt_metadata = manifest.get("prompt")
    evaluator_metadata = manifest.get("evaluator")
    if not isinstance(fixture_metadata, dict) or not isinstance(prompt_metadata, dict):
        _fail("manifest fixture and prompt metadata must be objects")
    if not isinstance(evaluator_metadata, dict):
        _fail("manifest evaluator metadata must be an object")
    _require(fixture_metadata.get("id") == EXPECTED_FIXTURE_ID, "manifest fixture ID is not bound to KODY-01")
    _require(
        fixture_metadata.get("version") == EXPECTED_FIXTURE_VERSION,
        "manifest fixture version is not bound to KODY-01",
    )
    _require(
        evaluator_metadata.get("version") == "kody-01-oracle-v2",
        "manifest evaluator version is not kody-01-oracle-v2",
    )

    fixture_path = _resolve_manifest_path(fixture_metadata.get("path"), "fixture")
    prompt_path = _resolve_manifest_path(prompt_metadata.get("path"), "prompt")
    evaluator_path = _resolve_manifest_path(evaluator_metadata.get("path"), "evaluator")
    output_schema_metadata = manifest.get("output_schema")
    run_schema_metadata = manifest.get("run_record_schema")
    if not isinstance(output_schema_metadata, dict) or not isinstance(run_schema_metadata, dict):
        _fail("manifest output and run-record schema metadata must be objects")
    output_schema_path = _resolve_manifest_path(output_schema_metadata.get("path"), "output schema")
    run_schema_path = _resolve_manifest_path(run_schema_metadata.get("path"), "run-record schema")
    gate_path = _resolve_manifest_path(manifest.get("release_gate"), "release gate")

    _require(evaluator_path == EXPECTED_EVALUATOR, "manifest evaluator path is not the KODY-01 evaluator")
    _require(output_schema_path == EXPECTED_OUTPUT_SCHEMA, "manifest output schema path is not bound")
    _require(
        run_schema_path == EXPECTED_COMMON_RUN_SCHEMA,
        "manifest run-record schema must use the shared v0.2.0 schema",
    )
    _require(
        gate_path == EXPECTED_RELEASE_GATE,
        "manifest release-gate path must use the global v0.2.0 release gate",
    )

    _require(
        fixture_metadata.get("sha256") == _sha256_reference(fixture_path),
        "fixture SHA-256 does not match the manifest pin",
    )
    _require(
        prompt_metadata.get("sha256") == _sha256_reference(prompt_path),
        "prompt SHA-256 does not match the manifest pin",
    )
    return fixture_path, prompt_path, output_schema_path, run_schema_path, gate_path


def _validate_controls(
    manifest: dict[str, Any],
    fixture_path: Path,
    prompt_path: Path,
    run_schema_path: Path,
) -> list[tuple[str, str]]:
    controls = manifest.get("controls")
    _require(isinstance(controls, list), "manifest controls must be an array")
    seen_conditions: set[str] = set()
    outcomes: list[tuple[str, str]] = []
    with tempfile.TemporaryDirectory(prefix="kody01-gate-") as directory:
        evidence_dir = Path(directory)
        for control in controls:
            if not isinstance(control, dict):
                _fail("manifest control entries must be objects")
            condition = control.get("condition")
            expected_status = control.get("expected_status")
            if not isinstance(condition, str) or not isinstance(expected_status, str):
                _fail("manifest control condition and expected_status must be strings")
            _require(condition in EXPECTED_CONTROL_STATUSES, f"unsupported control condition {condition!r}")
            _require(condition not in seen_conditions, f"duplicate control condition {condition!r}")
            seen_conditions.add(condition)
            _require(
                expected_status == EXPECTED_CONTROL_STATUSES[condition],
                f"manifest expected status is wrong for {condition}",
            )
            candidate_path = _resolve_manifest_path(control.get("path"), f"{condition} control")
            evaluation = evaluate_files(fixture_path, candidate_path)
            _require(
                evaluation["status"] == expected_status,
                f"{condition} evaluator status was {evaluation['status']!r}, expected {expected_status!r}",
            )
            if condition == "known-bad-control":
                failure_ids = {failure["id"] for failure in evaluation["hard_failures"]}
                _require(
                    EXPECTED_BAD_FAILURES.issubset(failure_ids),
                    "known-bad control did not trigger every expected hard failure",
                )

            run_id = "kody-01-gate-" + condition.removesuffix("-control")
            output_path = evidence_dir / f"{condition}.run.json"
            record = replay_task(
                "KODY-01",
                fixture_path,
                prompt_path,
                candidate_path,
                condition,
                run_id,
                "control-" + condition.removesuffix("-control"),
                "control-" + condition.removesuffix("-control"),
                output_path,
            )
            schema = _load_json(run_schema_path, "run-record schema")
            if not isinstance(schema, dict):
                _fail("run-record schema must be a JSON object")
            _require(
                not validate_schema_instance(record, schema),
                f"{condition} replay record failed run-record schema validation",
            )
            _require(record["status"] == expected_status, f"{condition} replay status did not match evaluator")
            outcomes.append((condition, expected_status))
    _require(set(seen_conditions) == set(EXPECTED_CONTROL_STATUSES), "manifest controls are incomplete")
    return outcomes


def validate_slice() -> list[tuple[str, str]]:
    manifest = _load_manifest()
    fixture_path, prompt_path, _, run_schema_path, _ = _bound_paths(manifest)
    return _validate_controls(manifest, fixture_path, prompt_path, run_schema_path)


def main() -> int:
    try:
        outcomes = validate_slice()
    except (ValueError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"KODY-01 gate failed: {exc}")
        return 1
    summary = ", ".join(f"{condition}={status}" for condition, status in outcomes)
    print(f"valid KODY-01 control slice: {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
