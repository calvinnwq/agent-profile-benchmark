#!/usr/bin/env python3
"""Build a deterministic benchmark-specific model leaderboard from run evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any, Iterable

try:
    from validate_benchmark import (
        EXPECTED_LEDGER_FINGERPRINT,
        _canonical_fingerprint,
        validate_schema_instance,
    )
except ImportError:  # pragma: no cover - package-style import
    from scripts.validate_benchmark import (
        EXPECTED_LEDGER_FINGERPRINT,
        _canonical_fingerprint,
        validate_schema_instance,
    )

try:
    from release_lock import (
        ReleaseLockError,
        load_release_lock,
        verify_task_release_artifacts,
    )
except ImportError:  # pragma: no cover - package-style import
    from scripts.release_lock import (
        ReleaseLockError,
        load_release_lock,
        verify_task_release_artifacts,
    )


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER = ROOT / "data" / "task-ledger.json"
DEFAULT_POLICY = ROOT / "data" / "leaderboard-policy.json"
POLICY_SCHEMA = ROOT / "schemas" / "leaderboard-policy.schema.json"
ROSTER_SCHEMA = ROOT / "schemas" / "model-roster.schema.json"
INPUT_SCHEMA = ROOT / "schemas" / "leaderboard-input.schema.json"
OUTPUT_SCHEMA = ROOT / "schemas" / "leaderboard-output.schema.json"
DEFAULT_RUN_SCHEMA = ROOT / "schemas" / "task-run-record.schema.json"
UNRESOLVED_IDENTITY_VALUES = {"", "none", "unresolved"}
VALID_AVAILABILITY = {"eligible", "excluded"}
VALID_RUN_STATUSES = {"passed", "failed", "blocked"}
VALID_EXECUTION_STATUSES = {"completed", "failed", "blocked", "timed_out"}


class LeaderboardInputError(ValueError):
    """Raised when leaderboard inputs cannot be trusted or compared."""


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise LeaderboardInputError(f"duplicate JSON object key {key!r}")
        value[key] = item
    return value


def _reject_nonfinite_json_constant(value: str) -> None:
    raise LeaderboardInputError(f"non-finite JSON number {value!r} is not supported")


def _load_json(path: Path, label: str) -> Any:
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_nonfinite_json_constant,
        )
    except LeaderboardInputError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise LeaderboardInputError(f"unable to read {label} ({type(exc).__name__})") from exc


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _required_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LeaderboardInputError(f"{path} must be a non-empty string")
    return value.strip()


def _relative_path(root: Path, value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise LeaderboardInputError(f"{label} must be a relative path")
    resolved = (root / value).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise LeaderboardInputError(f"{label} escapes the evidence root") from exc
    if not resolved.is_file():
        raise LeaderboardInputError(f"{label} does not name a file: {value}")
    return resolved


def _relative_reference(root: Path, value: Any, label: str) -> str:
    path = _relative_path(root, value, label)
    return path.relative_to(root.resolve()).as_posix()


def _file_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise LeaderboardInputError(f"unable to fingerprint evidence file ({type(exc).__name__})") from exc
    return f"sha256:{digest.hexdigest()}"


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _mean(values: Iterable[float | int]) -> float | None:
    values = list(values)
    if not values:
        return None
    return round(sum(values) / len(values), 6)


def _median(values: Iterable[int | float]) -> int | float | None:
    values = list(values)
    if not values:
        return None
    result = statistics.median(values)
    return int(result) if float(result).is_integer() else round(result, 3)


def _as_unresolved(value: Any) -> bool:
    return not isinstance(value, str) or value.strip().casefold() in UNRESOLVED_IDENTITY_VALUES


def _validate_checked_schema(value: Any, schema_path: Path, label: str) -> None:
    schema = _load_json(schema_path, f"{label} schema")
    if not isinstance(schema, dict):
        raise LeaderboardInputError(f"{label} schema must be an object")
    errors = validate_schema_instance(value, schema)
    if errors:
        raise LeaderboardInputError(f"{label} violates its schema: {'; '.join(errors[:4])}")


def _validate_policy(policy: Any, benchmark_id: str, benchmark_version: str) -> dict[str, Any]:
    if not isinstance(policy, dict):
        raise LeaderboardInputError("leaderboard policy must be an object")
    for key in ("schema_version", "policy_id", "policy_version", "benchmark_id", "benchmark_version", "scope", "status"):
        _required_string(policy.get(key), f"policy.{key}")
    if policy["schema_version"] != "leaderboard-policy-v1":
        raise LeaderboardInputError("policy.schema_version must be leaderboard-policy-v1")
    if policy["benchmark_id"] != benchmark_id or policy["benchmark_version"] != benchmark_version:
        raise LeaderboardInputError("policy benchmark identity does not match the input")
    if policy["policy_id"] != "leaderboard-v1" or policy["scope"] != "benchmark-specific model leaderboard and routing aid" or policy["status"] != "active":
        raise LeaderboardInputError("policy identity or scope is not supported by leaderboard-v1")
    coverage = policy.get("coverage")
    if not isinstance(coverage, dict):
        raise LeaderboardInputError("policy.coverage must be an object")
    minimum_coverage = coverage.get("provisional_min_task_coverage")
    minimum_replicates = coverage.get("confirmed_min_replicates_per_task")
    if not _finite_number(minimum_coverage) or not 0 < minimum_coverage <= 1:
        raise LeaderboardInputError("policy.coverage.provisional_min_task_coverage must be between 0 and 1")
    if not isinstance(minimum_replicates, int) or isinstance(minimum_replicates, bool) or minimum_replicates < 1:
        raise LeaderboardInputError("policy.coverage.confirmed_min_replicates_per_task must be a positive integer")
    ranking = policy.get("ranking")
    if not isinstance(ranking, dict):
        raise LeaderboardInputError("policy.ranking must be an object")
    if ranking.get("primary") != "full_contract_pass_rate":
        raise LeaderboardInputError("policy.ranking.primary must be full_contract_pass_rate")
    tie_breakers = ranking.get("tie_breakers")
    expected_tie_breakers = [
        "automatic_check_pass_rate",
        "human_quality_score",
        "hard_failure_rate",
        "invalid_output_rate",
        "median_latency_ms",
    ]
    if tie_breakers != expected_tie_breakers:
        raise LeaderboardInputError("policy.ranking.tie_breakers do not match leaderboard-v1")
    return policy


def _load_ledger(
    path: Path,
    benchmark_id: str,
    benchmark_version: str,
    *,
    require_frozen: bool,
) -> tuple[list[str], dict[str, list[str]], dict[str, str]]:
    ledger = _load_json(path, "benchmark ledger")
    if not isinstance(ledger, dict):
        raise LeaderboardInputError("benchmark ledger must be an object")
    if ledger.get("benchmark_id") != benchmark_id or ledger.get("benchmark_version") != benchmark_version:
        raise LeaderboardInputError("benchmark ledger identity does not match the leaderboard input")
    if require_frozen and _canonical_fingerprint(ledger) != EXPECTED_LEDGER_FINGERPRINT:
        raise LeaderboardInputError("benchmark ledger does not match the sealed v0.2.0 contract")
    raw_profiles = ledger.get("profiles")
    raw_tasks = ledger.get("tasks")
    if not isinstance(raw_profiles, list) or not isinstance(raw_tasks, list):
        raise LeaderboardInputError("benchmark ledger profiles and tasks must be arrays")
    profile_order: list[str] = []
    profile_tasks: dict[str, list[str]] = {}
    for index, profile in enumerate(raw_profiles):
        if not isinstance(profile, dict):
            raise LeaderboardInputError(f"ledger.profiles[{index}] must be an object")
        profile_id = _required_string(profile.get("id"), f"ledger.profiles[{index}].id")
        if profile_id in profile_tasks:
            raise LeaderboardInputError(f"ledger contains duplicate profile {profile_id!r}")
        task_ids = profile.get("task_ids")
        if not isinstance(task_ids, list) or any(not isinstance(item, str) or not item for item in task_ids):
            raise LeaderboardInputError(f"ledger.profiles[{index}].task_ids must be a string list")
        if len(task_ids) != len(set(task_ids)):
            raise LeaderboardInputError(f"ledger profile {profile_id!r} contains duplicate task IDs")
        profile_order.append(profile_id)
        profile_tasks[profile_id] = list(task_ids)
    task_profile: dict[str, str] = {}
    for index, task in enumerate(raw_tasks):
        if not isinstance(task, dict):
            raise LeaderboardInputError(f"ledger.tasks[{index}] must be an object")
        task_id = _required_string(task.get("id"), f"ledger.tasks[{index}].id")
        profile_id = _required_string(task.get("profile_id"), f"ledger.tasks[{index}].profile_id")
        if task_id in task_profile:
            raise LeaderboardInputError(f"ledger contains duplicate task {task_id!r}")
        if profile_id not in profile_tasks:
            raise LeaderboardInputError(f"task {task_id!r} names unknown profile {profile_id!r}")
        if task_id not in profile_tasks[profile_id]:
            raise LeaderboardInputError(f"task {task_id!r} is not listed under profile {profile_id!r}")
        task_profile[task_id] = profile_id
    expected_tasks = {task_id for task_ids in profile_tasks.values() for task_id in task_ids}
    if expected_tasks != set(task_profile):
        raise LeaderboardInputError("ledger profile task lists and task records disagree")
    ordered_tasks = [task_id for profile_id in profile_order for task_id in profile_tasks[profile_id]]
    return ordered_tasks, profile_tasks, task_profile


def _load_roster(path: Path, benchmark_id: str, benchmark_version: str) -> dict[str, Any]:
    roster = _load_json(path, "model roster")
    if not isinstance(roster, dict):
        raise LeaderboardInputError("model roster must be an object")
    _validate_checked_schema(roster, ROSTER_SCHEMA, "model roster")
    for key in ("schema_version", "snapshot_id", "provider", "captured_at"):
        _required_string(roster.get(key), f"roster.{key}")
    if roster["schema_version"] != "model-roster-v1":
        raise LeaderboardInputError("roster.schema_version must be model-roster-v1")
    if roster.get("benchmark_id") != benchmark_id or roster.get("benchmark_version") != benchmark_version:
        raise LeaderboardInputError("roster benchmark identity does not match the input")
    models = roster.get("models")
    if not isinstance(models, list) or not models:
        raise LeaderboardInputError("roster.models must be a non-empty array")
    seen: set[str] = set()
    for index, model in enumerate(models):
        if not isinstance(model, dict):
            raise LeaderboardInputError(f"roster.models[{index}] must be an object")
        model_id = _required_string(model.get("model_id"), f"roster.models[{index}].model_id")
        if model_id in seen:
            raise LeaderboardInputError(f"roster contains duplicate model ID {model_id!r}")
        seen.add(model_id)
        requested = _required_string(model.get("requested_model_id"), f"roster.models[{index}].requested_model_id")
        resolved = _required_string(model.get("resolved_model_id"), f"roster.models[{index}].resolved_model_id")
        availability = _required_string(model.get("availability"), f"roster.models[{index}].availability")
        if availability not in VALID_AVAILABILITY:
            raise LeaderboardInputError(f"roster.models[{index}].availability is not supported")
        _required_string(model.get("provider_requested"), f"roster.models[{index}].provider_requested")
        _required_string(model.get("provider_resolved"), f"roster.models[{index}].provider_resolved")
        if availability == "eligible":
            if resolved.casefold() in UNRESOLVED_IDENTITY_VALUES or model_id != resolved:
                raise LeaderboardInputError(f"eligible roster model {model_id!r} lacks a canonical resolved identity")
        elif not model.get("exclusion_reason"):
            raise LeaderboardInputError(f"excluded roster model {model_id!r} needs exclusion_reason")
        if requested == "":
            raise LeaderboardInputError(f"roster model {model_id!r} has an empty requested identity")
    return roster


def _load_input(path: Path, root: Path, benchmark_id: str, benchmark_version: str) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = _load_json(path, "leaderboard input manifest")
    if not isinstance(manifest, dict):
        raise LeaderboardInputError("leaderboard input manifest must be an object")
    _validate_checked_schema(manifest, INPUT_SCHEMA, "leaderboard input manifest")
    if manifest.get("schema_version") != "leaderboard-input-v1":
        raise LeaderboardInputError("input.schema_version must be leaderboard-input-v1")
    if manifest.get("benchmark_id") != benchmark_id or manifest.get("benchmark_version") != benchmark_version:
        raise LeaderboardInputError("input benchmark identity does not match the policy")
    snapshot_id = _required_string(manifest.get("snapshot_id"), "input.snapshot_id")
    roster_path = _relative_path(root, manifest.get("roster_path"), "input.roster_path")
    roster = _load_roster(roster_path, benchmark_id, benchmark_version)
    runs = manifest.get("runs")
    if not isinstance(runs, list):
        raise LeaderboardInputError("input.runs must be an array")
    run_refs: list[dict[str, str]] = []
    seen_run_ids: set[str] = set()
    seen_paths: set[str] = set()
    for index, item in enumerate(runs):
        if not isinstance(item, dict):
            raise LeaderboardInputError(f"input.runs[{index}] must be an object")
        run_id = _required_string(item.get("run_id"), f"input.runs[{index}].run_id")
        record_path = _relative_reference(root, item.get("record_path"), f"input.runs[{index}].record_path")
        if run_id in seen_run_ids:
            raise LeaderboardInputError(f"input contains duplicate run ID {run_id!r}")
        if record_path in seen_paths:
            raise LeaderboardInputError(f"input contains duplicate record path {record_path!r}")
        seen_run_ids.add(run_id)
        seen_paths.add(record_path)
        run_refs.append({"run_id": run_id, "record_path": record_path})
    run_refs.sort(key=lambda item: (item["run_id"], item["record_path"]))
    manifest = dict(manifest)
    manifest["snapshot_id"] = snapshot_id
    manifest["roster_path"] = roster_path.relative_to(root.resolve()).as_posix()
    manifest["runs"] = run_refs
    return manifest, roster


def _schema_errors(value: Any, schema_path: Path) -> list[str]:
    schema = _load_json(schema_path, "run-record schema")
    if not isinstance(schema, dict):
        raise LeaderboardInputError("run-record schema must be an object")
    return validate_schema_instance(value, schema)


def _human_score(value: Any) -> tuple[float | None, bool]:
    """Return a normalized 0-1 score and whether the supplied value was usable."""
    candidate: Any = None
    if _finite_number(value):
        candidate = value
    elif isinstance(value, dict):
        for key in ("weighted_score", "score", "overall"):
            if _finite_number(value.get(key)):
                candidate = value[key]
                break
        if candidate is None and isinstance(value.get("dimensions"), dict):
            numbers = [item for item in value["dimensions"].values() if _finite_number(item)]
            if numbers and len(numbers) == len(value["dimensions"]):
                candidate = sum(numbers) / len(numbers)
        if candidate is None:
            numbers = [item for item in value.values() if _finite_number(item)]
            if numbers and len(numbers) == len(value):
                candidate = sum(numbers) / len(numbers)
    if not _finite_number(candidate) or not 0 <= candidate <= 4:
        return None, False
    return round(float(candidate) / 4, 6), True


def _record_model_id(record: dict[str, Any]) -> tuple[str, bool]:
    resolution_status = record.get("resolution_status")
    if resolution_status == "resolved":
        model_id = record.get("model_resolved")
        if _as_unresolved(model_id):
            raise LeaderboardInputError(f"run {record.get('run_id')!r} is marked resolved with an unresolved model")
        return str(model_id).strip(), True
    if resolution_status == "unresolved":
        requested = _required_string(record.get("model_requested"), "run.model_requested")
        return requested, False
    raise LeaderboardInputError(f"run {record.get('run_id')!r} has unsupported resolution_status")


def _trusted_task_bindings(task_ids: Iterable[str]) -> dict[str, dict[str, str]]:
    """Verify the sealed release and return expected run-record fingerprints."""
    try:
        release_lock = load_release_lock()
        bindings: dict[str, dict[str, str]] = {}
        for task_id in task_ids:
            binding = verify_task_release_artifacts(task_id, "hermes-oneshot")
            task = release_lock["tasks"][task_id]
            artifacts = task["artifacts"]
            bindings[task_id] = {
                "release_lock_fingerprint": binding["release_lock_fingerprint"],
                "ledger_fingerprint": binding["ledger_fingerprint"],
                "task_manifest_fingerprint": artifacts["manifest"]["sha256"],
                "oracle_fingerprint": binding["oracle_fingerprint"],
                "output_schema_fingerprint": binding["output_schema_fingerprint"],
                "evaluator_fingerprint": binding["evaluator_fingerprint"],
                "run_record_schema_fingerprint": binding["run_record_schema_fingerprint"],
                "harness_fingerprint": binding["harness_fingerprint"],
                "prompt_fingerprint": artifacts["prompt"]["sha256"],
                "fixture_fingerprint": artifacts["fixture"]["sha256"],
            }
        return bindings
    except (KeyError, ReleaseLockError, TypeError) as exc:
        raise LeaderboardInputError(f"sealed benchmark release could not be verified ({exc})") from exc


def _load_records(
    manifest: dict[str, Any],
    roster: dict[str, Any],
    root: Path,
    task_profile: dict[str, str],
    run_schema_path: Path,
    policy: dict[str, Any],
    trusted_bindings: dict[str, dict[str, str]] | None,
) -> list[dict[str, Any]]:
    roster_by_id = {model["model_id"]: model for model in roster["models"]}
    roster_by_requested = {model["requested_model_id"]: model for model in roster["models"]}
    records: list[dict[str, Any]] = []
    release_lock: str | None = None
    provider_requested = roster["provider"]
    for ref in manifest["runs"]:
        path = _relative_path(root, ref["record_path"], f"run record {ref['run_id']}")
        record = _load_json(path, f"run record {ref['run_id']}")
        if not isinstance(record, dict):
            raise LeaderboardInputError(f"run record {ref['run_id']} must be an object")
        schema_errors = _schema_errors(record, run_schema_path)
        if schema_errors:
            raise LeaderboardInputError(
                f"run record {ref['run_id']} violates task-run-record schema: {'; '.join(schema_errors[:4])}"
            )
        if record.get("run_id") != ref["run_id"]:
            raise LeaderboardInputError(f"run record path {ref['record_path']} contains a different run ID")
        if record.get("benchmark_id") != manifest["benchmark_id"] or record.get("benchmark_version") != manifest["benchmark_version"]:
            raise LeaderboardInputError(f"run {ref['run_id']} has incompatible benchmark identity")
        if record.get("condition") != "model-calibration":
            raise LeaderboardInputError(f"run {ref['run_id']} is not a model-calibration record")
        if record.get("task_id") not in task_profile:
            raise LeaderboardInputError(f"run {ref['run_id']} names an unknown task")
        if record.get("profile_id") != task_profile[record["task_id"]]:
            raise LeaderboardInputError(f"run {ref['run_id']} task/profile mapping is inconsistent")
        if trusted_bindings is not None:
            expected = trusted_bindings[record["task_id"]]
            for field, expected_value in expected.items():
                if record.get(field) != expected_value:
                    raise LeaderboardInputError(
                        f"run {ref['run_id']} {field} does not match the sealed benchmark release"
                    )
        if record.get("provider_requested") != provider_requested:
            raise LeaderboardInputError(f"run {ref['run_id']} uses a different requested provider")
        if record.get("harness") != "hermes-oneshot":
            raise LeaderboardInputError(f"run {ref['run_id']} does not use the scoreable Hermes harness")
        if release_lock is None:
            release_lock = record.get("release_lock_fingerprint")
        elif record.get("release_lock_fingerprint") != release_lock:
            raise LeaderboardInputError("selected run records use different release-lock fingerprints")
        model_id, resolved = _record_model_id(record)
        roster_model = roster_by_id.get(model_id) if resolved else roster_by_requested.get(model_id)
        if roster_model is None:
            raise LeaderboardInputError(
                f"run {ref['run_id']} identity {model_id!r} is absent from the roster; update the roster before onboarding it"
            )
        if roster_model["requested_model_id"] != record.get("model_requested"):
            raise LeaderboardInputError(f"run {ref['run_id']} requested identity differs from its roster record")
        if record.get("provider_resolved") != roster_model.get("provider_resolved"):
            if roster_model["availability"] != "excluded" and resolved:
                raise LeaderboardInputError(f"run {ref['run_id']} resolved provider differs from its roster record")
        raw_path = _relative_path(root, record.get("raw_output_reference"), f"run {ref['run_id']} raw output")
        raw_fingerprint = _required_string(record.get("raw_output_fingerprint"), f"run {ref['run_id']} raw output fingerprint")
        if raw_fingerprint != _file_fingerprint(raw_path):
            raise LeaderboardInputError(f"run {ref['run_id']} raw output fingerprint does not match the recorded bytes")
        raw_reference = raw_path.relative_to(root.resolve()).as_posix()
        record = dict(record)
        record["_model_id"] = roster_model["model_id"]
        # An excluded roster entity stays visible, but never contributes to
        # comparable quality metrics even if an old run resolved its identity.
        record["_identity_resolved"] = resolved and roster_model["availability"] == "eligible"
        record["_raw_output_reference"] = raw_reference
        record["_record_path"] = ref["record_path"]
        records.append(record)
    return records


def _run_metrics(record: dict[str, Any]) -> dict[str, Any]:
    checks = record.get("automatic_checks")
    check_statuses = [item.get("status") for item in checks if isinstance(item, dict)] if isinstance(checks, list) else []
    hard_failure_ids = [item.get("id") for item in record.get("hard_failures", []) if isinstance(item, dict)]
    human_score, human_available = _human_score(record.get("human_scores"))
    full_pass = bool(
        record.get("resolution_status") == "resolved"
        and record.get("provider_resolved") not in UNRESOLVED_IDENTITY_VALUES
        and record.get("execution_status") == "completed"
        and record.get("status") == "passed"
        and not hard_failure_ids
        and check_statuses
        and all(status == "pass" for status in check_statuses)
    )
    auto_rate = sum(status == "pass" for status in check_statuses) / len(check_statuses) if check_statuses else 0.0
    return {
        "full_contract_pass": full_pass,
        "automatic_check_pass_rate": round(auto_rate, 6),
        "hard_failure": bool(hard_failure_ids),
        "invalid_output": "invalid-output" in hard_failure_ids,
        "human_quality_score": human_score,
        "human_score_available": human_available,
        "latency_ms": record.get("latency_ms") if _finite_number(record.get("latency_ms")) else None,
        "hard_failure_ids": sorted(item for item in hard_failure_ids if isinstance(item, str)),
        "automatic_check_statuses": [item for item in check_statuses if isinstance(item, str)],
    }


def _scope_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    comparable = [record for record in records if record["_identity_resolved"]]
    metrics = [_run_metrics(record) for record in comparable]
    human_scores = [item["human_quality_score"] for item in metrics if item["human_score_available"]]
    latencies = [item["latency_ms"] for item in metrics if item["latency_ms"] is not None]
    return {
        "full_contract_pass_rate": _mean(item["full_contract_pass"] for item in metrics),
        "automatic_check_pass_rate": _mean(item["automatic_check_pass_rate"] for item in metrics),
        "human_quality_score": _mean(human_scores),
        "human_score_coverage": round(len(human_scores) / len(metrics), 6) if metrics else 0.0,
        "hard_failure_rate": _mean(item["hard_failure"] for item in metrics),
        "invalid_output_rate": _mean(item["invalid_output"] for item in metrics),
        "median_latency_ms": _median(latencies),
    }


def _run_trace(record: dict[str, Any]) -> dict[str, Any]:
    metrics = _run_metrics(record)
    return {
        "run_id": record["run_id"],
        "record_path": record["_record_path"],
        "raw_output_reference": record["_raw_output_reference"],
        "model_requested": record["model_requested"],
        "model_resolved": record["model_resolved"],
        "provider_resolved": record["provider_resolved"],
        "resolution_status": record["resolution_status"],
        "status": record["status"],
        "execution_status": record["execution_status"],
        "failure_class": record["failure_class"],
        "full_contract_pass": metrics["full_contract_pass"],
        "automatic_check_pass_rate": metrics["automatic_check_pass_rate"],
        "automatic_check_statuses": metrics["automatic_check_statuses"],
        "hard_failure_ids": metrics["hard_failure_ids"],
        "latency_ms": metrics["latency_ms"],
        "human_quality_score": metrics["human_quality_score"],
    }


def _coverage_status(task_cells: list[dict[str, Any]], required_task_ids: list[str]) -> str:
    covered = {cell["task_id"] for cell in task_cells if cell["comparable_runs"] > 0}
    return "complete" if covered == set(required_task_ids) else "incomplete"


def _model_entry(
    model: dict[str, Any],
    records: list[dict[str, Any]],
    ordered_tasks: list[str],
    profile_tasks: dict[str, list[str]],
    task_profile: dict[str, str],
    minimum_confirmed_replicates: int,
) -> dict[str, Any]:
    task_cells: list[dict[str, Any]] = []
    for task_id in ordered_tasks:
        task_records = sorted(
            [record for record in records if record["task_id"] == task_id],
            key=lambda item: (item.get("completed_at", ""), item["run_id"]),
        )
        comparable = [record for record in task_records if record["_identity_resolved"]]
        cell = {
            "task_id": task_id,
            "profile_id": task_profile[task_id],
            "attempted_runs": len(task_records),
            "comparable_runs": len(comparable),
            "excluded_runs": len(task_records) - len(comparable),
            "replicate_count": len(comparable),
            "coverage_status": "covered" if comparable else "missing",
            "metrics": _scope_metrics(task_records),
            "runs": [_run_trace(record) for record in task_records],
        }
        task_cells.append(cell)

    def _aggregate_cells(cells: list[dict[str, Any]]) -> dict[str, Any]:
        """Aggregate equal task weights within profiles, then equal profile weights."""
        covered_cells = [cell for cell in cells if cell["comparable_runs"] > 0]
        grouped: dict[str, list[dict[str, Any]]] = {}
        for cell in covered_cells:
            grouped.setdefault(cell["profile_id"], []).append(cell)
        profile_metrics = [
            {
                key: _mean(
                    cell["metrics"][key]
                    for cell in profile_cells
                    if cell["metrics"][key] is not None
                )
                for key in (
                    "full_contract_pass_rate",
                    "automatic_check_pass_rate",
                    "human_quality_score",
                    "hard_failure_rate",
                    "invalid_output_rate",
                )
            }
            for profile_cells in grouped.values()
        ]
        # The trace carries every value needed for the median without exposing
        # filesystem paths or depending on the order of the input manifest.
        latencies = [
            run["latency_ms"]
            for cell in covered_cells
            for run in cell["runs"]
            if run["resolution_status"] == "resolved" and run["latency_ms"] is not None
        ]
        return {
            "full_contract_pass_rate": _mean(
                item["full_contract_pass_rate"]
                for item in profile_metrics
                if item["full_contract_pass_rate"] is not None
            ),
            "automatic_check_pass_rate": _mean(
                item["automatic_check_pass_rate"]
                for item in profile_metrics
                if item["automatic_check_pass_rate"] is not None
            ),
            "human_quality_score": _mean(
                item["human_quality_score"]
                for item in profile_metrics
                if item["human_quality_score"] is not None
            ),
            "human_score_coverage": round(
                sum(
                    cell["metrics"]["human_score_coverage"] * cell["comparable_runs"]
                    for cell in covered_cells
                )
                / sum(cell["comparable_runs"] for cell in covered_cells),
                6,
            ) if covered_cells else 0.0,
            "hard_failure_rate": _mean(
                item["hard_failure_rate"]
                for item in profile_metrics
                if item["hard_failure_rate"] is not None
            ),
            "invalid_output_rate": _mean(
                item["invalid_output_rate"]
                for item in profile_metrics
                if item["invalid_output_rate"] is not None
            ),
            "median_latency_ms": _median(latencies),
        }

    def _scope_view(task_ids: list[str]) -> dict[str, Any]:
        selected = [cell for cell in task_cells if cell["task_id"] in task_ids]
        covered = [cell for cell in selected if cell["comparable_runs"] > 0]
        comparable_records = [
            record
            for task_id in task_ids
            for record in records
            if record["task_id"] == task_id and record["_identity_resolved"]
        ]
        scope_status = "unranked"
        reason_codes: list[str] = []
        if model["availability"] == "excluded":
            scope_status = "excluded"
            reason_codes.append("roster-excluded")
        elif len(covered) == len(task_ids):
            min_replicates = min(cell["replicate_count"] for cell in selected) if selected else 0
            if min_replicates >= minimum_confirmed_replicates:
                scope_status = "confirmed"
            else:
                scope_status = "provisional"
        else:
            reason_codes.append("incomplete-task-coverage")
            if comparable_records:
                reason_codes.append("missing-comparable-cells")
            elif records:
                reason_codes.append("no-comparable-cells")
            else:
                reason_codes.append("no-evidence")
        return {
            "status": scope_status,
            "reason_codes": reason_codes,
            "task_ids": list(task_ids),
            "tasks_covered": len(covered),
            "tasks_total": len(task_ids),
            "task_coverage_rate": round(len(covered) / len(task_ids), 6) if task_ids else 0.0,
            "minimum_replicates": min((cell["replicate_count"] for cell in selected), default=0),
            "metrics": _aggregate_cells(selected),
        }

    profile_views = {
        profile_id: _scope_view(task_ids)
        for profile_id, task_ids in profile_tasks.items()
    }
    overall_view = _scope_view(ordered_tasks)
    return {
        "model_id": model["model_id"],
        "requested_model_id": model["requested_model_id"],
        "resolved_model_id": model["resolved_model_id"],
        "provider": model["provider_resolved"],
        "availability": model["availability"],
        "exclusion_reason": model.get("exclusion_reason"),
        "status": overall_view["status"],
        "reason_codes": overall_view["reason_codes"],
        "coverage": {
            "tasks_covered": overall_view["tasks_covered"],
            "tasks_total": overall_view["tasks_total"],
            "task_coverage_rate": overall_view["task_coverage_rate"],
            "profiles_covered": sum(view["tasks_covered"] == view["tasks_total"] for view in profile_views.values()),
            "profiles_total": len(profile_views),
            "minimum_replicates": overall_view["minimum_replicates"],
            "maximum_replicates": max((cell["replicate_count"] for cell in task_cells), default=0),
            "attempted_runs": sum(cell["attempted_runs"] for cell in task_cells),
            "comparable_runs": sum(cell["comparable_runs"] for cell in task_cells),
            "excluded_runs": sum(cell["excluded_runs"] for cell in task_cells),
        },
        "metrics": overall_view["metrics"],
        "profiles": profile_views,
        "task_cells": task_cells,
    }


def _ranking_key(entry: dict[str, Any]) -> tuple[Any, ...]:
    metrics = entry["metrics"]
    return (
        -(metrics["full_contract_pass_rate"] if metrics["full_contract_pass_rate"] is not None else -1),
        -(metrics["automatic_check_pass_rate"] if metrics["automatic_check_pass_rate"] is not None else -1),
        -(metrics["human_quality_score"] if metrics["human_quality_score"] is not None else -1),
        metrics["hard_failure_rate"] if metrics["hard_failure_rate"] is not None else math.inf,
        metrics["invalid_output_rate"] if metrics["invalid_output_rate"] is not None else math.inf,
        metrics["median_latency_ms"] if metrics["median_latency_ms"] is not None else math.inf,
        entry["model_id"],
    )


def _ranking_view(entries: list[dict[str, Any]], scope: str) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    unranked: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for entry in entries:
        if scope == "overall":
            view = {
                "status": entry["status"],
                "reason_codes": entry["reason_codes"],
                "tasks_covered": entry["coverage"]["tasks_covered"],
                "tasks_total": entry["coverage"]["tasks_total"],
                "task_coverage_rate": entry["coverage"]["task_coverage_rate"],
                "minimum_replicates": entry["coverage"]["minimum_replicates"],
                "metrics": entry["metrics"],
            }
        else:
            view = entry["profiles"][scope]
        row = {
            "model_id": entry["model_id"],
            "status": view["status"],
            "reason_codes": view["reason_codes"],
            "coverage": {
                "tasks_covered": view["tasks_covered"],
                "tasks_total": view["tasks_total"],
                "task_coverage_rate": view["task_coverage_rate"],
                "minimum_replicates": view["minimum_replicates"],
            },
            "metrics": view["metrics"],
        }
        if view["status"] == "excluded":
            excluded.append(row)
        elif view["status"] in {"provisional", "confirmed"}:
            candidates.append(row)
        else:
            unranked.append(row)
    candidates.sort(key=_ranking_key)
    for rank, row in enumerate(candidates, start=1):
        row["rank"] = rank
    for collection in (unranked, excluded):
        collection.sort(key=lambda item: item["model_id"])
    return {"ranked": candidates, "unranked": unranked, "excluded": excluded}


def build_leaderboard(
    *,
    root: Path,
    input_path: Path,
    policy_path: Path = DEFAULT_POLICY,
    ledger_path: Path = DEFAULT_LEDGER,
    run_schema_path: Path | None = None,
    allow_untrusted_inputs: bool = False,
) -> dict[str, Any]:
    """Build a leaderboard without using filesystem traversal order or wall-clock time."""
    input_manifest = _load_json(input_path, "leaderboard input manifest")
    if not isinstance(input_manifest, dict):
        raise LeaderboardInputError("leaderboard input manifest must be an object")
    benchmark_id = _required_string(input_manifest.get("benchmark_id"), "input.benchmark_id")
    benchmark_version = _required_string(input_manifest.get("benchmark_version"), "input.benchmark_version")
    policy_document = _load_json(policy_path, "leaderboard policy")
    _validate_checked_schema(policy_document, POLICY_SCHEMA, "leaderboard policy")
    policy = _validate_policy(policy_document, benchmark_id, benchmark_version)
    run_schema_path = run_schema_path or DEFAULT_RUN_SCHEMA
    trusted_paths = (
        ledger_path.resolve() == DEFAULT_LEDGER.resolve()
        and policy_path.resolve() == DEFAULT_POLICY.resolve()
        and run_schema_path.resolve() == DEFAULT_RUN_SCHEMA.resolve()
    )
    if not trusted_paths and not allow_untrusted_inputs:
        raise LeaderboardInputError(
            "custom ledger, policy, or run schema requires --allow-untrusted-inputs"
        )
    ordered_tasks, profile_tasks, task_profile = _load_ledger(
        ledger_path,
        benchmark_id,
        benchmark_version,
        require_frozen=trusted_paths,
    )
    input_manifest, roster = _load_input(input_path, root, benchmark_id, benchmark_version)
    trusted_bindings = _trusted_task_bindings(task_profile) if trusted_paths else None
    records = _load_records(
        input_manifest,
        roster,
        root,
        task_profile,
        run_schema_path,
        policy,
        trusted_bindings,
    )
    roster_by_id = {model["model_id"]: model for model in roster["models"]}
    records_by_model: dict[str, list[dict[str, Any]]] = {model_id: [] for model_id in roster_by_id}
    for record in records:
        records_by_model[record["_model_id"]].append(record)
    entries = [
        _model_entry(
            roster_model,
            records_by_model[model_id],
            ordered_tasks,
            profile_tasks,
            task_profile,
            policy["coverage"]["confirmed_min_replicates_per_task"],
        )
        for model_id, roster_model in roster_by_id.items()
    ]
    entries.sort(key=lambda item: item["model_id"])
    overall = _ranking_view(entries, "overall")
    profile_views = {profile_id: _ranking_view(entries, profile_id) for profile_id in profile_tasks}
    all_run_metrics = [_run_metrics(record) for record in records]
    comparable_records = [record for record in records if record["_identity_resolved"]]
    comparable_metrics = [
        metrics
        for record, metrics in zip(records, all_run_metrics)
        if record["_identity_resolved"]
    ]
    aggregate = {
        "attempted_runs": len(records),
        "comparable_resolved_runs": len(comparable_records),
        "excluded_provider_or_identity_runs": len(records) - len(comparable_records),
        "full_contract_pass_runs": sum(item["full_contract_pass"] for item in comparable_metrics),
        "all_automatic_checks_pass_runs": sum(
            bool(metrics["automatic_check_statuses"])
            and all(status == "pass" for status in metrics["automatic_check_statuses"])
            for metrics in comparable_metrics
        ),
        "hard_failure_runs": sum(item["hard_failure"] for item in comparable_metrics),
        "invalid_output_runs": sum(item["invalid_output"] for item in comparable_metrics),
        "process_or_timeout_failures": sum(
            record["execution_status"] in {"failed", "timed_out"}
            for record in comparable_records
        ),
        "human_scores_assigned": any(item["human_score_available"] for item in comparable_metrics),
    }
    release_locks = sorted({record["release_lock_fingerprint"] for record in records})
    generated_at = input_manifest.get("generated_at") or roster.get("captured_at")
    if not isinstance(generated_at, str) or not generated_at:
        completed = [record.get("completed_at") for record in records if isinstance(record.get("completed_at"), str)]
        generated_at = max(completed) if completed else None
    any_human_scores = any(
        entry["metrics"]["human_quality_score"] is not None
        for entry in entries
    )
    all_profiles_have_confirmed = all(
        any(row["status"] == "confirmed" for row in view["ranked"])
        for view in profile_views.values()
    )
    output = {
        "schema_version": "leaderboard-v1",
        "benchmark_id": benchmark_id,
        "benchmark_version": benchmark_version,
        "policy_id": policy["policy_id"],
        "policy_version": policy["policy_version"],
        "input_snapshot_id": input_manifest["snapshot_id"],
        "roster_snapshot_id": roster["snapshot_id"],
        "generated_at": generated_at,
        **({"release_lock_fingerprint": release_locks[0]} if release_locks else {}),
        "scope": "benchmark-specific model leaderboard and routing aid",
        "models": entries,
        "aggregate": aggregate,
        "overall": overall,
        "profiles": profile_views,
        "publication": {
            "ranking_available": bool(overall["ranked"]),
            "score_publishable": bool(overall["ranked"]),
            "human_scores_assigned": any_human_scores,
            "routing_recommendation_allowed": all_profiles_have_confirmed,
            "reason": (
                "Every profile has a confirmed candidate."
                if all_profiles_have_confirmed
                else "Coverage or repeat confirmation is incomplete for at least one profile."
            ),
        },
        "input": {
            "roster_path": input_manifest["roster_path"],
            "selected_runs": [dict(item) for item in input_manifest["runs"]],
            "selected_run_count": len(input_manifest["runs"]),
        },
    }
    _validate_checked_schema(output, OUTPUT_SCHEMA, "leaderboard output")
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="root for relative evidence paths")
    parser.add_argument("--input", type=Path, required=True, help="leaderboard-input-v1 manifest")
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--run-schema", type=Path, default=ROOT / "schemas" / "task-run-record.schema.json")
    parser.add_argument(
        "--allow-untrusted-inputs",
        action="store_true",
        help="allow custom ledger, policy, or run schema paths for controlled testing",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        output = build_leaderboard(
            root=args.root.resolve(),
            input_path=args.input if args.input.is_absolute() else args.root / args.input,
            policy_path=args.policy if args.policy.is_absolute() else args.root / args.policy,
            ledger_path=args.ledger if args.ledger.is_absolute() else args.root / args.ledger,
            run_schema_path=args.run_schema if args.run_schema.is_absolute() else args.root / args.run_schema,
            allow_untrusted_inputs=args.allow_untrusted_inputs,
        )
        output_path = args.output if args.output.is_absolute() else args.root / args.output
        _write_json(output_path, output)
    except (LeaderboardInputError, OSError, ValueError) as exc:
        print(f"leaderboard build failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "benchmark_version": output["benchmark_version"],
        "input_snapshot_id": output["input_snapshot_id"],
        "overall_ranked": len(output["overall"]["ranked"]),
        "overall_unranked": len(output["overall"]["unranked"]),
        "overall_excluded": len(output["overall"]["excluded"]),
        "output": str(args.output),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
