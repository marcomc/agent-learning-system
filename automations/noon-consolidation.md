# Noon Agent Learning Consolidation

Use `$consolidate-agent-learnings` in
`/path/to/agent-learning-system`.

Process new `inbox/` notes and automatically reprocess `needs-review/` notes
where the user selected exactly one review checkbox. Leave unchecked or
ambiguous notes pending. Do not reread processed history except to resolve a
specific duplicate or conflict.

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
