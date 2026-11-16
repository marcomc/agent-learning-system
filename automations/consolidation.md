# Consolidation Runner

Use the repository associated with this project chat. Read the installed
`$consolidate-agent-learnings` skill and its `references/pipeline.md` before
processing. Midnight and noon schedules share this workflow.

The user explicitly authorized one Codex Goal per scheduled consolidation
execution. Inspect `get_goal`, `pipeline-status` and actual run ownership first.
Retry runs and later heartbeats continue an unfinished Goal rather than starting
an overlapping execution. After a completed Goal is advanced, a new scheduled
execution creates its own Goal; the weekly audit cycle is separate.
Before starting a fresh run, inspect inbox availability. If it has files but
none are readable, do not create a run or Goal: report the storage blocker and
retry on the next heartbeat. Do not treat those files as an empty inbox.
If a prior cycle is active but its Goal is missing, replaced, or already complete,
preserve its state and report the conflict; do not manufacture a matching Goal.
For a scheduler heartbeat, use the Codex app `read_thread` tool on the current
thread with `turnLimit: 1`, `includeOutputs: true` and enough output capacity to
retain the complete heartbeat. Save an unmodified capture under the learning
store's `reports/`: schema `app-thread-event-v1`, `thread` from `thread.id`,
`turn`, `status` and `startedAt` from the current turn, and `event` as its single
untruncated `codex_app.automation_update` function-call output. Capture it
directly from the tool result; do not reconstruct or invent an event. A stored
event alone is an agent attestation, not proof of the current turn. Start the
run as `unknown`, then in a separate tool call read the current turn again and
include its completed `commandExecution` item matching the run's persisted
`start_tool_call_id` as `command`. Save the resulting capture and call
`pipeline-dispatch-bind --run-id ID --dispatch-evidence PATH`. The pipeline
correlates the heartbeat and this exact start command. This is an agent-attested
historical correlation, not authenticated proof; its saved path and hash allow
later receipt-integrity checks, but status does not recheck the receipt. If the app capture,
command identity or correlation is unavailable, keep `unknown` and continue
independent inbox work; do not guess from the clock.

When no Goal is unfinished and no cycle is active, begin a pipeline run with
`--goal-mode scheduled` so its intended lifecycle is saved before Goal creation.
The mode records intent, not proof that creation was attempted or succeeded;
reconcile the native Goal and actual ownership before adoption. `manual` alone
does not establish a scheduled creation attempt; it may still continue an
already active Goal. A missing mode means unknown legacy provenance, not manual
or scheduled; preserve it until evidence resolves it. Check its returned
`initial_unavailable` before creating any Goal. If it is nonempty,
process readable notes, finish partial and report the unavailable paths without
creating a Goal. Otherwise resolve `THREAD_ID` from this automation's configured
target chat; if its identity cannot be established, stop rather than guess.
Call `pipeline-goal-attempt --run-id ID --goal-thread-id THREAD_ID` before
calling the native Goal creation tool. This
marker means creation may have been attempted, not that it succeeded; after an
interruption reconcile the actual Goal and thread before retrying. Then create a
Goal whose objective starts `Agent Learning baseline ID initial_fingerprint HASH`,
with the exact saved run ID and fingerprint substituted. The Goal requires processing every independent
actionable initial note, verifying outcomes and receipts, and recording real
blockers and next actions. Save the actual unmodified JSON result of `get_goal`
in a private file inside the store's reports directory. Bind using
`pipeline-goal-bind --run-id ID --initial-fingerprint HASH --goal-thread-id
THREAD_ID --goal-receipt ACTIVE_JSON`, with the thread ID from that result.
If Goal creation fails after a new run began,
close that run as partial and report the failure. Never assume an active run
is orphaned merely because time elapsed. If a crash left an unbound running
run, establish that its actual owner and in-flight actions have terminated;
then adopt its existing run ID and fingerprint. If `get_goal` shows the matching
active Goal, save that actual result and bind the existing Goal. A different
unfinished Goal is a conflict. If no Goal is active after a possible creation
attempt, inspect the original tool call, result and owning chat history: an
absent active Goal alone does not prove creation failed. Reconcile a Goal shown
by that evidence; retry creation only when the evidence establishes that no
Goal was created. If the result or ownership remains uncertain, preserve the
run and report the conflict instead of repeating creation or starting another.
Before creating a fresh baseline, also check for fingerprinted runs without a
Goal binding that ended partial or failed with a saved Goal attempt marker, or
whose older execution evidence shows that Goal creation was attempted.
Adopt the earliest such unreconciled run as the baseline and bind its exact
saved fingerprint; do not discard its original scope merely because it ended.
If that saved baseline contains no readable sources and only unavailable
entries, do not create a Goal for it: preserve the partial run and request
reconciliation of those unknown sources before binding.
If its ownership or relationship to other runs cannot be established, report
the conflict and preserve state for reconciliation.

