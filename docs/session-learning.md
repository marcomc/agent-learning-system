# Session Learning and Retrospectives

Use one terminal learning path for review findings and observed execution
friction. Retro remains explicitly invoked; the daily consolidator and weekly
audit do not start retrospective tasks.

## Table of Contents

- [Ownership and Timing](#ownership-and-timing)
- [Evidence and Intervention](#evidence-and-intervention)
- [Capture and Reconciliation](#capture-and-reconciliation)
- [Independent Comparison](#independent-comparison)
- [Cost and Maintenance](#cost-and-maintenance)

## Ownership and Timing

| Component | Responsibility |
| --- | --- |
| Review coordinator | One run, ledger, stable policy and terminal learning owner |
| Auto Learn | Distill findings and evidenced friction; refine authorized sources |
| Explicit Retro | Independently investigate session difficulties and propose improvements |
| Record Agent Learning | One factual capture per project/source-run/family |
| Consolidation | Verify and route instruction learning within existing authority |
| Weekly audit | Compare changed targets, recurrence and available process-cost evidence |

Record compact source-linked observations during work. Use Retro at the end of
a difficult build, Pre-PR-only task, integrated local/cloud review or an explicit
non-success stop. During an ongoing integrated run, accumulate candidates rather
than starting a retrospective after every batch. Keep policy changes for the next
run; use the existing explicit rebaseline process for an urgent correction.

## Evidence and Intervention

Auto Learn begins with terminal findings/fixes/verification and the coordinator's
candidate record. Retro starts from a session history and can also uncover
navigation, tool-output, instruction-load and information-access problems. Both
use the agent's available tools; neither is an independent telemetry engine.

An actionable candidate identifies a source event, consequence, cause,
existing-coverage decision, narrow target and validation. Zero candidates is
valid. Missing historical snapshots and measurements stay explicit limitations.
Scope classification is judgement: a script can enforce the presence of a
decision/evidence record, not establish that its semantics are true.

| Cause | Preferred target |
| --- | --- |
| Reproducible product failure | Owning code and meaningful regression |
| Mechanical violation | Existing linter/validator/check before adding one |
| Reviewer judgement gap | Existing standards or relevant reviewer guidance |
| Navigation or tool friction | A precise pointer, query, command or output format |
| Repeated orchestration | Existing coordinator and valid evidence reuse |
| Conditional instruction detail | Existing relevant skill/reference or template atom |

The consolidation workflow can change instructions under its configured policy.
Code, CI, hooks, tooling, permissions and publication require applicable separate
scope. A proposal or archived note grants no additional authority.

## Capture and Reconciliation

Pass `--source-run-id` and an explicit `--lesson-family` to `record`. The identity
includes the resolved project path and excludes the reporting skill, so the
coordinator and Auto Learn can reference one capture. Identical retries return
the same note even after finalization; conflicting content requires explicit
reconciliation. Stable filenames and embedded capture identity allow recovery
after interruption between note and index writes. Distinct later runs remain
distinct recurrence evidence. Finalization rechecks its source while locked;
queue and archive symlinks cannot escape the configured store.

Finalization journals bind the original note, resulting note and processed-state
entry. After interruption, `prepare-run.pending_finalizations` exposes unfinished
commits without changing notes. Mutating capture/finalization retries reconcile
them under the lock; `recover-finalizations` also provides an explicit operator
path. A completed move restores its missing state; an uncommitted move restores
the queued original. Edited/foreign inputs stop for reconciliation. Completion
reports reject unresolved journals; notification and audit paths remain read-only.

Default `application_status` is `proposed`. For an already applied change pass
`--application-status applied`, repeated `--applied-target`, a saved
`--application-evidence` artifact and `--verification`. The helper snapshots
target/evidence hashes. Hashes establish identity, not successful execution or
correctness. Current coverage must still be verified during consolidation;
already-covered learning is archived without applying the same rule again.

Keep unresolved choices pending under the existing human/delegated-review
contract. Store experience in notes/reports, not append-only histories in skills.
Edit owning sources, validate, refresh installed projections and verify fanout.

## Independent Comparison

The optional comparison protocol lives with Auto Learn in
`references/session-improvement.md`. Freeze the same raw session bounds,
available code/policy snapshots, feedback and validation for both methods.
Use independent report-only contexts without each other's conclusions. Preserve
facts and identify unavailable historical sources; a bounded segment is not a
whole-session replay.

Compare a hard remediation, process-heavy session and smooth session by cause
and intervention: shared, unique grounded, conflicting, unsupported or covered.
Reconcile with current sources before acting, while keeping current coverage
separate from historical claims. A higher suggestion count is not better.

Keep raw transcripts and machine-specific evidence private, outside published
diffs. Publish only scrubbed conclusions. Record actual model/effort and usage
when observed; this pilot is not proof of general savings or equal detection.

## Cost and Maintenance

Use `audit-rules --baseline <previous-audit.json>` to discover changed targets
and size deltas while comparing them with existing lexical coverage. This audit
does not assess semantic usefulness or check effectiveness. The weekly workflow
reads only implicated targets and available execution evidence; it does not
rescan all sessions or create a second learner.

Prefer replacement, merging and established invariants over new case lists.
Measure the full reached context path, including references, plus execution and
maintenance cost. A new file alone saves no context. Retire check coverage only
with equivalent replacement evidence or an explicit requirement change; retain
protection against rare serious failures. Required checks remain required.
