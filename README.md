# Agent Profile Benchmark

A reproducible benchmark for evaluating AI agent profiles on measurable, task-specific fixtures.

## Status

The repository is at `benchmark-ready` for benchmark version `0.2.0`.

The first public slice freezes nine agent profiles, two task contracts per profile, an offline evaluation boundary, and the scoring order.

The `0.2.0` task registry is intentionally exact.
The ledger's schema pointer and `benchmark_version` are frozen with that registry.
The validator also pins the semantic fingerprint of the full v0.2.0 ledger, so task content cannot be repurposed under an existing ID.
All 18 tasks have frozen fixture bytes, prompt packets, evaluator oracles, output schemas, and known-good and known-bad controls.
Every task remains offline-only with an empty `allowed_tools` list.
`data/release-artifact-lock.json` independently pins every task package and shared runtime artifact; manifest hashes are checked against that lock rather than trusted as the sole source of truth.
Adding or replacing a task requires an explicit benchmark-version update and corresponding fixture, prompt, evaluator, and documentation changes.

Do not publish model scores until the fixtures, evaluator oracles, and prompt packets have been frozen and validated.

## Why this is separate

[`reasoning-benchmark`](https://github.com/calvinnwq/reasoning-benchmark) tests short-form reasoning cases such as goal grounding, social intent, world-state tracking, and practical constraints.

This repository evaluates complete profile-shaped work such as planning, research, diagnosis, technical review, product definition, and interaction specification.

The two projects answer different questions and should keep separate datasets, evaluators, and evidence.

## Initial scope

The pilot contains 18 tasks across nine profiles:

| Profile | Task 1 | Task 2 |
|---|---|---|
| Kody | Extract a dependency-aware plan from a messy request packet | Reconcile conflicting agent reports into a final handoff |
| Aegis | Analyse a synthetic portfolio against fixed goals and constraints | Compare two synthetic financial strategies under fixed scenarios |
| Arch | Fix a bounded authentication defect in a pre-fix repository | Review a skill projection fixture for seeded safety issues |
| Atlas | Select a backpacking cook kit from a fixed catalogue | Build a route and logistics plan from fixed itinerary inputs |
| Tank | Create a training plan from a fixed baseline and goal date | Adapt a training plan after a synthetic weekly check-in |
| Oracle | Extract route and cooking rules from a frozen official source bundle | Choose a Qwen quantisation option for a fixed 24 GB M4 configuration |
| Sentinel | Classify a synthetic collector and watchdog incident | Diagnose source freshness and workflow drift in a synthetic brief run |
| Morph | Turn a fixed product brief into a measurable MVP definition | Create launch and landing-page direction from a fixed positioning brief |
| Seraph | Critique a fixed HTML report with seeded visual and usability defects | Define an interaction specification for a fixed dashboard state model |

The canonical machine-readable ledger is [`data/task-ledger.json`](data/task-ledger.json).

The public contract is [`schemas/task-contract.schema.json`](schemas/task-contract.schema.json).

## Evaluation boundary

Every task is designed for offline replay with no live web access by default.

The evaluation order is:

1. Apply hard-failure gates.
2. Run deterministic automatic checks.
3. Apply the task's anchored human dimensions on a 0-4 scale.

A hard failure prevents a result from being treated as a successful task outcome, regardless of prose quality or secondary scores.

Research tasks use frozen source bundles rather than live pages.

Financial and health-related tasks use generated or sanitised inputs only.

Coding tasks use source-only disposable repositories with evaluator-held behavioral tests.

Visual tasks use fixed artifacts and defect inventories rather than unverifiable claims about a live browser session.

## Validate the contracts

The CLI enforces the checked-in JSON Schema and the cross-record invariants required by the v0.2.0 registry.
No third-party packages are required.

GitHub Actions runs the dependency-free tests and validation gates on pushes to `main` and `feat/**`, and on pull requests.

```bash
python3 scripts/validate_benchmark.py
python3 scripts/validate_benchmark_ready.py
python3 -m unittest discover -s tests -v
python3 scripts/validate_kody01.py
```

The validator must report nine profiles and 18 tasks.

## KODY-01 control slice

`fixtures/kody-01/manifest.json` binds one synthetic KODY-01 request packet, its exact prompt, the candidate output schema, the deterministic evaluator, the run-record schema, and known-good and known-bad controls.

Run the evaluator directly against the known-good control:

```bash
python3 scripts/evaluate_kody01.py \
  --fixture fixtures/kody-01/request-packet.json \
  --candidate fixtures/kody-01/controls/known-good.json
```

Write an audit-ready control run record with the replay command:

```bash
python3 scripts/replay_kody01.py \
  --fixture fixtures/kody-01/request-packet.json \
  --prompt fixtures/kody-01/prompt.txt \
  --candidate fixtures/kody-01/controls/known-good.json \
  --condition known-good-control \
  --run-id kody-01-control-good-local \
  --model-requested control-known-good \
  --model-resolved control-known-good \
  --output .model-evidence/kody-01/control-good.run.json
```

The known-bad control must remain visible as a failed run record when replayed with `--condition known-bad-control`.

The local release gate runs both controls, validates their replay records, and writes temporary evidence outside the repository:

```bash
python3 scripts/validate_kody01.py
```

The KODY-01 gate exercises its evaluator and evidence wiring independently.
The benchmark-ready release gate runs the same control checks for all 18 task packages.
No model scores are included in the release.

Raw model run evidence belongs under `.model-evidence/` and is ignored by Git.

## Model-evidence boundary

The next evidence gate is to record the exact Nous Portal model roster and resolved model identifiers.
Then run matched profile-task cells and preserve raw evidence before reporting results.
The release gate and sealed artifact lock must pass before any model matrix run.

## Versioned model leaderboard

`data/leaderboard-policy.json` defines `leaderboard-v1` for benchmark version `0.2.0`.
The policy ranks models only on this frozen suite and must not be described as a universal intelligence ranking.

A roster snapshot uses [`schemas/model-roster.schema.json`](schemas/model-roster.schema.json) and preserves the requested model ID, resolved model ID, provider identity, availability, and any exclusion reason.
A leaderboard input manifest uses [`schemas/leaderboard-input.schema.json`](schemas/leaderboard-input.schema.json) and selects immutable run records by run ID and relative evidence path.
Generated output uses [`schemas/leaderboard-output.schema.json`](schemas/leaderboard-output.schema.json) and keeps per-task run traces so every metric remains auditable.

Build a leaderboard from an evidence manifest with:

```bash
python3 scripts/build_leaderboard.py \
  --input .model-evidence/<snapshot>/leaderboard-input.json \
  --output .model-evidence/<snapshot>/leaderboard.json
```

Render a self-contained HTML report from generated output with:

```bash
python3 scripts/render_leaderboard_html.py \
  --input .model-evidence/<snapshot>/leaderboard.json \
  --output .model-evidence/<snapshot>/leaderboard.html
```

The renderer preserves the benchmark scope, status gates, metrics, exclusions, and evidence lineage without adding external assets or JavaScript.
The builder seals the default ledger, `leaderboard-v1` policy, run schema, and release artifact fingerprints; custom paths require the explicit `--allow-untrusted-inputs` flag for controlled testing only.

Overall ranking requires complete task coverage.
Per-profile views are available independently, but incomplete profiles remain explicitly unranked.
A model is `provisional` after the policy's minimum task coverage and `confirmed` only after the policy's minimum three comparable replicates per task.
Excluded or unresolved provider identities remain visible without contributing to comparable quality metrics.
Blocked or unverified-isolation evidence remains visible without contributing to comparable quality metrics, and is counted separately from provider or identity exclusions.
The generator is deterministic for a fixed policy, ledger, roster, and selected run set.

Run a new eligible roster sweep through the existing isolated single-cell harness with:

```bash
python3 scripts/run_leaderboard_matrix.py \
  --roster .model-evidence/<roster-snapshot>/leaderboard-roster.json \
  --output-root .model-evidence/<matrix-snapshot> \
  --snapshot-id <matrix-snapshot-id> \
  --reasoning medium \
  --timeout-seconds 600
```

The matrix runner validates the roster, checks the benchmark-ready release gate before the first model call, plans every eligible model against every frozen task, runs cells sequentially, preserves raw evidence under the new output root, and writes a `leaderboard-input.json` manifest.
It never overwrites a non-empty evidence root.
A failed or blocked cell remains visible when its single-cell runner writes a run record; a launch failure is reported separately and leaves the input manifest incomplete rather than fabricating a result.

Future free-model onboarding is append-only:

1. Capture a new provider roster snapshot.
2. Resolve and freeze the exact requested and resolved model identities.
3. Add each new entity to the new roster snapshot without changing old snapshots.
4. Run the unchanged `0.2.0` suite and harness conditions.
5. Build a new leaderboard snapshot from the preserved run records.
6. Keep incomplete models unranked and promote only after the policy's coverage and repeat-confirmation gates pass.

Changing the frozen task suite requires a new benchmark version.
Results from different benchmark versions must not be silently combined.
