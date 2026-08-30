"""Contract tests for the agent-profile benchmark ledger."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

import scripts.validate_benchmark as validator
from scripts.validate_benchmark import validate_ledger, validate_schema_instance


ROOT = Path(__file__).resolve().parents[1]
LEDGER_PATH = ROOT / "data" / "task-ledger.json"
SCHEMA_PATH = ROOT / "schemas" / "task-contract.schema.json"
VALIDATOR_PATH = ROOT / "scripts" / "validate_benchmark.py"

EXPECTED_PROFILES = {
    "kody",
    "aegis",
    "arch",
    "atlas",
    "tank",
    "oracle",
    "sentinel",
    "morph",
    "seraph",
}

EXPECTED_TASKS_BY_PROFILE = {
    "kody": ["KODY-01", "KODY-02"],
    "aegis": ["AEGIS-01", "AEGIS-02"],
    "arch": ["ARCH-01", "ARCH-02"],
    "atlas": ["ATLAS-01", "ATLAS-02"],
    "tank": ["TANK-01", "TANK-02"],
    "oracle": ["ORACLE-01", "ORACLE-02"],
    "sentinel": ["SENTINEL-01", "SENTINEL-02"],
    "morph": ["MORPH-01", "MORPH-02"],
    "seraph": ["SERAPH-01", "SERAPH-02"],
}


class BenchmarkContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.ledger = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
        cls.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    def run_validator_on_text(self, text: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "ledger.json"
            input_path.write_text(text, encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(VALIDATOR_PATH), "--input", str(input_path)],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )

    def run_validator_on_bytes(self, content: bytes) -> subprocess.CompletedProcess[bytes]:
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "ledger.json"
            input_path.write_bytes(content)
            return subprocess.run(
                [sys.executable, str(VALIDATOR_PATH), "--input", str(input_path)],
                cwd=ROOT,
                check=False,
                capture_output=True,
            )

    def test_validator_accepts_the_canonical_ledger(self) -> None:
        result = subprocess.run(
            [sys.executable, str(VALIDATOR_PATH)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("9 profiles", result.stdout)
        self.assertIn("18 tasks", result.stdout)

    def test_cli_routes_validation_through_the_schema_helper(self) -> None:
        stderr = io.StringIO()
        with patch.object(validator, "validate_schema_instance", return_value=["sentinel schema rejection"]):
            with redirect_stderr(stderr):
                result = validator.main([])

        self.assertEqual(result, 1)
        self.assertIn("sentinel schema rejection", stderr.getvalue())

    def test_schema_loader_rejects_duplicate_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            schema_path = Path(directory) / "schema.json"
            schema_path.write_text('{"type":"object","type":"array"}', encoding="utf-8")
            with patch.object(validator, "SCHEMA_PATH", schema_path):
                errors = validate_ledger(self.ledger)

        self.assertTrue(any("duplicate JSON object key" in error for error in errors))

    def test_cli_rejects_malformed_checked_in_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            schema_path = Path(directory) / "schema.json"
            schema_path.write_text('{"type":"object","properties":{"tasks":null}}', encoding="utf-8")
            stderr = io.StringIO()
            with patch.object(validator, "SCHEMA_PATH", schema_path):
                with redirect_stderr(stderr):
                    result = validator.main([])

        self.assertEqual(result, 1)
        self.assertIn("schema is malformed or unsupported", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_cli_rejects_an_invalid_ledger(self) -> None:
        broken = json.loads(json.dumps(self.ledger))
        broken["tasks"][0]["id"] = "KODY-99"
        broken["profiles"][0]["task_ids"][0] = "KODY-99"

        result = self.run_validator_on_text(json.dumps(broken))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("validation failed", result.stderr)

    def test_cli_rejects_duplicate_json_object_keys(self) -> None:
        canonical = json.dumps(self.ledger, separators=(",", ":"))
        cases = {}
        for first, second in (("contract-draft", "benchmark-ready"), ("benchmark-ready", "contract-draft")):
            cases[f"root_{first}_first"] = canonical.replace(
                '"status":"contract-draft"',
                f'"status":"{first}","status":"{second}"',
                1,
            )
            cases[f"nested_{first}_first"] = canonical.replace(
                '"id":"KODY-01"',
                f'"id":"{first}","id":"{second}"',
                1,
            )
        for label, text in cases.items():
            with self.subTest(case=label):
                result = self.run_validator_on_text(text)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("duplicate JSON object key", result.stderr)

    def test_cli_rejects_invalid_utf8(self) -> None:
        result = self.run_validator_on_bytes(b"{\xff")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"validation failed", result.stderr)
        self.assertNotIn(b"Traceback", result.stderr)

    def test_cli_does_not_echo_unreadable_input_path(self) -> None:
        missing_path = Path.home() / "agent-profile-benchmark-missing-ledger.json"
        result = subprocess.run(
            [sys.executable, str(VALIDATOR_PATH), "--input", str(missing_path)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unable to read input", result.stderr)
        self.assertNotIn(str(missing_path), result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_validator_rejects_duplicate_task_ids(self) -> None:
        broken = json.loads(json.dumps(self.ledger))
        broken["tasks"][1]["id"] = broken["tasks"][0]["id"]

        errors = validate_ledger(broken)

        self.assertTrue(any("duplicate ids" in error for error in errors))

    def test_validator_rejects_duplicate_required_output_fields(self) -> None:
        broken = json.loads(json.dumps(self.ledger))
        broken["tasks"][0]["prompt_contract"]["required_output_fields"] = [
            "goal",
            "goal",
            "goal",
        ]

        errors = validate_ledger(broken)

        self.assertTrue(any("unique" in error for error in errors))

    def test_validator_rejects_non_string_measurement_ids(self) -> None:
        mutations = {
            "automatic_check": ("automatic_checks", "id"),
            "human_dimension": ("human_dimensions", "id"),
            "hard_failure": ("hard_failures", "id"),
        }
        for label, (collection, field) in mutations.items():
            with self.subTest(case=label):
                broken = json.loads(json.dumps(self.ledger))
                broken["tasks"][0]["measurement"][collection][0][field] = 1
                errors = validate_ledger(broken)
                self.assertTrue(errors)

    def test_validator_rejects_inconsistent_readiness_status(self) -> None:
        broken = json.loads(json.dumps(self.ledger))
        broken["status"] = "benchmark-ready"
        for task in broken["tasks"]:
            task["status"] = "benchmark-ready"

        errors = validate_ledger(broken)

        self.assertIn(
            "ledger readiness cannot be benchmark-ready until evaluator and control evidence is modeled",
            errors,
        )
        self.assertFalse(any("status must match ledger readiness" in error for error in errors))

    def test_fixture_ready_requires_frozen_inputs(self) -> None:
        broken = json.loads(json.dumps(self.ledger))
        broken["status"] = "fixture-ready"
        for task in broken["tasks"]:
            task["status"] = "fixture-ready"

        errors = validate_ledger(broken)

        self.assertIn(
            "ledger readiness cannot be fixture-ready until fixture, prompt, evaluator, and control evidence is modeled",
            errors,
        )
        self.assertTrue(any("fixture-ready requires frozen" in error for error in errors))

    def test_fixture_ready_is_rejected_without_evidence(self) -> None:
        broken = json.loads(json.dumps(self.ledger))
        broken["status"] = "fixture-ready"
        for task in broken["tasks"]:
            task["status"] = "fixture-ready"
            task["fixture"]["status"] = "frozen"
            task["prompt_contract"]["status"] = "frozen"

        errors = validate_ledger(broken)

        self.assertIn(
            "ledger readiness cannot be fixture-ready until fixture, prompt, evaluator, and control evidence is modeled",
            errors,
        )
        self.assertFalse(any("fixture-ready requires frozen" in error for error in errors))
        self.assertFalse(any("status must match ledger readiness" in error for error in errors))

    def test_contract_draft_requires_unfrozen_inputs(self) -> None:
        broken = json.loads(json.dumps(self.ledger))
        for task in broken["tasks"]:
            task["fixture"]["status"] = "frozen"
            task["prompt_contract"]["status"] = "frozen"

        errors = validate_ledger(broken)

        self.assertTrue(any("contract-draft requires unfrozen" in error for error in errors))

    def test_validator_rejects_sequence_swaps(self) -> None:
        broken = json.loads(json.dumps(self.ledger))
        broken["tasks"][0]["sequence"], broken["tasks"][1]["sequence"] = 2, 1

        errors = validate_ledger(broken)

        self.assertTrue(any("sequence must be" in error and "frozen task ID" in error for error in errors))

    def test_validator_rejects_frozen_task_content_replacement(self) -> None:
        broken = json.loads(json.dumps(self.ledger))
        broken["tasks"][0]["title"] = "Repurposed task under the same ID"

        errors = validate_ledger(broken)

        self.assertIn("ledger content does not match the frozen v0.1.0 contract fingerprint", errors)

    def test_validator_rejects_replacement_of_the_frozen_task_registry(self) -> None:
        broken = json.loads(json.dumps(self.ledger))
        broken["tasks"][1]["id"] = "KODY-99"
        broken["profiles"][0]["task_ids"][1] = "KODY-99"

        errors = validate_ledger(broken)

        self.assertTrue(any("registry" in error or "KODY-02" in error for error in errors))

    def test_validator_rejects_malformed_types_without_crashing(self) -> None:
        cases = {
            "unhashable_profile_id": ("tasks", 0, "profile_id", ["kody"]),
            "unhashable_task_id": ("tasks", 0, "id", ["KODY-01"]),
            "unhashable_sequence": ("tasks", 0, "sequence", [1]),
            "null_provenance_values": ("evaluation_policy", None, "provenance_values", None),
            "null_status_values": ("evaluation_policy", None, "status_values", None),
        }
        for label, (parent, index, field, value) in cases.items():
            with self.subTest(case=label):
                broken = json.loads(json.dumps(self.ledger))
                target = broken[parent] if index is None else broken[parent][index]
                target[field] = value
                result = self.run_validator_on_text(json.dumps(broken))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(field, result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_validator_rejects_non_string_top_level_contract_fields(self) -> None:
        mutations = {
            "$schema": [],
            "benchmark_version": 1,
        }
        for field, replacement in mutations.items():
            with self.subTest(field=field):
                broken = json.loads(json.dumps(self.ledger))
                broken[field] = replacement
                errors = validate_ledger(broken)
                self.assertTrue(errors)

    def test_validator_rejects_unfrozen_contract_identity(self) -> None:
        mutations = {
            "$schema": "schemas/other-task-contract.schema.json",
            "benchmark_version": "9.9.9",
        }
        for field, replacement in mutations.items():
            with self.subTest(field=field):
                broken = json.loads(json.dumps(self.ledger))
                broken[field] = replacement
                errors = validate_ledger(broken)
                self.assertTrue(errors)

    def test_validator_rejects_boundary_metadata_mutations(self) -> None:
        mutations = {
            "duplicate_provenance_value": lambda value: value["evaluation_policy"]["provenance_values"].append(
                "synthetic"
            ),
            "network_tool": lambda value: value["tasks"][0]["fixture"].__setitem__(
                "allowed_tools", ["web_search"]
            ),
            "incompatible_source_policy": lambda value: value["tasks"][0]["fixture"].__setitem__(
                "source_policy", "sanitized_or_synthetic_reconstruction"
            ),
            "private_absolute_path": lambda value: value["tasks"][0]["fixture"].__setitem__(
                "notes", f"captured from {Path.home() / 'private-fixture' / 'records.json'}"
            ),
            "credential_shaped_text": lambda value: value["tasks"][0].__setitem__(
                "provenance_note", "password='" + "x" * 16 + "'"
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(case=label):
                broken = json.loads(json.dumps(self.ledger))
                mutate(broken)
                result = self.run_validator_on_text(json.dumps(broken))
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("Traceback", result.stderr)

    def test_validator_rejects_boolean_human_scale_bounds(self) -> None:
        for field in ("minimum", "maximum"):
            with self.subTest(field=field):
                broken = json.loads(json.dumps(self.ledger))
                broken["evaluation_policy"]["human_scale"][field] = False
                errors = validate_ledger(broken)
                self.assertTrue(errors)

    def test_validator_rejects_unknown_fields(self) -> None:
        broken = json.loads(json.dumps(self.ledger))
        broken["tasks"][0]["unplanned_field"] = True

        errors = validate_ledger(broken)

        self.assertTrue(any("unexpected keys" in error for error in errors))

    def test_validator_rejects_unknown_nested_fields(self) -> None:
        mutations = {
            "policy_anchors": lambda value: value["evaluation_policy"]["human_scale"]["anchors"].__setitem__(
                "3", "unsupported"
            ),
            "dimension_anchors": lambda value: value["tasks"][0]["measurement"]["human_dimensions"][0][
                "anchors"
            ].__setitem__("3", "unsupported"),
        }
        for label, mutate in mutations.items():
            with self.subTest(case=label):
                broken = json.loads(json.dumps(self.ledger))
                mutate(broken)
                errors = validate_ledger(broken)
                self.assertTrue(any("unexpected keys" in error for error in errors))

    def test_validator_rejects_non_string_enum_values(self) -> None:
        mutations = {
            "ledger_status": lambda value: value.__setitem__("status", []),
            "task_provenance": lambda value: value["tasks"][0].__setitem__("provenance", []),
            "task_privacy_class": lambda value: value["tasks"][0].__setitem__("privacy_class", {}),
            "task_status": lambda value: value["tasks"][0].__setitem__("status", []),
            "fixture_status": lambda value: value["tasks"][0]["fixture"].__setitem__("status", []),
            "prompt_format": lambda value: value["tasks"][0]["prompt_contract"].__setitem__(
                "required_output_format", []
            ),
            "measurement_type": lambda value: value["tasks"][0]["measurement"].__setitem__(
                "primary_type", {}
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(case=label):
                broken = json.loads(json.dumps(self.ledger))
                mutate(broken)
                result = self.run_validator_on_text(json.dumps(broken))
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("Traceback", result.stderr)
                field = {
                    "ledger_status": "status",
                    "task_provenance": "provenance",
                    "task_privacy_class": "privacy_class",
                    "task_status": "status",
                    "fixture_status": "status",
                    "prompt_format": "required_output_format",
                    "measurement_type": "primary_type",
                }[label]
                self.assertIn(field, result.stderr)

    def test_schema_declares_the_public_contract(self) -> None:
        self.assertEqual(self.schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertEqual(
            self.schema["$id"],
            "https://github.com/calvinnwq/agent-profile-benchmark/schemas/task-contract.schema.json",
        )
        self.assertEqual(self.schema["title"], "Agent Profile Benchmark Task Ledger")
        self.assertEqual(
            self.schema["properties"]["$schema"]["const"],
            "../schemas/task-contract.schema.json",
        )
        self.assertEqual(self.schema["properties"]["benchmark_version"]["const"], "0.1.0")
        self.assertIn("profiles", self.schema["required"])
        self.assertIn("tasks", self.schema["required"])
        self.assertEqual(self.schema["properties"]["profiles"]["minItems"], 9)
        self.assertEqual(self.schema["properties"]["profiles"]["maxItems"], 9)
        self.assertEqual(self.schema["properties"]["tasks"]["minItems"], 18)
        self.assertEqual(self.schema["properties"]["tasks"]["maxItems"], 18)
        self.assertEqual(
            self.schema["$defs"]["evaluation_policy"]["properties"]["default_allowed_tools"]["const"],
            [],
        )
        self.assertEqual(
            self.schema["$defs"]["evaluation_policy"]["properties"]["score_order"]["const"],
            ["hard_failures", "automatic_checks", "human_dimensions"],
        )
        self.assertEqual(
            self.schema["$defs"]["evaluation_policy"]["properties"]["provenance_values"]["const"],
            ["historical_candidate", "synthetic"],
        )
        self.assertEqual(
            self.schema["$defs"]["evaluation_policy"]["properties"]["status_values"]["const"],
            ["contract-draft", "fixture-ready", "benchmark-ready"],
        )
        self.assertEqual(self.schema["$defs"]["fixture"]["properties"]["allowed_tools"]["const"], [])
        self.assertTrue(
            self.schema["$defs"]["prompt_contract"]["properties"]["required_output_fields"]["uniqueItems"]
        )

    def test_checked_in_schema_accepts_canonical_ledger_and_rejects_mutations(self) -> None:
        self.assertEqual(validate_schema_instance(self.ledger, self.schema), [])

        broken = json.loads(json.dumps(self.ledger))
        broken["tasks"][0]["prompt_contract"]["required_output_fields"] = [
            "goal",
            "goal",
            "goal",
        ]
        errors = validate_schema_instance(broken, self.schema)
        self.assertTrue(any("unique" in error for error in errors))

        unsupported_schema = json.loads(json.dumps(self.schema))
        unsupported_schema["$defs"]["task"]["allOf"] = []
        errors = validate_schema_instance(self.ledger, unsupported_schema)
        self.assertTrue(any("unsupported schema keywords" in error for error in errors))

        optional_unsupported_schema = json.loads(json.dumps(self.schema))
        optional_unsupported_schema["$defs"]["task"]["properties"]["title"]["allOf"] = []
        errors = validate_schema_instance(self.ledger, optional_unsupported_schema)
        self.assertTrue(any("unsupported schema keywords" in error for error in errors))

        unused_unsupported_schema = json.loads(json.dumps(self.schema))
        unused_unsupported_schema["$defs"]["unused"] = {"type": "object", "allOf": []}
        errors = validate_schema_instance(self.ledger, unused_unsupported_schema)
        self.assertTrue(any("unsupported schema keywords" in error for error in errors))

        malformed_keyword_schema = json.loads(json.dumps(self.schema))
        malformed_keyword_schema["$defs"]["task"]["properties"]["id"]["type"] = ["string"]
        errors = validate_schema_instance(self.ledger, malformed_keyword_schema)
        self.assertTrue(any("schema is malformed or unsupported" in error for error in errors))

        malformed_schema = json.loads(json.dumps(self.schema))
        malformed_schema["properties"]["tasks"] = None
        errors = validate_schema_instance(self.ledger, malformed_schema)
        self.assertTrue(any("schema is malformed or unsupported" in error for error in errors))

    def test_ledger_covers_exactly_two_tasks_per_profile(self) -> None:
        profiles = {profile["id"] for profile in self.ledger["profiles"]}
        self.assertEqual(profiles, EXPECTED_PROFILES)
        self.assertEqual(len(self.ledger["profiles"]), 9)

        tasks_by_profile = {profile_id: [] for profile_id in profiles}
        for task in self.ledger["tasks"]:
            tasks_by_profile[task["profile_id"]].append(task)

        self.assertEqual(len(self.ledger["tasks"]), 18)
        self.assertTrue(all(len(tasks) == 2 for tasks in tasks_by_profile.values()))
        for profile in self.ledger["profiles"]:
            self.assertEqual(len(profile["task_ids"]), 2)
            self.assertEqual(profile["task_ids"], EXPECTED_TASKS_BY_PROFILE[profile["id"]])
            self.assertEqual(
                set(profile["task_ids"]),
                {task["id"] for task in tasks_by_profile[profile["id"]]},
            )

    def test_task_ids_and_sequences_are_unique_and_stable(self) -> None:
        task_ids = [task["id"] for task in self.ledger["tasks"]]
        self.assertEqual(len(task_ids), len(set(task_ids)))
        for profile_id in EXPECTED_PROFILES:
            profile_tasks = [
                task for task in self.ledger["tasks"] if task["profile_id"] == profile_id
            ]
            self.assertEqual(
                {task["sequence"] for task in profile_tasks},
                {1, 2},
            )
            self.assertTrue(all(task["id"].startswith(profile_id.upper() + "-") for task in profile_tasks))

    def test_every_task_declares_a_measurement_plan(self) -> None:
        required_task_fields = {
            "id",
            "profile_id",
            "sequence",
            "title",
            "task_family",
            "provenance",
            "privacy_class",
            "fixture",
            "prompt_contract",
            "measurement",
            "known_failure_modes",
            "status",
        }
        for task in self.ledger["tasks"]:
            with self.subTest(task=task["id"]):
                self.assertTrue(required_task_fields.issubset(task))
                self.assertEqual(task["fixture"]["live_web"], False)
                self.assertIsInstance(task["fixture"]["allowed_tools"], list)
                self.assertGreaterEqual(len(task["prompt_contract"]["required_output_fields"]), 3)
                self.assertGreaterEqual(len(task["measurement"]["automatic_checks"]), 1)
                self.assertGreaterEqual(len(task["measurement"]["human_dimensions"]), 2)
                self.assertGreaterEqual(len(task["measurement"]["hard_failures"]), 1)
                self.assertGreaterEqual(len(task["known_failure_modes"]), 1)

    def test_measurement_ids_are_unique_within_each_task(self) -> None:
        for task in self.ledger["tasks"]:
            check_ids = [check["id"] for check in task["measurement"]["automatic_checks"]]
            dimension_ids = [dimension["id"] for dimension in task["measurement"]["human_dimensions"]]
            with self.subTest(task=task["id"]):
                self.assertEqual(len(check_ids), len(set(check_ids)))
                self.assertEqual(len(dimension_ids), len(set(dimension_ids)))
                self.assertTrue(all(check_id for check_id in check_ids))
                self.assertTrue(all(dimension_id for dimension_id in dimension_ids))

    def test_human_indexes_match_the_canonical_ledger(self) -> None:
        tasks_by_id = {task["id"]: task for task in self.ledger["tasks"]}
        profile_names = {profile["id"]: profile["name"] for profile in self.ledger["profiles"]}

        task_index = (ROOT / "docs" / "task-ledger.md").read_text(encoding="utf-8")
        task_rows = [
            [cell.strip() for cell in line.strip().strip("|").split("|")]
            for line in task_index.splitlines()
            if line.startswith("| `")
        ]
        task_index_ids = [row[0].strip("`") for row in task_rows]
        self.assertEqual(len(task_rows), len(tasks_by_id))
        self.assertEqual(len(task_index_ids), len(set(task_index_ids)))
        self.assertEqual(set(task_index_ids), set(tasks_by_id))
        for task_id, profile, title, provenance, primary_evaluation in task_rows:
            task_id = task_id.strip("`")
            task = tasks_by_id[task_id]
            self.assertEqual(profile, profile_names[task["profile_id"]])
            self.assertEqual(title, task["title"])
            self.assertEqual(provenance.lower(), task["provenance"].replace("_", " "))
            self.assertTrue(primary_evaluation)

        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        readme_rows = [
            [cell.strip() for cell in line.strip().strip("|").split("|")]
            for line in readme.splitlines()
            if line.startswith("| ") and not line.startswith("|---") and not line.startswith("| Profile")
        ]
        readme_profile_names = [row[0] for row in readme_rows]
        self.assertEqual(len(readme_rows), len(self.ledger["profiles"]))
        self.assertEqual(len(readme_profile_names), len(set(readme_profile_names)))
        self.assertEqual(set(readme_profile_names), set(profile_names.values()))
        for profile_name, task_one, task_two in readme_rows:
            profile = next(profile for profile in self.ledger["profiles"] if profile["name"] == profile_name)
            profile_tasks = [tasks_by_id[task_id] for task_id in profile["task_ids"]]
            self.assertEqual([task_one, task_two], [task["title"] for task in profile_tasks])

        evaluation_contract = (ROOT / "docs" / "evaluation-contract.md").read_text(encoding="utf-8")
        self.assertIn("the validator accepts only `contract-draft`", evaluation_contract)
        self.assertIn("It rejects `fixture-ready`", evaluation_contract)
        self.assertIn("rejects `benchmark-ready`", evaluation_contract)

        serialized = json.dumps(self.ledger)
        forbidden_fragments = (
            "/Users/",
            "/Volumes/",
            "token",
            "password",
            "private financial",
            "customer data",
        )
        for fragment in forbidden_fragments:
            with self.subTest(fragment=fragment):
                self.assertNotIn(fragment.lower(), serialized.lower())


if __name__ == "__main__":
    unittest.main()
