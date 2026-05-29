# Midnight Agent Learning Consolidation

Use `$consolidate-agent-learnings` in
`/path/to/agent-learning-system`.

Process new Obsidian learning notes from `inbox/` and reviewed notes from
`needs-review/`. Promote only safe, grounded, reusable lessons. Update only:

- `$HOME/AGENTS.md`;
- the smallest relevant reusable skill;
- the touched project's `AGENTS.md`;
- the existing agent-template mining workflow when useful.

Do not commit or push. Write a consolidation report and validate every changed
Markdown or shell file.

After promotions, run a rule audit against installed promotion targets to catch
duplicates or potential conflicts:

```bash
repo="/path/to/agent-learning-system"
python3 "${repo}/scripts/agent_learning.py" audit-rules \
  --path "$HOME/AGENTS.md" \
  --path "$HOME/.agents/skills"
```
