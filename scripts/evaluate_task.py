"""Evaluate artifact-backed offline benchmark tasks with deterministic oracles."""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

try:
    from validate_benchmark import EXPECTED_TASK_IDS, validate_schema_instance
except ImportError:  # pragma: no cover - package-style import
    from scripts.validate_benchmark import EXPECTED_TASK_IDS, validate_schema_instance


ROOT = Path(__file__).resolve().parents[1]
ORACLE_VERSION = "task-oracle-v2"
CHECK_STATUSES = {"pass", "fail", "blocked"}


class DuplicateJSONKeyError(ValueError):
    """Raised when a JSON object repeats a member name."""


class InputError(ValueError):
    """Raised when an evaluator input cannot be read or decoded."""


def _reject_nonfinite_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number {value!r} is not supported")


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise DuplicateJSONKeyError(f"duplicate JSON object key {key!r}")
        value[key] = item
    return value


def _load_json(path: Path, label: str) -> Any:
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_nonfinite_json_constant,
        )
    except DuplicateJSONKeyError as exc:
        raise InputError(str(exc)) from exc
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise InputError(f"unable to read {label} ({type(exc).__name__})") from exc


def _get_path(value: Any, path: str) -> Any:
    if path in {"", "."}:
        return value
    current = value
    for part in path.split("."):
        if part == "":
            continue
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return None
    return current


def _flatten_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(_flatten_text(item) for item in value.values())
    if isinstance(value, list):
        return " ".join(_flatten_text(item) for item in value)
    return "" if value is None else str(value)


def _term_pattern(term: str) -> str:
    """Match a declared term without allowing it to cross word boundaries."""
    escaped = re.escape(term.casefold())
    variants = [escaped]
    if term.casefold().endswith("e"):
        stem = re.escape(term.casefold()[:-1])
        variants.extend((stem + suffix for suffix in ("s", "es", "ed", "ing", "ion", "ions")))
    else:
        stem = re.escape(term.casefold())
        variants.extend((stem + suffix for suffix in ("s", "ed", "ing")))
    return r"(?<![a-z0-9])(?:" + "|".join(variants) + r")(?![a-z0-9])"


def _term_matches(text: str, term: str) -> bool:
    return bool(re.search(_term_pattern(term), text.casefold()))


def _number_with_unit_matches(
    text: str,
    *,
    value: int,
    number_words: list[str],
    unit_terms: list[str],
) -> bool:
    number_tokens = [str(value), *number_words]
    number_pattern = "(?:" + "|".join(re.escape(item.casefold()) for item in number_tokens) + ")"
    unit_pattern = "(?:" + "|".join(_term_pattern(item) for item in unit_terms) + ")"
    pattern = (
        r"(?<![a-z0-9])" + number_pattern + r"(?![a-z0-9])"
        r"(?:\s+\w+){0,3}\s+" + unit_pattern
    )
    return bool(re.search(pattern, text.casefold()))


_NEGATION_PATTERN = re.compile(
    r"\b(?:did\s+not|didn't|do\s+not|don't|does\s+not|doesn't|"
    r"was\s+not|wasn't|were\s+not|weren't|has\s+not|hasn't|"
    r"have\s+not|haven't|no|never|without|neither|not)\b"
)


def _term_is_negated(text: str, position: int) -> bool:
    clause = re.split(r"[.!?,;:()\[\]]", text[:position])[-1]
    if re.search(
        r"(?:^|\s)(?:did\s+not|didn't|do\s+not|don't|does\s+not|doesn't|"
        r"was\s+not|wasn't|were\s+not|weren't)\s+"
        r"(?:refrain|avoid|prevent|stop)\s+from(?:\s+\w+){0,8}\s*$",
        clause,
    ):
        return False
    negations = sum(1 for _ in _NEGATION_PATTERN.finditer(clause))
    # Treat "none are/is ..." as a negator, but not an unrelated JSON value
    # such as `mutations: "none"` from an adjacent flattened field.
    if re.search(r"\bnone(?:\s+of(?:\s+\w+){0,6})?\s+(?:is|are|was|were)\b", clause):
        negations += 1
    return negations == 1


def _fact_polarity_errors(text: str, polarity: Any) -> tuple[list[str], str | None]:
    """Validate fixture-declared fact polarity without guessing task semantics."""
    if not isinstance(polarity, dict):
        return [], "fact polarity must be an object"
    expected = polarity.get("expected")
    anchors = polarity.get("anchors", [])
    if expected not in {"affirmed", "negative"}:
        return [], "fact polarity expected must be 'affirmed' or 'negative'"
    if not isinstance(anchors, list) or any(not isinstance(item, str) or not item.strip() for item in anchors):
        return [], "fact polarity anchors must be a non-empty string list"
    lowered = text.casefold()
    errors: list[str] = []
    if expected == "affirmed":
        for anchor in anchors:
            matches = list(re.finditer(_term_pattern(anchor), lowered))
            if matches and not any(not _term_is_negated(lowered, match.start()) for match in matches):
                errors.append(f"affirmed fact anchor {anchor!r} is negated")
    else:
        negative_terms = polarity.get("negative_terms", [])
        positive_terms = polarity.get("positive_terms", [])
        if (
            not isinstance(negative_terms, list)
            or any(not isinstance(item, str) or not item.strip() for item in negative_terms)
            or not isinstance(positive_terms, list)
            or any(not isinstance(item, str) or not item.strip() for item in positive_terms)
        ):
            return [], "negative fact polarity requires negative_terms and positive_terms lists"
        if not any(_term_matches(lowered, term) for term in negative_terms):
            errors.append("fact does not state the fixture-declared negative polarity")
        positive = [
            term
            for term in positive_terms
            if any(
                not _term_is_negated(lowered, match.start())
                for match in re.finditer(_term_pattern(term), lowered)
            )
        ]
        if positive:
            errors.append(f"fact states positive terms contrary to fixture polarity: {', '.join(positive)}")
    return errors, None


