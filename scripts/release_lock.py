"""Verify the independently pinned benchmark release artifact lock."""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "data" / "release-artifact-lock.json"
EXPECTED_RELEASE_LOCK_FINGERPRINT = "339138b35a69492d13b9410c19d7ccfe8b0f2846ef9308226a288adfe4c9d789"
LOCK_VERSION = "1"
TASK_ARTIFACT_KEYS = (
    "manifest",
    "fixture",
    "prompt",
    "oracle",
    "output_schema",
    "evaluator",
    "run_record_schema",
    "known_good",
    "known_bad",
    "release_gate",
)
SHARED_ARTIFACT_KEYS = (
    "ledger",
    "release_lock_schema",
    "contract_schema",
    "evaluator_task",
    "evaluator_kody01",
    "run_record_schema",
    "kody_run_schema",
    "replay_task",
    "replay_kody01",
    "model_runner",
    "kody_model_runner",
    "hermes_adapter",
    "release_validator",
    "kody_validator",
    "leaderboard_builder",
    "leaderboard_output_schema",
    "leaderboard_renderer",
    "validity_gate",
    "validity_probe_schema",
    "validity_probes",
    "release_lock",
    "benchmark_validator",
    "matrix_runner",
    "leaderboard_input_schema",
    "model_roster_schema",
    "leaderboard_policy_schema",
    "leaderboard_policy",
    "aggregate_validator",
    "diagnostic_runner",
    "diagnostic_manifest_schema",
    "historical_snapshot_manifest",
)
SHARED_ARTIFACT_PATHS = {
    "ledger": "data/task-ledger.json",
    "release_lock_schema": "schemas/release-artifact-lock.schema.json",
    "contract_schema": "schemas/task-contract.schema.json",
    "evaluator_task": "scripts/evaluate_task.py",
    "evaluator_kody01": "scripts/evaluate_kody01.py",
    "run_record_schema": "schemas/task-run-record.schema.json",
    "kody_run_schema": "schemas/kody-01-run-record.schema.json",
    "replay_task": "scripts/replay_task.py",
    "replay_kody01": "scripts/replay_kody01.py",
    "model_runner": "scripts/run_task_model.py",
    "kody_model_runner": "scripts/run_kody01_model.py",
    "hermes_adapter": "scripts/hermes_no_tools.py",
    "release_validator": "scripts/validate_benchmark_ready.py",
    "kody_validator": "scripts/validate_kody01.py",
    "leaderboard_builder": "scripts/build_leaderboard.py",
    "leaderboard_output_schema": "schemas/leaderboard-output.schema.json",
    "leaderboard_renderer": "scripts/render_leaderboard_html.py",
    "validity_gate": "scripts/validate_validity_probes.py",
    "validity_probe_schema": "schemas/validity-probes.schema.json",
    "validity_probes": "data/validity-probes.json",
    "release_lock": "scripts/release_lock.py",
    "benchmark_validator": "scripts/validate_benchmark.py",
    "matrix_runner": "scripts/run_leaderboard_matrix.py",
    "leaderboard_input_schema": "schemas/leaderboard-input.schema.json",
    "model_roster_schema": "schemas/model-roster.schema.json",
    "leaderboard_policy_schema": "schemas/leaderboard-policy.schema.json",
    "leaderboard_policy": "data/leaderboard-policy.json",
    "aggregate_validator": "scripts/leaderboard_aggregate.py",
    "diagnostic_runner": "scripts/run_diagnostic_matrix.py",
    "diagnostic_manifest_schema": "schemas/diagnostic-manifest.schema.json",
    "historical_snapshot_manifest": "data/historical-v03-snapshot-manifest.json",
}


class ReleaseLockError(ValueError):
    """Raised when the trusted release lock cannot be verified."""


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ReleaseLockError(f"duplicate JSON object key {key!r}")
        value[key] = item
    return value


