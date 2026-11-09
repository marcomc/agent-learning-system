# Consolidation Runner

Use the repository associated with this project chat. Read the installed
`$consolidate-agent-learnings` skill and its `references/pipeline.md` before
processing. Midnight and noon schedules share this workflow.

1. Begin with the caller's runner name and a unique run ID. Reconcile actual
   unfinished ownership before starting another run.
2. Run weekly discovery before consolidation when it is due, including recovery
   after a missed Monday execution.
3. Triage the initial inbox oldest first. Use `prepare-run` candidate cohorts
   and other shared contexts to review related inbox and review notes together;
   read each source and verify each outcome. Finish each bounded cohort before
   taking the next, and continue while independent notes remain actionable.
   Neither an audit candidate nor a deferred note stops the queue. Process
   explicit decisions and Joint Review, then reconcile open audit candidates.
   If `inventory_complete` is false, retry `unavailable_sources`, keep them
   unknown, and continue with readable independent notes. Never call an
   incomplete inventory empty or report a complete run.
4. Persist classified issues. Human decisions need a short explanation, clear
   question, at least two described options and current source/target hashes.
   Write digest-facing titles, questions, options and run summaries in English.
   Group related choices. Honor explicit chat answers
   with saved evidence; an unanswered question remains pending.
5. Verify changed targets once, validate files, finalize notes and save the
   report. Recheck the initial inventory before finishing. For every unresolved
   note record its concrete blocker and next action; continue working if it has
   no blocker. A short elapsed time, batch boundary, report preparation or an
   unrelated pending audit is not a reason to stop. Use partial only for a real
   interruption or blocker and state it; do not treat unassessed notes as
   blocked. Include counts and the oldest remaining note in the report. Finish
   with the actual status and every changed target.
6. Follow the consolidation skill's `references/integration.md`: reconcile
   owning sources and complete installed copies, review pending template
   handoffs, refresh affected generated consumers and record current integration
   receipts. Revalidate earlier verified receipts for drift. Use
   `pipeline-finish --require-integration`; `processed`, a draft or an
   out-of-sync report alone cannot establish integration. Keep incomplete
   distribution pending with an exact next action.

Keep results in this chat and store. The morning digest owns ordinary email.
Do not commit, push or prepare a release from consolidation.

Follow the consolidation skill's `references/coherence.md` before promotion:
review the complete affected contract with proportionate independent lenses and
retain current coherence proof. Weekly discovery must assess cumulative size,
repetition and rule interactions across promotions; lexical candidates and
budget warnings require semantic classification, not another appended rule.
