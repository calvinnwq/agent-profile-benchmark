"""Tests for the isolated exactly-24-cell validity diagnostic."""

from __future__ import annotations

import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
