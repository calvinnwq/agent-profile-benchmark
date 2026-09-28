"""Tests for the isolated exactly-24-cell validity diagnostic."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import scripts.run_diagnostic_matrix as diagnostic
from scripts.run_diagnostic_matrix import MatrixInputError, build_diagnostic_plan


class DiagnosticMatrixTests(unittest.TestCase):
    def _inputs(self) -> tuple[dict, dict, dict]:
        models = [
            {
                "model_id": "model-a",
                "requested_model_id": "model-a",
                "resolved_model_id": "model-a",
                "provider_requested": "provider-a",
                "provider_resolved": "provider-a",
                "availability": "eligible",
            },
            {
                "model_id": "model-b",
                "requested_model_id": "model-b",
                "resolved_model_id": "model-b",
                "provider_requested": "provider-b",
                "provider_resolved": "provider-b",
                "availability": "eligible",
            },
        ]
        task_ids = [f"PROFILE-{index:02d}" for index in range(1, 13)]
        manifest = {
            "schema_version": "diagnostic-manifest-v1",
            "benchmark_id": "agent-profile-benchmark",
            "benchmark_version": "0.4.0",
            "diagnostic_id": "validity-diagnostic-2026-09-07",
            "purpose": "validity-diagnostic",
            "decision_surfaces": ["ranking", "promotion", "suitability", "routing"],
            "cells": [
                {"cell_id": f"{model}-{task}", "model_id": model, "task_id": task}
                for model in ("model-a", "model-b")
                for task in task_ids
            ],
        }
        roster = {"models": models}
        ledger = {
            "tasks": [{"id": task, "profile_id": task.split("-")[0].lower()} for task in task_ids]
        }
        return manifest, roster, ledger

    def test_plan_is_exactly_24_cells_and_contains_no_decision_output(self) -> None:
        manifest, roster, ledger = self._inputs()

        plan = build_diagnostic_plan(manifest, roster, ledger, Path("evidence/diagnostic"))

        self.assertEqual(len(plan), 24)
        self.assertEqual(len({item["cell_id"] for item in plan}), 24)
        self.assertEqual(len({item["run_id"] for item in plan}), 24)
        self.assertTrue(all(set(item) >= {"cell_id", "run_id", "task_id", "model_id"} for item in plan))

    def test_manifest_cannot_shrink_or_expand_the_diagnostic(self) -> None:
        manifest, roster, ledger = self._inputs()
        manifest["cells"] = manifest["cells"][:-1]

        with self.assertRaisesRegex(MatrixInputError, "violates its schema"):
            build_diagnostic_plan(manifest, roster, ledger, Path("evidence/diagnostic"))

    def test_manifest_rejects_duplicate_cell_ids(self) -> None:
        manifest, roster, ledger = self._inputs()
        manifest["cells"][1]["cell_id"] = manifest["cells"][0]["cell_id"]

        with self.assertRaisesRegex(MatrixInputError, "repeats cell_id"):
            build_diagnostic_plan(manifest, roster, ledger, Path("evidence/diagnostic"))

    def test_manifest_cannot_add_a_25th_cell(self) -> None:
        manifest, roster, ledger = self._inputs()
        manifest["cells"].append({"cell_id": "extra", "model_id": "model-a", "task_id": "PROFILE-01"})

        with self.assertRaisesRegex(MatrixInputError, "violates its schema"):
            build_diagnostic_plan(manifest, roster, ledger, Path("evidence/diagnostic"))

    def test_manifest_cannot_drop_a_prohibited_decision_surface(self) -> None:
        for surfaces in (
            [],
            ["ranking", "promotion", "suitability"],
            ["promotion", "ranking", "suitability", "routing"],
        ):
            with self.subTest(surfaces=surfaces):
                manifest, roster, ledger = self._inputs()
                manifest["decision_surfaces"] = surfaces
                with self.assertRaisesRegex(MatrixInputError, "violates its schema"):
                    build_diagnostic_plan(manifest, roster, ledger, Path("evidence/diagnostic"))

    def test_manifest_rejects_decision_fields_on_cells_and_top_level(self) -> None:
        for field in ("rank", "ranking", "promotion", "suitability", "routing", "recommendation"):
            with self.subTest(field=field, where="top-level"):
                manifest, roster, ledger = self._inputs()
                manifest[field] = "model-a"
                with self.assertRaisesRegex(MatrixInputError, "violates its schema"):
                    build_diagnostic_plan(manifest, roster, ledger, Path("evidence/diagnostic"))
            with self.subTest(field=field, where="cell"):
                manifest, roster, ledger = self._inputs()
                manifest["cells"][0][field] = 1
                with self.assertRaisesRegex(MatrixInputError, "violates its schema"):
                    build_diagnostic_plan(manifest, roster, ledger, Path("evidence/diagnostic"))

    def test_executed_summary_is_evidence_only_with_no_decision_conclusions(self) -> None:
        """Drive run_diagnostic end to end with launches stubbed: no model is called."""
        manifest, roster, ledger = self._inputs()
        roster.update(
            {
                "schema_version": "model-roster-v1",
                "benchmark_id": "agent-profile-benchmark",
                "benchmark_version": "0.4.0",
                "snapshot_id": "test-roster",
                "provider": "provider-a",
                "captured_at": "2026-09-28T00:00:00Z",
            }
        )
        launches: list[list[str]] = []

        def fake_launch(command, **_kwargs):
            launches.append(command)
            return subprocess.CompletedProcess(command, 1, "", "stubbed launch")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            (root / "roster.json").write_text(json.dumps(roster), encoding="utf-8")
            (root / "ledger.json").write_text(json.dumps(ledger), encoding="utf-8")
            with mock.patch.object(diagnostic, "LEDGER_PATH", root / "ledger.json"), mock.patch.object(
                diagnostic, "_run_release_gate"
            ) as release_gate, mock.patch.object(subprocess, "run", side_effect=fake_launch):
                summary = diagnostic.run_diagnostic(
                    root=root,
                    manifest_path=root / "manifest.json",
                    roster_path=root / "roster.json",
                    output_root=root / "out",
                )
            written = json.loads((root / "out" / "diagnostic-summary.json").read_text(encoding="utf-8"))
            produced = sorted(path.relative_to(root / "out").as_posix() for path in (root / "out").rglob("*"))

        release_gate.assert_called_once()
        self.assertEqual(len(launches), 24)
        self.assertEqual(written, summary)
        self.assertEqual(produced, ["diagnostic-summary.json"])
        self.assertEqual(
            set(summary),
            {"diagnostic_id", "planned_cells", "completed_cells", "failed_launches", "decision_surfaces", "cells", "dry_run"},
        )
        self.assertEqual(summary["decision_surfaces"], [])
        self.assertEqual(summary["planned_cells"], 24)
        self.assertEqual(len(summary["cells"]), 24)
        for cell in summary["cells"]:
            self.assertEqual(set(cell), {"cell_id", "run_id", "task_id", "model_id", "returncode", "record_path", "status"})
        text = json.dumps(summary).lower()
        for term in ("rank", "promot", "suitab", "routing", "recommend", "winner", "leaderboard", "score"):
            self.assertNotIn(term, text)

    def test_dry_run_plans_24_cells_without_release_gate_launch_or_output(self) -> None:
        manifest, roster, ledger = self._inputs()
        roster.update(
            {
                "schema_version": "model-roster-v1",
                "benchmark_id": "agent-profile-benchmark",
                "benchmark_version": "0.4.0",
                "snapshot_id": "test-roster",
                "provider": "provider-a",
                "captured_at": "2026-09-28T00:00:00Z",
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            (root / "roster.json").write_text(json.dumps(roster), encoding="utf-8")
            (root / "ledger.json").write_text(json.dumps(ledger), encoding="utf-8")
            with mock.patch.object(diagnostic, "LEDGER_PATH", root / "ledger.json"), mock.patch.object(
                diagnostic, "_run_release_gate"
            ) as release_gate, mock.patch.object(subprocess, "run") as launch:
                summary = diagnostic.run_diagnostic(
                    root=root,
                    manifest_path=root / "manifest.json",
                    roster_path=root / "roster.json",
                    output_root=root / "out",
                    dry_run=True,
                )
            self.assertFalse((root / "out").exists())

        release_gate.assert_not_called()
        launch.assert_not_called()
        self.assertEqual(summary["planned_cells"], 24)
        self.assertTrue(summary["dry_run"])


if __name__ == "__main__":
    unittest.main()
