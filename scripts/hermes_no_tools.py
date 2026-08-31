"""Run one benchmark prompt through Hermes with a strictly empty tool surface.

This adapter is intentionally narrower than the interactive Hermes CLI.  It
selects a profile only for credentials, ignores profile rules/configuration,
disables fallback routing, and passes an explicit empty tool selection to the
one-shot implementation.
"""

from __future__ import annotations

import argparse
import copy
import os
import sys
from pathlib import Path


EMPTY_TOOLSET_SENTINEL = ["__benchmark_empty_tool_surface__"]
VALID_REASONING_EFFORTS = {
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
    "ultra",
}


def _parse_reasoning_config(effort: str) -> dict[str, object]:
    """Convert the benchmark's explicit reasoning effort to Hermes config."""
    normalized = effort.strip().lower()
    if normalized in {"none", "false", "disabled"}:
        return {"enabled": False}
    if normalized in VALID_REASONING_EFFORTS:
        return {"enabled": True, "effort": normalized}
    raise ValueError(f"unsupported reasoning effort: {effort!r}")


def _profile_home(profile: str) -> Path:
    configured_home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")).expanduser()
    if configured_home.name != "profiles":
        root = configured_home.parent.parent if configured_home.parent.name == "profiles" else configured_home
    else:
        root = configured_home.parent
    profile_home = root / "profiles" / profile
    if not profile_home.is_dir():
        raise RuntimeError(f"Hermes profile does not exist: {profile}")
    return profile_home


def _configure_isolation(profile: str) -> None:
    os.environ["HERMES_HOME"] = str(_profile_home(profile))
    os.environ["HERMES_SAFE_MODE"] = "1"
    os.environ["HERMES_IGNORE_USER_CONFIG"] = "1"
    os.environ["HERMES_IGNORE_RULES"] = "1"
    for variable in (
        "HERMES_KANBAN_TASK",
        "HERMES_KANBAN_WORKSPACE",
        "HERMES_KANBAN_DB",
        "HERMES_KANBAN_BOARD",
    ):
        os.environ.pop(variable, None)


def _disable_agent_state(reasoning_config: dict[str, object]) -> None:
    """Force one-shot agent construction to avoid context and memory state."""
    try:
        import run_agent
    except ImportError as exc:
        raise RuntimeError("unable to install Hermes agent isolation hook") from exc
    original_agent = getattr(run_agent, "AIAgent", None)
    if not isinstance(original_agent, type):
        raise RuntimeError("Hermes run_agent.AIAgent class is unavailable")

    # Keep the original class surface intact; Hermes runtime helpers access
    # class-level markers through run_agent.AIAgent.
    class IsolatedAIAgent(original_agent):
        def __init__(self, *args, **kwargs):
            kwargs["skip_context_files"] = True
            kwargs["load_soul_identity"] = False
            kwargs["skip_memory"] = True
            kwargs["skip_background_review"] = True
            kwargs["session_db"] = None
            kwargs["reasoning_config"] = reasoning_config
            super().__init__(*args, **kwargs)

    run_agent.AIAgent = IsolatedAIAgent


def _disable_mcp_discovery() -> None:
    """Prevent one-shot startup from probing configured MCP servers."""
    try:
        from hermes_cli import mcp_startup
    except ImportError as exc:
        raise RuntimeError("unable to install Hermes MCP isolation hook") from exc
    if not callable(getattr(mcp_startup, "ensure_mcp_discovery_before_agent_build", None)):
        raise RuntimeError("Hermes MCP discovery hook is unavailable")
    mcp_startup.ensure_mcp_discovery_before_agent_build = lambda **_kwargs: None


def _install_config_isolation(model: str, provider: str, reasoning: str) -> None:
    """Replace Hermes config reads with explicit benchmark-only settings."""
    try:
        from hermes_cli import config as hermes_config
    except ImportError as exc:
        raise RuntimeError("unable to install Hermes config isolation hook") from exc
    isolated_config = {
        "model": {"default": model, "provider": provider},
        "providers": {},
        "fallback_providers": [],
        "toolsets": [],
        "agent": {"reasoning_effort": reasoning, "reasoning_overrides": {}},
        "sessions": {"write_json_snapshots": False},
        "memory": {},
    }

    def load_isolated_config() -> dict[str, object]:
        return copy.deepcopy(isolated_config)

    hermes_config.load_config = load_isolated_config
    hermes_config.load_config_readonly = load_isolated_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--reasoning", default="medium")
    parser.add_argument("--query-file", type=Path, required=True)
    parser.add_argument("--usage-file", type=Path, required=True)
    # These flags are accepted so the runner's invocation remains auditable and
    # compatible with a future adapter implementation.
    parser.add_argument("--ignore-rules", action="store_true")
    parser.add_argument("--ignore-user-config", action="store_true")
    args = parser.parse_args(argv)

    try:
        _configure_isolation(args.profile)
        prompt = args.query_file.read_text(encoding="utf-8")
        reasoning_config = _parse_reasoning_config(args.reasoning)
        _disable_agent_state(reasoning_config)
        from hermes_cli import oneshot
        _disable_mcp_discovery()
        _install_config_isolation(args.model, args.provider, args.reasoning)

        required_hooks = {
            name: getattr(oneshot, name, None)
            for name in (
                "_validate_explicit_toolsets",
                "_normalize_toolsets",
                "get_fallback_chain",
                "_create_session_db_for_oneshot",
                "run_oneshot",
            )
        }
        missing_hooks = [name for name, hook in required_hooks.items() if not callable(hook)]
        if missing_hooks:
            raise RuntimeError("Hermes one-shot hooks are unavailable: " + ", ".join(missing_hooks))
        original_validate = oneshot._validate_explicit_toolsets
        original_normalize = oneshot._normalize_toolsets
        oneshot._validate_explicit_toolsets = (
            lambda value: ([], None) if value == EMPTY_TOOLSET_SENTINEL else original_validate(value)
        )
        oneshot._normalize_toolsets = (
            lambda value: []
            if value == EMPTY_TOOLSET_SENTINEL or value == []
            else original_normalize(value)
        )
        oneshot.get_fallback_chain = lambda _config: []
        oneshot._create_session_db_for_oneshot = lambda: None
        return int(
            oneshot.run_oneshot(
                prompt,
                model=args.model,
                provider=args.provider,
                toolsets=EMPTY_TOOLSET_SENTINEL,
                usage_file=str(args.usage_file),
            )
        )
    except (OSError, UnicodeError, RuntimeError, ValueError, ImportError) as exc:
        print(f"benchmark Hermes adapter failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
