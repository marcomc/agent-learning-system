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
- Installer for config generation, Obsidian store initialization, skill
  installation, Codex skill mirrors, automation records, and local validation.
- Codex automation prompt files for midnight consolidation, noon
  consolidation, and morning review email.
- Unit tests covering note capture, review decisions, finalization, and
  outside-store rejection.
- README documentation with Mermaid diagrams for install, note lifecycle, and
  scheduled automation flows.
- Repository-local agent guidance in `AGENTS.md`.