def _is_nonempty(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return value is not None


def _result(check_id: str, status: str, evidence: list[str]) -> dict[str, Any]:
    if status not in CHECK_STATUSES:
        raise InputError(f"unsupported check status {status!r}")
    return {"id": check_id, "status": status, "evidence": evidence}


def _blocked(check_id: str, reason: str) -> dict[str, Any]:
    return _result(check_id, "blocked", [reason])


def _required_fields(candidate: Any, oracle: dict[str, Any], schema_path: Path) -> dict[str, Any]:
    check_id = "required-fields"
    required = oracle.get("required_fields")
    if not isinstance(required, list) or any(not isinstance(item, str) for item in required):
        raise InputError("oracle required_fields must be a string list")
    if not isinstance(candidate, dict):
        return _result(check_id, "fail", ["candidate output must be a JSON object"])
    errors = [f"missing {field}" for field in required if field not in candidate]
    errors.extend(
        f"{field} must be non-empty"
        for field in required
        if field in candidate and not _is_nonempty(candidate[field])
    )
    schema = _load_json(schema_path, "task output schema")
    if not isinstance(schema, dict):
        raise InputError("task output schema must be a JSON object")
    errors.extend(
        f"output schema: {error}"
        for error in validate_schema_instance(candidate, schema)
    )
    if errors:
        return _result(check_id, "fail", errors)
    return _result(check_id, "pass", [f"all {len(required)} required fields are present and structurally valid"])


def _as_id(item: Any, id_key: str | None) -> str | None:
    if id_key is None and isinstance(item, str):
        return item if item.strip() else None
    if isinstance(item, dict) and isinstance(item.get(id_key or "id"), str):
        value = item[id_key or "id"]
        return value if value.strip() else None
    return None


def _list_ids(candidate: Any, rule: dict[str, Any]) -> tuple[str, list[str]]:
    path = rule.get("path", "")
    value = _get_path(candidate, path)
    if not isinstance(value, list):
        return "blocked", [f"{path or 'candidate'} must be a list"]
    id_key = rule.get("id_key")
    ids: list[str] = []
    errors: list[str] = []
    for index, item in enumerate(value):
        item_id = _as_id(item, id_key if isinstance(id_key, str) else None)
        if item_id is None:
            errors.append(f"{path}[{index}] does not expose a non-empty ID")
        else:
            ids.append(item_id)
    if len(ids) != len(set(ids)):
        errors.append(f"{path} contains duplicate IDs")
    required = {item for item in rule.get("required_ids", []) if isinstance(item, str)}
    allowed_raw = rule.get("allowed_ids")
    allowed = {item for item in allowed_raw if isinstance(item, str)} if isinstance(allowed_raw, list) else None
    missing = sorted(required - set(ids))
    if missing:
        errors.append(f"missing IDs: {', '.join(missing)}")
    if allowed is not None:
        unknown = sorted(set(ids) - allowed)
        if unknown:
            errors.append(f"unknown IDs: {', '.join(unknown)}")
    if rule.get("require_owner") is True:
        owners = set(rule.get("allowed_owners", []))
        for index, item in enumerate(value):
            if not isinstance(item, dict) or item.get("owner") not in owners:
                errors.append(f"{path}[{index}] has an unavailable or missing owner")
    support_map = rule.get("support_map")
    if support_map is not None:
        if not isinstance(support_map, dict):
            return "blocked", ["support_map must be an object"]
        for index, item in enumerate(value):
            item_id = _as_id(item, id_key if isinstance(id_key, str) else None)
            expected_supports = support_map.get(item_id) if item_id is not None else None
            supports = item.get("supports") if isinstance(item, dict) else None
            if not isinstance(expected_supports, list) or not isinstance(supports, list):
                errors.append(f"{path}[{index}] has an unsupported evidence relationship")
                continue
            if any(not isinstance(support, str) for support in supports):
                errors.append(f"{path}[{index}].supports must be a string list")
            elif len(supports) != len(set(supports)) or set(supports) != set(expected_supports):
                errors.append(f"{path}[{index}].supports does not match its frozen evidence relationship")
    if errors:
        return "fail", errors
    return "pass", [f"{len(ids)} IDs satisfy the declared coverage and membership rules"]


def _list_membership(candidate: Any, rule: dict[str, Any]) -> tuple[str, list[str]]:
    path = rule.get("path", "")
    value = _get_path(candidate, path)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        return "blocked", [f"{path} must be a string list"]
    if len(value) != len(set(value)):
        return "fail", [f"{path} contains duplicate values"]
    required = {item for item in rule.get("required_ids", []) if isinstance(item, str)}
    allowed = {item for item in rule.get("allowed_ids", []) if isinstance(item, str)}
    errors = []
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - allowed)
    if missing:
        errors.append(f"missing values: {', '.join(missing)}")
    if unknown:
        errors.append(f"unknown values: {', '.join(unknown)}")
    return ("fail", errors) if errors else ("pass", [f"all {len(required)} required values are present"])


def _fact_consistency(fixture: dict[str, Any], candidate: Any, rule: dict[str, Any]) -> tuple[str, list[str]]:
    facts = _get_path(candidate, rule.get("path", ""))
    contract = _get_path(fixture, rule.get("fixture_contract_path", ""))
    id_key = rule.get("id_key", "id")
    text_key = rule.get("text_key", "summary")
    if not isinstance(facts, list) or not isinstance(contract, list):
        return "blocked", ["fact consistency requires candidate facts and a fixture fact contract"]
    by_id: dict[str, Any] = {}
    for index, item in enumerate(facts):
        item_id = item.get(id_key) if isinstance(item, dict) else None
        if not isinstance(item_id, str) or not item_id.strip():
            return "blocked", [f"{rule.get('path')}[{index}] has no string {id_key}"]
        if item_id in by_id:
            return "fail", [f"{rule.get('path')} contains duplicate fact ID {item_id!r}"]
        by_id[item_id] = item
    errors: list[str] = []
    for index, expectation in enumerate(contract):
        if not isinstance(expectation, dict):
            return "blocked", [f"fixture fact contract item {index} must be an object"]
        fact_id = expectation.get("id")
        if not isinstance(fact_id, str) or not fact_id.strip():
            return "blocked", [f"fixture fact contract item {index} has no string id"]
        item = by_id.get(fact_id)
        if item is None:
            errors.append(f"missing fact {fact_id!r}")
            continue
        text = _flatten_text(item.get(text_key)).casefold()
        required_any = expectation.get("required_any", [])
        required_all = expectation.get("required_all", [])
        forbidden = expectation.get("forbidden", [])
        if not isinstance(required_any, list) or not isinstance(required_all, list) or not isinstance(forbidden, list):
            return "blocked", [f"fact expectation {fact_id!r} has invalid term lists"]
        required_terms = [term for term in required_any if isinstance(term, str) and term.strip()]
        required_all_terms = [term for term in required_all if isinstance(term, str) and term.strip()]
        forbidden_terms = [term for term in forbidden if isinstance(term, str) and term.strip()]
        if required_terms and not any(_term_matches(text, term) for term in required_terms):
            errors.append(f"fact {fact_id!r} is missing one of: {', '.join(required_terms)}")
        missing_required = [term for term in required_all_terms if not _term_matches(text, term)]
        if missing_required:
            errors.append(f"fact {fact_id!r} is missing required terms: {', '.join(missing_required)}")
        found_forbidden = [term for term in forbidden_terms if _term_matches(text, term)]
        if found_forbidden:
            errors.append(f"fact {fact_id!r} contradicts supplied evidence: {', '.join(found_forbidden)}")
        count = expectation.get("count")
        if count is not None:
            if not isinstance(count, dict):
                return "blocked", [f"fact expectation {fact_id!r} count must be an object"]
            count_value = count.get("value")
            number_words = count.get("number_words", [])
            unit_terms = count.get("unit_terms", [])
            if (
                not isinstance(count_value, int)
                or isinstance(count_value, bool)
                or not isinstance(number_words, list)
                or any(not isinstance(item, str) or not item.strip() for item in number_words)
                or not isinstance(unit_terms, list)
                or any(not isinstance(item, str) or not item.strip() for item in unit_terms)
            ):
                return "blocked", [f"fact expectation {fact_id!r} count contract is invalid"]
            if not _number_with_unit_matches(
                text,
                value=count_value,
                number_words=number_words,
                unit_terms=unit_terms,
            ):
                errors.append(f"fact {fact_id!r} does not state the required count of {count_value}")
        polarity_errors, polarity_error = (
            _fact_polarity_errors(text, expectation["polarity"])
            if "polarity" in expectation
            else ([], None)
        )
        if polarity_error is not None:
            return "blocked", [f"fact expectation {fact_id!r}: {polarity_error}"]
        errors.extend(f"fact {fact_id!r}: {error}" for error in polarity_errors)
    return ("fail", errors) if errors else ("pass", [f"{len(contract)} fact meanings match the fixture contract"])


