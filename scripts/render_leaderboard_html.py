"""Render a deterministic, source-backed leaderboard report as standalone HTML."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from html import escape
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "data" / "leaderboard-policy.json"
SOURCE_REPOSITORY = "https://github.com/calvinnwq/agent-profile-benchmark"


class RenderError(ValueError):
    """Raised when leaderboard data cannot support the report contract."""


CSS = """
:root {
  color-scheme: light;
  --bg: #f4f1e8;
  --paper: #fffdf7;
  --ink: #1e2823;
  --muted: #617068;
  --line: #d9ded7;
  --accent: #176b59;
  --accent-deep: #0f4b3e;
  --accent-soft: #e3f0e9;
  --amber: #91601b;
  --amber-soft: #fff5d8;
  --red: #9b3e39;
  --red-soft: #f9e4e0;
  --shadow: 0 14px 40px rgba(30, 40, 35, .08);
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}

* { box-sizing: border-box; }

body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font-size: 16px;
  line-height: 1.6;
  overflow-wrap: anywhere;
}

body::before {
  content: "";
  display: block;
  height: 7px;
  background: var(--accent-deep);
  border-bottom: 2px solid #c88938;
}

a {
  color: var(--accent-deep);
  font-weight: 750;
  text-decoration-thickness: 1px;
  text-underline-offset: 3px;
}

a:focus-visible,
button:focus-visible {
  outline: 3px solid #c88938;
  outline-offset: 3px;
}

.wrap {
  width: min(1180px, calc(100% - 32px));
  margin: 0 auto;
}

.hero {
  border-bottom: 1px solid var(--line);
  background: #fbfaf4;
}

.hero-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.25fr) minmax(300px, .75fr);
  gap: 32px;
  align-items: end;
  padding: 52px 0 34px;
}

.eyebrow {
  margin: 0 0 12px;
  color: var(--accent-deep);
  font-size: .76rem;
  font-weight: 850;
  letter-spacing: .13em;
  text-transform: uppercase;
}

h1,
h2,
h3,
p { margin-top: 0; }

h1 {
  max-width: 850px;
  margin-bottom: 16px;
  font-size: clamp(2.4rem, 6vw, 5.4rem);
  line-height: .97;
  letter-spacing: -.045em;
}

.lede {
  max-width: 760px;
  margin-bottom: 22px;
  color: var(--muted);
  font-size: 1.1rem;
  line-height: 1.62;
}

.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.button {
  display: inline-flex;
  min-height: 40px;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--paper);
  padding: 0 14px;
  text-decoration: none;
}

.button.primary {
  border-color: var(--accent);
  background: var(--accent);
  color: #fff;
}

.stats {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.stat {
  min-height: 102px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--paper);
  padding: 16px;
  box-shadow: var(--shadow);
}

.stat strong {
  display: block;
  color: var(--accent-deep);
  font-size: 2rem;
  line-height: 1;
}

.stat span {
  display: block;
  margin-top: 8px;
  color: var(--muted);
  font-size: .9rem;
  line-height: 1.35;
}

main { padding: 32px 0 58px; }

article {
  display: grid;
  gap: 34px;
}

section {
  border-top: 1px solid var(--line);
  padding-top: 28px;
}

section:first-child {
  border-top: 0;
  padding-top: 0;
}

h2 {
  margin-bottom: 12px;
  font-size: clamp(1.6rem, 3vw, 2.4rem);
  letter-spacing: -.025em;
}

h3 {
  margin-bottom: 8px;
  font-size: 1.08rem;
}

p,
li {
  color: #38463f;
  font-size: 1rem;
  line-height: 1.68;
}

.notice {
  border: 1px solid #d8bd68;
  border-radius: 8px;
  background: var(--amber-soft);
  padding: 18px 20px;
}

.notice strong { color: #704912; }

.grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}

.card {
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--paper);
  padding: 17px;
  box-shadow: 0 8px 25px rgba(30, 40, 35, .04);
}

