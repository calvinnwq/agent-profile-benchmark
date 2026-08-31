"""Release-artifact and evaluator contract tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.validate_benchmark import validate_schema_instance


ROOT = Path(__file__).resolve().parents[1]
LEDGER_PATH = ROOT / "data" / "task-ledger.json"
RELEASE_GATE = ROOT / "scripts" / "validate_benchmark_ready.py"
EVALUATOR = ROOT / "scripts" / "evaluate_task.py"
GENERIC_MODEL_RUNNER = ROOT / "scripts" / "run_task_model.py"
RUN_SCHEMA = ROOT / "schemas" / "task-run-record.schema.json"


class BenchmarkReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.ledger = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))

    def test_every_task_has_a_frozen_artifact_packet(self) -> None:
        self.assertEqual(self.ledger["status"], "benchmark-ready")
        self.assertEqual(self.ledger["benchmark_version"], "0.2.0")
        for task in self.ledger["tasks"]:
            slug = task["id"].lower()
            with self.subTest(task=task["id"]):
                package = ROOT / "fixtures" / slug
                self.assertTrue((package / "manifest.json").is_file())
                self.assertTrue((package / "prompt.txt").is_file())
                self.assertTrue((package / "input.json").is_file())
                self.assertTrue((package / "controls" / "known-good.json").is_file())
                self.assertTrue((package / "controls" / "known-bad.json").is_file())
                self.assertTrue((ROOT / "oracles" / f"{slug}.json").is_file())
                self.assertTrue((ROOT / "schemas" / f"{slug}-output.schema.json").is_file())

    def test_release_gate_validates_every_control(self) -> None:
        result = subprocess.run(
            [sys.executable, str(RELEASE_GATE)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("18 tasks", result.stdout)
        self.assertIn("known-good=18", result.stdout)
        self.assertIn("known-bad=18", result.stdout)

    def test_known_bad_controls_expose_expected_hard_failures(self) -> None:
        for task in self.ledger["tasks"]:
            if task["id"] == "KODY-01":
                continue
            slug = task["id"].lower()
            package = ROOT / "fixtures" / slug
            result = subprocess.run(
                [
                    sys.executable,
                    str(EVALUATOR),
                    "--task",
                    task["id"],
                    "--fixture",
                    str(package / "input.json"),
                    "--candidate",
                    str(package / "controls" / "known-bad.json"),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0, task["id"])
            evaluation = json.loads(result.stdout)
            self.assertEqual(evaluation["status"], "failed")
            self.assertGreaterEqual(len(evaluation["hard_failures"]), 1, task["id"])

    def test_malformed_model_output_is_visible_failed_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "not-json.txt"
            candidate.write_text("model preamble\n", encoding="utf-8")
            package = ROOT / "fixtures" / "kody-02"
            result = subprocess.run(
                [
                    sys.executable,
                    str(EVALUATOR),
                    "--task",
                    "KODY-02",
                    "--fixture",
                    str(package / "input.json"),
                    "--candidate",
                    str(candidate),
                    "--model-output",
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertNotEqual(result.returncode, 0)
        evaluation = json.loads(result.stdout)
        self.assertEqual(evaluation["status"], "failed")
        self.assertIn("invalid-output", {item["id"] for item in evaluation["hard_failures"]})

    def test_generic_model_runner_preserves_usage_output_and_process_failures(self) -> None:
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
                "usage.write_text(json.dumps({'model': 'control-model', 'api_calls': 1}), encoding='utf-8')\n"
                "sys.stdout.buffer.write(Path(os.environ['GENERIC_FAKE_OUTPUT']).read_bytes())\n",
                encoding="utf-8",
            )
            fake_agent.chmod(0o755)
            package = ROOT / "fixtures" / "kody-02"
            output_root = temp_dir / "evidence"
            environment = {**os.environ, "GENERIC_FAKE_OUTPUT": str(package / "controls" / "known-good.json")}
            result = subprocess.run(
                [
                    sys.executable,
                    str(GENERIC_MODEL_RUNNER),
                    "--task",
                    "KODY-02",
                    "--fixture",
                    str(package / "input.json"),
                    "--prompt",
                    str(package / "prompt.txt"),
                    "--output-root",
                    str(output_root),
                    "--run-id",
                    "kody-02-generic-good",
                    "--model-requested",
                    "control-model",
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
            evidence_dir = output_root / "kody-02-generic-good"
            record = json.loads((evidence_dir / "run-record.json").read_text(encoding="utf-8"))
            self.assertEqual(
                (evidence_dir / "raw-output.txt").read_bytes(),
                (package / "controls" / "known-good.json").read_bytes(),
            )
            self.assertEqual(record["usage"], {"api_calls": 1, "model": "control-model"})
            self.assertEqual(record["status"], "passed")
            self.assertEqual(validate_schema_instance(record, json.loads(RUN_SCHEMA.read_text())), [])

            fake_agent.write_text(
                "#!/usr/bin/env python3\n"
                "import sys\n"
                "from pathlib import Path\n"
                "Path(sys.argv[sys.argv.index('--usage-file') + 1]).write_text('{}', encoding='utf-8')\n"
                "print('partial output')\n"
                "sys.exit(7)\n",
                encoding="utf-8",
            )
            fake_agent.chmod(0o755)
            failed = subprocess.run(
                [
                    sys.executable,
                    str(GENERIC_MODEL_RUNNER),
                    "--task",
                    "KODY-02",
                    "--fixture",
                    str(package / "input.json"),
                    "--prompt",
                    str(package / "prompt.txt"),
                    "--output-root",
                    str(output_root),
                    "--run-id",
                    "kody-02-generic-nonzero",
                    "--model-requested",
                    "control-model",
                    "--agent-command",
                    str(fake_agent),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertNotEqual(failed.returncode, 0)
            failed_dir = output_root / "kody-02-generic-nonzero"
            failed_record = json.loads((failed_dir / "run-record.json").read_text(encoding="utf-8"))
            self.assertEqual(failed_record["status"], "failed")
            trial = json.loads((failed_dir / "trial.json").read_text(encoding="utf-8"))
            self.assertEqual(trial["process_returncode"], 7)
            self.assertEqual(validate_schema_instance(failed_record, json.loads(RUN_SCHEMA.read_text())), [])


if __name__ == "__main__":
    unittest.main()
