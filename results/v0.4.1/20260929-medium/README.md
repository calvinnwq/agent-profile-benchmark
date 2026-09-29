# v0.4.1 leaderboard snapshot - 2026-09-29 (medium reasoning)

Built with policy `leaderboard-v2` on the sealed release at `ac3c78c`, from 3 replicates × 6 eligible Nous Portal free models × 18 tasks (324 runs).
Scoped to this frozen suite only; not a general ranking.

- `leaderboard.json` - builder output (metrics, coverage, per-run traces).
- `leaderboard.html` - rendered report with the score-vs-latency and score-vs-output-tokens charts.
- `roster.json` - the model roster used, including the two excluded models and their reasons.

Roster source: the Portal's `freeRecommendedModels` list, minus the stealth model, filtered by a live one-call probe.
Raw prompts and model outputs are not published; the `raw_output_reference` paths point at local, git-ignored evidence.

Notes:
- `stepfun/step-3.7-flash:free` timed out (600 s) on 26 of 54 runs and is unranked; its free access ends 2026-10-01.
- `meituan/longcat-2.5-preview:free` and `poolside/laguna-s-2.1:free` are provisional because of 1-2 timeouts.
