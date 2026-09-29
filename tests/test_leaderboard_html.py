"""Tests for the deterministic leaderboard HTML renderer."""

from __future__ import annotations

from copy import deepcopy

import json
import math
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from scripts.render_leaderboard_html import (
    CHART_CAPTION,
    RenderError,
    chart_points,
    layout_chart,
    pareto_frontier,
    render_html,
    render_scatter_svg,
)


ROOT = Path(__file__).resolve().parents[1]
RENDERER = ROOT / "scripts" / "render_leaderboard_html.py"


def _entry(model_id: str, status: str = "provisional") -> dict[str, Any]:
    return {
        "model_id": model_id,
        "status": status,
        "rank": 1 if status == "provisional" else None,
        "reason_codes": [] if status == "provisional" else ["incomplete-task-coverage"],
        "coverage": {
            "tasks_covered": 2 if status == "provisional" else 1,
            "tasks_total": 2,
            "task_coverage_rate": 1.0 if status == "provisional" else 0.5,
            "minimum_replicates": 1 if status == "provisional" else 0,
        },
        "metrics": {
            "full_contract_pass_rate": 0.5 if status == "provisional" else 0.0,
            "automatic_check_pass_rate": 0.75 if status == "provisional" else 0.0,
            "human_quality_score": None,
            "human_score_coverage": 0.0,
            "hard_failure_rate": 0.0 if status == "provisional" else 1.0,
            "invalid_output_rate": 0.0,
            "median_latency_ms": 1234,
        },
    }


def _leaderboard() -> dict[str, Any]:
    ranked = _entry("model<one>:free")
    unranked = _entry("model-two:free", "unranked")
    return {
        "schema_version": "leaderboard-v2",
        "benchmark_id": "agent-profile-benchmark",
        "benchmark_version": "0.4.1",
        "policy_id": "leaderboard-v2",
        "policy_version": "2.0.0",
        "input_snapshot_id": "repeat-001",
        "roster_snapshot_id": "roster-001",
        "generated_at": "2026-09-01T00:00:00Z",
        "scope": "benchmark-specific model leaderboard and routing aid",
        "release_lock_fingerprint": "sha256:" + "0" * 64,
        "models": [
            {
                "model_id": ranked["model_id"],
                "status": ranked["status"],
                "availability": "eligible",
                "task_cells": [{
                    "task_id": "ALPHA-01",
                    "attempted_runs": 4,
                    "completed_execution_records": 4,
                    "execution_blocked_runs": 0,
                    "all_attempt_process_or_timeout_failures": 0,
                    "resolved_identity_runs": 3,
                    "parseable_output_runs": 4,
                    "non_json_output_runs": 0,
                    "all_attempt_hard_failure_runs": 1,
                    "all_attempt_hard_failure_entries": 1,
                    "all_attempt_invalid_output_runs": 0,
                    "all_attempt_automatic_check_pass_runs": 1,
                    "evaluator_blocked_runs": 0,
                    "comparable_runs": 3,
                    "comparable_resolved_runs": 3,
                    "excluded_runs": 1,
                    "excluded_provider_or_identity_runs": 1,
                    "blocked_or_unverified_runs": 0,
                    "comparable_full_contract_pass_runs": 1,
                    "full_contract_pass_runs": 1,
                    "comparable_automatic_check_pass_runs": 1,
                    "all_automatic_checks_pass_runs": 1,
                    "comparable_process_or_timeout_failures": 0,
                    "comparable_hard_failure_runs": 1,
                    "hard_failure_runs": 1,
                    "comparable_invalid_output_runs": 0,
                    "invalid_output_runs": 0,
                    "process_or_timeout_failures": 0,
                }],
            },
            {
                "model_id": unranked["model_id"],
                "status": unranked["status"],
                "availability": "eligible",
                "task_cells": [],
            },
        ],
        "aggregate": {
            "planned_cells": 4,
            "launch_failures": 3,
            "attempted_runs": 4,
            "completed_execution_records": 4,
            "execution_blocked_runs": 0,
            "all_attempt_process_or_timeout_failures": 0,
            "resolved_identity_runs": 3,
            "parseable_output_runs": 4,
            "non_json_output_runs": 0,
            "all_attempt_hard_failure_runs": 1,
            "all_attempt_hard_failure_entries": 1,
            "all_attempt_invalid_output_runs": 0,
            "all_attempt_automatic_check_pass_runs": 1,
            "evaluator_blocked_runs": 0,
            "comparable_resolved_runs": 3,
            "excluded_provider_or_identity_runs": 1,
            "blocked_or_unverified_runs": 0,
            "comparable_full_contract_pass_runs": 1,
            "full_contract_pass_runs": 1,
            "comparable_automatic_check_pass_runs": 1,
            "all_automatic_checks_pass_runs": 1,
            "comparable_process_or_timeout_failures": 0,
            "hard_failure_runs": 1,
            "comparable_hard_failure_runs": 1,
            "invalid_output_runs": 0,
            "comparable_invalid_output_runs": 0,
            "process_or_timeout_failures": 0,
            "comparable_strict_json_valid_runs": 3,
            "comparable_format_recovered_runs": 0,
            "comparable_unrecoverable_output_runs": 0,
            "human_scores_assigned": False,
        },
        "overall": {
            "ranked": [ranked],
            "unranked": [unranked],
            "excluded": [],
        },
        "profiles": {
            "alpha": {"ranked": [ranked], "unranked": [unranked], "excluded": []},
        },
        "publication": {
            "ranking_available": True,
            "score_publishable": True,
            "human_scores_assigned": False,
            "routing_recommendation_allowed": False,
            "reason": "Repeat confirmation is incomplete.",
        },
        "input": {
            "roster_path": ".model-evidence/roster.json",
            "selected_run_count": 4,
            "selected_runs": [],
        },
    }


