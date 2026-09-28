#!/usr/bin/env python3
"""Build a separate tolerant/objective analysis from frozen model evidence.

This diagnostic never rewrites run records or the strict leaderboard.
It re-reads immutable raw responses, conservatively recovers eligible JSON
objects, and evaluates the recovered object with the frozen task evaluator.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from pathlib import Path
from typing import Any, Iterable

try:
    from build_leaderboard import (
        DEFAULT_LEDGER,
        DEFAULT_POLICY,
        DEFAULT_RUN_SCHEMA,
        LeaderboardInputError,
        _load_input,
        _load_json,
        _load_ledger,
        _load_records,
        _required_string,
        _trusted_task_bindings,
        _validate_policy,
        _validate_checked_schema,
    )
    from evaluate_task import evaluate_task
    from release_lock import expected_release_artifact_paths
    from tolerant_output import PARSER_VERSION, parse_tolerant_output
except ImportError:  # pragma: no cover - package-style imports
    from scripts.build_leaderboard import (
        DEFAULT_LEDGER,
        DEFAULT_POLICY,
        DEFAULT_RUN_SCHEMA,
        LeaderboardInputError,
        _load_input,
        _load_json,
        _load_ledger,
        _load_records,
        _required_string,
        _trusted_task_bindings,
        _validate_policy,
        _validate_checked_schema,
    )
    from scripts.evaluate_task import evaluate_task
    from scripts.release_lock import expected_release_artifact_paths
    from scripts.tolerant_output import PARSER_VERSION, parse_tolerant_output


ROOT = Path(__file__).resolve().parents[1]
REPORT_SCHEMA = ROOT / "schemas" / "tolerant-analysis.schema.json"


class TolerantAnalysisError(ValueError):
    """Raised when tolerant analysis inputs cannot be trusted."""


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _mean(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 6)


def _median(values: Iterable[int]) -> int | float | None:
    values = list(values)
    if not values:
        return None
    value = statistics.median(values)
    return int(value) if float(value).is_integer() else round(value, 3)


def _status_list(evaluation: dict[str, Any] | None) -> list[str]:
    checks = evaluation.get("automatic_checks") if isinstance(evaluation, dict) else None
    if not isinstance(checks, list):
        return []
    return [
        item["status"]
        for item in checks
        if isinstance(item, dict) and isinstance(item.get("status"), str)
    ]


def _failure_ids(evaluation: dict[str, Any] | None) -> list[str]:
    failures = evaluation.get("hard_failures") if isinstance(evaluation, dict) else None
    if not isinstance(failures, list):
        return []
    return sorted(
        item["id"]
        for item in failures
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    )


def _execution_eligible(record: dict[str, Any]) -> bool:
    """Exclude provider, identity, process, and unverified execution failures."""
    return bool(
        record.get("_identity_resolved")
        and record.get("resolution_status") == "resolved"
        and record.get("execution_status") == "completed"
        and record.get("failure_class") == "none"
    )


def _strict_objective_pass(record: dict[str, Any], strict_contract_valid: bool) -> bool:
    checks = record.get("automatic_checks")
    statuses = [item.get("status") for item in checks if isinstance(item, dict)] if isinstance(checks, list) else []
    failures = record.get("hard_failures")
    return bool(
        _execution_eligible(record)
        and strict_contract_valid
        and record.get("status") == "passed"
        and not failures
        and statuses
        and all(status == "pass" for status in statuses)
    )


def _evaluate_recovered(
    task_id: str,
    fixture: Any,
    candidate: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    try:
        return evaluate_task(task_id, fixture, candidate, model_output=True), None
    except Exception as exc:  # evaluator failures are visible, never converted to a pass
        return None, f"evaluator raised {type(exc).__name__}: {exc}"


def _analyse_run(
    record: dict[str, Any],
    raw_path: Path,
    fixture: Any,
) -> dict[str, Any]:
    raw = raw_path.read_bytes()
    parsed = parse_tolerant_output(raw)
    eligible = _execution_eligible(record)
    strict_contract_valid = parsed.strict_contract_valid
    strict_objective_pass = _strict_objective_pass(record, strict_contract_valid)
    evaluation: dict[str, Any] | None = None
    evaluation_error: str | None = None
    evaluation_status = "not_evaluated"
    statuses: list[str] = []
    failures: list[str] = []
    if parsed.recovered and eligible and parsed.candidate is not None:
        evaluation, evaluation_error = _evaluate_recovered(record["task_id"], fixture, parsed.candidate)
        if evaluation is not None:
            evaluation_status = str(evaluation.get("status", "blocked"))
            statuses = _status_list(evaluation)
            failures = _failure_ids(evaluation)
        else:
            evaluation_status = "blocked"
    blocked = not statuses or "blocked" in statuses or evaluation_status == "blocked"
    judgeable = bool(parsed.recovered and eligible and evaluation is not None and not blocked)
    automatic_pass = bool(judgeable and statuses and all(status == "pass" for status in statuses))
    objective_pass = bool(automatic_pass and evaluation_status == "passed" and not failures)
    return {
        "run_id": record["run_id"],
        "record_path": record["_record_path"],
        "raw_output_reference": record["_raw_output_reference"],
        "raw_output_fingerprint": record["raw_output_fingerprint"],
        "model_id": record["_model_id"],
        "task_id": record["task_id"],
        "profile_id": record["profile_id"],
        "execution_eligible": eligible,
        "strict_record_output_parse_status": record.get("output_parse_status"),
        "strict_contract_valid": strict_contract_valid,
        "strict_comparable": bool(record.get("_comparable")),
        "strict_objective_pass": strict_objective_pass,
        "recovery": parsed.as_dict(),
        "tolerant_recoverable": bool(parsed.recovered),
        "tolerant_judgeable": judgeable,
        "tolerant_evaluation_status": evaluation_status,
        "tolerant_automatic_check_pass": automatic_pass,
        "tolerant_objective_pass": objective_pass,
        "tolerant_hard_failure_ids": failures,
        "tolerant_automatic_check_statuses": statuses,
        "evaluation_error": evaluation_error or "",
        "latency_ms": record.get("latency_ms") if isinstance(record.get("latency_ms"), int) else None,
    }


def _metrics(runs: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = [run for run in runs if run["execution_eligible"]]
    judgeable = [run for run in eligible if run["tolerant_judgeable"]]
    latencies = [run["latency_ms"] for run in eligible if isinstance(run["latency_ms"], int)]
    strict_passes = sum(run["strict_objective_pass"] for run in eligible)
    strict_comparable = sum(run["strict_comparable"] for run in eligible)
    tolerant_recoverable = sum(run["tolerant_recoverable"] for run in eligible)
    tolerant_passes = sum(run["tolerant_objective_pass"] for run in eligible)
    return {
        "attempted_runs": len(runs),
        "execution_eligible_runs": len(eligible),
        "strict_comparable_runs": strict_comparable,
        "strict_contract_valid_runs": sum(run["strict_contract_valid"] for run in eligible),
        "tolerant_recoverable_runs": tolerant_recoverable,
        "tolerant_judgeable_runs": len(judgeable),
        "strict_objective_pass_runs": strict_passes,
        "tolerant_automatic_check_pass_runs": sum(run["tolerant_automatic_check_pass"] for run in eligible),
        "tolerant_objective_pass_runs": tolerant_passes,
        "format_only_recovery_pass_runs": sum(
            run["tolerant_objective_pass"] and not run["strict_objective_pass"] for run in eligible
        ),
        "tolerant_hard_failure_runs": sum(bool(run["tolerant_hard_failure_ids"]) for run in judgeable),
        "strict_contract_valid_rate_all_eligible": _mean(
            sum(run["strict_contract_valid"] for run in eligible), len(eligible)
        ),
        "strict_objective_pass_rate_all_eligible": _mean(strict_passes, len(eligible)),
        "strict_objective_pass_rate_among_strict_comparable": _mean(strict_passes, strict_comparable),
        "tolerant_recovery_rate_all_eligible": _mean(tolerant_recoverable, len(eligible)),
        "tolerant_automatic_check_pass_rate_all_eligible": _mean(
            sum(run["tolerant_automatic_check_pass"] for run in eligible), len(eligible)
        ),
        "tolerant_objective_pass_rate_all_eligible": _mean(tolerant_passes, len(eligible)),
        "tolerant_objective_pass_rate_among_tolerant_recoverable": _mean(
            tolerant_passes, tolerant_recoverable
        ),
        "tolerant_objective_pass_rate_among_judgeable": _mean(
            sum(run["tolerant_objective_pass"] for run in judgeable), len(judgeable)
        ),
        "tolerant_automatic_check_pass_rate_among_judgeable": _mean(
            sum(run["tolerant_automatic_check_pass"] for run in judgeable), len(judgeable)
        ),
        "median_latency_ms": _median(latencies),
    }


def _task_cell(task_id: str, runs: list[dict[str, Any]]) -> dict[str, Any]:
    metrics = _metrics(runs)
    return {
        "task_id": task_id,
        "metrics": metrics,
        "coverage": {
            "tolerant_judgeable": metrics["tolerant_judgeable_runs"] > 0,
            "strict_comparable": metrics["strict_comparable_runs"] > 0,
        },
    }


def _model_entry(
    roster_model: dict[str, Any],
    runs: list[dict[str, Any]],
    ordered_tasks: list[str],
    task_profile: dict[str, str],
) -> dict[str, Any]:
    task_cells = [
        _task_cell(task_id, [run for run in runs if run["task_id"] == task_id])
        for task_id in ordered_tasks
    ]
    metrics = _metrics(runs)
    covered = sum(cell["coverage"]["tolerant_judgeable"] for cell in task_cells)
    judgeable_counts = [
        cell["metrics"]["tolerant_judgeable_runs"]
        for cell in task_cells
    ]
    availability = roster_model["availability"]
    if availability == "excluded":
        status = "excluded"
        reason = "roster-excluded"
    elif covered == len(ordered_tasks):
        status = "ranked"
        reason = "complete-tolerant-task-coverage"
    else:
        status = "unranked"
        reason = "incomplete-tolerant-task-coverage"
    return {
        "model_id": roster_model["model_id"],
        "requested_model_id": roster_model["requested_model_id"],
        "resolved_model_id": roster_model["resolved_model_id"],
        "provider": roster_model["provider_resolved"],
        "availability": availability,
        "status": status,
        "status_reason": reason,
        "coverage": {
            "tasks_with_tolerant_judgeable_runs": covered,
            "tasks_total": len(ordered_tasks),
            "tolerant_task_coverage_rate": _mean(covered, len(ordered_tasks)),
            "minimum_tolerant_judgeable_runs_per_task": min(judgeable_counts, default=0),
        },
        "metrics": metrics,
        "profiles": {
            profile_id: _metrics([run for run in runs if task_profile[run["task_id"]] == profile_id])
            for profile_id in sorted(set(task_profile.values()))
        },
        "task_cells": task_cells,
    }


def _ranking_key(entry: dict[str, Any]) -> tuple[Any, ...]:
    metrics = entry["metrics"]
    return (
        -(metrics["tolerant_objective_pass_rate_all_eligible"] or -1),
        -(metrics["tolerant_objective_pass_rate_among_judgeable"] or -1),
        -(metrics["tolerant_recovery_rate_all_eligible"] or -1),
        entry["model_id"],
    )


def _ranking(entries: list[dict[str, Any]]) -> dict[str, Any]:
    ranked = [
        entry
        for entry in entries
        if entry["status"] == "ranked" and entry["availability"] == "eligible"
    ]
    unranked = [entry for entry in entries if entry["status"] == "unranked"]
    excluded = [entry for entry in entries if entry["status"] == "excluded"]
    ranked.sort(key=_ranking_key)
    for index, entry in enumerate(ranked, start=1):
        entry = entry.copy()
        entry["rank"] = index
        ranked[index - 1] = {
            "model_id": entry["model_id"],
            "status": entry["status"],
            "coverage": entry["coverage"],
            "metrics": entry["metrics"],
            "rank": index,
        }
    unranked.sort(key=lambda entry: entry["model_id"])
    excluded.sort(key=lambda entry: entry["model_id"])
    return {
        "status": "diagnostic-only",
        "primary_metric": "tolerant_objective_pass_rate_all_eligible",
        "tie_breakers": [
            "tolerant_objective_pass_rate_among_judgeable",
            "tolerant_recovery_rate_all_eligible",
        ],
        "ranked": ranked,
        "unranked": [
            {
                "model_id": entry["model_id"],
                "status": entry["status"],
                "coverage": entry["coverage"],
                "metrics": entry["metrics"],
            }
            for entry in unranked
        ],
        "excluded": [
            {
                "model_id": entry["model_id"],
                "status": entry["status"],
                "coverage": entry["coverage"],
                "metrics": entry["metrics"],
            }
            for entry in excluded
        ],
    }


def _load_analysis_inputs(
    root: Path,
    input_path: Path,
    policy_path: Path,
    ledger_path: Path,
    run_schema_path: Path,
    *,
    allow_untrusted_inputs: bool,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], dict[str, str], list[str]]:
    raw_manifest = _load_json(input_path, "leaderboard input manifest")
    if not isinstance(raw_manifest, dict):
        raise TolerantAnalysisError("leaderboard input manifest must be an object")
    benchmark_id = _required_string(raw_manifest.get("benchmark_id"), "input.benchmark_id")
    benchmark_version = _required_string(raw_manifest.get("benchmark_version"), "input.benchmark_version")
    policy_document = _load_json(policy_path, "leaderboard policy")
    _validate_checked_schema(
        policy_document,
        ROOT / "schemas" / "leaderboard-policy.schema.json",
        "leaderboard policy",
    )
    policy = _validate_policy(policy_document, benchmark_id, benchmark_version)
    trusted_paths = (
        policy_path.resolve() == DEFAULT_POLICY.resolve()
        and ledger_path.resolve() == DEFAULT_LEDGER.resolve()
        and run_schema_path.resolve() == DEFAULT_RUN_SCHEMA.resolve()
    )
    if not trusted_paths and not allow_untrusted_inputs:
        raise TolerantAnalysisError("custom ledger, policy, or run schema requires --allow-untrusted-inputs")
    ordered_tasks, profile_tasks, task_profile = _load_ledger(
        ledger_path,
        benchmark_id,
        benchmark_version,
        require_frozen=trusted_paths,
    )
    manifest, roster = _load_input(input_path, root, benchmark_id, benchmark_version)
    trusted_bindings = _trusted_task_bindings(task_profile) if trusted_paths else None
    records = _load_records(
        manifest,
        roster,
        root,
        task_profile,
        run_schema_path,
        policy,
        trusted_bindings,
    )
    return (
        manifest,
        roster,
        records,
        task_profile,
        ordered_tasks,
    )


def _load_fixtures(task_ids: Iterable[str]) -> dict[str, Any]:
    fixtures: dict[str, Any] = {}
    for task_id in task_ids:
        fixture_path = ROOT / expected_release_artifact_paths(task_id)["fixture"]
        fixtures[task_id] = _load_json(fixture_path, f"{task_id} fixture")
    return fixtures


def build_analysis(
    *,
    root: Path,
    input_path: Path,
    policy_path: Path = DEFAULT_POLICY,
    ledger_path: Path = DEFAULT_LEDGER,
    run_schema_path: Path = DEFAULT_RUN_SCHEMA,
    allow_untrusted_inputs: bool = False,
) -> dict[str, Any]:
    manifest, roster, records, task_profile, ordered_tasks = _load_analysis_inputs(
        root,
        input_path,
        policy_path,
        ledger_path,
        run_schema_path,
        allow_untrusted_inputs=allow_untrusted_inputs,
    )
    fixtures = _load_fixtures(ordered_tasks)
    analysed_runs: list[dict[str, Any]] = []
    for record in records:
        raw_path = (root / record["raw_output_reference"]).resolve()
        analysed_runs.append(_analyse_run(record, raw_path, fixtures[record["task_id"]]))
    analysed_runs.sort(key=lambda item: item["run_id"])
    roster_by_id = {model["model_id"]: model for model in roster["models"]}
    runs_by_model: dict[str, list[dict[str, Any]]] = {model_id: [] for model_id in roster_by_id}
    for run in analysed_runs:
        runs_by_model[run["model_id"]].append(run)
    models = [
        _model_entry(roster_model, runs_by_model[model_id], ordered_tasks, task_profile)
        for model_id, roster_model in sorted(roster_by_id.items())
    ]
    overall_metrics = _metrics(analysed_runs)
    classification_counts: dict[str, int] = {}
    for run in analysed_runs:
        classification = run["recovery"]["classification"]
        classification_counts[classification] = classification_counts.get(classification, 0) + 1
    release_fingerprints = sorted(
        {
            record["release_lock_fingerprint"]
            for record in records
            if isinstance(record.get("release_lock_fingerprint"), str)
        }
    )
    report = {
        "$schema": "../schemas/tolerant-analysis.schema.json",
        "schema_version": "tolerant-analysis-v1",
        "analysis_id": f"{manifest.get('snapshot_id', 'unknown')}-tolerant-v1",
        "benchmark_id": manifest["benchmark_id"],
        "benchmark_version": manifest["benchmark_version"],
        "input_snapshot_id": manifest.get("snapshot_id", "unknown"),
        "input_manifest_fingerprint": _sha256(input_path),
        "roster_snapshot_id": roster["snapshot_id"],
        "generated_at": manifest.get("generated_at") or roster["captured_at"],
        "release_lock_fingerprints": release_fingerprints,
        "strict_results_preserved": True,
        "recovery_policy": {
            "parser_version": PARSER_VERSION,
            "max_surrounding_chars": 240,
            "accepted_recoveries": ["strict-json-object", "markdown-fenced-json", "surrounded-json"],
            "rejected_as_unrecoverable": [
                "ambiguous-json",
                "duplicate-key",
                "fenced-invalid-json",
                "invalid-json",
                "json-value",
                "markdown-fence-non-json",
                "surrounding-text-too-large",
            ],
        },
        "metric_definitions": {
            "strict_contract_valid": "The original response is one bare JSON object, without Markdown or surrounding prose.",
            "tolerant_recoverable": "The response contains one unambiguously recoverable JSON object under the bounded recovery policy.",
            "tolerant_judgeable": "A recovered object was evaluated with no blocked automatic checks after excluding execution and identity failures.",
            "tolerant_objective_pass": "A tolerant-judged object passed every automatic check and hard-failure gate.",
            "format_only_recovery_pass": "The tolerant objective pass was not also a strict objective pass.",
        },
        "coverage": {
            "attempted_runs": len(analysed_runs),
            "execution_eligible_runs": overall_metrics["execution_eligible_runs"],
            "strict_comparable_runs": overall_metrics["strict_comparable_runs"],
            "tolerant_recoverable_runs": overall_metrics["tolerant_recoverable_runs"],
            "tolerant_judgeable_runs": overall_metrics["tolerant_judgeable_runs"],
        },
        "classification_counts": dict(sorted(classification_counts.items())),
        "overall_metrics": overall_metrics,
        "models": models,
        "ranking": _ranking(models),
        "runs": analysed_runs,
    }
    _validate_checked_schema(report, REPORT_SCHEMA, "tolerant analysis")
    return report


def render_markdown(report: dict[str, Any]) -> str:
    """Render a concise, deterministic handoff without copying raw responses."""
    lines = [
        "# Tolerant objective analysis",
        "",
        f"- Analysis: `{report['analysis_id']}`",
        f"- Input snapshot: `{report['input_snapshot_id']}`",
        f"- Parser: `{report['recovery_policy']['parser_version']}`",
        "- Strict leaderboard records and raw responses were not modified.",
        "",
        "## Overall",
        "",
    ]
    overall = report["overall_metrics"]
    for label, key in (
        ("Attempted runs", "attempted_runs"),
        ("Execution-eligible runs", "execution_eligible_runs"),
        ("Strict-comparable runs", "strict_comparable_runs"),
        ("Tolerant-recoverable runs", "tolerant_recoverable_runs"),
        ("Tolerant-judgeable runs", "tolerant_judgeable_runs"),
        ("Strict objective passes", "strict_objective_pass_runs"),
        ("Tolerant objective passes", "tolerant_objective_pass_runs"),
        ("Format-only recovery passes", "format_only_recovery_pass_runs"),
    ):
        lines.append(f"- {label}: `{overall[key]}`")
    lines.extend(["", "## Model diagnostic ranking", ""])
    for entry in report["ranking"]["ranked"]:
        metrics = entry["metrics"]
        lines.append(
            f"- `{entry['rank']}. {entry['model_id']}` - tolerant objective "
            f"`{metrics['tolerant_objective_pass_rate_all_eligible']}` over eligible runs; "
            f"recovery `{metrics['tolerant_recovery_rate_all_eligible']}`; "
            f"judgeable quality `{metrics['tolerant_objective_pass_rate_among_judgeable']}`."
        )
    if report["ranking"]["unranked"]:
        lines.extend(["", "## Unranked", ""])
        for entry in report["ranking"]["unranked"]:
            lines.append(
                f"- `{entry['model_id']}` - tolerant task coverage "
                f"`{entry['coverage']['tolerant_task_coverage_rate']}`."
            )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- Strict validity measures direct machine-interface compliance.",
            "- Tolerant recovery measures whether bounded formatting noise can be removed without changing the object.",
            "- Tolerant objective success measures the frozen task evaluator result after that recovery.",
            "- Truncated, duplicate-key, ambiguous, non-object, and otherwise unrecoverable responses remain failures.",
        ]
    )
    return "\n".join(lines) + "\n"


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--run-schema", type=Path, default=DEFAULT_RUN_SCHEMA)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown", type=Path)
    parser.add_argument("--allow-untrusted-inputs", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    input_path = args.input if args.input.is_absolute() else root / args.input
    policy_path = args.policy if args.policy.is_absolute() else root / args.policy
    ledger_path = args.ledger if args.ledger.is_absolute() else root / args.ledger
    run_schema_path = args.run_schema if args.run_schema.is_absolute() else root / args.run_schema
    output_path = args.output if args.output.is_absolute() else root / args.output
    markdown_path = args.markdown if args.markdown is None or args.markdown.is_absolute() else root / args.markdown
    try:
        report = build_analysis(
            root=root,
            input_path=input_path,
            policy_path=policy_path,
            ledger_path=ledger_path,
            run_schema_path=run_schema_path,
            allow_untrusted_inputs=args.allow_untrusted_inputs,
        )
        _write_json(output_path, report)
        if markdown_path is not None:
            markdown_path.parent.mkdir(parents=True, exist_ok=True)
            markdown_path.write_text(render_markdown(report), encoding="utf-8")
    except (LeaderboardInputError, TolerantAnalysisError, OSError, UnicodeError, ValueError) as exc:
        print(f"tolerant analysis failed: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "analysis_id": report["analysis_id"],
                "attempted_runs": report["coverage"]["attempted_runs"],
                "execution_eligible_runs": report["coverage"]["execution_eligible_runs"],
                "tolerant_recoverable_runs": report["coverage"]["tolerant_recoverable_runs"],
                "tolerant_judgeable_runs": report["coverage"]["tolerant_judgeable_runs"],
                "output": str(args.output),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
