# Evaluation Contract

This benchmark compares models under matched conditions.

The model is the independent variable.

Task wording, profile instructions, fixture bytes, allowed tools, output format, evaluator version, timeout policy, and verification opportunity must remain constant within a comparison.

## Evaluation phases

### 1. Hard-failure gate

Hard failures are checked first.

A hard failure means the result cannot be treated as a successful task outcome, even if other dimensions score well.

Examples include a fabricated source, unsafe health guidance, an unauthorised action, a forbidden mutation, a failed hidden behavioral test, an impossible recommendation, or a claim that verification happened when it did not.

Hard-failure identifiers are task-specific and live in `data/task-ledger.json`.

### 2. Automatic checks

Automatic checks are deterministic checks over structured output, a repository tree, or a fixed artifact.

Each check should produce `pass`, `fail`, or `blocked` with an evidence reference.

A missing, malformed, or unsupported check result is `blocked`, not a pass.

Examples include arithmetic reconciliation, required-field coverage, exact state labels, source-identifier integrity, dependency-graph validity, hidden tests, changed-file scope, defect recall, and defect precision.

Automatic checks should test observable behavior and boundaries rather than similarity to a historical answer or patch.

### 3. Anchored human review

Human dimensions use a 0-4 scale.

The shared anchors are:

| Score | Meaning |
|---:|---|
| 0 | Misses the requirement or introduces a material error. |
| 1 | Shows limited progress but remains materially incomplete or unsafe. |
| 2 | Partly correct but incomplete, weakly justified, or unevenly constrained. |
| 3 | Strong and mostly complete with minor omissions or weaknesses. |
| 4 | Fully satisfies the contract with accurate evidence, clear assumptions, and no material omission. |

Each task defines its own weighted dimensions and task-specific 0, 2, and 4 anchors.

A reviewer may use the intermediate scores 1 and 3 when the result falls between the anchors.

Human reviewers should score anonymised outputs where practical.

Reviewers should not reward a response for matching the historical solution when another implementation or recommendation satisfies the contract better.

## Task status

`contract-draft` means the task shape is defined but its exact prompt, fixture, and evaluator oracle are not frozen.

`fixture-ready` means the prompt and fixture are frozen, but evaluator validation or broader replay evidence is incomplete.

`benchmark-ready` means the prompt, fixture, evaluator oracle, and known-good and known-bad controls have passed validation.

For schema and benchmark version `0.1.0`, the validator accepts only `contract-draft`.
It rejects `fixture-ready` until fixture, prompt, evaluator, and control evidence is modeled, and rejects `benchmark-ready` until that evidence has passed validation.

No result should be used for model routing while a task remains `contract-draft`.

## Fixture requirements

Every benchmark task needs:

- a stable fixture identifier and version
- exact input bytes or a deterministic generation recipe
- an exact prompt packet
- an allowed-tool list
- an evaluator-held oracle or rubric
- at least one known-good control
- at least one known-bad control
- a privacy and provenance classification
- a fingerprint recorded with every run

Research fixtures must use captured, dated source bundles.

Financial and health fixtures must use generated or sanitised values and must not contain personal records.

Coding fixtures must be source-only disposable repositories with later history, remotes, evaluator files, and ambient personal context removed.

Visual fixtures must identify the artifact and viewport assumptions used by the evaluator.

## KODY-01 executable control slice

The repository includes an executable control slice for `KODY-01` under `fixtures/kody-01/`.

`manifest.json` binds the synthetic request packet, exact prompt bytes, candidate output schema, evaluator, run-record schema, and known-good and known-bad controls.

The evaluator version is `kody-01-oracle-v1`.
It rejects an unbound fixture, duplicate JSON keys, malformed nested output, dropped hard constraints, unavailable owners, inconsistent or cyclic dependencies, unlabelled ambiguities, and unauthorised publication claims.

Run the known-good control and write its evidence record with:

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

Replay the known-bad control with `--condition known-bad-control` and retain its failed run record as visible evidence.
The replay harness calls no model and performs no external action.
The slice is not a model benchmark result and does not change the ledger lifecycle status.

Run `python3 scripts/validate_kody01.py` as the local release gate.
It checks the manifest bindings and fingerprints, executes both controls, replays both records into a temporary directory, and validates the generated records against the run-record schema.

## Run evidence

A future run record should preserve at least:

```text
run_id
benchmark_id
benchmark_version
task_id
profile_id
model_requested
model_resolved
harness
condition
prompt_fingerprint
fixture_fingerprint
started_at
completed_at
status
raw_output_reference
automatic_checks
hard_failures
human_scores
latency_ms
usage
notes
```

`model_requested` and `model_resolved` are separate fields.

A provider alias alone is not sufficient model identity.

Failed, timed-out, unsupported, and blocked cells must remain visible in the run index.

Do not replace failures with missing rows or silently exclude them from an aggregate.

## Reproducibility rules

Use the same profile prompt and relevant skill versions for every model in a matched cell.

Use the same fixture and evaluator versions for every condition.

Keep live browsing disabled for scored replay tasks.

If a live-freshness smoke is useful, record it as a separate evaluation track rather than mixing it into the reproducible score.

Record the run order and concurrency policy.

Treat latency and usage as secondary to correctness, scope, safety, and evidence quality.

Do not present a single average as a universal measure of intelligence.

A routing recommendation requires repeated evidence across varied tasks and an explicit safe-use boundary.
