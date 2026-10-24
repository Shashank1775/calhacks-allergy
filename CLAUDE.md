@AGENTS.md

## Claude Code specifics

- Agent definitions live in [.claude/agents/](.claude/agents/): `director`, `team-manager`, `worker`, `reviewer`. Start the Director with `claude --agent director`. Managers spawn workers with the `worker` subagent type, which runs in its own git worktree. Launch recipes: [docs/agents/LAUNCH.md](docs/agents/LAUNCH.md).
- Worktrees made by Claude Code live in `.claude/worktrees/` (gitignored). A worktree branches from `origin/main`. Rename its branch to the convention before your first push: `git branch -m <team>/<issue>-<slug>`.
- Auto memory is shared by every worktree on this machine. Use it only for facts about the human's preferences, never for task or team state.
- When a subagent finishes, check its PR and CI yourself. Its report is a claim, not proof.
