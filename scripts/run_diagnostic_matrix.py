#!/usr/bin/env python3
"""Run the sealed, evidence-only 24-cell validity diagnostic.

This runner intentionally emits only cell execution evidence.  It has no
leaderboard, ranking, promotion, suitability, or routing output path.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    from run_leaderboard_matrix import (
        MatrixInputError,
        RUNNER,
        _load_json,
        _record_reference,
        _relative_path,
        _run_release_gate,
        _validate_cell_record,
        _write_json,
        _required_string,
        _safe_component,
        _run_id,
    )
    from validate_benchmark import validate_schema_instance
except ImportError:  # pragma: no cover - package-style import
    from scripts.run_leaderboard_matrix import (
        MatrixInputError,
        RUNNER,
        _load_json,
        _record_reference,
        _relative_path,
        _run_release_gate,
        _validate_cell_record,
        _write_json,
        _required_string,
        _safe_component,
        _run_id,
    )
    from scripts.validate_benchmark import validate_schema_instance


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_SCHEMA = ROOT / "schemas" / "diagnostic-manifest.schema.json"
ROSTER_SCHEMA = ROOT / "schemas" / "model-roster.schema.json"
LEDGER_PATH = ROOT / "data" / "task-ledger.json"
EXPECTED_CELL_COUNT = 24


def _validate_schema(value: Any, schema_path: Path, label: str) -> None:
    schema = _load_json(schema_path, f"{label} schema")
    if not isinstance(schema, dict):
        raise MatrixInputError(f"{label} schema must be an object")
    errors = validate_schema_instance(value, schema)
    if errors:
        raise MatrixInputError(f"{label} violates its schema: {'; '.join(errors[:4])}")


def _task_profile_map(ledger: dict[str, Any]) -> dict[str, str]:
    tasks = ledger.get("tasks")
    if not isinstance(tasks, list):
        raise MatrixInputError("ledger.tasks must be an array")
    result: dict[str, str] = {}
    for index, task in enumerate(tasks):
        if not isinstance(task, dict):
            raise MatrixInputError(f"ledger.tasks[{index}] must be an object")
        task_id = _required_string(task.get("id"), f"ledger.tasks[{index}].id")
        profile_id = _required_string(task.get("profile_id"), f"ledger.tasks[{index}].profile_id")
        if task_id in result:
            raise MatrixInputError(f"ledger contains duplicate task ID {task_id!r}")
        result[task_id] = profile_id
    return result


def build_diagnostic_plan(
    manifest: dict[str, Any],
    roster: dict[str, Any],
    ledger: dict[str, Any],
    output_root: Path,
) -> list[dict[str, str]]:
    """Resolve the manifest's exactly 24 evidence cells without adding cells."""
    _validate_schema(manifest, MANIFEST_SCHEMA, "diagnostic manifest")
    if len(manifest["cells"]) != EXPECTED_CELL_COUNT:
        raise MatrixInputError(f"diagnostic manifest must contain exactly {EXPECTED_CELL_COUNT} cells")
    task_profiles = _task_profile_map(ledger)
    roster_models = {
        model["model_id"]: model
        for model in roster.get("models", [])
        if isinstance(model, dict) and isinstance(model.get("model_id"), str)
    }
    seen_cells: set[str] = set()
    seen_runs: set[str] = set()
    plan: list[dict[str, str]] = []
    for index, item in enumerate(manifest["cells"]):
        cell_id = _required_string(item.get("cell_id"), f"diagnostic cells[{index}].cell_id")
        model_id = _required_string(item.get("model_id"), f"diagnostic cells[{index}].model_id")
        task_id = _required_string(item.get("task_id"), f"diagnostic cells[{index}].task_id")
        if cell_id in seen_cells:
            raise MatrixInputError(f"diagnostic manifest repeats cell_id {cell_id!r}")
        seen_cells.add(cell_id)
        model = roster_models.get(model_id)
        if model is None:
            raise MatrixInputError(f"diagnostic cell {cell_id!r} references unknown model {model_id!r}")
        if model.get("availability") != "eligible":
            raise MatrixInputError(f"diagnostic cell {cell_id!r} references an ineligible model")
        resolved = _required_string(model.get("resolved_model_id"), f"diagnostic model {model_id}.resolved_model_id")
        if resolved != model_id:
            raise MatrixInputError(f"diagnostic model {model_id!r} does not have a canonical resolved identity")
        profile_id = task_profiles.get(task_id)
        if profile_id is None:
            raise MatrixInputError(f"diagnostic cell {cell_id!r} references unknown task {task_id!r}")
        run_id = _run_id(manifest["diagnostic_id"], f"{cell_id}-{model_id}", task_id)
        if run_id in seen_runs:
            raise MatrixInputError(f"diagnostic plan generated duplicate run ID {run_id!r}")
        seen_runs.add(run_id)
        task_slug = task_id.lower()
        fixture_path = "fixtures/{}/input.json".format(task_slug)
        if task_id == "KODY-01":
            fixture_path = "fixtures/kody-01/request-packet.json"
        plan.append(
            {
                "cell_id": cell_id,
                "run_id": run_id,
                "model_id": model_id,
                "requested_model_id": _required_string(model.get("requested_model_id"), f"diagnostic model {model_id}.requested_model_id"),
                "resolved_model_id": resolved,
                "provider": _required_string(model.get("provider_requested"), f"diagnostic model {model_id}.provider_requested"),
                "provider_resolved": _required_string(model.get("provider_resolved"), f"diagnostic model {model_id}.provider_resolved"),
                "task_id": task_id,
                "profile_id": profile_id,
                "fixture_path": fixture_path,
                "prompt_path": f"fixtures/{task_slug}/prompt.txt",
                "output_root": output_root.as_posix(),
            }
        )
    return plan


