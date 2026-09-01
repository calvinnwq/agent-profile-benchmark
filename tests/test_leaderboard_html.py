"""Tests for the deterministic leaderboard HTML renderer."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from scripts.render_leaderboard_html import RenderError, render_html


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
        "schema_version": "leaderboard-v1",
        "benchmark_id": "agent-profile-benchmark",
        "benchmark_version": "0.2.0",
        "policy_id": "leaderboard-v1",
        "policy_version": "1.0.0",
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
                "task_cells": [{"task_id": "ALPHA-01", "excluded_runs": 1}],
            },
            {
                "model_id": unranked["model_id"],
                "status": unranked["status"],
                "availability": "eligible",
            },
        ],
        "aggregate": {
            "attempted_runs": 4,
            "comparable_resolved_runs": 3,
            "excluded_provider_or_identity_runs": 1,
            "full_contract_pass_runs": 1,
            "all_automatic_checks_pass_runs": 1,
            "hard_failure_runs": 1,
            "invalid_output_runs": 0,
            "process_or_timeout_failures": 0,
            "human_scores_assigned": False,
        },
        "overall": {
            "ranked": [ranked],
            "unranked": [unranked],
            "excluded": [],
        },
        "profiles": {
            "alpha": {"ranked": [ranked], "unranked": [], "excluded": []},
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

    def test_render_reflects_enabled_routing_and_human_scores(self) -> None:
        data = _leaderboard()
        data["aggregate"]["human_scores_assigned"] = True
        data["publication"]["human_scores_assigned"] = True
        data["publication"]["routing_recommendation_allowed"] = True
        rendered = render_html(data)
        self.assertIn("Human scores are included in this snapshot.", rendered)
        self.assertIn("Routing recommendations are enabled for confirmed profiles", rendered)
        self.assertIn("may inform routing recommendations for confirmed candidates", rendered)
        self.assertNotIn("Human scores are not present in this snapshot.", rendered)
        self.assertNotIn("Routing recommendations are disabled for this snapshot.", rendered)

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


if __name__ == "__main__":
    unittest.main()
