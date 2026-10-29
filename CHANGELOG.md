# Changelog

All notable changes to this project are documented in this file.

The project follows semantic versioning.

## Unreleased

## [1.2.0] - 2026-10-27

### Added

- Coordinated consolidation cycles with integrated weekly discovery, persistent
  audit findings, scoped post-verification and recoverable run outcomes.
- Readable HTML/plain-text decision digests with described options, verified
  local chat links, seven-day reminder suppression and delivery receipts.
- File-backed Codex automation prompts and setup guidance for shared task chats,
  including a post-audit release workflow requiring user approval and merge.
- Idempotent terminal capture by project, source run and lesson family, retaining
  separate recurrence evidence across runs, rejecting conflicting retries and
  recovering interrupted note/index commits without duplicate captures.
- Proposed/applied learning state with target and verification-artifact hashes;
  consolidation verifies already-applied coverage instead of repeating it.
- Audit snapshots and changed-target comparisons with size deltas, explicitly
  separate from semantic approval and check-effectiveness assessment.
- Shared validation entrypoint used by installer and versioned GitHub Actions,
  with concurrency, negative-path, archival and audit regressions.
- Optional independent Retro/Auto Learn comparison and bounded weekly process
  maintenance without adding an automatic review gate or retrospective loop.

### Fixed

- Revalidate every finalization source while locked, preventing concurrent
  callers from recreating a note already moved by another finalizer.
- Reject queue/archive directories resolving outside the configured store,
  including directory-symlink source and destination escapes.
- Atomically replace finalization content and journal note/state commits for
  recovery after process interruption. Surface unfinished commits in preparation
  output and reject completion reports until they are reconciled.

## [1.1.0] - 2026-10-26

### Added

- Joint review discovery at eight pending notes by default, with configurable
  trigger, cohort fingerprints, context hints and explicit human precedence.
- Opt-in instruction-only autonomous resolution with source/target hash
  receipts, technical/covered classifications and auditable archive metadata.
- Regressions for threshold boundaries, stale or foreign evidence, unresolved
  choices, protected human decisions and installer preflight failures.
- Document the four-project agent ecosystem, source-ownership contract,
  installation boundaries, architecture weak points, and phased improvements.

### Fixed

- Require per-note residual evidence before reporting a due autonomous Joint
  Review complete; reject missing coverage, unfinalized technical decisions and
  stale target snapshots. Reassess dirty/untracked instruction overlap within
  the same run instead of carrying blanket deferrals forward.
- Recognize project `docs/agent-procedures.md` as an instruction target only
  when the project's root `AGENTS.md` explicitly loads that path.
- Correct the automation validation example to use plain `prepare-run`, which
  already emits JSON.

## [1.0.0] - 2026-06-06

### Added in 1.0.0

- Composable learning architecture documentation for routing lessons into
  global, atom, project-local, skill-prevention, and detection targets.
- Routing metadata for captured and finalized learning notes, including lesson
  family, scope, prevention targets, detection targets, template upstream
  status, recurrence classification, and routing rationale.
- `--enforce-routing` validation for promoted lessons, including detection-only
  routes that require a detection target.
- `summarize-runs` reporting for recurrence, missing routing, queue counts, and
  duplicate-after-prevention metrics.
- `create-template-draft` for clean, draft-first handoffs into the agent
  template repository.
- AI-agent runbook and prompt source for installing the full two-repository
  auto-learning automation pipeline.
- Tests for routing enforcement, recurrence metrics, concurrent state updates,
  transactional finalization, and template draft privacy handling.

### Changed in 1.0.0

- Consolidation skills and automations now require routing-aware finalization
  and distinguish prevention targets from detection targets.
- State persistence now uses locked atomic updates and validates state before
  moving notes out of the queue.

### Fixed in 1.0.0

- Prevented notes from being archived when state update or state loading fails.
- Rejected blank routing rationales when routing enforcement is enabled.
- Rejected blocked or unsanitized template draft creation.
- Restricted `summarize-runs --since` to `YYYY-MM-DD` date filtering.

## [0.1.0] - 2026-05-19

### Added

- Initial local Agent Learning System.
- `record-agent-learning` skill for writing reusable session learnings into the
  Obsidian inbox.
- `consolidate-agent-learnings` skill for processing inbox and reviewed notes,
  promoting reusable lessons, archiving processed notes, and writing reports.
- `scripts/agent_learning.py` helper for store initialization, note capture,
  consolidation preparation, note finalization, report writing, notification,
  and privacy scanning.
- Explicit `record-agent-learning` hook mode for detecting local review skills
  and adding an idempotent learning-capture hook only when requested.
- Repository source synchronization for hook mode when installed skills are
  copied locally instead of symlinked from their source repository.
- Filesystem-backed learning store configuration where `AGENT_LEARNING_DIR`
  names the base directory and `AGENT_LEARNING_STORE_NAME` names the generated
  `AI Agent Learnings` folder.
- Installer for config generation, learning store initialization, copy-based
  skill installation, Codex skill mirrors, automation records, and local
  validation.
- Codex automation prompt files for midnight consolidation, noon
  consolidation, and morning review email.
- Unit tests covering note capture, review decisions, finalization, and
  outside-store rejection.
- README documentation with Mermaid diagrams for install, note lifecycle, and
  hooked review-skill, and scheduled automation flows.
- Repository-local agent guidance in `AGENTS.md`.
