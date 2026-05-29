# Weekly Agent Learning Rule Audit

Run once a week (audit-only). Do not modify learning notes. Do not commit or
push.

## Goal

Catch long-term drift: duplicate or potentially conflicting rules that have
accumulated across `$HOME/AGENTS.md` and installed `$HOME/.agents/skills`.

## Steps

Run the audit:

```bash
repo="/path/to/agent-learning-system"
python3 "${repo}/scripts/agent_learning.py" audit-rules \
  --path "$HOME/AGENTS.md" \
  --path "$HOME/.agents/skills" \
  > /tmp/agent-learning-audit.json
```

If `potential_conflicts` or `near_duplicates` is non-empty, write a short
Obsidian note under `AI Agent Learnings/reports/` summarizing:

- counts for `exact_duplicates`, `near_duplicates`, `potential_conflicts`;
- the top 3 highest-ratio pairs with file paths + line numbers;
- the recommended action: merge/clarify scope or move to `needs-review`.

If all three lists are empty, do nothing.
