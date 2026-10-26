# Midnight Agent Learning Consolidation

Use `$consolidate-agent-learnings` in
`/path/to/agent-learning-system`.

Process new Obsidian learning notes from `inbox/` and reviewed notes from
`needs-review/`. Promote only safe, grounded, reusable lessons. Update only:

- `$HOME/AGENTS.md`;
- the smallest relevant reusable skill;
- the touched project's `AGENTS.md`;
- ignored template-upstream drafts when reusable atom changes are useful.

Inspect `prepare-run.review_analysis` and follow the skill's Joint Review
workflow when the configured threshold is reached (default eight pending notes).
Compare related full notes for complementary evidence; grouping is not approval.
When delegated autonomous instruction review is enabled, resolve verified
technical or already-covered notes with a saved receipt and
`finalize-note --review-evidence --enforce-routing`. Honor explicit human
decisions and preserve ambiguous boxes. Investigate technical uncertainty and
group only real residual user choices; leave them pending. Avoid re-reading an
unchanged unresolved cohort without new evidence, target state or user input.
Re-run `prepare-run` after inbox processing. Follow the skill's exact target
overlap checks for dirty/untracked projects and its residual-receipt completion
gate; compare current target hashes before reusing a previous deferral.

Do not commit or push. Write a consolidation report and validate every changed
Markdown or shell file. Finalize promoted notes with routing metadata and
`--enforce-routing`. For detection-only lessons, use
`--scope skill-detection --detection-target ... --enforce-routing`. Omit
enforcement only for rejected, no-op, or pending-review outcomes. Use
`create-template-draft` for reusable template candidates before any curated
template edit.

After promotions, summarize recurrence and run a rule audit against installed
promotion targets to catch duplicates or potential conflicts:

```bash
repo="/path/to/agent-learning-system"
python3 "${repo}/scripts/agent_learning.py" summarize-runs \
  --format markdown
```

```bash
repo="/path/to/agent-learning-system"
python3 "${repo}/scripts/agent_learning.py" audit-rules \
  --path "$HOME/AGENTS.md" \
  --path "$HOME/.agents/skills"
```
