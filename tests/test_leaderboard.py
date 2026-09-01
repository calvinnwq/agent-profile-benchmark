"""Leaderboard contract and generator tests."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts" / "build_leaderboard.py"
POLICY = ROOT / "data" / "leaderboard-policy.json"
POLICY_SCHEMA = ROOT / "schemas" / "leaderboard-policy.schema.json"


FINGERPRINT = "sha256:" + "0" * 64
RAW_FINGERPRINT = "sha256:" + hashlib.sha256(b"{}\n").hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _run_record(model_id: str, task_id: str, passed: bool, sequence: int) -> dict[str, Any]:
    timestamp = f"2026-01-01T00:00:{sequence:02d}Z"
    safe_model_id = model_id.replace("/", "-").replace(":", "-")
    return {
        "run_id": f"{safe_model_id}-{task_id}-{sequence}",
        "benchmark_id": "agent-profile-benchmark",
        "benchmark_version": "0.2.0",
        "release_lock_fingerprint": FINGERPRINT,
        "ledger_fingerprint": FINGERPRINT,
        "task_id": task_id,
        "profile_id": task_id.split("-")[0].lower(),
        "harness": "hermes-oneshot",
        "model_requested": model_id,
        "model_resolved": model_id,
        "provider_requested": "nous",
        "provider_resolved": "nous",
        "resolution_status": "resolved",
        "condition": "model-calibration",
        "evaluator_version": "task-oracle-v1",
        "task_manifest_fingerprint": FINGERPRINT,
        "oracle_fingerprint": FINGERPRINT,
        "output_schema_fingerprint": FINGERPRINT,
        "evaluator_fingerprint": FINGERPRINT,
        "run_record_schema_fingerprint": FINGERPRINT,
        "harness_fingerprint": FINGERPRINT,
        "prompt_fingerprint": FINGERPRINT,
        "fixture_fingerprint": FINGERPRINT,
        "input_fingerprint": FINGERPRINT,
        "input_composition_version": "prompt-plus-fixture-v1",
        "started_at": timestamp,
        "completed_at": timestamp,
        "status": "passed" if passed else "failed",
        "execution_status": "completed",
        "failure_class": "none",
        "raw_output_reference": f"runs/{safe_model_id}-{task_id}-{sequence}.txt",
        "raw_output_fingerprint": RAW_FINGERPRINT,
        "automatic_checks": [
            {
                "id": "contract",
                "status": "pass" if passed else "fail",
                "evidence": ["synthetic test evidence"],
            }
        ],
        "hard_failures": [],
        "human_scores": {},
        "latency_ms": 100 + sequence,
        "usage": {"model": model_id, "provider": "nous", "completed": True, "failed": False},
        "notes": "synthetic leaderboard test run",
    }


def _base_ledger() -> dict[str, Any]:
    return {
        "benchmark_id": "agent-profile-benchmark",
        "benchmark_version": "0.2.0",
        "profiles": [
            {"id": "alpha", "task_ids": ["ALPHA-01", "ALPHA-02"]},
            {"id": "beta", "task_ids": ["BETA-01", "BETA-02", "BETA-03", "BETA-04"]},
        ],
        "tasks": [
            {"id": task_id, "profile_id": profile_id}
            for profile_id, task_ids in (
                ("alpha", ["ALPHA-01", "ALPHA-02"]),
                ("beta", ["BETA-01", "BETA-02", "BETA-03", "BETA-04"]),
            )
            for task_id in task_ids
        ],
    }


def _base_policy() -> dict[str, Any]:
    return {
        "schema_version": "leaderboard-policy-v1",
        "policy_id": "leaderboard-v1",
        "policy_version": "1.0.0",
        "benchmark_id": "agent-profile-benchmark",
        "benchmark_version": "0.2.0",
        "scope": "benchmark-specific model leaderboard and routing aid",
        "status": "active",
        "coverage": {
            "provisional_min_task_coverage": 1.0,
            "confirmed_min_replicates_per_task": 3,
        },
        "ranking": {
            "primary": "full_contract_pass_rate",
            "tie_breakers": [
                "automatic_check_pass_rate",
                "human_quality_score",
                "hard_failure_rate",
                "invalid_output_rate",
                "median_latency_ms",
            ],
        },
        "publication": {
            "score_publishable_requires_complete_overall_coverage": True,
            "routing_requires_confirmed_model_per_profile": True,
        },
    }


def _base_roster(model_ids: tuple[str, ...] = ("model-a:free", "model-b:free")) -> dict[str, Any]:
    return {
        "schema_version": "model-roster-v1",
        "benchmark_id": "agent-profile-benchmark",
        "benchmark_version": "0.2.0",
        "snapshot_id": "synthetic-roster-1",
        "provider": "nous",
        "captured_at": "2026-01-01T00:00:00Z",
        "models": [
            {
                "model_id": model_id,
                "requested_model_id": model_id,
                "resolved_model_id": model_id,
                "provider_requested": "nous",
                "provider_resolved": "nous",
                "availability": "eligible",
            }
            for model_id in model_ids
        ],
    }


def _build_synthetic_input(
    root: Path,
    *,
    passed_tasks_by_model: dict[str, set[str]],
    roster: dict[str, Any] | None = None,
    records_override: list[dict[str, Any]] | None = None,
) -> Path:
    ledger = _base_ledger()
    _write_json(root / "ledger.json", ledger)
    _write_json(root / "policy.json", _base_policy())
    _write_json(root / "roster.json", roster or _base_roster(tuple(passed_tasks_by_model)))
    input_manifest = {
        "schema_version": "leaderboard-input-v1",
        "benchmark_id": "agent-profile-benchmark",
        "benchmark_version": "0.2.0",
        "snapshot_id": "synthetic-input-1",
        "roster_path": "roster.json",
        "runs": [],
    }
    records = records_override or []
    if not records:
        sequence = 1
        for model_id, passed_tasks in passed_tasks_by_model.items():
            for task in ledger["tasks"]:
                records.append(_run_record(model_id, task["id"], task["id"] in passed_tasks, sequence))
                sequence += 1
    for record in records:
        record_path = root / "records" / f"{record['run_id']}.json"
        raw_path = root / record["raw_output_reference"]
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_text("{}\n", encoding="utf-8")
        _write_json(record_path, record)
        input_manifest["runs"].append(
            {"run_id": record["run_id"], "record_path": record_path.relative_to(root).as_posix()}
        )
    _write_json(root / "input.json", input_manifest)
    return root / "input.json"


def _run_builder(
    root: Path,
    *,
    allow_untrusted_inputs: bool = True,
) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        str(BUILDER),
        "--root",
        str(root),
        "--ledger",
        "ledger.json",
        "--policy",
        "policy.json",
        "--input",
        "input.json",
        "--output",
        "leaderboard.json",
    ]
    if allow_untrusted_inputs:
        command.append("--allow-untrusted-inputs")
    return subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


class LeaderboardTests(unittest.TestCase):
    def test_custom_inputs_require_an_explicit_untrusted_override(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _build_synthetic_input(
                root,
                passed_tasks_by_model={"model-a:free": set()},
            )
            result = _run_builder(root, allow_untrusted_inputs=False)
            self.assertEqual(result.returncode, 2)
            self.assertIn("allow-untrusted-inputs", result.stderr)
            self.assertFalse((root / "leaderboard.json").exists())

    def test_checked_in_policy_is_versioned_and_schema_valid(self) -> None:
        self.assertTrue(POLICY.is_file(), "the checked-in leaderboard policy is required")
        self.assertTrue(POLICY_SCHEMA.is_file(), "the leaderboard policy schema is required")
        from scripts.validate_benchmark import validate_schema_instance

        policy = json.loads(POLICY.read_text(encoding="utf-8"))
        schema = json.loads(POLICY_SCHEMA.read_text(encoding="utf-8"))
        self.assertEqual(validate_schema_instance(policy, schema), [])
        self.assertEqual(policy["policy_id"], "leaderboard-v1")
        self.assertEqual(policy["policy_version"], "1.0.0")

    def test_incomplete_global_coverage_stays_unranked_but_profile_view_can_rank(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            records = [
                _run_record("model-a:free", task_id, True, sequence)
                for sequence, task_id in enumerate(("ALPHA-01", "ALPHA-02"), start=1)
            ]
            _build_synthetic_input(
                root,
                passed_tasks_by_model={"model-a:free": set()},
                records_override=records,
            )
            result = _run_builder(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads((root / "leaderboard.json").read_text(encoding="utf-8"))
            self.assertEqual(output["overall"]["ranked"], [])
            self.assertEqual(output["overall"]["unranked"][0]["status"], "unranked")
            self.assertFalse(output["publication"]["ranking_available"])
            self.assertEqual(
                [item["model_id"] for item in output["profiles"]["alpha"]["ranked"]],
                ["model-a:free"],
            )
            self.assertEqual(output["profiles"]["alpha"]["ranked"][0]["status"], "provisional")
            self.assertEqual(output["profiles"]["beta"]["ranked"], [])

    def test_repeated_complete_coverage_is_confirmed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task_ids = [task["id"] for task in _base_ledger()["tasks"]]
            records = [
                _run_record("model-a:free", task_id, True, sequence)
                for sequence, task_id in enumerate(task_ids * 3, start=1)
            ]
            _build_synthetic_input(
                root,
                passed_tasks_by_model={"model-a:free": set()},
                records_override=records,
            )
            result = _run_builder(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads((root / "leaderboard.json").read_text(encoding="utf-8"))
            self.assertEqual(output["overall"]["ranked"][0]["status"], "confirmed")
            model = output["models"][0]
            self.assertEqual(model["coverage"]["minimum_replicates"], 3)
            self.assertEqual(model["coverage"]["maximum_replicates"], 3)
            self.assertTrue(output["publication"]["routing_recommendation_allowed"])

    def test_hard_failure_prevents_full_pass_even_if_status_is_passed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            records = []
            for sequence, task in enumerate(_base_ledger()["tasks"], start=1):
                record = _run_record("model-a:free", task["id"], True, sequence)
                record["hard_failures"] = [
                    {"id": "unsafe-result", "condition": "test", "evidence": ["synthetic failure"]}
                ]
                records.append(record)
            _build_synthetic_input(
                root,
                passed_tasks_by_model={"model-a:free": set()},
                records_override=records,
            )
            result = _run_builder(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads((root / "leaderboard.json").read_text(encoding="utf-8"))
            metrics = output["overall"]["ranked"][0]["metrics"]
            self.assertEqual(metrics["full_contract_pass_rate"], 0)
            self.assertEqual(metrics["hard_failure_rate"], 1)

    def test_excluded_roster_identity_is_visible_and_never_ranked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            roster = _base_roster(("tencent/hy3:free",))
            roster["models"][0].update(
                {
                    "resolved_model_id": "unresolved",
                    "provider_resolved": "unresolved",
                    "availability": "excluded",
                    "exclusion_reason": "Nous reported that the free period had ended",
                }
            )
            record = _run_record("tencent/hy3:free", "ALPHA-01", False, 1)
            record.update(
                {
                    "model_resolved": "unresolved",
                    "provider_resolved": "unresolved",
                    "resolution_status": "unresolved",
                    "execution_status": "failed",
                    "hard_failures": [
                        {"id": "invalid-output", "condition": "provider", "evidence": ["HTTP 404"]}
                    ],
                }
            )
            _build_synthetic_input(
                root,
                passed_tasks_by_model={"tencent/hy3:free": set()},
                roster=roster,
                records_override=[record],
            )
            result = _run_builder(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads((root / "leaderboard.json").read_text(encoding="utf-8"))
            self.assertEqual(output["overall"]["ranked"], [])
            self.assertEqual(output["overall"]["excluded"][0]["model_id"], "tencent/hy3:free")
            self.assertEqual(output["overall"]["excluded"][0]["status"], "excluded")
            self.assertEqual(output["models"][0]["exclusion_reason"], "Nous reported that the free period had ended")
            self.assertEqual(output["aggregate"]["attempted_runs"], 1)
            self.assertEqual(output["aggregate"]["comparable_resolved_runs"], 0)
            self.assertEqual(output["aggregate"]["excluded_provider_or_identity_runs"], 1)
            self.assertEqual(output["aggregate"]["hard_failure_runs"], 0)

    def test_excluded_roster_entity_never_contributes_even_with_resolved_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model_id = "tencent/hy3:free"
            roster = _base_roster((model_id,))
            roster["models"][0].update(
                {
                    "availability": "excluded",
                    "exclusion_reason": "free period ended",
                }
            )
            record = _run_record(model_id, "ALPHA-01", True, 1)
            _build_synthetic_input(
                root,
                passed_tasks_by_model={model_id: set()},
                roster=roster,
                records_override=[record],
            )
            result = _run_builder(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads((root / "leaderboard.json").read_text(encoding="utf-8"))
            self.assertEqual(output["aggregate"]["comparable_resolved_runs"], 0)
            self.assertEqual(output["aggregate"]["excluded_provider_or_identity_runs"], 1)
            self.assertIsNone(output["models"][0]["metrics"]["full_contract_pass_rate"])

    def test_unresolved_runtime_identity_is_excluded_without_aborting_build(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model_id = "model-a:free"
            record = _run_record(model_id, "ALPHA-01", False, 1)
            record.update(
                {
                    "model_resolved": "unresolved",
                    "provider_resolved": "unresolved",
                    "resolution_status": "unresolved",
                    "execution_status": "failed",
                    "failure_class": "usage-failure",
                    "status": "failed",
                }
            )
            _build_synthetic_input(
                root,
                passed_tasks_by_model={model_id: set()},
                records_override=[record],
            )
            result = _run_builder(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads((root / "leaderboard.json").read_text(encoding="utf-8"))
            self.assertEqual(output["overall"]["ranked"], [])
            self.assertEqual(output["overall"]["unranked"][0]["model_id"], model_id)
            self.assertEqual(output["aggregate"]["attempted_runs"], 1)
            self.assertEqual(output["aggregate"]["comparable_resolved_runs"], 0)
            self.assertEqual(output["aggregate"]["excluded_provider_or_identity_runs"], 1)

    def test_overall_ranking_weights_profiles_equally(self) -> None:
        """A four-task profile must not outweigh a two-task profile."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            passed_tasks_by_model = {
                "model-a:free": {"ALPHA-01", "ALPHA-02", "BETA-01", "BETA-02"},
                "model-b:free": {"BETA-01", "BETA-02", "BETA-03", "BETA-04"},
            }
            _build_synthetic_input(root, passed_tasks_by_model=passed_tasks_by_model)
            result = _run_builder(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads((root / "leaderboard.json").read_text(encoding="utf-8"))
            ranked = output["overall"]["ranked"]
            self.assertEqual([item["model_id"] for item in ranked], ["model-a:free", "model-b:free"])
            self.assertAlmostEqual(ranked[0]["metrics"]["full_contract_pass_rate"], 0.75)
            self.assertAlmostEqual(ranked[1]["metrics"]["full_contract_pass_rate"], 0.5)

    def test_human_scores_are_normalized_without_overriding_contract_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task_ids = [task["id"] for task in _base_ledger()["tasks"]]
            records = []
            for sequence, task_id in enumerate(task_ids, start=1):
                record = _run_record("model-a:free", task_id, True, sequence)
                record["human_scores"] = {"dimensions": {"quality": 4}}
                records.append(record)
            _build_synthetic_input(
                root,
                passed_tasks_by_model={"model-a:free": set()},
                records_override=records,
            )
            result = _run_builder(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads((root / "leaderboard.json").read_text(encoding="utf-8"))
            metrics = output["overall"]["ranked"][0]["metrics"]
            self.assertEqual(metrics["full_contract_pass_rate"], 1)
            self.assertEqual(metrics["human_quality_score"], 1)
            self.assertEqual(metrics["human_score_coverage"], 1)

    def test_tie_breakers_are_applied_in_declared_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task_ids = [task["id"] for task in _base_ledger()["tasks"]]
            records = []
            sequence = 1
            for model_id, score in (("model-a:free", 2), ("model-b:free", 4)):
                for task_id in task_ids:
                    record = _run_record(model_id, task_id, True, sequence)
                    record["human_scores"] = {"score": score}
                    records.append(record)
                    sequence += 1
            _build_synthetic_input(
                root,
                passed_tasks_by_model={"model-a:free": set(), "model-b:free": set()},
                records_override=records,
            )
            result = _run_builder(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads((root / "leaderboard.json").read_text(encoding="utf-8"))
            self.assertEqual(
                [item["model_id"] for item in output["overall"]["ranked"]],
                ["model-b:free", "model-a:free"],
            )

    def test_invalid_output_is_counted_and_cannot_be_a_full_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            records = []
            for sequence, task in enumerate(_base_ledger()["tasks"], start=1):
                record = _run_record("model-a:free", task["id"], False, sequence)
                record["hard_failures"] = [
                    {"id": "invalid-output", "condition": "decoder", "evidence": ["invalid JSON"]}
                ]
                records.append(record)
            _build_synthetic_input(
                root,
                passed_tasks_by_model={"model-a:free": set()},
                records_override=records,
            )
            result = _run_builder(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads((root / "leaderboard.json").read_text(encoding="utf-8"))
            metrics = output["overall"]["ranked"][0]["metrics"]
            self.assertEqual(metrics["full_contract_pass_rate"], 0)
            self.assertEqual(metrics["hard_failure_rate"], 1)
            self.assertEqual(metrics["invalid_output_rate"], 1)

    def test_output_schema_and_raw_run_references_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _build_synthetic_input(
                root,
                passed_tasks_by_model={
                    "model-a:free": {task["id"] for task in _base_ledger()["tasks"]},
                },
            )
            result = _run_builder(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads((root / "leaderboard.json").read_text(encoding="utf-8"))
            schema = json.loads((ROOT / "schemas" / "leaderboard-output.schema.json").read_text(encoding="utf-8"))
            from scripts.validate_benchmark import validate_schema_instance

            self.assertEqual(validate_schema_instance(output, schema), [])
            cell = output["models"][0]["task_cells"][0]
            self.assertEqual(cell["comparable_runs"], 1)
            self.assertTrue(cell["runs"][0]["record_path"].startswith("records/"))
            self.assertTrue(cell["runs"][0]["raw_output_reference"].startswith("runs/"))

    def test_tampered_raw_output_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _build_synthetic_input(
                root,
                passed_tasks_by_model={
                    "model-a:free": {task["id"] for task in _base_ledger()["tasks"]},
                },
            )
            raw_path = root / "runs" / "model-a-free-ALPHA-01-1.txt"
            raw_path.write_text("tampered\n", encoding="utf-8")
            result = _run_builder(root)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("fingerprint", result.stderr)

    def test_aggregate_counts_are_explicit_and_identity_aware(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            records = []
            for sequence, task in enumerate(_base_ledger()["tasks"], start=1):
                records.append(_run_record("model-a:free", task["id"], True, sequence))
                invalid = _run_record("model-b:free", task["id"], False, sequence + 6)
                invalid["hard_failures"] = [
                    {"id": "invalid-output", "condition": "decoder", "evidence": ["invalid JSON"]}
                ]
                records.append(invalid)
            _build_synthetic_input(
                root,
                passed_tasks_by_model={"model-a:free": set(), "model-b:free": set()},
                records_override=records,
            )
            result = _run_builder(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads((root / "leaderboard.json").read_text(encoding="utf-8"))
            self.assertEqual(
                output["aggregate"],
                {
                    "attempted_runs": 12,
                    "comparable_resolved_runs": 12,
                    "excluded_provider_or_identity_runs": 0,
                    "full_contract_pass_runs": 6,
                    "all_automatic_checks_pass_runs": 6,
                    "hard_failure_runs": 6,
                    "invalid_output_runs": 6,
                    "process_or_timeout_failures": 0,
                    "human_scores_assigned": False,
                },
            )

    def test_unexpected_input_keys_are_rejected_by_the_checked_in_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = _build_synthetic_input(
                root,
                passed_tasks_by_model={"model-a:free": set()},
            )
            input_manifest = json.loads(input_path.read_text(encoding="utf-8"))
            input_manifest["unexpected"] = "must be rejected"
            _write_json(input_path, input_manifest)
            result = _run_builder(root)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("schema", result.stderr)

    def test_mismatched_release_lock_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            records = []
            for sequence, task in enumerate(_base_ledger()["tasks"], start=1):
                record = _run_record("model-a:free", task["id"], True, sequence)
                if sequence == 2:
                    record["release_lock_fingerprint"] = "sha256:" + "1" * 64
                records.append(record)
            _build_synthetic_input(
                root,
                passed_tasks_by_model={"model-a:free": set()},
                records_override=records,
            )
            result = _run_builder(root)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("release-lock", result.stderr)

    def test_new_roster_model_is_unranked_until_it_has_complete_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            roster = _base_roster(("model-a:free", "model-b:free"))
            _build_synthetic_input(
                root,
                passed_tasks_by_model={
                    "model-a:free": {task["id"] for task in _base_ledger()["tasks"]},
                },
                roster=roster,
            )
            result = _run_builder(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads((root / "leaderboard.json").read_text(encoding="utf-8"))
            self.assertEqual([item["model_id"] for item in output["overall"]["ranked"]], ["model-a:free"])
            self.assertEqual(output["overall"]["unranked"][0]["model_id"], "model-b:free")
            self.assertEqual(output["overall"]["unranked"][0]["reason_codes"], ["incomplete-task-coverage", "no-evidence"])

    def test_empty_input_remains_schema_valid_and_unranked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = _build_synthetic_input(
                root,
                passed_tasks_by_model={"model-a:free": set()},
            )
            input_manifest = json.loads(input_path.read_text(encoding="utf-8"))
            input_manifest["runs"] = []
            _write_json(input_path, input_manifest)
            result = _run_builder(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads((root / "leaderboard.json").read_text(encoding="utf-8"))
            schema = json.loads((ROOT / "schemas" / "leaderboard-output.schema.json").read_text(encoding="utf-8"))
            from scripts.validate_benchmark import validate_schema_instance

            self.assertEqual(validate_schema_instance(output, schema), [])
            self.assertNotIn("release_lock_fingerprint", output)
            self.assertEqual(output["overall"]["ranked"], [])
            self.assertEqual(output["overall"]["unranked"][0]["reason_codes"], ["incomplete-task-coverage", "no-evidence"])

    def test_generation_is_byte_deterministic_when_input_runs_are_reordered(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = _build_synthetic_input(
                root,
                passed_tasks_by_model={
                    "model-a:free": {task["id"] for task in _base_ledger()["tasks"]},
                    "model-b:free": set(),
                },
            )
            first = _run_builder(root)
            self.assertEqual(first.returncode, 0, first.stderr)
            first_bytes = (root / "leaderboard.json").read_bytes()
            input_manifest = json.loads(input_path.read_text(encoding="utf-8"))
            input_manifest["runs"].reverse()
            _write_json(input_path, input_manifest)
            second = _run_builder(root)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(first_bytes, (root / "leaderboard.json").read_bytes())

    def test_generation_is_byte_deterministic_for_the_same_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _build_synthetic_input(
                root,
                passed_tasks_by_model={
                    "model-a:free": {task["id"] for task in _base_ledger()["tasks"]},
                    "model-b:free": set(),
                },
            )
            first = _run_builder(root)
            self.assertEqual(first.returncode, 0, first.stderr)
            first_bytes = (root / "leaderboard.json").read_bytes()
            second = _run_builder(root)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(first_bytes, (root / "leaderboard.json").read_bytes())



if __name__ == "__main__":
    unittest.main()
