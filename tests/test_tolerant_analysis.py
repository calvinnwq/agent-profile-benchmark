"""Focused tests for tolerant re-analysis of a recovered model response."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.analyze_tolerant_outputs import _analyse_run


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "fixtures" / "kody-01" / "request-packet.json"
KNOWN_GOOD_PATH = ROOT / "fixtures" / "kody-01" / "controls" / "known-good.json"


class TolerantAnalysisTests(unittest.TestCase):
    def test_fenced_good_plan_is_judgeable_without_changing_strict_result(self) -> None:
        fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        candidate = KNOWN_GOOD_PATH.read_text(encoding="utf-8").strip()
        raw = f"```json\n{candidate}\n```\n".encode("utf-8")
        record = {
            "run_id": "synthetic-fenced-kody-01",
            "_record_path": "records/synthetic-fenced-kody-01/run-record.json",
            "_raw_output_reference": "records/synthetic-fenced-kody-01/raw-output.txt",
            "raw_output_fingerprint": "sha256:" + hashlib.sha256(raw).hexdigest(),
            "_model_id": "synthetic/model:free",
            "task_id": "KODY-01",
            "profile_id": "kody",
            "_identity_resolved": True,
            "_comparable": False,
            "resolution_status": "resolved",
            "execution_status": "completed",
            "failure_class": "none",
            "output_parse_status": "invalid-json",
            "status": "failed",
            "automatic_checks": [
                {"id": "required-fields", "status": "fail", "evidence": ["strict wrapper failure"]}
            ],
            "hard_failures": [
                {
                    "id": "invalid-output",
                    "condition": "strict wrapper failure",
                    "evidence": ["Markdown fence"],
                }
            ],
            "latency_ms": 100,
        }
        with tempfile.TemporaryDirectory() as directory:
            raw_path = Path(directory) / "raw-output.txt"
            raw_path.write_bytes(raw)
            result = _analyse_run(record, raw_path, fixture)

        self.assertEqual(result["recovery"]["classification"], "markdown-fenced-json")
        self.assertFalse(result["strict_contract_valid"])
        self.assertFalse(result["strict_objective_pass"])
        self.assertTrue(result["tolerant_recoverable"])
        self.assertTrue(result["tolerant_judgeable"])
        self.assertTrue(result["tolerant_automatic_check_pass"])
        self.assertTrue(result["tolerant_objective_pass"])
        self.assertEqual(result["tolerant_hard_failure_ids"], [])


if __name__ == "__main__":
    unittest.main()
