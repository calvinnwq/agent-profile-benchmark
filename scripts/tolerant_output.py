"""Conservatively recover structured objects from model response text.

The strict benchmark contract remains unchanged.
This module is only for a separately labelled diagnostic analysis.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any


PARSER_VERSION = "tolerant-json-v1"
MAX_SURROUNDING_CHARS = 240


class DuplicateJSONKeyError(ValueError):
    """Raised when a candidate JSON object repeats a member name."""


@dataclass(frozen=True)
class TolerantParseResult:
    """A lossless classification of one raw model response."""

    classification: str
    strict_contract_valid: bool
    recovered: bool
    candidate: dict[str, Any] | None
    candidate_fingerprint: str | None
    surrounding_chars: int
    reason: str

    def as_dict(self, *, include_candidate: bool = False) -> dict[str, Any]:
        """Return a serialisable interpretation without including raw response text."""
        value: dict[str, Any] = {
            "parser_version": PARSER_VERSION,
            "classification": self.classification,
            "strict_contract_valid": self.strict_contract_valid,
            "recovered": self.recovered,
            "candidate_fingerprint": self.candidate_fingerprint,
            "surrounding_chars": self.surrounding_chars,
            "reason": self.reason,
        }
        if include_candidate:
            value["candidate"] = self.candidate
        return value


_FENCE_PATTERN = re.compile(
    r"^```(?P<language>[^\r\n`]*)\r?\n(?P<body>[\s\S]*?)\r?\n```$",
    re.DOTALL,
)
_SURROUNDING_FORBIDDEN = set("{}[]`")


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise DuplicateJSONKeyError(f"duplicate JSON object key {key!r}")
        value[key] = item
    return value


def _reject_nonfinite_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number {value!r} is not supported")


def _decode_json(value: str) -> tuple[Any, int]:
    decoder = json.JSONDecoder(
        object_pairs_hook=_reject_duplicate_json_keys,
        parse_constant=_reject_nonfinite_json_constant,
    )
    return decoder.raw_decode(value)


def _fingerprint(candidate: dict[str, Any]) -> str:
    canonical = json.dumps(
        candidate,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def _result(
    classification: str,
    *,
    strict_contract_valid: bool = False,
    candidate: dict[str, Any] | None = None,
    surrounding_chars: int = 0,
    reason: str,
) -> TolerantParseResult:
    return TolerantParseResult(
        classification=classification,
        strict_contract_valid=strict_contract_valid,
        recovered=candidate is not None,
        candidate=candidate,
        candidate_fingerprint=_fingerprint(candidate) if candidate is not None else None,
        surrounding_chars=surrounding_chars,
        reason=reason,
    )


def _decode_object(value: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        candidate, end = _decode_json(value)
    except DuplicateJSONKeyError as exc:
        return None, f"{exc}"
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        return None, f"invalid JSON ({type(exc).__name__})"
    if end != len(value):
        return None, "JSON object is followed by additional JSON or non-whitespace content"
    if not isinstance(candidate, dict):
        return None, "decoded JSON value is not an object"
    return candidate, None


def _whole_value_classification(text: str) -> TolerantParseResult | None:
    stripped = text.strip()
    if not stripped:
        return _result("empty-output", reason="response is empty or whitespace-only")
    try:
        value, end = _decode_json(stripped)
    except DuplicateJSONKeyError as exc:
        return _result("duplicate-key", reason=str(exc))
    except (json.JSONDecodeError, RecursionError, ValueError):
        return None
    if end != len(stripped):
        return None
    if isinstance(value, dict):
        return _result(
            "strict-json-object",
            strict_contract_valid=True,
            candidate=value,
            reason="response is one bare JSON object after whitespace trimming",
        )
    return _result("json-value", reason="response is valid JSON but its top-level value is not an object")


def _fenced_classification(text: str) -> TolerantParseResult | None:
    stripped = text.strip()
    match = _FENCE_PATTERN.fullmatch(stripped)
    if match is None:
        return None
    language = match.group("language").strip().casefold()
    body = match.group("body").strip()
    if language not in {"", "json"}:
        return _result(
            "markdown-fence-non-json",
            reason=f"Markdown fence language {language!r} is not JSON",
        )
    candidate, error = _decode_object(body)
    if candidate is None:
        classification = "duplicate-key" if error and error.startswith("duplicate JSON object key") else "fenced-invalid-json"
        return _result(classification, reason=error or "fenced body is not a JSON object")
    return _result(
        "markdown-fenced-json",
        candidate=candidate,
        surrounding_chars=0,
        reason="one JSON object was recovered from an otherwise isolated Markdown fence",
    )


def _surrounded_classification(text: str) -> TolerantParseResult:
    stripped = text.strip()
    if "```" in stripped:
        return _result(
            "fenced-invalid-json",
            reason="response contains an incomplete or non-isolated Markdown fence",
        )
    first = stripped.find("{")
    last = stripped.rfind("}")
    if first < 0 or last < first:
        return _result("invalid-json", reason="response contains no recoverable JSON object")
    prefix = stripped[:first]
    suffix = stripped[last + 1 :]
    surrounding = len(prefix.strip()) + len(suffix.strip())
    if surrounding > MAX_SURROUNDING_CHARS:
        return _result(
            "surrounding-text-too-large",
            surrounding_chars=surrounding,
            reason=f"surrounding text exceeds the {MAX_SURROUNDING_CHARS}-character recovery limit",
        )
    if any(char in _SURROUNDING_FORBIDDEN for char in prefix + suffix):
        return _result(
            "ambiguous-json",
            surrounding_chars=surrounding,
            reason="surrounding text contains JSON-like delimiters or Markdown markers",
        )
    body = stripped[first : last + 1]
    candidate, error = _decode_object(body)
    if candidate is None:
        if error and error.startswith("duplicate JSON object key"):
            classification = "duplicate-key"
        elif error and "additional JSON" in error:
            classification = "ambiguous-json"
        else:
            classification = "invalid-json"
        return _result(
            classification,
            surrounding_chars=surrounding,
            reason=error or "surrounding text does not contain one complete JSON object",
        )
    if not prefix.strip() and not suffix.strip():
        return _result(
            "strict-json-object",
            strict_contract_valid=True,
            candidate=candidate,
            reason="response is one bare JSON object after whitespace trimming",
        )
    return _result(
        "surrounded-json",
        candidate=candidate,
        surrounding_chars=surrounding,
        reason="one complete JSON object has limited, unambiguous surrounding text",
    )


def parse_tolerant_output(raw: str | bytes) -> TolerantParseResult:
    """Classify and, only when unambiguous, recover a top-level JSON object.

    Recovery never repairs JSON syntax, drops fields, chooses between duplicate
    keys, or chooses one object from multiple JSON-like values.
    """
    if isinstance(raw, bytes):
        text = raw.decode("utf-8", errors="replace")
    elif isinstance(raw, str):
        text = raw
    else:
        raise TypeError("raw response must be str or bytes")

    whole = _whole_value_classification(text)
    if whole is not None:
        return whole
    fenced = _fenced_classification(text)
    if fenced is not None:
        return fenced
    return _surrounded_classification(text)
