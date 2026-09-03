#!/usr/bin/env python3
"""Validate every artifact and control required by the benchmark-ready release."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

try:
    from evaluate_kody01 import CHECK_IDS as KODY_CHECK_IDS
    from evaluate_kody01 import evaluate_files as evaluate_kody01_files
    from evaluate_task import InputError, evaluate_files as evaluate_task_files
    from release_lock import (
        ReleaseLockError,
        expected_manifest_paths,
        expected_release_artifact_paths,
        load_release_lock,
    )
    from validate_benchmark import (
        DuplicateJSONKeyError,
        EXPECTED_BENCHMARK_VERSION,
        EXPECTED_LEDGER_SCHEMA,
        EXPECTED_TASK_IDS,
        _reject_duplicate_json_keys,
        validate_ledger,
        validate_schema_instance,
    )
except ImportError:  # pragma: no cover - package-style import
    from scripts.evaluate_kody01 import CHECK_IDS as KODY_CHECK_IDS
    from scripts.evaluate_kody01 import evaluate_files as evaluate_kody01_files
    from scripts.evaluate_task import InputError, evaluate_files as evaluate_task_files
    from scripts.release_lock import (
        ReleaseLockError,
        expected_manifest_paths,
        expected_release_artifact_paths,
        load_release_lock,
    )
    from scripts.validate_benchmark import (
        DuplicateJSONKeyError,
        EXPECTED_BENCHMARK_VERSION,
        EXPECTED_LEDGER_SCHEMA,
        EXPECTED_TASK_IDS,
        _reject_duplicate_json_keys,
        validate_ledger,
        validate_schema_instance,
    )


ROOT = Path(__file__).resolve().parents[1]
LEDGER_PATH = ROOT / "data" / "task-ledger.json"
PRIVATE_PATH_PATTERN = re.compile(r"(?<![A-Za-z0-9_])/(?:Users|Volumes|home|private/var)/[^\s\"'`]+")
CREDENTIAL_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(?:api[_-]?key|passwd|password|secret|token)\s*[:=]\s*[\"'][^\"']{8,}[\"']"
)


class ReleaseInputError(ValueError):
    """Raised when a release artifact cannot be loaded or verified."""


def _reject_nonfinite_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number {value!r} is not supported")


def _load_json(path: Path, label: str) -> Any:
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_nonfinite_json_constant,
        )
    except DuplicateJSONKeyError as exc:
        raise ReleaseInputError(f"{label} has duplicate JSON keys: {exc}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise ReleaseInputError(f"unable to read {label} ({type(exc).__name__})") from exc


def _resolve_package_path(package: Path, relative_path: Any, label: str) -> Path:
    if not isinstance(relative_path, str) or not relative_path or Path(relative_path).is_absolute():
        raise ReleaseInputError(f"{label} must be a relative path")
    resolved = (package / relative_path).resolve()
    try:
        resolved.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise ReleaseInputError(f"{label} escapes the repository root") from exc
    if not resolved.is_file():
        raise ReleaseInputError(f"{label} does not name a file: {relative_path}")
    return resolved


def _sha256(path: Path) -> str:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ReleaseInputError(f"unable to read artifact {path.name} ({type(exc).__name__})") from exc
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _check_fingerprint(expected: Any, path: Path, label: str, errors: list[str]) -> None:
    if expected != _sha256(path):
        errors.append(f"{label} fingerprint does not match its manifest")


def _check_schema_definition(schema: Any, label: str, errors: list[str]) -> None:
    if not isinstance(schema, dict):
        errors.append(f"{label} must be a schema object")
        return
    definition_errors = validate_schema_instance({}, schema)
    malformed = [
        error
        for error in definition_errors
        if "schema is malformed or unsupported" in error
        or "unsupported schema keywords" in error
        or "unresolved schema reference" in error
    ]
    errors.extend(f"{label}: {error}" for error in malformed)


def _scan_public_artifact(value: Any, path: str, errors: list[str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            _scan_public_artifact(child, f"{path}.{key}", errors)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _scan_public_artifact(child, f"{path}[{index}]", errors)
    elif isinstance(value, str):
        if PRIVATE_PATH_PATTERN.search(value):
            errors.append(f"{path} contains a private absolute path")
        if CREDENTIAL_ASSIGNMENT_PATTERN.search(value):
            errors.append(f"{path} contains credential-shaped text")


def _evaluate(task_id: str, fixture_path: Path, candidate_path: Path) -> dict[str, Any]:
    if task_id == "KODY-01":
        return evaluate_kody01_files(fixture_path, candidate_path)
    return evaluate_task_files(task_id, fixture_path, candidate_path, model_output=False)


def _expected_check_ids(task_id: str, oracle: Any) -> set[str]:
    if task_id == "KODY-01":
        if not isinstance(oracle, dict):
            raise ReleaseInputError("KODY-01 oracle must be an object")
        if (
            oracle.get("task_id") != "KODY-01"
            or oracle.get("automatic_checks") != list(KODY_CHECK_IDS)
            or oracle.get("hard_failures")
            != [
                "dropped-hard-constraint",
                "invented-authority",
                "unsafe-external-action",
            ]
            or oracle.get("evaluator")
            != {"path": "../../scripts/evaluate_kody01.py", "version": "kody-01-oracle-v2"}
        ):
            raise ReleaseInputError("KODY-01 oracle metadata does not match its evaluator contract")
        return set(KODY_CHECK_IDS)
    if not isinstance(oracle, dict):
        raise ReleaseInputError(f"{task_id}: oracle must be an object")
    checks = oracle.get("checks")
    if not isinstance(checks, list) or any(
        not isinstance(rule, dict) or not isinstance(rule.get("id"), str)
        for rule in checks
    ):
        raise ReleaseInputError(f"{task_id}: oracle checks must have string IDs")
    return {"required-fields", *(rule["id"] for rule in checks)}


def _validate_task_package(
    task: dict[str, Any],
    errors: list[str],
    release_lock: dict[str, Any],
) -> tuple[bool, bool]:
    task_error_start = len(errors)
    task_id = task.get("id")
    if not isinstance(task_id, str):
        errors.append("ledger task has a non-string ID")
        return False, False
    slug = task_id.lower()
    package = ROOT / "fixtures" / slug
    if not package.is_dir():
        errors.append(f"{task_id}: fixture package is missing")
        return False, False
    manifest_path = package / "manifest.json"
    try:
        manifest = _load_json(manifest_path, f"{task_id} manifest")
    except ReleaseInputError as exc:
        errors.append(str(exc))
        return False, False
    if not isinstance(manifest, dict):
        errors.append(f"{task_id}: manifest must be an object")
        return False, False

    expected_version = EXPECTED_BENCHMARK_VERSION
    checks = {
        "task_id": task_id,
        "profile_id": task.get("profile_id"),
        "benchmark_version": expected_version,
        "status": "benchmark-ready",
        "benchmark_ready": True,
        "allowed_tools": [],
    }
    for key, expected in checks.items():
        if manifest.get(key) != expected:
            errors.append(f"{task_id}: manifest.{key} must be {expected!r}")
    evaluator = manifest.get("evaluator")
    expected_evaluator_version = "kody-01-oracle-v2" if task_id == "KODY-01" else "task-oracle-v1"
    if not isinstance(evaluator, dict) or evaluator.get("version") != expected_evaluator_version:
        errors.append(f"{task_id}: manifest evaluator version must be {expected_evaluator_version}")
    required_manifest_keys = {
        "benchmark_ready",
        "allowed_tools",
        "benchmark_version",
        "controls",
        "evaluator",
        "fixture",
        "oracle",
        "output_schema",
        "profile_id",
        "prompt",
        "release_gate",
        "run_record_schema",
        "status",
        "task_id",
    }
    missing = sorted(required_manifest_keys - set(manifest))
    if missing:
        errors.append(f"{task_id}: manifest missing keys: {', '.join(missing)}")

    locked_task = release_lock.get("tasks", {}).get(task_id)
    locked_artifacts = locked_task.get("artifacts") if isinstance(locked_task, dict) else None
    if not isinstance(locked_task, dict) or not isinstance(locked_artifacts, dict):
        errors.append(f"{task_id}: release lock has no task artifact binding")
        locked_artifacts = {}
    else:
        fixture_contract = task.get("fixture")
        if (
            not isinstance(fixture_contract, dict)
            or locked_task.get("fixture_id") != fixture_contract.get("id")
            or locked_task.get("fixture_version") != fixture_contract.get("version", "1.0.0")
        ):
            errors.append(f"{task_id}: release lock fixture identity differs from the frozen ledger")
        for artifact, expected_path in expected_release_artifact_paths(task_id).items():
            entry = locked_artifacts.get(artifact)
            if not isinstance(entry, dict) or entry.get("path") != expected_path:
                errors.append(f"{task_id}: release lock {artifact} path is not canonical")
                continue
            try:
                if entry.get("sha256") != _sha256(ROOT / expected_path):
                    errors.append(f"{task_id}: release lock {artifact} fingerprint does not match")
            except ReleaseInputError as exc:
                errors.append(str(exc))

    def _metadata_path(name: str) -> Any:
        metadata = manifest.get(name)
        return metadata.get("path") if isinstance(metadata, dict) else None

    for artifact, expected_path in expected_manifest_paths(task_id).items():
        actual_path = (
            _metadata_path(artifact)
            if artifact in {"fixture", "prompt", "oracle", "output_schema", "run_record_schema", "evaluator"}
            else manifest.get(artifact)
        )
        if actual_path != expected_path:
            errors.append(
                f"{task_id}: manifest {artifact} path must be the canonical {expected_path!r}"
            )

    manifest_artifact_keys = {
        "fixture": "fixture",
        "prompt": "prompt",
        "oracle": "oracle",
        "output_schema": "output_schema",
        "run_record_schema": "run_record_schema",
        "evaluator": "evaluator",
    }
    for manifest_key, lock_key in manifest_artifact_keys.items():
        metadata = manifest.get(manifest_key)
        lock_entry = locked_artifacts.get(lock_key)
        if isinstance(metadata, dict) and isinstance(lock_entry, dict):
            if metadata.get("sha256") != lock_entry.get("sha256"):
                errors.append(f"{task_id}: manifest {manifest_key} hash differs from the release lock")
    manifest_controls = manifest.get("controls")
    if not isinstance(manifest_controls, list):
        manifest_controls = []
    for condition, lock_key in (
        ("known-good-control", "known_good"),
        ("known-bad-control", "known_bad"),
    ):
        control = next(
            (
                item
                for item in manifest_controls
                if isinstance(item, dict) and item.get("condition") == condition
            ),
            None,
        )
        lock_entry = locked_artifacts.get(lock_key)
        if isinstance(control, dict) and isinstance(lock_entry, dict):
            if control.get("sha256") != lock_entry.get("sha256"):
                errors.append(f"{task_id}: manifest {condition} hash differs from the release lock")

    try:
        fixture_meta = manifest["fixture"]
        prompt_meta = manifest["prompt"]
        oracle_meta = manifest["oracle"]
        fixture_path = _resolve_package_path(package, fixture_meta.get("path"), f"{task_id} fixture path")
        prompt_path = _resolve_package_path(package, prompt_meta.get("path"), f"{task_id} prompt path")
        oracle_path = _resolve_package_path(package, oracle_meta.get("path"), f"{task_id} oracle path")
        output_schema_path = _resolve_package_path(package, manifest["output_schema"].get("path"), f"{task_id} output schema path")
        run_schema_path = _resolve_package_path(package, manifest["run_record_schema"].get("path"), f"{task_id} run-record schema path")
        evaluator_path = _resolve_package_path(package, evaluator.get("path") if isinstance(evaluator, dict) else None, f"{task_id} evaluator path")
        _resolve_package_path(package, manifest["release_gate"], f"{task_id} release-gate path")
    except (KeyError, AttributeError, ReleaseInputError) as exc:
        errors.append(f"{task_id}: {exc}")
        return False, False

    _check_fingerprint(fixture_meta.get("sha256"), fixture_path, f"{task_id} fixture", errors)
    _check_fingerprint(prompt_meta.get("sha256"), prompt_path, f"{task_id} prompt", errors)
    _check_fingerprint(oracle_meta.get("sha256"), oracle_path, f"{task_id} oracle", errors)
    _check_fingerprint(
        evaluator.get("sha256") if isinstance(evaluator, dict) else None,
        evaluator_path,
        f"{task_id} evaluator",
        errors,
    )
    _check_fingerprint(manifest["output_schema"].get("sha256"), output_schema_path, f"{task_id} output schema", errors)
    _check_fingerprint(manifest["run_record_schema"].get("sha256"), run_schema_path, f"{task_id} run-record schema", errors)
    for public_path in (manifest_path, fixture_path, prompt_path, oracle_path):
        if public_path.suffix.lower() == ".json":
            try:
                _scan_public_artifact(
                    _load_json(public_path, f"{task_id} {public_path.name}"),
                    public_path.relative_to(ROOT).as_posix(),
                    errors,
                )
            except ReleaseInputError as exc:
                errors.append(str(exc))
        else:
            try:
                text = public_path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                errors.append(f"{task_id}: unable to read {public_path.name} ({type(exc).__name__})")
            else:
                _scan_public_artifact(text, public_path.relative_to(ROOT).as_posix(), errors)

    try:
        fixture = _load_json(fixture_path, f"{task_id} fixture")
        oracle = _load_json(oracle_path, f"{task_id} oracle")
        output_schema = _load_json(output_schema_path, f"{task_id} output schema")
        run_schema = _load_json(run_schema_path, f"{task_id} run-record schema")
    except ReleaseInputError as exc:
        errors.append(str(exc))
        return False, False
    if fixture_meta.get("status") != "frozen":
        errors.append(f"{task_id}: fixture metadata must be frozen")
    if prompt_meta.get("status") != "frozen":
        errors.append(f"{task_id}: prompt metadata must be frozen")
    if not isinstance(fixture, dict) or fixture.get("fixture_id") != fixture_meta.get("id") or fixture.get("fixture_version") != fixture_meta.get("version"):
        errors.append(f"{task_id}: fixture identity does not match its manifest")
    if not isinstance(oracle, dict):
        errors.append(f"{task_id}: oracle must be an object")
    elif task_id != "KODY-01" and (
        oracle.get("fixture_id") != fixture_meta.get("id")
        or oracle.get("fixture_version") != fixture_meta.get("version")
    ):
        errors.append(f"{task_id}: oracle identity does not match its fixture manifest")
    _check_schema_definition(output_schema, f"{task_id} output schema", errors)
    _check_schema_definition(run_schema, f"{task_id} run-record schema", errors)
    try:
        expected_check_ids = _expected_check_ids(task_id, oracle)
    except ReleaseInputError as exc:
        errors.append(str(exc))
        expected_check_ids = set()

    # Never execute controls after any task-integrity check has failed.
    if len(errors) > task_error_start:
        return False, False

    controls = manifest.get("controls")
    if not isinstance(controls, list):
        errors.append(f"{task_id}: manifest controls must be an array")
        controls = []
    else:
        if len(controls) != 2:
            errors.append(f"{task_id}: manifest must contain exactly two controls")
        conditions = [
            control.get("condition")
            for control in controls
            if isinstance(control, dict)
        ]
        if len(conditions) != len(controls):
            errors.append(f"{task_id}: manifest control entries must be objects")
        string_conditions = [condition for condition in conditions if isinstance(condition, str)]
        if len(string_conditions) != len(set(string_conditions)):
            errors.append(f"{task_id}: manifest controls contain duplicate conditions")
        if set(string_conditions) != {"known-good-control", "known-bad-control"}:
            errors.append(f"{task_id}: manifest controls have unexpected conditions")

    for condition, filename, expected in (
        ("known-good-control", "known-good.json", "passed"),
        ("known-bad-control", "known-bad.json", "failed"),
    ):
        control_meta = next(
            (
                item
                for item in controls
                if isinstance(item, dict) and item.get("condition") == condition
            ),
            None,
        )
        if not isinstance(control_meta, dict):
            errors.append(f"{task_id}: manifest has no {condition} control")
            continue
        if control_meta.get("path") != f"controls/{filename}" or control_meta.get("expected_status") != expected:
            errors.append(f"{task_id}: {condition} control metadata is inconsistent")
            continue
        try:
            candidate_path = _resolve_package_path(package, control_meta.get("path"), f"{task_id} {condition} path")
            candidate = _load_json(candidate_path, f"{task_id} {condition}")
        except ReleaseInputError as exc:
            errors.append(str(exc))
            continue
        _check_fingerprint(control_meta.get("sha256"), candidate_path, f"{task_id} {condition} control", errors)
        _scan_public_artifact(candidate, candidate_path.relative_to(ROOT).as_posix(), errors)
        if condition == "known-good-control" and isinstance(output_schema, dict):
            schema_errors = validate_schema_instance(candidate, output_schema)
            if schema_errors:
                errors.append(f"{task_id}: known-good control violates output schema: {'; '.join(schema_errors)}")
        try:
            evaluation = _evaluate(task_id, fixture_path, candidate_path)
        except (ValueError, OSError, UnicodeError, json.JSONDecodeError) as exc:
            errors.append(f"{task_id}: {condition} evaluation failed ({type(exc).__name__})")
            continue
        if not isinstance(evaluation, dict):
            errors.append(f"{task_id}: {condition} evaluator returned a non-object")
            continue
        if evaluation.get("task_id") != task_id:
            errors.append(f"{task_id}: {condition} evaluator returned the wrong task ID")
        hard_failures_value = evaluation.get("hard_failures")
        if not isinstance(hard_failures_value, list):
            errors.append(f"{task_id}: {condition} evaluation has malformed hard failures")
            hard_failures_value = []
        checks = evaluation.get("automatic_checks")
        if not isinstance(checks, list):
            errors.append(f"{task_id}: {condition} evaluation has no automatic checks")
            checks = []
        check_ids: list[str] = []
        for check in checks:
            if not isinstance(check, dict):
                errors.append(f"{task_id}: {condition} evaluation has a malformed automatic check")
                continue
            check_id = check.get("id")
            if not isinstance(check_id, str) or not check_id:
                errors.append(f"{task_id}: {condition} evaluation has an invalid automatic check ID")
            else:
                check_ids.append(check_id)
            if check.get("status") not in {"pass", "fail", "blocked"}:
                errors.append(f"{task_id}: {condition} evaluation has an invalid automatic check status")
            evidence = check.get("evidence")
            if not isinstance(evidence, list) or not evidence or any(
                not isinstance(item, str) or not item.strip() for item in evidence
            ):
                errors.append(f"{task_id}: {condition} evaluation has missing check evidence")
        if len(check_ids) != len(set(check_ids)):
            errors.append(f"{task_id}: {condition} evaluation has duplicate automatic check IDs")
        if set(check_ids) != expected_check_ids:
            errors.append(
                f"{task_id}: {condition} evaluation check coverage differs from its oracle"
            )
        if evaluation.get("status") != expected:
            errors.append(f"{task_id}: {condition} expected {expected}, got {evaluation.get('status')}")
        if evaluation.get("evaluator_version") != expected_evaluator_version:
            errors.append(f"{task_id}: {condition} reports the wrong evaluator version")
        if condition == "known-good-control":
            if hard_failures_value:
                errors.append(f"{task_id}: known-good control has hard failures")
            if any(not isinstance(check, dict) or check.get("status") != "pass" for check in checks):
                errors.append(f"{task_id}: known-good control has a non-passing automatic check")
        else:
            declared: set[str] = set()
            measurement = task.get("measurement")
            if isinstance(measurement, dict) and isinstance(measurement.get("hard_failures"), list):
                declared = {
                    item["id"]
                    for item in measurement["hard_failures"]
                    if isinstance(item, dict) and isinstance(item.get("id"), str)
                }
            observed: set[str] = set()
            observed = {
                item["id"]
                for item in hard_failures_value
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            }
            missing_failures = sorted(declared - observed)
            if missing_failures:
                errors.append(f"{task_id}: known-bad control misses declared hard failures: {', '.join(missing_failures)}")

    return True, True


def validate_release(ledger: Any) -> list[str]:
    errors = validate_ledger(ledger)
    if errors:
        return [f"ledger: {error}" for error in errors]
    tasks = ledger.get("tasks", []) if isinstance(ledger, dict) else []
    if not isinstance(tasks, list) or {task.get("id") for task in tasks if isinstance(task, dict)} != EXPECTED_TASK_IDS:
        return ["ledger does not enumerate the frozen 18-task registry"]
    try:
        release_lock = load_release_lock()
    except ReleaseLockError as exc:
        return [f"release lock: {exc}"]
    good_count = 0
    bad_count = 0
    for task in tasks:
        if not isinstance(task, dict):
            errors.append("ledger task is not an object")
            continue
        good, bad = _validate_task_package(task, errors, release_lock)
        good_count += int(good)
        bad_count += int(bad)
    if good_count != 18 or bad_count != 18:
        errors.append(f"release control counts are incomplete: known-good={good_count}, known-bad={bad_count}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, default=LEDGER_PATH)
    args = parser.parse_args(argv)
    try:
        ledger = _load_json(args.ledger, "benchmark ledger")
    except ReleaseInputError as exc:
        print(f"release validation failed: {exc}", file=sys.stderr)
        return 1
    errors = validate_release(ledger)
    if errors:
        print("release validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("valid benchmark-ready release: 18 tasks, known-good=18, known-bad=18")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