def _defect_consistency(
    fixture: dict[str, Any], candidate: Any, rule: dict[str, Any]
) -> tuple[str, list[str]]:
    """Match review records by supplied evidence, not oracle-only labels."""

    artifacts = _get_path(fixture, rule.get("fixture_path", ""))
    findings = _get_path(candidate, rule.get("findings_path", "findings"))
    evidence = _get_path(candidate, rule.get("evidence_path", "evidence"))
    remediation = _get_path(candidate, rule.get("remediation_path", "remediation"))
    severity = _get_path(candidate, rule.get("severity_path", "severity"))
    if not all(isinstance(value, list) for value in (artifacts, findings, evidence, remediation)):
        return "blocked", ["defect consistency requires fixture and candidate item lists"]
    if not isinstance(severity, dict):
        return "blocked", ["defect consistency requires a severity object"]

    finding_id_key = rule.get("finding_id_key", "id")
    finding_summary_key = rule.get("finding_summary_key", "summary")
    evidence_id_key = rule.get("evidence_id_key", "finding_id")
    fixture_location_key = rule.get("fixture_location_key", "path")
    evidence_location_key = rule.get("evidence_location_key", "location")
    remediation_id_key = rule.get("remediation_id_key", "finding_id")
    remediation_action_key = rule.get("remediation_action_key", "action")
    if not all(
        isinstance(value, str) and value.strip()
        for value in (
            finding_id_key,
            finding_summary_key,
            evidence_id_key,
            fixture_location_key,
            evidence_location_key,
            remediation_id_key,
            remediation_action_key,
        )
    ):
        return "blocked", ["defect consistency has invalid candidate field keys"]

    fixture_by_location: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for index, artifact in enumerate(artifacts):
        if not isinstance(artifact, dict):
            return "blocked", [f"fixture artifact {index} must be an object"]
        location = artifact.get(fixture_location_key)
        expected_severity = artifact.get("severity")
        if not isinstance(location, str) or not location.strip() or not isinstance(expected_severity, str):
            return "blocked", ["fixture artifacts must expose a location and severity"]
        if location in fixture_by_location:
            return "blocked", [f"fixture contains duplicate artifact location {location!r}"]
        fixture_by_location[location] = artifact

    def item_id(item: Any, key: str, path: str) -> str | None:
        value = item.get(key) if isinstance(item, dict) else None
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{path} has no non-empty {key}")
            return None
        return value

    findings_by_id: dict[str, Any] = {}
    for index, item in enumerate(findings):
        identifier = item_id(item, finding_id_key, f"findings[{index}]")
        if identifier is not None:
            if identifier in findings_by_id:
                errors.append(f"findings contains duplicate ID {identifier!r}")
            findings_by_id[identifier] = item
            summary = item.get(finding_summary_key) if isinstance(item, dict) else None
            if not isinstance(summary, str) or not summary.strip():
                errors.append(f"findings[{index}].{finding_summary_key} must be non-empty")

    evidence_by_location: dict[str, Any] = {}
    for index, item in enumerate(evidence):
        identifier = item_id(item, evidence_id_key, f"evidence[{index}]")
        location = item.get(evidence_location_key) if isinstance(item, dict) else None
        if not isinstance(location, str) or not location.strip():
            errors.append(f"evidence[{index}] has no non-empty {evidence_location_key}")
            continue
        if location in evidence_by_location:
            errors.append(f"evidence contains duplicate location {location!r}")
        evidence_by_location[location] = (identifier, item)

    remediation_by_id: dict[str, Any] = {}
    for index, item in enumerate(remediation):
        identifier = item_id(item, remediation_id_key, f"remediation[{index}]")
        if identifier is not None:
            if identifier in remediation_by_id:
                errors.append(f"remediation contains duplicate ID {identifier!r}")
            remediation_by_id[identifier] = item
            action = item.get(remediation_action_key) if isinstance(item, dict) else None
            if not isinstance(action, str) or not action.strip():
                errors.append(f"remediation[{index}].{remediation_action_key} must be non-empty")

    expected_locations = set(fixture_by_location)
    actual_locations = set(evidence_by_location)
    if missing := sorted(expected_locations - actual_locations):
        errors.append(f"evidence is missing supplied artifact locations: {', '.join(missing)}")
    if unknown := sorted(actual_locations - expected_locations):
        errors.append(f"evidence contains locations absent from the supplied packet: {', '.join(unknown)}")

    expected_ids: set[str] = set()
    for location, artifact in fixture_by_location.items():
        evidence_record = evidence_by_location.get(location)
        if evidence_record is None:
            continue
        identifier, _ = evidence_record
        if identifier is None:
            continue
        expected_ids.add(identifier)
        if identifier not in findings_by_id:
            errors.append(f"evidence for {location!r} has no matching finding")
        if identifier not in remediation_by_id:
            errors.append(f"evidence for {location!r} has no matching remediation")
        if severity.get(identifier) != artifact.get("severity"):
            errors.append(
                f"severity for evidence {location!r} must be {artifact.get('severity')!r}, got {severity.get(identifier)!r}"
            )
        summary_terms = artifact.get("summary_terms")
        remediation_terms = artifact.get("remediation_terms")
        if (
            not isinstance(summary_terms, list)
            or any(not isinstance(term, str) or not term.strip() for term in summary_terms)
            or not isinstance(remediation_terms, list)
            or any(not isinstance(term, str) or not term.strip() for term in remediation_terms)
        ):
            return "blocked", [f"fixture artifact {location!r} lacks semantic summary/remediation terms"]
        finding = findings_by_id.get(identifier)
        remediation_item = remediation_by_id.get(identifier)
        summary = _flatten_text(finding.get(finding_summary_key) if isinstance(finding, dict) else "")
        action = _flatten_text(remediation_item.get(remediation_action_key) if isinstance(remediation_item, dict) else "")
        missing_summary = [term for term in summary_terms if not _term_matches(summary, term)]
        missing_action = [term for term in remediation_terms if not _term_matches(action, term)]
        if missing_summary:
            errors.append(
                f"finding for {location!r} does not identify supplied risk terms: {', '.join(missing_summary)}"
            )
        if missing_action:
            errors.append(
                f"remediation for {location!r} lacks bounded action terms: {', '.join(missing_action)}"
            )
    if set(findings_by_id) != expected_ids:
        errors.append("finding IDs must map one-to-one to the supplied artifact evidence")
    if set(remediation_by_id) != expected_ids:
        errors.append("remediation IDs must map one-to-one to the supplied artifact evidence")
    if set(severity) != expected_ids:
        errors.append("severity keys must map one-to-one to the supplied artifact evidence")
    if len(findings) != len(fixture_by_location):
        errors.append("findings must contain exactly one item per supplied artifact")
    if len(evidence) != len(fixture_by_location):
        errors.append("evidence must contain exactly one item per supplied artifact")
    if len(remediation) != len(fixture_by_location):
        errors.append("remediation must contain exactly one item per supplied artifact")
    return ("fail", errors) if errors else ("pass", [f"{len(expected_ids)} supplied defects have consistent local labels"])


