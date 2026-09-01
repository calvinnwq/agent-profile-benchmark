# Evaluation Contract

This benchmark compares models under matched conditions.

The model is the independent variable.

Task wording, profile instructions, fixture bytes, allowed tools, output format, evaluator version, reasoning effort, timeout policy, and verification opportunity must remain constant within a comparison.

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

For benchmark version `0.2.0`, the checked-in ledger is `benchmark-ready` only after the release gate validates every task package.
`python3 scripts/validate_benchmark.py` validates the frozen ledger contract, while `python3 scripts/validate_benchmark_ready.py` validates artifact bindings and both controls for all 18 tasks.

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

The evaluator version is `kody-01-oracle-v2`.
It rejects an unbound fixture, duplicate JSON keys, malformed nested output, dropped hard constraints, unavailable owners, inconsistent or cyclic dependencies, unlabelled ambiguities, and unauthorised publication claims.
Checks are evaluated independently when their own inputs are structurally available, so a schema failure does not manufacture hard-failure labels for unrelated checks.

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
The control result is evaluator evidence, not a model benchmark score.

Run `python3 scripts/validate_kody01.py` for the standalone KODY-01 compatibility gate.
Run `python3 scripts/validate_benchmark_ready.py` for the release gate covering all 18 tasks.
The release gate checks manifest bindings and fingerprints, executes both controls for every task, validates known-good outputs against their schemas, and requires known-bad controls to trigger every declared hard failure.
The sealed release-artifact lock independently pins those package bytes and shared runtime files, so changing an artifact and repinning its mutable manifest cannot redefine a benchmark task.

## Model calibration harness

`scripts/run_task_model.py` runs one approved model-calibration cell for any task with the frozen prompt and fixture.
By default it invokes `scripts/hermes_no_tools.py`, which selects the task profile only for credentials, ignores profile configuration and rules, disables fallback routing, pins the requested reasoning effort, and passes an explicit empty tool surface.
The adapter also disables the one-shot session database, so no transcript or memory state is persisted.
It preserves the exact composed input, stdout, stderr, usage report, process status, model/provider resolution, trial metadata, and pre-run Git state under `.model-evidence/<run-id>/`.
A custom `--agent-command` is supported for test doubles and diagnostics, but its tool, memory, and fallback isolation is unverified, so the runner records the cell as `blocked` rather than scoreable evidence.
Malformed model output, non-zero exits, and timeouts remain visible as failed evidence.
Missing or contradictory model/provider resolution remains visible as blocked evidence.
Model-authored Python in `ARCH-01` is not executed without an OS sandbox, so that cell is blocked until a sandboxed evaluator is available.
Direct `scripts/evaluate_task.py` calls also treat candidate output as untrusted by default and do not execute `ARCH-01` code.
The `--trusted-control` flag is reserved for exact release-locked controls after integrity validation; never use it for model output.
The runner verifies the sealed release-artifact lock before launching a model process.

For example:

```bash
python3 scripts/run_task_model.py \
  --task KODY-02 \
  --fixture fixtures/kody-02/input.json \
  --prompt fixtures/kody-02/prompt.txt \
  --run-id kody-02-model-001 \
  --model-requested <resolved-model-id> \
  --provider <verified-provider-id>
```

The command does not publish scores or create a model matrix.

## Re-runnable leaderboard workflow

`data/leaderboard-policy.json` is the checked-in `leaderboard-v1` policy for benchmark version `0.2.0`.
It defines a benchmark-specific model leaderboard and routing aid, not a universal intelligence ranking.

Roster snapshots must conform to `schemas/model-roster.schema.json`.
Each model entity preserves its requested ID, resolved ID, requested and resolved provider, availability, and exclusion reason where applicable.
An eligible entity must have a canonical resolved model identity.
An excluded entity remains visible but cannot enter a ranking.

A matrix run should use `scripts/run_leaderboard_matrix.py` rather than invoking models through profile aliases or assembling run records by hand.
The runner validates the roster and frozen task manifests, checks `scripts/validate_benchmark_ready.py` before the first model call, plans every eligible model against all 18 frozen tasks, and invokes `scripts/run_task_model.py` sequentially with the same reasoning and timeout settings.
The runner writes a new evidence root and refuses to overwrite a non-empty one.
A cell's raw output and run record remain immutable under its run ID.

```bash
python3 scripts/run_leaderboard_matrix.py \
  --roster .model-evidence/<roster-snapshot>/leaderboard-roster.json \
  --output-root .model-evidence/<matrix-snapshot> \
  --snapshot-id <matrix-snapshot-id> \
  --reasoning medium \
  --timeout-seconds 600
```

The resulting `leaderboard-input.json` conforms to `schemas/leaderboard-input.schema.json` and selects only run records that were actually written and identity-checked by the matrix runner.
A launch failure is recorded in `matrix-run.json` and does not become a fabricated run record.

Build the deterministic output from that manifest:

```bash
python3 scripts/build_leaderboard.py \
  --input .model-evidence/<matrix-snapshot>/leaderboard-input.json \
  --output .model-evidence/<matrix-snapshot>/leaderboard.json
```

The generated artifact conforms to `schemas/leaderboard-output.schema.json`.
It contains overall and per-profile views, explicit attempted/comparable/excluded aggregate counts, coverage, objective metrics, status, and raw run references.
Overall ranking requires every task to have at least one comparable run.
A complete but not repeated model is `provisional`.
A model with at least three comparable replicates for every task is `confirmed`.
Incomplete evidence is emitted under `unranked` rather than being intermingled with ranked entries.
The primary metric is full contract pass rate, followed by automatic-check pass rate, normalized human quality, hard-failure rate, invalid-output rate, and median latency.
Human quality cannot rescue a hard contract failure.

For a fixed policy, benchmark ledger, roster, and selected run set, output generation must be byte-deterministic.
The input manifest and generated output preserve the release-lock fingerprint and exact run references so each value can be audited back to raw evidence.
Provider or identity exclusions are counted separately and do not contaminate comparable quality aggregates.

To onboard a later free model, capture a new roster snapshot and create a new model entity rather than mutating an earlier snapshot.
Run the unchanged frozen suite and conditions, preserve its raw evidence, build a new input manifest, and generate a new leaderboard snapshot.
A new model remains `unranked` until it has complete evidence and becomes `provisional` or `confirmed` only under the coverage policy.
A task-suite change requires a new benchmark version and non-comparable output lineage.

## Run evidence

A future run record should preserve at least:

```text
run_id
benchmark_id
benchmark_version
release_lock_fingerprint
ledger_fingerprint
task_id
profile_id
model_requested
model_resolved
provider_requested
provider_resolved
resolution_status
harness
condition
evaluator_version
task_manifest_fingerprint
oracle_fingerprint
output_schema_fingerprint
evaluator_fingerprint
run_record_schema_fingerprint
harness_fingerprint
prompt_fingerprint
fixture_fingerprint
input_fingerprint
input_composition_version
started_at
completed_at
status
execution_status
failure_class
raw_output_reference
raw_output_fingerprint
automatic_checks
hard_failures
human_scores
latency_ms
usage
notes
```

`model_requested` and `model_resolved` are separate fields.
A scoreable cell must carry the full resolved model and provider IDs from the usage report.
If the provider cannot resolve either identity, the record uses the explicit `unresolved` marker and a `blocked` status rather than substituting the request.
A provider alias alone is not sufficient model identity.
The manifest, evaluator, output schema, run-record schema, and exact composed input fingerprints must be recorded for every cell.
Run IDs are unique within an evidence root; retries use a new run ID so failed attempts remain immutable.

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
