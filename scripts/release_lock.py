"""Verify the independently pinned benchmark release artifact lock."""

from __future__ import annotations

import hashlib
import json
import posixpath
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "data" / "release-artifact-lock.json"
EXPECTED_RELEASE_LOCK_FINGERPRINT = "944782ff122eb1c57a9e3821286e2ce5c1a4286882a9434e03f4d0f3fd9db453"
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
)


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
        return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
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
    if lock.get("benchmark_version") != "0.2.0":
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
        _verify_entry(shared[key])
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
    }