def _allocation_reconciliation(
    fixture: dict[str, Any],
    candidate: Any,
    rule: dict[str, Any],
) -> tuple[str, list[str]]:
    allocation = _get_path(candidate, rule.get("allocation_path", ""))
    positions = _get_path(fixture, rule.get("fixture_path", ""))
    if not isinstance(allocation, list) or not isinstance(positions, list):
        return "blocked", ["allocation reconciliation requires allocation and position arrays"]
    id_key = rule.get("id_key", "id")
    share_key = rule.get("share_key", "share")
    value_key = rule.get("value_key", "value")
    position_values: dict[str, float | int] = {}
    for position in positions:
        if not isinstance(position, dict):
            return "blocked", ["fixture positions must be objects"]
        item_id = position.get(id_key)
        value = position.get(value_key)
        if not isinstance(item_id, str) or not item_id.strip() or not isinstance(value, (int, float)) or isinstance(value, bool):
            return "blocked", ["fixture positions must expose string IDs and numeric values"]
        position_values[item_id] = value
    total = sum(position_values.values())
    if total <= 0:
        return "blocked", ["fixture position total must be positive"]
    seen: set[str] = set()
    errors: list[str] = []
    share_total = 0.0
    for index, item in enumerate(allocation):
        if not isinstance(item, dict):
            errors.append(f"allocation[{index}] must be an object")
            continue
        item_id = item.get(id_key)
        share = item.get(share_key)
        if not isinstance(item_id, str) or not item_id.strip() or not isinstance(share, (int, float)) or isinstance(share, bool):
            errors.append(f"allocation[{index}] must expose a string ID and numeric share")
            continue
        if item_id in seen:
            errors.append(f"allocation contains duplicate ID {item_id!r}")
        seen.add(item_id)
        expected_value = position_values.get(item_id)
        if expected_value is None:
            errors.append(f"allocation contains unknown ID {item_id!r}")
            continue
        expected_share = expected_value / total
        if abs(share - expected_share) > 1e-9:
            errors.append(
                f"allocation {item_id!r} share {share!r} does not match fixture share {expected_share:.6f}"
            )
        share_total += share
    missing = sorted(set(position_values) - seen)
    if missing:
        errors.append(f"allocation is missing IDs: {', '.join(missing)}")
    if abs(share_total - 1.0) > 1e-9:
        errors.append(f"allocation shares sum to {share_total:.6f}, not 1.0")
    return ("fail", errors) if errors else ("pass", ["allocation shares reconcile to the fixture positions"])


def _object_values(fixture: dict[str, Any], candidate: Any, rule: dict[str, Any]) -> tuple[str, list[str]]:
    path = rule.get("path", "")
    value = _get_path(candidate, path)
    expected = rule.get("expected")
    if not isinstance(value, dict) or not isinstance(expected, dict):
        return "blocked", [f"{path or 'candidate'} and expected values must be objects"]
    errors = [f"{path}.{key} expected {expected_value!r}, got {value.get(key)!r}" for key, expected_value in expected.items() if value.get(key) != expected_value]
    allocation_rule = rule.get("allocation_reconciliation")
    if allocation_rule is not None:
        if not isinstance(allocation_rule, dict):
            return "blocked", ["allocation_reconciliation must be an object"]
        allocation_status, allocation_evidence = _allocation_reconciliation(fixture, candidate, allocation_rule)
        if allocation_status == "blocked":
            return "blocked", allocation_evidence
        if allocation_status == "fail":
            errors.extend(f"allocation: {item}" for item in allocation_evidence)
    return ("fail", errors) if errors else ("pass", [f"all {len(expected)} expected values match"])


def _text_contains(fixture: dict[str, Any], candidate: Any, rule: dict[str, Any]) -> tuple[str, list[str]]:
    paths = rule.get("paths")
    terms = rule.get("terms")
    if not isinstance(paths, list) or not isinstance(terms, list):
        return "blocked", ["text rule requires paths and terms lists"]
    text = " ".join(_flatten_text(_get_path(candidate, path)) for path in paths if isinstance(path, str))
    lowered = text.casefold()
    equivalent_terms = rule.get("equivalent_terms", {})
    if equivalent_terms is not None and not isinstance(equivalent_terms, dict):
        return "blocked", ["text rule equivalent_terms must be an object"]
    missing = []
    for term in terms:
        if not isinstance(term, str):
            continue
        alternatives = [term]
        declared = equivalent_terms.get(term) if isinstance(equivalent_terms, dict) else None
        if isinstance(declared, list):
            alternatives.extend(item for item in declared if isinstance(item, str))
        if not any(_term_matches(lowered, alternative) for alternative in alternatives):
            missing.append(term)
    errors = [f"missing required text: {', '.join(missing)}"] if missing else []
    fixture_requirements = rule.get("fixture_requirements", [])
    if not isinstance(fixture_requirements, list):
        return "blocked", ["text rule fixture_requirements must be a list"]
    for requirement in fixture_requirements:
        if not isinstance(requirement, dict):
            return "blocked", ["text rule fixture requirement must be an object"]
        fixture_path = requirement.get("fixture_path")
        required_any = requirement.get("required_any")
        required_all = requirement.get("required_all", [])
        if (
            not isinstance(fixture_path, str)
            or not isinstance(required_any, list)
            or not required_any
            or not isinstance(required_all, list)
        ):
            return "blocked", ["text rule fixture requirement is malformed"]
        fixture_text = _flatten_text(_get_path(fixture, fixture_path))
        alternatives = [term for term in required_any if isinstance(term, str) and term.strip()]
        if not alternatives:
            return "blocked", ["text rule fixture requirement has no terms"]
        required_terms = [term for term in required_all if isinstance(term, str) and term.strip()]
        if not any(_term_matches(fixture_text, term) for term in alternatives):
            return "blocked", [f"fixture evidence at {fixture_path!r} has no declared required terms"]
        if required_terms and any(not _term_matches(fixture_text, term) for term in required_terms):
            return "blocked", [f"fixture evidence at {fixture_path!r} is missing a declared required term"]
        if required_terms and any(not _term_matches(text, term) for term in required_terms):
            errors.append(
                f"candidate does not preserve all fixture evidence at {fixture_path!r}: {', '.join(required_terms)}"
            )
        elif not any(_term_matches(text, term) for term in alternatives):
            errors.append(
                f"candidate does not preserve fixture evidence at {fixture_path!r}: {', '.join(alternatives)}"
            )
    return ("fail", errors) if errors else ("pass", [f"all {len(terms)} required text anchors are present"])


def _strategy_recommendation(candidate: Any, rule: dict[str, Any]) -> tuple[str, list[str]]:
    path = rule.get("path", "")
    value = _get_path(candidate, path)
    required = rule.get("required_strategy")
    forbidden = rule.get("forbidden_strategy")
    if not isinstance(value, str) or not isinstance(required, str) or not required.strip():
        return "blocked", ["strategy recommendation requires a string path and required strategy"]
    text = value.casefold()

    def is_selected(strategy: str) -> bool:
        escaped = re.escape(strategy.casefold())
        patterns = (
            rf"\b(?:prefer|recommend|recommended|choose|select|use|pick)\s+(?:the\s+)?{escaped}\b",
            rf"\b(?:my|the)\s+recommendation\s+is\s+(?:the\s+)?{escaped}\b",
            rf"\b{escaped}\s+is\s+(?:my|the)\s+recommendation\b",
        )
        return any(re.search(pattern, text) for pattern in patterns)

    errors: list[str] = []
    if not is_selected(required):
        errors.append(f"recommendation does not select {required!r}")
    if isinstance(forbidden, str) and is_selected(forbidden):
        errors.append(f"recommendation selects forbidden strategy {forbidden!r}")
    return ("fail", errors) if errors else ("pass", [f"recommendation selects the required {required!r} strategy"])


