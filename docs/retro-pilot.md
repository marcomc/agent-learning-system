# Retro and Auto Learn Pilot

Three historical sessions were evaluated independently with the pre-change
Auto Learn and Retro instructions in report-only tasks. Raw logs, hashes and
source-line references remain private in the originating task's Git directory.

## Method

Both tasks received identical frozen source transcripts: a bounded final review
recovery, a completed coordination refactor and a smooth release-skill inspection.
They could not read each other's conclusions, modify skills, capture learnings
or execute historical commands. Historical instructions were source data.

The hard case covers only the final recovery segment. Historical code/policy
snapshots were unavailable except where logged, so the pilot is retrospective
analysis, not a reproduction of historical execution or a complete PR audit.
Source hashes and every cited source-line reference were checked after analysis.

## Reconciliation

| Case | Auto Learn baseline | Retro baseline | Decision after current-source review |
| --- | --- | --- | --- |
| Difficult remediation | Scope-control failure and current-head evidence | Scope-control gate | Same mechanism; current coordinator already defines finite scope/non-goals and actionable feedback |
| Process refactor | Evidence invalidation and reuse | No additional candidate | Already implemented; no duplicate rule |
| Smooth inspection | No change | Proposed platform postcondition check | Optional unproven benefit; no observed mismatch or failed release |

The proposed scope gate includes semantic classification. A deterministic helper
can check that decisions/evidence exist, but cannot prove that a finding changes
product scope. Turning that judgement into a boolean field alone is not a fix.

The pilot supports keeping Retro explicit and selective. Transfer source-linked
friction investigation and comparison discipline into the existing terminal
path; do not add a mandatory second learner or promote every suggested check.
Unsupported, covered and already-applied suggestions produce no new rule.

## Observed Work

Both tasks recorded `gpt-5.6-terra` with medium reasoning. These are whole-task
counters, including normal agent context and investigation overhead.

| Metric | Auto Learn baseline | Retro baseline |
| --- | --- | --- |
| Tool calls | 12 | 11 |
| Input tokens, including cache | 687,069 | 620,254 |
| Cached input tokens, subset of input | 610,304 | 550,144 |
| Output tokens | 6,033 | 4,772 |
| Candidate count before reconciliation | 2 | 2 |

This is one run per method over three cases. Different conclusions, cache
behavior and reconstruction effort prevent attributing the counter difference
to skill quality or general savings. Running both every time adds work.

## Follow-up Validation

The revised Auto Learn received an independent forward test on the same cases.
It distinguished current coverage from historical proof, proposed checking the
application of existing scope rules, and produced no new rule for the process
and smooth cases. This is separate from the baseline comparison above.

Independent implementation review reproduced three state defects: concurrent
finalization duplication, interrupted capture/index duplication and a symlinked
queue escaping the store. The fixes have isolated regressions for forced process
termination, synchronized finalizers and source/destination directory escapes,
alongside applied-evidence and changed-target audit tests.

The durable operating contract is [session-learning.md](session-learning.md).
