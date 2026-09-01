"""Run one no-tool Hermes model calibration cell for any benchmark task."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

try:
    from replay_task import (
        InputError,
        _reject_duplicate_json_keys,
        compose_model_input,
        replay,
        validate_task_inputs,
    )
except ImportError:  # pragma: no cover - exercised when imported as scripts.run_task_model
    from scripts.replay_task import (
        InputError,
        _reject_duplicate_json_keys,
        compose_model_input,
        replay,
        validate_task_inputs,
    )


ROOT = Path(__file__).resolve().parents[1]
UNRESOLVED_IDENTITY_VALUES = {"none", "unresolved"}


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _as_bytes(value: bytes | str | None) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8", errors="replace")
    return b""


def _reject_nonfinite_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number {value!r} is not supported")


def build_model_prompt(prompt_path: Path, fixture_path: Path) -> str:
    """Keep both frozen inputs byte-for-byte intact while composing the request."""
    return compose_model_input(prompt_path, fixture_path).decode("utf-8")


def _validate_run_id(run_id: str) -> None:
    if not run_id or not run_id[0].isalnum() or any(
        char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-" for char in run_id
    ):
        raise InputError("run_id contains unsupported characters")


def _hermes_python() -> str:
    """Find a Python interpreter behind the first usable Hermes launcher."""
    shell_interpreters = {"bash", "csh", "fish", "ksh", "sh", "tcsh", "zsh"}
    launcher_paths: list[Path] = []
    for directory in os.get_exec_path(os.environ):
        launcher = Path(directory) / "hermes"
        if launcher.is_file() and os.access(launcher, os.X_OK):
            launcher_paths.append(launcher)
    if not launcher_paths:
        raise InputError("Hermes executable was not found on PATH")

    seen_launchers: set[Path] = set()
    for launcher in launcher_paths:
        executable = launcher.resolve()
        if executable in seen_launchers:
            continue
        seen_launchers.add(executable)
        try:
            first_line = executable.read_text(encoding="utf-8").splitlines()[0]
        except (OSError, UnicodeError, IndexError) as exc:
            raise InputError(f"unable to inspect Hermes executable ({type(exc).__name__})") from exc
        for candidate in (executable.parent / "python3", executable.parent / "python"):
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        if not first_line.startswith("#!"):
            continue
        shebang = shlex.split(first_line[2:].strip())
        if not shebang:
            continue
        interpreter = shebang[0]
        if Path(interpreter).name == "env":
            interpreter = next((item for item in shebang[1:] if not item.startswith("-")), "")
        if not interpreter or Path(interpreter).name in shell_interpreters:
            continue
        resolved_interpreter = interpreter if Path(interpreter).is_file() else shutil.which(interpreter)
        if resolved_interpreter and Path(resolved_interpreter).name.startswith("python"):
            return resolved_interpreter

    raise InputError("Hermes executable has no Python interpreter on PATH")


def _default_agent_command(profile_id: str) -> list[str]:
    """Use the benchmark adapter, never a profile alias with inherited tools."""
    return [
        _hermes_python(),
        str(ROOT / "scripts" / "hermes_no_tools.py"),
        "--profile",
        profile_id,
    ]


def _load_usage(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_nonfinite_json_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, RecursionError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _capture_git_state() -> dict[str, Any]:
    """Capture reproducibility metadata without exposing absolute paths."""
    def run_git(*arguments: str) -> dict[str, Any]:
        try:
            result = subprocess.run(
                ["git", *arguments],
                cwd=ROOT,
                capture_output=True,
                check=False,
                timeout=10,
            )
        except subprocess.TimeoutExpired:
            return {"returncode": None, "stdout": "", "stderr": "TimeoutExpired"}
        except OSError as exc:
            return {"returncode": None, "stdout": "", "stderr": type(exc).__name__}
        return {
            "returncode": result.returncode,
            "stdout": result.stdout.decode("utf-8", errors="replace"),
            "stderr": result.stderr.decode("utf-8", errors="replace"),
        }

    head = run_git("rev-parse", "HEAD")
    branch = run_git("branch", "--show-current")
    status = run_git("status", "--short", "--untracked-files=all")
    changed = run_git("diff", "--name-only", "HEAD")
    untracked = run_git("ls-files", "--others", "--exclude-standard")
    return {
        "head": head["stdout"].strip(),
        "branch": branch["stdout"].strip(),
        "status": status["stdout"].splitlines(),
        "changed_files": changed["stdout"].splitlines(),
        "untracked_files": untracked["stdout"].splitlines(),
        "git_errors": {
            name: value["stderr"]
            for name, value in (
                ("head", head),
                ("branch", branch),
                ("status", status),
                ("changed_files", changed),
                ("untracked_files", untracked),
            )
            if value["returncode"] not in (0, None) or value["stderr"]
        },
    }


def _adapter_environment() -> dict[str, str]:
    """Keep credentials while removing inherited Hermes/session code state."""
    environment = {key: value for key, value in os.environ.items()}
    environment.pop("PYTHONPATH", None)
    environment.pop("HERMES_PROFILE", None)
    for key in tuple(environment):
        if key.startswith("HERMES_SESSION_"):
            environment.pop(key, None)
    return environment


def _normalise_output_root(output_root: Path) -> Path:
    return output_root if output_root.is_absolute() else ROOT / output_root


def display_evidence_dir(path: Path) -> str:
    """Return a portable, non-sensitive CLI path for an evidence directory."""
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return "external-evidence"


def _command_parts(agent_command: str | Sequence[str]) -> list[str]:
    if isinstance(agent_command, str):
        parts = shlex.split(agent_command)
    else:
        parts = list(agent_command)
    if not parts:
        raise InputError("agent command must not be empty")
    return parts


def run_model(
    task_id: str,
    fixture_path: Path,
    prompt_path: Path,
    output_root: Path,
    run_id: str,
    model_requested: str,
    *,
    model_resolved: str | None = None,
    provider: str = "openai-codex",
    reasoning: str = "medium",
    agent_command: str | Sequence[str] | None = None,
    timeout_seconds: int = 600,
) -> dict[str, Any]:
    """Run Hermes once, preserve all process artifacts, and write a validated record."""
    if timeout_seconds <= 0:
        raise InputError("timeout must be positive")
    if not task_id or not model_requested.strip():
        raise InputError("task ID and requested model must be non-empty")
    if not provider.strip():
        raise InputError("provider must be non-empty")
    _validate_run_id(run_id)

    binding = validate_task_inputs(task_id, fixture_path, prompt_path)

    evidence_root = _normalise_output_root(output_root)
    evidence_root.mkdir(parents=True, exist_ok=True)
    evidence_dir = evidence_root / run_id
    if evidence_dir.exists() or evidence_dir.is_symlink():
        raise InputError(f"run ID {run_id!r} already exists in the evidence root")
    try:
        evidence_dir.mkdir()
    except FileExistsError as exc:
        raise InputError(f"run ID {run_id!r} already exists in the evidence root") from exc
    raw_output_path = evidence_dir / "raw-output.txt"
    stderr_path = evidence_dir / "stderr.txt"
    usage_path = evidence_dir / "usage.json"
    run_record_path = evidence_dir / "run-record.json"
    trial_metadata_path = evidence_dir / "trial.json"
    model_input_path = evidence_dir / "model-input.txt"
    git_state_path = evidence_dir / "git-state.json"

    model_input = compose_model_input(prompt_path, fixture_path)
    model_input_path.write_bytes(model_input)
    git_state = _capture_git_state()
    _write_json(git_state_path, git_state)
    git_state_failure = bool(git_state["git_errors"] or not git_state["head"])
    custom_command = agent_command is not None
    started_at = _utc_timestamp()
    started = time.perf_counter()
    command: list[str] = []
    process_returncode: int | None = None
    launch_error: str | None = None
    timed_out = False
    try:
        command = (
            _command_parts(agent_command)
            if custom_command
            else _default_agent_command(binding["profile_id"])
        ) + [
            "--ignore-rules",
            "--ignore-user-config",
            "--model",
            model_requested,
            "--provider",
            provider,
            "--reasoning",
            reasoning,
            "--usage-file",
            str(usage_path),
            "--query-file",
            str(model_input_path),
        ]
    except (InputError, OSError, TypeError, ValueError) as exc:
        stdout = b""
        stderr = f"agent launch failed ({type(exc).__name__})\n".encode("utf-8")
        launch_error = type(exc).__name__
    else:
        try:
            process = subprocess.run(
                command,
                cwd=ROOT,
                capture_output=True,
                check=False,
                timeout=timeout_seconds,
                env=None if custom_command else _adapter_environment(),
            )
            stdout = process.stdout
            stderr = process.stderr
            process_returncode = process.returncode
        except subprocess.TimeoutExpired as exc:
            stdout = _as_bytes(exc.stdout)
            stderr = _as_bytes(exc.stderr)
            timed_out = True
        except (OSError, TypeError, ValueError) as exc:
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
    usage_model = usage.get("model")
    usage_provider = usage.get("provider")
    resolved_model = (
        usage_model.strip()
        if isinstance(usage_model, str)
        and usage_model.strip()
        and usage_model.strip().casefold() not in UNRESOLVED_IDENTITY_VALUES
        else None
    )
    resolved_provider = (
        usage_provider.strip()
        if isinstance(usage_provider, str)
        and usage_provider.strip()
        and usage_provider.strip().casefold() not in UNRESOLVED_IDENTITY_VALUES
        else None
    )
    resolution_errors: list[str] = []
    if resolved_model is None:
        resolution_errors.append("usage did not report a resolved model")
    if resolved_provider is None:
        resolution_errors.append("usage did not report a resolved provider")
    if model_resolved and resolved_model and model_resolved != resolved_model:
        resolution_errors.append("usage model differs from the supplied resolved model")
    usage_failure = (
        usage.get("failed") is True
        or usage.get("partial") is True
        or usage.get("completed") is False
    )

    notes = (
        f"Hermes one-shot model calibration for {task_id}; requested no tools and no external action. "
        f"process_returncode={process_returncode!r}; timed_out={timed_out}."
    )
    if custom_command:
        notes += " Custom agent command isolation is unverified; use the benchmark Hermes adapter for scoreable evidence."
    else:
        notes += " The benchmark Hermes adapter enforced an empty tool surface, ignored user rules, and disabled fallback."
    if resolution_errors:
        notes += " model/provider resolution: " + "; ".join(resolution_errors) + "."
    if usage_failure:
        notes += " usage reported an incomplete or failed execution."
    if git_state_failure:
        notes += " Git provenance capture failed; the cell is not scoreable."
    if launch_error:
        notes += f" launch_error={launch_error}."

    process_failed = (
        timed_out
        or launch_error is not None
        or process_returncode is not None and process_returncode != 0
    )
    if timed_out:
        execution_status = "timed_out"
        failure_class = "timeout"
    elif launch_error:
        execution_status = "failed"
        failure_class = "process-launch"
    elif process_failed:
        execution_status = "failed"
        failure_class = "process-nonzero"
    elif usage_failure:
        execution_status = "failed"
        failure_class = "usage-failure"
    elif git_state_failure:
        execution_status = "blocked"
        failure_class = "git-provenance"
    elif custom_command:
        execution_status = "completed"
        failure_class = "unverified-isolation"
    elif resolution_errors:
        execution_status = "blocked"
        failure_class = "usage-resolution"
    else:
        execution_status = "completed"
        failure_class = "none"
    status_override = (
        "failed"
        if process_failed or usage_failure
        else "blocked"
        if custom_command or resolution_errors
        else None
    )
    record = replay(
        task_id,
        fixture_path,
        prompt_path,
        raw_output_path,
        "model-calibration",
        run_id,
        model_requested,
        resolved_model,
        run_record_path,
        harness="hermes-oneshot",
        raw_output_path=raw_output_path,
        started_at=started_at,
        completed_at=completed_at,
        latency_ms=latency_ms,
        usage=usage,
        notes=notes,
        model_output=True,
        status_override=status_override,
        provider_requested=provider,
        provider_resolved=resolved_provider,
        execution_status=execution_status,
        failure_class=failure_class,
    )
    _write_json(
        trial_metadata_path,
        {
            "run_id": run_id,
            "task_id": task_id,
            "command": command,
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
            "model_input": model_input_path.name,
            "git_state": git_state_path.name,
            "model_input_fingerprint": record["input_fingerprint"],
            "release_lock_fingerprint": record["release_lock_fingerprint"],
            "oracle_fingerprint": record["oracle_fingerprint"],
            "harness_fingerprint": record["harness_fingerprint"],
            "isolation": "custom-command-unverified" if custom_command else "hermes-no-tools-adapter",
            "toolsets": [],
            "memory": "disabled-by-safe-mode-and-ignore-rules" if not custom_command else "unverified",
            "fallback": "disabled-by-adapter" if not custom_command else "unverified",
            "resolution_errors": resolution_errors,
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
    parser.add_argument("--task", required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--prompt", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=Path(".model-evidence"))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--model-requested", required=True)
    parser.add_argument("--model-resolved")
    parser.add_argument("--provider", default="openai-codex")
    parser.add_argument("--reasoning", default="medium")
    parser.add_argument("--agent-command", help="Hermes profile command; defaults to the task profile alias")
    parser.add_argument("--timeout-seconds", type=int, default=600)
    args = parser.parse_args(argv)
    try:
        result = run_model(
            args.task,
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
    except (ValueError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"model calibration failed: {exc}", file=sys.stderr)
        return 2
    record = result["record"]
    print(json.dumps({
        "run_id": record["run_id"],
        "status": record["status"],
        "model_resolved": record["model_resolved"],
        "evidence_dir": display_evidence_dir(result["evidence_dir"]),
    }, sort_keys=True))
    return 0 if record["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