def _text_forbidden(candidate: Any, rule: dict[str, Any]) -> tuple[str, list[str]]:
    paths = rule.get("paths")
    terms = rule.get("terms")
    if not isinstance(paths, list) or not isinstance(terms, list):
        return "blocked", ["text rule requires paths and terms lists"]
    text = " ".join(_flatten_text(_get_path(candidate, path)) for path in paths if isinstance(path, str))
    lowered = text.casefold()
    found = []

    for term in terms:
        if not isinstance(term, str) or not term.strip():
            continue
        pattern = _term_pattern(term)
        start = 0
        while True:
            match = re.search(pattern, lowered[start:])
            if match is None:
                break
            position = start + match.start()
            if not _term_is_negated(lowered, position):
                found.append(term)
                break
            start = position + max(1, match.end() - match.start())
    return ("fail", [f"forbidden text found: {', '.join(found)}"]) if found else ("pass", ["no forbidden text was found"])


def _number_bound(candidate: Any, rule: dict[str, Any], minimum: bool) -> tuple[str, list[str]]:
    path = rule.get("path", "")
    value = _get_path(candidate, path)
    bound_key = "minimum" if minimum else "maximum"
    bound = rule.get(bound_key)
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not isinstance(bound, (int, float)):
        return "blocked", [f"{path} and {bound_key} must be numeric"]
    valid = value >= bound if minimum else value <= bound
    if valid:
        return "pass", [f"{path}={value} satisfies {bound_key}={bound}"]
    return "fail", [f"{path}={value} violates {bound_key}={bound}"]


def _list_number_bound(candidate: Any, rule: dict[str, Any], minimum: bool) -> tuple[str, list[str]]:
    path = rule.get("path", "")
    values = _get_path(candidate, path)
    value_key = rule.get("value_key")
    bound_key = "minimum" if minimum else "maximum"
    bound = rule.get(bound_key)
    if not isinstance(values, list) or not isinstance(value_key, str) or not isinstance(bound, (int, float)) or isinstance(bound, bool):
        return "blocked", [f"{path} and {bound_key} must describe a numeric item list"]
    errors: list[str] = []
    for index, item in enumerate(values):
        value = item.get(value_key) if isinstance(item, dict) else None
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return "blocked", [f"{path}[{index}].{value_key} must be numeric"]
        valid = value >= bound if minimum else value <= bound
        if not valid:
            errors.append(f"{path}[{index}].{value_key}={value} violates {bound_key}={bound}")
    return ("fail", errors) if errors else ("pass", [f"all {len(values)} {value_key} values satisfy {bound_key}={bound}"])


def _ratio_max(candidate: Any, rule: dict[str, Any]) -> tuple[str, list[str]]:
    numerator = _get_path(candidate, rule.get("numerator", ""))
    denominator = _get_path(candidate, rule.get("denominator", ""))
    maximum = rule.get("maximum")
    if not all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in (numerator, denominator, maximum)) or denominator == 0:
        return "blocked", ["ratio operands must be numeric and denominator non-zero"]
    ratio = numerator / denominator
    if ratio <= maximum:
        return "pass", [f"ratio={ratio:.6f} is at most {maximum}"]
    return "fail", [f"ratio={ratio:.6f} exceeds {maximum}"]


def _exact_list(candidate: Any, rule: dict[str, Any]) -> tuple[str, list[str]]:
    value = _get_path(candidate, rule.get("path", ""))
    expected = rule.get("expected")
    if not isinstance(value, list) or not isinstance(expected, list):
        return "blocked", ["exact-list rule requires arrays"]
    return ("pass", ["list matches the frozen expected order"]) if value == expected else ("fail", [f"expected {expected!r}, got {value!r}"])


def _list_count(candidate: Any, rule: dict[str, Any]) -> tuple[str, list[str]]:
    value = _get_path(candidate, rule.get("path", ""))
    expected = rule.get("expected")
    if not isinstance(value, list) or not isinstance(expected, int):
        return "blocked", ["list-count rule requires an array and integer"]
    return ("pass", [f"list contains exactly {expected} items"]) if len(value) == expected else ("fail", [f"expected {expected} items, got {len(value)}"])


def _sum_equals(candidate: Any, rule: dict[str, Any]) -> tuple[str, list[str]]:
    target = _get_path(candidate, rule.get("path", ""))
    items = _get_path(candidate, rule.get("items_path", ""))
    key = rule.get("value_key")
    if not isinstance(target, (int, float)) or isinstance(target, bool) or not isinstance(items, list) or not isinstance(key, str):
        return "blocked", ["sum rule requires numeric target and item list"]
    values = [item.get(key) for item in items if isinstance(item, dict)]
    if len(values) != len(items) or any(not isinstance(item, (int, float)) or isinstance(item, bool) for item in values):
        return "blocked", [f"{rule.get('items_path')} contains non-numeric {key} values"]
    total = sum(values)
    return ("pass", [f"{rule.get('path')}={target} equals item sum {total}"]) if target == total else ("fail", [f"{rule.get('path')}={target} does not equal item sum {total}"])


def _progression_limit(candidate: Any, rule: dict[str, Any]) -> tuple[str, list[str]]:
    values = _get_path(candidate, rule.get("path", ""))
    key = rule.get("value_key")
    maximum = rule.get("maximum_increase")
    base = rule.get("base")
    if not isinstance(values, list) or not isinstance(key, str) or not isinstance(maximum, (int, float)) or not isinstance(base, (int, float)):
        return "blocked", ["progression rule has invalid parameters"]
    previous = base
    for index, item in enumerate(values):
        value = item.get(key) if isinstance(item, dict) else None
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return "blocked", [f"{rule.get('path')}[{index}] lacks a numeric {key}"]
        increase = (value - previous) / previous if previous else 0
        if increase > maximum:
            return "fail", [f"{rule.get('path')}[{index}] increases by {increase:.4f}, above {maximum}"]
        previous = value
    return "pass", [f"all progression steps stay within {maximum:.0%} of the prior volume"]


def _selected_catalogue_properties(fixture: dict[str, Any], candidate: Any, rule: dict[str, Any]) -> tuple[str, list[str]]:
    selected = _get_path(candidate, rule.get("path", ""))
    catalogue = _get_path(fixture, rule.get("fixture_path", ""))
    if not isinstance(selected, list) or not isinstance(catalogue, list):
        return "blocked", ["catalogue property rule requires arrays"]
    id_key = rule.get("id_key", "id")
    property_name = rule.get("property")
    expected = rule.get("expected")
    by_id = {item.get(id_key): item for item in catalogue if isinstance(item, dict)}
    errors = []
    for item_id in selected:
        if not isinstance(item_id, str):
            errors.append(f"selected catalogue ID {item_id!r} is not a string")
            continue
        item = by_id.get(item_id)
        if item is None:
            errors.append(f"unknown catalogue item {item_id!r}")
        elif item.get(property_name) != expected:
            errors.append(f"catalogue item {item_id!r} has {property_name}={item.get(property_name)!r}")
    return ("fail", errors) if errors else ("pass", ["all selected catalogue items satisfy the property constraint"])


