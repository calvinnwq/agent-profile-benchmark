"""Fail-closed aggregate count validation shared by build and report paths."""

from __future__ import annotations

from typing import Any, Mapping


AGGREGATE_INTEGER_FIELDS = (
    "planned_cells",
    "launch_failures",
    "attempted_runs",
    "completed_execution_records",
    "execution_blocked_runs",
    "all_attempt_process_or_timeout_failures",
    "resolved_identity_runs",
    "parseable_output_runs",
    "non_json_output_runs",
    "all_attempt_hard_failure_runs",
    "all_attempt_hard_failure_entries",
    "all_attempt_invalid_output_runs",
    "evaluator_blocked_runs",
    "comparable_resolved_runs",
    "excluded_provider_or_identity_runs",
    "blocked_or_unverified_runs",
    "comparable_full_contract_pass_runs",
    "full_contract_pass_runs",
    "comparable_automatic_check_pass_runs",
    "all_attempt_automatic_check_pass_runs",
    "all_automatic_checks_pass_runs",
    "comparable_process_or_timeout_failures",
    "hard_failure_runs",
    "comparable_hard_failure_runs",
    "invalid_output_runs",
    "comparable_invalid_output_runs",
    "process_or_timeout_failures",
)


class AggregateValidationError(ValueError):
    """Raised when a leaderboard count cannot be reconciled."""


def _integer(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise AggregateValidationError(f"aggregate.{name} must be a non-negative integer")
    return value


def validate_aggregate(
    aggregate: Mapping[str, Any],
    *,
    cell_totals: Mapping[str, int] | None = None,
    scope_totals: Mapping[str, int] | None = None,
) -> None:
    """Validate all denominators, aliases, ranges, and cross-count identities."""
    if not isinstance(aggregate, Mapping):
        raise AggregateValidationError("aggregate must be an object")
    for name in AGGREGATE_INTEGER_FIELDS:
        if name not in aggregate:
            raise AggregateValidationError(f"aggregate is missing {name}")
        _integer(aggregate[name], name)
    if not isinstance(aggregate.get("human_scores_assigned"), bool):
        raise AggregateValidationError("aggregate.human_scores_assigned must be a boolean")

    attempted = aggregate["attempted_runs"]
    planned = aggregate["planned_cells"]
    launch_failures = aggregate["launch_failures"]
    if launch_failures > planned:
        raise AggregateValidationError("aggregate.launch_failures exceeds planned_cells")
    if attempted + launch_failures < planned:
        raise AggregateValidationError("aggregate attempts and launch failures do not cover planned cells")

    if (
        aggregate["parseable_output_runs"] + aggregate["non_json_output_runs"]
        != attempted
    ):
        raise AggregateValidationError("aggregate parseable and non-JSON counts do not add up")
    if (
        aggregate["completed_execution_records"]
        + aggregate["execution_blocked_runs"]
        + aggregate["all_attempt_process_or_timeout_failures"]
        != attempted
    ):
        raise AggregateValidationError("aggregate execution counts do not add up")
    if aggregate["resolved_identity_runs"] > attempted:
        raise AggregateValidationError("aggregate resolved identity count exceeds attempts")
    if aggregate["comparable_resolved_runs"] > aggregate["resolved_identity_runs"]:
        raise AggregateValidationError("aggregate comparable count exceeds resolved identities")
    if (
        aggregate["comparable_resolved_runs"]
        + aggregate["excluded_provider_or_identity_runs"]
        + aggregate["blocked_or_unverified_runs"]
        != attempted
    ):
        raise AggregateValidationError("aggregate comparable and exclusion counts do not add up")

    for name, denominator in (
        ("all_attempt_hard_failure_runs", attempted),
        ("all_attempt_invalid_output_runs", attempted),
        ("all_attempt_automatic_check_pass_runs", attempted),
        ("evaluator_blocked_runs", attempted),
        ("comparable_full_contract_pass_runs", aggregate["comparable_resolved_runs"]),
        ("comparable_automatic_check_pass_runs", aggregate["comparable_resolved_runs"]),
        ("comparable_process_or_timeout_failures", aggregate["comparable_resolved_runs"]),
        ("comparable_hard_failure_runs", aggregate["comparable_resolved_runs"]),
        ("comparable_invalid_output_runs", aggregate["comparable_resolved_runs"]),
    ):
        if aggregate[name] > denominator:
            raise AggregateValidationError(f"aggregate.{name} exceeds its denominator")
    if aggregate["all_attempt_hard_failure_entries"] < aggregate["all_attempt_hard_failure_runs"]:
        raise AggregateValidationError("aggregate hard-failure entries are below hard-failure runs")

    if scope_totals is not None:
        for name in ("planned_cells", "launch_failures"):
            expected = scope_totals.get(name)
            _integer(expected, f"scope.{name}")
            if aggregate[name] != expected:
                raise AggregateValidationError(f"aggregate.{name} disagrees with sealed model/task scope")

    for alias, canonical in (
        ("full_contract_pass_runs", "comparable_full_contract_pass_runs"),
        ("all_automatic_checks_pass_runs", "all_attempt_automatic_check_pass_runs"),
        ("hard_failure_runs", "comparable_hard_failure_runs"),
        ("invalid_output_runs", "comparable_invalid_output_runs"),
        ("process_or_timeout_failures", "comparable_process_or_timeout_failures"),
    ):
        if aggregate[alias] != aggregate[canonical]:
            raise AggregateValidationError(f"aggregate.{alias} disagrees with aggregate.{canonical}")

    if cell_totals is not None:
        for name in (
            "planned_cells",
            "launch_failures",
            "attempted_runs",
            "completed_execution_records",
            "parseable_output_runs",
            "all_attempt_hard_failure_runs",
            "all_attempt_invalid_output_runs",
            "all_attempt_automatic_check_pass_runs",
            "comparable_resolved_runs",
            "excluded_provider_or_identity_runs",
            "blocked_or_unverified_runs",
            "comparable_hard_failure_runs",
            "comparable_invalid_output_runs",
        ):
            if aggregate[name] != cell_totals.get(name):
                raise AggregateValidationError(f"aggregate.{name} disagrees with task-cell totals")