Do not create a Goal when the saved baseline has any `initial_unavailable`:
process independently readable notes, finish partial, and report the unknown
paths. Retry availability on a later heartbeat. Do not bind that incomplete
baseline or infer its original contents from a later file at the same path.
Keep the incomplete run and its unresolved blocker visible; it does not prevent
a later, fully readable snapshot from starting a separate Goal cycle.
An active Goal cannot accept a continuation run with an incomplete inventory;
retain its current state and retry after storage recovery.

An active Agent Learning Goal retains its baseline across retry runs. If its
marker is missing after interruption, bind the exact ID and hash from its
objective with its actual current active Goal JSON; do not guess. If the marker
is sealed, recheck evidence. If the same native Goal remains active and evidence
still holds, complete it. If evidence has changed and the Goal is still active,
use `pipeline-goal-reopen` with the exact `sealed_at`, reason and a fresh active
Goal receipt, then continue. If the Goal is complete, save its actual current
`get_goal` result and call `pipeline-goal-advance` with the baseline, exact
`sealed_at` and `--goal-receipt COMPLETE_JSON` before another cycle. If advance
reports `needs-reconciliation`, retain the saved seal and investigate its
persisted issues; do not call the old outcomes verified. The marker permits a
new cycle so independent notes can continue. If a ledger issue is missing,
retry the exact saved advance and completion receipt to restore it before
starting another run; never replace a changed issue with guessed evidence. A Goal
belonging to other work is a conflict: report it unchanged.
A null Goal after sealing requires checking this chat's recorded execution for
the original successful `update_goal` result. If it matches the sealed Goal's
identity and baseline, save that result and advance using it; do not substitute
the null response. If no matching result can be recovered, retain the seal.
A paused, blocked, budget- or usage-limited Goal remains untouched; report its
state and stop its work. If Goal tools are unavailable, do not start a run or
claim the Goal exists.

Before completing the Goal, run `pipeline-goal-status --run-id ID
--initial-fingerprint HASH --seal --goal-receipt ACTIVE_JSON`, using a freshly
saved actual `get_goal` result. It checks all bound runs, source and archive
identity, unavailable sources and integration receipts. When review is due,
pass `--review-evidence` with the current residual Joint Review receipt. A
running or unbound run, unassessed source or pending receipt is not clean.
Continue independent actionable notes across turns; a partial finish alone is
not Goal completion. Assess review dispositions and blockers semantically and
preserve user decisions.
Goal accounting may accept a matching finalized initial source from independent
manual work; this proves an outcome, not that the manual run belongs to the
Goal. Integration and residual-review checks still apply.
An initially unreadable source has no verified identity: a later file at the
same path does not prove it is the same source. Retain it as unknown, keep the
run partial and report the blocker and next action. If a legacy Goal is already
bound to such a run, preserve it; when a later inbox inventory is incomplete,
do not start a continuation run, and retry after storage recovery. Do not seal
that Goal or claim the unknown was processed without evidence that resolves its
identity. Respect native Goal status rules;
if the remaining blocker requires user action, explain that limitation. Call
`update_goal` complete only after sealing and
the required evidence audit. Save the unmodified result returned by that
`update_goal` call immediately as the completion receipt; a subsequent
`get_goal` may return null once the Goal is complete. Call
`pipeline-goal-advance --run-id ID --initial-fingerprint HASH --sealed-at TIME
--goal-receipt COMPLETE_JSON`. Goal sealing covers note outcomes, not unrelated
pipeline audits, release readiness or a completed pipeline status. Handle an
advance result of `needs-reconciliation` as described above. Do not
allocate a token budget unless the user explicitly specifies one.

