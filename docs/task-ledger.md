# Task Ledger

The canonical task data lives in [`data/task-ledger.json`](../data/task-ledger.json).

This page is the human-readable index of the first 18 task contracts.
The Primary evaluation column is an editorial summary; the canonical measurement contract remains in the JSON ledger.

All 18 tasks are `benchmark-ready` in benchmark version `0.2.0`.
Each task has a frozen prompt, fixture, evaluator oracle, output schema, known-good control, and known-bad control under `fixtures/`, `oracles/`, and `schemas/`.

| ID | Profile | Task | Provenance | Primary evaluation (editorial summary) |
|---|---|---|---|---|
| `KODY-01` | Kody | Extract a dependency-aware plan from a messy request packet | Synthetic | Hybrid: required fields, constraints, owner set, dependency DAG, uncertainty |
| `KODY-02` | Kody | Reconcile conflicting agent reports into a final handoff | Synthetic | Hybrid: fact retention, conflict surfacing, evidence links, action completeness |
| `AEGIS-01` | Aegis | Analyse a synthetic portfolio against fixed goals and constraints | Synthetic | Hybrid: arithmetic, reserve and risk constraints, assumption provenance |
| `AEGIS-02` | Aegis | Compare two synthetic financial strategies under fixed scenarios | Synthetic | Hybrid: scenario formulas, comparison coverage, liquidity, recommendation consistency |
| `ARCH-01` | Arch | Fix a bounded authentication defect in a pre-fix repository | Historical candidate | Objective: hidden tests, focused tests, changed-file scope, security boundary |
| `ARCH-02` | Arch | Review a skill projection fixture for seeded safety issues | Historical candidate | Hybrid: defect recall, precision, severity mapping, path evidence |
| `ATLAS-01` | Atlas | Select a backpacking cook kit from a fixed catalogue | Historical candidate | Hybrid: catalogue membership, weight, cost, fuel, availability |
| `ATLAS-02` | Atlas | Build a route and logistics plan from fixed itinerary inputs | Historical candidate | Hybrid: timeline, transport, required facts, contingencies |
| `TANK-01` | Tank | Create a training plan from a fixed baseline and goal date | Synthetic | Hybrid: sessions, progression, volume, safety boundary |
| `TANK-02` | Tank | Adapt a training plan after a synthetic weekly check-in | Synthetic | Hybrid: availability, load response, recovery hold, goal preservation |
| `ORACLE-01` | Oracle | Extract route and cooking rules from a frozen official source bundle | Historical candidate | Hybrid: claim-source mapping, quote fidelity, dates, unsupported claims |
| `ORACLE-02` | Oracle | Choose a Qwen quantisation option for a fixed 24 GB M4 configuration | Historical candidate | Hybrid: memory arithmetic, capacity, requirements, estimate labelling |
| `SENTINEL-01` | Sentinel | Classify a synthetic collector and watchdog incident | Historical candidate | Objective: state, evidence, severity, action order |
| `SENTINEL-02` | Sentinel | Diagnose source freshness and workflow drift in a synthetic brief run | Historical candidate | Objective: source states, stale-versus-failed, causal chain, read-only boundary |
| `MORPH-01` | Morph | Turn a fixed product brief into a measurable MVP definition | Synthetic | Hybrid: field coverage, priority consistency, metric measurability, contradictions |
| `MORPH-02` | Morph | Create launch and landing-page direction from a fixed positioning brief | Synthetic | Hybrid: positioning, claim grounding, CTA, section coverage |
| `SERAPH-01` | Seraph | Critique a fixed HTML report with seeded visual and usability defects | Synthetic | Hybrid: defect recall, precision, location integrity, severity order |
| `SERAPH-02` | Seraph | Define an interaction specification for a fixed dashboard state model | Synthetic | Hybrid: state coverage, transitions, keyboard behavior, read-only boundary |

## Shared hard gates

Every task has task-specific hard failures in the JSON ledger.

The shared gate rejects fabricated evidence, unsupported certainty, unauthorised external action, privacy leakage, and claims of verification that did not occur.

Coding tasks additionally reject failed hidden tests and unrelated repository changes.

Health-related tasks reject diagnostic overreach and progression through fixture-defined red flags.

## Contract review checklist

Before moving a task to `benchmark-ready`, verify that:

- the input is replayable without live web access
- the output format can be parsed consistently
- at least one automatic check has an executable oracle
- human dimensions have concrete 0, 2, and 4 anchors
- a known-good and known-bad output can be distinguished
- provenance and privacy classification are accurate
- failure and blocked states remain visible

Run `python3 scripts/validate_benchmark_ready.py` to apply this checklist to every task package.