def _reject_nonfinite_json_constant(value: str) -> None:
    raise ReleaseLockError(f"non-finite JSON number {value!r} is not supported")


def _load_json(path: Path) -> Any:
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_nonfinite_json_constant,
        )
    except ReleaseLockError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseLockError(f"unable to read release lock ({type(exc).__name__})") from exc


def _sha256(path: Path) -> str:
    try:
        content = path.read_bytes()
        if path.resolve() == Path(__file__).resolve():
            content = re.sub(
                rb'(EXPECTED_RELEASE_LOCK_FINGERPRINT\s*=\s*)"[0-9a-f_]+"',
                rb'\1"__sealed__"',
                content,
            )
        return "sha256:" + hashlib.sha256(content).hexdigest()
    except OSError as exc:
        raise ReleaseLockError(f"unable to read locked artifact {path.name} ({type(exc).__name__})") from exc


def _verify_entry(entry: Any, expected_path: str | None = None) -> str:
    if not isinstance(entry, dict):
        raise ReleaseLockError("release lock artifact entry must be an object")
    path_value = entry.get("path")
    fingerprint = entry.get("sha256")
    if not isinstance(path_value, str) or not path_value or Path(path_value).is_absolute():
        raise ReleaseLockError("release lock artifact path must be relative")
    resolved = (ROOT / path_value).resolve()
    try:
        resolved.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise ReleaseLockError(f"release lock artifact path escapes the repository: {path_value!r}") from exc
    if expected_path is not None and path_value != expected_path:
        raise ReleaseLockError(
            f"release lock artifact path {path_value!r} does not match {expected_path!r}"
        )
    if not isinstance(fingerprint, str) or fingerprint != _sha256(resolved):
        raise ReleaseLockError(f"release lock fingerprint does not match {path_value!r}")
    return fingerprint


def load_release_lock() -> dict[str, Any]:
    lock = _load_json(LOCK_PATH)
    if not isinstance(lock, dict):
        raise ReleaseLockError("release lock must be an object")
    if lock.get("$schema") != "../schemas/release-artifact-lock.schema.json":
        raise ReleaseLockError("release lock schema pointer is invalid")
    if lock.get("benchmark_id") != "agent-profile-benchmark":
        raise ReleaseLockError("release lock benchmark identity is invalid")
    if lock.get("benchmark_version") != "0.4.1":
        raise ReleaseLockError("release lock benchmark version is invalid")
    if lock.get("lock_version") != LOCK_VERSION:
        raise ReleaseLockError("release lock version is invalid")
    if EXPECTED_RELEASE_LOCK_FINGERPRINT == "__pending__":
        raise ReleaseLockError("release lock fingerprint has not been sealed")
    actual = hashlib.sha256(LOCK_PATH.read_bytes()).hexdigest()
    if actual != EXPECTED_RELEASE_LOCK_FINGERPRINT:
        raise ReleaseLockError("release lock content does not match its sealed fingerprint")
    shared = lock.get("shared")
    tasks = lock.get("tasks")
    if not isinstance(shared, dict) or not isinstance(tasks, dict):
        raise ReleaseLockError("release lock must define shared and task artifacts")
    for key in SHARED_ARTIFACT_KEYS:
        if key not in shared:
            raise ReleaseLockError(f"release lock is missing shared artifact {key!r}")
        _verify_entry(shared[key], SHARED_ARTIFACT_PATHS[key])
    return lock


def expected_manifest_paths(task_id: str) -> dict[str, str]:
    """Return canonical manifest bindings for one frozen task."""
    slug = task_id.lower()
    return {
        "fixture": "request-packet.json" if task_id == "KODY-01" else "input.json",
        "prompt": "prompt.txt",
        "oracle": f"../../oracles/{slug}.json",
        "output_schema": f"../../schemas/{slug}-output.schema.json",
        "run_record_schema": "../../schemas/task-run-record.schema.json",
        "evaluator": "../../scripts/evaluate_kody01.py"
        if task_id == "KODY-01"
        else "../../scripts/evaluate_task.py",
        "release_gate": "../../scripts/validate_benchmark_ready.py",
    }