class LeaderboardHtmlTests(unittest.TestCase):
    def test_render_is_deterministic_escaped_and_explicit_about_routing(self) -> None:
        data = _leaderboard()
        first = render_html(data, source_sha256="abc123")
        second = render_html(data, source_sha256="abc123")
        self.assertEqual(first, second)
        self.assertIn("model&lt;one&gt;:free", first)
        self.assertIn("Routing recommendations are disabled", first)
        self.assertIn("not routing recommendations while the confirmation gate is off", first)
        self.assertIn("Run-level provider and identity exclusions", first)
        self.assertIn("ALPHA-01", first)
        self.assertNotIn("<script", first.lower())
        self.assertNotIn("{{", first)
        self.assertIn("abc123", first)

    def test_render_rejects_missing_benchmark_identity(self) -> None:
        data = _leaderboard()
        del data["benchmark_id"]
        with self.assertRaises(RenderError):
            render_html(data)

    def test_render_rejects_malformed_ranking_metrics(self) -> None:
        data = _leaderboard()
        del data["overall"]["ranked"][0]["metrics"]["full_contract_pass_rate"]
        with self.assertRaises(RenderError):
            render_html(data)

    def test_render_rejects_inconsistent_model_and_aggregate_counts(self) -> None:
        mutations = {
            "model status": lambda data: data["models"][0].update(status="confirmed"),
            "model availability": lambda data: data["models"][0].update(availability="excluded"),
            "attempted runs": lambda data: data["aggregate"].update(attempted_runs=99),
            "comparable runs": lambda data: data["aggregate"].update(comparable_resolved_runs=99),
            "provider exclusions": lambda data: data["aggregate"].update(
                excluded_provider_or_identity_runs=99
            ),
            "blocked evidence": lambda data: data["aggregate"].update(blocked_or_unverified_runs=99),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                data = _leaderboard()
                mutate(data)
                with self.assertRaises(RenderError):
                    render_html(data)

    def test_render_rejects_incomplete_ranked_coverage_under_default_policy(self) -> None:
        data = _leaderboard()
        data["overall"]["ranked"][0]["coverage"].update(
            tasks_covered=1,
            task_coverage_rate=0.5,
        )
        with self.assertRaises(RenderError):
            render_html(data)

    def test_render_rejects_reviewed_impossible_aggregate_counts(self) -> None:
        render_html(_leaderboard())
        mutations = {
            "all_attempt_hard_failure_entries": 999,
            "comparable_full_contract_pass_runs": 99,
            "resolved_identity_runs": 0,
            "hard_failure_runs": 99,
        }
        for field, value in mutations.items():
            with self.subTest(field=field, value=value):
                data = _leaderboard()
                data["aggregate"][field] = value
                with self.assertRaises(RenderError):
                    render_html(data)

    def test_render_rejects_duplicate_task_cells(self) -> None:
        data = _leaderboard()
        data["models"][0]["task_cells"].append(deepcopy(data["models"][0]["task_cells"][0]))
        with self.assertRaises(RenderError):
            render_html(data)

    def test_render_rejects_ranking_identity_drift(self) -> None:
        data = _leaderboard()
        data["overall"]["unranked"] = []
        with self.assertRaises(RenderError):
            render_html(data)

    def test_render_rejects_inconsistent_coverage_rate(self) -> None:
        data = _leaderboard()
        data["overall"]["ranked"][0]["coverage"]["tasks_covered"] = 1
        with self.assertRaises(RenderError):
            render_html(data)

    def test_render_rejects_aggregate_scope_count_drift(self) -> None:
        data = _leaderboard()
        data["aggregate"]["planned_cells"] = 1
        with self.assertRaises(RenderError):
            render_html(data)

        data = _leaderboard()
        data["aggregate"]["launch_failures"] = 0
        with self.assertRaises(RenderError):
            render_html(data)

    def test_render_rejects_non_contiguous_ranks(self) -> None:
        data = _leaderboard()
        data["overall"]["ranked"][0]["rank"] = 2
        with self.assertRaises(RenderError):
            render_html(data)

    def test_render_separates_blocked_evidence_from_identity_exclusions(self) -> None:
        data = _leaderboard()
        cell = data["models"][0]["task_cells"][0]
        cell["excluded_provider_or_identity_runs"] = 0
        cell["blocked_or_unverified_runs"] = 1
        data["aggregate"]["excluded_provider_or_identity_runs"] = 0
        data["aggregate"]["blocked_or_unverified_runs"] = 1
        rendered = render_html(data)
        self.assertIn("Run-level blocked or unverified evidence", rendered)
        self.assertNotIn("Run-level provider and identity exclusions", rendered)

    def test_render_reflects_enabled_routing_and_human_scores(self) -> None:
        data = _leaderboard()
        data["models"][0]["status"] = "confirmed"
        data["overall"]["ranked"][0]["status"] = "confirmed"
        data["overall"]["ranked"][0]["coverage"]["minimum_replicates"] = 3
        data["aggregate"]["human_scores_assigned"] = True
        data["publication"]["human_scores_assigned"] = True
        data["publication"]["routing_recommendation_allowed"] = True
        rendered = render_html(data)
        self.assertIn("Human scores are included in this snapshot.", rendered)
        self.assertIn("Routing recommendations are enabled for confirmed profiles", rendered)
        self.assertIn("may inform routing recommendations for confirmed candidates", rendered)
        self.assertNotIn("Human scores are not present in this snapshot.", rendered)
        self.assertNotIn("Routing recommendations are disabled for this snapshot.", rendered)

    def test_render_uses_policy_routing_gate_in_copy(self) -> None:
        data = _leaderboard()
        data["publication"]["routing_recommendation_allowed"] = True
        rendered = render_html(
            data,
            policy={"publication": {"routing_requires_confirmed_model_per_profile": False}},
        )
        self.assertIn("Routing recommendations are enabled for ranked profiles", rendered)
        self.assertIn("may inform routing recommendations for ranked candidates", rendered)
        self.assertNotIn("enabled for confirmed profiles", rendered)

    def test_render_accepts_excluded_roster_models_without_launch_failures(self) -> None:
        """Regression: excluded models must not count as planned cells or launch failures."""
        data = _leaderboard()
        excluded = _entry("gone:free", "excluded")
        excluded["reason_codes"] = ["roster-excluded"]
        excluded["coverage"].update(tasks_covered=0, task_coverage_rate=0.0)
        data["models"].append(
            {"model_id": "gone:free", "status": "excluded", "availability": "excluded", "task_cells": []}
        )
        data["overall"]["excluded"] = [excluded]
        data["profiles"]["alpha"]["excluded"] = [deepcopy(excluded)]
        rendered = render_html(data)
        self.assertIn("gone:free", rendered)

        data["aggregate"]["planned_cells"] = 6
        data["aggregate"]["launch_failures"] = 5
        with self.assertRaises(RenderError):
            render_html(data)

    def test_cli_writes_a_complete_html_document(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "leaderboard.json"
            output_path = root / "index.html"
            input_path.write_text(json.dumps(_leaderboard()), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(RENDERER), "--input", str(input_path), "--output", str(output_path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            html = output_path.read_text(encoding="utf-8")
            self.assertTrue(html.startswith("<!doctype html>"))
            self.assertIn("Nous Portal free model leaderboard", html)
            self.assertIn("<table", html)

    def test_cli_rejects_duplicate_json_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "leaderboard.json"
            output_path = root / "index.html"
            input_path.write_text(
                '{"schema_version":"leaderboard-v2","schema_version":"leaderboard-v2"}\n',
                encoding="utf-8",
            )
            result = subprocess.run(
                [sys.executable, str(RENDERER), "--input", str(input_path), "--output", str(output_path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("duplicate JSON object key", result.stderr)
            self.assertFalse(output_path.exists())

    def test_cli_rejects_numeric_overflow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "leaderboard.json"
            output_path = root / "index.html"
            serialized = json.dumps(_leaderboard(), separators=(",", ":"))
            serialized = serialized.replace(
                '"schema_version":"leaderboard-v2"',
                '"overflow":1e9999,"schema_version":"leaderboard-v2"',
                1,
            )
            input_path.write_text(serialized, encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(RENDERER), "--input", str(input_path), "--output", str(output_path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("contains a non-finite number", result.stderr)
            self.assertFalse(output_path.exists())


def _chart_row(model_id: str, status: str, pass_rate: float, latency: float, rank: int | None = None) -> dict[str, Any]:
    return {
        "model_id": model_id,
        "status": status,
        **({"rank": rank} if rank is not None else {}),
        "metrics": {"full_contract_pass_rate": pass_rate, "median_latency_ms": latency},
    }


def _chart_leaderboard() -> dict[str, Any]:
    return {
        "overall": {
            "ranked": [
                _chart_row("slow-best:free", "confirmed", 0.40, 100000, 1),
                _chart_row("mid:free", "provisional", 0.30, 10000, 2),
                _chart_row("dominated:free", "confirmed", 0.20, 20000, 3),
                _chart_row("fast:free", "provisional", 0.10, 1000, 4),
            ],
            "unranked": [_chart_row("partial:free", "unranked", 0.90, 500)],
            "excluded": [_chart_row("gone:free", "excluded", 0.95, 100)],
        },
        "models": [
            {"model_id": "slow-best:free", "usage": {"mean_output_tokens": 4000.0}},
            {"model_id": "mid:free", "usage": {"mean_output_tokens": 400.0}},
            {"model_id": "dominated:free", "usage": {"mean_output_tokens": None}},
            {"model_id": "fast:free", "usage": {"mean_output_tokens": 40.0}},
            {"model_id": "partial:free", "usage": {"mean_output_tokens": 10.0}},
            {"model_id": "gone:free", "usage": {"mean_output_tokens": 1.0}},
        ],
    }


class LeaderboardChartTests(unittest.TestCase):
    def test_points_exclude_excluded_models_and_omit_missing_values(self) -> None:
        points, omitted = chart_points(_chart_leaderboard(), "median_latency_ms")
        self.assertEqual(
            [(point["model_id"], point["status"]) for point in points],
            [
                ("partial:free", "unranked"),
                ("fast:free", "provisional"),
                ("mid:free", "provisional"),
                ("dominated:free", "confirmed"),
                ("slow-best:free", "confirmed"),
            ],
        )
        self.assertEqual(omitted, [])
        token_points, token_omitted = chart_points(_chart_leaderboard(), "mean_output_tokens")
        self.assertNotIn("gone:free", [point["model_id"] for point in token_points])
        self.assertEqual(token_omitted, ["dominated:free"])

    def test_point_placement_uses_log_x_and_linear_y(self) -> None:
        points, _ = chart_points(_chart_leaderboard(), "median_latency_ms")
        layout = layout_chart(points)
        # Domains snap outward to 1-2-5 steps (x) and 0.1 steps (y).
        self.assertEqual(layout["x_domain"], (500.0, 100000.0))
        self.assertEqual(layout["y_domain"], (0.0, 1.0))
        placed = {point["model_id"]: (point["cx"], point["cy"]) for point in layout["points"]}
        left, top, right, bottom = layout["plot"]
        span = math.log10(100000) - math.log10(500)

        def expected_x(value: float) -> float:
            return left + (math.log10(value) - math.log10(500)) / span * (right - left)

        self.assertAlmostEqual(placed["partial:free"][0], left, places=1)
        self.assertAlmostEqual(placed["slow-best:free"][0], right, places=1)
        for model_id, latency in (("fast:free", 1000), ("mid:free", 10000), ("dominated:free", 20000)):
            self.assertAlmostEqual(placed[model_id][0], expected_x(latency), places=1)
        # Ten times the latency is the same horizontal distance anywhere on the axis.
        self.assertAlmostEqual(
            placed["mid:free"][0] - placed["fast:free"][0],
            placed["slow-best:free"][0] - placed["mid:free"][0],
            places=1,
        )
        self.assertAlmostEqual(placed["slow-best:free"][1], bottom - 0.4 * (bottom - top), places=1)
        self.assertAlmostEqual(placed["fast:free"][1], bottom - 0.1 * (bottom - top), places=1)
        # Labels stay inside the plot area.
        svg = render_scatter_svg(points, chart_id="c", x_title="median latency per task", x_unit="ms")
        for x_value in re.findall(r'class="point-label [a-z]+" x="([0-9.]+)"', svg):
            self.assertTrue(left <= float(x_value) <= right)

    def test_frontier_uses_ranked_models_only_and_skips_dominated_points(self) -> None:
        points, _ = chart_points(_chart_leaderboard(), "median_latency_ms")
        self.assertEqual(pareto_frontier(points), ["fast:free", "mid:free", "slow-best:free"])
        token_points, _ = chart_points(_chart_leaderboard(), "mean_output_tokens")
        self.assertEqual(pareto_frontier(token_points), ["fast:free", "mid:free", "slow-best:free"])

    def test_frontier_breaks_equal_x_ties_by_higher_pass_rate(self) -> None:
        points = [
            {"model_id": "b:free", "status": "confirmed", "x": 10.0, "y": 0.2},
            {"model_id": "a:free", "status": "confirmed", "x": 10.0, "y": 0.5},
            {"model_id": "c:free", "status": "provisional", "x": 20.0, "y": 0.5},
        ]
        self.assertEqual(pareto_frontier(points), ["a:free"])

    def test_svg_marks_status_and_frontier_deterministically(self) -> None:
        points, _ = chart_points(_chart_leaderboard(), "median_latency_ms")
        first = render_scatter_svg(points, chart_id="c", x_title="median latency per task", x_unit="ms")
        second = render_scatter_svg(
            list(reversed(points)), chart_id="c", x_title="median latency per task", x_unit="ms"
        )
        self.assertEqual(first, second)
        self.assertIn('data-frontier="fast:free mid:free slow-best:free"', first)
        self.assertIn('class="point confirmed" data-status="confirmed"', first)
        self.assertIn('class="point provisional" data-status="provisional"', first)
        self.assertIn('<polygon class="point unranked" data-status="unranked"', first)
        self.assertIn('class="point-label unranked"', first)
        self.assertIn('class="point-label provisional"', first)
        self.assertIn('data-model="partial:free" data-status="unranked"', first)
        self.assertIn('data-model="mid:free" data-status="provisional" data-frontier="true"', first)
        self.assertIn('data-model="slow-best:free" data-status="confirmed" data-frontier="true"', first)
        self.assertIn('data-model="dominated:free" data-status="confirmed">', first)
        self.assertNotIn("gone:free", first)
        self.assertEqual(first.count('class="model-point"'), 5)
        self.assertEqual(first.count('data-frontier="true"'), 3)
        self.assertNotIn('data-model="partial:free" data-status="unranked" data-frontier', first)

    def test_report_embeds_both_charts_with_caption_and_no_script(self) -> None:
        data = _leaderboard()
        data["models"][0]["usage"] = {"mean_output_tokens": 812.5, "output_token_runs": 3, "comparable_runs": 3}
        rendered = render_html(data)
        self.assertEqual(CHART_CAPTION, "scoped to this frozen suite, not a general ranking")
        self.assertIn('id="chart-latency"', rendered)
        self.assertIn('id="chart-output-tokens"', rendered)
        self.assertIn(CHART_CAPTION, rendered)
        self.assertNotIn("<script", rendered.lower())
        self.assertNotIn("href=\"http", rendered.split('id="efficiency"', 1)[1].split("</section>", 1)[0])
        self.assertEqual(rendered, render_html(deepcopy(data)))

    def test_report_omits_token_chart_without_usage_data(self) -> None:
        rendered = render_html(_leaderboard())
        self.assertIn('id="chart-latency"', rendered)
        self.assertNotIn('id="chart-output-tokens"', rendered)

    def test_report_rejects_malformed_usage_summary(self) -> None:
        data = _leaderboard()
        data["models"][0]["usage"] = {"mean_output_tokens": -1}
        with self.assertRaises(RenderError):
            render_html(data)


if __name__ == "__main__":
    unittest.main()
