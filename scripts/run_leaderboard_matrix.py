#!/usr/bin/env python3
"""Run a deterministic, sequential model matrix through the single-cell harness."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    from validate_benchmark import validate_schema_instance
except ImportError:  # pragma: no cover - package-style import
    from scripts.validate_benchmark import validate_schema_instance


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run_task_model.py"
RELEASE_GATE = ROOT / "scripts" / "validate_benchmark_ready.py"
ROSTER_SCHEMA = ROOT / "schemas" / "model-roster.schema.json"
INPUT_SCHEMA = ROOT / "schemas" / "leaderboard-input.schema.json"
RUN_SCHEMA = ROOT / "schemas" / "task-run-record.schema.json"
UNRESOLVED_IDENTITY_VALUES = {"", "none", "unresolved"}


class MatrixInputError(ValueError):
    """Raised when a matrix cannot be planned or its evidence is unsafe."""


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise MatrixInputError(f"duplicate JSON object key {key!r}")
        value[key] = item
    return value


def _reject_nonfinite_json_constant(value: str) -> None:
    raise MatrixInputError(f"non-finite JSON number {value!r} is not supported")


def _load_json(path: Path, label: str) -> Any:
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_nonfinite_json_constant,
        )
    except MatrixInputError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise MatrixInputError(f"unable to read {label} ({type(exc).__name__})") from exc


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _required_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MatrixInputError(f"{label} must be a non-empty string")
    return value.strip()


def _relative_path(root: Path, value: Path | str, label: str) -> Path:
    candidate = value if isinstance(value, Path) else Path(value)
    resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise MatrixInputError(f"{label} escapes the repository root") from exc
    return resolved


def _safe_component(value: str) -> str:
    component = "".join(char if char.isalnum() or char in "_.-" else "-" for char in value)
    component = component.strip("-") or "item"
    return component


def _run_id(snapshot_id: str, model_id: str, task_id: str) -> str:
    model_digest = hashlib.sha256(model_id.encode("utf-8")).hexdigest()[:10]
    value = "-".join(
        (
            _safe_component(snapshot_id),
            _safe_component(model_id),
            model_digest,
            _safe_component(task_id.lower()),
        )
    )
    return value if value[0].isalnum() else f"run-{value}"


def _ordered_tasks(ledger: dict[str, Any]) -> list[dict[str, str]]:
    profiles = ledger.get("profiles")
    tasks = ledger.get("tasks")
    if not isinstance(profiles, list) or not isinstance(tasks, list):
        raise MatrixInputError("ledger profiles and tasks must be arrays")
    task_by_id: dict[str, dict[str, str]] = {}
    for index, task in enumerate(tasks):
        if not isinstance(task, dict):
            raise MatrixInputError(f"ledger.tasks[{index}] must be an object")
        task_id = _required_string(task.get("id"), f"ledger.tasks[{index}].id")
        profile_id = _required_string(task.get("profile_id"), f"ledger.tasks[{index}].profile_id")
        if task_id in task_by_id:
            raise MatrixInputError(f"ledger contains duplicate task ID {task_id!r}")
        task_by_id[task_id] = {"id": task_id, "profile_id": profile_id}
    ordered: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, profile in enumerate(profiles):
        if not isinstance(profile, dict):
            raise MatrixInputError(f"ledger.profiles[{index}] must be an object")
        profile_id = _required_string(profile.get("id"), f"ledger.profiles[{index}].id")
        task_ids = profile.get("task_ids")
        if not isinstance(task_ids, list):
            raise MatrixInputError(f"ledger.profiles[{index}].task_ids must be an array")
        for task_id_value in task_ids:
            task_id = _required_string(task_id_value, f"ledger profile {profile_id}.task_ids item")
            if task_id in seen:
                raise MatrixInputError(f"ledger repeats task ID {task_id!r}")
            task = task_by_id.get(task_id)
            if task is None or task["profile_id"] != profile_id:
                raise MatrixInputError(f"ledger task/profile mapping is inconsistent for {task_id!r}")
            seen.add(task_id)
            ordered.append(task)
    if seen != set(task_by_id):
        raise MatrixInputError("ledger task records and profile task lists disagree")
    return ordered


def _eligible_models(roster: dict[str, Any]) -> list[dict[str, str]]:
    models = roster.get("models")
    if not isinstance(models, list) or not models:
        raise MatrixInputError("roster.models must be a non-empty array")
    eligible: list[dict[str, str]] = []
    seen: set[str] = set()
    seen_requested: set[str] = set()
    for index, model in enumerate(models):
        if not isinstance(model, dict):
            raise MatrixInputError(f"roster.models[{index}] must be an object")
        model_id = _required_string(model.get("model_id"), f"roster.models[{index}].model_id")
        if model_id in seen:
            raise MatrixInputError(f"roster contains duplicate model ID {model_id!r}")
        seen.add(model_id)
        requested = _required_string(model.get("requested_model_id"), f"roster.models[{index}].requested_model_id")
        if requested in seen_requested:
            raise MatrixInputError(f"roster contains duplicate requested model ID {requested!r}")
        seen_requested.add(requested)
        availability = _required_string(model.get("availability"), f"roster.models[{index}].availability")
        if availability != "eligible":
            continue
        resolved = _required_string(model.get("resolved_model_id"), f"roster.models[{index}].resolved_model_id")
        provider = _required_string(model.get("provider_requested"), f"roster.models[{index}].provider_requested")
        provider_resolved = _required_string(
            model.get("provider_resolved"), f"roster.models[{index}].provider_resolved"
        )
        if model_id != resolved:
            raise MatrixInputError(f"eligible roster model {model_id!r} lacks a canonical resolved identity")
        eligible.append(
            {
                "model_id": model_id,
                "requested_model_id": requested,
                "resolved_model_id": resolved,
                "provider": provider,
                "provider_resolved": provider_resolved,
            }
        )
    return sorted(eligible, key=lambda item: item["model_id"])


def build_matrix_plan(
    roster: dict[str, Any],
    ledger: dict[str, Any],
    snapshot_id: str,
    output_root: Path,
    *,
    task_inputs: dict[str, dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    """Return eligible model/task cells in stable roster-then-ledger order."""
    snapshot_id = _required_string(snapshot_id, "snapshot_id")
    _required_string(roster.get("provider"), "roster.provider")
    tasks = _ordered_tasks(ledger)
    task_inputs = task_inputs or {}
    plan: list[dict[str, str]] = []
    seen_run_ids: set[str] = set()
    for model in _eligible_models(roster):
        for task in tasks:
            task_slug = task["id"].lower()
            task_input = task_inputs.get(task["id"], {})
            if not isinstance(task_input, dict):
                raise MatrixInputError(f"task input metadata for {task['id']!r} must be an object")
            fixture_path = _required_string(
                task_input.get("fixture_path", f"fixtures/{task_slug}/input.json"),
                f"task input {task['id']}.fixture_path",
            )
            prompt_path = _required_string(
                task_input.get("prompt_path", f"fixtures/{task_slug}/prompt.txt"),
                f"task input {task['id']}.prompt_path",
            )
            cell = {
                "model_id": model["model_id"],
                "requested_model_id": model["requested_model_id"],
                "resolved_model_id": model["resolved_model_id"],
                "provider": model["provider"],
                "provider_resolved": model["provider_resolved"],
                "task_id": task["id"],
                "profile_id": task["profile_id"],
                "fixture_path": fixture_path,
                "prompt_path": prompt_path,
                "output_root": output_root.as_posix(),
                "run_id": _run_id(snapshot_id, model["model_id"], task["id"]),
            }
            if cell["run_id"] in seen_run_ids:
                raise MatrixInputError(f"matrix plan generated a duplicate run ID {cell['run_id']!r}")
            seen_run_ids.add(cell["run_id"])
            plan.append(cell)
    return plan


def build_input_manifest(
    *,
    benchmark_id: str,
    benchmark_version: str,
    snapshot_id: str,
    roster_path: str,
    completed_cells: Iterable[dict[str, str]],
) -> dict[str, Any]:
    """Build the minimal strict input manifest from verified run records."""
    completed = []
    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    for item in completed_cells:
        run_id = _required_string(item.get("run_id"), "completed cell run_id")
        record_path = _required_string(item.get("record_path"), "completed cell record_path")
        if run_id in seen_ids:
            raise MatrixInputError(f"completed cells contain duplicate run ID {run_id!r}")
        if record_path in seen_paths:
            raise MatrixInputError(f"completed cells contain duplicate record path {record_path!r}")
        seen_ids.add(run_id)
        seen_paths.add(record_path)
        completed.append({"run_id": run_id, "record_path": record_path})
    completed.sort(key=lambda item: item["run_id"])
    return {
        "schema_version": "leaderboard-input-v1",
        "benchmark_id": _required_string(benchmark_id, "benchmark_id"),
        "benchmark_version": _required_string(benchmark_version, "benchmark_version"),
        "snapshot_id": _required_string(snapshot_id, "snapshot_id"),
        "roster_path": _required_string(roster_path, "roster_path"),
        "runs": completed,
    }


def _task_input_paths(root: Path, ledger: dict[str, Any]) -> dict[str, dict[str, str]]:
    task_inputs: dict[str, dict[str, str]] = {}
    for task in _ordered_tasks(ledger):
        task_id = task["id"]
        package = root / "fixtures" / task_id.lower()
        manifest = _load_json(package / "manifest.json", f"{task_id} manifest")
        if not isinstance(manifest, dict):
            raise MatrixInputError(f"{task_id} manifest must be an object")
        fixture_meta = manifest.get("fixture")
        prompt_meta = manifest.get("prompt")
        if not isinstance(fixture_meta, dict) or not isinstance(prompt_meta, dict):
            raise MatrixInputError(f"{task_id} manifest has incomplete input metadata")
        paths: dict[str, str] = {}
        for name, metadata in (("fixture_path", fixture_meta), ("prompt_path", prompt_meta)):
            relative = _required_string(metadata.get("path"), f"{task_id} manifest {name}")
            candidate = _relative_path(package, relative, f"{task_id} manifest {name}")
            try:
                candidate.relative_to(package.resolve())
            except ValueError as exc:
                raise MatrixInputError(f"{task_id} manifest {name} escapes its fixture package") from exc
            paths[name] = (Path("fixtures") / task_id.lower() / relative).as_posix()
        task_inputs[task_id] = paths
    return task_inputs


def _validate_schema(value: Any, schema_path: Path, label: str) -> None:
    schema = _load_json(schema_path, f"{label} schema")
    if not isinstance(schema, dict):
        raise MatrixInputError(f"{label} schema must be an object")
    errors = validate_schema_instance(value, schema)
    if errors:
        raise MatrixInputError(f"{label} violates its schema: {'; '.join(errors[:4])}")


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _run_release_gate() -> None:
    result = subprocess.run(
        [sys.executable, str(RELEASE_GATE)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise MatrixInputError("benchmark-ready release gate failed; no model matrix was started")


def _record_reference(root: Path, output_root: Path, run_id: str) -> str:
    record_path = output_root / run_id / "run-record.json"
    if not record_path.is_file():
        raise MatrixInputError(f"cell {run_id!r} completed without a run-record.json")
    return record_path.resolve().relative_to(root.resolve()).as_posix()


def _validate_cell_record(record: Any, cell: dict[str, str]) -> None:
    """Ensure a written run record still belongs to its planned matrix cell."""
    if not isinstance(record, dict):
        raise MatrixInputError(f"cell {cell['run_id']!r} run record must be an object")
    _validate_schema(record, RUN_SCHEMA, f"cell {cell['run_id']} run record")
    expected = {
        "run_id": cell["run_id"],
        "benchmark_id": "agent-profile-benchmark",
        "benchmark_version": "0.2.0",
        "task_id": cell["task_id"],
        "profile_id": cell["profile_id"],
        "harness": "hermes-oneshot",
        "condition": "model-calibration",
        "model_requested": cell["requested_model_id"],
        "provider_requested": cell["provider"],
    }
    for field, expected_value in expected.items():
        if record.get(field) != expected_value:
            raise MatrixInputError(
                f"cell {cell['run_id']!r} run record {field} does not match the planned cell"
            )
    for field, expected_value in (
        ("model_resolved", cell["resolved_model_id"]),
        ("provider_resolved", cell["provider_resolved"]),
    ):
        actual = record.get(field)
        if actual not in UNRESOLVED_IDENTITY_VALUES and actual != expected_value:
            raise MatrixInputError(
                f"cell {cell['run_id']!r} run record {field} does not match the planned cell"
            )


def run_matrix(
    *,
    root: Path,
    roster_path: Path,
    output_root: Path,
    snapshot_id: str,
    reasoning: str = "medium",
    timeout_seconds: int = 600,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run all eligible cells sequentially and emit a self-contained input manifest."""
    if timeout_seconds <= 0:
        raise MatrixInputError("timeout must be positive")
    root = root.resolve()
    roster_path = _relative_path(root, roster_path, "roster path")
    output_root = _relative_path(root, output_root, "output root")
    if output_root.exists() and any(output_root.iterdir()):
        raise MatrixInputError(f"output root is not empty: {output_root.relative_to(root)}")
    ledger = _load_json(ROOT / "data" / "task-ledger.json", "benchmark ledger")
    roster = _load_json(roster_path, "model roster")
    if not isinstance(ledger, dict) or not isinstance(roster, dict):
        raise MatrixInputError("ledger and roster must be objects")
    _validate_schema(roster, ROSTER_SCHEMA, "model roster")
    benchmark_id = _required_string(ledger.get("benchmark_id"), "ledger.benchmark_id")
    benchmark_version = _required_string(ledger.get("benchmark_version"), "ledger.benchmark_version")
    if roster.get("benchmark_id") != benchmark_id or roster.get("benchmark_version") != benchmark_version:
        raise MatrixInputError("roster benchmark identity does not match the frozen ledger")
    output_root.mkdir(parents=True, exist_ok=True)
    copied_roster = output_root / "roster.json"
    if roster_path.resolve() != copied_roster.resolve():
        shutil.copyfile(roster_path, copied_roster)
    relative_output_root = output_root.relative_to(root).as_posix()
    task_inputs = _task_input_paths(root, ledger)
    plan = build_matrix_plan(
        roster,
        ledger,
        snapshot_id,
        Path(relative_output_root),
        task_inputs=task_inputs,
    )
    if dry_run:
        return {
            "snapshot_id": snapshot_id,
            "planned_cells": len(plan),
            "completed_cells": 0,
            "failed_launches": 0,
            "input_manifest": None,
            "dry_run": True,
        }
    _run_release_gate()
    completed_cells: list[dict[str, str]] = []
    cell_results: list[dict[str, Any]] = []
    for cell in plan:
        run_dir = output_root / cell["run_id"]
        command = [
            sys.executable,
            str(RUNNER),
            "--task",
            cell["task_id"],
            "--fixture",
            str(root / cell["fixture_path"]),
            "--prompt",
            str(root / cell["prompt_path"]),
            "--output-root",
            str(output_root),
            "--run-id",
            cell["run_id"],
            "--model-requested",
            cell["requested_model_id"],
            "--model-resolved",
            cell["resolved_model_id"],
            "--provider",
            cell["provider"],
            "--reasoning",
            reasoning,
            "--timeout-seconds",
            str(timeout_seconds),
        ]
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
        record_path: str | None = None
        status: str | None = None
        if (run_dir / "run-record.json").is_file():
            record_path = _record_reference(root, output_root, cell["run_id"])
            record = _load_json(run_dir / "run-record.json", f"cell {cell['run_id']} run record")
            _validate_cell_record(record, cell)
            status = record.get("status") if isinstance(record.get("status"), str) else None
            completed_cells.append({"run_id": cell["run_id"], "record_path": record_path})
        cell_results.append(
            {
                "run_id": cell["run_id"],
                "model_id": cell["model_id"],
                "task_id": cell["task_id"],
                "returncode": result.returncode,
                "record_path": record_path,
                "status": status,
                "launch_failed": record_path is None,
            }
        )
    roster_reference = copied_roster.relative_to(root).as_posix()
    input_manifest = build_input_manifest(
        benchmark_id=benchmark_id,
        benchmark_version=benchmark_version,
        snapshot_id=snapshot_id,
        roster_path=roster_reference,
        completed_cells=completed_cells,
    )
    _validate_schema(input_manifest, INPUT_SCHEMA, "leaderboard input manifest")
    input_path = output_root / "leaderboard-input.json"
    _write_json(input_path, input_manifest)
    summary = {
        "snapshot_id": snapshot_id,
        "planned_cells": len(plan),
        "completed_cells": len(completed_cells),
        "failed_launches": sum(item["launch_failed"] for item in cell_results),
        "input_manifest": input_path.relative_to(root).as_posix(),
        "dry_run": False,
    }
    _write_json(
        output_root / "matrix-run.json",
        {
            "schema_version": "matrix-run-v1",
            "snapshot_id": snapshot_id,
            "reasoning": reasoning,
            "timeout_seconds": timeout_seconds,
            "planned_cells": len(plan),
            "completed_cells": len(completed_cells),
            "failed_launches": summary["failed_launches"],
            "cells": sorted(cell_results, key=lambda item: item["run_id"]),
            "input_manifest": input_path.relative_to(root).as_posix(),
            "generated_at": _utc_timestamp(),
        },
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--roster", type=Path, required=True, help="model-roster-v1 snapshot")
    parser.add_argument("--output-root", type=Path, required=True, help="new evidence root")
    parser.add_argument("--snapshot-id", required=True)
    parser.add_argument("--reasoning", default="medium")
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        summary = run_matrix(
            root=args.root,
            roster_path=args.roster,
            output_root=args.output_root,
            snapshot_id=args.snapshot_id,
            reasoning=args.reasoning,
            timeout_seconds=args.timeout_seconds,
            dry_run=args.dry_run,
        )
    except (MatrixInputError, OSError, ValueError) as exc:
        print(f"model matrix failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["failed_launches"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