def _budget_from_catalogue(fixture: dict[str, Any], candidate: Any, rule: dict[str, Any]) -> tuple[str, list[str]]:
    selected = _get_path(candidate, rule.get("selected_path", ""))
    catalogue = _get_path(fixture, rule.get("fixture_path", ""))
    if not isinstance(selected, list) or not isinstance(catalogue, list):
        return "blocked", ["budget rule requires selected IDs and catalogue"]
    id_key = rule.get("id_key", "id")
    by_id = {item.get(id_key): item for item in catalogue if isinstance(item, dict)}
    chosen: list[dict[str, Any]] = []
    for item_id in selected:
        if not isinstance(item_id, str) or item_id not in by_id:
            return "fail", ["selected IDs include an unknown or non-string catalogue item"]
        item = by_id[item_id]
        if not isinstance(item, dict):
            return "blocked", [f"catalogue item {item_id!r} is malformed"]
        chosen.append(item)
    weight_key = rule.get("weight_key")
    cost_key = rule.get("cost_key")
    weight_max = rule.get("weight_max")
    cost_max = rule.get("cost_max")
    if (
        not isinstance(weight_key, str)
        or not isinstance(cost_key, str)
        or not isinstance(weight_max, (int, float))
        or isinstance(weight_max, bool)
        or not isinstance(cost_max, (int, float))
        or isinstance(cost_max, bool)
    ):
        return "blocked", ["budget rule has invalid catalogue keys or numeric limits"]
    try:
        weight_values = [item[weight_key] for item in chosen]
        cost_values = [item[cost_key] for item in chosen]
    except KeyError:
        return "blocked", ["catalogue items lack the declared budget fields"]
    if any(
        not isinstance(value, (int, float)) or isinstance(value, bool)
        for value in (*weight_values, *cost_values)
    ):
        return "blocked", ["catalogue budget fields must be numeric"]
    weight = sum(weight_values)
    cost = sum(cost_values)
    errors = []
    if weight > weight_max:
        errors.append(f"catalogue weight {weight} exceeds {weight_max}")
    if cost > cost_max:
        errors.append(f"catalogue cost {cost} exceeds {cost_max}")
    reported_totals = rule.get("reported_totals")
    if reported_totals is not None:
        if not isinstance(reported_totals, dict):
            return "blocked", ["reported_totals must be an object"]
        for label, expected_total in (("weight", weight), ("cost", cost)):
            report = reported_totals.get(label)
            if not isinstance(report, dict) or not isinstance(report.get("path"), str):
                return "blocked", [f"reported_totals lacks a {label} path"]
            actual_total = _get_path(candidate, report["path"])
            if not isinstance(actual_total, (int, float)) or isinstance(actual_total, bool):
                return "blocked", [f"{report['path']} must be numeric"]
            if actual_total != expected_total:
                errors.append(
                    f"{report['path']}={actual_total} does not match catalogue {label} total {expected_total}"
                )
    return ("fail", errors) if errors else ("pass", [f"catalogue totals are {weight} g and AUD {cost}"])


def _source_mapping(candidate: Any, rule: dict[str, Any]) -> tuple[str, list[str]]:
    claims = _get_path(candidate, rule.get("path", ""))
    links = _get_path(candidate, rule.get("source_path", ""))
    if not isinstance(claims, list) or not isinstance(links, list):
        return "blocked", ["source mapping requires claim and link arrays"]
    claim_key = str(rule.get("claim_key", "claim_id"))
    source_key = str(rule.get("source_key", "source_id"))
    claim_ids: set[str] = set()
    link_by_claim: dict[str, Any] = {}
    errors: list[str] = []
    expected_mapping = rule.get("expected_mapping")
    if expected_mapping is not None and not isinstance(expected_mapping, dict):
        return "blocked", ["expected_mapping must be an object"]
    for index, item in enumerate(claims):
        claim_id = item.get(claim_key) if isinstance(item, dict) else None
        if not isinstance(claim_id, str) or not claim_id.strip():
            errors.append(f"{rule.get('path')}[{index}] has no string {claim_key}")
            continue
        claim_ids.add(claim_id)
    for index, item in enumerate(links):
        claim_id = item.get(claim_key) if isinstance(item, dict) else None
        if not isinstance(claim_id, str) or not claim_id.strip():
            errors.append(f"{rule.get('source_path')}[{index}] has no string {claim_key}")
            continue
        if claim_id in link_by_claim:
            errors.append(f"{rule.get('source_path')} contains duplicate claim ID {claim_id!r}")
        link_by_claim[claim_id] = item.get(source_key)
    required = {item for item in rule.get("required_ids", []) if isinstance(item, str)}
    if missing := sorted(required - claim_ids):
        errors.append(f"missing claims: {', '.join(missing)}")
    if missing := sorted(required - set(link_by_claim)):
        errors.append(f"missing source links: {', '.join(missing)}")
    for claim in claims:
        claim_id = claim.get(claim_key) if isinstance(claim, dict) else None
        if isinstance(claim_id, str) and claim_id in link_by_claim and claim.get(source_key) != link_by_claim[claim_id]:
            errors.append(f"claim {claim_id!r} disagrees with its source link")
    if isinstance(expected_mapping, dict):
        for claim_id in required:
            expected_source = expected_mapping.get(claim_id)
            if not isinstance(expected_source, str) or not expected_source.strip():
                errors.append(f"expected source mapping is missing for claim {claim_id!r}")
            elif link_by_claim.get(claim_id) != expected_source:
                errors.append(
                    f"claim {claim_id!r} must map to frozen source {expected_source!r}"
                )
    return ("fail", errors) if errors else ("pass", [f"{len(required)} claims have matching source links"])


def _source_mapping_strict(candidate: Any, rule: dict[str, Any]) -> tuple[str, list[str]]:
    links = _get_path(candidate, rule.get("path", ""))
    allowed = set(rule.get("allowed_ids", []))
    if not isinstance(links, list):
        return "blocked", [f"{rule.get('path')} must be a list"]
    source_key = str(rule.get("source_key", "source_id"))
    source_ids: set[str] = set()
    errors: list[str] = []
    for index, item in enumerate(links):
        source_id = item.get(source_key) if isinstance(item, dict) else None
        if not isinstance(source_id, str) or not source_id.strip():
            errors.append(f"{rule.get('path')}[{index}] has no string {source_key}")
            continue
        source_ids.add(source_id)
    unknown = sorted(source_ids - allowed)
    if errors:
        return "fail", errors + ([f"unknown source IDs: {', '.join(unknown)}"] if unknown else [])
    return ("fail", [f"unknown source IDs: {', '.join(str(item) for item in unknown)}"]) if unknown else ("pass", ["all source IDs belong to the frozen source bundle"])


def _value_in(candidate: Any, rule: dict[str, Any]) -> tuple[str, list[str]]:
    path = rule.get("path", "")
    value = _get_path(candidate, path)
    allowed = rule.get("allowed")
    if not isinstance(allowed, list):
        return "blocked", ["value-in rule requires an allowed list"]
    if value in allowed:
        return "pass", [f"{path}={value!r} is an allowed value"]
    return "fail", [f"{path}={value!r} is not one of {allowed!r}"]