def run_diagnostic(
    *,
    root: Path,
    manifest_path: Path,
    roster_path: Path,
    output_root: Path,
    reasoning: str = "medium",
    timeout_seconds: int = 600,
    dry_run: bool = False,
) -> dict[str, Any]:
    if timeout_seconds <= 0:
        raise MatrixInputError("timeout must be positive")
    root = root.resolve()
    manifest_path = manifest_path.resolve()
    roster_path = _relative_path(root, roster_path, "roster path")
    output_root = _relative_path(root, output_root, "output root")
    if output_root.exists() and any(output_root.iterdir()):
        raise MatrixInputError(f"output root is not empty: {output_root.relative_to(root)}")
    manifest = _load_json(manifest_path, "diagnostic manifest")
    roster = _load_json(roster_path, "model roster")
    ledger = _load_json(LEDGER_PATH, "benchmark ledger")
    if not isinstance(manifest, dict) or not isinstance(roster, dict) or not isinstance(ledger, dict):
        raise MatrixInputError("diagnostic manifest, roster, and ledger must be objects")
    _validate_schema(roster, ROSTER_SCHEMA, "model roster")
    if roster.get("benchmark_id") != manifest.get("benchmark_id") or roster.get("benchmark_version") != manifest.get("benchmark_version"):
        raise MatrixInputError("roster benchmark identity does not match the diagnostic manifest")
    plan = build_diagnostic_plan(manifest, roster, ledger, output_root.relative_to(root))
    if len(plan) != EXPECTED_CELL_COUNT:
        raise MatrixInputError("diagnostic plan did not close exactly 24 cells")
    if dry_run:
        return {"diagnostic_id": manifest["diagnostic_id"], "planned_cells": len(plan), "completed_cells": 0, "failed_launches": 0, "dry_run": True}
    _run_release_gate()
    output_root.mkdir(parents=True, exist_ok=True)
    completed = 0
    failed_launches = 0
    cells: list[dict[str, Any]] = []
    for cell in plan:
        run_dir = output_root / cell["run_id"]
        command = [
            sys.executable, str(RUNNER), "--task", cell["task_id"],
            "--fixture", str(root / cell["fixture_path"]),
            "--prompt", str(root / cell["prompt_path"]),
            "--output-root", str(output_root), "--run-id", cell["run_id"],
            "--model-requested", cell["requested_model_id"],
            "--model-resolved", cell["resolved_model_id"],
            "--provider", cell["provider"], "--reasoning", reasoning,
            "--timeout-seconds", str(timeout_seconds),
        ]
        import subprocess
        result = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)
        record_path = None
        status = None
        if (run_dir / "run-record.json").is_file():
            record_path = _record_reference(root, output_root, cell["run_id"])
            record = _load_json(run_dir / "run-record.json", f"diagnostic cell {cell['cell_id']} run record")
            _validate_cell_record(record, cell)
            status = record.get("status") if isinstance(record.get("status"), str) else None
            completed += 1
        else:
            failed_launches += 1
        cells.append({"cell_id": cell["cell_id"], "run_id": cell["run_id"], "task_id": cell["task_id"], "model_id": cell["model_id"], "returncode": result.returncode, "record_path": record_path, "status": status})
    summary = {"diagnostic_id": manifest["diagnostic_id"], "planned_cells": EXPECTED_CELL_COUNT, "completed_cells": completed, "failed_launches": failed_launches, "decision_surfaces": [], "cells": sorted(cells, key=lambda item: item["cell_id"]), "dry_run": False}
    _write_json(output_root / "diagnostic-summary.json", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--roster", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--reasoning", default="medium")
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        summary = run_diagnostic(root=args.root, manifest_path=args.manifest, roster_path=args.roster, output_root=args.output_root, reasoning=args.reasoning, timeout_seconds=args.timeout_seconds, dry_run=args.dry_run)
    except (MatrixInputError, OSError, ValueError) as exc:
        print(f"diagnostic failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["failed_launches"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