.card p {
  margin-bottom: 0;
  color: var(--muted);
  font-size: .95rem;
}

.table-shell {
  overflow-x: auto;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--paper);
  box-shadow: var(--shadow);
}

table {
  width: 100%;
  min-width: 900px;
  border-collapse: collapse;
}

caption {
  padding: 14px 16px;
  color: var(--muted);
  font-size: .9rem;
  text-align: left;
}

th,
td {
  border-top: 1px solid var(--line);
  padding: 13px 14px;
  text-align: left;
  vertical-align: top;
}

th {
  color: var(--muted);
  font-size: .78rem;
  letter-spacing: .04em;
  text-transform: uppercase;
}

.rank {
  color: var(--accent-deep);
  font-size: 1.25rem;
  font-weight: 850;
}

.model {
  display: block;
  color: var(--ink);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
  font-size: .9rem;
  font-weight: 800;
}

.sub {
  display: block;
  margin-top: 4px;
  color: var(--muted);
  font-size: .82rem;
}

.status {
  display: inline-flex;
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: 3px 9px;
  color: var(--muted);
  font-size: .76rem;
  font-weight: 800;
  text-transform: uppercase;
}

.status.provisional {
  border-color: #c9dfd2;
  background: var(--accent-soft);
  color: var(--accent-deep);
}

.status.confirmed {
  border-color: #b6d5c0;
  background: #d9eddd;
  color: #245a37;
}

.status.unranked,
.status.excluded {
  border-color: #e1c5c0;
  background: var(--red-soft);
  color: var(--red);
}

.bar {
  width: 130px;
  height: 8px;
  overflow: hidden;
  border-radius: 999px;
  background: #e8ebe5;
}

.bar span {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: var(--accent);
}

.metric {
  white-space: nowrap;
}

code {
  border: 1px solid var(--line);
  border-radius: 4px;
  background: #eef1eb;
  padding: 2px 5px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
  font-size: .84em;
}

.profile-list {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.profile-card {
  border-left: 4px solid var(--accent);
  border-top: 1px solid var(--line);
  border-right: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
  border-radius: 6px;
  background: var(--paper);
  padding: 14px 15px;
}

.profile-card h3 {
  margin-bottom: 5px;
  color: var(--accent-deep);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
  font-size: .9rem;
  text-transform: uppercase;
}

.profile-card p {
  margin: 0;
  color: var(--muted);
  font-size: .9rem;
  line-height: 1.48;
}

.profile-card .model { margin: 7px 0 3px; font-size: .82rem; }

.meta-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px 22px;
  margin: 0;
}

.meta-list div {
  border-top: 1px solid var(--line);
  padding-top: 9px;
}

.meta-list dt {
  color: var(--muted);
  font-size: .78rem;
  font-weight: 800;
  letter-spacing: .04em;
  text-transform: uppercase;
}

.meta-list dd {
  margin: 3px 0 0;
  color: var(--ink);
  font-size: .92rem;
}

footer {
  border-top: 1px solid var(--line);
  background: #fbfaf4;
  padding: 22px 0;
}

footer p {
  margin: 0;
  color: var(--muted);
  font-size: .9rem;
}

