"""Run the v0.4.0 validity gate without invoking any model.

This gate evaluates only checked-in controls and deterministic report/audit
artifacts.  It intentionally has no model-runner dependency.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    from evaluate_task import evaluate_task
    from render_leaderboard_html import RenderError, render_html
    from release_lock import ReleaseLockError, load_release_lock, verify_task_release_artifacts
    from validate_benchmark import EXPECTED_TASK_IDS, validate_schema_instance
except ImportError:  # pragma: no cover - package-style import
    from scripts.evaluate_task import evaluate_task
    from scripts.render_leaderboard_html import RenderError, render_html
    from scripts.release_lock import ReleaseLockError, load_release_lock, verify_task_release_artifacts
    from scripts.validate_benchmark import EXPECTED_TASK_IDS, validate_schema_instance


ROOT = Path(__file__).resolve().parents[1]
PROBES_PATH = ROOT / "data" / "validity-probes.json"
PROBES_SCHEMA_PATH = ROOT / "schemas" / "validity-probes.schema.json"
HISTORICAL_MANIFEST_PATH = ROOT / "data" / "historical-v03-snapshot-manifest.json"
SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
GIT_OBJECT_ID_PATTERN = re.compile(r"^[0-9a-f]{40}$")
EXPECTED_HISTORICAL_SOURCE_COMMIT = "4a7b1bb1e79b6b18b3cb2f40954d651806ddfc11"
EXPECTED_HISTORICAL_SOURCE_TREE_ID = "357d8f617494fb197d4ca746337d0c3a7178a10a"
EXPECTED_HISTORICAL_TREE_FINGERPRINT = (
    "sha256:71d40d894459e331b2e19aa1de6e40e19d5f0ba237fb7880a300afb59fc11ef8"
)
EXPECTED_HISTORICAL_RELEASE_REF = f"git:agent-profile-benchmark@{EXPECTED_HISTORICAL_SOURCE_COMMIT}"
EXPECTED_HISTORICAL_ARTIFACT_COMMAND = (
    "git archive --format=tar --prefix=agent-profile-benchmark-v0.3.0/ "
    "--mtime='1970-01-01T00:00:00Z' 4a7b1bb1e79b6b18b3cb2f40954d651806ddfc11"
)


class GateError(ValueError):
    """Raised when the validity gate cannot establish a required invariant."""


def _load_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GateError(f"unable to read {label}: {type(exc).__name__}: {exc}") from exc


def _sha256_file(path: Path, label: str) -> str:
    digest = hashlib.sha256()
    if path.is_symlink():
        raise GateError(f"{label} must be a regular immutable release input, not a symlink")
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except (OSError, IOError) as exc:
        raise GateError(f"unable to hash {label}: {type(exc).__name__}: {exc}") from exc
    return f"sha256:{digest.hexdigest()}"


def _git_output(*arguments: str, label: str) -> bytes:
    result = subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise GateError(f"unable to verify {label}: git exited {result.returncode}: {detail}")
    return result.stdout


def _reproduce_historical_archive_digest(source_commit: str) -> str:
    archive = _git_output(
        "archive",
        "--format=tar",
        "--prefix=agent-profile-benchmark-v0.3.0/",
        "--mtime=1970-01-01T00:00:00Z",
        source_commit,
        label="the reproducibility archive",
    )
    return f"sha256:{hashlib.sha256(archive).hexdigest()}"


def _fixture_path(task_id: str) -> Path:
    filename = "request-packet.json" if task_id == "KODY-01" else "input.json"
    return ROOT / "fixtures" / task_id.lower() / filename


def _get_path(value: Any, path: list[Any]) -> Any:
    current = value
    for part in path:
        if isinstance(current, dict) and isinstance(part, str) and part in current:
            current = current[part]
        elif isinstance(current, list) and isinstance(part, int) and 0 <= part < len(current):
            current = current[part]
        else:
            raise GateError(f"mutation path does not exist: {path!r}")
    return current


def _get_parent(value: Any, path: list[Any]) -> tuple[Any, Any]:
    if not path:
        raise GateError("mutation path must not be empty")
    return _get_path(value, path[:-1]), path[-1]


def _same_json(left: Any, right: Any) -> bool:
    return type(left) is type(right) and left == right


def _apply_mutation(candidate: Any, mutation: dict[str, Any]) -> None:
    operation = mutation.get("operation")
    path = mutation.get("path")
    if not isinstance(path, list) or not path:
        raise GateError("probe mutation path must be a non-empty array")
    parent, key = _get_parent(candidate, path)
    if operation == "replace":
        if "from" not in mutation or "to" not in mutation:
            raise GateError("replace mutation must declare from and to")
        if isinstance(parent, dict) and isinstance(key, str):
            current = parent.get(key)
        elif isinstance(parent, list) and isinstance(key, int) and 0 <= key < len(parent):
            current = parent[key]
        else:
            raise GateError(f"replace mutation has invalid parent/key: {path!r}")
        if not _same_json(current, mutation["from"]):
            raise GateError(f"replace mutation source mismatch at {path!r}: {current!r}")
        if isinstance(parent, dict) and isinstance(key, str):
            parent[key] = mutation["to"]
        elif isinstance(parent, list) and isinstance(key, int) and 0 <= key < len(parent):
            parent[key] = mutation["to"]
        else:
            raise GateError(f"replace mutation has invalid parent/key: {path!r}")
        return
    if operation == "rename_key":
        target = _get_path(candidate, path)
        if not isinstance(target, dict):
            raise GateError("rename_key mutation must target an object")
        old_key = mutation.get("from")
        new_key = mutation.get("to")
        if not isinstance(old_key, str) or not isinstance(new_key, str) or not old_key or not new_key:
            raise GateError("rename_key mutation must declare non-empty string from/to")
        if old_key not in target:
            raise GateError(f"rename_key source is absent at {path!r}: {old_key!r}")
        if new_key in target:
            raise GateError(f"rename_key target already exists at {path!r}: {new_key!r}")
        target[new_key] = target.pop(old_key)
        return
    raise GateError(f"unsupported probe mutation operation: {operation!r}")


def _validate_probe_catalogue(probes: Any) -> list[str]:
    schema = _load_json(PROBES_SCHEMA_PATH, "validity probe schema")
    if not isinstance(schema, dict):
        return ["validity probe schema must be an object"]
    errors = validate_schema_instance(probes, schema)
    if errors:
        return [f"probe schema: {error}" for error in errors]
    if not isinstance(probes, dict) or probes.get("benchmark_version") != "0.4.0":
        return ["validity probes must belong to benchmark version 0.4.0"]
    return []


def _control_result(task_id: str, condition: str) -> dict[str, Any]:
    task_root = ROOT / "fixtures" / task_id.lower()
    fixture = _load_json(_fixture_path(task_id), f"{task_id} fixture")
    candidate = _load_json(
        task_root / "controls" / ("known-good.json" if condition == "known-good-control" else "known-bad.json"),
        f"{task_id} {condition}",
    )
    expected = "passed" if condition == "known-good-control" else "failed"
    result = evaluate_task(task_id, fixture, candidate, model_output=False)
    if result.get("status") != expected:
        raise GateError(f"{task_id} {condition} returned {result.get('status')!r}, expected {expected!r}")
    if result.get("status") == "blocked":
        raise GateError(f"{task_id} {condition} was blocked")
    return result


def validate_controls() -> dict[str, int]:
    good = 0
    bad = 0
    for task_id in sorted(EXPECTED_TASK_IDS):
        _control_result(task_id, "known-good-control")
        good += 1
        _control_result(task_id, "known-bad-control")
        bad += 1
    return {"known_good": good, "known_bad": bad, "total": good + bad}


def validate_probes(probes: dict[str, Any]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for probe in probes["probes"]:
        task_id = probe["task_id"]
        task_root = ROOT / "fixtures" / task_id.lower()
        fixture = _load_json(_fixture_path(task_id), f"{task_id} fixture")
        candidate = _load_json(task_root / "controls" / "known-good.json", f"{task_id} known-good control")
        mutated = copy.deepcopy(candidate)
        for mutation in probe["mutations"]:
            _apply_mutation(mutated, mutation)
        result = evaluate_task(task_id, fixture, mutated, model_output=False)
        expected_status = probe["expected_status"]
        if result.get("status") != expected_status:
            raise GateError(
                f"probe {probe['id']} returned {result.get('status')!r}, expected {expected_status!r}"
            )
        expected_check = probe["expected_check"]
        check = next(
            (item for item in result.get("automatic_checks", []) if item.get("id") == expected_check["id"]),
            None,
        )
        if not isinstance(check, dict) or check.get("status") != expected_check["status"]:
            raise GateError(
                f"probe {probe['id']} check {expected_check['id']!r} did not return "
                f"{expected_check['status']!r}"
            )
        expected_hard = set(probe.get("expected_hard_failure_ids", []))
        actual_hard = {
            item.get("id")
            for item in result.get("hard_failures", [])
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        if expected_hard and actual_hard != expected_hard:
            raise GateError(
                f"probe {probe['id']} hard failures {sorted(actual_hard)!r}, "
                f"expected {sorted(expected_hard)!r}"
            )
        results.append({"id": probe["id"], "status": result["status"], "check": check["status"]})
    return {"total": len(results), "results": results}


def validate_report(report: dict[str, Any]) -> dict[str, Any]:
    try:
        render_html(report)
    except RenderError as exc:
        raise GateError(str(exc)) from exc
    aggregate = report.get("aggregate")
    if not isinstance(aggregate, dict):
        raise GateError("report aggregate must be an object")
    return {"attempted_runs": aggregate["attempted_runs"], "comparable_runs": aggregate["comparable_resolved_runs"]}


def validate_trusted_release() -> dict[str, Any]:
    """Establish the sealed release boundary before executing any control."""
    try:
        lock = load_release_lock()
        bindings = {
            task_id: verify_task_release_artifacts(task_id, "local-replay")
            for task_id in sorted(EXPECTED_TASK_IDS)
        }
    except (KeyError, OSError, ReleaseLockError, TypeError, UnicodeError) as exc:
        raise GateError(f"trusted release could not be verified: {exc}") from exc
    return {"lock_version": lock["lock_version"], "tasks": len(bindings)}


def validate_historical_audit(path: Path, historical_input: Path) -> dict[str, Any]:
    """Validate reproducibility-backed v0.3 evidence without model calls.

    The supplied archive and audit are evidence inputs, not proof that an
    external historical package was recovered.  The committed manifest binds
    them to a pinned Git source and the gate independently regenerates the
    deterministic archive from that source.
    """
    audit = _load_json(path, "historical v0.3 validity audit")
    if not isinstance(audit, dict):
        raise GateError("historical audit must be an object")
    expected = {
        "audit_schema_version": "validity-audit-v1",
        "audit_type": "zero-model-call-read-only",
        "benchmark_id": "agent-profile-benchmark",
        "benchmark_version": "0.3.0",
    }
    for key, value in expected.items():
        if audit.get(key) != value:
            raise GateError(f"historical audit {key} must be {value!r}")
    controls = audit.get("controls")
    replay = audit.get("replay")
    scope = audit.get("scope")
    snapshot = audit.get("snapshot_integrity")
    if not isinstance(controls, dict) or controls.get("total") != 36 or controls.get("failures") != []:
        raise GateError("historical audit controls are not the expected 36/0 result")
    if not isinstance(replay, dict) or replay.get("attempted") != 108 or replay.get("errors") != []:
        raise GateError("historical audit replay did not close 108 records without errors")
    if any(replay.get(key) != 108 for key in ("binary_status_matches", "check_maps_match", "hard_failure_maps_match")):
        raise GateError("historical audit replay maps are incomplete")
    if (
        not isinstance(scope, dict)
        or scope.get("model_calls_issued") != 0
        or scope.get("historical_snapshot_modified") is not False
        or scope.get("expensive_matrix_rerun") is not False
        or scope.get("repository_files_modified") != []
    ):
        raise GateError("historical audit scope is not read-only and zero-call")
    if not isinstance(snapshot, dict) or snapshot.get("unchanged") is not True:
        raise GateError("historical snapshot integrity did not remain unchanged")
    before = snapshot.get("before")
    after = snapshot.get("after")
    if not isinstance(before, str) or not SHA256_PATTERN.fullmatch(before):
        raise GateError("historical snapshot integrity.before must be a sha256 digest")
    if not isinstance(after, str) or not SHA256_PATTERN.fullmatch(after):
        raise GateError("historical snapshot integrity.after must be a sha256 digest")
    if before != after:
        raise GateError("historical snapshot integrity before/after digests differ")
    manifest = _load_json(HISTORICAL_MANIFEST_PATH, "committed historical v0.3 snapshot manifest")
    if not isinstance(manifest, dict):
        raise GateError("committed historical v0.3 snapshot manifest must be an object")
    required_manifest = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "manifest_version": "historical-v03-snapshot-v2",
        "evidence_mode": "reproducibility-backed",
        "original_external_artifact_recovered": False,
        "benchmark_id": "agent-profile-benchmark",
        "benchmark_version": "0.3.0",
        "source_commit": EXPECTED_HISTORICAL_SOURCE_COMMIT,
        "source_tree_id": EXPECTED_HISTORICAL_SOURCE_TREE_ID,
        "historical_tree_fingerprint": EXPECTED_HISTORICAL_TREE_FINGERPRINT,
        "release_ref": EXPECTED_HISTORICAL_RELEASE_REF,
        "artifact_format": "tar",
        "artifact_command": EXPECTED_HISTORICAL_ARTIFACT_COMMAND,
    }
    required_manifest["release_artifact_sha256"] = None
    required_manifest["source_bound_audit_sha256"] = None
    required_manifest["derived_from_original_audit_sha256"] = None
    required_manifest["notice"] = None
    if set(manifest) != set(required_manifest):
        raise GateError("committed historical v0.3 snapshot manifest has unexpected or missing fields")
    for key, value in required_manifest.items():
        if value is not None and manifest.get(key) != value:
            raise GateError(f"historical snapshot manifest {key} is not the committed v0.3 pin")
    manifest_digest = manifest.get("release_artifact_sha256")
    if not isinstance(manifest_digest, str) or not SHA256_PATTERN.fullmatch(manifest_digest):
        raise GateError("historical snapshot manifest release_artifact_sha256 must be a sha256 digest")
    audit_digest = manifest.get("source_bound_audit_sha256")
    if not isinstance(audit_digest, str) or not SHA256_PATTERN.fullmatch(audit_digest):
        raise GateError("historical snapshot manifest source_bound_audit_sha256 must be a sha256 digest")
    derived_audit_digest = manifest.get("derived_from_original_audit_sha256")
    if not isinstance(derived_audit_digest, str) or not SHA256_PATTERN.fullmatch(derived_audit_digest):
        raise GateError(
            "historical snapshot manifest derived_from_original_audit_sha256 must be a sha256 digest"
        )
    notice = manifest.get("notice")
    if not isinstance(notice, str) or "not" not in notice.lower() or "original" not in notice.lower():
        raise GateError("historical snapshot manifest notice must disclose the unavailable original artifact")
    actual_digest = _sha256_file(historical_input, "reproducibility-backed historical v0.3 release input")
    if actual_digest != before:
        raise GateError("historical input digest does not match the audit snapshot digest")
    if actual_digest != manifest_digest:
        raise GateError("historical input digest does not match the reproducibility manifest")
    if audit_digest != _sha256_file(path, "source-bound historical v0.3 validity audit"):
        raise GateError("historical audit digest does not match the reproducibility manifest")
    if audit["snapshot_integrity"].get("derived_from_audit_sha256") != derived_audit_digest:
        raise GateError("historical audit derivation does not match the reproducibility manifest")
    if audit["snapshot_integrity"].get("original_tree_fingerprint") != manifest["historical_tree_fingerprint"]:
        raise GateError("historical audit tree fingerprint does not match the reproducibility manifest")
    source_commit = manifest["source_commit"]
    if not GIT_OBJECT_ID_PATTERN.fullmatch(source_commit):
        raise GateError("historical snapshot manifest source_commit must be a 40-character Git object ID")
    source_tree_id = manifest["source_tree_id"]
    if not GIT_OBJECT_ID_PATTERN.fullmatch(source_tree_id):
        raise GateError("historical snapshot manifest source_tree_id must be a 40-character Git object ID")
    actual_tree_id = _git_output(
        "rev-parse",
        "--verify",
        f"{source_commit}^{{tree}}",
        label="the pinned historical source tree",
    ).decode("ascii", "strict").strip()
    if actual_tree_id != source_tree_id:
        raise GateError("pinned historical source tree does not match the reproducibility manifest")
    reproduced_digest = _reproduce_historical_archive_digest(source_commit)
    if reproduced_digest != manifest_digest:
        raise GateError("regenerated historical archive does not match the reproducibility manifest")
    source = snapshot.get("source")
    if not isinstance(source, dict):
        raise GateError("historical snapshot integrity must identify the pinned source")
    release_ref = source.get("release_ref")
    release_digest = source.get("release_artifact_sha256")
    if release_ref != manifest["release_ref"]:
        raise GateError("historical snapshot source release_ref does not match the committed snapshot manifest")
    if release_digest != manifest_digest:
        raise GateError("historical snapshot source digest does not match the committed snapshot manifest")
    return {
        "benchmark_version": audit["benchmark_version"],
        "evidence_mode": manifest["evidence_mode"],
        "original_external_artifact_recovered": manifest["original_external_artifact_recovered"],
        "replay_attempted": replay["attempted"],
        "model_calls_issued": scope["model_calls_issued"],
        "snapshot_unchanged": snapshot["unchanged"],
        "snapshot_digest": before,
        "release_ref": release_ref,
        "verified_input_digest": actual_digest,
        "reproduced_artifact_digest": reproduced_digest,
        "source_bound_audit_digest": audit_digest,
        "source_commit": source_commit,
        "source_tree_id": source_tree_id,
        "historical_tree_fingerprint": manifest["historical_tree_fingerprint"],
    }


def run_gate(historical_audit: Path, historical_input: Path, report: Path | None) -> dict[str, Any]:
    release = validate_trusted_release()
    probes = _load_json(PROBES_PATH, "validity probe catalogue")
    schema_errors = _validate_probe_catalogue(probes)
    if schema_errors:
        raise GateError("; ".join(schema_errors))
    if not isinstance(probes, dict):
        raise GateError("validity probe catalogue must be an object")
    controls = validate_controls()
    probe_results = validate_probes(probes)
    historical = validate_historical_audit(historical_audit, historical_input)
    report_result = validate_report(_load_json(report, "v0.4 leaderboard report")) if report else None
    return {
        "gate": "zero-model-call-validity-v04",
        "benchmark_version": "0.4.0",
        "model_calls_issued": 0,
        "trusted_release": release,
        "controls": controls,
        "probes": probe_results,
        "historical_v03": historical,
        "report": report_result,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--historical-audit", type=Path, required=True)
    parser.add_argument("--historical-input", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    try:
        result = run_gate(args.historical_audit, args.historical_input, args.report)
    except (GateError, OSError, ValueError) as exc:
        print(f"validity gate failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
