"""Batch matrix planning and evidence-manifest tests."""

from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class LeaderboardMatrixTests(unittest.TestCase):
    def test_plan_filters_excluded_models_and_uses_stable_task_order(self) -> None:
        from scripts.run_leaderboard_matrix import build_matrix_plan

        roster = {
            "provider": "nous",
            "models": [
                {
                    "model_id": "zeta/model:free",
                    "requested_model_id": "zeta/model:free",
                    "resolved_model_id": "zeta/model:free",
                    "provider_requested": "provider-zeta",
                    "provider_resolved": "provider-zeta",
                    "availability": "eligible",
                },
                {
                    "model_id": "alpha/model:free",
                    "requested_model_id": "alpha/model:free",
                    "resolved_model_id": "alpha/model:free",
                    "provider_requested": "provider-alpha",
                    "provider_resolved": "provider-alpha",
                    "availability": "eligible",
                },
                {
                    "model_id": "old/model:free",
                    "requested_model_id": "old/model:free",
                    "resolved_model_id": "unresolved",
                    "provider_requested": "nous",
                    "provider_resolved": "unresolved",
                    "availability": "excluded",
                    "exclusion_reason": "not entitled",
                },
            ],
        }
        ledger = {
            "profiles": [
                {"id": "zeta", "task_ids": ["ZETA-02", "ZETA-01"]},
                {"id": "alpha", "task_ids": ["ALPHA-01"]},
            ],
            "tasks": [
                {"id": "ZETA-02", "profile_id": "zeta"},
                {"id": "ZETA-01", "profile_id": "zeta"},
                {"id": "ALPHA-01", "profile_id": "alpha"},
            ],
        }
        plan = build_matrix_plan(
            roster,
            ledger,
            "nous-snapshot-1",
            Path(".model-evidence/sweep"),
            task_inputs={
                "ZETA-02": {
                    "fixture_path": "fixtures/zeta/custom-fixture.json",
                    "prompt_path": "fixtures/zeta/custom-prompt.txt",
                }
            },
        )
        self.assertEqual(
            [(cell["model_id"], cell["task_id"]) for cell in plan],
            [
                ("alpha/model:free", "ZETA-02"),
                ("alpha/model:free", "ZETA-01"),
                ("alpha/model:free", "ALPHA-01"),
                ("zeta/model:free", "ZETA-02"),
                ("zeta/model:free", "ZETA-01"),
                ("zeta/model:free", "ALPHA-01"),
            ],
        )
        self.assertEqual(len({cell["run_id"] for cell in plan}), len(plan))
        self.assertTrue(all(":" not in cell["run_id"] and "/" not in cell["run_id"] for cell in plan))
        self.assertEqual(
            [cell["provider"] for cell in plan],
            ["provider-alpha"] * 3 + ["provider-zeta"] * 3,
        )
        self.assertEqual(plan[0]["fixture_path"], "fixtures/zeta/custom-fixture.json")
        self.assertEqual(plan[0]["prompt_path"], "fixtures/zeta/custom-prompt.txt")

    def test_frozen_manifests_supply_nonstandard_task_input_paths(self) -> None:
        from scripts.run_leaderboard_matrix import _task_input_paths

        ledger = json.loads((ROOT / "data" / "task-ledger.json").read_text(encoding="utf-8"))
        task_inputs = _task_input_paths(ROOT, ledger)
        self.assertEqual(task_inputs["KODY-01"]["fixture_path"], "fixtures/kody-01/request-packet.json")
        self.assertEqual(task_inputs["KODY-01"]["prompt_path"], "fixtures/kody-01/prompt.txt")
        self.assertEqual(len(task_inputs), 18)
        self.assertTrue(all((ROOT / paths["fixture_path"]).is_file() for paths in task_inputs.values()))
        self.assertTrue(all((ROOT / paths["prompt_path"]).is_file() for paths in task_inputs.values()))

    def test_manifest_preserves_only_existing_records_and_is_sorted(self) -> None:
        from scripts.run_leaderboard_matrix import build_input_manifest

        manifest = build_input_manifest(
            benchmark_id="agent-profile-benchmark",
            benchmark_version="0.2.0",
            snapshot_id="nous-snapshot-1",
            roster_path="roster.json",
            completed_cells=[
                {"run_id": "z-run", "record_path": ".model-evidence/sweep/z/run-record.json"},
                {"run_id": "a-run", "record_path": ".model-evidence/sweep/a/run-record.json"},
            ],
        )
        self.assertEqual(manifest["schema_version"], "leaderboard-input-v1")
        self.assertEqual([item["run_id"] for item in manifest["runs"]], ["a-run", "z-run"])
        self.assertEqual(manifest["roster_path"], "roster.json")

    def test_plan_rejects_duplicate_requested_model_aliases(self) -> None:
        from scripts.run_leaderboard_matrix import MatrixInputError, build_matrix_plan

        roster = {
            "provider": "nous",
            "models": [
                {
                    "model_id": "alpha/model:free",
                    "requested_model_id": "shared-alias",
                    "resolved_model_id": "alpha/model:free",
                    "provider_requested": "nous",
                    "provider_resolved": "nous",
                    "availability": "eligible",
                },
                {
                    "model_id": "beta/model:free",
                    "requested_model_id": "shared-alias",
                    "resolved_model_id": "beta/model:free",
                    "provider_requested": "nous",
                    "provider_resolved": "nous",
                    "availability": "eligible",
                },
            ],
        }
        ledger = {
            "profiles": [{"id": "alpha", "task_ids": ["ALPHA-01"]}],
            "tasks": [{"id": "ALPHA-01", "profile_id": "alpha"}],
        }
        with self.assertRaisesRegex(MatrixInputError, "requested model ID"):
            build_matrix_plan(roster, ledger, "snapshot", Path("out"))

    def test_written_record_must_match_the_planned_cell_schema(self) -> None:
        from scripts.run_leaderboard_matrix import MatrixInputError, _validate_cell_record

        cell = {
            "run_id": "snapshot-model-alpha-0123456789-ALPHA-01",
            "task_id": "ALPHA-01",
            "profile_id": "alpha",
            "requested_model_id": "model-a:free",
            "resolved_model_id": "model-a:free",
            "provider": "nous",
            "provider_resolved": "nous",
        }
        with self.assertRaisesRegex(MatrixInputError, "violates its schema"):
            _validate_cell_record({"run_id": cell["run_id"]}, cell)


if __name__ == "__main__":
    unittest.main()