If completion succeeded but the receipt was not saved, recover the unmodified
`update_goal` tool result from this same chat's recorded execution and verify
its Goal identity before advancing. If the native result cannot be found,
retain the sealed state for reconciliation; a null `get_goal` alone does not
prove completion.

1. Begin with the caller's runner name and a unique run ID. Reconcile actual
   unfinished ownership before starting another run.
2. Run weekly discovery before consolidation when it is due, including recovery
   after a missed Monday execution.
3. Triage the initial inbox oldest first. Use `prepare-run` candidate cohorts
   and other shared contexts to review related inbox and review notes together;
   read each source and verify each outcome. Finish each bounded cohort before
   taking the next, and continue while independent notes remain actionable.
   Neither an audit candidate nor a deferred note stops the queue. Process
   explicit decisions and Joint Review, then reconcile current-scope audit
   candidates; retain unrelated findings, including newly discovered ones, as
   separate open work.
   If `inventory_complete` is false, retry `unavailable_sources`, keep them
   unknown, and continue with readable independent notes. Never call an
   incomplete inventory empty or report a complete run.
4. Persist classified issues. Human decisions need a short explanation, clear
   question, at least two described options and current source/target hashes.
   Write digest-facing titles, questions, options and run summaries in the
   configured language. Group related choices. Honor explicit chat answers
   with saved evidence; an unanswered question remains pending.
5. Verify changed targets once, validate files, finalize notes and save the
   report. Pass `--run-id ID` to each `finalize-note` so the integration audit
   can identify every note processed by this run. Recheck the initial inventory
   before finishing. For every unresolved
   note record its concrete blocker and next action; continue working if it has
   no blocker. A short elapsed time, batch boundary, report preparation or an
   unrelated pending audit is not a reason to stop. Use partial only for a real
   interruption or blocker and state it; do not treat unassessed notes as
   blocked. Include counts and the oldest remaining note in the report. Finish
   with the actual status and every changed target. For partial or failed runs,
   pass `--blocker-scope notes`, `operational`, `mixed` or `unknown` only when
   that classification is supported; use `unknown` when unsure. Record each
   operational blocker as an issue before finishing.
6. Follow the consolidation skill's `references/integration.md`: reconcile
   owning sources and complete installed copies, review pending template
   handoffs, refresh affected generated consumers and record current integration
   receipts. Verify current-run notes with `integration-audit --run-id ID
   --require-verified`, then use `pipeline-finish --require-integration`;
   `processed`, a draft or an
   out-of-sync report alone cannot establish integration. Keep incomplete
   distribution pending with an exact next action.

Historical integration debt is a separate audit, not an inbox precondition.
Do not run a global strict integration audit before processing the initial inbox
or stop on its pending historic receipts. Preserve unknown historical status;
never manufacture receipts or waive verification for current-run outcomes.
For a legacy run without exact integration attribution, audit explicit note IDs
separately; after reconciling its owner, close an interrupted legacy run as
partial or failed and start a new run. Never infer ownership from timestamps.

Keep results in this chat and store. The morning digest owns ordinary email.
Do not commit, push or prepare a release from consolidation.

Follow the consolidation skill's `references/coherence.md` before promotion:
review the complete affected contract with proportionate independent lenses and
retain current coherence proof. Weekly discovery must assess cumulative size,
repetition and rule interactions across promotions; lexical candidates and
budget warnings require semantic classification, not another appended rule.
