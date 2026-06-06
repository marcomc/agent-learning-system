# Midnight Agent Learning Consolidation

Use `$consolidate-agent-learnings` in
`/path/to/agent-learning-system`.

Process new Obsidian learning notes from `inbox/` and reviewed notes from
`needs-review/`. Promote only safe, grounded, reusable lessons. Update only:

- `$HOME/AGENTS.md`;
- the smallest relevant reusable skill;
- the touched project's `AGENTS.md`;
- ignored template-upstream drafts when reusable atom changes are useful.

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
