"""Regression tests for the v0.4.0 benchmark validity repair."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from scripts.evaluate_task import evaluate_task
from scripts.validate_validity_probes import GateError, validate_historical_audit, validate_report


ROOT = Path(__file__).resolve().parents[1]


def _json(path: str) -> Any:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def _fixture_and_control(task_id: str) -> tuple[dict, dict]:
    fixture = _json(f"fixtures/{task_id.lower()}/input.json")
    control = _json(f"fixtures/{task_id.lower()}/controls/known-good.json")
    assert isinstance(fixture, dict)
    assert isinstance(control, dict)
    return fixture, control


def _git_archive(source_commit: str) -> bytes:
    result = subprocess.run(
        [
            "git",
            "archive",
            "--format=tar",
            "--prefix=agent-profile-benchmark-v0.3.0/",
            "--mtime=1970-01-01T00:00:00Z",
            source_commit,
        ],
        cwd=ROOT,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    return result.stdout


def _historical_audit(manifest: dict[str, Any], digest: str) -> dict[str, Any]:
    return {
        "audit_schema_version": "validity-audit-v1",
        "audit_type": "zero-model-call-read-only",
        "benchmark_id": "agent-profile-benchmark",
        "benchmark_version": "0.3.0",
        "controls": {"total": 36, "failures": []},
        "replay": {
            "attempted": 108,
            "errors": [],
            "binary_status_matches": 108,
            "check_maps_match": 108,
            "hard_failure_maps_match": 108,
        },
        "scope": {
            "model_calls_issued": 0,
            "historical_snapshot_modified": False,
            "expensive_matrix_rerun": False,
            "repository_files_modified": [],
        },
        "snapshot_integrity": {
            "unchanged": True,
            "before": digest,
            "after": digest,
            "derived_from_audit_sha256": manifest["derived_from_original_audit_sha256"],
            "original_tree_fingerprint": manifest["historical_tree_fingerprint"],
            "source": {
                "release_ref": manifest["release_ref"],
                "release_artifact_sha256": digest,
            },
        },
    }


def _write_reproducibility_evidence(root: Path) -> tuple[Path, Path, Path, dict[str, Any]]:
    manifest = _json("data/historical-v03-snapshot-manifest.json")
    archive = _git_archive(manifest["source_commit"])
    input_path = root / "historical-input.tar"
    input_path.write_bytes(archive)
    digest = "sha256:" + hashlib.sha256(archive).hexdigest()
    audit_path = root / "audit.json"
    audit_path.write_text(
        json.dumps(_historical_audit(manifest, digest), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    trusted_manifest = dict(manifest)
    trusted_manifest["source_bound_audit_sha256"] = (
        "sha256:" + hashlib.sha256(audit_path.read_bytes()).hexdigest()
    )
    trusted_path = root / "trusted-manifest.json"
    trusted_path.write_text(json.dumps(trusted_manifest, sort_keys=True) + "\n", encoding="utf-8")
    return audit_path, input_path, trusted_path, trusted_manifest


class EvaluatorRepairTests(unittest.TestCase):
    def test_kody_rejects_reviewed_fact_contradictions_and_unspecified_count(self) -> None:
        cases = {
            "dry-run": "The dry run never succeeded.",
            "rollback-window": "Rollback is unavailable and cannot be performed.",
            "migration-owner": "The owner is not Priya.",
            "index-gap": "Several indexes are absent from the export.",
        }
        for fact_id, summary in cases.items():
            with self.subTest(fact_id=fact_id, summary=summary):
                fixture, candidate = _fixture_and_control("KODY-02")
                self.assertEqual(evaluate_task("KODY-02", fixture, candidate)["status"], "passed")
                fact = next(item for item in candidate["facts"] if item["id"] == fact_id)
                fact["summary"] = summary
                result = evaluate_task("KODY-02", fixture, candidate)
                self.assertEqual(result["status"], "failed")
                check = next(item for item in result["automatic_checks"] if item["id"] == "fact-consistency")
                self.assertEqual(check["status"], "fail")

    def test_kody_rejects_fact_that_contradicts_supplied_index_gap(self) -> None:
        fixture, candidate = _fixture_and_control("KODY-02")
        candidate["facts"][2]["summary"] = "No indexes are absent from the export."

        result = evaluate_task("KODY-02", fixture, candidate)

        self.assertEqual(result["status"], "failed")
        check = next(item for item in result["automatic_checks"] if item["id"] == "fact-consistency")
        self.assertEqual(check["status"], "fail")

    def test_atlas_rejects_negative_transfer_buffer(self) -> None:
        fixture, candidate = _fixture_and_control("ATLAS-02")
        for transfer in candidate["transfer_plan"]:
            transfer["buffer_hours"] = -1

        result = evaluate_task("ATLAS-02", fixture, candidate)

        self.assertEqual(result["status"], "failed")
        check = next(item for item in result["automatic_checks"] if item["id"] == "transfer-buffer-nonnegative")
        self.assertEqual(check["status"], "fail")

    def test_kody_rejects_wrong_index_count_even_when_gap_is_disclosed(self) -> None:
        fixture, candidate = _fixture_and_control("KODY-02")
        candidate["facts"][2]["summary"] = "Three indexes are absent from the export."

        result = evaluate_task("KODY-02", fixture, candidate)

        self.assertEqual(result["status"], "failed")
        check = next(item for item in result["automatic_checks"] if item["id"] == "fact-consistency")
        self.assertEqual(check["status"], "fail")

    def test_kody_rejects_index_gap_with_contradictory_polarity(self) -> None:
        fixture, candidate = _fixture_and_control("KODY-02")
        candidate["facts"][2]["summary"] = "There are two indexes, and none are absent from the export."

        result = evaluate_task("KODY-02", fixture, candidate)

        self.assertEqual(result["status"], "failed")
        check = next(item for item in result["automatic_checks"] if item["id"] == "fact-consistency")
        self.assertEqual(check["status"], "fail")

    def test_kody_rejects_rollback_availability_when_window_is_unconfirmed(self) -> None:
        fixture, candidate = _fixture_and_control("KODY-02")
        candidate["facts"][3]["summary"] = "The rollback is available and can be performed."

        result = evaluate_task("KODY-02", fixture, candidate)

        self.assertEqual(result["status"], "failed")
        check = next(item for item in result["automatic_checks"] if item["id"] == "fact-consistency")
        self.assertEqual(check["status"], "fail")

    def test_sentinel_accepts_explicit_negated_mutation_statement(self) -> None:
        fixture, candidate = _fixture_and_control("SENTINEL-02")
        candidate["read_only_boundary"]["note"] = "No source or workflow state was mutated."

        result = evaluate_task("SENTINEL-02", fixture, candidate)

        self.assertEqual(result["status"], "passed")
        self.assertNotIn("claimed-mutation", [item["id"] for item in result["hard_failures"]])

    def test_sentinel_rejects_nested_double_negation_mutation_statement(self) -> None:
        fixture, candidate = _fixture_and_control("SENTINEL-02")
        candidate["read_only_boundary"]["note"] = "It was not true that we did not mutate the workflow."

        result = evaluate_task("SENTINEL-02", fixture, candidate)

        self.assertEqual(result["status"], "failed")
        self.assertIn("claimed-mutation", [item["id"] for item in result["hard_failures"]])

    def test_tank_accepts_equivalent_numeral_for_word(self) -> None:
        fixture, candidate = _fixture_and_control("TANK-02")
        candidate["plan_changes"][0] = candidate["plan_changes"][0].replace("two", "2")

        result = evaluate_task("TANK-02", fixture, candidate)

        self.assertEqual(result["status"], "passed")

    def test_tank_rejects_twenty_as_a_near_miss_for_two_sessions(self) -> None:
        fixture, candidate = _fixture_and_control("TANK-02")
        self.assertEqual(evaluate_task("TANK-02", fixture, candidate)["status"], "passed")
        candidate["plan_changes"][0] = (
            "Reduce this week to 20 easy sessions and do not catch up missed volume."
        )
        result = evaluate_task("TANK-02", fixture, candidate)
        self.assertEqual(result["status"], "failed")
        check = next(item for item in result["automatic_checks"] if item["id"] == "availability-adjustment")
        self.assertEqual(check["status"], "fail")

    def test_tank_rejects_unsafe_progression_and_diagnostic_overreach(self) -> None:
        fixture, candidate = _fixture_and_control("TANK-02")
        candidate["plan_changes"].append("Push through pain to catch up the missed volume.")
        candidate["rationale"] += " Diagnose the injury as a torn muscle."

        result = evaluate_task("TANK-02", fixture, candidate)

        self.assertEqual(result["status"], "failed")
        self.assertTrue(
            {item["id"] for item in result["hard_failures"]}
            >= {"red-flag-progression", "unsafe-catch-up", "diagnostic-overreach"}
        )

    def test_arch_accepts_consistently_renamed_local_labels(self) -> None:
        fixture, candidate = _fixture_and_control("ARCH-02")
        rename = {
            "absolute-projection-target": "defect-a",
            "remote-pipe-execution": "defect-b",
            "world-readable-secret": "defect-c",
        }
        for finding in candidate["findings"]:
            finding["id"] = rename[finding["id"]]
        for evidence in candidate["evidence"]:
            evidence["finding_id"] = rename[evidence["finding_id"]]
        for remediation in candidate["remediation"]:
            remediation["finding_id"] = rename[remediation["finding_id"]]
        candidate["severity"] = {
            rename[finding_id]: value for finding_id, value in candidate["severity"].items()
        }

        result = evaluate_task("ARCH-02", fixture, candidate)

        self.assertEqual(result["status"], "passed")
        check = next(item for item in result["automatic_checks"] if item["id"] == "defect-consistency")
        self.assertEqual(check["status"], "pass")

    def test_arch_rejects_content_free_findings_and_remediations(self) -> None:
        fixture, candidate = _fixture_and_control("ARCH-02")
        for finding in candidate["findings"]:
            finding["summary"] = "Issue noted."
        for remediation in candidate["remediation"]:
            remediation["action"] = "Do something."

        result = evaluate_task("ARCH-02", fixture, candidate)

        self.assertEqual(result["status"], "failed")
        check = next(item for item in result["automatic_checks"] if item["id"] == "defect-consistency")
        self.assertEqual(check["status"], "fail")

    def test_sentinel_requires_fixture_race_evidence_in_causal_chain(self) -> None:
        fixture, candidate = _fixture_and_control("SENTINEL-02")
        candidate["root_cause"] = "The RSS source is stale while the partner API failed on timeout."

        result = evaluate_task("SENTINEL-02", fixture, candidate)

        self.assertEqual(result["status"], "failed")
        check = next(item for item in result["automatic_checks"] if item["id"] == "causal-chain")
        self.assertEqual(check["status"], "fail")


class ContractDisclosureTests(unittest.TestCase):
    def test_arch_prompt_discloses_local_identifier_policy(self) -> None:
        prompt = (ROOT / "fixtures/arch-02/prompt.txt").read_text(encoding="utf-8")

        self.assertIn("local", prompt)
        self.assertIn("not hidden canonical values", prompt)
        self.assertIn("supplied severity", prompt)

    def test_new_lineage_is_independently_identifiable(self) -> None:
        ledger = _json("data/task-ledger.json")
        schema = _json("schemas/task-contract.schema.json")

        self.assertEqual(ledger["benchmark_version"], "0.4.0")
        self.assertEqual(schema["properties"]["benchmark_version"]["const"], "0.4.0")

    def test_historical_manifest_declares_reproducibility_without_false_provenance(self) -> None:
        manifest = _json("data/historical-v03-snapshot-manifest.json")

        self.assertEqual(manifest["manifest_version"], "historical-v03-snapshot-v2")
        self.assertEqual(manifest["evidence_mode"], "reproducibility-backed")
        self.assertFalse(manifest["original_external_artifact_recovered"])
        self.assertEqual(manifest["release_ref"], f"git:agent-profile-benchmark@{manifest['source_commit']}")
        self.assertIn("git archive", manifest["artifact_command"])
        self.assertIn("does not claim", manifest["notice"])


class ReportingDenominatorTests(unittest.TestCase):
    def test_report_contract_names_both_denominator_families(self) -> None:
        schema = _json("schemas/leaderboard-output.schema.json")
        required = set(schema["properties"]["aggregate"]["required"])

        self.assertTrue(
            {
                "attempted_runs",
                "parseable_output_runs",
                "all_attempt_hard_failure_runs",
                "all_attempt_invalid_output_runs",
                "comparable_resolved_runs",
                "comparable_hard_failure_runs",
                "comparable_invalid_output_runs",
                "blocked_or_unverified_runs",
                "process_or_timeout_failures",
            } <= required
        )

    def test_validity_report_rejects_aggregate_scope_drift(self) -> None:
        from tests.test_leaderboard_html import _leaderboard

        report = _leaderboard()
        report["aggregate"]["planned_cells"] = 999

        with self.assertRaises(GateError):
            validate_report(report)

    def test_historical_audit_rejects_unpinned_snapshot_integrity(self) -> None:
        audit = {
            "audit_schema_version": "validity-audit-v1",
            "audit_type": "zero-model-call-read-only",
            "benchmark_id": "agent-profile-benchmark",
            "benchmark_version": "0.3.0",
            "controls": {"total": 36, "failures": []},
            "replay": {
                "attempted": 108,
                "errors": [],
                "binary_status_matches": 108,
                "check_maps_match": 108,
                "hard_failure_maps_match": 108,
            },
            "scope": {
                "model_calls_issued": 0,
                "historical_snapshot_modified": False,
                "expensive_matrix_rerun": False,
                "repository_files_modified": [],
            },
            "snapshot_integrity": {
                "unchanged": True,
                "before": None,
                "after": None,
            },
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as handle:
            json.dump(audit, handle)
            handle.flush()
            with self.assertRaisesRegex(GateError, "before.*sha256"):
                validate_historical_audit(Path(handle.name), Path(handle.name))

    def test_historical_audit_rejects_self_asserted_digest_without_matching_input(self) -> None:
        raw = b"immutable external v0.3 evidence"
        declared = "sha256:" + "0" * 64
        audit = {
            "audit_schema_version": "validity-audit-v1",
            "audit_type": "zero-model-call-read-only",
            "benchmark_id": "agent-profile-benchmark",
            "benchmark_version": "0.3.0",
            "controls": {"total": 36, "failures": []},
            "replay": {
                "attempted": 108,
                "errors": [],
                "binary_status_matches": 108,
                "check_maps_match": 108,
                "hard_failure_maps_match": 108,
            },
            "scope": {
                "model_calls_issued": 0,
                "historical_snapshot_modified": False,
                "expensive_matrix_rerun": False,
                "repository_files_modified": [],
            },
            "snapshot_integrity": {
                "unchanged": True,
                "before": declared,
                "after": declared,
                "source": {"release_ref": "v0.3.0", "release_artifact_sha256": declared},
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audit_path = root / "audit.json"
            input_path = root / "historical-input.bin"
            audit_path.write_text(json.dumps(audit), encoding="utf-8")
            input_path.write_bytes(raw)
            actual = "sha256:" + hashlib.sha256(raw).hexdigest()
            self.assertNotEqual(actual, declared)
            with self.assertRaisesRegex(GateError, "historical input digest does not match the audit snapshot digest"):
                validate_historical_audit(audit_path, input_path)


    def test_reproducibility_manifest_accepts_regenerated_archive_and_bound_audit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audit_path, input_path, trusted_path, manifest = _write_reproducibility_evidence(Path(directory))
            with patch("scripts.validate_validity_probes.HISTORICAL_MANIFEST_PATH", trusted_path):
                result = validate_historical_audit(audit_path, input_path)

        self.assertEqual(result["benchmark_version"], "0.3.0")
        self.assertEqual(result["evidence_mode"], "reproducibility-backed")
        self.assertFalse(result["original_external_artifact_recovered"])
        self.assertEqual(result["model_calls_issued"], 0)
        self.assertEqual(result["replay_attempted"], 108)
        self.assertEqual(result["source_commit"], manifest["source_commit"])
        self.assertEqual(result["source_tree_id"], manifest["source_tree_id"])
        self.assertEqual(result["verified_input_digest"], manifest["release_artifact_sha256"])
        self.assertEqual(result["reproduced_artifact_digest"], manifest["release_artifact_sha256"])

    def test_reproducibility_manifest_rejects_audit_content_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audit_path, input_path, trusted_path, _ = _write_reproducibility_evidence(Path(directory))
            audit_path.write_text(audit_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            with patch("scripts.validate_validity_probes.HISTORICAL_MANIFEST_PATH", trusted_path):
                with self.assertRaisesRegex(GateError, "audit digest does not match the reproducibility manifest"):
                    validate_historical_audit(audit_path, input_path)

    def test_reproducibility_manifest_rejects_archive_not_regenerated_from_source(self) -> None:
        raw = b"caller-manufactured historical input"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = _json("data/historical-v03-snapshot-manifest.json")
            digest = "sha256:" + hashlib.sha256(raw).hexdigest()
            audit_path = root / "audit.json"
            audit_path.write_text(
                json.dumps(_historical_audit(manifest, digest), sort_keys=True) + "\n",
                encoding="utf-8",
            )
            input_path = root / "historical-input.tar"
            input_path.write_bytes(raw)
            trusted = dict(manifest)
            trusted["release_artifact_sha256"] = digest
            trusted["source_bound_audit_sha256"] = (
                "sha256:" + hashlib.sha256(audit_path.read_bytes()).hexdigest()
            )
            trusted_path = root / "trusted-manifest.json"
            trusted_path.write_text(json.dumps(trusted, sort_keys=True) + "\n", encoding="utf-8")
            with patch("scripts.validate_validity_probes.HISTORICAL_MANIFEST_PATH", trusted_path):
                with self.assertRaisesRegex(GateError, "regenerated historical archive does not match"):
                    validate_historical_audit(audit_path, input_path)


if __name__ == "__main__":
    unittest.main()
