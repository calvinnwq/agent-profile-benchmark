# Agent Profile Benchmark

A reproducible benchmark for evaluating AI agent profiles on measurable, task-specific fixtures.

## Status

The repository is currently at `contract-draft`.

The first public slice freezes nine agent profiles, two task contracts per profile, an offline evaluation boundary, and the scoring order.

The `0.1.0` task registry is intentionally exact.
The ledger's schema pointer and `benchmark_version` are frozen with that registry.
The validator also pins the semantic fingerprint of the full v0.1.0 ledger, so task content cannot be repurposed under an existing ID.
Every task remains offline-only with an empty `allowed_tools` list in this slice.
Adding or replacing a task requires an explicit benchmark-version update and corresponding fixture, prompt, evaluator, and documentation changes.

Task prompts and replay fixtures are deliberately not marked benchmark-ready yet.

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

The CLI enforces the checked-in JSON Schema and the cross-record invariants required by the v0.1.0 registry.
No third-party packages are required.

```bash
python3 scripts/validate_benchmark.py
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

This slice exercises evaluator and evidence wiring only.
It does not change the benchmark ledger from `contract-draft` or provide model evidence.

Raw model run evidence belongs under `.model-evidence/` and is ignored by Git.

## Next gates

1. Freeze one fixture and exact prompt packet per task.
2. Build evaluator oracles with known-good and known-bad controls.
3. Validate the evaluators before any model matrix run.
4. Record the exact Nous Portal model roster and resolved model identifiers.
5. Run matched profile-task cells and preserve raw evidence before reporting results.

The benchmark is a routing aid, not a universal intelligence ranking.
One successful task or one aggregate score is insufficient evidence for trusted use.
