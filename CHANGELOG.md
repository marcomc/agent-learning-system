# Changelog

All notable changes to this project are documented in this file.

The project follows semantic versioning.

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
