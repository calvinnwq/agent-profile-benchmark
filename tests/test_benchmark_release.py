"""Release-artifact and evaluator contract tests."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
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
HERMES_ADAPTER = ROOT / "scripts" / "hermes_no_tools.py"
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
                fixture_name = "request-packet.json" if task["id"] == "KODY-01" else "input.json"
                self.assertTrue((package / fixture_name).is_file())
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

    def test_release_artifact_lock_is_sealed(self) -> None:
        lock_path = ROOT / "data" / "release-artifact-lock.json"
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        from scripts.release_lock import EXPECTED_RELEASE_LOCK_FINGERPRINT

        self.assertEqual(hashlib.sha256(lock_path.read_bytes()).hexdigest(), EXPECTED_RELEASE_LOCK_FINGERPRINT)
        self.assertEqual(lock["benchmark_id"], "agent-profile-benchmark")
        self.assertEqual(lock["benchmark_version"], "0.2.0")
        self.assertEqual(len(lock["tasks"]), 18)

    def test_release_lock_rejects_repinned_prompt_in_disposable_copy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            copy_root = Path(directory) / "benchmark"
            shutil.copytree(
                ROOT,
                copy_root,
                ignore=shutil.ignore_patterns(".git", ".model-evidence", "__pycache__", "*.pyc"),
            )
            prompt_path = copy_root / "fixtures" / "aegis-01" / "prompt.txt"
            prompt_path.write_text(
                prompt_path.read_text(encoding="utf-8") + "\nRepurposed.",
                encoding="utf-8",
            )
            manifest_path = copy_root / "fixtures" / "aegis-01" / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["prompt"]["sha256"] = "sha256:" + hashlib.sha256(prompt_path.read_bytes()).hexdigest()
            manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(copy_root / "scripts" / "validate_benchmark_ready.py")],
                cwd=copy_root,
                check=False,
                capture_output=True,
                text=True,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("release lock", result.stderr)

    def test_release_gate_does_not_execute_controls_after_lock_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            copy_root = Path(directory) / "benchmark"
            shutil.copytree(
                ROOT,
                copy_root,
                ignore=shutil.ignore_patterns(".git", ".model-evidence", "__pycache__", "*.pyc"),
            )
            marker_path = copy_root / "control-executed.marker"
            control_path = copy_root / "fixtures" / "arch-01" / "controls" / "known-good.json"
            control = json.loads(control_path.read_text(encoding="utf-8"))
            control["implementation"]["auth.py"] = (
                "from pathlib import Path\n"
                f"Path({str(marker_path)!r}).write_text('executed', encoding='utf-8')\n"
                "import hmac\n"
                "\n"
                "def verify(token, expected):\n"
                "    if not isinstance(token, str) or not isinstance(expected, str):\n"
                "        return False\n"
                "    return hmac.compare_digest(token, expected)\n"
            )
            control_path.write_text(json.dumps(control), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(copy_root / "scripts" / "validate_benchmark_ready.py")],
                cwd=copy_root,
                check=False,
                capture_output=True,
                text=True,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(marker_path.exists(), "release validation executed a drifted control")

    def test_hermes_adapter_enforces_empty_tool_and_session_surfaces(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp_dir = Path(directory)
            fake_package = temp_dir / "hermes_cli"
            fake_package.mkdir()
            (fake_package / "__init__.py").write_text("from . import oneshot\n", encoding="utf-8")
            (fake_package / "mcp_startup.py").write_text(
                "def ensure_mcp_discovery_before_agent_build(**_kwargs):\n"
                "    raise RuntimeError('MCP discovery was not disabled')\n",
                encoding="utf-8",
            )
            (fake_package / "config.py").write_text(
                "def load_config():\n"
                "    return {'profile_setting': 'must-not-leak'}\n"
                "def load_config_readonly():\n"
                "    return {'profile_setting': 'must-not-leak'}\n",
                encoding="utf-8",
            )
            (temp_dir / "run_agent.py").write_text(
                "class AIAgent:\n"
                "    _TOOL_CALL_ARGUMENTS_CORRUPTION_MARKER = 'marker'\n"
                "    def __init__(self, **kwargs):\n"
                "        self.kwargs = kwargs\n",
                encoding="utf-8",
            )
            (fake_package / "oneshot.py").write_text(
                "import json\n"
                "import os\n"
                "from pathlib import Path\n"
                "from hermes_cli import config\n"
                "from run_agent import AIAgent\n"
                "from hermes_cli import mcp_startup\n"
                "def _validate_explicit_toolsets(value):\n"
                "    return ('unpatched', value)\n"
                "def _normalize_toolsets(value):\n"
                "    return ['unpatched']\n"
                "def get_fallback_chain(_config):\n"
                "    return ['unpatched']\n"
                "def _create_session_db_for_oneshot():\n"
                "    return 'unpatched'\n"
                "def run_oneshot(prompt, model=None, provider=None, toolsets=None, usage_file=None):\n"
                "    marker = AIAgent._TOOL_CALL_ARGUMENTS_CORRUPTION_MARKER\n"
                "    mcp_startup.ensure_mcp_discovery_before_agent_build(single_query=True)\n"
                "    explicit_toolsets, error = _validate_explicit_toolsets(toolsets)\n"
                "    assert error is None\n"
                "    use_config_toolsets = _normalize_toolsets(toolsets) is None\n"
                "    toolsets_list = _normalize_toolsets(explicit_toolsets)\n"
                "    if toolsets_list is None and not use_config_toolsets:\n"
                "        toolsets_list = []\n"
                "    agent = AIAgent(enabled_toolsets=toolsets_list)\n"
                "    observation = {'prompt': prompt, 'model': model, 'provider': provider,\n"
                "                   'marker': marker,\n"
                "                   'validated': _validate_explicit_toolsets(toolsets),\n"
                "                   'normalized': _normalize_toolsets(toolsets),\n"
                "                   'normalized_empty': _normalize_toolsets([]),\n"
                "                   'fallback': get_fallback_chain({}),\n"
                "                   'session_db': _create_session_db_for_oneshot(),\n"
                "                   'home': os.environ.get('HERMES_HOME'),\n"
                "                   'safe': os.environ.get('HERMES_SAFE_MODE'),\n"
                "                   'ignore_user_config': os.environ.get('HERMES_IGNORE_USER_CONFIG'),\n"
                "                   'ignore_rules': os.environ.get('HERMES_IGNORE_RULES'),\n"
                "                   'kanban_task': os.environ.get('HERMES_KANBAN_TASK'),\n"
                "                   'kanban_workspace': os.environ.get('HERMES_KANBAN_WORKSPACE'),\n"
                "                   'kanban_db': os.environ.get('HERMES_KANBAN_DB'),\n"
                "                   'kanban_board': os.environ.get('HERMES_KANBAN_BOARD'),\n"
                "                   'config': config.load_config(),\n"
                "                   'config_readonly': config.load_config_readonly(),\n"
                "                   'agent_kwargs': agent.kwargs}\n"
                "    Path(os.environ['ADAPTER_OBSERVATION']).write_text(json.dumps(observation), encoding='utf-8')\n"
                "    Path(usage_file).write_text(json.dumps({'model': model, 'provider': provider}), encoding='utf-8')\n"
                "    print('{}')\n"
                "    return 0\n",
                encoding="utf-8",
            )
            profile_home = temp_dir / "hermes-home" / "profiles" / "kody"
            profile_home.mkdir(parents=True)
            query_path = temp_dir / "query.txt"
            query_path.write_text("frozen query", encoding="utf-8")
            usage_path = temp_dir / "usage.json"
            observation_path = temp_dir / "observation.json"
            environment = {
                **os.environ,
                "HERMES_HOME": str(temp_dir / "hermes-home"),
                "PYTHONPATH": str(temp_dir),
                "ADAPTER_OBSERVATION": str(observation_path),
                "HERMES_KANBAN_TASK": "task-123",
                "HERMES_KANBAN_WORKSPACE": "/tmp/benchmark-worker",
                "HERMES_KANBAN_DB": "/tmp/kanban.db",
                "HERMES_KANBAN_BOARD": "default",
            }
            result = subprocess.run(
                [
                    sys.executable,
                    str(HERMES_ADAPTER),
                    "--profile",
                    "kody",
                    "--model",
                    "resolved/model",
                    "--provider",
                    "verified-provider",
                    "--query-file",
                    str(query_path),
                    "--usage-file",
                    str(usage_path),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            observation = json.loads(observation_path.read_text(encoding="utf-8"))
            self.assertEqual(observation["marker"], "marker")
            self.assertEqual(observation["validated"], [[], None])
            self.assertEqual(observation["normalized"], [])
            self.assertEqual(observation["normalized_empty"], [])
            self.assertEqual(observation["agent_kwargs"]["enabled_toolsets"], [])
            self.assertEqual(observation["fallback"], [])
            self.assertIsNone(observation["session_db"])
            self.assertEqual(observation["safe"], "1")
            self.assertEqual(observation["ignore_user_config"], "1")
            self.assertEqual(observation["ignore_rules"], "1")
            self.assertIsNone(observation.get("kanban_task"))
            self.assertIsNone(observation.get("kanban_workspace"))
            self.assertIsNone(observation.get("kanban_db"))
            self.assertIsNone(observation.get("kanban_board"))
            self.assertEqual(
                observation["config"],
                {
                    "agent": {"reasoning_effort": "medium", "reasoning_overrides": {}},
                    "fallback_providers": [],
                    "memory": {},
                    "model": {"default": "resolved/model", "provider": "verified-provider"},
                    "providers": {},
                    "sessions": {"write_json_snapshots": False},
                    "toolsets": [],
                },
            )
            self.assertEqual(observation["config_readonly"], observation["config"])
            self.assertTrue(observation["agent_kwargs"]["skip_context_files"])
            self.assertFalse(observation["agent_kwargs"]["load_soul_identity"])
            self.assertTrue(observation["agent_kwargs"]["skip_memory"])
            self.assertTrue(observation["agent_kwargs"]["skip_background_review"])
            self.assertIsNone(observation["agent_kwargs"]["session_db"])
            self.assertEqual(
                observation["agent_kwargs"]["reasoning_config"],
                {"enabled": True, "effort": "medium"},
            )

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

    def test_kody02_rejects_fabricated_evidence_links(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp_dir = Path(directory)
            package = ROOT / "fixtures" / "kody-02"
            candidate = json.loads((package / "controls" / "known-good.json").read_text(encoding="utf-8"))
            candidate["evidence_links"].append(
                {"evidence_id": "note-fabricated", "supports": ["made-up-result"]}
            )
            candidate_path = temp_dir / "fabricated-evidence.json"
            candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(EVALUATOR),
                    "--task",
                    "KODY-02",
                    "--fixture",
                    str(package / "input.json"),
                    "--candidate",
                    str(candidate_path),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertNotEqual(result.returncode, 0)
        evaluation = json.loads(result.stdout)
        self.assertEqual(evaluation["status"], "failed")
        self.assertIn("fabricated-evidence", {item["id"] for item in evaluation["hard_failures"]})

    def test_aegis01_rejects_allocation_that_disagrees_with_positions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp_dir = Path(directory)
            package = ROOT / "fixtures" / "aegis-01"
            candidate = json.loads((package / "controls" / "known-good.json").read_text(encoding="utf-8"))
            candidate["allocation"][-1]["share"] = 0.9
            candidate_path = temp_dir / "contradictory-allocation.json"
            candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(EVALUATOR),
                    "--task",
                    "AEGIS-01",
                    "--fixture",
                    str(package / "input.json"),
                    "--candidate",
                    str(candidate_path),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertNotEqual(result.returncode, 0)
        evaluation = json.loads(result.stdout)
        self.assertEqual(evaluation["status"], "failed")
        checks = {item["id"]: item for item in evaluation["automatic_checks"]}
        self.assertEqual(checks["numeric-reconciliation"]["status"], "fail")

    def test_aegis02_rejects_recommendation_for_growth_strategy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp_dir = Path(directory)
            package = ROOT / "fixtures" / "aegis-02"
            candidate = json.loads((package / "controls" / "known-good.json").read_text(encoding="utf-8"))
            candidate["recommendation"] = "Choose growth despite the downside and liquidity priority."
            candidate_path = temp_dir / "contradictory-recommendation.json"
            candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(EVALUATOR),
                    "--task",
                    "AEGIS-02",
                    "--fixture",
                    str(package / "input.json"),
                    "--candidate",
                    str(candidate_path),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertNotEqual(result.returncode, 0)
        evaluation = json.loads(result.stdout)
        self.assertEqual(evaluation["status"], "failed")
        checks = {item["id"]: item for item in evaluation["automatic_checks"]}
        self.assertEqual(checks["recommendation-consistency"]["status"], "fail")

    def test_atlas01_reconciles_reported_catalogue_totals(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp_dir = Path(directory)
            package = ROOT / "fixtures" / "atlas-01"
            candidate = json.loads((package / "controls" / "known-good.json").read_text(encoding="utf-8"))
            candidate["weight_total"] = -1
            candidate["cost_total"] = -1
            candidate_path = temp_dir / "wrong-catalogue-totals.json"
            candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(EVALUATOR),
                    "--task",
                    "ATLAS-01",
                    "--fixture",
                    str(package / "input.json"),
                    "--candidate",
                    str(candidate_path),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertNotEqual(result.returncode, 0)
        evaluation = json.loads(result.stdout)
        self.assertEqual(evaluation["status"], "failed")
        self.assertIn("hidden-budget-breach", {item["id"] for item in evaluation["hard_failures"]})

    def test_oracle01_rejects_wrong_claim_source_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp_dir = Path(directory)
            package = ROOT / "fixtures" / "oracle-01"
            candidate = json.loads((package / "controls" / "known-good.json").read_text(encoding="utf-8"))
            candidate["claim_register"][2]["source_id"] = "rulebook-2026"
            candidate["source_links"][2]["source_id"] = "rulebook-2026"
            candidate_path = temp_dir / "wrong-source-mapping.json"
            candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(EVALUATOR),
                    "--task",
                    "ORACLE-01",
                    "--fixture",
                    str(package / "input.json"),
                    "--candidate",
                    str(candidate_path),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertNotEqual(result.returncode, 0)
        evaluation = json.loads(result.stdout)
        self.assertEqual(evaluation["status"], "failed")
        checks = {item["id"]: item for item in evaluation["automatic_checks"]}
        self.assertEqual(checks["claim-source-mapping"]["status"], "fail")

    def test_oracle02_rejects_over_capacity_recommendation_grammar(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp_dir = Path(directory)
            package = ROOT / "fixtures" / "oracle-02"
            candidate = json.loads((package / "controls" / "known-good.json").read_text(encoding="utf-8"))
            candidate["recommendation"] = "q8 is my recommendation despite the 24 GB ceiling; q6 remains within the target."
            candidate_path = temp_dir / "over-capacity-recommendation.json"
            candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(EVALUATOR),
                    "--task",
                    "ORACLE-02",
                    "--fixture",
                    str(package / "input.json"),
                    "--candidate",
                    str(candidate_path),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertNotEqual(result.returncode, 0)
        evaluation = json.loads(result.stdout)
        self.assertEqual(evaluation["status"], "failed")
        self.assertIn("over-capacity-recommendation", {item["id"] for item in evaluation["hard_failures"]})

    def test_tank01_requires_each_declared_red_flag(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp_dir = Path(directory)
            package = ROOT / "fixtures" / "tank-01"
            candidate = json.loads((package / "controls" / "known-good.json").read_text(encoding="utf-8"))
            candidate["stop_rules"] = ["Stop for pain and seek appropriate professional help."]
            candidate["recovery_rules"] = ["Use rest to recover before resuming."]
            candidate_path = temp_dir / "missing-red-flags.json"
            candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(EVALUATOR),
                    "--task",
                    "TANK-01",
                    "--fixture",
                    str(package / "input.json"),
                    "--candidate",
                    str(candidate_path),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertNotEqual(result.returncode, 0)
        evaluation = json.loads(result.stdout)
        self.assertEqual(evaluation["status"], "failed")
        self.assertIn("missing-red-flag", {item["id"] for item in evaluation["hard_failures"]})

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
                "usage.write_text(json.dumps({'model': 'control-model', 'provider': 'test-provider', 'api_calls': 1}), encoding='utf-8')\n"
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
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertNotIn(str(temp_dir), result.stdout)
            self.assertIn("external-evidence", result.stdout)
            evidence_dir = output_root / "kody-02-generic-good"
            record = json.loads((evidence_dir / "run-record.json").read_text(encoding="utf-8"))
            trial = json.loads((evidence_dir / "trial.json").read_text(encoding="utf-8"))
            git_state = json.loads((evidence_dir / trial["git_state"]).read_text(encoding="utf-8"))
            self.assertEqual(
                (evidence_dir / "raw-output.txt").read_bytes(),
                (package / "controls" / "known-good.json").read_bytes(),
            )
            self.assertEqual(record["usage"], {"api_calls": 1, "model": "control-model", "provider": "test-provider"})
            self.assertEqual(record["status"], "blocked")
            self.assertEqual(record["failure_class"], "unverified-isolation")
            self.assertIn("--query-file", trial["command"])
            self.assertNotIn("context_engine", trial["command"])
            self.assertIn("--ignore-rules", trial["command"])
            self.assertEqual(
                git_state["head"],
                subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            )
            self.assertIsInstance(git_state["changed_files"], list)
            self.assertIsInstance(git_state["untracked_files"], list)
            self.assertNotIn(str(ROOT), json.dumps(git_state))
            self.assertEqual(validate_schema_instance(record, json.loads(RUN_SCHEMA.read_text())), [])

            duplicate = subprocess.run(
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
            self.assertNotEqual(duplicate.returncode, 0)
            self.assertIn("already exists", duplicate.stderr)

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

            missing = subprocess.run(
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
                    "kody-02-generic-launch-error",
                    "--model-requested",
                    "control-model",
                    "--agent-command",
                    str(temp_dir / "missing-agent"),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertNotEqual(missing.returncode, 0)
            missing_record = json.loads(
                (output_root / "kody-02-generic-launch-error" / "run-record.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(missing_record["status"], "failed")
            self.assertEqual(missing_record["execution_status"], "failed")
            self.assertEqual(missing_record["failure_class"], "process-launch")
            self.assertEqual(
                validate_schema_instance(missing_record, json.loads(RUN_SCHEMA.read_text())), []
            )

    def test_model_runner_rejects_fixture_path_drift_before_launch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp_dir = Path(directory)
            fake_agent = temp_dir / "fake-agent.py"
            marker = temp_dir / "launched"
            fake_agent.write_text(
                "#!/usr/bin/env python3\n"
                "from pathlib import Path\n"
                f"Path({str(marker)!r}).write_text('launched', encoding='utf-8')\n",
                encoding="utf-8",
            )
            fake_agent.chmod(0o755)
            package = ROOT / "fixtures" / "kody-02"
            mutated_fixture = temp_dir / "input.json"
            mutated_fixture.write_bytes((package / "input.json").read_bytes() + b"\n")
            result = subprocess.run(
                [
                    sys.executable,
                    str(GENERIC_MODEL_RUNNER),
                    "--task",
                    "KODY-02",
                    "--fixture",
                    str(mutated_fixture),
                    "--prompt",
                    str(package / "prompt.txt"),
                    "--output-root",
                    str(temp_dir / "evidence"),
                    "--run-id",
                    "kody-02-drifted-input",
                    "--model-requested",
                    "control-model",
                    "--agent-command",
                    str(fake_agent),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("does not match the frozen task package", result.stderr)
            self.assertFalse(marker.exists())

    def test_model_runner_blocks_when_usage_does_not_resolve_model(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp_dir = Path(directory)
            fake_agent = temp_dir / "fake-agent.py"
            package = ROOT / "fixtures" / "kody-02"
            fake_agent.write_text(
                "#!/usr/bin/env python3\n"
                "import os\n"
                "import sys\n"
                "from pathlib import Path\n"
                "Path(os.sys.argv[os.sys.argv.index('--usage-file') + 1]).write_text('{}\\n', encoding='utf-8')\n"
                "sys.stdout.buffer.write(Path('" + str(package / "controls" / "known-good.json") + "').read_bytes())\n",
                encoding="utf-8",
            )
            fake_agent.chmod(0o755)
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
                    str(temp_dir / "evidence"),
                    "--run-id",
                    "kody-02-unresolved-model",
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
            self.assertNotEqual(result.returncode, 0)
            record = json.loads((temp_dir / "evidence" / "kody-02-unresolved-model" / "run-record.json").read_text(encoding="utf-8"))
            self.assertEqual(record["status"], "blocked")
            self.assertEqual(record["model_resolved"], "unresolved")
            self.assertEqual(record["provider_resolved"], "unresolved")
            self.assertIn("model/provider resolution", record["notes"])

    def test_model_runner_preserves_usage_failure_and_identity_sentinels(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp_dir = Path(directory)
            package = ROOT / "fixtures" / "kody-02"
            fake_agent = temp_dir / "fake-agent.py"
            fake_agent.write_text(
                "#!/usr/bin/env python3\n"
                "import json\n"
                "import os\n"
                "import sys\n"
                "from pathlib import Path\n"
                "usage = Path(sys.argv[sys.argv.index('--usage-file') + 1])\n"
                "if os.environ['USAGE_MODE'] == 'failure':\n"
                "    value = {'model': 'control-model', 'provider': 'test-provider', 'failed': True, 'completed': False}\n"
                "else:\n"
                "    value = {'model': 'unresolved', 'provider': 'none'}\n"
                "usage.write_text(json.dumps(value), encoding='utf-8')\n"
                "sys.stdout.buffer.write(Path(os.environ['GENERIC_FAKE_OUTPUT']).read_bytes())\n",
                encoding="utf-8",
            )
            fake_agent.chmod(0o755)
            environment = {
                **os.environ,
                "GENERIC_FAKE_OUTPUT": str(package / "controls" / "known-good.json"),
            }
            for mode, run_id, expected_status, expected_failure in (
                ("sentinel", "usage-sentinel", "blocked", "unverified-isolation"),
                ("failure", "usage-failure", "failed", "usage-failure"),
            ):
                environment["USAGE_MODE"] = mode
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
                        str(temp_dir / "evidence"),
                        "--run-id",
                        run_id,
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
                self.assertNotEqual(result.returncode, 0)
                record = json.loads(
                    (temp_dir / "evidence" / run_id / "run-record.json").read_text(encoding="utf-8")
                )
                self.assertEqual(record["status"], expected_status)
                self.assertEqual(record["failure_class"], expected_failure)
                if mode == "sentinel":
                    self.assertEqual(record["resolution_status"], "unresolved")

    def test_model_runner_rejects_unknown_task_before_launch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp_dir = Path(directory)
            marker = temp_dir / "launched"
            fake_agent = temp_dir / "fake-agent.py"
            fake_agent.write_text(
                "#!/usr/bin/env python3\n"
                f"Path({str(marker)!r}).write_text('launched', encoding='utf-8')\n",
                encoding="utf-8",
            )
            fake_agent.chmod(0o755)
            package = ROOT / "fixtures" / "kody-02"
            result = subprocess.run(
                [
                    sys.executable,
                    str(GENERIC_MODEL_RUNNER),
                    "--task",
                    "../README",
                    "--fixture",
                    str(package / "input.json"),
                    "--prompt",
                    str(package / "prompt.txt"),
                    "--output-root",
                    str(temp_dir / "evidence"),
                    "--run-id",
                    "unknown-task",
                    "--model-requested",
                    "control-model",
                    "--agent-command",
                    str(fake_agent),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unknown benchmark task ID", result.stderr)
            self.assertFalse(marker.exists())
            self.assertFalse((temp_dir / "evidence").exists())

    def test_model_runner_preserves_default_command_construction_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp_dir = Path(directory)
            package = ROOT / "fixtures" / "kody-02"
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
                    str(temp_dir / "evidence"),
                    "--run-id",
                    "missing-hermes",
                    "--model-requested",
                    "control-model",
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                env={"PATH": str(temp_dir), "PYTHONIOENCODING": "utf-8"},
            )
            self.assertNotEqual(result.returncode, 0)
            evidence = temp_dir / "evidence" / "missing-hermes"
            record = json.loads((evidence / "run-record.json").read_text(encoding="utf-8"))
            self.assertEqual(record["status"], "failed")
            self.assertEqual(record["execution_status"], "failed")
            self.assertEqual(record["failure_class"], "process-launch")
            self.assertEqual(json.loads((evidence / "trial.json").read_text(encoding="utf-8"))["command"], [])
            self.assertEqual(validate_schema_instance(record, json.loads(RUN_SCHEMA.read_text())), [])

    def test_model_runner_preserves_timeout_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp_dir = Path(directory)
            package = ROOT / "fixtures" / "kody-02"
            fake_agent = temp_dir / "slow-agent.py"
            fake_agent.write_text(
                "#!/usr/bin/env python3\n"
                "import time\n"
                "time.sleep(3)\n",
                encoding="utf-8",
            )
            fake_agent.chmod(0o755)
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
                    str(temp_dir / "evidence"),
                    "--run-id",
                    "timed-out-model",
                    "--model-requested",
                    "control-model",
                    "--agent-command",
                    str(fake_agent),
                    "--timeout-seconds",
                    "1",
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            evidence = temp_dir / "evidence" / "timed-out-model"
            record = json.loads((evidence / "run-record.json").read_text(encoding="utf-8"))
            trial = json.loads((evidence / "trial.json").read_text(encoding="utf-8"))
            self.assertEqual(record["status"], "failed")
            self.assertEqual(record["execution_status"], "timed_out")
            self.assertEqual(record["failure_class"], "timeout")
            self.assertTrue(trial["timed_out"])

    def test_auth_probe_timeout_is_a_visible_hidden_test_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp_dir = Path(directory)
            package = ROOT / "fixtures" / "arch-01"
            candidate = json.loads((package / "controls" / "known-good.json").read_text(encoding="utf-8"))
            candidate["implementation"]["auth.py"] = "def verify(token, expected):\n    while True:\n        pass\n"
            candidate_path = temp_dir / "candidate.json"
            candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(EVALUATOR),
                    "--task",
                    "ARCH-01",
                    "--fixture",
                    str(package / "input.json"),
                    "--candidate",
                    str(candidate_path),
                    "--trusted-control",
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertNotEqual(result.returncode, 0)
            evaluation = json.loads(result.stdout)
            checks = {item["id"]: item for item in evaluation["automatic_checks"]}
            self.assertEqual(checks["hidden-behavioral-tests"]["status"], "fail")
            self.assertIn("hidden-test-failure", {item["id"] for item in evaluation["hard_failures"]})

    def test_auth_probe_rejects_dead_constant_time_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp_dir = Path(directory)
            package = ROOT / "fixtures" / "arch-01"
            candidate = json.loads((package / "controls" / "known-good.json").read_text(encoding="utf-8"))
            candidate["implementation"]["auth.py"] = (
                "import hmac\n\n"
                "def verify(token, expected):\n"
                "    if not isinstance(token, str) or not isinstance(expected, str):\n"
                "        return False\n"
                "    return token == expected\n\n"
                "# hmac.compare_digest\n"
            )
            candidate_path = temp_dir / "dead-constant-time-marker.json"
            candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(EVALUATOR),
                    "--task",
                    "ARCH-01",
                    "--fixture",
                    str(package / "input.json"),
                    "--candidate",
                    str(candidate_path),
                    "--trusted-control",
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertNotEqual(result.returncode, 0)
        evaluation = json.loads(result.stdout)
        self.assertEqual(evaluation["status"], "failed")
        self.assertIn("hidden-test-failure", {item["id"] for item in evaluation["hard_failures"]})

    def test_auth_probe_rejects_early_successful_exit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp_dir = Path(directory)
            package = ROOT / "fixtures" / "arch-01"
            candidate = json.loads((package / "controls" / "known-good.json").read_text(encoding="utf-8"))
            candidate["implementation"]["auth.py"] = (
                "import os\n"
                "os._exit(0)\n"
                "# hmac.compare_digest\n"
            )
            candidate_path = temp_dir / "early-exit-auth.json"
            candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(EVALUATOR),
                    "--task",
                    "ARCH-01",
                    "--fixture",
                    str(package / "input.json"),
                    "--candidate",
                    str(candidate_path),
                    "--trusted-control",
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertNotEqual(result.returncode, 0)
        evaluation = json.loads(result.stdout)
        self.assertEqual(evaluation["status"], "failed")
        self.assertIn("hidden-test-failure", {item["id"] for item in evaluation["hard_failures"]})

    def test_evaluator_blocks_auth_code_without_explicit_trusted_control(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp_dir = Path(directory)
            marker = temp_dir / "auth-code-ran"
            package = ROOT / "fixtures" / "arch-01"
            candidate = json.loads((package / "controls" / "known-good.json").read_text(encoding="utf-8"))
            candidate["implementation"]["auth.py"] = (
                "from pathlib import Path\n"
                f"Path({str(marker)!r}).write_text('ran', encoding='utf-8')\n"
                "def verify(token, expected):\n"
                "    return False\n"
            )
            candidate_path = temp_dir / "untrusted-auth.json"
            candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(EVALUATOR),
                    "--task",
                    "ARCH-01",
                    "--fixture",
                    str(package / "input.json"),
                    "--candidate",
                    str(candidate_path),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(marker.exists())
        evaluation = json.loads(result.stdout)
        self.assertEqual(evaluation["status"], "blocked")
        checks = {item["id"]: item for item in evaluation["automatic_checks"]}
        self.assertEqual(checks["hidden-behavioral-tests"]["status"], "blocked")

    def test_malformed_numeric_and_deep_json_output_is_visible_failed_evidence(self) -> None:
        package = ROOT / "fixtures" / "tank-01"
        valid_text = (package / "controls" / "known-good.json").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            temp_dir = Path(directory)
            candidates = {
                "nonfinite": valid_text.replace('"total_km": 32', '"total_km": NaN'),
                "deep": "[" * 2000 + "0" + "]" * 2000,
            }
            for name, text in candidates.items():
                with self.subTest(candidate=name):
                    candidate_path = temp_dir / f"{name}.json"
                    candidate_path.write_text(text, encoding="utf-8")
                    result = subprocess.run(
                        [
                            sys.executable,
                            str(EVALUATOR),
                            "--task",
                            "TANK-01",
                            "--fixture",
                            str(package / "input.json"),
                            "--candidate",
                            str(candidate_path),
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


if __name__ == "__main__":
    unittest.main()