@media (max-width: 880px) {
  .hero-grid,
  .grid,
  .profile-list {
    grid-template-columns: 1fr;
  }

  .stats {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
}

@media (max-width: 620px) {
  .stats,
  .meta-list {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  h1 { font-size: clamp(2.35rem, 14vw, 4rem); }
  .wrap { width: min(100% - 24px, 1180px); }
  .hero-grid { padding-top: 38px; }
}

@media print {
  body { background: #fff; }
  body::before { display: none; }
  .button { display: none; }
  .table-shell { overflow: visible; box-shadow: none; }
  table { min-width: 0; }
  .card,
  .stat { box-shadow: none; }
}
"""


def _esc(value: Any) -> str:
    return escape(str(value), quote=True)


def _required_mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RenderError(f"{name} must be an object")
    return value


def _required_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise RenderError(f"{name} must be a non-empty string")
    return value


def _list(value: Any, name: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise RenderError(f"{name} must be an array of objects")
    return value


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _metric(item: dict[str, Any], name: str) -> Any:
    metrics = item.get("metrics")
    if not isinstance(metrics, dict):
        return None
    return metrics.get(name)


def _percentage(value: Any) -> str:
    if not _finite_number(value):
        return "n/a"
    return f"{float(value) * 100:.1f}%"


def _bar(value: Any) -> str:
    if not _finite_number(value):
        return '<div class="bar" aria-label="not available"><span style="width:0%"></span></div>'
    percentage = max(0.0, min(100.0, float(value) * 100))
    label = _percentage(value)
    return f'<div class="bar" aria-label="{_esc(label)}"><span style="width:{percentage:.1f}%"></span></div>'


def _latency(value: Any) -> str:
    if not _finite_number(value):
        return "n/a"
    return f"{float(value):,.0f} ms" if float(value).is_integer() else f"{float(value):,.1f} ms"


def _coverage(item: dict[str, Any]) -> str:
    coverage = item.get("coverage")
    if not isinstance(coverage, dict):
        return "n/a"
    covered = coverage.get("tasks_covered")
    total = coverage.get("tasks_total")
    replicates = coverage.get("minimum_replicates")
    if not all(isinstance(value, int) and not isinstance(value, bool) for value in (covered, total, replicates)):
        return "n/a"
    return f"{covered}/{total} tasks, {replicates} replicate" + ("s" if replicates != 1 else "")


def _status(value: Any) -> str:
    text = value if isinstance(value, str) and value in {"provisional", "confirmed", "unranked", "excluded"} else "unknown"
    return f'<span class="status {_esc(text)}">{_esc(text)}</span>'


def _reason_codes(item: dict[str, Any]) -> str:
    values = item.get("reason_codes")
    if not isinstance(values, list) or not values:
        return ""
    codes = [f"<code>{_esc(value)}</code>" for value in values]
    return "<span class=\"sub\">" + ", ".join(codes) + "</span>"


def _ranked_row(item: dict[str, Any]) -> str:
    model_id = _required_text(item.get("model_id"), "overall model_id")
    rank = item.get("rank")
    rank_text = str(rank) if isinstance(rank, int) and not isinstance(rank, bool) else "-"
    auto = _metric(item, "automatic_check_pass_rate")
    full = _metric(item, "full_contract_pass_rate")
    return """<tr>
  <td class="rank">{rank}</td>
  <td><span class="model">{model}</span>{status}<span class="sub">{coverage}</span></td>
  <td><span class="metric">{full}</span>{bar}</td>
  <td><span class="metric">{auto}</span></td>
  <td><span class="metric">{hard}</span></td>
  <td><span class="metric">{invalid}</span></td>
  <td><span class="metric">{latency}</span></td>
</tr>""".format(
        rank=_esc(rank_text),
        model=_esc(model_id),
        status=_status(item.get("status")),
        coverage=_esc(_coverage(item)),
        full=_esc(_percentage(full)),
        bar=_bar(full),
        auto=_esc(_percentage(auto)),
        hard=_esc(_percentage(_metric(item, "hard_failure_rate"))),
        invalid=_esc(_percentage(_metric(item, "invalid_output_rate"))),
        latency=_esc(_latency(_metric(item, "median_latency_ms"))),
    )


def _exception_row(item: dict[str, Any]) -> str:
    model_id = _required_text(item.get("model_id"), "exception model_id")
    return """<tr>
  <td><span class="model">{model}</span>{status}</td>
  <td>{coverage}</td>
  <td>{reasons}</td>
</tr>""".format(
        model=_esc(model_id),
        status=_status(item.get("status")),
        coverage=_esc(_coverage(item)),
        reasons=_reason_codes(item) or "<span class=\"sub\">No reason supplied</span>",
    )


def _run_exclusion_rows(models: list[dict[str, Any]]) -> str:
    rows: list[tuple[str, str, int]] = []
    for index, model in enumerate(models):
        model_id = _required_text(model.get("model_id"), f"models[{index}].model_id")
        task_cells = model.get("task_cells", [])
        if not isinstance(task_cells, list):
            raise RenderError(f"models[{index}].task_cells must be an array")
        for cell_index, cell in enumerate(task_cells):
            if not isinstance(cell, dict):
                raise RenderError(f"models[{index}].task_cells[{cell_index}] must be an object")
            excluded = cell.get("excluded_runs", 0)
            if isinstance(excluded, int) and not isinstance(excluded, bool) and excluded > 0:
                task_id = _required_text(cell.get("task_id"), f"models[{index}].task_cells[{cell_index}].task_id")
                rows.append((model_id, task_id, excluded))
    return "\n".join(
        f'<tr><td><span class="model">{_esc(model_id)}</span></td><td><code>{_esc(task_id)}</code></td><td>{excluded}</td></tr>'
        for model_id, task_id, excluded in sorted(rows)
    )


def _profile_card(profile_id: str, view: dict[str, Any]) -> str:
    ranked = _list(view.get("ranked", []), f"profiles.{profile_id}.ranked")
    unranked = _list(view.get("unranked", []), f"profiles.{profile_id}.unranked")
    excluded = _list(view.get("excluded", []), f"profiles.{profile_id}.excluded")
    if ranked:
        candidate = ranked[0]
        model_id = _required_text(candidate.get("model_id"), f"profiles.{profile_id}.ranked[0].model_id")
        summary = f"Top candidate: <span class=\"model\">{_esc(model_id)}</span>"
        detail = f"{_status(candidate.get('status'))} {_esc(_percentage(_metric(candidate, 'automatic_check_pass_rate')))} automatic checks"
    elif unranked:
        candidate = unranked[0]
        model_id = _required_text(candidate.get("model_id"), f"profiles.{profile_id}.unranked[0].model_id")
        summary = f"Held out: <span class=\"model\">{_esc(model_id)}</span>"
        detail = f"{_status(candidate.get('status'))} {_esc(_coverage(candidate))}"
    elif excluded:
        candidate = excluded[0]
        model_id = _required_text(candidate.get("model_id"), f"profiles.{profile_id}.excluded[0].model_id")
        summary = f"Excluded: <span class=\"model\">{_esc(model_id)}</span>"
        detail = _status(candidate.get("status"))
    else:
        summary = "No candidate evidence"
        detail = '<span class="status unranked">unavailable</span>'
    return f"""<div class=\"profile-card\">
  <h3>{_esc(profile_id)}</h3>
  <p>{summary}</p>
  <p>{detail}</p>
</div>"""


def _policy_value(policy: dict[str, Any] | None, path: tuple[str, ...], default: Any) -> Any:
    value: Any = policy
    for key in path:
        if not isinstance(value, dict):
            return default
        value = value.get(key)
    return value if value is not None else default


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RenderError(f"could not read JSON from {path}: {exc}") from exc
    return _required_mapping(value, str(path))


def render_html(
    leaderboard: dict[str, Any],
    *,
    source_sha256: str | None = None,
    policy: dict[str, Any] | None = None,
) -> str:
    """Return a deterministic standalone HTML report for a generated leaderboard."""
    data = _required_mapping(leaderboard, "leaderboard")
    for key in (
        "schema_version",
        "benchmark_id",
        "benchmark_version",
        "policy_id",
        "policy_version",
        "input_snapshot_id",
        "roster_snapshot_id",
        "generated_at",
        "scope",
        "aggregate",
        "overall",
        "profiles",
        "publication",
        "input",
    ):
        _required_text(data.get(key), f"leaderboard.{key}") if key not in {"aggregate", "overall", "profiles", "publication", "input"} else _required_mapping(data.get(key), f"leaderboard.{key}")
    if data["schema_version"] != "leaderboard-v1":
        raise RenderError("leaderboard.schema_version must be leaderboard-v1")
    if data["benchmark_id"] != "agent-profile-benchmark":
        raise RenderError("leaderboard.benchmark_id is not supported")
    if data["scope"] != "benchmark-specific model leaderboard and routing aid":
        raise RenderError("leaderboard.scope is not supported")

    aggregate = _required_mapping(data["aggregate"], "leaderboard.aggregate")
    overall = _required_mapping(data["overall"], "leaderboard.overall")
    publication = _required_mapping(data["publication"], "leaderboard.publication")
    input_info = _required_mapping(data["input"], "leaderboard.input")
    ranked = _list(overall.get("ranked", []), "overall.ranked")
    unranked = _list(overall.get("unranked", []), "overall.unranked")
    excluded = _list(overall.get("excluded", []), "overall.excluded")
    profiles = _required_mapping(data["profiles"], "leaderboard.profiles")
    models = _list(data.get("models", []), "leaderboard.models")

    attempts = aggregate.get("attempted_runs", "n/a")
    comparable = aggregate.get("comparable_resolved_runs", "n/a")
    ranked_count = len(ranked)
    confirmation_replicates = _policy_value(policy, ("coverage", "confirmed_min_replicates_per_task"), 3)
    if not isinstance(confirmation_replicates, int) or isinstance(confirmation_replicates, bool):
        confirmation_replicates = 3
    routing_allowed = publication.get("routing_recommendation_allowed") is True
    ranking_available = publication.get("ranking_available") is True
    route_label = "ON" if routing_allowed else "OFF"
    route_class = "confirmed" if routing_allowed else "unranked"
    human_scores_assigned = publication.get("human_scores_assigned") is True or aggregate.get("human_scores_assigned") is True
    routing_note = (
        "Routing recommendations are enabled for confirmed profiles in this snapshot."
        if routing_allowed
        else "Routing recommendations are disabled for this snapshot."
    )
    human_scores_note = (
        "Human scores are included in this snapshot."
        if human_scores_assigned
        else "Human scores are not present in this snapshot."
    )
    profile_signal_note = (
        "These profile views may inform routing recommendations for confirmed candidates in this snapshot."
        if routing_allowed
        else "These profile views show signal and gaps, but they are not routing recommendations while the confirmation gate is off."
    )
    source_label = source_sha256 or "not supplied"
    selected_count = input_info.get("selected_run_count", attempts)
    tasks_total = 0
    for item in ranked + unranked + excluded:
        coverage = item.get("coverage")
        if isinstance(coverage, dict) and isinstance(coverage.get("tasks_total"), int):
            tasks_total = max(tasks_total, coverage["tasks_total"])
    if tasks_total == 0:
        for model in models:
            task_cells = model.get("task_cells")
            if isinstance(task_cells, list):
                tasks_total = max(tasks_total, len(task_cells))

    ranked_rows = "\n".join(_ranked_row(item) for item in ranked)
    if not ranked_rows:
        ranked_rows = '<tr><td colspan="7">No complete model has enough evidence to rank.</td></tr>'
    exception_rows = "\n".join(_exception_row(item) for item in unranked + excluded)
    run_exclusion_rows = _run_exclusion_rows(models)
    run_exclusion_section = (
        f"""<h3>Run-level provider and identity exclusions</h3>
        <p>These attempts remain visible in the evidence ledger but do not contribute to comparable quality metrics.</p>
        <div class=\"table-shell\">
          <table>
            <caption>Excluded attempts by model and task</caption>
            <thead><tr><th scope=\"col\">Model</th><th scope=\"col\">Task</th><th scope=\"col\">Excluded runs</th></tr></thead>
            <tbody>{run_exclusion_rows}</tbody>
          </table>
        </div>"""
        if run_exclusion_rows
        else ""
    )
    profiles_html = "\n".join(_profile_card(profile_id, _required_mapping(view, f"profiles.{profile_id}")) for profile_id, view in sorted(profiles.items()))
    if not profiles_html:
        profiles_html = '<div class="card"><p>No profile views were generated.</p></div>'

    reason = _required_text(publication.get("reason"), "publication.reason")
    scope = _required_text(data["scope"], "leaderboard.scope")
    source_roster = _required_text(input_info.get("roster_path"), "input.roster_path")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="Source-backed Nous Portal free model leaderboard for the frozen Agent Profile Benchmark.">
  <title>Nous Portal free model leaderboard</title>
  <style>{CSS}</style>
</head>
<body>
  <header class="hero">
    <div class="wrap hero-grid">
      <div>
        <p class="eyebrow">Nous Portal / Agent Profile Benchmark</p>
        <h1>Which free models held the line?</h1>
        <p class="lede">A benchmark-specific model leaderboard and routing aid for the frozen <code>0.2.0</code> task suite. It measures contract-following across fixed agent profiles, not general intelligence.</p>
        <div class="actions">
          <a class="button primary" href="#leaderboard">Read the ranking</a>
          <a class="button" href="#method">How to read this</a>
          <a class="button" href="{_esc(SOURCE_REPOSITORY)}">Benchmark source</a>
        </div>
      </div>
      <div class="stats" aria-label="Benchmark snapshot">
        <div class="stat"><strong>{_esc(ranked_count)}</strong><span>complete models ranked</span></div>
        <div class="stat"><strong>{_esc(len(unranked))}</strong><span>models held out</span></div>
        <div class="stat"><strong>{_esc(comparable)}/{_esc(attempts)}</strong><span>comparable runs</span></div>
        <div class="stat"><strong class="status {route_class}">{_esc(route_label)}</strong><span>routing recommendations</span></div>
      </div>
    </div>
  </header>

  <main class="wrap">
    <article>
      <section>
        <aside class="notice" aria-label="Important interpretation note">
          <strong>Read this as a course scoreboard, not an IQ test.</strong>
          The benchmark is strict: a full-contract pass means the model satisfied every required contract check for a task. One sweep creates provisional evidence; confirmation requires at least {_esc(confirmation_replicates)} comparable replicates per task. {_esc(human_scores_note)} {_esc(routing_note)}
        </aside>
      </section>

      <section id="leaderboard">
        <p class="eyebrow">Overall view</p>
        <h2>{'Provisional ranking' if ranking_available else 'No ranking yet'}</h2>
        <p>The primary order is full-contract pass rate, followed by automatic-check pass rate, human quality, hard failures, invalid output, and latency. Profile views are weighted equally in the overall score.</p>
        <div class="table-shell">
          <table>
            <caption>Snapshot <code>{_esc(data['input_snapshot_id'])}</code> - {tasks_total} frozen tasks across {len(profiles)} profiles</caption>
            <thead>
              <tr><th scope="col">Rank</th><th scope="col">Model</th><th scope="col">Full contract</th><th scope="col">Automatic checks</th><th scope="col">Hard failures</th><th scope="col">Invalid output</th><th scope="col">Median latency</th></tr>
            </thead>
            <tbody>{ranked_rows}</tbody>
          </table>
        </div>
      </section>

      <section>
        <p class="eyebrow">Profile signal</p>
        <h2>Where each model looks strongest</h2>
        <p>{_esc(profile_signal_note)}</p>
        <div class="profile-list">{profiles_html}</div>
      </section>

      <section>
        <p class="eyebrow">Exceptions</p>
        <h2>What is not ranked</h2>
        <p>Incomplete and excluded evidence stays visible instead of being silently folded into a partial score.</p>
        <div class="table-shell">
          <table>
            <caption>Held-out or excluded model records</caption>
            <thead><tr><th scope="col">Model</th><th scope="col">Coverage</th><th scope="col">Reason</th></tr></thead>
            <tbody>{exception_rows or '<tr><td colspan="3">No held-out or excluded models in this snapshot.</td></tr>'}</tbody>
          </table>
        </div>
        {run_exclusion_section}
      </section>

      <section>
        <p class="eyebrow">Evidence ledger</p>
        <h2>What this snapshot contains</h2>
        <div class="grid">
          <div class="card"><h3>Attempts</h3><p>{_esc(aggregate.get('attempted_runs', 'n/a'))} attempted cells, {_esc(aggregate.get('comparable_resolved_runs', 'n/a'))} comparable after provider and identity checks.</p></div>
          <div class="card"><h3>Contract quality</h3><p>{_esc(aggregate.get('full_contract_pass_runs', 'n/a'))} full-contract passes and {_esc(aggregate.get('all_automatic_checks_pass_runs', 'n/a'))} cells passing every automatic check.</p></div>
          <div class="card"><h3>Failure visibility</h3><p>{_esc(aggregate.get('hard_failure_runs', 'n/a'))} hard-failure cells, {_esc(aggregate.get('invalid_output_runs', 'n/a'))} invalid-output cells, and {_esc(aggregate.get('process_or_timeout_failures', 'n/a'))} process or timeout failures.</p></div>
        </div>
      </section>

      <section id="method">
        <p class="eyebrow">Method</p>
        <h2>How to use this page</h2>
        <ol>
          <li>Use the overall order as a signal for this exact frozen suite and harness.</li>
          <li>Use profile cards to see where a model performed better or worse, not to infer general capability.</li>
          <li>Ignore any model marked unranked or excluded when comparing quality metrics.</li>
          <li>Do not automate routing until models are confirmed across repeated comparable runs.</li>
        </ol>
        <dl class="meta-list">
          <div><dt>Benchmark</dt><dd>{_esc(data['benchmark_id'] if isinstance(data.get('benchmark_id'), str) else 'agent-profile-benchmark')} v{_esc(data['benchmark_version'])}</dd></div>
          <div><dt>Policy</dt><dd>{_esc(data['policy_id'])} v{_esc(data['policy_version'])}</dd></div>
          <div><dt>Input snapshot</dt><dd><code>{_esc(data['input_snapshot_id'])}</code></dd></div>
          <div><dt>Roster snapshot</dt><dd><code>{_esc(data['roster_snapshot_id'])}</code></dd></div>
          <div><dt>Generated</dt><dd><code>{_esc(data['generated_at'])}</code></dd></div>
          <div><dt>Selected run records</dt><dd>{_esc(selected_count)}</dd></div>
          <div><dt>Roster source</dt><dd><code>{_esc(source_roster)}</code></dd></div>
          <div><dt>Release lock</dt><dd><code>{_esc(data.get('release_lock_fingerprint', 'not supplied'))}</code></dd></div>
          <div><dt>Source JSON SHA-256</dt><dd><code>{_esc(source_label)}</code></dd></div>
          <div><dt>Publication note</dt><dd>{_esc(reason)}</dd></div>
        </dl>
        <p><strong>Scope:</strong> {_esc(scope)}. Historical evidence remains append-only under the benchmark repository's local <code>.model-evidence/</code> roots.</p>
      </section>
    </article>
  </main>

  <footer>
    <div class="wrap"><p>Generated from a validated leaderboard artifact. This page reports evidence; it does not make a universal intelligence claim.</p></div>
  </footer>
</body>
</html>
"""


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="generated leaderboard JSON")
    parser.add_argument("--output", required=True, type=Path, help="standalone HTML output path")
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY, help="leaderboard policy JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _argument_parser().parse_args(argv)
    try:
        source_bytes = args.input.read_bytes()
        leaderboard = json.loads(source_bytes.decode("utf-8"))
        policy = _load_json(args.policy)
        html = render_html(
            _required_mapping(leaderboard, "leaderboard"),
            source_sha256=hashlib.sha256(source_bytes).hexdigest(),
            policy=policy,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(html, encoding="utf-8")
    except (OSError, UnicodeError, json.JSONDecodeError, RenderError) as exc:
        print(f"leaderboard HTML render failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"output": str(args.output), "bytes": len(html.encode("utf-8"))}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
