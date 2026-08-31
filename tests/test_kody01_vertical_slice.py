"""End-to-end tests for the KODY-01 fixture and deterministic replay slice."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.validate_benchmark import validate_schema_instance


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "fixtures" / "kody-01"
MANIFEST_PATH = FIXTURE_DIR / "manifest.json"
FIXTURE_PATH = FIXTURE_DIR / "request-packet.json"
PROMPT_PATH = FIXTURE_DIR / "prompt.txt"
GOOD_PATH = FIXTURE_DIR / "controls" / "known-good.json"
BAD_PATH = FIXTURE_DIR / "controls" / "known-bad.json"
ORACLE_PATH = ROOT / "scripts" / "evaluate_kody01.py"
REPLAY_PATH = ROOT / "scripts" / "replay_kody01.py"
MODEL_RUNNER_PATH = ROOT / "scripts" / "run_kody01_model.py"
GATE_PATH = ROOT / "scripts" / "validate_kody01.py"
RUN_SCHEMA_PATH = ROOT / "schemas" / "kody-01-run-record.schema.json"


class Kody01VerticalSliceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        cls.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        cls.good = json.loads(GOOD_PATH.read_text(encoding="utf-8"))
        cls.bad = json.loads(BAD_PATH.read_text(encoding="utf-8"))
        cls.run_schema = json.loads(RUN_SCHEMA_PATH.read_text(encoding="utf-8"))

    def run_oracle(
        self,
        candidate: Path,
        fixture: Path = FIXTURE_PATH,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(ORACLE_PATH),
                "--fixture",
                str(fixture),
                "--candidate",
                str(candidate),
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

    def test_fixture_and_prompt_are_exactly_defined(self) -> None:
        self.assertEqual(self.fixture["fixture_id"], "kody-plan-extraction-v1")
        self.assertEqual(self.fixture["fixture_version"], "1.0.0")
        self.assertEqual(
            {item["id"] for item in self.fixture["hard_constraints"]},
            {"synthetic-only", "owner-membership", "approval-required", "budget-cap"},
        )
        self.assertEqual(self.fixture["available_owners"], ["Morgan", "Priya", "Lee"])
        self.assertEqual(
            {item["id"] for item in self.fixture["ambiguities"]},
            {"deadline-zone", "workspace-destination"},
        )
        prompt = PROMPT_PATH.read_text(encoding="utf-8")
        for required_text in (
            "Return exactly one JSON object and no Markdown.",
            "Preserve every hard constraint",
            "Make the task dependency graph explicit and acyclic.",
            "Do not perform or claim any external action.",
        ):
            self.assertIn(required_text, prompt)

    def test_manifest_binds_the_complete_control_slice(self) -> None:
        self.assertEqual(self.manifest["task_id"], "KODY-01")
        self.assertEqual(self.manifest["profile_id"], "kody")
        self.assertTrue(self.manifest["benchmark_ready"])
        referenced_paths = [
            self.manifest["fixture"]["path"],
            self.manifest["prompt"]["path"],
            self.manifest["evaluator"]["path"],
            self.manifest["output_schema"]["path"],
            self.manifest["run_record_schema"]["path"],
            self.manifest["release_gate"],
            *(control["path"] for control in self.manifest["controls"]),
        ]
        for relative_path in referenced_paths:
            with self.subTest(path=relative_path):
                self.assertTrue((MANIFEST_PATH.parent / relative_path).resolve().is_file())
        for manifest_key in ("fixture", "prompt"):
            path = MANIFEST_PATH.parent / self.manifest[manifest_key]["path"]
            fingerprint = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
            with self.subTest(fingerprint=manifest_key):
                self.assertEqual(fingerprint, self.manifest[manifest_key]["sha256"])
        self.assertEqual(
            [(control["condition"], control["expected_status"]) for control in self.manifest["controls"]],
            [
                ("known-good-control", "passed"),
                ("known-bad-control", "failed"),
            ],
        )

    def test_known_good_control_passes_all_automatic_checks(self) -> None:
        result = self.run_oracle(GOOD_PATH)

        self.assertEqual(result.returncode, 0, result.stderr)
        evaluation = json.loads(result.stdout)
        self.assertEqual(evaluation["status"], "passed")
        self.assertEqual(evaluation["hard_failures"], [])
        self.assertEqual(
            {check["id"] for check in evaluation["automatic_checks"]},
            {
                "required-fields",
                "constraint-coverage",
                "owner-membership",
                "dependency-dag",
                "assumption-labeling",
            },
        )
        self.assertTrue(all(check["status"] == "pass" for check in evaluation["automatic_checks"]))

    def test_known_bad_control_fails_specific_hard_gates(self) -> None:
        result = self.run_oracle(BAD_PATH)

        self.assertNotEqual(result.returncode, 0)
        evaluation = json.loads(result.stdout)
        self.assertEqual(evaluation["status"], "failed")
        self.assertTrue(
            {
                failure["id"] for failure in evaluation["hard_failures"]
            }.issuperset(
                {
                    "dropped-hard-constraint",
                    "invented-authority",
                    "unsafe-external-action",
                }
            )
        )
        self.assertTrue(any(check["status"] == "fail" for check in evaluation["automatic_checks"]))

    def test_assumption_labeling_allows_cross_references(self) -> None:
        candidate = json.loads(json.dumps(self.good))
        candidate["decisions"][0]["ambiguity_refs"] = ["deadline-zone"]
        candidate["decisions"][1]["ambiguity_refs"] = ["workspace-destination"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cross-references.json"
            path.write_text(json.dumps(candidate), encoding="utf-8")
            result = self.run_oracle(path)

        self.assertEqual(result.returncode, 0, result.stderr)
        evaluation = json.loads(result.stdout)
        checks = {check["id"]: check for check in evaluation["automatic_checks"]}
        self.assertEqual(checks["assumption-labeling"]["status"], "pass")

    def test_schema_errors_do_not_block_independent_checks(self) -> None:
        broken = json.loads(json.dumps(self.good))
        broken["tasks"][0]["action_type"] = "analysis"
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "schema-invalid.json"
            candidate.write_text(json.dumps(broken), encoding="utf-8")
            result = self.run_oracle(candidate)

        self.assertNotEqual(result.returncode, 0)
        evaluation = json.loads(result.stdout)
        self.assertEqual(evaluation["status"], "failed")
        checks = {check["id"]: check for check in evaluation["automatic_checks"]}
        self.assertEqual(checks["constraint-coverage"]["status"], "pass")
        self.assertEqual(checks["owner-membership"]["status"], "pass")
        self.assertEqual(checks["dependency-dag"]["status"], "pass")
        self.assertEqual(checks["assumption-labeling"]["status"], "pass")
        self.assertNotIn("dropped-hard-constraint", {failure["id"] for failure in evaluation["hard_failures"]})
        self.assertNotIn("invented-authority", {failure["id"] for failure in evaluation["hard_failures"]})
        self.assertNotIn("unsafe-external-action", {failure["id"] for failure in evaluation["hard_failures"]})

    def test_oracle_rejects_duplicate_json_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "duplicate.json"
            candidate.write_text('{"goal":"x","goal":"y"}', encoding="utf-8")
            result = self.run_oracle(candidate)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("duplicate JSON object key", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_oracle_rejects_malformed_structures_without_traceback(self) -> None:
        broken = json.loads(json.dumps(self.good))
        broken["tasks"][0]["id"] = []
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "malformed.json"
            candidate.write_text(json.dumps(broken), encoding="utf-8")
            result = self.run_oracle(candidate)

        self.assertNotEqual(result.returncode, 0)
        evaluation = json.loads(result.stdout)
        self.assertEqual(evaluation["status"], "failed")
        self.assertNotIn("Traceback", result.stderr)

    def test_oracle_rejects_incomplete_nested_task_objects(self) -> None:
        broken = json.loads(json.dumps(self.good))
        del broken["tasks"][0]["title"]
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "incomplete-task.json"
            candidate.write_text(json.dumps(broken), encoding="utf-8")
            result = self.run_oracle(candidate)

        self.assertNotEqual(result.returncode, 0)
        evaluation = json.loads(result.stdout)
        self.assertEqual(evaluation["status"], "failed")
        self.assertTrue(
            any(
                "title" in evidence
                for check in evaluation["automatic_checks"]
                for evidence in check["evidence"]
            )
        )

    def test_oracle_rejects_an_unbound_fixture(self) -> None:
        fixture = json.loads(json.dumps(self.fixture))
        fixture["fixture_id"] = "another-fixture-v1"
        with tempfile.TemporaryDirectory() as directory:
            fixture_path = Path(directory) / "unbound-fixture.json"
            fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
            result = self.run_oracle(GOOD_PATH, fixture_path)

        self.assertEqual(result.returncode, 2)
        self.assertIn("fixture identity", result.stderr)

    def test_local_control_release_gate_passes(self) -> None:
        result = subprocess.run(
            [sys.executable, str(GATE_PATH)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "valid KODY-01 control slice: known-good-control=passed, known-bad-control=failed",
            result.stdout,
        )

    def test_replay_writes_audit_ready_run_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_record_path = Path(directory) / "kody-01-control-good.run.json"
            result = subprocess.run(
                [
                    sys.executable,
                    str(REPLAY_PATH),
                    "--fixture",
                    str(FIXTURE_PATH),
                    "--prompt",
                    str(PROMPT_PATH),
                    "--candidate",
                    str(GOOD_PATH),
                    "--condition",
                    "known-good-control",
                    "--run-id",
                    "kody-01-control-good-001",
                    "--model-requested",
                    "control-known-good",
                    "--model-resolved",
                    "control-known-good",
                    "--output",
                    str(run_record_path),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(run_record_path.is_file())
            record = json.loads(run_record_path.read_text(encoding="utf-8"))

        self.assertEqual(record["run_id"], "kody-01-control-good-001")
        self.assertEqual(record["task_id"], "KODY-01")
        self.assertEqual(record["profile_id"], "kody")
        self.assertEqual(record["condition"], "known-good-control")
        self.assertEqual(record["status"], "passed")
        self.assertEqual(record["model_requested"], "control-known-good")
        self.assertEqual(record["model_resolved"], "control-known-good")
        self.assertRegex(record["prompt_fingerprint"], r"^sha256:[0-9a-f]{64}$")
        self.assertRegex(record["fixture_fingerprint"], r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(record["hard_failures"], [])
        self.assertEqual(record["human_scores"], {})
        self.assertEqual(record["usage"], {})
        self.assertEqual(validate_schema_instance(record, self.run_schema), [])

    def test_replay_preserves_failed_control_as_visible_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_record_path = Path(directory) / "kody-01-control-bad.run.json"
            result = subprocess.run(
                [
                    sys.executable,
                    str(REPLAY_PATH),
                    "--fixture",
                    str(FIXTURE_PATH),
                    "--prompt",
                    str(PROMPT_PATH),
                    "--candidate",
                    str(BAD_PATH),
                    "--condition",
                    "known-bad-control",
                    "--run-id",
                    "kody-01-control-bad-001",
                    "--model-requested",
                    "control-known-bad",
                    "--model-resolved",
                    "control-known-bad",
                    "--output",
                    str(run_record_path),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            record = json.loads(run_record_path.read_text(encoding="utf-8"))

        self.assertEqual(record["status"], "failed")
        self.assertTrue(record["hard_failures"])
        self.assertEqual(validate_schema_instance(record, self.run_schema), [])

    def test_replay_supports_a_model_calibration_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_record_path = Path(directory) / "kody-01-model.run.json"
            result = subprocess.run(
                [
                    sys.executable,
                    str(REPLAY_PATH),
                    "--fixture",
                    str(FIXTURE_PATH),
                    "--prompt",
                    str(PROMPT_PATH),
                    "--candidate",
                    str(GOOD_PATH),
                    "--harness",
                    "hermes-oneshot",
                    "--condition",
                    "model-calibration",
                    "--run-id",
                    "kody-01-model-001",
                    "--model-requested",
                    "gpt-5.6-luna",
                    "--model-resolved",
                    "gpt-5.6-luna",
                    "--output",
                    str(run_record_path),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            record = json.loads(run_record_path.read_text(encoding="utf-8"))

        self.assertEqual(record["harness"], "hermes-oneshot")
        self.assertEqual(record["condition"], "model-calibration")
        self.assertEqual(record["status"], "passed")
        self.assertEqual(validate_schema_instance(record, self.run_schema), [])

    def test_model_runner_preserves_raw_output_usage_and_failed_cells(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp_dir = Path(directory)
            fake_agent = temp_dir / "fake-agent.py"
            fake_agent.write_text(
                "#!/usr/bin/env python3\n"
                "import json\n"
                "import os\n"
                "import sys\n"
                "from pathlib import Path\n"
                "usage = Path(sys.argv[sys.argv.index('--usage-file') + 1])\n"
                "usage.write_text(json.dumps({'model': 'gpt-5.6-luna', 'api_calls': 1}), encoding='utf-8')\n"
                "sys.stdout.buffer.write(Path(os.environ['KODY01_FAKE_OUTPUT']).read_bytes())\n",
                encoding="utf-8",
            )
            fake_agent.chmod(0o755)
            output_root = temp_dir / "evidence"
            environment = {**os.environ, "KODY01_FAKE_OUTPUT": str(GOOD_PATH)}
            result = subprocess.run(
                [
                    sys.executable,
                    str(MODEL_RUNNER_PATH),
                    "--fixture",
                    str(FIXTURE_PATH),
                    "--prompt",
                    str(PROMPT_PATH),
                    "--output-root",
                    str(output_root),
                    "--run-id",
                    "kody-01-fake-good",
                    "--model-requested",
                    "gpt-5.6-luna",
                    "--agent-command",
                    str(fake_agent),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            evidence_dir = output_root / "kody-01-fake-good"
            record = json.loads((evidence_dir / "run-record.json").read_text(encoding="utf-8"))
            self.assertEqual((evidence_dir / "raw-output.txt").read_bytes(), GOOD_PATH.read_bytes())
            self.assertEqual(record["usage"], {"api_calls": 1, "model": "gpt-5.6-luna"})
            self.assertEqual(record["harness"], "hermes-oneshot")
            self.assertEqual(record["condition"], "model-calibration")
            self.assertEqual(validate_schema_instance(record, self.run_schema), [])

            fake_agent.write_text(
                "#!/usr/bin/env python3\n"
                "import sys\n"
                "from pathlib import Path\n"
                "Path(sys.argv[sys.argv.index('--usage-file') + 1]).write_text('{}', encoding='utf-8')\n"
                "print('not JSON')\n",
                encoding="utf-8",
            )
            fake_agent.chmod(0o755)
            failed_result = subprocess.run(
                [
                    sys.executable,
                    str(MODEL_RUNNER_PATH),
                    "--fixture",
                    str(FIXTURE_PATH),
                    "--prompt",
                    str(PROMPT_PATH),
                    "--output-root",
                    str(output_root),
                    "--run-id",
                    "kody-01-fake-bad",
                    "--model-requested",
                    "gpt-5.6-luna",
                    "--agent-command",
                    str(fake_agent),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertNotEqual(failed_result.returncode, 0)
            failed_record = json.loads(
                (output_root / "kody-01-fake-bad" / "run-record.json").read_text(encoding="utf-8")
            )
            self.assertEqual(failed_record["status"], "failed")
            self.assertIn("invalid-output", {failure["id"] for failure in failed_record["hard_failures"]})
            self.assertEqual(validate_schema_instance(failed_record, self.run_schema), [])

    def test_fingerprints_are_sha256_of_exact_input_bytes(self) -> None:
        expected_fixture = "sha256:" + hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest()
        expected_prompt = "sha256:" + hashlib.sha256(PROMPT_PATH.read_bytes()).hexdigest()
        self.assertRegex(expected_fixture, r"^sha256:[0-9a-f]{64}$")
        self.assertRegex(expected_prompt, r"^sha256:[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