def _disjoint_lists(candidate: Any, rule: dict[str, Any]) -> tuple[str, list[str]]:
    paths = rule.get("paths")
    if not isinstance(paths, list):
        return "blocked", ["disjoint rule requires paths"]
    values: dict[str, set[str]] = {}
    for path in paths:
        item = _get_path(candidate, path)
        if not isinstance(item, list) or any(not isinstance(value, str) for value in item):
            return "blocked", [f"{path} must be a string list"]
        values[path] = {value.casefold() for value in item}
    errors = []
    paths_list = list(values)
    for index, left in enumerate(paths_list):
        for right in paths_list[index + 1 :]:
            overlap = sorted(values[left] & values[right])
            if overlap:
                errors.append(f"{left} overlaps {right}: {', '.join(overlap)}")
    return ("fail", errors) if errors else ("pass", ["priority lists are disjoint"])


def _ordered_list_contains(candidate: Any, rule: dict[str, Any]) -> tuple[str, list[str]]:
    value = _get_path(candidate, rule.get("path", ""))
    terms = rule.get("terms")
    if not isinstance(value, list) or not isinstance(terms, list):
        return "blocked", ["ordered-list rule requires an array and terms"]
    text = [_flatten_text(item).casefold() for item in value]
    positions = []
    for term in terms:
        matches = [index for index, item in enumerate(text) if isinstance(term, str) and term.casefold() in item]
        if not matches:
            return "fail", [f"missing ordered action {term!r}"]
        positions.append(matches[0])
    if positions != sorted(positions):
        return "fail", ["required actions are out of order"]
    return "pass", ["required actions appear in the declared safe order"]


def _uses_hmac_compare_digest(implementation: str) -> bool:
    try:
        tree = ast.parse(implementation)
    except SyntaxError:
        return False
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)) or function.name != "verify":
            continue
        for node in ast.walk(function):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "compare_digest"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "hmac"
            ):
                return True
    return False


def _kill_probe_process_group(process: subprocess.Popen[bytes]) -> None:
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
    try:
        process.kill()
    except OSError:
        pass


