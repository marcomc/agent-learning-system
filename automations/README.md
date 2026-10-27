# Codex Automations

These Markdown files are the prompt sources for the Agent Learning System
automations. The installer prints setup guidance by default; use
`./install.sh --skip-automations` to omit that guidance. It does not create or
change scheduled tasks, because chat and project registration require Codex.

| Prompt | Schedule | Purpose |
| --- | --- | --- |
| `midnight-consolidation.md` | Daily 00:00 | Weekly discovery when due, then consolidation and scoped verification. |
| `morning-review-email.md` | Daily 08:30 | Deduplicated decision and operational digest with chat links. |
| `noon-consolidation.md` | Daily 12:00 | Consolidate daytime notes and decisions. |
| `weekly-rule-audit.md` | Integrated weekly phase; no separate schedule | Discover and persist instruction issues before consolidation. |
| `weekly-release-pr.md` | Monday 10:30 | Prepare or resume a release PR after a verified learning cycle. |

The template-repository automation is sourced separately from
`agents-file-templates-and-skills/automations/` and is installed when that
repository is available and the task is configured in Codex.

## Set up the project and chats

In the desktop app, add the existing repository folder as a local project, or
select the existing project if it is already present. Create two local chats
in that project: one for the three ordinary schedules and one for release PRs.
From the CLI, run `codex` in the repository to prepare or inspect the prompts;
use the desktop app to add the local project and manage scheduled tasks. The
CLI does not provide the scheduled-task management interface.

In the desktop app, tell Codex:

> Configure the four schedules from `automations/README.md`: three ordinary
> schedules in one shared project chat and release preparation in another.
> Reuse existing tasks and retire the standalone weekly audit schedule.

Use GPT 6.1 Terra for these chats and confirm the four schedules are active. A
scheduled task in a chat continues that chat and inherits its context. The
computer and app must be running for tasks that require local files. The
template task belongs in the template repository's project and should be set up
separately from its prompt. See [Scheduled tasks](https://learn.chatgpt.com/docs/automations)
for the current Codex interface.

Bind the verified chat UUIDs with `pipeline-configure --ordinary-thread UUID
--release-thread UUID` through `scripts/agent_learning.py`. The installer leaves
these local bindings and existing schedules untouched. The full handoff and
email delivery contract is in the consolidation skill's
[`references/pipeline.md`](../skills/consolidate-agent-learnings/references/pipeline.md).
