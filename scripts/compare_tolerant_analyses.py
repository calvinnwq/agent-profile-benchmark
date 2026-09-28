#!/usr/bin/env python3
"""Compare two deterministic tolerant objective-analysis reports."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    from validate_benchmark import validate_schema_instance
except ImportError:  # pragma: no cover - package-style import
    from scripts.validate_benchmark import validate_schema_instance


ROOT = Path(__file__).resolve().parents[1]
COMPARISON_SCHEMA = ROOT / "schemas" / "tolerant-comparison.schema.json"


RATE_KEYS = (
    "strict_contract_valid_rate_all_eligible",
    "strict_objective_pass_rate_all_eligible",
    "strict_objective_pass_rate_among_strict_comparable",
    "tolerant_recovery_rate_all_eligible",
    "tolerant_automatic_check_pass_rate_all_eligible",
    "tolerant_objective_pass_rate_all_eligible",
    "tolerant_objective_pass_rate_among_tolerant_recoverable",
    "tolerant_objective_pass_rate_among_judgeable",
)
COUNT_KEYS = (
    "attempted_runs",
    "execution_eligible_runs",
    "strict_comparable_runs",
    "tolerant_recoverable_runs",
    "tolerant_judgeable_runs",
    "strict_objective_pass_runs",
    "tolerant_objective_pass_runs",
    "format_only_recovery_pass_runs",
)


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to read analysis report ({type(exc).__name__})") from exc
    if not isinstance(value, dict) or value.get("schema_version") != "tolerant-analysis-v1":
        raise ValueError("analysis report must use tolerant-analysis-v1")
    return value


def _delta(high: Any, medium: Any) -> float | int | None:
    if not isinstance(high, (int, float)) or isinstance(high, bool):
        return None
    if not isinstance(medium, (int, float)) or isinstance(medium, bool):
        return None
    value = high - medium
    return round(value, 6) if isinstance(value, float) else value


def _metric_view(report: dict[str, Any]) -> dict[str, Any]:
    metrics = report["overall_metrics"]
    return {key: metrics.get(key) for key in (*COUNT_KEYS, *RATE_KEYS)}


def _model_view(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry["model_id"]: entry for entry in report["models"]}


def compare(high: dict[str, Any], medium: dict[str, Any]) -> dict[str, Any]:
    high_models = _model_view(high)
    medium_models = _model_view(medium)
    models: list[dict[str, Any]] = []
    high_ranks = {
        row["model_id"]: row.get("rank")
        for row in high["ranking"]["ranked"]
    }
    medium_ranks = {
        row["model_id"]: row.get("rank")
        for row in medium["ranking"]["ranked"]
    }
    for model_id in sorted(set(high_models) | set(medium_models)):
        high_entry = high_models.get(model_id)
        medium_entry = medium_models.get(model_id)
        high_metrics = high_entry["metrics"] if high_entry else {}
        medium_metrics = medium_entry["metrics"] if medium_entry else {}
        high_view = {key: high_metrics.get(key) for key in (*COUNT_KEYS, *RATE_KEYS)}
        medium_view = {key: medium_metrics.get(key) for key in (*COUNT_KEYS, *RATE_KEYS)}
        models.append(
            {
                "model_id": model_id,
                "high_rank": high_ranks.get(model_id),
                "medium_rank": medium_ranks.get(model_id),
                "rank_delta_high_minus_medium": _delta(high_ranks.get(model_id), medium_ranks.get(model_id)),
                "high": high_view,
                "medium": medium_view,
                "delta_high_minus_medium": {
                    key: _delta(high_view[key], medium_view[key])
                    for key in (*COUNT_KEYS, *RATE_KEYS)
                },
            }
        )
    high_overall = _metric_view(high)
    medium_overall = _metric_view(medium)
    return {
        "$schema": "../schemas/tolerant-comparison.schema.json",
        "schema_version": "tolerant-comparison-v1",
        "benchmark_id": high["benchmark_id"],
        "benchmark_version": high["benchmark_version"],
        "high_analysis_id": high["analysis_id"],
        "medium_analysis_id": medium["analysis_id"],
        "sample_scopes": {
            "high_attempted_runs": high["coverage"]["attempted_runs"],
            "medium_attempted_runs": medium["coverage"]["attempted_runs"],
            "denominators_differ": high["coverage"] != medium["coverage"],
            "interpretation": "Rates are diagnostic because the selected high and medium evidence scopes are not identical.",
        },
        "overall": {
            "high": high_overall,
            "medium": medium_overall,
            "delta_high_minus_medium": {
                key: _delta(high_overall[key], medium_overall[key])
                for key in (*COUNT_KEYS, *RATE_KEYS)
            },
        },
        "models": models,
        "interpretation": {
            "strict_results_preserved": True,
            "rate_delta_units": "high minus medium; positive means the high-reasoning arm is higher",
            "primary_objective_view": "tolerant_objective_pass_rate_all_eligible",
            "quality_view": "tolerant_objective_pass_rate_among_judgeable",
            "format_view": "strict_contract_valid_rate_all_eligible and tolerant_recovery_rate_all_eligible",
        },
    }


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _fmt(value: Any) -> str:
    return "n/a" if value is None else str(value)


def render_markdown(comparison: dict[str, Any]) -> str:
    lines = [
        "# Tolerant objective comparison",
        "",
        f"- High analysis: `{comparison['high_analysis_id']}`",
        f"- Medium analysis: `{comparison['medium_analysis_id']}`",
        f"- Selected runs: high `{comparison['sample_scopes']['high_attempted_runs']}`, medium `{comparison['sample_scopes']['medium_attempted_runs']}`.",
        "- Positive deltas mean the high-reasoning arm is higher.",
        "",
        "## Overall deltas",
        "",
    ]
    delta = comparison["overall"]["delta_high_minus_medium"]
    for key in (*COUNT_KEYS, *RATE_KEYS):
        lines.append(f"- `{key}`: `{_fmt(delta[key])}`")
    lines.extend(["", "## Model deltas", ""])
    for entry in comparison["models"]:
        metrics = entry["delta_high_minus_medium"]
        lines.append(
            f"- `{entry['model_id']}` - objective `{_fmt(metrics['tolerant_objective_pass_rate_all_eligible'])}`, "
            f"judgeable quality `{_fmt(metrics['tolerant_objective_pass_rate_among_judgeable'])}`, "
            f"recovery `{_fmt(metrics['tolerant_recovery_rate_all_eligible'])}`, "
            f"strict objective `{_fmt(metrics['strict_objective_pass_rate_among_strict_comparable'])}`."
        )
    lines.extend(
        [
            "",
            "## Reading the comparison",
            "",
            "- Strict objective rate is the original objective pass rate among strict-comparable runs.",
            "- Tolerant objective rate includes boundedly recovered objects but does not repair ambiguous or truncated output.",
            "- Judgeable quality excludes recovered responses whose evaluator checks remained blocked.",
        ]
    )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--high", type=Path, required=True)
    parser.add_argument("--medium", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args(argv)
    try:
        comparison = compare(_load(args.high), _load(args.medium))
        schema = json.loads(COMPARISON_SCHEMA.read_text(encoding="utf-8"))
        errors = validate_schema_instance(comparison, schema)
        if errors:
            raise ValueError("comparison violates its schema: " + "; ".join(errors[:4]))
        _write(args.output, comparison)
        if args.markdown is not None:
            args.markdown.parent.mkdir(parents=True, exist_ok=True)
            args.markdown.write_text(render_markdown(comparison), encoding="utf-8")
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"tolerant comparison failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"output": str(args.output), "models": len(comparison["models"])}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