def _python_auth(candidate: Any, *, allow_code_execution: bool) -> tuple[str, list[str]]:
    implementation_root = _get_path(candidate, "implementation")
    implementation = implementation_root.get("auth.py") if isinstance(implementation_root, dict) else None
    if not isinstance(implementation, str):
        return "blocked", ["implementation.auth.py must be a string"]
    if not allow_code_execution:
        return "blocked", ["untrusted candidate code execution is disabled without a sandbox"]
    with tempfile.TemporaryDirectory(prefix="benchmark-auth-") as directory:
        path = Path(directory) / "auth.py"
        try:
            path.write_text(implementation, encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            return "blocked", [f"unable to prepare hidden authentication probe ({type(exc).__name__})"]
        sentinel = "__BENCHMARK_AUTH_PROBE_COMPLETE_" + secrets.token_hex(16) + "__"
        probe = (
            "import importlib.util\n"
            f"spec = importlib.util.spec_from_file_location('candidate_auth', {str(path)!r})\n"
            "module = importlib.util.module_from_spec(spec)\n"
            "assert spec.loader is not None\n"
            "spec.loader.exec_module(module)\n"
            "verify = module.verify\n"
            "assert verify('abc', 'abc') is True\n"
            "assert verify('abc', 'abd') is False\n"
            "assert verify(b'abc', 'abc') is False\n"
            f"print({sentinel!r})\n"
        )
        process = subprocess.Popen(
            [sys.executable, "-I", "-S", "-c", probe],
            cwd=directory,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"PATH": "", "PYTHONNOUSERSITE": "1"},
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            _kill_probe_process_group(process)
            stdout, stderr = process.communicate()
            return "fail", ["hidden authentication behavior probe timed out"]
        except OSError as exc:
            _kill_probe_process_group(process)
            process.communicate()
            return "blocked", [f"unable to run hidden authentication probe ({type(exc).__name__})"]
    if process.returncode == 0:
        if sentinel.encode("utf-8") not in stdout:
            return "fail", ["hidden authentication behavior probe did not reach completion"]
        if not _uses_hmac_compare_digest(implementation):
            return "fail", ["implementation does not use hmac.compare_digest"]
        return "pass", ["hidden equality, mismatch, and non-string behavior tests passed"]
    detail_text = stderr.decode("utf-8", errors="replace")
    detail = detail_text.strip().splitlines()[-1] if detail_text.strip() else "hidden behavior failed"
    return "fail", [detail]


def _run_check(
    fixture: dict[str, Any],
    candidate: Any,
    rule: dict[str, Any],
    *,
    allow_code_execution: bool,
) -> tuple[str, list[str]]:
    kind = rule.get("kind")
    if kind == "list_ids": return _list_ids(candidate, rule)
    if kind == "list_membership": return _list_membership(candidate, rule)
    if kind == "fact_consistency": return _fact_consistency(fixture, candidate, rule)
    if kind == "defect_consistency": return _defect_consistency(fixture, candidate, rule)
    if kind == "object_values": return _object_values(fixture, candidate, rule)
    if kind == "text_contains": return _text_contains(fixture, candidate, rule)
    if kind == "strategy_recommendation": return _strategy_recommendation(candidate, rule)
    if kind == "text_forbidden": return _text_forbidden(candidate, rule)
    if kind == "number_min": return _number_bound(candidate, rule, True)
    if kind == "number_max": return _number_bound(candidate, rule, False)
    if kind == "list_number_min": return _list_number_bound(candidate, rule, True)
    if kind == "list_number_max": return _list_number_bound(candidate, rule, False)
    if kind == "ratio_max": return _ratio_max(candidate, rule)
    if kind == "exact_list": return _exact_list(candidate, rule)
    if kind == "list_count": return _list_count(candidate, rule)
    if kind == "sum_equals": return _sum_equals(candidate, rule)
    if kind == "progression_limit": return _progression_limit(candidate, rule)
    if kind == "selected_catalogue_properties": return _selected_catalogue_properties(fixture, candidate, rule)
    if kind == "budget_from_catalogue": return _budget_from_catalogue(fixture, candidate, rule)
    if kind == "source_mapping": return _source_mapping(candidate, rule)
    if kind == "source_mapping_strict": return _source_mapping_strict(candidate, rule)
    if kind == "value_in": return _value_in(candidate, rule)
    if kind == "disjoint_lists": return _disjoint_lists(candidate, rule)
    if kind == "ordered_list_contains": return _ordered_list_contains(candidate, rule)
    if kind == "python_auth": return _python_auth(candidate, allow_code_execution=allow_code_execution)
    raise InputError(f"unsupported oracle check kind {kind!r}")


def _hard_rule(fixture: dict[str, Any], candidate: Any, rule: dict[str, Any], checks: dict[str, dict[str, Any]]) -> tuple[bool, list[str]]:
    kind = rule.get("kind")
    if kind == "check_ref":
        check = checks.get(rule.get("check_id"))
        if check is None:
            raise InputError(f"hard rule references unknown check {rule.get('check_id')!r}")
        return check["status"] != "fail", check["evidence"]
    if kind == "forbidden_text":
        status, evidence = _text_forbidden(candidate, rule)
        return status != "fail", evidence
    if kind == "text_forbidden":
        status, evidence = _text_forbidden(candidate, rule)
        return status != "fail", evidence
    if kind == "source_mapping_strict":
        status, evidence = _source_mapping_strict(candidate, rule)
        return status != "fail", evidence
    if kind == "budget_from_catalogue":
        status, evidence = _budget_from_catalogue(fixture, candidate, rule)
        return status != "fail", evidence
    if kind == "text_contains_forbidden_choice":
        paths = rule.get("paths", [])
        terms = rule.get("terms", [])
        text = " ".join(_flatten_text(_get_path(candidate, path)) for path in paths if isinstance(path, str)).casefold()
        found = []
        for term in terms:
            if not isinstance(term, str):
                continue
            escaped = re.escape(term.casefold())
            patterns = (
                rf"\b(?:choose|recommend|recommended|select|use|pick)\s+(?:the\s+)?{escaped}\b",
                rf"\b{escaped}\s+(?:is|was)\s+(?:my|the)\s+(?:recommendation|choice|pick)\b",
                rf"\b(?:my|the)\s+(?:recommendation|choice|pick)\s+(?:is|should\s+be)\s+(?:the\s+)?{escaped}\b",
                rf"\b(?:recommendation|choice|pick)\s*[:=]\s*(?:the\s+)?{escaped}\b",
                rf"\b{escaped}\s+is\s+(?:recommended|the\s+recommendation|my\s+choice)\b",
            )
            if any(re.search(pattern, text) for pattern in patterns):
                found.append(term)
        return (not found, [f"forbidden recommendation choice: {', '.join(found)}"] if found else ["no forbidden recommendation choice was found"])
    if kind == "forbidden_order":
        value = _get_path(candidate, rule.get("path", ""))
        if not isinstance(value, list):
            return False, [f"{rule.get('path')} must be a list"]
        text = [_flatten_text(item).casefold() for item in value]
        before = rule.get("before", "").casefold()
        after = rule.get("after", "").casefold()
        before_positions = [i for i, item in enumerate(text) if before in item]
        after_positions = [i for i, item in enumerate(text) if after in item]
        unsafe = bool(before_positions) and (not after_positions or min(before_positions) < min(after_positions))
        return (not unsafe, ["unsafe remediation order detected"] if unsafe else ["no unsafe remediation order detected"])
    if kind == "list_count_max":
        value = _get_path(candidate, rule.get("path", ""))
        maximum = rule.get("maximum")
        if not isinstance(value, list) or not isinstance(maximum, int):
            return False, ["list-count hard rule has invalid input"]
        return (len(value) <= maximum, [f"{rule.get('path')} has {len(value)} items; maximum is {maximum}"])
    raise InputError(f"unsupported hard rule kind {kind!r}")


def _invalid_candidate(
    reason: str,
    oracle: dict[str, Any],
    *,
    output_parse_status: str = "invalid-json",
) -> dict[str, Any]:
    checks = [_result("required-fields", "fail", [reason])]
    checks.extend(_blocked(rule.get("id", "unknown"), "candidate output could not be decoded") for rule in oracle.get("checks", []))
    return {
        "evaluator_version": ORACLE_VERSION,
        "output_parse_status": output_parse_status,
        "status": "failed",
        "hard_failures": [
            {
                "id": "invalid-output",
                "condition": "The model response is not a valid JSON object for this task.",
                "evidence": [reason],
            }
        ],
        "automatic_checks": checks,
        "human_review": {"status": "pending", "dimensions": []},
    }


def evaluate_task(
    task_id: str,
    fixture: Any,
    candidate: Any,
    *,
    model_output: bool = True,
) -> dict[str, Any]:
    if task_id not in EXPECTED_TASK_IDS:
        raise InputError(f"unknown benchmark task ID {task_id!r}")
    if task_id == "KODY-01":
        try:
            from evaluate_kody01 import evaluate
        except ImportError:  # pragma: no cover
            from scripts.evaluate_kody01 import evaluate
        return evaluate(fixture, candidate)
    if not isinstance(fixture, dict):
        raise InputError("fixture must be a JSON object")
    if fixture.get("fixture_id") is None or fixture.get("fixture_version") is None:
        raise InputError("fixture identity is incomplete")
    oracle_path = ROOT / "oracles" / f"{task_id.lower()}.json"
    schema_path = ROOT / "schemas" / f"{task_id.lower()}-output.schema.json"
    oracle = _load_json(oracle_path, "task oracle")
    if not isinstance(oracle, dict):
        raise InputError("task oracle must be a JSON object")
    if (
        oracle.get("fixture_id") != fixture.get("fixture_id")
        or oracle.get("fixture_version") != fixture.get("fixture_version")
    ):
        raise InputError("fixture identity does not match the task oracle")
    if not isinstance(candidate, dict):
        return _invalid_candidate(
            "candidate output must be a JSON object",
            oracle,
            output_parse_status="json-value",
        )
    checks: list[dict[str, Any]] = []
    required = _required_fields(candidate, oracle, schema_path)
    checks.append(required)
    for rule in oracle.get("checks", []):
        if not isinstance(rule, dict) or not isinstance(rule.get("id"), str):
            raise InputError("oracle checks must have string IDs")
        status, evidence = _run_check(
            fixture,
            candidate,
            rule,
            allow_code_execution=not model_output,
        )
        checks.append(_result(rule["id"], status, evidence))
    checks_by_id = {check["id"]: check for check in checks}
    hard_failures: list[dict[str, Any]] = []
    for rule in oracle.get("hard_rules", []):
        if not isinstance(rule, dict) or not isinstance(rule.get("id"), str):
            raise InputError("oracle hard_rules must have string IDs")
        passes, evidence = _hard_rule(fixture, candidate, rule, checks_by_id)
        if not passes:
            hard_failures.append({'id':rule['id'],'condition':'The candidate violates a frozen task boundary.','evidence':evidence})
    statuses = {check["status"] for check in checks}
    status = "failed" if hard_failures or "fail" in statuses else "blocked" if "blocked" in statuses else "passed"
    return {
        "evaluator_version": ORACLE_VERSION,
        "task_id": task_id,
        "output_parse_status": "json-object",
        "status": status,
        "hard_failures": hard_failures,
        "automatic_checks": checks,
        "human_review": {"status": "pending", "dimensions": []},
    }


def evaluate_files(task_id: str, fixture_path: Path, candidate_path: Path, *, model_output: bool = True) -> dict[str, Any]:
    if task_id not in EXPECTED_TASK_IDS:
        raise InputError(f"unknown benchmark task ID {task_id!r}")
    fixture = _load_json(fixture_path, "fixture")
    if task_id == "KODY-01" and model_output:
        try:
            from evaluate_kody01 import evaluate_model_file
        except ImportError:  # pragma: no cover
            from scripts.evaluate_kody01 import evaluate_model_file
        return evaluate_model_file(fixture_path, candidate_path)
    try:
        candidate = _load_json(candidate_path, "candidate output")
    except InputError as exc:
        if model_output and task_id != "KODY-01":
            oracle = _load_json(ROOT / "oracles" / f"{task_id.lower()}.json", "task oracle")
            return _invalid_candidate(str(exc), oracle, output_parse_status="invalid-json")
        raise
    return evaluate_task(task_id, fixture, candidate, model_output=model_output)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--model-output", action="store_true", help="treat the candidate as untrusted model output (default)")
    mode.add_argument("--trusted-control", action="store_true", help="execute a checked-in, release-locked control")
    args = parser.parse_args(argv)
    try:
        result = evaluate_files(
            args.task,
            args.fixture,
            args.candidate,
            model_output=not args.trusted_control,
        )
    except ValueError as exc:
        print(f"evaluation failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
