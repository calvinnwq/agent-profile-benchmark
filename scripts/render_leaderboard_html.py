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

try:
    from validate_benchmark import validate_schema_instance
except ImportError:  # pragma: no cover - package-style import
    from scripts.validate_benchmark import validate_schema_instance

try:
    from leaderboard_aggregate import validate_aggregate
except ImportError:  # pragma: no cover - package-style import
    from scripts.leaderboard_aggregate import validate_aggregate


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "data" / "leaderboard-policy.json"
OUTPUT_SCHEMA = ROOT / "schemas" / "leaderboard-output.schema.json"
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

.chart {
  margin: 0 0 16px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--paper);
  padding: 16px;
  box-shadow: var(--shadow);
}

.chart svg {
  display: block;
  width: 100%;
  height: auto;
}

.chart figcaption {
  margin-top: 8px;
  color: var(--muted);
  font-size: .86rem;
}

.chart .axis { stroke: #8d988f; stroke-width: 1; }
.chart .grid-line { stroke: #e3e7e0; stroke-width: 1; }
.chart .tick { fill: #4f5d56; font-size: 12px; }
.chart .axis-title { fill: var(--ink); font-size: 13px; font-weight: 700; }
.chart .frontier { fill: none; stroke: #b36f1c; stroke-width: 2.5; stroke-dasharray: 7 4; }
.chart .leader { stroke: #aab3ad; stroke-width: 1; }
.chart .point-label { fill: var(--ink); font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 11px; paint-order: stroke; stroke: var(--paper); stroke-width: 3px; }
.chart .point-label.unranked { fill: var(--red); }
.chart .point.confirmed { fill: var(--accent-deep); stroke: var(--accent-deep); stroke-width: 1.5; }
.chart .point.provisional { fill: var(--paper); stroke: var(--accent); stroke-width: 2.5; }
.chart .point.unranked { fill: var(--red-soft); stroke: var(--red); stroke-width: 2; }
.chart-legend { display: flex; flex-wrap: wrap; gap: 8px 18px; margin: 8px 0 0; padding: 0; list-style: none; color: var(--muted); font-size: .85rem; }
.chart .chart-legend svg { display: inline-block; width: 18px; height: 18px; vertical-align: -4px; }

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


def _nonnegative_int(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise RenderError(f"{name} must be a non-negative integer")
    return value


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise RenderError(f"duplicate JSON object key {key!r}")
        value[key] = item
    return value


def _reject_nonfinite_json_constant(value: str) -> None:
    raise RenderError(f"non-finite JSON number {value!r} is not supported")


def _list(value: Any, name: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise RenderError(f"{name} must be an array of objects")
    return value


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _reject_nonfinite_values(value: Any, name: str) -> None:
    pending: list[tuple[Any, str]] = [(value, name)]
    while pending:
        current, path = pending.pop()
        if isinstance(current, float) and not math.isfinite(current):
            raise RenderError(f"{path} contains a non-finite number")
        if isinstance(current, dict):
            pending.extend((child, f"{path}.{key}") for key, child in current.items())
        elif isinstance(current, list):
            pending.extend((child, f"{path}[{index}]") for index, child in enumerate(current))


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
  <td><span class="metric">{strict}</span></td>
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
        strict=_esc(_percentage(_metric(item, "strict_json_valid_rate"))),
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


def _run_count_rows(models: list[dict[str, Any]], count_field: str) -> str:
    rows: list[tuple[str, str, int]] = []
    for index, model in enumerate(models):
        model_id = _required_text(model.get("model_id"), f"models[{index}].model_id")
        task_cells = model.get("task_cells", [])
        if not isinstance(task_cells, list):
            raise RenderError(f"models[{index}].task_cells must be an array")
        for cell_index, cell in enumerate(task_cells):
            if not isinstance(cell, dict):
                raise RenderError(f"models[{index}].task_cells[{cell_index}] must be an object")
            count = cell.get(count_field, 0)
            if isinstance(count, int) and not isinstance(count, bool) and count > 0:
                task_id = _required_text(cell.get("task_id"), f"models[{index}].task_cells[{cell_index}].task_id")
                rows.append((model_id, task_id, count))
    return "\n".join(
        f'<tr><td><span class="model">{_esc(model_id)}</span></td><td><code>{_esc(task_id)}</code></td><td>{count}</td></tr>'
        for model_id, task_id, count in sorted(rows)
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


CHART_CAPTION = "scoped to this frozen suite, not a general ranking"
CHART_STATUSES = ("confirmed", "provisional", "unranked")
CHART_WIDTH = 960
CHART_HEIGHT = 440
CHART_MARGIN = {"left": 70, "right": 24, "top": 20, "bottom": 56}
LABEL_CHAR_WIDTH = 6.6
LABEL_HEIGHT = 13
LABEL_SUFFIX = ":free"
LABEL_OFFSETS = tuple(
    (dx, dy)
    for dy in (0, -16, 16, -32, 32, -48, 48, -64, 64)
    for dx in (12, -12, 40, -40)
)


def _round(value: float) -> float:
    return round(value + 0.0, 2)


def _fmt(value: float) -> str:
    text = f"{_round(value):.2f}".rstrip("0").rstrip(".")
    return "0" if text in {"-0", ""} else text


def chart_points(
    leaderboard: dict[str, Any],
    x_metric: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Return plottable eligible-model points for one x metric, plus omitted model IDs.

    ``x_metric`` is ``median_latency_ms`` (from overall metrics) or
    ``mean_output_tokens`` (from the model usage summary). Excluded models are
    never returned. Points without a positive x value or a pass rate are omitted
    because they cannot be placed on a log axis.
    """
    overall = _required_mapping(leaderboard.get("overall"), "leaderboard.overall")
    usage_by_model: dict[str, Any] = {}
    for model in _list(leaderboard.get("models", []), "leaderboard.models"):
        if isinstance(model.get("model_id"), str):
            usage_by_model[model["model_id"]] = model.get("usage")
    points: list[dict[str, Any]] = []
    omitted: list[str] = []
    for key in ("ranked", "unranked"):
        for row in _list(overall.get(key, []), f"leaderboard.overall.{key}"):
            status = row.get("status")
            model_id = row.get("model_id")
            if status not in CHART_STATUSES or not isinstance(model_id, str):
                continue
            y = _metric(row, "full_contract_pass_rate")
            if x_metric == "median_latency_ms":
                x = _metric(row, "median_latency_ms")
            else:
                usage = usage_by_model.get(model_id)
                x = usage.get(x_metric) if isinstance(usage, dict) else None
            if not _finite_number(x) or float(x) <= 0 or not _finite_number(y):
                omitted.append(model_id)
                continue
            points.append({"model_id": model_id, "status": status, "x": float(x), "y": float(y)})
    points.sort(key=lambda item: (item["x"], -item["y"], item["model_id"]))
    return points, sorted(omitted)


def pareto_frontier(points: list[dict[str, Any]]) -> list[str]:
    """Return model IDs on the efficiency frontier, ordered by increasing x.

    A point is on the frontier when no other ranked point has an x value at or
    below it with a strictly higher pass rate. Only ranked (provisional or
    confirmed) points are eligible: an unranked model's pass rate covers an
    incomplete task set and is not comparable, so it is plotted but never
    drawn as efficient.
    """
    frontier: list[str] = []
    best_y = -math.inf
    ranked = [point for point in points if point["status"] in {"confirmed", "provisional"}]
    for point in sorted(ranked, key=lambda item: (item["x"], -item["y"], item["model_id"])):
        if point["y"] > best_y:
            frontier.append(point["model_id"])
            best_y = point["y"]
    return frontier


def _nice_log_bounds(values: list[float]) -> tuple[float, float]:
    """Return the nearest 1-2-5 steps at or outside the data range."""
    def steps(value: float) -> list[float]:
        decade = 10 ** math.floor(math.log10(value))
        return [decade * multiple for multiple in (1, 2, 5, 10)]

    low_value, high_value = min(values), max(values)
    low = max(step for step in steps(low_value) if step <= low_value * 1.0000001)
    high = min(step for step in steps(high_value) + [10 * steps(high_value)[-1]] if step >= high_value * 0.9999999)
    if high <= low:
        high = low * 2
    return float(low), float(high)


def _linear_bounds(values: list[float]) -> tuple[float, float]:
    """Return a 0.1-aligned pass-rate range with room above and below the data."""
    low = max(0.0, math.floor((min(values) - 0.05) * 10) / 10)
    high = min(1.0, math.ceil((max(values) + 0.05) * 10) / 10)
    if high <= low:
        high = min(1.0, low + 0.1)
        low = high - 0.1
    return round(low, 1), round(high, 1)


def _log_ticks(low: float, high: float) -> list[float]:
    ticks: list[float] = []
    decade = 10 ** math.floor(math.log10(low))
    while decade <= high * 1.0000001:
        for multiple in (1, 2, 5):
            value = decade * multiple
            if low * 0.9999999 <= value <= high * 1.0000001:
                ticks.append(value)
        decade *= 10
    return ticks


def _tick_label(value: float, unit: str) -> str:
    if unit == "ms":
        if value >= 1000:
            return f"{value / 1000:g}s"
        return f"{value:g}ms"
    if value >= 1000:
        return f"{value / 1000:g}k"
    return f"{value:g}"


def _value_label(value: float, unit: str) -> str:
    if unit == "ms":
        return _latency(value)
    return f"{value:,.0f} tokens"


def layout_chart(points: list[dict[str, Any]]) -> dict[str, Any]:
    """Map points to deterministic SVG coordinates on a log-x, linear-y plane."""
    plot_left = CHART_MARGIN["left"]
    plot_right = CHART_WIDTH - CHART_MARGIN["right"]
    plot_top = CHART_MARGIN["top"]
    plot_bottom = CHART_HEIGHT - CHART_MARGIN["bottom"]
    x_low, x_high = _nice_log_bounds([point["x"] for point in points])
    y_low, y_high = _linear_bounds([point["y"] for point in points])
    log_low, log_high = math.log10(x_low), math.log10(x_high)

    def x_pos(value: float) -> float:
        return _round(plot_left + (math.log10(value) - log_low) / (log_high - log_low) * (plot_right - plot_left))

    def y_pos(value: float) -> float:
        return _round(plot_bottom - (value - y_low) / (y_high - y_low) * (plot_bottom - plot_top))

    step = 0.05 if y_high - y_low <= 0.3 else 0.1
    y_ticks = []
    tick = y_low
    while tick <= y_high + 1e-9:
        y_ticks.append((round(tick, 2), y_pos(tick)))
        tick += step
    ordered = sorted(points, key=lambda item: (item["x"], -item["y"], item["model_id"]))
    placed = [dict(point, cx=x_pos(point["x"]), cy=y_pos(point["y"])) for point in ordered]
    return {
        "points": placed,
        "x_domain": (x_low, x_high),
        "y_domain": (y_low, y_high),
        "x_ticks": [(value, x_pos(value)) for value in _log_ticks(x_low, x_high)],
        "y_ticks": y_ticks,
        "plot": (plot_left, plot_top, plot_right, plot_bottom),
    }


def _point_label(model_id: str) -> str:
    return model_id[: -len(LABEL_SUFFIX)] if model_id.endswith(LABEL_SUFFIX) else model_id


def _overlaps(box: tuple[float, float, float, float], other: tuple[float, float, float, float]) -> bool:
    return not (box[2] <= other[0] or box[0] >= other[2] or box[3] <= other[1] or box[1] >= other[3])


def _segment_boxes(path: list[tuple[float, float]]) -> list[tuple[float, float, float, float]]:
    """Approximate a polyline with small boxes so labels can avoid it."""
    boxes: list[tuple[float, float, float, float]] = []
    for (x1, y1), (x2, y2) in zip(path, path[1:]):
        steps = max(1, int(math.hypot(x2 - x1, y2 - y1) // 6))
        for index in range(steps + 1):
            x = x1 + (x2 - x1) * index / steps
            y = y1 + (y2 - y1) * index / steps
            boxes.append((x - 3, y - 3, x + 3, y + 3))
    return boxes


def _label_positions(
    points: list[dict[str, Any]],
    plot: tuple[float, float, float, float],
    obstacles: list[tuple[float, float, float, float]] | None = None,
) -> dict[str, tuple[float, float, str]]:
    """Place each label in the first free slot of a fixed search order.

    Slots avoid every marker, every earlier label, and the plot edges, so labels
    are never clipped. Placement order is highest pass rate first, then x, then
    model ID, which keeps the output deterministic.
    """
    left, top, right, bottom = plot
    boxes = [(point["cx"] - 8, point["cy"] - 8, point["cx"] + 8, point["cy"] + 8) for point in points]
    boxes.extend(obstacles or [])
    positions: dict[str, tuple[float, float, str]] = {}
    for point in sorted(points, key=lambda item: (-item["y"], item["x"], item["model_id"])):
        width = len(_point_label(point["model_id"])) * LABEL_CHAR_WIDTH
        fallback: tuple[float, float, str] | None = None
        chosen: tuple[float, float, str] | None = None
        for dx, dy in LABEL_OFFSETS:
            anchor = "start" if dx > 0 else "end"
            x = point["cx"] + dx
            baseline = point["cy"] + dy + 4
            x0 = x if anchor == "start" else x - width
            box = (x0 - 2, baseline - LABEL_HEIGHT + 2, x0 + width + 2, baseline + 3)
            if box[0] < left + 4 or box[2] > right - 8 or box[1] < top or box[3] > bottom - 2:
                continue
            if fallback is None:
                fallback = (x, baseline, anchor)
            if any(_overlaps(box, other) for other in boxes):
                continue
            chosen = (x, baseline, anchor)
            boxes.append(box)
            break
        if chosen is None:
            chosen = fallback or (point["cx"] + 12, point["cy"] + 4, "start")
        positions[point["model_id"]] = (_round(chosen[0]), _round(chosen[1]), chosen[2])
    return positions


def _marker(status: str, cx: float, cy: float, title: str) -> str:
    label = f"<title>{_esc(title)}</title>"
    if status == "unranked":
        points = f"{_fmt(cx)},{_fmt(cy - 7)} {_fmt(cx + 7)},{_fmt(cy)} {_fmt(cx)},{_fmt(cy + 7)} {_fmt(cx - 7)},{_fmt(cy)}"
        return f'<polygon class="point unranked" data-status="unranked" points="{points}">{label}</polygon>'
    radius = "6" if status == "confirmed" else "5.5"
    return (
        f'<circle class="point {status}" data-status="{status}" cx="{_fmt(cx)}" cy="{_fmt(cy)}" r="{radius}">'
        f"{label}</circle>"
    )


def render_scatter_svg(
    points: list[dict[str, Any]],
    *,
    chart_id: str,
    x_title: str,
    x_unit: str,
) -> str:
    """Return a deterministic inline SVG scatter with the Pareto frontier drawn."""
    layout = layout_chart(points)
    left, top, right, bottom = layout["plot"]
    frontier_ids = pareto_frontier(points)
    by_id = {point["model_id"]: point for point in layout["points"]}
    frontier_path = [(by_id[m]["cx"], by_id[m]["cy"]) for m in frontier_ids]
    labels = _label_positions(layout["points"], layout["plot"], _segment_boxes(frontier_path))
    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {CHART_WIDTH} {CHART_HEIGHT}" '
        f'role="img" aria-labelledby="{chart_id}-title" id="{chart_id}">',
        f'<title id="{chart_id}-title">Full-contract pass rate against {_esc(x_title)}</title>',
    ]
    for value, y in layout["y_ticks"]:
        parts.append(f'<line class="grid-line" x1="{left}" y1="{_fmt(y)}" x2="{right}" y2="{_fmt(y)}"/>')
        parts.append(
            f'<text class="tick" x="{left - 8}" y="{_fmt(y + 4)}" text-anchor="end">{value * 100:.0f}%</text>'
        )
    for value, x in layout["x_ticks"]:
        parts.append(f'<line class="grid-line" x1="{_fmt(x)}" y1="{top}" x2="{_fmt(x)}" y2="{bottom}"/>')
        parts.append(
            f'<text class="tick" x="{_fmt(x)}" y="{bottom + 18}" text-anchor="middle">{_esc(_tick_label(value, x_unit))}</text>'
        )
    parts.append(f'<line class="axis" x1="{left}" y1="{bottom}" x2="{right}" y2="{bottom}"/>')
    parts.append(f'<line class="axis" x1="{left}" y1="{top}" x2="{left}" y2="{bottom}"/>')
    parts.append(
        f'<text class="axis-title" x="{_fmt((left + right) / 2)}" y="{CHART_HEIGHT - 14}" text-anchor="middle">'
        f"{_esc(x_title)} (log scale)</text>"
    )
    parts.append(
        f'<text class="axis-title" x="18" y="{_fmt((top + bottom) / 2)}" text-anchor="middle" '
        f'transform="rotate(-90 18 {_fmt((top + bottom) / 2)})">Full-contract pass rate</text>'
    )
    if frontier_ids:
        coordinates = " ".join(f"{_fmt(by_id[m]['cx'])},{_fmt(by_id[m]['cy'])}" for m in frontier_ids)
        parts.append(
            f'<polyline class="frontier" data-frontier="{_esc(" ".join(frontier_ids))}" points="{coordinates}"/>'
        )
    for point in layout["points"]:
        x, y, anchor = labels[point["model_id"]]
        parts.append(
            f'<line class="leader" x1="{_fmt(point["cx"])}" y1="{_fmt(point["cy"])}" '
            f'x2="{_fmt(x)}" y2="{_fmt(y - 4)}"/>'
        )
    for point in layout["points"]:
        title = (
            f"{point['model_id']} ({point['status']}): {point['y'] * 100:.1f}% full contract, "
            f"{_value_label(point['x'], x_unit)}"
        )
        frontier_attr = ' data-frontier="true"' if point["model_id"] in frontier_ids else ""
        parts.append(
            f'<g class="model-point" data-model="{_esc(point["model_id"])}" '
            f'data-status="{point["status"]}"{frontier_attr}>'
        )
        parts.append(_marker(point["status"], point["cx"], point["cy"], title))
        x, y, anchor = labels[point["model_id"]]
        parts.append(
            f'<text class="point-label {point["status"]}" x="{_fmt(x)}" y="{_fmt(y)}" text-anchor="{anchor}">'
            f"{_esc(_point_label(point['model_id']))}</text>"
        )
        parts.append("</g>")
    parts.append("</svg>")
    return "".join(parts)


def _chart_legend() -> str:
    items = (
        ('<circle class="point confirmed" cx="9" cy="9" r="6"/>', "confirmed"),
        ('<circle class="point provisional" cx="9" cy="9" r="5.5"/>', "provisional (one sweep, not confirmed)"),
        ('<polygon class="point unranked" points="9,2 16,9 9,16 2,9"/>', "unranked (incomplete coverage, never on the frontier)"),
        ('<line class="frontier" x1="0" y1="9" x2="18" y2="9"/>', "efficiency frontier (ranked models)"),
    )
    return '<ul class="chart-legend">' + "".join(
        f'<li><svg viewBox="0 0 18 18" aria-hidden="true">{shape}</svg> {_esc(text)}</li>'
        for shape, text in items
    ) + "</ul>"


def _scatter_figure(
    leaderboard: dict[str, Any],
    *,
    x_metric: str,
    chart_id: str,
    x_title: str,
    x_unit: str,
) -> str:
    points, omitted = chart_points(leaderboard, x_metric)
    if not points:
        return ""
    svg = render_scatter_svg(points, chart_id=chart_id, x_title=x_title, x_unit=x_unit)
    notes = []
    if any(point["model_id"].endswith(LABEL_SUFFIX) for point in points):
        notes.append(f"Labels omit the {LABEL_SUFFIX} suffix.")
    if omitted:
        notes.append("Not plotted (no value): " + ", ".join(omitted) + ".")
    note = (" " + " ".join(notes)) if notes else ""
    return (
        f'<figure class="chart">{svg}{_chart_legend()}<figcaption>Full-contract pass rate against '
        f"{_esc(x_title)}, {_esc(CHART_CAPTION)}.{_esc(note)}</figcaption></figure>"
    )


def _validate_usage(models: list[dict[str, Any]]) -> None:
    for index, model in enumerate(models):
        if "usage" not in model:
            continue
        usage = _required_mapping(model["usage"], f"leaderboard.models[{index}].usage")
        value = usage.get("mean_output_tokens")
        if value is not None and (not _finite_number(value) or float(value) < 0):
            raise RenderError(f"leaderboard.models[{index}].usage.mean_output_tokens must be a non-negative number or null")


def render_charts(leaderboard: dict[str, Any]) -> str:
    """Return the chart section body, or an empty string when nothing can be plotted."""
    figures = [
        _scatter_figure(
            leaderboard,
            x_metric="median_latency_ms",
            chart_id="chart-latency",
            x_title="median latency per task",
            x_unit="ms",
        ),
        _scatter_figure(
            leaderboard,
            x_metric="mean_output_tokens",
            chart_id="chart-output-tokens",
            x_title="mean output tokens per task",
            x_unit="tokens",
        ),
    ]
    return "".join(figure for figure in figures if figure)


def _policy_value(policy: dict[str, Any] | None, path: tuple[str, ...], default: Any) -> Any:
    value: Any = policy
    for key in path:
        if not isinstance(value, dict):
            return default
        value = value.get(key)
    return value if value is not None else default


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_nonfinite_json_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RenderError(f"could not read JSON from {path}: {exc}") from exc
    return _required_mapping(value, str(path))


def _validate_metrics(value: Any, name: str) -> None:
    metrics = _required_mapping(value, name)
    required = (
        "full_contract_pass_rate",
        "automatic_check_pass_rate",
        "human_quality_score",
        "human_score_coverage",
        "hard_failure_rate",
        "invalid_output_rate",
        "median_latency_ms",
    )
    for key in required:
        if key not in metrics:
            raise RenderError(f"{name} is missing {key!r}")
    for key in (
        "full_contract_pass_rate",
        "automatic_check_pass_rate",
        "human_score_coverage",
        "hard_failure_rate",
        "invalid_output_rate",
        "strict_json_valid_rate",
    ):
        value = metrics.get(key)
        if value is not None and (not _finite_number(value) or not 0 <= float(value) <= 1):
            raise RenderError(f"{name}.{key} must be a number between 0 and 1 or null")
    latency = metrics["median_latency_ms"]
    if latency is not None and (not _finite_number(latency) or float(latency) < 0):
        raise RenderError(f"{name}.median_latency_ms must be a non-negative number or null")
    human = metrics["human_quality_score"]
    if human is not None and (not _finite_number(human) or not 0 <= float(human) <= 1):
        raise RenderError(f"{name}.human_quality_score must be a number between 0 and 1 or null")


def _validate_coverage(value: Any, name: str) -> None:
    coverage = _required_mapping(value, name)
    for key in ("tasks_covered", "tasks_total", "minimum_replicates"):
        candidate = coverage.get(key)
        if not isinstance(candidate, int) or isinstance(candidate, bool) or candidate < 0:
            raise RenderError(f"{name}.{key} must be a non-negative integer")
    if coverage["tasks_covered"] > coverage["tasks_total"]:
        raise RenderError(f"{name}.tasks_covered cannot exceed tasks_total")
    rate = coverage.get("task_coverage_rate")
    if (
        not isinstance(rate, (int, float))
        or isinstance(rate, bool)
        or not math.isfinite(float(rate))
        or not 0 <= float(rate) <= 1
    ):
        raise RenderError(f"{name}.task_coverage_rate must be between 0 and 1")
    expected_rate = round(
        coverage["tasks_covered"] / coverage["tasks_total"],
        6,
    ) if coverage["tasks_total"] else 0.0
    if round(float(rate), 6) != expected_rate:
        raise RenderError(f"{name}.task_coverage_rate disagrees with tasks_covered/tasks_total")


def _validate_ranking_row(
    value: Any,
    name: str,
    expected_status: str | None = None,
    *,
    require_rank: bool = False,
) -> None:
    row = _required_mapping(value, name)
    _required_text(row.get("model_id"), f"{name}.model_id")
    status = _required_text(row.get("status"), f"{name}.status")
    if status not in {"provisional", "confirmed", "unranked", "excluded"}:
        raise RenderError(f"{name}.status is not supported")
    if expected_status is not None and status != expected_status:
        raise RenderError(f"{name}.status must be {expected_status}")
    reason_codes = row.get("reason_codes")
    if not isinstance(reason_codes, list) or any(not isinstance(item, str) for item in reason_codes):
        raise RenderError(f"{name}.reason_codes must be a string array")
    _validate_coverage(row.get("coverage"), f"{name}.coverage")
    _validate_metrics(row.get("metrics"), f"{name}.metrics")
    rank = row.get("rank")
    if require_rank:
        if not isinstance(rank, int) or isinstance(rank, bool) or rank < 1:
            raise RenderError(f"{name}.rank must be a positive integer")
    elif rank is not None:
        raise RenderError(f"{name}.rank is only valid for ranked rows")


def _validate_ranking_view(value: Any, name: str) -> None:
    view = _required_mapping(value, name)
    for key, expected_status in (("ranked", None), ("unranked", "unranked"), ("excluded", "excluded")):
        rows = _list(view.get(key), f"{name}.{key}")
        for index, row in enumerate(rows):
            row_status = None
            if key == "ranked":
                row_status = None
                row_value = _required_mapping(row, f"{name}.{key}[{index}]")
                status = row_value.get("status")
                if status not in {"provisional", "confirmed"}:
                    raise RenderError(f"{name}.{key}[{index}].status must be provisional or confirmed")
            else:
                row_status = expected_status
            _validate_ranking_row(
                row,
                f"{name}.{key}[{index}]",
                row_status,
                require_rank=key == "ranked",
            )
        if key == "ranked":
            ranks = [row["rank"] for row in rows]
            if ranks != list(range(1, len(rows) + 1)):
                raise RenderError(f"{name}.ranked ranks must be contiguous starting at 1")


def _validate_leaderboard_structure(
    data: dict[str, Any],
    policy: dict[str, Any] | None = None,
) -> None:
    schema = _load_json(OUTPUT_SCHEMA)
    schema_errors = validate_schema_instance(data, schema)
    if schema_errors:
        raise RenderError(f"leaderboard violates its schema: {'; '.join(schema_errors[:4])}")
    models = _list(data.get("models"), "leaderboard.models")
    if not models:
        raise RenderError("leaderboard.models must not be empty")
    overall_scope = _required_mapping(data.get("overall"), "leaderboard.overall")
    _validate_ranking_view(overall_scope, "leaderboard.overall")
    scope_rows = list(_list(overall_scope.get("ranked"), "leaderboard.overall.ranked"))
    scope_rows += _list(overall_scope.get("unranked"), "leaderboard.overall.unranked")
    scope_rows += _list(overall_scope.get("excluded"), "leaderboard.overall.excluded")
    task_totals = {
        row["coverage"]["tasks_total"]
        for row in scope_rows
        if isinstance(row, dict) and isinstance(row.get("coverage"), dict)
    }
    if len(task_totals) != 1:
        raise RenderError("leaderboard.overall rows must declare one common tasks_total")
    declared_task_total = task_totals.pop()
    # Excluded roster models are planned-but-not-launched by design, so the
    # planned scope covers eligible models only. A launch failure is an
    # eligible model/task cell with no attempted run.
    eligible_models = [
        model for model in models if isinstance(model, dict) and model.get("availability") == "eligible"
    ]
    expected_planned_cells = len(eligible_models) * declared_task_total
    observed_cells = 0
    model_ids: set[str] = set()
    model_metadata: dict[str, tuple[str, str]] = {}
    aggregate_totals = {
        "planned_cells": expected_planned_cells,
        "launch_failures": 0,
        "attempted_runs": 0,
        "completed_execution_records": 0,
        "execution_blocked_runs": 0,
        "all_attempt_process_or_timeout_failures": 0,
        "resolved_identity_runs": 0,
        "parseable_output_runs": 0,
        "non_json_output_runs": 0,
        "all_attempt_hard_failure_runs": 0,
        "all_attempt_hard_failure_entries": 0,
        "all_attempt_invalid_output_runs": 0,
        "all_attempt_automatic_check_pass_runs": 0,
        "evaluator_blocked_runs": 0,
        "comparable_resolved_runs": 0,
        "excluded_provider_or_identity_runs": 0,
        "blocked_or_unverified_runs": 0,
        "comparable_full_contract_pass_runs": 0,
        "full_contract_pass_runs": 0,
        "comparable_automatic_check_pass_runs": 0,
        "all_automatic_checks_pass_runs": 0,
        "comparable_process_or_timeout_failures": 0,
        "comparable_hard_failure_runs": 0,
        "hard_failure_runs": 0,
        "comparable_invalid_output_runs": 0,
        "invalid_output_runs": 0,
        "process_or_timeout_failures": 0,
    }
    for index, model in enumerate(models):
        name = f"leaderboard.models[{index}]"
        model_id = _required_text(model.get("model_id"), f"{name}.model_id")
        if model_id in model_ids:
            raise RenderError(f"{name}.model_id is duplicated")
        model_ids.add(model_id)
        status = _required_text(model.get("status"), f"{name}.status")
        if status not in {"provisional", "confirmed", "unranked", "excluded"}:
            raise RenderError(f"{name}.status is not supported")
        availability = _required_text(model.get("availability"), f"{name}.availability")
        if availability not in {"eligible", "excluded"}:
            raise RenderError(f"{name}.availability is not supported")
        model_metadata[model_id] = (status, availability)
        task_cells = model.get("task_cells", [])
        if not isinstance(task_cells, list):
            raise RenderError(f"{name}.task_cells must be an array")
        seen_task_ids: set[str] = set()
        for cell_index, cell in enumerate(task_cells):
            cell_name = f"{name}.task_cells[{cell_index}]"
            cell = _required_mapping(cell, cell_name)
            task_id = _required_text(cell.get("task_id"), f"{cell_name}.task_id")
            if task_id in seen_task_ids:
                raise RenderError(f"{cell_name}.task_id is duplicated")
            seen_task_ids.add(task_id)
            attempted_count = _nonnegative_int(cell.get("attempted_runs"), f"{cell_name}.attempted_runs")
            if availability == "eligible" and attempted_count > 0:
                observed_cells += 1
            comparable_count = _nonnegative_int(cell.get("comparable_runs"), f"{cell_name}.comparable_runs")
            excluded_count = _nonnegative_int(cell.get("excluded_runs"), f"{cell_name}.excluded_runs")
            provider_count = _nonnegative_int(
                cell.get("excluded_provider_or_identity_runs"),
                f"{cell_name}.excluded_provider_or_identity_runs",
            )
            blocked_count = _nonnegative_int(
                cell.get("blocked_or_unverified_runs"),
                f"{cell_name}.blocked_or_unverified_runs",
            )
            if comparable_count + excluded_count != attempted_count:
                raise RenderError(f"{cell_name} comparable and excluded counts do not add up")
            if provider_count + blocked_count != excluded_count:
                raise RenderError(f"{cell_name} exclusion counts do not add up")
            cell_values = {
                key: _nonnegative_int(cell.get(key), f"{cell_name}.{key}")
                for key in (
                    "execution_blocked_runs",
                    "all_attempt_process_or_timeout_failures",
                    "resolved_identity_runs",
                    "non_json_output_runs",
                    "all_attempt_hard_failure_entries",
                    "evaluator_blocked_runs",
                    "comparable_resolved_runs",
                    "comparable_full_contract_pass_runs",
                    "full_contract_pass_runs",
                    "comparable_automatic_check_pass_runs",
                    "all_automatic_checks_pass_runs",
                    "comparable_process_or_timeout_failures",
                    "hard_failure_runs",
                    "invalid_output_runs",
                    "process_or_timeout_failures",
                )
            }
            cell_aggregate = {
                "planned_cells": attempted_count,
                "launch_failures": 0,
                "attempted_runs": attempted_count,
                "completed_execution_records": _nonnegative_int(
                    cell.get("completed_execution_records"), f"{cell_name}.completed_execution_records"
                ),
                **cell_values,
                "parseable_output_runs": _nonnegative_int(
                    cell.get("parseable_output_runs"), f"{cell_name}.parseable_output_runs"
                ),
                "all_attempt_hard_failure_runs": _nonnegative_int(
                    cell.get("all_attempt_hard_failure_runs"), f"{cell_name}.all_attempt_hard_failure_runs"
                ),
                "all_attempt_invalid_output_runs": _nonnegative_int(
                    cell.get("all_attempt_invalid_output_runs"), f"{cell_name}.all_attempt_invalid_output_runs"
                ),
                "all_attempt_automatic_check_pass_runs": _nonnegative_int(
                    cell.get("all_attempt_automatic_check_pass_runs"),
                    f"{cell_name}.all_attempt_automatic_check_pass_runs",
                ),
                "excluded_provider_or_identity_runs": provider_count,
                "blocked_or_unverified_runs": blocked_count,
                "comparable_hard_failure_runs": _nonnegative_int(
                    cell.get("comparable_hard_failure_runs"), f"{cell_name}.comparable_hard_failure_runs"
                ),
                "comparable_invalid_output_runs": _nonnegative_int(
                    cell.get("comparable_invalid_output_runs"), f"{cell_name}.comparable_invalid_output_runs"
                ),
                "human_scores_assigned": False,
            }
            try:
                validate_aggregate(cell_aggregate)
            except ValueError as exc:
                raise RenderError(f"{cell_name} aggregate is inconsistent: {exc}") from exc
            aggregate_totals["attempted_runs"] += attempted_count
            for key in aggregate_totals:
                if key not in {"planned_cells", "launch_failures", "attempted_runs"}:
                    aggregate_totals[key] += cell_aggregate.get(key, 0)
    aggregate_totals["launch_failures"] = expected_planned_cells - observed_cells
    if aggregate_totals["launch_failures"] < 0:
        raise RenderError("leaderboard task cells exceed the declared planned scope")
    overall = _required_mapping(data["overall"], "leaderboard.overall")
    _validate_ranking_view(overall, "leaderboard.overall")
    profiles = _required_mapping(data["profiles"], "leaderboard.profiles")
    if not profiles:
        raise RenderError("leaderboard.profiles must not be empty")
    for profile_id, view in profiles.items():
        _required_text(profile_id, "leaderboard.profiles key")
        _validate_ranking_view(view, f"leaderboard.profiles.{profile_id}")

    def _assert_view_model_set(view: dict[str, Any], name: str) -> None:
        observed: list[str] = []
        for key in ("ranked", "unranked", "excluded"):
            observed.extend(
                _required_text(row.get("model_id"), f"{name}.{key}[{index}].model_id")
                for index, row in enumerate(_list(view[key], f"{name}.{key}"))
            )
        if len(observed) != len(set(observed)):
            raise RenderError(f"{name} contains duplicate model rows")
        if set(observed) != model_ids:
            raise RenderError(f"{name} model IDs do not match leaderboard.models")

    _assert_view_model_set(overall, "leaderboard.overall")
    overall_rows_by_id = {
        row["model_id"]: row
        for key in ("ranked", "unranked", "excluded")
        for row in _list(overall[key], f"leaderboard.overall.{key}")
    }
    for model_id, (model_status, availability) in model_metadata.items():
        overall_row = overall_rows_by_id[model_id]
        if overall_row["status"] != model_status:
            raise RenderError(f"leaderboard model {model_id!r} status disagrees with overall ranking")
        if (availability == "excluded") != (model_status == "excluded"):
            raise RenderError(f"leaderboard model {model_id!r} availability disagrees with overall ranking")
    for profile_id, view in profiles.items():
        _assert_view_model_set(view, f"leaderboard.profiles.{profile_id}")
    aggregate = _required_mapping(data["aggregate"], "leaderboard.aggregate")
    for key in aggregate_totals:
        if aggregate.get(key) != aggregate_totals[key]:
            raise RenderError(f"leaderboard.aggregate.{key} disagrees with model task-cell totals")
    try:
        validate_aggregate(aggregate, cell_totals=aggregate_totals)
    except ValueError as exc:
        raise RenderError(str(exc)) from exc
    publication = _required_mapping(data["publication"], "leaderboard.publication")
    for key in (
        "ranking_available",
        "score_publishable",
        "human_scores_assigned",
        "routing_recommendation_allowed",
    ):
        if not isinstance(publication.get(key), bool):
            raise RenderError(f"leaderboard.publication.{key} must be a boolean")
    overall_has_ranked = bool(overall["ranked"])
    if publication["ranking_available"] != overall_has_ranked:
        raise RenderError("leaderboard.publication.ranking_available disagrees with overall.ranked")
    if publication["score_publishable"] != overall_has_ranked:
        raise RenderError("leaderboard.publication.score_publishable disagrees with overall.ranked")
    if publication["human_scores_assigned"] != aggregate["human_scores_assigned"]:
        raise RenderError("leaderboard.publication.human_scores_assigned disagrees with aggregate")
    require_complete_overall = _policy_value(
        policy,
        ("publication", "score_publishable_requires_complete_overall_coverage"),
        True,
    )
    if not isinstance(require_complete_overall, bool):
        require_complete_overall = True
    provisional_min_coverage = _policy_value(
        policy,
        ("coverage", "provisional_min_task_coverage"),
        1.0,
    )
    if not _finite_number(provisional_min_coverage) or not 0 <= float(provisional_min_coverage) <= 1:
        provisional_min_coverage = 1.0
    confirmation_replicates = _policy_value(
        policy,
        ("coverage", "confirmed_min_replicates_per_task"),
        3,
    )
    if not isinstance(confirmation_replicates, int) or isinstance(confirmation_replicates, bool) or confirmation_replicates < 1:
        confirmation_replicates = 3
    for index, row in enumerate(_list(overall["ranked"], "leaderboard.overall.ranked")):
        coverage = row["coverage"]
        if require_complete_overall and coverage["tasks_covered"] != coverage["tasks_total"]:
            raise RenderError(
                f"leaderboard.overall.ranked[{index}] violates complete overall coverage policy"
            )
        if float(coverage["task_coverage_rate"]) < float(provisional_min_coverage):
            raise RenderError(
                f"leaderboard.overall.ranked[{index}] violates provisional coverage policy"
            )
        if row["status"] == "confirmed" and coverage["minimum_replicates"] < confirmation_replicates:
            raise RenderError(
                f"leaderboard.overall.ranked[{index}] violates confirmation replicate policy"
            )
    routing_requires_confirmed = _policy_value(
        policy,
        ("publication", "routing_requires_confirmed_model_per_profile"),
        True,
    )
    if not isinstance(routing_requires_confirmed, bool):
        routing_requires_confirmed = True
    if publication["routing_recommendation_allowed"]:
        for profile_id, view in profiles.items():
            profile_view = _required_mapping(view, f"leaderboard.profiles.{profile_id}")
            ranked_rows = _list(profile_view["ranked"], f"leaderboard.profiles.{profile_id}.ranked")
            if not ranked_rows:
                raise RenderError("leaderboard.publication.routing_recommendation_allowed requires ranked profiles")
            if routing_requires_confirmed and not any(
                row.get("status") == "confirmed" for row in ranked_rows
            ):
                raise RenderError(
                    "leaderboard.publication.routing_recommendation_allowed requires confirmed profiles"
                )
    _required_text(publication.get("reason"), "leaderboard.publication.reason")
    input_info = _required_mapping(data["input"], "leaderboard.input")
    _required_text(input_info.get("roster_path"), "leaderboard.input.roster_path")
    selected_count = input_info.get("selected_run_count")
    if not isinstance(selected_count, int) or isinstance(selected_count, bool) or selected_count < 0:
        raise RenderError("leaderboard.input.selected_run_count must be a non-negative integer")


def render_html(
    leaderboard: dict[str, Any],
    *,
    source_sha256: str | None = None,
    policy: dict[str, Any] | None = None,
) -> str:
    """Return a deterministic standalone HTML report for a generated leaderboard."""
    data = _required_mapping(leaderboard, "leaderboard")
    _reject_nonfinite_values(data, "leaderboard")
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
    if data["schema_version"] != "leaderboard-v2":
        raise RenderError("leaderboard.schema_version must be leaderboard-v2")
    if data["benchmark_id"] != "agent-profile-benchmark":
        raise RenderError("leaderboard.benchmark_id is not supported")
    if data["scope"] != "benchmark-specific model leaderboard and routing aid":
        raise RenderError("leaderboard.scope is not supported")

    _validate_leaderboard_structure(data, policy)
    _validate_usage(_list(data.get("models", []), "leaderboard.models"))

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
    routing_requires_confirmed = _policy_value(
        policy,
        ("publication", "routing_requires_confirmed_model_per_profile"),
        True,
    )
    if not isinstance(routing_requires_confirmed, bool):
        routing_requires_confirmed = True
    routing_subject = "confirmed profiles" if routing_requires_confirmed else "ranked profiles"
    routing_candidates = "confirmed candidates" if routing_requires_confirmed else "ranked candidates"
    ranking_available = publication.get("ranking_available") is True
    route_label = "ON" if routing_allowed else "OFF"
    route_class = "confirmed" if routing_allowed else "unranked"
    human_scores_assigned = publication.get("human_scores_assigned") is True or aggregate.get("human_scores_assigned") is True
    routing_note = (
        f"Routing recommendations are enabled for {routing_subject} in this snapshot."
        if routing_allowed
        else "Routing recommendations are disabled for this snapshot."
    )
    human_scores_note = (
        "Human scores are included in this snapshot."
        if human_scores_assigned
        else "Human scores are not present in this snapshot."
    )
    profile_signal_note = (
        f"These profile views may inform routing recommendations for {routing_candidates} in this snapshot."
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
    run_exclusion_rows = _run_count_rows(models, "excluded_provider_or_identity_runs")
    blocked_rows = _run_count_rows(models, "blocked_or_unverified_runs")
    run_exclusion_sections: list[str] = []
    if run_exclusion_rows:
        run_exclusion_sections.append(
            f"""<h3>Run-level provider and identity exclusions</h3>
        <p>These attempts remain visible in the evidence ledger but do not contribute to comparable quality metrics.</p>
        <div class=\"table-shell\">
          <table>
            <caption>Excluded attempts by model and task</caption>
            <thead><tr><th scope=\"col\">Model</th><th scope=\"col\">Task</th><th scope=\"col\">Excluded runs</th></tr></thead>
            <tbody>{run_exclusion_rows}</tbody>
          </table>
        </div>"""
        )
    if blocked_rows:
        run_exclusion_sections.append(
            f"""<h3>Run-level blocked or unverified evidence</h3>
        <p>These attempts remain visible but are excluded from comparable quality metrics because their execution or isolation was not verified.</p>
        <div class=\"table-shell\">
          <table>
            <caption>Blocked or unverified attempts by model and task</caption>
            <thead><tr><th scope=\"col\">Model</th><th scope=\"col\">Task</th><th scope=\"col\">Blocked or unverified runs</th></tr></thead>
            <tbody>{blocked_rows}</tbody>
          </table>
        </div>"""
        )
    run_exclusion_section = "\n".join(run_exclusion_sections)
    profiles_html = "\n".join(_profile_card(profile_id, _required_mapping(view, f"profiles.{profile_id}")) for profile_id, view in sorted(profiles.items()))
    if not profiles_html:
        profiles_html = '<div class="card"><p>No profile views were generated.</p></div>'

    charts_html = render_charts(data)
    chart_section = (
        f"""<section id="efficiency">
        <p class="eyebrow">Efficiency view</p>
        <h2>Pass rate against time and output</h2>
        <p>Each point is one eligible model. Excluded models are left off. The dashed line joins ranked models that no faster or leaner ranked model beats on full-contract pass rate. These charts are {_esc(CHART_CAPTION)}.</p>
        {charts_html}
      </section>
"""
        if charts_html
        else ""
    )
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
        <p class="lede">A benchmark-specific model leaderboard and routing aid for the frozen <code>0.4.1</code> task suite. It measures contract-following across fixed agent profiles, not general intelligence.</p>
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
        <p>The primary order is full-contract pass rate, followed by automatic-check pass rate, human quality, hard failures, invalid output, and latency. Profile views are weighted equally in the overall score. Under leaderboard-v2 an answer wrapped in one JSON fence or brief prose is recovered and scored on its content; an unreadable answer counts as a failure. Strict JSON validity is reported separately as format compliance and does not change the rank.</p>
        <div class="table-shell">
          <table>
            <caption>Snapshot <code>{_esc(data['input_snapshot_id'])}</code> - {tasks_total} frozen tasks across {len(profiles)} profiles</caption>
            <thead>
              <tr><th scope="col">Rank</th><th scope="col">Model</th><th scope="col">Full contract</th><th scope="col">Automatic checks</th><th scope="col">Hard failures</th><th scope="col">Invalid output</th><th scope="col">Strict JSON (format compliance)</th><th scope="col">Median latency</th></tr>
            </thead>
            <tbody>{ranked_rows}</tbody>
          </table>
        </div>
      </section>

      {chart_section}
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
          <div class="card"><h3>Attempts</h3><p>{_esc(aggregate.get('attempted_runs', 'n/a'))} attempted runs ({_esc(aggregate.get('planned_cells', 'n/a'))} planned cells), {_esc(aggregate.get('completed_execution_records', 'n/a'))} completed, and {_esc(aggregate.get('comparable_resolved_runs', 'n/a'))} comparable.</p></div>
          <div class="card"><h3>Contract quality</h3><p>Comparable subset: {_esc(aggregate.get('comparable_full_contract_pass_runs', aggregate.get('full_contract_pass_runs', 'n/a')))} full-contract passes and {_esc(aggregate.get('comparable_automatic_check_pass_runs', aggregate.get('all_automatic_checks_pass_runs', 'n/a')))} automatic-check passes.</p></div>
          <div class="card"><h3>Failure visibility</h3><p>All attempts: {_esc(aggregate.get('all_attempt_hard_failure_runs', 'n/a'))} hard-failure runs and {_esc(aggregate.get('all_attempt_invalid_output_runs', 'n/a'))} invalid-output runs. Comparable subset: {_esc(aggregate.get('comparable_hard_failure_runs', aggregate.get('hard_failure_runs', 'n/a')))} hard-failure runs and {_esc(aggregate.get('comparable_invalid_output_runs', aggregate.get('invalid_output_runs', 'n/a')))} invalid-output runs.</p></div>
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
        leaderboard = json.loads(
            source_bytes.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_nonfinite_json_constant,
        )
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
