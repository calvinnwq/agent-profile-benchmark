"""Run one no-tool Hermes KODY-01 model calibration cell."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

try:
    from replay_kody01 import InputError, replay
except ImportError:  # pragma: no cover - exercised when imported as scripts.run_kody01_model
    from scripts.replay_kody01 import InputError, replay


ROOT = Path(__file__).resolve().parents[1]
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _as_bytes(value: bytes | str | None) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8", errors="replace")
    return b""


def build_model_prompt(prompt_path: Path, fixture_path: Path) -> str:
    """Keep the frozen prompt intact and append the frozen packet as input."""
    try:
        prompt = prompt_path.read_text(encoding="utf-8")
        fixture = fixture_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise InputError(f"unable to read model input ({type(exc).__name__})") from exc
    return prompt + "\n\nSupplied synthetic request packet (JSON):\n" + fixture


def _load_usage(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _normalise_output_root(output_root: Path) -> Path:
    return output_root if output_root.is_absolute() else ROOT / output_root


def _validate_run_id(run_id: str) -> None:
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise InputError("run ID must contain only letters, numbers, dot, underscore, or hyphen")


def _command_parts(agent_command: str | Sequence[str]) -> list[str]:
    if isinstance(agent_command, str):
        parts = shlex.split(agent_command)
    else:
        parts = list(agent_command)
    if not parts:
        raise InputError("agent command must not be empty")
    return parts


def run_model(
    fixture_path: Path,
    prompt_path: Path,
    output_root: Path,
    run_id: str,
    model_requested: str,
    *,
    model_resolved: str | None = None,
    provider: str = "openai-codex",
    reasoning: str = "medium",
    agent_command: str | Sequence[str] = "kody",
    timeout_seconds: int = 600,
) -> dict[str, Any]:
    """Run Hermes once, preserve its outputs, and write a validated record."""
    _validate_run_id(run_id)
    if timeout_seconds <= 0:
        raise InputError("timeout must be positive")
    if not model_requested.strip():
        raise InputError("requested model must be non-empty")
    if not provider.strip():
        raise InputError("provider must be non-empty")

    evidence_dir = _normalise_output_root(output_root) / run_id
    evidence_dir.mkdir(parents=True, exist_ok=True)
    raw_output_path = evidence_dir / "raw-output.txt"
    stderr_path = evidence_dir / "stderr.txt"
    usage_path = evidence_dir / "usage.json"
    run_record_path = evidence_dir / "run-record.json"
    trial_metadata_path = evidence_dir / "trial.json"

    model_prompt = build_model_prompt(prompt_path, fixture_path)
    command = _command_parts(agent_command) + [
        "--ignore-rules",
        "--in",
        str(ROOT),
        "--toolsets",
        "context_engine",
        "--model",
        model_requested,
        "--provider",
        provider,
        "--reasoning",
        reasoning,
        "--usage-file",
        str(usage_path),
        "-z",
        model_prompt,
    ]
    metadata_command = command[:-1] + ["<frozen-prompt+fixture>"]

    started_at = _utc_timestamp()
    started = time.perf_counter()
    process_returncode: int | None = None
    launch_error: str | None = None
    timed_out = False
    try:
        process = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
        stdout = process.stdout
        stderr = process.stderr
        process_returncode = process.returncode
    except subprocess.TimeoutExpired as exc:
        stdout = _as_bytes(exc.stdout)
        stderr = _as_bytes(exc.stderr)
        timed_out = True
    except OSError as exc:
        stdout = b""
        stderr = f"agent launch failed ({type(exc).__name__})\n".encode("utf-8")
        launch_error = type(exc).__name__
    completed_at = _utc_timestamp()
    latency_ms = max(0, int((time.perf_counter() - started) * 1000))

    raw_output_path.write_bytes(_as_bytes(stdout))
    stderr_path.write_bytes(_as_bytes(stderr))
    if not usage_path.exists():
        usage_path.write_text("{}\n", encoding="utf-8")
    usage = _load_usage(usage_path)
    resolved = model_resolved or (
        str(usage.get("model")) if isinstance(usage.get("model"), str) and usage.get("model") else model_requested
    )

    notes = (
        "Hermes one-shot model calibration; no tools were exposed and no external action was performed. "
        f"process_returncode={process_returncode!r}; timed_out={timed_out}."
    )
    if launch_error:
        notes += f" launch_error={launch_error}."
    record = replay(
        fixture_path,
        prompt_path,
        raw_output_path,
        "model-calibration",
        run_id,
        model_requested,
        resolved,
        run_record_path,
        harness="hermes-oneshot",
        raw_output_path=raw_output_path,
        started_at=started_at,
        completed_at=completed_at,
        latency_ms=latency_ms,
        usage=usage,
        notes=notes,
        status_override="failed" if timed_out or process_returncode != 0 else None,
    )
    _write_json(
        trial_metadata_path,
        {
            "run_id": run_id,
            "command": metadata_command,
            "provider": provider,
            "reasoning": reasoning,
            "timeout_seconds": timeout_seconds,
            "process_returncode": process_returncode,
            "timed_out": timed_out,
            "started_at": started_at,
            "completed_at": completed_at,
            "raw_output": raw_output_path.name,
            "stderr": stderr_path.name,
            "usage": usage_path.name,
            "run_record": run_record_path.name,
        },
    )
    return {
        "record": record,
        "evidence_dir": evidence_dir,
        "process_returncode": process_returncode,
        "timed_out": timed_out,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True, help="path to the KODY-01 fixture")
    parser.add_argument("--prompt", type=Path, required=True, help="path to the exact prompt packet")
    parser.add_argument("--output-root", type=Path, default=Path(".model-evidence/kody-01"))
    parser.add_argument("--run-id", required=True, help="stable identifier for the model cell")
    parser.add_argument("--model-requested", required=True, help="full requested model ID")
    parser.add_argument("--model-resolved", help="full resolved model ID; defaults to usage metadata or request")
    parser.add_argument("--provider", default="openai-codex")
    parser.add_argument("--reasoning", default="medium")
    parser.add_argument("--agent-command", default="kody", help="Hermes profile command; defaults to the Kody alias")
    parser.add_argument("--timeout-seconds", type=int, default=600)
    args = parser.parse_args(argv)
    try:
        result = run_model(
            args.fixture,
            args.prompt,
            args.output_root,
            args.run_id,
            args.model_requested,
            model_resolved=args.model_resolved,
            provider=args.provider,
            reasoning=args.reasoning,
            agent_command=args.agent_command,
            timeout_seconds=args.timeout_seconds,
        )
    except (InputError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"model calibration failed: {exc}", file=sys.stderr)
        return 2
    record = result["record"]
    print(
        json.dumps(
            {
                "run_id": record["run_id"],
                "status": record["status"],
                "model_resolved": record["model_resolved"],
                "evidence_dir": str(result["evidence_dir"]),
            },
            sort_keys=True,
        )
    )
    return 0 if record["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
