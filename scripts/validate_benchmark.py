#!/usr/bin/env python3
"""Validate the public agent-profile benchmark task ledger."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


EXPECTED_BENCHMARK_VERSION = "0.2.0"
EXPECTED_LEDGER_SCHEMA = "../schemas/task-contract.schema.json"
EXPECTED_LEDGER_FINGERPRINT = "7632bac24f0d8b815c05fdb4d71197d7364ced10e5c858e9b2457c870eb60b96"
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
    "kody": ("KODY-01", "KODY-02"),
    "aegis": ("AEGIS-01", "AEGIS-02"),
    "arch": ("ARCH-01", "ARCH-02"),
    "atlas": ("ATLAS-01", "ATLAS-02"),
    "tank": ("TANK-01", "TANK-02"),
    "oracle": ("ORACLE-01", "ORACLE-02"),
    "sentinel": ("SENTINEL-01", "SENTINEL-02"),
    "morph": ("MORPH-01", "MORPH-02"),
    "seraph": ("SERAPH-01", "SERAPH-02"),
}
EXPECTED_TASK_IDS = {task_id for task_ids in EXPECTED_TASKS_BY_PROFILE.values() for task_id in task_ids}
EXPECTED_TASK_SEQUENCE_BY_ID = {
    task_id: sequence
    for task_ids in EXPECTED_TASKS_BY_PROFILE.values()
    for sequence, task_id in enumerate(task_ids, start=1)
}
EXPECTED_PROVENANCE = {"historical_candidate", "synthetic"}
EXPECTED_PROVENANCE_VALUES = ["historical_candidate", "synthetic"]
EXPECTED_PRIVACY_CLASSES = {"public_synthetic", "sanitized_candidate"}
EXPECTED_STATUSES = {"contract-draft", "fixture-ready", "benchmark-ready"}
EXPECTED_STATUS_VALUES = ["contract-draft", "fixture-ready", "benchmark-ready"]
EXPECTED_SOURCE_POLICY_BY_CLASS = {
    ("synthetic", "public_synthetic"): "public_synthetic",
    ("historical_candidate", "public_synthetic"): "public_synthetic",
    ("historical_candidate", "sanitized_candidate"): "sanitized_or_synthetic_reconstruction",
}
ID_PATTERN = re.compile(r"^[A-Z][A-Z0-9-]+-[0-9]{2}$")
FIELD_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
PRIVATE_PATH_PATTERN = re.compile(r"(?<![A-Za-z0-9_])/(?:Users|Volumes|home|private/var)/[^\s\"'`]+")
CREDENTIAL_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(?:api[_-]?key|passwd|password|secret|token)\s*[:=]\s*[\"'][^\"']{8,}[\"']"
)
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "task-contract.schema.json"
SUPPORTED_SCHEMA_KEYS = {
    "$defs",
    "$id",
    "$ref",
    "$schema",
    "additionalProperties",
    "const",
    "description",
    "enum",
    "exclusiveMinimum",
    "items",
    "maxItems",
    "maximum",
    "minItems",
    "minLength",
    "pattern",
    "properties",
    "required",
    "title",
    "type",
    "uniqueItems",
}
SUPPORTED_JSON_TYPES = {"array", "boolean", "integer", "null", "number", "object", "string"}


class DuplicateJSONKeyError(ValueError):
    """Raised when a JSON object repeats a member name."""


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise DuplicateJSONKeyError(f"duplicate JSON object key {key!r}")
        value[key] = item
    return value


def _json_type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def _json_values_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    return type(left) is type(right) and left == right


def _canonical_fingerprint(value: Any) -> str | None:
    try:
        serialized = json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _resolve_schema_ref(reference: str, root_schema: dict[str, Any]) -> dict[str, Any] | None:
    prefix = "#/$defs/"
    if not reference.startswith(prefix):
        return None
    target: Any = root_schema.get("$defs", {})
    for part in reference[len(prefix) :].split("/"):
        if not isinstance(target, dict) or part not in target:
            return None
        target = target[part]
    return target if isinstance(target, dict) else None


def _validate_schema_shape(
    schema: Any,
    root_schema: dict[str, Any],
    path: str,
    errors: list[str],
) -> None:
    if not isinstance(schema, dict):
        errors.append(f"schema is malformed or unsupported: {path} must be a schema object")
        return

    unsupported_keys = sorted(set(schema) - SUPPORTED_SCHEMA_KEYS)
    if unsupported_keys:
        errors.append(f"{path} uses unsupported schema keywords: {', '.join(unsupported_keys)}")

    for key in ("$id", "$schema", "description", "title"):
        if key in schema and not isinstance(schema[key], str):
            errors.append(f"schema is malformed or unsupported: {path}.{key} must be a string")

    if "$ref" in schema:
        reference = schema["$ref"]
        if not isinstance(reference, str):
            errors.append(f"schema is malformed or unsupported: {path}.$ref must be a string")
        elif _resolve_schema_ref(reference, root_schema) is None:
            errors.append(f"schema is malformed or unsupported: {path} has an unresolved $ref")

    if "type" in schema:
        schema_type = schema["type"]
        if not isinstance(schema_type, str) or schema_type not in SUPPORTED_JSON_TYPES:
            errors.append(f"schema is malformed or unsupported: {path}.type must be a supported string")

    if "enum" in schema and not isinstance(schema["enum"], list):
        errors.append(f"schema is malformed or unsupported: {path}.enum must be an array")

    if "required" in schema:
        required = schema["required"]
        if (
            not isinstance(required, list)
            or any(not isinstance(key, str) for key in required)
            or len(required) != len(set(required))
        ):
            errors.append(f"schema is malformed or unsupported: {path}.required must be unique string names")

    properties = schema.get("properties")
    if properties is not None:
        if not isinstance(properties, dict):
            errors.append(f"schema is malformed or unsupported: {path}.properties must be an object")
        else:
            for key, child in properties.items():
                _validate_schema_shape(child, root_schema, f"{path}.properties.{key}", errors)

    definitions = schema.get("$defs")
    if definitions is not None:
        if not isinstance(definitions, dict):
            errors.append(f"schema is malformed or unsupported: {path}.$defs must be an object")
        else:
            for key, child in definitions.items():
                _validate_schema_shape(child, root_schema, f"{path}.$defs.{key}", errors)

    if "items" in schema:
        _validate_schema_shape(schema["items"], root_schema, f"{path}.items", errors)

    if "additionalProperties" in schema:
        additional = schema["additionalProperties"]
        if not isinstance(additional, (bool, dict)):
            errors.append(
                f"schema is malformed or unsupported: {path}.additionalProperties must be boolean or schema"
            )
        elif isinstance(additional, dict):
            _validate_schema_shape(additional, root_schema, f"{path}.additionalProperties", errors)

    for key in ("minItems", "maxItems", "minLength"):
        if key in schema and (not isinstance(schema[key], int) or isinstance(schema[key], bool)):
            errors.append(f"schema is malformed or unsupported: {path}.{key} must be an integer")
    for key in ("exclusiveMinimum", "maximum"):
        if key in schema and (
            not isinstance(schema[key], (int, float)) or isinstance(schema[key], bool)
        ):
            errors.append(f"schema is malformed or unsupported: {path}.{key} must be a number")
    if "pattern" in schema and not isinstance(schema["pattern"], str):
        errors.append(f"schema is malformed or unsupported: {path}.pattern must be a string")
    if "uniqueItems" in schema and not isinstance(schema["uniqueItems"], bool):
        errors.append(f"schema is malformed or unsupported: {path}.uniqueItems must be boolean")


def _validate_schema_node(
    value: Any,
    schema: dict[str, Any],
    root_schema: dict[str, Any],
    path: str,
    errors: list[str],
) -> None:
    unsupported_keys = sorted(set(schema) - SUPPORTED_SCHEMA_KEYS)
    if unsupported_keys:
        errors.append(f"{path} uses unsupported schema keywords: {', '.join(unsupported_keys)}")

    reference = schema.get("$ref")
    if reference is not None:
        target = _resolve_schema_ref(reference, root_schema)
        if target is None:
            errors.append(f"{path} uses an unresolved schema reference {reference!r}")
            return
        _validate_schema_node(value, target, root_schema, path, errors)
        return

    expected_type = schema.get("type")
    if isinstance(expected_type, str) and not _json_type_matches(value, expected_type):
        errors.append(f"{path} must be a {expected_type}")
        return

    if "const" in schema and not _json_values_equal(value, schema["const"]):
        errors.append(f"{path} must equal the declared constant")
    enum = schema.get("enum")
    if isinstance(enum, list) and not any(_json_values_equal(value, item) for item in enum):
        errors.append(f"{path} is not an allowed value")

    if isinstance(value, dict):
        required = schema.get("required", [])
        if isinstance(required, list):
            for key in required:
                if key not in value:
                    errors.append(f"{path} is missing required key {key!r}")
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            properties = {}
        additional = schema.get("additionalProperties", True)
        for key, child in value.items():
            if key in properties:
                _validate_schema_node(child, properties[key], root_schema, f"{path}.{key}", errors)
            elif additional is False:
                errors.append(f"{path} contains unexpected key {key!r}")
            elif isinstance(additional, dict):
                _validate_schema_node(child, additional, root_schema, f"{path}.{key}", errors)

    if isinstance(value, list):
        minimum = schema.get("minItems")
        if isinstance(minimum, int) and len(value) < minimum:
            errors.append(f"{path} must contain at least {minimum} items")
        maximum = schema.get("maxItems")
        if isinstance(maximum, int) and len(value) > maximum:
            errors.append(f"{path} must contain at most {maximum} items")
        if schema.get("uniqueItems") is True:
            for index, item in enumerate(value):
                if any(_json_values_equal(item, prior) for prior in value[:index]):
                    errors.append(f"{path} must contain unique items")
                    break
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _validate_schema_node(item, item_schema, root_schema, f"{path}[{index}]", errors)

    if isinstance(value, str):
        minimum = schema.get("minLength")
        if isinstance(minimum, int) and len(value) < minimum:
            errors.append(f"{path} must contain at least {minimum} characters")
        pattern = schema.get("pattern")
        if isinstance(pattern, str):
            try:
                matches = re.search(pattern, value)
            except re.error:
                matches = False
                errors.append(f"{path} uses an invalid schema pattern")
            if not matches:
                errors.append(f"{path} does not match the declared pattern")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        exclusive_minimum = schema.get("exclusiveMinimum")
        if isinstance(exclusive_minimum, (int, float)) and value <= exclusive_minimum:
            errors.append(f"{path} must be greater than {exclusive_minimum}")
        maximum = schema.get("maximum")
        if isinstance(maximum, (int, float)) and value > maximum:
            errors.append(f"{path} must be at most {maximum}")


def validate_schema_instance(value: Any, schema: dict[str, Any]) -> list[str]:
    """Return structural validation errors using the checked-in JSON Schema."""

    errors: list[str] = []
    if not isinstance(schema, dict):
        return ["schema must be an object"]
    try:
        _validate_schema_shape(schema, schema, "$", errors)
        if errors:
            return errors
        _validate_schema_node(value, schema, schema, "$", errors)
    except (AttributeError, IndexError, KeyError, TypeError, ValueError) as exc:
        errors.append(f"schema is malformed or unsupported: {type(exc).__name__}: {exc}")
    return errors


def _load_schema_errors(value: Any) -> list[str]:
    try:
        schema = json.loads(
            SCHEMA_PATH.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except DuplicateJSONKeyError as exc:
        return [f"unable to load contract schema: {exc}"]
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return [f"unable to load contract schema ({type(exc).__name__})"]
    return [f"schema: {error}" for error in validate_schema_instance(value, schema)]


def _is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_allowed_string(value: Any, allowed: set[str]) -> bool:
    return isinstance(value, str) and value in allowed


def _scan_public_text(value: Any, path: str, errors: list[str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            _scan_public_text(child, f"{path}.{key}", errors)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _scan_public_text(child, f"{path}[{index}]", errors)
    elif isinstance(value, str):
        if PRIVATE_PATH_PATTERN.search(value):
            errors.append(f"{path} contains a private absolute path")
        if CREDENTIAL_ASSIGNMENT_PATTERN.search(value):
            errors.append(f"{path} contains credential-shaped text")


def _require_keys(value: Any, required: set[str], path: str, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append(f"{path} must be an object")
        return
    missing = sorted(required - value.keys())
    if missing:
        errors.append(f"{path} missing required keys: {', '.join(missing)}")
    unexpected = sorted(
        str(key) for key in value if not isinstance(key, str) or key not in required
    )
    if unexpected:
        errors.append(f"{path} contains unexpected keys: {', '.join(unexpected)}")


def _validate_string_list(
    value: Any,
    path: str,
    errors: list[str],
    minimum: int = 1,
    unique: bool = False,
) -> None:
    if not isinstance(value, list) or len(value) < minimum:
        errors.append(f"{path} must contain at least {minimum} items")
        return
    valid_items = True
    for index, item in enumerate(value):
        if not _is_nonempty_string(item):
            valid_items = False
            errors.append(f"{path}[{index}] must be a non-empty string")
    if unique and valid_items and len(value) != len(set(value)):
        errors.append(f"{path} must contain unique items")


def _validate_dimension(dimension: Any, task_id: str, errors: list[str]) -> None:
    path = f"tasks[{task_id}].measurement.human_dimensions"
    _require_keys(dimension, {"id", "label", "weight", "anchors"}, path, errors)
    if not isinstance(dimension, dict):
        return
    dimension_id = dimension.get("id")
    if not isinstance(dimension_id, str) or not FIELD_ID_PATTERN.fullmatch(dimension_id):
        errors.append(f"{path} id must be lowercase kebab-case string")
    if not _is_nonempty_string(dimension.get("label")):
        errors.append(f"{path} label must be non-empty")
    weight = dimension.get("weight")
    if not isinstance(weight, (int, float)) or isinstance(weight, bool) or not 0 < weight <= 1:
        errors.append(f"{path} weight must be greater than 0 and at most 1")
    anchors = dimension.get("anchors")
    anchors_path = f"{path}.anchors"
    _require_keys(anchors, {"0", "2", "4"}, anchors_path, errors)
    if not isinstance(anchors, dict) or any(
        not _is_nonempty_string(anchors.get(key)) for key in ("0", "2", "4")
    ):
        errors.append(f"{path} anchors must define non-empty 0, 2, and 4 levels")


def _validate_task(task: Any, profile_ids: set[str], errors: list[str]) -> None:
    if not isinstance(task, dict):
        errors.append("tasks contains a non-object item")
        return

    task_id = str(task.get("id", "<missing>"))
    path = f"tasks[{task_id}]"
    _require_keys(
        task,
        {
            "id",
            "profile_id",
            "sequence",
            "title",
            "task_family",
            "provenance",
            "provenance_note",
            "privacy_class",
            "status",
            "fixture",
            "prompt_contract",
            "measurement",
            "known_failure_modes",
        },
        path,
        errors,
    )

    if not ID_PATTERN.fullmatch(task_id):
        errors.append(f"{path}.id must match PROFILE-NN")
    if not isinstance(task.get("id"), str):
        errors.append(f"{path}.id must be a string")
    profile_id = task.get("profile_id")
    if not isinstance(profile_id, str):
        errors.append(f"{path}.profile_id must be a string")
    elif profile_id not in profile_ids:
        errors.append(f"{path}.profile_id references unknown profile {profile_id!r}")
    sequence = task.get("sequence")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence not in (1, 2):
        errors.append(f"{path}.sequence must be 1 or 2")
    task_id_value = task.get("id")
    expected_sequence = (
        EXPECTED_TASK_SEQUENCE_BY_ID.get(task_id_value)
        if isinstance(task_id_value, str)
        else None
    )
    if expected_sequence is not None and sequence != expected_sequence:
        errors.append(f"{path}.sequence must be {expected_sequence} for the frozen task ID")
    for field in ("title", "task_family", "provenance_note"):
        if not _is_nonempty_string(task.get(field)):
            errors.append(f"{path}.{field} must be non-empty")
    if not _is_allowed_string(task.get("provenance"), EXPECTED_PROVENANCE):
        errors.append(f"{path}.provenance is not an allowed value")
    if not _is_allowed_string(task.get("privacy_class"), EXPECTED_PRIVACY_CLASSES):
        errors.append(f"{path}.privacy_class is not an allowed value")
    if not _is_allowed_string(task.get("status"), EXPECTED_STATUSES):
        errors.append(f"{path}.status is not an allowed value")

    fixture = task.get("fixture")
    fixture_path = f"{path}.fixture"
    _require_keys(
        fixture,
        {"id", "status", "source_policy", "live_web", "allowed_tools", "notes"},
        fixture_path,
        errors,
    )
    if isinstance(fixture, dict):
        fixture_id = fixture.get("id")
        if not isinstance(fixture_id, str) or not re.fullmatch(
            r"^[a-z0-9][a-z0-9-]*-v[0-9]+$", fixture_id
        ):
            errors.append(f"{fixture_path}.id must be a string ending with a numeric version")
        if not _is_allowed_string(fixture.get("status"), {"to_be_frozen", "frozen"}):
            errors.append(f"{fixture_path}.status is not an allowed value")
        if not _is_allowed_string(
            fixture.get("source_policy"),
            {"public_synthetic", "sanitized_or_synthetic_reconstruction"},
        ):
            errors.append(f"{fixture_path}.source_policy is not an allowed value")
        provenance = task.get("provenance")
        privacy_class = task.get("privacy_class")
        source_policy = fixture.get("source_policy")
        expected_source_policy = (
            EXPECTED_SOURCE_POLICY_BY_CLASS.get((provenance, privacy_class))
            if isinstance(provenance, str) and isinstance(privacy_class, str)
            else None
        )
        if expected_source_policy is None or source_policy != expected_source_policy:
            errors.append(
                f"{fixture_path}.source_policy is incompatible with provenance and privacy classification"
            )
        if fixture.get("live_web") is not False:
            errors.append(f"{fixture_path}.live_web must be false")
        allowed_tools = fixture.get("allowed_tools")
        if not isinstance(allowed_tools, list):
            errors.append(f"{fixture_path}.allowed_tools must be a list")
        elif any(not _is_nonempty_string(tool) for tool in allowed_tools):
            errors.append(f"{fixture_path}.allowed_tools must contain non-empty strings")
        elif allowed_tools != []:
            errors.append(f"{fixture_path}.allowed_tools must be empty for offline replay")
        if not _is_nonempty_string(fixture.get("notes")):
            errors.append(f"{fixture_path}.notes must be non-empty")

    prompt = task.get("prompt_contract")
    prompt_path = f"{path}.prompt_contract"
    _require_keys(
        prompt,
        {
            "status",
            "input_description",
            "required_output_format",
            "required_output_fields",
            "output_constraints",
        },
        prompt_path,
        errors,
    )
    if isinstance(prompt, dict):
        if not _is_allowed_string(prompt.get("status"), {"to_be_frozen", "frozen"}):
            errors.append(f"{prompt_path}.status is not an allowed value")
        if not _is_nonempty_string(prompt.get("input_description")):
            errors.append(f"{prompt_path}.input_description must be non-empty")
        if not _is_allowed_string(
            prompt.get("required_output_format"),
            {"json", "repository_change", "structured_markdown"},
        ):
            errors.append(f"{prompt_path}.required_output_format is not an allowed value")
        _validate_string_list(
            prompt.get("required_output_fields"),
            f"{prompt_path}.required_output_fields",
            errors,
            3,
            unique=True,
        )
        _validate_string_list(prompt.get("output_constraints"), f"{prompt_path}.output_constraints", errors)

    measurement = task.get("measurement")
    measurement_path = f"{path}.measurement"
    _require_keys(
        measurement,
        {"primary_type", "automatic_checks", "human_dimensions", "hard_failures"},
        measurement_path,
        errors,
    )
    if isinstance(measurement, dict):
        if not _is_allowed_string(measurement.get("primary_type"), {"objective", "hybrid", "rubric"}):
            errors.append(f"{measurement_path}.primary_type is not an allowed value")

        checks = measurement.get("automatic_checks")
        if not isinstance(checks, list) or not checks:
            errors.append(f"{measurement_path}.automatic_checks must contain at least one check")
        else:
            check_ids: list[str] = []
            for check in checks:
                check_path = f"{measurement_path}.automatic_checks"
                _require_keys(check, {"id", "type", "description", "pass_condition"}, check_path, errors)
                if isinstance(check, dict):
                    check_id = check.get("id")
                    check_ids.append(check_id if isinstance(check_id, str) else "")
                    if not isinstance(check_id, str) or not FIELD_ID_PATTERN.fullmatch(check_id):
                        errors.append(f"{check_path} ids must be lowercase kebab-case strings")
                    for field in ("type", "description", "pass_condition"):
                        if not _is_nonempty_string(check.get(field)):
                            errors.append(f"{check_path}.{field} must be non-empty")
            if len(check_ids) != len(set(check_ids)):
                errors.append(f"{measurement_path}.automatic_checks contains duplicate ids")

        dimensions = measurement.get("human_dimensions")
        if not isinstance(dimensions, list) or len(dimensions) < 2:
            errors.append(f"{measurement_path}.human_dimensions must contain at least two dimensions")
        else:
            dimension_ids: list[str] = []
            weight_total = 0.0
            for dimension in dimensions:
                _validate_dimension(dimension, task_id, errors)
                if isinstance(dimension, dict):
                    dimension_id = dimension.get("id")
                    dimension_ids.append(dimension_id if isinstance(dimension_id, str) else "")
                    weight = dimension.get("weight")
                    if isinstance(weight, (int, float)) and not isinstance(weight, bool):
                        weight_total += weight
            if len(dimension_ids) != len(set(dimension_ids)):
                errors.append(f"{measurement_path}.human_dimensions contains duplicate ids")
            if abs(weight_total - 1.0) > 1e-9:
                errors.append(f"{measurement_path}.human_dimensions weights must sum to 1.0")

        failures = measurement.get("hard_failures")
        if not isinstance(failures, list) or not failures:
            errors.append(f"{measurement_path}.hard_failures must contain at least one failure")
        else:
            failure_ids: list[str] = []
            for failure in failures:
                failure_path = f"{measurement_path}.hard_failures"
                _require_keys(failure, {"id", "condition"}, failure_path, errors)
                if isinstance(failure, dict):
                    failure_id = failure.get("id")
                    failure_ids.append(failure_id if isinstance(failure_id, str) else "")
                    if not isinstance(failure_id, str) or not FIELD_ID_PATTERN.fullmatch(failure_id):
                        errors.append(f"{failure_path} ids must be lowercase kebab-case strings")
                    if not _is_nonempty_string(failure.get("condition")):
                        errors.append(f"{failure_path}.condition must be non-empty")
            if len(failure_ids) != len(set(failure_ids)):
                errors.append(f"{measurement_path}.hard_failures contains duplicate ids")

    _validate_string_list(task.get("known_failure_modes"), f"{path}.known_failure_modes", errors)


def validate_ledger(ledger: Any) -> list[str]:
    """Return validation errors for a decoded task ledger."""

    errors: list[str] = _load_schema_errors(ledger)
    _require_keys(
        ledger,
        {
            "$schema",
            "schema_version",
            "benchmark_id",
            "benchmark_version",
            "status",
            "evaluation_policy",
            "profiles",
            "tasks",
        },
        "ledger",
        errors,
    )
    if not isinstance(ledger, dict):
        return errors

    fingerprint = _canonical_fingerprint(ledger)
    if fingerprint is None:
        errors.append("ledger cannot be fingerprinted as strict JSON")
    elif fingerprint != EXPECTED_LEDGER_FINGERPRINT:
        errors.append("ledger content does not match the frozen v0.2.0 contract fingerprint")
    _scan_public_text(ledger, "ledger", errors)

    if ledger.get("$schema") != EXPECTED_LEDGER_SCHEMA:
        errors.append(f"ledger.$schema must be {EXPECTED_LEDGER_SCHEMA}")
    if ledger.get("schema_version") != "0.1.0":
        errors.append("ledger.schema_version must be 0.1.0")
    if ledger.get("benchmark_id") != "agent-profile-benchmark":
        errors.append("ledger.benchmark_id must be agent-profile-benchmark")
    benchmark_version = ledger.get("benchmark_version")
    if benchmark_version != EXPECTED_BENCHMARK_VERSION:
        errors.append(f"ledger.benchmark_version must be {EXPECTED_BENCHMARK_VERSION} for the frozen registry")
    if not _is_allowed_string(ledger.get("status"), EXPECTED_STATUSES):
        errors.append("ledger.status is not an allowed value")

    policy = ledger.get("evaluation_policy")
    _require_keys(
        policy,
        {
            "execution_boundary",
            "live_web",
            "default_allowed_tools",
            "score_order",
            "human_scale",
            "provenance_values",
            "status_values",
        },
        "ledger.evaluation_policy",
        errors,
    )
    if isinstance(policy, dict):
        if policy.get("execution_boundary") != "offline_replay":
            errors.append("ledger.evaluation_policy.execution_boundary must be offline_replay")
        if policy.get("live_web") is not False:
            errors.append("ledger.evaluation_policy.live_web must be false")
        if policy.get("default_allowed_tools") != []:
            errors.append("ledger.evaluation_policy.default_allowed_tools must be empty")
        if policy.get("score_order") != ["hard_failures", "automatic_checks", "human_dimensions"]:
            errors.append("ledger.evaluation_policy.score_order must apply hard failures first")
        human_scale = policy.get("human_scale")
        _require_keys(human_scale, {"minimum", "maximum", "anchors"}, "ledger.evaluation_policy.human_scale", errors)
        if isinstance(human_scale, dict):
            minimum = human_scale.get("minimum")
            maximum = human_scale.get("maximum")
            if (
                not isinstance(minimum, int)
                or isinstance(minimum, bool)
                or not isinstance(maximum, int)
                or isinstance(maximum, bool)
                or minimum != 0
                or maximum != 4
            ):
                errors.append("ledger.evaluation_policy.human_scale must cover 0 through 4")
            anchors = human_scale.get("anchors")
            anchors_path = "ledger.evaluation_policy.human_scale.anchors"
            _require_keys(anchors, {"0", "2", "4"}, anchors_path, errors)
            if not isinstance(anchors, dict) or any(
                not _is_nonempty_string(anchors.get(key)) for key in ("0", "2", "4")
            ):
                errors.append("ledger.evaluation_policy.human_scale.anchors must define 0, 2, and 4")
        provenance_values = policy.get("provenance_values")
        if not isinstance(provenance_values, list) or any(
            not isinstance(value, str) for value in provenance_values
        ):
            errors.append("ledger.evaluation_policy.provenance_values must be a string list")
        elif provenance_values != EXPECTED_PROVENANCE_VALUES:
            errors.append("ledger.evaluation_policy.provenance_values is incomplete, duplicated, or out of order")
        status_values = policy.get("status_values")
        if not isinstance(status_values, list) or any(
            not isinstance(value, str) for value in status_values
        ):
            errors.append("ledger.evaluation_policy.status_values must be a string list")
        elif status_values != EXPECTED_STATUS_VALUES:
            errors.append("ledger.evaluation_policy.status_values is incomplete, duplicated, or out of order")

    profiles = ledger.get("profiles")
    if not isinstance(profiles, list):
        errors.append("ledger.profiles must be a list")
        profiles = []
    profile_ids: set[str] = {
        profile["id"]
        for profile in profiles
        if isinstance(profile, dict) and isinstance(profile.get("id"), str)
    }
    if profile_ids != EXPECTED_PROFILES:
        errors.append(f"ledger.profiles must contain exactly: {', '.join(sorted(EXPECTED_PROFILES))}")
    if len(profiles) != 9:
        errors.append("ledger.profiles must contain exactly 9 profiles")

    profile_task_ids: dict[str, list[str]] = {}
    for profile in profiles:
        if not isinstance(profile, dict):
            errors.append("ledger.profiles contains a non-object item")
            continue
        profile_id_value = profile.get("id")
        profile_id = profile_id_value if isinstance(profile_id_value, str) else "<missing>"
        profile_path = f"profiles[{profile_id}]"
        _require_keys(profile, {"id", "name", "remit", "task_ids", "primary_dimensions"}, profile_path, errors)
        if not isinstance(profile_id_value, str) or not FIELD_ID_PATTERN.fullmatch(profile_id_value):
            errors.append(f"{profile_path}.id must be lowercase kebab-case string")
        for field in ("name", "remit"):
            if not _is_nonempty_string(profile.get(field)):
                errors.append(f"{profile_path}.{field} must be non-empty")
        task_ids_value = profile.get("task_ids")
        if (
            not isinstance(task_ids_value, list)
            or len(task_ids_value) != 2
            or any(not _is_nonempty_string(item) for item in task_ids_value)
        ):
            errors.append(f"{profile_path}.task_ids must contain exactly two non-empty ids")
        else:
            profile_task_ids[profile_id] = list(task_ids_value)
        expected_task_ids = EXPECTED_TASKS_BY_PROFILE.get(profile_id)
        if (
            expected_task_ids is not None
            and isinstance(task_ids_value, list)
            and task_ids_value != list(expected_task_ids)
        ):
            errors.append(f"{profile_path}.task_ids must match the frozen v0.2.0 task registry")
        _validate_string_list(profile.get("primary_dimensions"), f"{profile_path}.primary_dimensions", errors)

    tasks = ledger.get("tasks")
    if not isinstance(tasks, list):
        errors.append("ledger.tasks must be a list")
        tasks = []
    if len(tasks) != 18:
        errors.append("ledger.tasks must contain exactly 18 tasks")

    task_ids: list[str] = []
    tasks_by_profile: dict[str, list[dict[str, Any]]] = {profile_id: [] for profile_id in EXPECTED_PROFILES}
    for task in tasks:
        if isinstance(task, dict):
            task_id = task.get("id")
            if isinstance(task_id, str):
                task_ids.append(task_id)
            profile_id = task.get("profile_id")
            if isinstance(profile_id, str) and profile_id in tasks_by_profile:
                tasks_by_profile[profile_id].append(task)
        _validate_task(task, profile_ids, errors)

    if len(task_ids) != len(set(task_ids)):
        errors.append("ledger.tasks contains duplicate ids")
    if set(task_ids) != EXPECTED_TASK_IDS:
        errors.append("ledger.tasks must match the frozen v0.2.0 task registry")
    for profile_id, profile_tasks in tasks_by_profile.items():
        if len(profile_tasks) != 2:
            errors.append(f"profile {profile_id} must have exactly two tasks")
        sequences = [task.get("sequence") for task in profile_tasks]
        if (
            len(sequences) != 2
            or any(not isinstance(sequence, int) or isinstance(sequence, bool) for sequence in sequences)
            or sequences.count(1) != 1
            or sequences.count(2) != 1
        ):
            errors.append(f"profile {profile_id} must have task sequences 1 and 2")
        declared = profile_task_ids.get(profile_id, [])
        actual = [task.get("id") for task in profile_tasks if isinstance(task.get("id"), str)]
        if set(declared) != set(actual):
            errors.append(f"profile {profile_id} task_ids do not match its tasks")
        expected_prefix = profile_id.upper() + "-"
        if any(not str(task.get("id", "")).startswith(expected_prefix) for task in profile_tasks):
            errors.append(f"profile {profile_id} task ids must start with {expected_prefix}")

    ledger_status = ledger.get("status")
    if isinstance(ledger_status, str) and ledger_status in EXPECTED_STATUSES:
        for task in tasks:
            if not isinstance(task, dict):
                continue
            task_id = task.get("id", "<missing>")
            task_status = task.get("status")
            if task_status != ledger_status:
                errors.append(f"tasks[{task_id}] status must match ledger readiness {ledger_status}")
            fixture_status = (
                task.get("fixture", {}).get("status")
                if isinstance(task.get("fixture"), dict)
                else None
            )
            prompt_status = (
                task.get("prompt_contract", {}).get("status")
                if isinstance(task.get("prompt_contract"), dict)
                else None
            )
            if ledger_status == "contract-draft" and (
                fixture_status != "to_be_frozen" or prompt_status != "to_be_frozen"
            ):
                errors.append(f"tasks[{task_id}] contract-draft requires unfrozen fixture and prompt")
            if ledger_status in {"fixture-ready", "benchmark-ready"} and (
                fixture_status != "frozen" or prompt_status != "frozen"
            ):
                errors.append(f"tasks[{task_id}] {ledger_status} requires frozen fixture and prompt")

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data" / "task-ledger.json",
        help="path to the JSON task ledger",
    )
    args = parser.parse_args(argv)

    try:
        ledger = json.loads(
            args.input.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except DuplicateJSONKeyError as exc:
        print(f"validation failed: unable to read input: {exc}", file=sys.stderr)
        return 1
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"validation failed: unable to read input ({type(exc).__name__})", file=sys.stderr)
        return 1

    errors = validate_ledger(ledger)
    if errors:
        print("validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print(
        f"valid benchmark ledger: {len(ledger['profiles'])} profiles, "
        f"{len(ledger['tasks'])} tasks, status={ledger['status']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
