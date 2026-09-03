"""Compatibility CLI for running KODY-01 through the generic model runner."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

try:
    from run_task_model import (
        InputError,
        display_evidence_dir,
        run_model as _run_model,
    )
except ImportError:  # pragma: no cover - exercised when imported as scripts.run_kody01_model
    from scripts.run_task_model import (
        InputError,
        display_evidence_dir,
        run_model as _run_model,
    )


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
    agent_command: str | Sequence[str] | None = None,
    timeout_seconds: int = 600,
) -> dict:
    """Preserve the historical function signature while using one runner."""
    return _run_model(
        "KODY-01",
        fixture_path,
        prompt_path,
        output_root,
        run_id,
        model_requested,
        model_resolved=model_resolved,
        provider=provider,
        reasoning=reasoning,
        agent_command=agent_command,
        timeout_seconds=timeout_seconds,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True, help="path to the KODY-01 fixture")
    parser.add_argument("--prompt", type=Path, required=True, help="path to the exact prompt packet")
    parser.add_argument("--output-root", type=Path, default=Path(".model-evidence/kody-01"))
    parser.add_argument("--run-id", required=True, help="stable identifier for the model cell")
    parser.add_argument("--model-requested", required=True, help="full requested model ID")
    parser.add_argument("--model-resolved", help="full resolved model ID used only for a usage cross-check")
    parser.add_argument("--provider", default="openai-codex")
    parser.add_argument("--reasoning", default="medium")
    parser.add_argument("--agent-command", help="custom command for tests; isolation is unverified")
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
    except (ValueError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"model calibration failed: {exc}", file=sys.stderr)
        return 2
    record = result["record"]
    print(
        json.dumps(
            {
                "run_id": record["run_id"],
                "status": record["status"],
                "model_resolved": record["model_resolved"],
                "evidence_dir": display_evidence_dir(result["evidence_dir"]),
            },
            sort_keys=True,
        )
    )
    return 0 if record["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