def expected_release_artifact_paths(task_id: str) -> dict[str, str]:
    package = f"fixtures/{task_id.lower()}"
    manifest = expected_manifest_paths(task_id)
    def package_path(relative_path: str) -> str:
        return posixpath.normpath(posixpath.join(package, relative_path))

    return {
        "manifest": f"{package}/manifest.json",
        "fixture": f"{package}/{manifest['fixture']}",
        "prompt": f"{package}/{manifest['prompt']}",
        "oracle": package_path(manifest["oracle"]),
        "output_schema": package_path(manifest["output_schema"]),
        "run_record_schema": package_path(manifest["run_record_schema"]),
        "evaluator": package_path(manifest["evaluator"]),
        "known_good": f"{package}/controls/known-good.json",
        "known_bad": f"{package}/controls/known-bad.json",
        "release_gate": package_path(manifest["release_gate"]),
    }


def verify_task_release_artifacts(task_id: str, harness: str) -> dict[str, str]:
    lock = load_release_lock()
    tasks = lock["tasks"]
    task = tasks.get(task_id)
    if not isinstance(task, dict):
        raise ReleaseLockError(f"release lock has no task {task_id!r}")
    artifacts = task.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ReleaseLockError(f"release lock task {task_id!r} has no artifacts")
    expected_paths = expected_release_artifact_paths(task_id)
    fingerprints: dict[str, str] = {}
    for key in TASK_ARTIFACT_KEYS:
        fingerprints[key] = _verify_entry(artifacts.get(key), expected_paths[key])
    manifest = _load_json(ROOT / expected_paths["manifest"])
    if not isinstance(manifest, dict):
        raise ReleaseLockError(f"release lock task {task_id!r} manifest must be an object")
    if manifest.get("task_id") != task_id or manifest.get("benchmark_version") != "0.4.1":
        raise ReleaseLockError(f"release lock task {task_id!r} manifest identity is invalid")
    evaluator = manifest.get("evaluator")
    expected_evaluator_version = "kody-01-oracle-v2" if task_id == "KODY-01" else "task-oracle-v3"
    if not isinstance(evaluator, dict) or evaluator.get("version") != expected_evaluator_version:
        raise ReleaseLockError(f"release lock task {task_id!r} evaluator version is not sealed")
    if evaluator.get("path") != expected_manifest_paths(task_id)["evaluator"]:
        raise ReleaseLockError(f"release lock task {task_id!r} evaluator path is not canonical")
    fixture_id = task.get("fixture_id")
    fixture_version = task.get("fixture_version")
    if not isinstance(fixture_id, str) or not fixture_id or not isinstance(fixture_version, str) or not fixture_version:
        raise ReleaseLockError(f"release lock task {task_id!r} has invalid fixture identity")
    harness_key = "hermes_adapter" if harness == "hermes-oneshot" else "replay_task"
    harness_fingerprint = _verify_entry(lock["shared"][harness_key])
    ledger_fingerprint = _verify_entry(lock["shared"]["ledger"])
    return {
        "release_lock_fingerprint": "sha256:" + EXPECTED_RELEASE_LOCK_FINGERPRINT,
        "ledger_fingerprint": ledger_fingerprint,
        "manifest_fingerprint": fingerprints["manifest"],
        "oracle_fingerprint": fingerprints["oracle"],
        "output_schema_fingerprint": fingerprints["output_schema"],
        "evaluator_fingerprint": fingerprints["evaluator"],
        "run_record_schema_fingerprint": fingerprints["run_record_schema"],
        "harness_fingerprint": harness_fingerprint,
        "fixture_id": fixture_id,
        "fixture_version": fixture_version,
        "evaluator_version": expected_evaluator_version,
    }
