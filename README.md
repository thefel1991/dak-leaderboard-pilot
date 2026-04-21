# DAK Leaderboard Pilot

Client-side scripts for the DAK leaderboard pilot — push your projects
and usage stats to `thefel1991` GitHub account instead of the old
HTTP-based leaderboard.

## For players: one-prompt onboarding

You do not install this repo manually. Paste the prompt in
[`ONBOARD_PROMPT.md`](./ONBOARD_PROMPT.md) into Claude Code once per
machine. It will:

1. Download these scripts to `~/.claude/`
2. Ask you to paste your GitHub token (given to you separately)
3. Wire the Stop hook so your projects + stats sync automatically at
   the end of every Claude Code session

After that, nothing to do — every session-end triggers an automatic sync.

## What the scripts do

| Script | Purpose |
|---|---|
| `publish_project.py` | Take one project directory, push it as a private repo `thefel1991/{player}__{project}` |
| `commit_stats.py` | Aggregate all your Claude Code sessions, commit the totals as JSON to `thefel1991/leaderboard-data/players/{player}.json` |
| `sync_all.py` | Stop-hook wrapper — finds every project you've worked in, calls `publish_project.py` on each, then calls `commit_stats.py` |

## Kill switch

To temporarily disable auto-sync (e.g., working on something sensitive):

```bash
export SYNC_ALL_DISABLE=1
```

Set in your shell before starting Claude Code. Unset (`unset SYNC_ALL_DISABLE`) to resume.

## Security posture during pilot

- Token lives at `~/.claude/.thefel1991_token` mode 600 — not in env,
  not in process memory longer than needed, not in `.git/config`
  (scripts sanitize the remote URL after every push).
- Scripts scan projects for `.env`, `*.key`, `*.pem`, `credentials.json`,
  `*.db` and **abort** the push for that project if any are found.
  Override with `FORCE=1` only after you're sure.
- Default `.gitignore` is written for projects that don't have one,
  blocking `node_modules`, `venv`, `.env*`, build artifacts, and OS junk.
