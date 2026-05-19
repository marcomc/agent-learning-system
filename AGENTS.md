# Agent Learning System Instructions

Follow `$HOME/AGENTS.md` for the canonical user-wide policy.

Additional repository rules:

- When changing note finalization or movement, reject sources outside the
  configured learning store and cover the negative path with a test.
- Keep shared examples and prompt files publication-safe: use placeholders for
  local repo paths, user emails, home directories, and private vault locations.
- Run installer validation before writing user config or replacing installed
  skill paths, so failed checks do not leave a partial local install.
